"""Tests for dispenser.py: validation, the allocation algorithm, the ATM, its lock and the state file.

Unit tests check single examples from the brief; the property tests throw thousands of random
inputs (fixed seeds, so failures are repeatable) at the code and check rules that must ALWAYS hold.
"""

import contextlib
import itertools
import random
import string
import tempfile
import threading
import time
import unittest
from collections import OrderedDict
from collections.abc import Hashable
from pathlib import Path
from unittest import mock

from dispenser import (
    CANNOT_DISPENSE,
    ATMError,
    CashDispenser,
    InvalidAmountError,
    InvalidInventoryError,
    InventoryStore,
    NoteAllocator,
    Validator,
)

# ================================================================================================
# Validator: amounts and inventories
# ================================================================================================


class ValidAmountTests(unittest.TestCase):
    def test_positive_whole_number_is_accepted(self):
        self.assertEqual(Validator.amount(2700), 2700)

    def test_smallest_positive_amount_is_accepted(self):
        self.assertEqual(Validator.amount(1), 1)

    def test_whole_number_text_is_accepted(self):
        self.assertEqual(Validator.amount("2700"), 2700)

    def test_text_with_surrounding_spaces_and_plus_sign_is_accepted(self):
        self.assertEqual(Validator.amount("  +2700 "), 2700)

    def test_very_large_amount_is_accepted(self):
        self.assertEqual(Validator.amount(10 ** 12), 10 ** 12)


class InvalidAmountTests(unittest.TestCase):
    def assert_rejected(self, amount, message_part):
        with self.assertRaises(InvalidAmountError) as raised:
            Validator.amount(amount)
        self.assertIn(message_part, str(raised.exception))

    def test_zero_is_rejected(self):
        self.assert_rejected(0, "greater than zero")

    def test_zero_as_text_is_rejected(self):
        self.assert_rejected("0", "greater than zero")

    def test_negative_amount_is_rejected(self):
        self.assert_rejected(-500, "cannot be negative")

    def test_negative_amount_as_text_is_rejected(self):
        self.assert_rejected("-500", "cannot be negative")

    def test_non_numeric_text_is_rejected(self):
        self.assert_rejected("abc", "must be a number")

    def test_empty_text_is_rejected(self):
        self.assert_rejected("", "must be a number")

    def test_text_mixing_digits_and_letters_is_rejected(self):
        self.assert_rejected("27OO", "must be a number")

    def test_currency_symbol_is_rejected(self):
        self.assert_rejected("₹2700", "must be a number")

    def test_decimal_amount_is_rejected(self):
        self.assert_rejected(2700.5, "whole number")

    def test_decimal_text_is_rejected(self):
        self.assert_rejected("12.5", "whole number")

    def test_float_with_whole_value_is_rejected(self):
        # 2700.0 is a float: money amounts must be given as whole numbers, not floats.
        self.assert_rejected(2700.0, "whole number")

    def test_not_a_number_and_infinity_are_rejected(self):
        self.assert_rejected(float("nan"), "whole number")
        self.assert_rejected(float("inf"), "whole number")
        self.assert_rejected("inf", "whole number")

    def test_underscore_separated_number_is_rejected(self):
        # Python's int() would accept "2_700"; an ATM keypad would not.
        self.assert_rejected("2_700", "must be a number")

    def test_none_is_rejected(self):
        self.assert_rejected(None, "must be a number")

    def test_boolean_is_rejected(self):
        # True is technically the integer 1 in Python; it is still not a valid amount.
        self.assert_rejected(True, "must be a number")

    def test_list_is_rejected(self):
        self.assert_rejected([2700], "must be a number")

    def test_non_ascii_digits_are_rejected(self):
        # Python's int() accepts Arabic-Indic, Devanagari and full-width digits; an ATM must not.
        for text in ("١٠٠", "१००", "１００", "١٢.٥"):
            with self.subTest(text=text):
                self.assert_rejected(text, "must be a number")

    def test_extremely_long_number_text_is_rejected_before_conversion(self):
        self.assert_rejected("9" * 19, "too large")
        self.assert_rejected("1" * 100_000, "too large")

    def test_amount_errors_are_value_errors(self):
        with self.assertRaises(ValueError):
            Validator.amount(-1)


class ValidInventoryTests(unittest.TestCase):
    def test_example_inventory_is_accepted(self):
        self.assertEqual(Validator.inventory({500: 8, 200: 10, 100: 20}), {500: 8, 200: 10, 100: 20})

    def test_result_is_sorted_largest_denomination_first(self):
        result = Validator.inventory({100: 20, 500: 8, 200: 10})
        self.assertEqual(list(result), [500, 200, 100])

    def test_zero_notes_of_a_denomination_is_allowed(self):
        self.assertEqual(Validator.inventory({500: 0, 100: 3}), {500: 0, 100: 3})

    def test_any_denomination_is_supported(self):
        inventory = {2000: 1, 50: 4, 20: 7, 10: 3, 1: 9}
        self.assertEqual(Validator.inventory(inventory), inventory)

    def test_other_mapping_types_are_accepted(self):
        self.assertEqual(Validator.inventory(OrderedDict([(100, 1)])), {100: 1})

    def test_result_is_an_independent_copy(self):
        original = {500: 8}
        result = Validator.inventory(original)
        result[500] = 0
        self.assertEqual(original, {500: 8})


