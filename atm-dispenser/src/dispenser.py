"""The ATM core: input validation, the minimum-notes algorithm and the cash dispenser.

    Validator       is the input sensible? Returns clean values or raises a clear error.
    NoteAllocator   which notes pay an amount with the fewest notes? (pure calculation)
    InventoryStore  optional JSON state file: the inventory survives restarts and can be
                    shared by several processes, which then behave as one ATM.
    CashDispenser   the ATM: owns the notes, updates them only after a complete allocation,
                    and never lets two withdrawals take the same notes.

The command line (cli.py) and the HTTP service (service.py) are thin front ends over CashDispenser.
"""

import contextlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Iterator, Mapping
from functools import reduce
from math import gcd
from pathlib import Path
from typing import Any

try:
    import fcntl  # file locks shared between processes (macOS and Linux)
except ImportError:  # pragma: no cover - Windows runs the Docker image instead
    fcntl = None  # type: ignore[assignment]

CANNOT_DISPENSE = "Cannot dispense requested amount"
DEFAULT_INVENTORY = "500:8, 200:10, 100:20"   # the example from the brief
DEFAULT_MAX_WITHDRAWAL = 200_000

Notes = dict[int, int]   # {denomination: number of notes}



# Errors ------------------------------------------------------------------------------------

class ATMError(ValueError):
    """Base class for invalid input. A withdrawal that cannot be paid is not an error: it returns None."""


class InvalidAmountError(ATMError):
    """The withdrawal amount is not a positive whole number (or is above the limit)."""


class InvalidInventoryError(ATMError):
    """The ATM inventory is empty or contains an invalid denomination or note count."""


