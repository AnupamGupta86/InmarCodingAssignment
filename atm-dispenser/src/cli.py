"""Command-line front end for the ATM.

    python3 atm-dispenser/src/cli.py 2700                         # default inventory from the brief
    python3 atm-dispenser/src/cli.py --inventory "{ 500: 8, 200: 10, 100: 20 }" 2700 600
    python3 atm-dispenser/src/cli.py --inventory "4:1, 3:2" 6
    python3 atm-dispenser/src/cli.py                              # interactive: type amounts, q to quit

Several amounts are withdrawn one after another from the same ATM, so you can watch the
inventory go down. Exit code: 0 = every withdrawal succeeded, 1 = at least one could not be
dispensed, 2 = invalid input.
"""

import argparse
import sys
from collections.abc import Iterable
from typing import TextIO

from dispenser import (
    CANNOT_DISPENSE,
    DEFAULT_INVENTORY,
    DEFAULT_MAX_WITHDRAWAL,
    ATMError,
    CashDispenser,
    InvalidInventoryError,
    Notes,
    Validator,
)

CURRENCY = "₹"

EXIT_OK = 0
EXIT_NOT_DISPENSED = 1
EXIT_INVALID_INPUT = 2


class CommandLine:
    """Parses the arguments, runs the withdrawals and prints the results."""

    def __init__(self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr):
        self.stdin = stdin
        self.out = stdout
        self.err = stderr

    def run(self, argv: list[str] | None = None) -> int:
        """Run the program. Returns the exit code: the worst result of all withdrawals."""
        args = self._parser().parse_args(argv)
        atm = self._create_atm(args.inventory, args.max_withdrawal)
        if atm is None:
            return EXIT_INVALID_INPUT

        self._print(f"ATM inventory: {self.format_notes(atm.inventory)}")
        if not args.amounts:
            return self.run_interactive(atm, self.stdin)
        return max(self.withdraw(atm, amount) for amount in args.amounts)

    def withdraw(self, atm: CashDispenser, amount_text: str) -> int:
        """Run one withdrawal and print the result. Returns the exit code for it."""
        try:
            amount = Validator.amount(amount_text)
            notes = atm.dispense(amount)
        except ATMError as error:
            self._print(f"Invalid amount {amount_text!r}: {error}", error=True)
            return EXIT_INVALID_INPUT

        self._print(self.format_withdrawal(amount, notes))
        label = "Inventory unchanged" if notes is None else "Updated inventory"
        self._print(f"{label}: {self.format_notes(atm.inventory)}")
        return EXIT_NOT_DISPENSED if notes is None else EXIT_OK

    def run_interactive(self, atm: CashDispenser, lines: Iterable[str]) -> int:
        """Read one amount per line until 'q', 'quit', 'exit', end of input or Ctrl+C."""
        worst = EXIT_OK
        self._print("Enter an amount to withdraw (q to quit).")
        try:
            for line in lines:
                text = line.strip()
                if text.lower() in ("q", "quit", "exit"):
                    break
                if text:
                    worst = max(worst, self.withdraw(atm, text))
        except KeyboardInterrupt:  # Ctrl+C ends the session quietly
            self._print("")
        return worst

    @staticmethod
    def format_notes(notes: Notes) -> str:
        """{500: 5, 200: 1} -> "₹500 × 5, ₹200 × 1" (largest denomination first)."""
        if not notes:
            return "no notes"
        return ", ".join(f"{CURRENCY}{value} × {count}" for value, count in sorted(notes.items(), reverse=True))

    @classmethod
    def format_withdrawal(cls, amount: int, notes: Notes | None) -> str:
        """The result line, e.g. "Withdrawal ₹2700: ₹500 × 5, ₹200 × 1 (Total notes dispensed: 6)"."""
        if notes is None:
            return f"Withdrawal {CURRENCY}{amount}: {CANNOT_DISPENSE}"
        total = sum(notes.values())
        return f"Withdrawal {CURRENCY}{amount}: {cls.format_notes(notes)} (Total notes dispensed: {total})"

    def _create_atm(self, inventory_text: str, max_withdrawal: str) -> CashDispenser | None:
        try:
            inventory = Validator.parse_inventory(inventory_text)
            return CashDispenser(inventory, max_withdrawal=max_withdrawal)
        except InvalidInventoryError as error:
            self._print(f"Invalid inventory: {error}", error=True)
        except ATMError as error:
            self._print(f"Invalid --max-withdrawal: {error}", error=True)
        return None

    @staticmethod
    def _parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="python3 cli.py",
            description="Simulate an ATM cash dispenser that pays out with the fewest notes.",
        )
        parser.add_argument("--inventory", default=DEFAULT_INVENTORY,
                            help=f'notes in the ATM as "denomination:count, ..." (default: "{DEFAULT_INVENTORY}")')
        parser.add_argument("--max-withdrawal", default=str(DEFAULT_MAX_WITHDRAWAL),
                            help=f"largest amount per withdrawal (default: {DEFAULT_MAX_WITHDRAWAL})")
        parser.add_argument("amounts", nargs="*", help="amounts to withdraw, in order")
        return parser

    def _print(self, text: str, error: bool = False) -> None:
        print(text, file=self.err if error else self.out)


if __name__ == "__main__":
    sys.exit(CommandLine().run())