class InvalidInventoryTests(unittest.TestCase):
    def assert_rejected(self, inventory, message_part):
        with self.assertRaises(InvalidInventoryError) as raised:
            Validator.inventory(inventory)
        self.assertIn(message_part, str(raised.exception))

    def test_empty_inventory_is_rejected(self):
        self.assert_rejected({}, "empty")

    def test_inventory_that_is_not_a_mapping_is_rejected(self):
        self.assert_rejected([(500, 8)], "must be a mapping")
        self.assert_rejected(None, "must be a mapping")

    def test_negative_note_count_is_rejected(self):
        self.assert_rejected({500: -1}, "cannot be negative")

    def test_decimal_note_count_is_rejected(self):
        self.assert_rejected({500: 2.5}, "must be a whole number")

    def test_text_note_count_is_rejected(self):
        self.assert_rejected({500: "8"}, "must be a whole number")

    def test_boolean_note_count_is_rejected(self):
        self.assert_rejected({500: True}, "must be a whole number")

    def test_zero_denomination_is_rejected(self):
        self.assert_rejected({0: 5}, "Invalid denomination 0")

    def test_negative_denomination_is_rejected(self):
        self.assert_rejected({-100: 5}, "Invalid denomination -100")

    def test_decimal_denomination_is_rejected(self):
        self.assert_rejected({0.5: 5}, "Invalid denomination 0.5")

    def test_text_denomination_is_rejected(self):
        self.assert_rejected({"500": 5}, "Invalid denomination '500'")

    def test_boolean_denomination_is_rejected(self):
        self.assert_rejected({True: 5}, "Invalid denomination True")

    def test_one_bad_entry_rejects_the_whole_inventory(self):
        self.assert_rejected({500: 8, 200: -3, 100: 20}, "cannot be negative")

    def test_inventory_errors_share_a_common_base_class(self):
        with self.assertRaises(ATMError):
            Validator.inventory({})


class ParseInventoryTests(unittest.TestCase):
    def test_simple_format(self):
        self.assertEqual(Validator.parse_inventory("500:8, 200:10, 100:20"), {500: 8, 200: 10, 100: 20})

    def test_format_used_in_the_brief(self):
        self.assertEqual(Validator.parse_inventory("{ 500: 8, 200: 10, 100: 20 }"), {500: 8, 200: 10, 100: 20})

    def test_trailing_comma_is_tolerated(self):
        self.assertEqual(Validator.parse_inventory("500:8,"), {500: 8})

    def test_negative_values_are_parsed_and_left_for_the_atm_to_reject(self):
        self.assertEqual(Validator.parse_inventory("500:-1"), {500: -1})

    def test_rejects_empty_text(self):
        for text in ("", "   ", "{}", "{ }"):
            with self.subTest(text=text), self.assertRaises(InvalidInventoryError):
                Validator.parse_inventory(text)

    def test_rejects_missing_count(self):
        with self.assertRaises(InvalidInventoryError):
            Validator.parse_inventory("500")

    def test_rejects_extra_colon(self):
        with self.assertRaises(InvalidInventoryError):
            Validator.parse_inventory("500:8:1")

    def test_rejects_non_numeric_denomination(self):
        with self.assertRaisesRegex(InvalidInventoryError, "denomination 'abc'"):
            Validator.parse_inventory("abc:8")

    def test_rejects_non_numeric_count(self):
        with self.assertRaisesRegex(InvalidInventoryError, "note count"):
            Validator.parse_inventory("500:many")

    def test_rejects_non_ascii_digits(self):
        with self.assertRaisesRegex(InvalidInventoryError, "must be a whole number"):
            Validator.parse_inventory("５００:8")

    def test_huge_number_gets_a_short_clear_message(self):
        with self.assertRaises(InvalidInventoryError) as raised:
            Validator.parse_inventory("500:" + "9" * 5000)
        self.assertIn("too large", str(raised.exception))
        self.assertLess(len(str(raised.exception)), 120)

    def test_rejects_duplicate_denomination(self):
        with self.assertRaisesRegex(InvalidInventoryError, "more than once"):
            Validator.parse_inventory("500:8, 500:2")


# ================================================================================================
# NoteAllocator: correct, minimal, within the available notes, never greedy-only
# ================================================================================================


find_minimum_notes = NoteAllocator().find_minimum_notes


