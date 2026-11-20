"""The command-line program: inventory parsing, output format and exit codes."""

import io
import subprocess
import sys
import unittest
from pathlib import Path

from atm.cli import (
    EXIT_INVALID_INPUT,
    EXIT_NOT_DISPENSED,
    EXIT_OK,
    format_notes,
    main,
    parse_inventory,
)
from atm.errors import InvalidInventoryError

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run(argv, stdin_text=""):
    """Run the CLI in-process. Returns (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    code = main(argv, stdin=io.StringIO(stdin_text), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


class ParseInventoryTests(unittest.TestCase):
    def test_simple_format(self):
        self.assertEqual(parse_inventory("500:8, 200:10, 100:20"), {500: 8, 200: 10, 100: 20})

    def test_format_used_in_the_brief(self):
        self.assertEqual(parse_inventory("{ 500: 8, 200: 10, 100: 20 }"), {500: 8, 200: 10, 100: 20})

    def test_trailing_comma_is_tolerated(self):
        self.assertEqual(parse_inventory("500:8,"), {500: 8})

    def test_negative_values_are_parsed_and_left_for_the_atm_to_reject(self):
        self.assertEqual(parse_inventory("500:-1"), {500: -1})

    def test_rejects_empty_text(self):
        for text in ("", "   ", "{}", "{ }"):
            with self.subTest(text=text), self.assertRaises(InvalidInventoryError):
                parse_inventory(text)

    def test_rejects_missing_count(self):
        with self.assertRaises(InvalidInventoryError):
            parse_inventory("500")

    def test_rejects_extra_colon(self):
        with self.assertRaises(InvalidInventoryError):
            parse_inventory("500:8:1")

    def test_rejects_non_numeric_denomination(self):
        with self.assertRaisesRegex(InvalidInventoryError, "denomination 'abc'"):
            parse_inventory("abc:8")

    def test_rejects_non_numeric_count(self):
        with self.assertRaisesRegex(InvalidInventoryError, "note count"):
            parse_inventory("500:many")

    def test_rejects_duplicate_denomination(self):
        with self.assertRaisesRegex(InvalidInventoryError, "more than once"):
            parse_inventory("500:8, 500:2")


class FormatTests(unittest.TestCase):
    def test_notes_are_listed_largest_first(self):
        self.assertEqual(format_notes({200: 1, 500: 5}), "₹500 × 5, ₹200 × 1")

    def test_no_notes(self):
        self.assertEqual(format_notes({}), "no notes")


class CommandLineTests(unittest.TestCase):
    def test_example_from_the_brief(self):
        code, out, _ = run(["--inventory", "{ 500: 8, 200: 10, 100: 20 }", "2700"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("₹500 × 5, ₹200 × 1 (Total notes dispensed: 6)", out)
        self.assertIn("Updated inventory: ₹500 × 3, ₹200 × 9, ₹100 × 20", out)

    def test_default_inventory_is_the_one_from_the_brief(self):
        code, out, _ = run(["2700"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("ATM inventory: ₹500 × 8, ₹200 × 10, ₹100 × 20", out)

    def test_several_withdrawals_share_one_atm(self):
        code, out, _ = run(["2700", "2700"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Updated inventory: ₹500 × 0, ₹200 × 3, ₹100 × 20", out)

    def test_cannot_dispense(self):
        code, out, _ = run(["250"])
        self.assertEqual(code, EXIT_NOT_DISPENSED)
        self.assertIn("Cannot dispense requested amount", out)
        self.assertIn("Inventory unchanged: ₹500 × 8, ₹200 × 10, ₹100 × 20", out)

    def test_invalid_amounts_are_reported_on_stderr(self):
        for amount, message in (("0", "greater than zero"), ("-5", "cannot be negative"),
                                ("abc", "must be a number"), ("12.5", "whole number")):
            with self.subTest(amount=amount):
                code, out, err = run(["--", amount])
                self.assertEqual(code, EXIT_INVALID_INPUT)
                self.assertIn(message, err)
                self.assertNotIn("Withdrawal", out)

    def test_invalid_inventory_is_reported(self):
        for inventory, message in (("{}", "empty"), ("500:-1", "cannot be negative"),
                                   ("0:5", "Invalid denomination 0"), ("x:1", "denomination 'x'")):
            with self.subTest(inventory=inventory):
                code, _, err = run(["--inventory", inventory, "100"])
                self.assertEqual(code, EXIT_INVALID_INPUT)
                self.assertIn(message, err)

    def test_worst_result_decides_the_exit_code(self):
        code, _, _ = run(["2700", "250", "100"])
        self.assertEqual(code, EXIT_NOT_DISPENSED)
        code, _, _ = run(["2700", "abc", "250"])
        self.assertEqual(code, EXIT_INVALID_INPUT)

    def test_interactive_mode_reads_amounts_until_quit(self):
        code, out, err = run([], stdin_text="2700\n\nabc\n600\nq\n2700\n")
        self.assertEqual(code, EXIT_INVALID_INPUT)  # 'abc' was invalid
        self.assertEqual(out.count("Withdrawal"), 2)  # 2700 and 600; the 2700 after 'q' is ignored
        self.assertIn("must be a number", err)

    def test_interactive_mode_ends_at_end_of_input(self):
        code, out, _ = run([], stdin_text="2700\n")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Total notes dispensed: 6", out)


class RunAsModuleTests(unittest.TestCase):
    def test_python_dash_m_atm(self):
        result = subprocess.run(
            [sys.executable, "-m", "atm", "--inventory", "4:1, 3:2", "6"],
            cwd=PROJECT_ROOT / "src", capture_output=True, text=True, encoding="utf-8",
            env={"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "PATH": ""}, timeout=60,
        )
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        self.assertIn("₹3 × 2 (Total notes dispensed: 2)", result.stdout)


if __name__ == "__main__":
    unittest.main()
