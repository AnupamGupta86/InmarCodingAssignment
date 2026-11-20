"""The allocation algorithm: which notes should the ATM pay out?

Problem
    Given the notes in the machine, e.g. {500: 8, 200: 10, 100: 20}, and an amount, e.g. 2700,
    choose how many notes of each denomination to dispense so that
      1. they add up to exactly the amount,
      2. no denomination uses more notes than the machine holds,
      3. the total number of notes is as small as possible.

Why not "always take the biggest note first" (greedy)?
    Greedy fails when notes are limited or denominations are unusual: with {4: 1, 3: 2} and 6,
    it takes the 4 and is left with 2, which it cannot make. The right answer is 3 + 3.

Approach: dynamic programming ("bounded coin change")
    A table answers, for every amount from 0 up to the target, "what is the fewest notes that
    make exactly this amount?". Every combination of the available notes is covered, so the
    answer is always the true minimum, and "no answer" really means the amount cannot be paid.

      1. Usable notes   - keep denominations that can help, in units of their common divisor.
      2. Fill the table - add the notes in bundles of 1, 2, 4, ... notes, so even thousands of
                          notes take only a few steps per denomination.
      3. Read it back   - follow the table backwards to find which notes were used.

Complexity (S = amount / GCD of the denominations, B = number of bundles)
    Time O(S * B), space O(S * B). README section 7 explains the algorithm in more detail.
"""

from functools import reduce
from math import gcd
from typing import Dict, List, Mapping, Optional, Tuple

_IMPOSSIBLE = float("inf")

Bundle = Tuple[int, int]   # (denomination, number of notes in the bundle)


def find_minimum_notes(amount: int, inventory: Mapping[int, int]) -> Optional[Dict[int, int]]:
    """Return {denomination: notes} that pays `amount` with the fewest notes, or None.

    `amount` must be a positive int and `inventory` a validated {denomination: count} mapping
    (see validation.py). This function only calculates: it never changes the inventory.

    When several combinations use the same, minimal number of notes, the one using more
    high-value notes is preferred, so the result is always the same.
    """
    usable = _usable_notes(amount, inventory)
    if not usable:
        return None

    unit = reduce(gcd, usable)           # 100 for 500/200/100: work in "units" of ₹100
    if amount % unit != 0:
        return None                      # e.g. 250 can never be made from 500/200/100 notes
    target = amount // unit              # 2700 -> 27 units

    bundles = [(value, size) for value in sorted(usable) for size in split_into_bundles(usable[value])]
    used_bundle = _fill_table(target, unit, bundles)
    if used_bundle is None:
        return None
    return _read_back(target, unit, bundles, used_bundle)


def split_into_bundles(count: int) -> List[int]:
    """Split a note count into bundle sizes 1, 2, 4, ... plus the remainder.

    split_into_bundles(8) == [1, 2, 4, 1]; choosing some of these bundles gives any count 0..8.
    """
    bundles = []
    size = 1
    while count > 0:
        take = min(size, count)
        bundles.append(take)
        count -= take
        size *= 2
    return bundles


def _usable_notes(amount: int, inventory: Mapping[int, int]) -> Dict[int, int]:
    """Step 1: the notes that can help pay `amount`, or {} if there is not enough cash.

    A denomination helps only if it has notes and is not bigger than the amount, and it can
    never use more than amount // value notes (so a huge stock does not slow anything down).
    """
    usable = {
        value: min(count, amount // value)
        for value, count in inventory.items()
        if count > 0 and value <= amount
    }
    if sum(value * count for value, count in usable.items()) < amount:
        return {}
    return usable


def _fill_table(target: int, unit: int, bundles: List[Bundle]) -> Optional[List[bytearray]]:
    """Step 2: find the fewest notes for every amount up to `target` (in units).

    Returns, for each bundle, the amounts at which that bundle is part of the best answer,
    or None if the target cannot be reached at all.
    """
    fewest_notes: List[float] = [0] + [_IMPOSSIBLE] * target
    used_bundle: List[bytearray] = []

    # Bundles are sorted smallest denomination first, so the largest notes are decided last and
    # win ties (see the "<=" below).
    for value, notes in bundles:
        bundle_units = (value // unit) * notes
        used = bytearray(target + 1)
        for total in range(target, bundle_units - 1, -1):    # high to low: each bundle used once
            with_bundle = fewest_notes[total - bundle_units] + notes
            if with_bundle <= fewest_notes[total]:
                fewest_notes[total] = with_bundle
                used[total] = 1
        used_bundle.append(used)

    return None if fewest_notes[target] == _IMPOSSIBLE else used_bundle


def _read_back(target: int, unit: int, bundles: List[Bundle], used_bundle: List[bytearray]) -> Dict[int, int]:
    """Step 3: walk the bundles backwards and collect the ones that make up the target."""
    dispensed: Dict[int, int] = {}
    remaining = target
    for index in range(len(bundles) - 1, -1, -1):
        if used_bundle[index][remaining]:
            value, notes = bundles[index]
            dispensed[value] = dispensed.get(value, 0) + notes
            remaining -= (value // unit) * notes
    return dict(sorted(dispensed.items(), reverse=True))