def paid(notes):
    return sum(value * count for value, count in notes.items())


def cheapest_by_trying_everything(amount, inventory):
    """Reference answer: try every possible combination of the available notes."""
    denominations = list(inventory)
    best = None
    for counts in itertools.product(*(range(inventory[d] + 1) for d in denominations)):
        pays_exactly = sum(d * c for d, c in zip(denominations, counts, strict=True)) == amount
        if pays_exactly and (best is None or sum(counts) < best):
            best = sum(counts)
    return best


class ExamplesFromTheBriefTests(unittest.TestCase):
    def test_main_example(self):
        # ₹2700 from ₹500 × 8, ₹200 × 10, ₹100 × 20 -> ₹500 × 5, ₹200 × 1 (6 notes)
        self.assertEqual(find_minimum_notes(2700, {500: 8, 200: 10, 100: 20}), {500: 5, 200: 1})

    def test_greedy_trap_from_the_brief(self):
        # Taking ₹4 first leaves ₹2, which cannot be paid. The answer is ₹3 × 2.
        self.assertEqual(find_minimum_notes(6, {4: 1, 3: 2}), {3: 2})


class MinimumNotesTests(unittest.TestCase):
    def test_single_note(self):
        self.assertEqual(find_minimum_notes(500, {500: 8, 200: 10, 100: 20}), {500: 1})

    def test_fewest_notes_even_when_greedy_would_succeed_with_more(self):
        # Greedy: 4 + 1 + 1 = 3 notes. Best: 3 + 3 = 2 notes.
        self.assertEqual(find_minimum_notes(6, {4: 10, 3: 10, 1: 10}), {3: 2})

    def test_greedy_trap_with_realistic_notes(self):
        # Greedy takes ₹50 and is left with ₹10, but has no ₹10 notes. Answer: ₹20 × 3.
        self.assertEqual(find_minimum_notes(60, {50: 1, 20: 3}), {20: 3})

    def test_greedy_trap_when_the_largest_note_must_be_skipped_entirely(self):
        self.assertEqual(find_minimum_notes(600, {500: 5, 200: 3}), {200: 3})

    def test_limited_large_notes_are_topped_up_with_smaller_ones(self):
        # Only 2 × ₹500 left, so the rest comes from ₹200 notes.
        self.assertEqual(find_minimum_notes(2400, {500: 2, 200: 10, 100: 20}), {500: 2, 200: 7})

    def test_every_note_in_the_machine_can_be_used(self):
        self.assertEqual(find_minimum_notes(1400, {500: 2, 200: 1, 100: 2}), {500: 2, 200: 1, 100: 2})

    def test_large_amount_with_realistic_denominations(self):
        result = find_minimum_notes(98_700, {2000: 40, 500: 100, 200: 100, 100: 100})
        self.assertEqual(result, {2000: 40, 500: 37, 200: 1})

    def test_ties_prefer_high_value_notes(self):
        # 6 = 3 + 3 = 4 + 2 (both 2 notes): the one with the higher note is chosen.
        self.assertEqual(find_minimum_notes(6, {4: 1, 3: 2, 2: 1}), {4: 1, 2: 1})

    def test_result_is_the_same_every_time(self):
        inventory = {500: 8, 200: 10, 100: 20}
        results = {tuple(sorted(find_minimum_notes(1500, inventory).items())) for _ in range(20)}
        self.assertEqual(len(results), 1)


class CannotDispenseTests(unittest.TestCase):
    def test_amount_not_a_multiple_of_any_combination(self):
        self.assertIsNone(find_minimum_notes(250, {500: 8, 200: 10, 100: 20}))

    def test_amount_smaller_than_the_smallest_note(self):
        self.assertIsNone(find_minimum_notes(50, {500: 8, 200: 10, 100: 20}))

    def test_amount_larger_than_all_cash_in_the_machine(self):
        self.assertIsNone(find_minimum_notes(10_000, {500: 8, 200: 10, 100: 20}))

    def test_amount_needs_more_notes_than_available(self):
        # ₹300 needs ₹200 + ₹100, but there is no ₹100 note and ₹200 alone cannot make it.
        self.assertIsNone(find_minimum_notes(300, {500: 5, 200: 5, 100: 0}))

    def test_amount_below_total_cash_but_impossible_to_combine(self):
        # The machine holds ₹7 in total, but no combination of a ₹4 and a ₹3 makes ₹5.
        self.assertIsNone(find_minimum_notes(5, {4: 1, 3: 1}))

    def test_machine_with_no_notes_left(self):
        self.assertIsNone(find_minimum_notes(100, {500: 0, 200: 0, 100: 0}))

    def test_notes_exist_but_all_are_too_large(self):
        self.assertIsNone(find_minimum_notes(100, {500: 3, 200: 3}))


