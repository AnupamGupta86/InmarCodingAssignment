"""The allocation algorithm: correct, minimal, within the available notes, never greedy-only."""

import itertools
import random
import time
import unittest

from atm.allocator import find_minimum_notes


def paid(notes):
    return sum(value * count for value, count in notes.items())


def cheapest_by_trying_everything(amount, inventory):
    """Reference answer: try every possible combination of the available notes."""
    denominations = list(inventory)
    best = None
    for counts in itertools.product(*(range(inventory[d] + 1) for d in denominations)):
        pays_exactly = sum(d * c for d, c in zip(denominations, counts)) == amount
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
    def test_large_atm_withdrawal_is_fast(self):
        inventory = {2000: 500, 500: 2000, 200: 2000, 100: 2000, 50: 2000, 20: 2000, 10: 2000}
        start = time.perf_counter()
        result = find_minimum_notes(199_990, inventory)
        elapsed = time.perf_counter() - start
        self.assertEqual(paid(result), 199_990)
        self.assertLess(elapsed, 5.0, f"took {elapsed:.2f} s")


if __name__ == "__main__":
    unittest.main()
