"""The command-line program: inventory parsing, output format and exit codes."""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cli import EXIT_INVALID_INPUT, EXIT_NOT_DISPENSED, EXIT_OK, CommandLine

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run(argv, stdin_text=""):
    """Run the CLI in-process. Returns (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    code = CommandLine(stdin=io.StringIO(stdin_text), stdout=out, stderr=err).run(argv)
    return code, out.getvalue(), err.getvalue()


class FormatTests(unittest.TestCase):
    def test_notes_are_listed_largest_first(self):
        self.assertEqual(CommandLine.format_notes({200: 1, 500: 5}), "₹500 × 5, ₹200 × 1")

    def test_no_notes(self):
        self.assertEqual(CommandLine.format_notes({}), "no notes")


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

    def test_withdrawal_limit_defaults_to_200000(self):
        code, _, err = run(["--inventory", "2000:200", "202000"])
        self.assertEqual(code, EXIT_INVALID_INPUT)
        self.assertIn("exceeds the limit of 200000", err)

    def test_withdrawal_limit_can_be_changed(self):
        code, out, _ = run(["--inventory", "2000:200", "--max-withdrawal", "400000", "202000"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("₹2000 × 101", out)
        code, _, err = run(["--max-withdrawal", "1000", "2700"])
        self.assertEqual(code, EXIT_INVALID_INPUT)
        self.assertIn("exceeds the limit of 1000", err)

    def test_invalid_withdrawal_limit(self):
        for limit in ("0", "-5", "abc"):
            with self.subTest(limit=limit):
                code, _, err = run(["--max-withdrawal", limit, "100"])
                self.assertEqual(code, EXIT_INVALID_INPUT)
                self.assertIn("Invalid --max-withdrawal", err)

    def test_huge_amount_with_huge_inventory_is_refused_quickly(self):
        code, _, err = run(["--inventory", "1:" + "9" * 15, "9" * 15])
        self.assertEqual(code, EXIT_INVALID_INPUT)
        self.assertIn("exceeds the limit", err)

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

    def test_interactive_inventory_command_shows_current_notes(self):
        code, out, _ = run([], stdin_text="2700\ni\nq\n")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("ATM inventory: ₹500 × 3, ₹200 × 9, ₹100 × 20 (total cash ₹5300)", out)
        self.assertEqual(out.count("Withdrawal"), 1)  # 'i' does not withdraw anything

    def test_interactive_inventory_command_is_case_insensitive_and_has_a_long_form(self):
        code, out, err = run([], stdin_text="I\ninventory\nINVENTORY\n")
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(out.count("(total cash ₹8000)"), 3)
        self.assertNotIn("Withdrawal", out)
        self.assertEqual(err, "")

    def test_ctrl_c_ends_interactive_mode_quietly(self):
        class TypesThenPressesCtrlC:
            def __iter__(self):
                yield "2700\n"
                raise KeyboardInterrupt

        out, err = io.StringIO(), io.StringIO()
        code = CommandLine(stdin=TypesThenPressesCtrlC(), stdout=out, stderr=err).run([])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Total notes dispensed: 6", out.getvalue())
        self.assertEqual(err.getvalue(), "")

    def test_interactive_mode_ends_at_end_of_input(self):
        code, out, _ = run([], stdin_text="2700\n")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Total notes dispensed: 6", out)


class StateFileTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = str(Path(folder.name) / "atm.json")

    def test_the_inventory_carries_over_to_the_next_run(self):
        self.assertEqual(run(["--state-file", self.path, "2700"])[0], EXIT_OK)
        code, out, _ = run(["--state-file", self.path, "2700"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("ATM inventory: ₹500 × 3, ₹200 × 9, ₹100 × 20", out)

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "needs a non-root POSIX user")
    def test_a_failed_save_is_reported_and_nothing_is_dispensed(self):
        run(["--state-file", self.path, "100"])
        folder = Path(self.path).parent
        folder.chmod(0o500)  # the folder becomes read-only: the next save cannot happen
        try:
            code, out, err = run(["--state-file", self.path, "100"])
        finally:
            folder.chmod(0o700)
        self.assertEqual(code, EXIT_INVALID_INPUT)
        self.assertIn("Cannot save the state file", err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn("Withdrawal", out)

    def test_a_corrupt_state_file_is_invalid_input(self):
        Path(self.path).write_text("{bad", encoding="utf-8")
        code, _, err = run(["--state-file", self.path, "100"])
        self.assertEqual(code, EXIT_INVALID_INPUT)
        self.assertIn("Cannot read the state file", err)

    def test_a_state_file_in_a_missing_folder_is_invalid_input(self):
        code, _, err = run(["--state-file", str(Path(self.path).parent / "missing" / "atm.json"), "100"])
        self.assertEqual(code, EXIT_INVALID_INPUT)
        self.assertIn("Cannot use the state file", err)


class RunAsModuleTests(unittest.TestCase):
    def test_python_dash_m_cli(self):
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "src" / "cli.py"), "--inventory", "4:1, 3:2", "6"],
            cwd=PROJECT_ROOT.parent, capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}, timeout=60,
        )
        self.assertEqual(result.returncode, EXIT_OK, result.stderr)
        self.assertIn("₹3 × 2 (Total notes dispensed: 2)", result.stdout)


if __name__ == "__main__":
    unittest.main()