class NotesLimitTests(unittest.TestCase):
    def test_never_uses_more_notes_than_available(self):
        inventory = {500: 1, 200: 2, 100: 50}
        result = find_minimum_notes(2000, inventory)
        self.assertEqual(result, {500: 1, 200: 2, 100: 11})
        for value, count in result.items():
            self.assertLessEqual(count, inventory[value])

    def test_empty_slots_are_ignored(self):
        self.assertEqual(find_minimum_notes(700, {500: 0, 200: 3, 100: 1}), {200: 3, 100: 1})

    def test_never_changes_the_inventory_it_is_given(self):
        inventory = {500: 8, 200: 10, 100: 20}
        find_minimum_notes(2700, inventory)
        find_minimum_notes(250, inventory)
        self.assertEqual(inventory, {500: 8, 200: 10, 100: 20})


class AgainstBruteForceTests(unittest.TestCase):
    """Compare with an exhaustive search on many random small ATMs (fixed seed: repeatable)."""

    def test_matches_exhaustive_search_on_random_inventories(self):
        rng = random.Random(2026)
        for _ in range(400):
            denominations = rng.sample(range(1, 30), rng.randint(1, 4))
            inventory = {d: rng.randint(0, 4) for d in denominations}
            total_cash = sum(d * c for d, c in inventory.items())
            amount = rng.randint(1, max(1, total_cash + 5))

            expected = cheapest_by_trying_everything(amount, inventory)
            result = find_minimum_notes(amount, inventory)

            context = f"amount={amount}, inventory={inventory}"
            if expected is None:
                self.assertIsNone(result, context)
            else:
                self.assertIsNotNone(result, context)
                self.assertEqual(paid(result), amount, context)
                self.assertEqual(sum(result.values()), expected, context)
                for value, count in result.items():
                    self.assertGreater(count, 0, context)
                    self.assertLessEqual(count, inventory[value], context)


class PerformanceTests(unittest.TestCase):
    def test_huge_note_counts_stay_fast(self):
        # Only amount // value notes of each denomination can ever be used, so a machine
        # reporting a trillion notes is no slower than one with just enough.
        start = time.perf_counter()
        result = find_minimum_notes(100_000, {1: 10 ** 12, 2: 10 ** 12, 5: 10 ** 12})
        self.assertEqual(result, {5: 20_000})
        self.assertLess(time.perf_counter() - start, 5.0)

    def test_large_atm_withdrawal_is_fast(self):
        inventory = {2000: 500, 500: 2000, 200: 2000, 100: 2000, 50: 2000, 20: 2000, 10: 2000}
        start = time.perf_counter()
        result = find_minimum_notes(199_990, inventory)
        elapsed = time.perf_counter() - start
        self.assertEqual(paid(result), 199_990)
        self.assertLess(elapsed, 5.0, f"took {elapsed:.2f} s")


# ================================================================================================
# CashDispenser: dispensing, inventory updates and inventory consistency
# ================================================================================================


class SuccessfulWithdrawalTests(unittest.TestCase):
    def test_example_from_the_brief(self):
        atm = CashDispenser({500: 8, 200: 10, 100: 20})
        self.assertEqual(atm.dispense(2700), {500: 5, 200: 1})
        self.assertEqual(atm.inventory, {500: 3, 200: 9, 100: 20})

    def test_greedy_trap_from_the_brief(self):
        atm = CashDispenser({4: 1, 3: 2})
        self.assertEqual(atm.dispense(6), {3: 2})
        self.assertEqual(atm.inventory, {4: 1, 3: 0})

    def test_amount_given_as_text(self):
        atm = CashDispenser({500: 8, 200: 10, 100: 20})
        self.assertEqual(atm.dispense("2700"), {500: 5, 200: 1})

    def test_withdrawals_one_after_another_use_the_remaining_notes(self):
        atm = CashDispenser({500: 8, 200: 10, 100: 20})
        self.assertEqual(atm.dispense(2700), {500: 5, 200: 1})
        self.assertEqual(atm.dispense(2700), {500: 3, 200: 6})       # only 3 × ₹500 left
        self.assertEqual(atm.inventory, {500: 0, 200: 3, 100: 20})
        self.assertEqual(atm.dispense(800), {200: 3, 100: 2})        # no ₹500 left at all
        self.assertEqual(atm.inventory, {500: 0, 200: 0, 100: 18})

    def test_withdrawing_all_cash_empties_the_machine(self):
        atm = CashDispenser({500: 2, 100: 3})
        self.assertEqual(atm.dispense(1300), {500: 2, 100: 3})
        self.assertEqual(atm.inventory, {500: 0, 100: 0})
        self.assertEqual(atm.total_cash, 0)

    def test_other_denominations_are_supported(self):
        atm = CashDispenser({2000: 2, 50: 10, 20: 10, 10: 5, 1: 9})
        self.assertEqual(atm.dispense(4093), {2000: 2, 50: 1, 20: 2, 1: 3})
        self.assertEqual(atm.inventory, {2000: 0, 50: 9, 20: 8, 10: 5, 1: 6})

    def test_total_cash(self):
        self.assertEqual(CashDispenser({500: 8, 200: 10, 100: 20}).total_cash, 8000)


