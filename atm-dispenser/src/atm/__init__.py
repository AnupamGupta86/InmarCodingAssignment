"""ATM cash dispenser.

Public API:
    CashDispenser        - the ATM: holds the notes and dispenses withdrawals safely
    find_minimum_notes   - the allocation algorithm on its own (no state, no side effects)
    CANNOT_DISPENSE      - the message shown when an amount cannot be paid out exactly
    ATMError, InvalidAmountError, InvalidInventoryError - validation errors
"""

from atm.allocator import find_minimum_notes
from atm.dispenser import CANNOT_DISPENSE, CashDispenser
from atm.errors import ATMError, InvalidAmountError, InvalidInventoryError

__all__ = [
    "ATMError",
    "CANNOT_DISPENSE",
    "CashDispenser",
    "InvalidAmountError",
    "InvalidInventoryError",
    "find_minimum_notes",
]
