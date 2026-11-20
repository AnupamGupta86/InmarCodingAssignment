"""The ATM itself: it owns the inventory and dispenses withdrawals safely.

Responsibilities are split on purpose:
    validation.py  - is the input sensible?
    allocator.py   - which notes pay this amount with the fewest notes? (pure calculation)
    dispenser.py   - keep the inventory correct: update it only after a complete, successful
                     allocation, and never let two withdrawals take the same notes.

Concurrency
    Several customers may withdraw at the same time (several threads in one process).
    Without protection, two withdrawals could both read "5 notes of 500 left", both decide to
    use them, and together dispense notes that do not exist. A lock makes "read inventory ->
    allocate -> update inventory" one indivisible step, so withdrawals happen one after another.
"""

import threading
from typing import Dict, Mapping, Optional

from atm.allocator import find_minimum_notes
from atm.errors import InvalidAmountError
from atm.validation import validate_amount, validate_inventory

CANNOT_DISPENSE = "Cannot dispense requested amount"


class CashDispenser:
    """An ATM holding a limited number of notes.

    Example:
        >>> atm = CashDispenser({500: 8, 200: 10, 100: 20})
        >>> atm.dispense(2700)
        {500: 5, 200: 1}
        >>> atm.inventory
        {500: 3, 200: 9, 100: 20}
        >>> atm.dispense(250) is None      # cannot be paid exactly; inventory unchanged
        True
    """

    def __init__(self, inventory: Mapping[int, int], max_withdrawal: Optional[int] = None):
        """Create the ATM from {denomination: note count}. Raises InvalidInventoryError.

        `max_withdrawal` is an optional limit per withdrawal, like a real ATM's daily limit.
        The service always sets one, which also caps how much work a single request can cause.
        """
        self._notes: Dict[int, int] = validate_inventory(inventory)
        if max_withdrawal is not None:
            max_withdrawal = validate_amount(max_withdrawal)
        self.max_withdrawal = max_withdrawal
        self._lock = threading.Lock()

    def dispense(self, amount) -> Optional[Dict[int, int]]:
        """Withdraw `amount` and return {denomination: notes dispensed}.

        Returns None if the notes in the machine cannot make the amount exactly
        (the caller shows CANNOT_DISPENSE); the inventory is then left untouched.
        Raises InvalidAmountError for an amount that is zero, negative or not a whole number.
        """
        amount = validate_amount(amount)  # invalid input fails before the inventory is touched
        if self.max_withdrawal is not None and amount > self.max_withdrawal:
            raise InvalidAmountError(
                f"Amount {amount} exceeds the limit of {self.max_withdrawal} per withdrawal"
            )

        with self._lock:
            notes_to_dispense = find_minimum_notes(amount, self._notes)
            if notes_to_dispense is None:
                return None

            # The full amount has been allocated: only now is the inventory updated. The new
            # inventory is built completely and then swapped in, so it is never half-updated.
            updated = dict(self._notes)
            for denomination, count in notes_to_dispense.items():
                updated[denomination] -= count
            self._notes = updated

            return dict(notes_to_dispense)

    @property
    def inventory(self) -> Dict[int, int]:
        """A copy of the notes currently in the machine, largest denomination first."""
        with self._lock:
            return dict(self._notes)

    @property
    def total_cash(self) -> int:
        """The total value of all notes currently in the machine."""
        with self._lock:
            return sum(denomination * count for denomination, count in self._notes.items())