class FailedWithdrawalTests(unittest.TestCase):
    """Rules 6 and 7: a failed withdrawal returns None and leaves the inventory unchanged."""

    def setUp(self):
        self.atm = CashDispenser({500: 8, 200: 10, 100: 20})
        self.before = self.atm.inventory

    def assert_cannot_dispense(self, amount):
        self.assertIsNone(self.atm.dispense(amount))
        self.assertEqual(self.atm.inventory, self.before)

    def test_amount_that_cannot_be_made_exactly(self):
        self.assert_cannot_dispense(250)

    def test_amount_smaller_than_the_smallest_note(self):
        self.assert_cannot_dispense(50)

    def test_amount_larger_than_all_cash(self):
        self.assert_cannot_dispense(8100)

    def test_failure_after_notes_ran_out(self):
        atm = CashDispenser({500: 1})
        self.assertEqual(atm.dispense(500), {500: 1})
        self.assertIsNone(atm.dispense(500))
        self.assertEqual(atm.inventory, {500: 0})

    def test_machine_without_any_notes(self):
        atm = CashDispenser({500: 0, 100: 0})
        self.assertIsNone(atm.dispense(100))
        self.assertEqual(atm.inventory, {500: 0, 100: 0})

    def test_failure_message_matches_the_brief(self):
        self.assertEqual(CANNOT_DISPENSE, "Cannot dispense requested amount")

    def test_success_still_works_after_a_failure(self):
        self.assert_cannot_dispense(250)
        self.assertEqual(self.atm.dispense(300), {200: 1, 100: 1})


class InvalidInputTests(unittest.TestCase):
    def setUp(self):
        self.atm = CashDispenser({500: 8, 200: 10, 100: 20})
        self.before = self.atm.inventory

    def assert_invalid_and_unchanged(self, amount):
        with self.assertRaises(InvalidAmountError):
            self.atm.dispense(amount)
        self.assertEqual(self.atm.inventory, self.before)

    def test_zero_amount(self):
        self.assert_invalid_and_unchanged(0)

    def test_negative_amount(self):
        self.assert_invalid_and_unchanged(-2700)

    def test_non_numeric_amount(self):
        self.assert_invalid_and_unchanged("two thousand")

    def test_decimal_amount(self):
        self.assert_invalid_and_unchanged(2700.5)

    def test_missing_amount(self):
        self.assert_invalid_and_unchanged(None)

    def test_empty_inventory(self):
        with self.assertRaises(InvalidInventoryError):
            CashDispenser({})

    def test_negative_note_quantity(self):
        with self.assertRaises(InvalidInventoryError):
            CashDispenser({500: -8})

    def test_invalid_denomination(self):
        for denomination in (0, -500, 2.5, "500"):
            with self.subTest(denomination=denomination), self.assertRaises(InvalidInventoryError):
                CashDispenser({denomination: 1})


class DispenseWithInventoryTests(unittest.TestCase):
    def test_success_returns_notes_and_inventory_after(self):
        atm = CashDispenser({500: 8, 200: 10, 100: 20})
        notes, after = atm.dispense_with_inventory(2700)
        self.assertEqual(notes, {500: 5, 200: 1})
        self.assertEqual(after, {500: 3, 200: 9, 100: 20})

    def test_failure_returns_none_and_unchanged_inventory(self):
        atm = CashDispenser({500: 8, 200: 10, 100: 20})
        self.assertEqual(atm.dispense_with_inventory(250), (None, {500: 8, 200: 10, 100: 20}))

    def test_returned_inventory_is_a_copy(self):
        atm = CashDispenser({500: 8})
        _, after = atm.dispense_with_inventory(500)
        after[500] = 99
        self.assertEqual(atm.inventory, {500: 7})


class WithdrawalLimitTests(unittest.TestCase):
    def test_amount_up_to_the_limit_is_allowed(self):
        atm = CashDispenser({500: 8}, max_withdrawal=1000)
        self.assertEqual(atm.dispense(1000), {500: 2})

    def test_amount_above_the_limit_is_rejected_and_inventory_unchanged(self):
        atm = CashDispenser({500: 8}, max_withdrawal=1000)
        with self.assertRaisesRegex(InvalidAmountError, "exceeds the limit of 1000"):
            atm.dispense(1500)
        self.assertEqual(atm.inventory, {500: 8})

    def test_no_limit_by_default(self):
        self.assertIsNone(CashDispenser({500: 8}).max_withdrawal)

    def test_invalid_limit_is_rejected(self):
        for limit in (0, -1, "abc", 2.5):
            with self.subTest(limit=limit), self.assertRaises(InvalidAmountError):
                CashDispenser({500: 8}, max_withdrawal=limit)