class DuplicateKeyError(ValueError):
    """A JSON object names the same key twice."""


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """json.loads hook: {"amount": 100, "amount": 2700} is ambiguous, so refuse it.

    By default the last value would silently win, while a proxy or a log may read the first.
    """
    keys = [key for key, _ in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise DuplicateKeyError(f"Duplicate field(s): {', '.join(duplicates)}")
    return dict(pairs)


# Validation --------------------------------------------------------------------------------

class Validator:
    """Turns raw input into clean, trusted values, or raises an error that says what is wrong."""

    # ASCII digits only, with an optional sign: "2700", "+2700", "-50".
    # Rejects "2_700", "27e2", "12.5" and non-ASCII digits such as "१००" or "１００".
    WHOLE_NUMBER_TEXT = re.compile(r"^[+-]?[0-9]+$")

    # Longer text is refused before converting it, so nobody can make us parse a 100,000-digit number.
    MAX_DIGITS = 18

    # Real ATMs have a handful of cassettes; this keeps a mistyped inventory from slowing every withdrawal.
    MAX_DENOMINATIONS = 50

    @classmethod
    def amount(cls, amount) -> int:
        """Return the withdrawal amount as an int. Accepts an int or whole-number text (" 2700 ")."""
        if isinstance(amount, str):
            amount = cls._amount_from_text(amount)
        elif not cls._is_whole_number(amount):
            expected = "a whole number" if isinstance(amount, float) else "a number"
            raise InvalidAmountError(f"Amount must be {expected}, got {cls.preview(amount)}")

        if abs(amount) >= 10 ** cls.MAX_DIGITS:
            raise InvalidAmountError(f"Amount is too large: at most {cls.MAX_DIGITS} digits")
        if amount < 0:
            raise InvalidAmountError(f"Amount cannot be negative, got {amount}")
        if amount == 0:
            raise InvalidAmountError("Amount must be greater than zero")
        return amount

    @classmethod
    def inventory(cls, inventory: Mapping) -> Notes:
        """Return a validated copy of {denomination: note count}, largest denomination first.

        Every denomination must be a positive whole number and every count a whole number of
        zero or more (zero = an empty cassette). The copy is independent of the caller's dict.
        """
        if not isinstance(inventory, Mapping):
            raise InvalidInventoryError(
                f"Inventory must be a mapping of denomination to note count, got {type(inventory).__name__}"
            )
        if not inventory:
            raise InvalidInventoryError("ATM inventory is empty: add at least one denomination")
        if len(inventory) > cls.MAX_DENOMINATIONS:
            raise InvalidInventoryError(
                f"Too many denominations: {len(inventory)} (at most {cls.MAX_DENOMINATIONS})"
            )

        validated: Notes = {}
        for denomination, count in inventory.items():
            if not cls._is_whole_number(denomination) or denomination <= 0:
                raise InvalidInventoryError(
                    f"Invalid denomination {denomination!r}: must be a positive whole number"
                )
            if not cls._is_whole_number(count):
                raise InvalidInventoryError(
                    f"Invalid note count {count!r} for denomination {denomination}: must be a whole number"
                )
            if count < 0:
                raise InvalidInventoryError(
                    f"Invalid note count {count} for denomination {denomination}: cannot be negative"
                )
            validated[denomination] = count
        return dict(sorted(validated.items(), reverse=True))

    @classmethod
    def parse_inventory(cls, text: str) -> Notes:
        """Turn "500:8, 200:10" or "{ 500: 8, 200: 10 }" into a validated {500: 8, 200: 10}."""
        body = text.strip()
        if body.startswith("{") and body.endswith("}"):
            body = body[1:-1]

        inventory: Notes = {}
        for entry in body.split(","):
            if not entry.strip():
                continue  # tolerate a trailing comma
            parts = entry.split(":")
            if len(parts) != 2:
                raise InvalidInventoryError(
                    f"Invalid inventory entry {entry.strip()!r}: use denomination:count, e.g. 500:8"
                )
            denomination = cls.whole_number(parts[0], "denomination")
            count = cls.whole_number(parts[1], f"note count for denomination {denomination}")
            if denomination in inventory:
                raise InvalidInventoryError(f"Denomination {denomination} is listed more than once")
            inventory[denomination] = count
        return cls.inventory(inventory)

    @staticmethod
    def preview(value, limit: int = 30) -> str:
        """The value as it appears in an error message, cut short so long input is never echoed."""
        text = repr(value)
        return text if len(text) <= limit else text[:limit] + "…"

    @classmethod
    def _amount_from_text(cls, original: str) -> int:
        text = original.strip()
        if not cls.WHOLE_NUMBER_TEXT.match(text):
            expected = "a whole number" if cls._looks_like_a_number(text) else "a number"
            raise InvalidAmountError(f"Amount must be {expected}, got {cls.preview(original)}")
        if len(text.lstrip("+-")) > cls.MAX_DIGITS:  # checked before int(), which would parse any length
            raise InvalidAmountError(f"Amount is too large: at most {cls.MAX_DIGITS} digits")
        return int(text)

    @staticmethod
    def _is_whole_number(value) -> bool:
        # bool is a subclass of int in Python (True == 1), so it must be excluded explicitly.
        return isinstance(value, int) and not isinstance(value, bool)

    @staticmethod
    def _looks_like_a_number(text: str) -> bool:
        """True for decimal-looking text such as "12.5" or "1e3" (to give a more precise message)."""
        if "_" in text or not text.isascii():  # Python would accept "2_700" and "१२.५"
            return False
        try:
            float(text)
            return True
        except ValueError:
            return False

    @classmethod
    def whole_number(cls, text: str, what: str) -> int:
        """Parse a denomination or note count written as text. Raises InvalidInventoryError."""
        text = text.strip()
        if not cls.WHOLE_NUMBER_TEXT.match(text):
            raise InvalidInventoryError(f"Invalid {what} {text[:20]!r}: must be a whole number")
        if len(text.lstrip("+-")) > cls.MAX_DIGITS:
            raise InvalidInventoryError(f"Invalid {what}: too large (at most {cls.MAX_DIGITS} digits)")
        return int(text)


# The minimum-notes algorithm ---------------------------------------------------------------

class NoteAllocator:
    """Finds the fewest notes that pay an amount exactly, using only the notes available.

    Why not "biggest note first" (greedy)? With {4: 1, 3: 2} and 6, greedy takes the 4 and is
    left with 2, which it cannot make; the right answer is 3 + 3.

    Approach: dynamic programming ("bounded coin change"). A table answers, for every amount up
    to the target, "what is the fewest notes that make exactly this amount?". Every combination
    is covered, so the answer is always the true minimum.

      1. Usable notes   - keep denominations that can help, in units of their common divisor.
      2. Fill the table - add the notes in bundles of 1, 2, 4, ... notes, so even thousands of
                          notes take only a few steps per denomination.
      3. Read it back   - follow the table backwards to find which notes were used.

    Time and space O(S * B): S = amount / GCD (greatest common divisor), B = number of bundles.
    """

    _IMPOSSIBLE = float("inf")

    # The table has (amount in units + 1) x (number of bundles) cells, one byte each. 64 million
    # cells is about 3 seconds and 64 MB; every Indian note and coin with up to ₹199,999 to pay
    # needs about 19 million. Anything bigger is refused rather than exhausting memory or CPU.
    MAX_TABLE_CELLS = 64_000_000

    def find_minimum_notes(self, amount: int, inventory: Mapping[int, int]) -> Notes | None:
        """Return {denomination: notes} paying `amount` with the fewest notes, or None.

        Never changes the inventory. On a tie, the combination with more high-value notes wins,
        so the result is always the same.
        """
        usable = self._usable_notes(amount, inventory)
        if not usable:
            return None

        unit = reduce(gcd, usable)           # 100 for 500/200/100: work in "units" of ₹100
        if amount % unit != 0:
            return None                      # e.g. 250 can never be made from 500/200/100 notes
        target = amount // unit              # 2700 -> 27 units

        bundles = [
            (value, size)                        # (denomination, notes in this bundle)
            for value in sorted(usable)          # smallest first, so bigger notes win ties
            for size in self.split_into_bundles(usable[value])
        ]
        if (target + 1) * len(bundles) > self.MAX_TABLE_CELLS:
            raise InvalidAmountError(
                f"Amount {amount} is too large to work out with this inventory; "
                "lower the amount or the withdrawal limit"
            )
        used_bundle = self._fill_table(target, unit, bundles)
        if used_bundle is None:
            return None
        return self._read_back(target, unit, bundles, used_bundle)

    @staticmethod
    def split_into_bundles(count: int) -> list[int]:
        """8 -> [1, 2, 4, 1]: choosing some of these bundles gives any count from 0 to 8."""
        bundles = []
        size = 1
        while count > 0:
            take = min(size, count)
            bundles.append(take)
            count -= take
            size *= 2
        return bundles

    @staticmethod
    def _usable_notes(amount: int, inventory: Mapping[int, int]) -> Notes:
        """Step 1: the notes that can help, or {} if there is not enough cash.

        A denomination never needs more than amount // value notes, so a huge stock is no slower.
        """
        usable = {
            value: min(count, amount // value)
            for value, count in inventory.items()
            if count > 0 and value <= amount
        }
        if sum(value * count for value, count in usable.items()) < amount:
            return {}
        return usable

    def _fill_table(
        self, target: int, unit: int, bundles: list[tuple[int, int]]
    ) -> list[bytearray] | None:
        """Step 2: fewest notes for every amount up to `target` (in units).

        Returns, for each bundle, the amounts at which it is part of the best answer,
        or None if the target cannot be reached.
        """
        fewest_notes: list[float] = [0] + [self._IMPOSSIBLE] * target
        used_bundle: list[bytearray] = []

        # Bundles are sorted smallest denomination first, so the largest notes are decided last
        # and win ties (see the "<=" below).
        for value, notes in bundles:
            bundle_units = (value // unit) * notes
            used = bytearray(target + 1)
            for total in range(target, bundle_units - 1, -1):   # high to low: each bundle used once
                with_bundle = fewest_notes[total - bundle_units] + notes
                if with_bundle <= fewest_notes[total]:
                    fewest_notes[total] = with_bundle
                    used[total] = 1
            used_bundle.append(used)

        return None if fewest_notes[target] == self._IMPOSSIBLE else used_bundle

    @staticmethod
    def _read_back(
        target: int, unit: int, bundles: list[tuple[int, int]], used_bundle: list[bytearray]
    ) -> Notes:
        """Step 3: walk the bundles backwards and collect the ones that make up the target."""
        dispensed: Notes = {}
        remaining = target
        for index in reversed(range(len(bundles))):
            if used_bundle[index][remaining]:
                value, notes = bundles[index]
                dispensed[value] = dispensed.get(value, 0) + notes
                remaining -= (value // unit) * notes
        return dict(sorted(dispensed.items(), reverse=True))


# Persistence --------------------------------------------------------------------------------

class InventoryStore:
    """Keeps the inventory in a JSON file, so it survives restarts and can be shared.

    The file looks like {"inventory": {"500": 8, "200": 10, "100": 20}}.

    Several programs or containers may use the same file at the same time. Each withdrawal
    holds an exclusive lock on "<file>.lock" while it reads the notes, allocates and saves the
    result, so two processes can never pay out the same notes. The file is replaced atomically
    (write a temporary file, then rename it), so a crash never leaves it half-written.
    """

    def __init__(self, path: str | os.PathLike[str]):
        if fcntl is None:  # pragma: no cover - Windows
            raise InvalidInventoryError("A state file needs macOS or Linux; on Windows use the Docker image")
        # Follow a symbolic link once, so saving updates the real file instead of replacing the link.
        self.path = Path(os.path.realpath(path))
        self._lock_path = self.path.with_name(self.path.name + ".lock")

    @contextlib.contextmanager
    def locked(self) -> Iterator[None]:
        """Hold the lock that every process using this file must take before changing it."""
        with open(self._lock_path, "a") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)  # waits while another process holds it
            try:
                yield
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def load_or_create(self, starting_inventory: Notes) -> Notes:
        """The saved inventory if the file exists (it wins), otherwise create the file from the start."""
        with self.locked():
            if self.path.exists():
                return self.load()
            self.save(starting_inventory)
            return starting_inventory

    def load(self) -> Notes:
        """Read and validate the saved inventory. Raises InvalidInventoryError if the file is bad."""
        try:
            text = self.path.read_text(encoding="utf-8-sig")  # -sig: also accepts a Windows Notepad file
            data = json.loads(text, object_pairs_hook=reject_duplicate_keys)
        except (OSError, ValueError) as error:
            raise InvalidInventoryError(f"Cannot read the state file {self.path}: {error}") from None
        if not isinstance(data, dict) or not isinstance(data.get("inventory"), dict):
            raise InvalidInventoryError(
                f'State file {self.path} must look like {{"inventory": {{"500": 8}}}}'
            )

        notes: dict[int, object] = {}
        for key, count in data["inventory"].items():
            try:
                denomination = Validator.whole_number(key, "denomination")
            except InvalidInventoryError as error:
                raise InvalidInventoryError(f"{error} (in the state file {self.path})") from None
            if denomination in notes:  # "500" and "0500" are the same denomination
                raise InvalidInventoryError(
                    f"Denomination {denomination} is listed more than once in the state file {self.path}"
                )
            notes[denomination] = count
        return Validator.inventory(notes)

    def save(self, notes: Notes) -> None:
        """Write the inventory atomically: readers see either the old file or the new one."""
        text = json.dumps({"inventory": {str(value): count for value, count in notes.items()}}, indent=2)
        handle, temporary = tempfile.mkstemp(
            dir=self.path.parent, prefix=self.path.name + ".", suffix=".tmp"  # same folder: rename is atomic
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                file.write(text + "\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
            raise


# The ATM -----------------------------------------------------------------------------------

class CashDispenser:
    """An ATM holding a limited number of notes.

    Concurrency: several customers may withdraw at the same time. Without protection, two
    withdrawals could both read "5 notes of 500 left" and together dispense notes that do not
    exist. A lock makes "read inventory -> allocate -> update inventory" one indivisible step.

    With an InventoryStore, the file lock extends that guarantee to every process sharing the
    file: the notes are re-read from the file inside the lock before each withdrawal.
    """

    def __init__(self, inventory: Mapping[int, int], max_withdrawal: int | str | None = None,
                 allocator: NoteAllocator | None = None, store: InventoryStore | None = None):
        """Create the ATM from {denomination: note count}. Raises InvalidInventoryError.

        `max_withdrawal` is an optional limit per withdrawal, like a real ATM's; it also caps how
        much work one request can cause. `allocator` can be replaced in tests. With a `store`,
        an existing state file wins over `inventory`; otherwise the file is created from it.
        """
        self._notes: Notes = Validator.inventory(inventory)
        self.max_withdrawal = None if max_withdrawal is None else Validator.amount(max_withdrawal)
        self._allocator = allocator or NoteAllocator()
        self._lock = threading.Lock()
        self._store = store
        if store is not None:
            self._notes = store.load_or_create(self._notes)

    def dispense(self, amount) -> Notes | None:
        """Withdraw `amount` and return {denomination: notes dispensed}.

        Returns None if the notes cannot make the amount exactly (show CANNOT_DISPENSE); the
        inventory is then untouched. Raises InvalidAmountError for invalid amounts.
        """
        return self.dispense_with_inventory(amount)[0]

    def dispense_with_inventory(self, amount) -> tuple[Notes | None, Notes]:
        """Like dispense(), but also return the inventory right after this withdrawal.

        Both come from the same locked step, so the inventory reported to a customer is never
        mixed up with another customer's withdrawal happening at the same time.
        """
        amount = Validator.amount(amount)  # invalid input fails before the inventory is touched
        if self.max_withdrawal is not None and amount > self.max_withdrawal:
            raise InvalidAmountError(
                f"Amount {amount} exceeds the limit of {self.max_withdrawal} per withdrawal"
            )

        with self._lock, self._state_file_lock():
            self._reload_from_state_file()  # another process sharing the file may have withdrawn
            notes_to_dispense = self._allocator.find_minimum_notes(amount, self._notes)
            if notes_to_dispense is None:
                return None, dict(self._notes)

            # The full amount has been allocated: only now is the inventory updated. The new
            # inventory is built completely, saved (if there is a state file), then swapped in,
            # so it is never half-updated and a failed save changes nothing.
            updated = dict(self._notes)
            for denomination, count in notes_to_dispense.items():
                updated[denomination] -= count
            if self._store is not None:
                self._store.save(updated)
            self._notes = updated
            return dict(notes_to_dispense), dict(updated)

    @property
    def inventory(self) -> Notes:
        """A copy of the notes currently in the machine, largest denomination first."""
        with self._lock, self._state_file_lock():
            self._reload_from_state_file()
            return dict(self._notes)

    @property
    def total_cash(self) -> int:
        """The total value of all notes currently in the machine."""
        return sum(denomination * count for denomination, count in self.inventory.items())

    def _state_file_lock(self) -> contextlib.AbstractContextManager[None]:
        """The lock shared with other processes using the same state file (nothing without one)."""
        return self._store.locked() if self._store is not None else contextlib.nullcontext()

    def _reload_from_state_file(self) -> None:
        if self._store is not None:
            self._notes = self._store.load()
