"""Fuzz / property-based tests (standard library only, fixed seeds so failures are repeatable).

Instead of checking single examples, these throw thousands of random inputs at the code and
check rules that must ALWAYS hold:
  - validation either returns a clean value or raises our own error - never anything else
  - a dispensed result always adds up to the amount and never uses notes the ATM lacks
  - a failed or invalid withdrawal never changes the inventory
  - the HTTP service never answers 500, whatever bytes it receives
"""

import contextlib
import json
import random
import string
import unittest
from collections.abc import Hashable

from dispenser import CashDispenser, InvalidAmountError, InvalidInventoryError, Validator
from tests.test_service import RunningServer

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


class HttpFuzzTests(unittest.TestCase):
    def test_service_never_returns_500(self):
        rng = random.Random(SEED)
        with RunningServer() as srv:
            for _ in range(300):
                kind = rng.randrange(3)
                if kind == 0:
                    raw = json.dumps({"amount": random_value(rng)}, default=str).encode()
                elif kind == 1:
                    raw = bytes(rng.randrange(256) for _ in range(rng.randint(0, 60)))
                else:
                    raw = json.dumps({random_value(rng).__repr__(): random_value(rng)}, default=str).encode()
                status, body, _ = srv.request("POST", "/withdrawals", raw_body=raw,
                                              headers={"Content-Type": "application/json"})
                self.assertIn(status, (200, 400, 422), (raw, body))
                self.assertIn("error" if status != 200 else "dispensed", body)


if __name__ == "__main__":
    unittest.main()