class InventoryIsolationTests(unittest.TestCase):
    """Nobody outside the ATM can change its notes by accident."""

    def test_changing_the_original_dictionary_does_not_affect_the_atm(self):
        notes = {500: 8, 200: 10}
        atm = CashDispenser(notes)
        notes[500] = 1000
        self.assertEqual(atm.inventory, {500: 8, 200: 10})

    def test_changing_the_returned_inventory_does_not_affect_the_atm(self):
        atm = CashDispenser({500: 8})
        snapshot = atm.inventory
        snapshot[500] = 0
        self.assertEqual(atm.inventory, {500: 8})

    def test_changing_a_dispense_result_does_not_affect_the_atm(self):
        atm = CashDispenser({500: 8})
        result = atm.dispense(1000)
        result[500] = 8
        self.assertEqual(atm.inventory, {500: 6})

    def test_notes_are_conserved_over_many_withdrawals(self):
        start = {500: 8, 200: 10, 100: 20}
        atm = CashDispenser(start)
        paid_out = {500: 0, 200: 0, 100: 0}
        for amount in (2700, 250, 1300, 100, 900, 4000, 600, 300):
            notes = atm.dispense(amount)
            if notes:
                self.assertEqual(sum(v * c for v, c in notes.items()), amount)
                for value, count in notes.items():
                    paid_out[value] += count
        for value in start:
            self.assertEqual(atm.inventory[value] + paid_out[value], start[value])
            self.assertGreaterEqual(atm.inventory[value], 0)


# ================================================================================================
# Concurrency: several customers withdrawing at the same time never get the same notes
# The lock makes "allocate notes -> update inventory" one step. These tests start many threads at
# once and check that notes paid out plus notes left always equal the notes the machine started with.
# ================================================================================================


class SlowAllocator(NoteAllocator):
    """Finds the notes, then pauses: other threads would read the old inventory during the pause."""

    def find_minimum_notes(self, amount, inventory):
        result = super().find_minimum_notes(amount, inventory)
        time.sleep(0.02)
        return result


