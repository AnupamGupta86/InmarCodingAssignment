"""Command-line front end for the ATM.

    python -m atm 2700                                          # default inventory from the brief
    python -m atm --inventory "{ 500: 8, 200: 10, 100: 20 }" 2700 600
    python -m atm --inventory "4:1, 3:2" 6
    python -m atm                                               # interactive: type amounts, q to quit

Several amounts are withdrawn one after another from the same ATM, so you can watch the
inventory go down. Exit code: 0 = every withdrawal succeeded, 1 = at least one could not be
dispensed, 2 = invalid input.
"""

import argparse
import sys
from typing import Dict, Iterable, List, Optional, TextIO

from atm.dispenser import CANNOT_DISPENSE, CashDispenser
from atm.errors import ATMError, InvalidInventoryError

DEFAULT_INVENTORY = "500:8, 200:10, 100:20"
CURRENCY = "₹"

EXIT_OK = 0
EXIT_NOT_DISPENSED = 1
EXIT_INVALID_INPUT = 2


def parse_inventory(text: str) -> Dict[int, int]:
    """Turn "500:8, 200:10" or "{ 500: 8, 200: 10 }" into {500: 8, 200: 10}.

    Only the text format is checked here (pairs of whole numbers, no duplicates). Whether the
    values make sense (positive denominations, no negative counts) is checked by the ATM.
    """
    body = text.strip()
    if body.startswith("{") and body.endswith("}"):
        body = body[1:-1]
    if not body.strip():
        raise InvalidInventoryError("ATM inventory is empty: add at least one denomination")

    inventory: Dict[int, int] = {}
    for entry in body.split(","):
        if not entry.strip():
            continue  # tolerate a trailing comma
        parts = entry.split(":")
        if len(parts) != 2:
            raise InvalidInventoryError(
                f"Invalid inventory entry {entry.strip()!r}: use denomination:count, e.g. 500:8"
            )
        denomination = _parse_int(parts[0], "denomination")
        count = _parse_int(parts[1], f"note count for denomination {denomination}")
        if denomination in inventory:
            raise InvalidInventoryError(f"Denomination {denomination} is listed more than once")
        inventory[denomination] = count
    return inventory


def _parse_int(text: str, what: str) -> int:
    try:
        return int(text.strip())
    except ValueError:
        raise InvalidInventoryError(f"Invalid {what} {text.strip()!r}: must be a whole number") from None


def format_notes(notes: Dict[int, int]) -> str:
    """{500: 5, 200: 1} -> "₹500 × 5, ₹200 × 1" (largest denomination first)."""
    if not notes:
        return "no notes"
    return ", ".join(f"{CURRENCY}{value} × {count}" for value, count in sorted(notes.items(), reverse=True))


def format_withdrawal(amount: int, notes: Optional[Dict[int, int]]) -> str:
    if notes is None:
        return f"Withdrawal {CURRENCY}{amount}: {CANNOT_DISPENSE}"
    return (
        f"Withdrawal {CURRENCY}{amount}: {format_notes(notes)} "
        f"(Total notes dispensed: {sum(notes.values())})"
    )


def withdraw(atm: CashDispenser, amount_text: str, out: TextIO, err: TextIO) -> int:
    """Run one withdrawal and print the result. Returns the exit code for it."""
    try:
        notes = atm.dispense(amount_text)
    except ATMError as error:
        print(f"Invalid amount {amount_text!r}: {error}", file=err)
        return EXIT_INVALID_INPUT

    print(format_withdrawal(int(amount_text), notes), file=out)
    label = "Inventory unchanged" if notes is None else "Updated inventory"
    print(f"{label}: {format_notes(atm.inventory)}", file=out)
    return EXIT_NOT_DISPENSED if notes is None else EXIT_OK


def run_interactive(atm: CashDispenser, lines: Iterable[str], out: TextIO, err: TextIO) -> int:
    """Read one amount per line until 'q', 'quit' or end of input."""
    worst = EXIT_OK
    print("Enter an amount to withdraw (q to quit).", file=out)
    for line in lines:
        text = line.strip()
        if text.lower() in ("q", "quit", "exit"):
            break
        if text:
            worst = max(worst, withdraw(atm, text, out, err))
    return worst


def main(argv: Optional[List[str]] = None, stdin: TextIO = sys.stdin,
         stdout: TextIO = sys.stdout, stderr: TextIO = sys.stderr) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m atm",
        description="Simulate an ATM cash dispenser that pays out with the fewest notes.",
    )
    parser.add_argument(
        "--inventory", default=DEFAULT_INVENTORY,
        help=f'notes in the ATM as "denomination:count, ..." (default: "{DEFAULT_INVENTORY}")',
    )
    parser.add_argument("amounts", nargs="*", help="amounts to withdraw, in order")
    args = parser.parse_args(argv)

    try:
        atm = CashDispenser(parse_inventory(args.inventory))
    except ATMError as error:
        print(f"Invalid inventory: {error}", file=stderr)
        return EXIT_INVALID_INPUT

    print(f"ATM inventory: {format_notes(atm.inventory)}", file=stdout)
    if not args.amounts:
        return run_interactive(atm, stdin, stdout, stderr)

    worst = EXIT_OK
    for amount_text in args.amounts:
        worst = max(worst, withdraw(atm, amount_text, stdout, stderr))
    return worst
