"""Input parsing and validation for withdrawal amounts and ATM inventories.

Every function here either returns a clean, trusted value or raises a clear error that
says exactly what is wrong. The rest of the code only ever works with validated data.
"""

import re
from typing import Dict, Mapping

from errors import InvalidAmountError, InvalidInventoryError

# ASCII digits only, with an optional sign: "2700", "+2700", "-50".
# Rejects "2_700", "27e2", "12.5" and non-ASCII digits such as "१००" or "１００".
WHOLE_NUMBER_TEXT = re.compile(r"^[+-]?[0-9]+$")

# Longer text is refused before converting it, so nobody can make us parse a 100,000-digit number.
MAX_AMOUNT_DIGITS = 18


def _is_whole_number(value) -> bool:
    # bool is a subclass of int in Python (True == 1), so it must be excluded explicitly.
    return isinstance(value, int) and not isinstance(value, bool)


def validate_amount(amount) -> int:
    """Return the withdrawal amount as an int, or raise InvalidAmountError.

    Accepts an int, or text holding a whole number (for example from user input: " 2700 ").
    """
    if isinstance(amount, str):
        text = amount.strip()
        if not WHOLE_NUMBER_TEXT.match(text):
            if _looks_like_a_number(text):
                raise InvalidAmountError(f"Amount must be a whole number, got {amount!r}")
            raise InvalidAmountError(f"Amount must be a number, got {amount!r}")
        if len(text.lstrip("+-")) > MAX_AMOUNT_DIGITS:
            raise InvalidAmountError(f"Amount is too large: at most {MAX_AMOUNT_DIGITS} digits")
        amount = int(text)
    elif not _is_whole_number(amount):
        if isinstance(amount, float):
            raise InvalidAmountError(f"Amount must be a whole number, got {amount!r}")
        raise InvalidAmountError(f"Amount must be a number, got {amount!r}")

    if amount < 0:
        raise InvalidAmountError(f"Amount cannot be negative, got {amount}")
    if amount == 0:
        raise InvalidAmountError("Amount must be greater than zero")
    return amount


def _looks_like_a_number(text: str) -> bool:
    """True for decimal-looking text such as "12.5" or "1e3" (to give a more precise message)."""
    if "_" in text or not text.isascii():  # Python would accept "2_700" and "१२.५"
        return False
    try:
        float(text)
        return True
    except ValueError:
        return False


def validate_inventory(inventory: Mapping) -> Dict[int, int]:
    """Return a validated copy of the inventory {denomination: note count}, or raise.

    Rules:
      - the inventory must be a non-empty mapping
      - every denomination must be a positive whole number (a note worth 0 or -100 is invalid)
      - every note count must be a whole number of zero or more (zero = that slot is empty)

    The copy is sorted from the largest denomination to the smallest, and is independent
    of the caller's dictionary, so later changes to that dictionary cannot affect the ATM.
    """
    if not isinstance(inventory, Mapping):
        raise InvalidInventoryError(
            f"Inventory must be a mapping of denomination to note count, got {type(inventory).__name__}"
        )
    if not inventory:
        raise InvalidInventoryError("ATM inventory is empty: add at least one denomination")

    validated: Dict[int, int] = {}
    for denomination, count in inventory.items():
        if not _is_whole_number(denomination) or denomination <= 0:
            raise InvalidInventoryError(
                f"Invalid denomination {denomination!r}: must be a positive whole number"
            )
        if not _is_whole_number(count):
            raise InvalidInventoryError(
                f"Invalid note count {count!r} for denomination {denomination}: must be a whole number"
            )
        if count < 0:
            raise InvalidInventoryError(
                f"Invalid note count {count} for denomination {denomination}: cannot be negative"
            )
        validated[denomination] = count

    return dict(sorted(validated.items(), reverse=True))


def parse_inventory(text: str) -> Dict[int, int]:
    """Turn "500:8, 200:10" or "{ 500: 8, 200: 10 }" into {500: 8, 200: 10}.

    Only the text format is checked here (pairs of whole numbers, no duplicates). Whether the
    values make sense (positive denominations, no negative counts) is checked by validate_inventory.
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
        denomination = _parse_whole_number(parts[0], "denomination")
        count = _parse_whole_number(parts[1], f"note count for denomination {denomination}")
        if denomination in inventory:
            raise InvalidInventoryError(f"Denomination {denomination} is listed more than once")
        inventory[denomination] = count
    return inventory


def _parse_whole_number(text: str, what: str) -> int:
    text = text.strip()
    if not WHOLE_NUMBER_TEXT.match(text):
        raise InvalidInventoryError(f"Invalid {what} {text[:20]!r}: must be a whole number")
    if len(text.lstrip("+-")) > MAX_AMOUNT_DIGITS:
        raise InvalidInventoryError(f"Invalid {what}: too large (at most {MAX_AMOUNT_DIGITS} digits)")
    return int(text)