def withdraw_concurrently(atm, amounts):
    """Run one withdrawal per amount, all released at the same moment. Returns the results."""
    results = [None] * len(amounts)
    start_together = threading.Barrier(len(amounts))

    def customer(index, amount):
        start_together.wait()
        results[index] = atm.dispense(amount)

    threads = [threading.Thread(target=customer, args=(i, a)) for i, a in enumerate(amounts)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


class ConcurrentWithdrawalTests(unittest.TestCase):
    def assert_notes_conserved(self, start, atm, results):
        paid_out = dict.fromkeys(start, 0)
        for notes in results:
            for value, count in (notes or {}).items():
                paid_out[value] += count
        for value, count in start.items():
            self.assertGreaterEqual(atm.inventory[value], 0)
            self.assertEqual(atm.inventory[value] + paid_out[value], count, f"denomination {value}")

    def test_more_customers_than_notes(self):
        # 20 customers each want ₹500, but only 8 notes exist: exactly 8 must succeed.
        start = {500: 8}
        atm = CashDispenser(start)
        results = withdraw_concurrently(atm, [500] * 20)
        self.assertEqual(sum(1 for r in results if r), 8)
        self.assertEqual(atm.inventory, {500: 0})
        self.assert_notes_conserved(start, atm, results)

    def test_mixed_amounts_from_many_threads(self):
        start = {500: 8, 200: 10, 100: 20}
        atm = CashDispenser(start)
        amounts = [2700, 600, 300, 100, 1500, 900, 2000, 400, 250, 700] * 4
        results = withdraw_concurrently(atm, amounts)
        for amount, notes in zip(amounts, results, strict=True):
            if notes:
                self.assertEqual(sum(v * c for v, c in notes.items()), amount)
        self.assert_notes_conserved(start, atm, results)

    def test_lock_prevents_double_spending_even_when_allocation_is_slow(self):
        """Make allocation slow to widen the race window: without the lock, every thread
        would see the same single note and all of them would 'get' it."""
        atm = CashDispenser({500: 1}, allocator=SlowAllocator())
        results = withdraw_concurrently(atm, [500] * 10)

        self.assertEqual(sum(1 for r in results if r), 1)
        self.assertEqual(atm.inventory, {500: 0})

    def test_without_the_lock_the_race_really_happens(self):
        """Control experiment: proves the test above would catch a missing lock."""
        class NoLock:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        atm = CashDispenser({500: 1}, allocator=SlowAllocator())
        atm._lock = NoLock()  # deliberately remove the protection
        results = withdraw_concurrently(atm, [500] * 10)

        self.assertGreater(sum(1 for r in results if r), 1, "expected several threads to take the one note")


# ================================================================================================
# InventoryStore: the inventory in a JSON file survives restarts and can be shared by processes
# ================================================================================================

class StateFileTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.path = self.folder / "atm.json"

    def saved(self):
        return InventoryStore(self.path).load()

    def test_a_new_state_file_is_created_from_the_starting_inventory(self):
        CashDispenser({500: 8, 200: 10}, store=InventoryStore(self.path))
        self.assertEqual(self.saved(), {500: 8, 200: 10})

    def test_withdrawals_survive_a_restart(self):
        CashDispenser({500: 8, 200: 10, 100: 20}, store=InventoryStore(self.path)).dispense(2700)
        restarted = CashDispenser({500: 8, 200: 10, 100: 20}, store=InventoryStore(self.path))
        self.assertEqual(restarted.inventory, {500: 3, 200: 9, 100: 20})

    def test_an_existing_state_file_wins_over_the_starting_inventory(self):
        InventoryStore(self.path).save({500: 1})
        atm = CashDispenser({500: 8, 200: 10}, store=InventoryStore(self.path))
        self.assertEqual(atm.inventory, {500: 1})

    def test_a_failed_withdrawal_does_not_change_the_file(self):
        atm = CashDispenser({500: 8, 200: 10, 100: 20}, store=InventoryStore(self.path))
        before = self.path.read_bytes()
        self.assertIsNone(atm.dispense(250))
        self.assertEqual(self.path.read_bytes(), before)

    def test_a_failed_save_changes_nothing(self):
        atm = CashDispenser({500: 8}, store=InventoryStore(self.path))
        with mock.patch.object(InventoryStore, "save", side_effect=OSError("disk full")), \
                self.assertRaises(OSError):
            atm.dispense(500)
        self.assertEqual(atm.inventory, {500: 8})
        self.assertEqual(self.saved(), {500: 8})

    def test_no_temporary_files_are_left_behind(self):
        atm = CashDispenser({500: 8}, store=InventoryStore(self.path))
        for _ in range(3):
            atm.dispense(500)
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()), ["atm.json", "atm.json.lock"])

    def test_invalid_state_files_are_rejected_with_a_clear_message(self):
        for content in ("{bad", "[]", '{"inventory": []}', '{"inventory": {"abc": 1}}',
                        '{"inventory": {"500": "8"}}', '{"inventory": {"500": -1}}', '{"inventory": {}}'):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaises(InvalidInventoryError):
                    CashDispenser({500: 8}, store=InventoryStore(self.path))

    def test_the_same_denomination_written_twice_is_rejected(self):
        for content in ('{"inventory": {"500": 1, "0500": 2}}', '{"inventory": {"500": 1, "500": 2}}'):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                with self.assertRaises(InvalidInventoryError):
                    InventoryStore(self.path).load()

    def test_a_file_saved_with_a_byte_order_mark_is_accepted(self):
        self.path.write_bytes(b'\xef\xbb\xbf{"inventory": {"500": 2}}')
        self.assertEqual(InventoryStore(self.path).load(), {500: 2})

    def test_a_symbolic_link_keeps_pointing_at_the_real_file(self):
        real = self.folder / "real.json"
        InventoryStore(real).save({500: 2})
        link = self.folder / "link.json"
        link.symlink_to(real)
        CashDispenser({500: 9}, store=InventoryStore(link)).dispense(500)
        self.assertTrue(link.is_symlink())
        self.assertEqual(InventoryStore(real).load(), {500: 1})

    def test_two_atms_sharing_one_file_never_pay_out_the_same_notes(self):
        # Two CashDispenser objects stand in for two processes: their thread locks are separate,
        # so only the file lock keeps them apart. 20 customers want 2 notes each; 8 notes exist.
        first = CashDispenser({500: 8}, allocator=SlowAllocator(), store=InventoryStore(self.path))
        second = CashDispenser({500: 8}, allocator=SlowAllocator(), store=InventoryStore(self.path))
        results = []
        threads = [threading.Thread(target=lambda atm=atm: results.append(atm.dispense(1000)))
                   for atm in [first, second] * 10]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        paid = [notes for notes in results if notes is not None]
        self.assertEqual(len(paid), 4)
        self.assertEqual(self.saved(), {500: 0})
        self.assertEqual(first.inventory, second.inventory)


# ================================================================================================
# Found by adversarial testing: each test is a bug that used to crash, hang or mislead
# ================================================================================================

