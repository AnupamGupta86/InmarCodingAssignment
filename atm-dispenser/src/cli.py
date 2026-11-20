"""Command-line front end for the ATM.

    python -m cli 2700                                          # default inventory from the brief
    python -m cli --inventory "{ 500: 8, 200: 10, 100: 20 }" 2700 600
    python -m cli --inventory "4:1, 3:2" 6
    python -m cli                                               # interactive: type amounts, q to quit

Several amounts are withdrawn one after another from the same ATM, so you can watch the
inventory go down. Exit code: 0 = every withdrawal succeeded, 1 = at least one could not be
dispensed, 2 = invalid input.
"""

import argparse
import sys
from typing import Dict, Iterable, List, Optional, TextIO

from dispenser import CANNOT_DISPENSE, DEFAULT_INVENTORY, DEFAULT_MAX_WITHDRAWAL, CashDispenser
from errors import ATMError, InvalidInventoryError
from validation import parse_inventory, validate_amount

CURRENCY = "₹"

EXIT_OK = 0
EXIT_NOT_DISPENSED = 1
EXIT_INVALID_INPUT = 2


def format_notes(notes: Dict[int, int]) -> str:
    """{500: 5, 200: 1} -> "₹500 × 5, ₹200 × 1" (largest denomination first)."""
    if not notes:
        return "no notes"
    return ", ".join(f"{CURRENCY}{value} × {count}" for value, count in sorted(notes.items(), reverse=True))


def format_withdrawal(amount: int, notes: Optional[Dict[int, int]]) -> str:
    """The result line, e.g. "Withdrawal ₹2700: ₹500 × 5, ₹200 × 1 (Total notes dispensed: 6)"."""
    if notes is None:
        return f"Withdrawal {CURRENCY}{amount}: {CANNOT_DISPENSE}"
    return (
        f"Withdrawal {CURRENCY}{amount}: {format_notes(notes)} "
        f"(Total notes dispensed: {sum(notes.values())})"
    )


def withdraw(atm: CashDispenser, amount_text: str, out: TextIO, err: TextIO) -> int:
    """Run one withdrawal and print the result. Returns the exit code for it."""
    try:
        amount = validate_amount(amount_text)
        notes = atm.dispense(amount)
    except ATMError as error:
        print(f"Invalid amount {amount_text!r}: {error}", file=err)
        return EXIT_INVALID_INPUT

    print(format_withdrawal(amount, notes), file=out)
    label = "Inventory unchanged" if notes is None else "Updated inventory"
    print(f"{label}: {format_notes(atm.inventory)}", file=out)
    return EXIT_NOT_DISPENSED if notes is None else EXIT_OK


def run_interactive(atm: CashDispenser, lines: Iterable[str], out: TextIO, err: TextIO) -> int:
    """Read one amount per line until 'q', 'quit' or end of input."""
    worst = EXIT_OK
    print("Enter an amount to withdraw (q to quit).", file=out)
    try:
        for line in lines:
            text = line.strip()
            if text.lower() in ("q", "quit", "exit"):
                break
            if text:
                worst = max(worst, withdraw(atm, text, out, err))
    except KeyboardInterrupt:  # Ctrl+C ends the session quietly
        print(file=out)
    return worst


def main(argv: Optional[List[str]] = None, stdin: TextIO = sys.stdin,
         stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr) -> int:
    """Run the command line. Returns the exit code (the worst result of all withdrawals)."""
    parser = argparse.ArgumentParser(
        prog="python -m cli",
        description="Simulate an ATM cash dispenser that pays out with the fewest notes.",
    )
    parser.add_argument(
        "--inventory", default=DEFAULT_INVENTORY,
        help=f'notes in the ATM as "denomination:count, ..." (default: "{DEFAULT_INVENTORY}")',
    )
    parser.add_argument(
        "--max-withdrawal", default=str(DEFAULT_MAX_WITHDRAWAL),
        help=f"largest amount per withdrawal (default: {DEFAULT_MAX_WITHDRAWAL})",
    )
    parser.add_argument("amounts", nargs="*", help="amounts to withdraw, in order")
    args = parser.parse_args(argv)

    try:
        inventory = parse_inventory(args.inventory)
    except ATMError as error:
        print(f"Invalid inventory: {error}", file=stderr)
        return EXIT_INVALID_INPUT
    try:
        atm = CashDispenser(inventory, max_withdrawal=args.max_withdrawal)
    except InvalidInventoryError as error:
        print(f"Invalid inventory: {error}", file=stderr)
        return EXIT_INVALID_INPUT
    except ATMError as error:
        print(f"Invalid --max-withdrawal: {error}", file=stderr)
        return EXIT_INVALID_INPUT

    print(f"ATM inventory: {format_notes(atm.inventory)}", file=stdout)
    if not args.amounts:
        return run_interactive(atm, stdin, stdout, stderr)

    worst = EXIT_OK
    for amount_text in args.amounts:
        worst = max(worst, withdraw(atm, amount_text, stdout, stderr))
    return worst


if __name__ == "__main__":
    sys.exit(main())
