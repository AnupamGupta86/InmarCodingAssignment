"""Command-line front end for the ATM.

    python3 atm-dispenser/src/cli.py 2700                         # default inventory from the brief
    python3 atm-dispenser/src/cli.py --inventory "{ 500: 8, 200: 10, 100: 20 }" 2700 600
    python3 atm-dispenser/src/cli.py --inventory "4:1, 3:2" 6
    python3 atm-dispenser/src/cli.py --state-file atm.json 2700      # inventory kept in atm.json
    python3 atm-dispenser/src/cli.py                              # interactive (i = inventory, q = quit)

Several amounts are withdrawn one after another from the same ATM, so you can watch the
inventory go down.

Exit code: 0 = every withdrawal succeeded, 1 = at least one could not be dispensed,
2 = invalid input, or a state file that cannot be read or saved.
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
    InventoryStore,
    Notes,
    Validator,
)

CURRENCY = "₹"

EXIT_OK = 0
EXIT_NOT_DISPENSED = 1     # "Cannot dispense requested amount"
EXIT_INVALID_INPUT = 2     # invalid input, or a state file that cannot be read or saved

QUIT_COMMANDS = ("q", "quit", "exit")
INVENTORY_COMMANDS = ("i", "inventory")


class CommandLine:
    """Parses the arguments, runs the withdrawals and prints the results."""

    def __init__(
        self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr
    ):
        self.stdin = stdin
        self.out = stdout
        self.err = stderr

    def run(self, argv: list[str] | None = None) -> int:
        """Run the program. Returns the exit code: the worst result of all withdrawals."""
        args = self._parser().parse_args(argv)
        atm = self._create_atm(args.inventory, args.max_withdrawal, args.state_file)
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
            self._print(f"Invalid amount {Validator.preview(amount_text)}: {error}", error=True)
            return EXIT_INVALID_INPUT
        except OSError as error:  # the state file could not be saved: nothing was dispensed
            self._print(f"Cannot save the state file ({error}); nothing was dispensed", error=True)
            return EXIT_INVALID_INPUT

        self._print(self.format_withdrawal(amount, notes))
        label = "Inventory unchanged" if notes is None else "Updated inventory"
        self._print(f"{label}: {self.format_notes(atm.inventory)}")
        return EXIT_NOT_DISPENSED if notes is None else EXIT_OK

    def run_interactive(self, atm: CashDispenser, lines: Iterable[str]) -> int:
        """Read one amount per line until 'q', 'quit', 'exit', end of input or Ctrl+C.

        'i' or 'inventory' shows the notes currently in the ATM without withdrawing anything.
        """
        worst = EXIT_OK
        self._print("Enter an amount to withdraw, i to show the inventory, q to quit.")
        try:
            for line in lines:
                text = line.strip()
                command = text.lower()
                if command in QUIT_COMMANDS:
                    break
                if command in INVENTORY_COMMANDS:
                    self.show_inventory(atm)
                elif text:
                    worst = max(worst, self.withdraw(atm, text))
        except KeyboardInterrupt:  # Ctrl+C ends the session quietly
            self._print("")
        return worst

    def show_inventory(self, atm: CashDispenser) -> None:
        """Print the notes in the ATM and their total value."""
        notes = self.format_notes(atm.inventory)
        self._print(f"ATM inventory: {notes} (total cash {CURRENCY}{atm.total_cash})")

    @staticmethod
    def format_notes(notes: Notes) -> str:
        """{500: 5, 200: 1} -> "₹500 × 5, ₹200 × 1" (largest denomination first)."""
        if not notes:
            return "no notes"
        largest_first = sorted(notes.items(), reverse=True)
        return ", ".join(f"{CURRENCY}{value} × {count}" for value, count in largest_first)

    @classmethod
    def format_withdrawal(cls, amount: int, notes: Notes | None) -> str:
        """The result line, e.g. "Withdrawal ₹2700: ₹500 × 5, ₹200 × 1 (Total notes dispensed: 6)"."""
        if notes is None:
            return f"Withdrawal {CURRENCY}{amount}: {CANNOT_DISPENSE}"
        total = sum(notes.values())
        return f"Withdrawal {CURRENCY}{amount}: {cls.format_notes(notes)} (Total notes dispensed: {total})"

    def _create_atm(
        self, inventory_text: str, max_withdrawal: str, state_file: str | None
    ) -> CashDispenser | None:
        """Build the ATM from the options, or print what is wrong and return None."""
        try:
            inventory = Validator.parse_inventory(inventory_text)
            store = InventoryStore(state_file) if state_file else None
            return CashDispenser(inventory, max_withdrawal=max_withdrawal, store=store)
        except InvalidInventoryError as error:
            self._print(f"Invalid inventory: {error}", error=True)
        except ATMError as error:
            self._print(f"Invalid --max-withdrawal: {error}", error=True)
        except OSError as error:
            self._print(f"Cannot use the state file: {error}", error=True)
        return None

    @staticmethod
    def _parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="python3 cli.py",
            description="Simulate an ATM cash dispenser that pays out with the fewest notes.",
        )
        parser.add_argument("--inventory", default=DEFAULT_INVENTORY,
                            help='notes in the ATM as "denomination:count, ..." '
                                 f'(default: "{DEFAULT_INVENTORY}")')
        parser.add_argument("--max-withdrawal", default=str(DEFAULT_MAX_WITHDRAWAL),
                            help=f"largest amount per withdrawal (default: {DEFAULT_MAX_WITHDRAWAL})")
        parser.add_argument("--state-file", metavar="PATH",
                            help="keep the inventory in this JSON file: it survives restarts and can be "
                                 "shared by several processes (an existing file wins over --inventory)")
        parser.add_argument("amounts", nargs="*", help="amounts to withdraw, in order")
        return parser

    def _print(self, text: str, error: bool = False) -> None:
        print(text, file=self.err if error else self.out)


if __name__ == "__main__":
    sys.exit(CommandLine().run())