class HostileInputRegressionTests(unittest.TestCase):
    def test_a_huge_calculation_is_refused_instead_of_exhausting_memory(self):
        atm = CashDispenser({1: 10 ** 12}, max_withdrawal=10 ** 12)
        with self.assertRaisesRegex(InvalidAmountError, "too large to work out"):
            atm.dispense(10 ** 12)
        self.assertEqual(atm.inventory, {1: 10 ** 12})

    def test_many_denominations_are_refused_instead_of_slowing_every_withdrawal(self):
        with self.assertRaisesRegex(InvalidInventoryError, "Too many denominations: 51"):
            CashDispenser(dict.fromkeys(range(1, 52), 1))
        CashDispenser(dict.fromkeys(range(1, 51), 1))  # 50 is still fine

    def test_a_realistic_large_inventory_is_still_calculated(self):
        atm = CashDispenser(dict.fromkeys((1, 2, 5, 10, 20, 50, 100, 200, 500, 2000), 1000))
        self.assertEqual(sum(v * c for v, c in atm.dispense(199_999).items()), 199_999)

    def test_long_input_is_never_echoed_in_full(self):
        for value in ("x" * 5000, [[[[0]]]] * 500, 1.5e300):
            with self.subTest(value=type(value).__name__), self.assertRaises(InvalidAmountError) as raised:
                Validator.amount(value)
            self.assertLess(len(str(raised.exception)), 120)
        with self.assertRaisesRegex(InvalidAmountError, "too large: at most 18 digits"):
            Validator.amount(10 ** 300)


# ================================================================================================
# Property / fuzz tests: validation only returns clean values or our own error; invariants hold
# ================================================================================================


SEED = 20261120


def random_value(rng):
    """Any kind of value a careless or malicious caller might send."""
    choice = rng.randrange(10)
    if choice == 0:
        return rng.randint(-10 ** 6, 10 ** 6)
    if choice == 1:
        return rng.uniform(-1e6, 1e6)
    if choice == 2:
        return "".join(rng.choice(string.printable) for _ in range(rng.randint(0, 12)))
    if choice == 3:
        return str(rng.randint(-10 ** 25, 10 ** 25))
    if choice == 4:
        return rng.choice([None, True, False, [], {}, (), b"100", float("nan"), float("-inf")])
    if choice == 5:
        return rng.choice(["١٠٠", "１００", "100\n", "0x64", "1e2", "--1", "+-1", " ", "١"])
    if choice == 6:
        return 10 ** rng.randint(10, 400)
    if choice == 7:
        return [rng.randint(1, 5)]
    if choice == 8:
        return "".join(rng.choice("0123456789") for _ in range(rng.randint(1, 40)))
    return rng.randint(1, 10_000)


class ValidationFuzzTests(unittest.TestCase):
    def test_validate_amount_only_returns_positive_ints_or_raises_our_error(self):
        rng = random.Random(SEED)
        for _ in range(5000):
            value = random_value(rng)
            try:
                result = Validator.amount(value)
            except InvalidAmountError:
                continue
            self.assertIs(type(result), int, repr(value))
            self.assertGreater(result, 0, repr(value))

    def test_validate_inventory_only_returns_clean_dicts_or_raises_our_error(self):
        rng = random.Random(SEED)
        for _ in range(3000):
            pairs = [(random_value(rng), random_value(rng)) for _ in range(rng.randint(0, 4))]
            inventory = {key: value for key, value in pairs if isinstance(key, Hashable)}
            try:
                result = Validator.inventory(inventory)
            except InvalidInventoryError:
                continue
            for denomination, count in result.items():
                self.assertIs(type(denomination), int)
                self.assertIs(type(count), int)
                self.assertGreater(denomination, 0)
                self.assertGreaterEqual(count, 0)

    def test_parse_inventory_never_crashes(self):
        rng = random.Random(SEED)
        alphabet = "0123456789:,{} -+.abc\t"
        for _ in range(3000):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 30)))
            with contextlib.suppress(InvalidInventoryError):
                Validator.parse_inventory(text)


class DispenserPropertyTests(unittest.TestCase):
    def test_random_withdrawal_sequences_keep_every_invariant(self):
        rng = random.Random(SEED)
        for _ in range(150):
            denominations = rng.sample([1, 2, 5, 10, 20, 50, 100, 200, 500, 2000], rng.randint(1, 5))
            start = {d: rng.randint(0, 15) for d in denominations}
            atm = CashDispenser(start)
            paid_out = dict.fromkeys(start, 0)
            for _ in range(15):
                amount = random_value(rng) if rng.random() < 0.3 else rng.randint(1, max(1, atm.total_cash))
                before = atm.inventory
                try:
                    notes = atm.dispense(amount)
                except InvalidAmountError:
                    self.assertEqual(atm.inventory, before)
                    continue
                if notes is None:
                    self.assertEqual(atm.inventory, before)
                    continue
                self.assertEqual(sum(v * c for v, c in notes.items()), Validator.amount(amount))
                for value, count in notes.items():
                    self.assertGreater(count, 0)
                    self.assertLessEqual(count, before[value])
                    paid_out[value] += count
            for value, count in start.items():
                self.assertEqual(atm.inventory[value] + paid_out[value], count)


if __name__ == "__main__":
    unittest.main()
