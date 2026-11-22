"""Command-line front end for the ATM: python3 atm-dispenser/src/cli.py --help shows the options,
examples and exit codes. Several amounts are withdrawn one after another from the same ATM, so
you can watch the inventory go down.
"""

import argparse
import contextlib
import os
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
LABEL_WIDTH = 22           # labels such as "Updated inventory:" are padded to this width

EXIT_OK = 0
EXIT_NOT_DISPENSED = 1     # "Cannot dispense requested amount"
EXIT_INVALID_INPUT = 2     # invalid input, or a state file that cannot be read or saved
EXIT_CANCELLED = 130       # stopped with Ctrl+C (the usual code for that)

# Used only when the terminal cannot show these characters (e.g. an ASCII or Latin-1 console).
ASCII_FALLBACK = str.maketrans({"₹": "Rs.", "×": "x", "✓": "OK", "✗": "X", "…": "..."})

QUIT_COMMANDS = ("q", "quit", "exit")
INVENTORY_COMMANDS = ("i", "inventory")
HELP_COMMANDS = ("h", "help", "?")
INTERACTIVE_HELP = "Type an amount to withdraw (e.g. 2700), i to show the inventory, h for help, q to quit."

EXAMPLES = """examples:
  python3 cli.py 2700                                   the example from the brief
  python3 cli.py 2700 2700 800                          several withdrawals from the same ATM
  python3 cli.py --inventory "4:1, 3:2" 6               your own notes
  python3 cli.py --state-file atm.json 2700             keep the inventory between runs
  python3 cli.py                                        interactive mode

exit codes: 0 = dispensed, 1 = cannot dispense, 2 = invalid input, 130 = cancelled (Ctrl+C)"""


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
        state_file_existed = bool(args.state_file) and os.path.exists(args.state_file)
        inventory_text = DEFAULT_INVENTORY if args.inventory is None else args.inventory
        atm = self._create_atm(inventory_text, args.max_withdrawal, args.state_file)
        if atm is None:
            return EXIT_INVALID_INPUT

        if state_file_existed:
            ignored = " (--inventory is ignored: the saved notes are used)" if args.inventory is not None else ""
            self._print(f"Using the saved inventory from {args.state_file}{ignored}")
        elif args.state_file:
            self._print(f"Created the state file {args.state_file}")

        self.show_inventory(atm)
        if not args.amounts:
            return self.run_interactive(atm, self.stdin)
        return max(self.withdraw(atm, amount) for amount in args.amounts)

    def withdraw(self, atm: CashDispenser, amount_text: str) -> int:
        """Run one withdrawal and print the result. Returns the exit code for it."""
        try:
            amount = Validator.amount(amount_text)
            notes, inventory_after = atm.dispense_with_inventory(amount)
        except InvalidInventoryError as error:  # the state file was broken or removed meanwhile
            self._print_problem(amount_text, f"{error}; nothing was dispensed")
            return EXIT_INVALID_INPUT
        except ATMError as error:
            self._print_problem(amount_text, f"Invalid amount: {error}{self._amount_hint(amount_text)}")
            return EXIT_INVALID_INPUT
        except OSError as error:  # the state file could not be saved: nothing was dispensed
            self._print_problem(amount_text, f"Cannot save the state file ({error}); nothing was dispensed")
            return EXIT_INVALID_INPUT

        self._print("")
        self._print(self.format_withdrawal(amount, notes, inventory_after))
        return EXIT_NOT_DISPENSED if notes is None else EXIT_OK

    def run_interactive(self, atm: CashDispenser, lines: Iterable[str]) -> int:
        """Read one amount per line until 'q', 'quit', 'exit', end of input or Ctrl+C.

        'i' or 'inventory' shows the notes in the ATM; 'h', 'help' or '?' lists the commands.
        """
        worst = EXIT_OK
        self._print(INTERACTIVE_HELP)
        try:
            self._prompt()
            for line in lines:
                text = line.strip()
                command = text.lower()
                if command in QUIT_COMMANDS:
                    break
                if command in INVENTORY_COMMANDS:
                    self._print("")
                    self.show_inventory(atm)
                elif command in HELP_COMMANDS:
                    self._print(INTERACTIVE_HELP)
                elif text:
                    worst = max(worst, self.withdraw(atm, text))
                self._prompt()
        except KeyboardInterrupt:  # Ctrl+C ends the session quietly
            self._print("")
        self._print("Goodbye.")
        return worst

    def show_inventory(self, atm: CashDispenser) -> None:
        """Print the notes in the ATM and their total value, between two dashed rules."""
        try:
            inventory = atm.inventory
        except InvalidInventoryError as error:  # the state file was broken or removed meanwhile
            self._print(f"✗ {error}", error=True)
            return
        line = f"{'ATM inventory:':<{LABEL_WIDTH + 2}}{self.format_inventory(inventory)}"
        rule = "-" * len(line)
        self._print(f"{rule}\n{line}\n{rule}")

    # Formatting ---------------------------------------------------------------------------

    @staticmethod
    def money(amount: int) -> str:
        """2700 -> "₹2,700", 200000 -> "₹2,00,000" (Indian digit grouping)."""
        digits = str(amount)
        if len(digits) <= 3:
            return CURRENCY + digits
        rest, last_three = digits[:-3], digits[-3:]
        groups: list[str] = []
        while rest:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        return CURRENCY + ",".join([*groups, last_three])

    @classmethod
    def format_notes(cls, notes: Notes) -> str:
        """{500: 5, 200: 1} -> "₹500 × 5, ₹200 × 1" (largest denomination first)."""
        if not notes:
            return "no notes"
        largest_first = sorted(notes.items(), reverse=True)
        return ", ".join(f"{cls.money(value)} × {count}" for value, count in largest_first)

    @classmethod
    def format_inventory(cls, notes: Notes) -> str:
        """The notes and their total, e.g. "₹500 × 3, ₹200 × 9, ₹100 × 20  (total ₹5,300)"."""
        total = sum(value * count for value, count in notes.items())
        return f"{cls.format_notes(notes)}  (total {cls.money(total)})"

    @classmethod
    def format_withdrawal(cls, amount: int, notes: Notes | None, inventory_after: Notes) -> str:
        """The result of one withdrawal, for example:

            Withdrawal ₹2,700
            -----------------
              ✓ Dispensed:          ₹500 × 5, ₹200 × 1 (Total notes dispensed: 6)
              Updated inventory:    ₹500 × 3, ₹200 × 9, ₹100 × 20  (total ₹5,300)
        """
        lines = [cls._underlined(f"Withdrawal {cls.money(amount)}")]
        if notes is None:
            lines.append(f"  ✗ {CANNOT_DISPENSE}")
            lines.append(cls._row("Inventory unchanged", cls.format_inventory(inventory_after)))
        else:
            dispensed = f"{cls.format_notes(notes)} (Total notes dispensed: {sum(notes.values())})"
            lines.append(cls._row("✓ Dispensed", dispensed))
            lines.append(cls._row("Updated inventory", cls.format_inventory(inventory_after)))
        return "\n".join(lines)

    @staticmethod
    def _underlined(title: str) -> str:
        return f"{title}\n{'-' * len(title)}"

    @staticmethod
    def _row(label: str, value: str) -> str:
        return f"  {label + ':':<{LABEL_WIDTH}}{value}"

    def _create_atm(
        self, inventory_text: str, max_withdrawal: str, state_file: str | None
    ) -> CashDispenser | None:
        """Build the ATM from the options, or print what is wrong and return None."""
        try:
            inventory = Validator.parse_inventory(inventory_text)
            store = InventoryStore(state_file) if state_file else None
            return CashDispenser(inventory, max_withdrawal=max_withdrawal, store=store)
        except InvalidInventoryError as error:
            self._print(f"✗ Invalid inventory: {error}", error=True)
        except ATMError as error:
            self._print(f"✗ Invalid --max-withdrawal: {error}", error=True)
        except OSError as error:
            self._print(f"✗ Cannot use the state file: {error}", error=True)
        return None

    @staticmethod
    def _parser() -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="python3 cli.py",
            description="Simulate an ATM cash dispenser that pays out with the fewest notes.",
            epilog=EXAMPLES,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        parser.add_argument("--inventory",
                            help='notes in the ATM as "denomination:count, ..." '
                                 f'(default: "{DEFAULT_INVENTORY}")')
        parser.add_argument("--max-withdrawal", default=str(DEFAULT_MAX_WITHDRAWAL),
                            help=f"largest amount per withdrawal (default: {DEFAULT_MAX_WITHDRAWAL})")
        parser.add_argument("--state-file", metavar="PATH",
                            help="keep the inventory in this JSON file: it survives restarts and can be "
                                 "shared by several processes (an existing file wins over --inventory)")
        parser.add_argument("amounts", nargs="*", help="amounts to withdraw, in order")
        return parser

    @staticmethod
    def _amount_hint(amount_text: str) -> str:
        """A nudge for amounts written the way people write money: "2,700", "₹2700", "2700/-"."""
        has_digits = any(character.isdigit() for character in amount_text)
        if has_digits and any(mark in amount_text for mark in ",₹/"):
            return ". Type the amount as digits only, for example 2700"
        return ""

    def _prompt(self) -> None:
        """Show "> " before each line in a real terminal (not when the input is piped or a file)."""
        if getattr(self.stdin, "isatty", lambda: False)():
            print("> ", end="", file=self.out, flush=True)

    def _print_problem(self, amount_text: str, problem: str) -> None:
        """A withdrawal that could not even be attempted, e.g. "abc" (printed to stderr)."""
        self._print("", error=True)
        heading = self._underlined(f"Withdrawal {Validator.preview(amount_text)}")
        self._print(f"{heading}\n  ✗ {problem}", error=True)

    def _print(self, text: str, error: bool = False) -> None:
        stream = self.err if error else self.out
        encoding = getattr(stream, "encoding", None) or "utf-8"
        try:
            text.encode(encoding)
        except UnicodeEncodeError:  # e.g. "₹" on an ASCII console: print "Rs." rather than crash
            text = text.translate(ASCII_FALLBACK).encode(encoding, "replace").decode(encoding)
        print(text, file=stream)


def main() -> int:
    """Run the program; Ctrl+C or a closed output pipe ends it quietly instead of with a traceback."""
    try:
        return CommandLine().run()
    except KeyboardInterrupt:  # Ctrl+C outside interactive mode, e.g. during a long calculation
        print("\nCancelled.", file=sys.stderr)
        return EXIT_CANCELLED
    except BrokenPipeError:  # the output went to a program that stopped reading, e.g. "| head -1"
        with contextlib.suppress(OSError, ValueError):  # stop Python reporting it again at exit
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
