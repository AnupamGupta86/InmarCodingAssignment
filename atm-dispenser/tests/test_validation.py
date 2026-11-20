"""Validation of withdrawal amounts and ATM inventories (the "Validation Requirements")."""

import unittest
from collections import OrderedDict

from atm.errors import ATMError, InvalidAmountError, InvalidInventoryError
from atm.validation import validate_amount, validate_inventory


class ValidAmountTests(unittest.TestCase):
    def test_positive_whole_number_is_accepted(self):
        self.assertEqual(validate_amount(2700), 2700)

    def test_smallest_positive_amount_is_accepted(self):
        self.assertEqual(validate_amount(1), 1)

    def test_whole_number_text_is_accepted(self):
        self.assertEqual(validate_amount("2700"), 2700)

    def test_text_with_surrounding_spaces_and_plus_sign_is_accepted(self):
        self.assertEqual(validate_amount("  +2700 "), 2700)

    def test_very_large_amount_is_accepted(self):
        self.assertEqual(validate_amount(10 ** 12), 10 ** 12)


class InvalidAmountTests(unittest.TestCase):
    def assert_rejected(self, amount, message_part):
        with self.assertRaises(InvalidAmountError) as raised:
            validate_amount(amount)
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

    def test_extremely_long_number_text_is_rejected_before_conversion(self):
        self.assert_rejected("9" * 19, "too large")
        self.assert_rejected("1" * 100_000, "too large")

    def test_amount_errors_are_value_errors(self):
        with self.assertRaises(ValueError):
            validate_amount(-1)


class ValidInventoryTests(unittest.TestCase):
    def test_example_inventory_is_accepted(self):
        self.assertEqual(validate_inventory({500: 8, 200: 10, 100: 20}), {500: 8, 200: 10, 100: 20})

    def test_result_is_sorted_largest_denomination_first(self):
        result = validate_inventory({100: 20, 500: 8, 200: 10})
        self.assertEqual(list(result), [500, 200, 100])

    def test_zero_notes_of_a_denomination_is_allowed(self):
        self.assertEqual(validate_inventory({500: 0, 100: 3}), {500: 0, 100: 3})

    def test_any_denomination_is_supported(self):
        inventory = {2000: 1, 50: 4, 20: 7, 10: 3, 1: 9}
        self.assertEqual(validate_inventory(inventory), inventory)

    def test_other_mapping_types_are_accepted(self):
        self.assertEqual(validate_inventory(OrderedDict([(100, 1)])), {100: 1})

    def test_result_is_an_independent_copy(self):
        original = {500: 8}
        result = validate_inventory(original)
        result[500] = 0
        self.assertEqual(original, {500: 8})


class InvalidInventoryTests(unittest.TestCase):
    def assert_rejected(self, inventory, message_part):
        with self.assertRaises(InvalidInventoryError) as raised:
            validate_inventory(inventory)
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
            validate_inventory({})


if __name__ == "__main__":
    unittest.main()
