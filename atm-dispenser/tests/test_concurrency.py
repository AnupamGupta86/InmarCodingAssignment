"""Several customers withdrawing at the same time must never get the same notes.

The ATM protects "allocate notes -> update inventory" with a lock. These tests start many
threads at once and check that the notes paid out plus the notes left always equal the notes
the machine started with, and that no count ever goes below zero.
"""

import threading
import time
import unittest
from unittest import mock

import allocator
import dispenser
from dispenser import CashDispenser


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
        for amount, notes in zip(amounts, results):
            if notes:
                self.assertEqual(sum(v * c for v, c in notes.items()), amount)
        self.assert_notes_conserved(start, atm, results)

    def test_lock_prevents_double_spending_even_when_allocation_is_slow(self):
        """Make allocation slow to widen the race window: without the lock, every thread
        would see the same single note and all of them would 'get' it."""
        real_allocator = allocator.find_minimum_notes

        def slow_allocator(amount, inventory):
            result = real_allocator(amount, inventory)
            time.sleep(0.02)  # other threads would read the old inventory during this pause
            return result

        with mock.patch.object(dispenser, "find_minimum_notes", slow_allocator):
            atm = CashDispenser({500: 1})
            results = withdraw_concurrently(atm, [500] * 10)

        self.assertEqual(sum(1 for r in results if r), 1)
        self.assertEqual(atm.inventory, {500: 0})

    def test_without_the_lock_the_race_really_happens(self):
        """Control experiment: proves the test above would catch a missing lock."""
        real_allocator = allocator.find_minimum_notes

        def slow_allocator(amount, inventory):
            result = real_allocator(amount, inventory)
            time.sleep(0.02)
            return result

        class NoLock:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with mock.patch.object(dispenser, "find_minimum_notes", slow_allocator):
            atm = CashDispenser({500: 1})
            atm._lock = NoLock()  # deliberately remove the protection
            results = withdraw_concurrently(atm, [500] * 10)

        self.assertGreater(sum(1 for r in results if r), 1, "expected several threads to take the one note")


if __name__ == "__main__":
    unittest.main()
