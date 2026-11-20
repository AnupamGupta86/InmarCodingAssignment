"""The ATM (CashDispenser): dispensing, inventory updates and inventory consistency."""

import unittest

from atm import CANNOT_DISPENSE, CashDispenser, InvalidAmountError, InvalidInventoryError


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


if __name__ == "__main__":
    unittest.main()
