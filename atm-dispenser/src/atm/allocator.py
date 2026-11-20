"""The allocation algorithm: which notes should the ATM pay out?

Problem
    Given the notes in the machine, e.g. {500: 8, 200: 10, 100: 20}, and an amount, e.g. 2700,
    find how many notes of each denomination to dispense so that
      1. they add up to exactly the amount,
      2. no denomination uses more notes than the machine holds,
      3. the total number of notes is as small as possible.

Why not "always take the biggest note first" (greedy)?
    Greedy fails when notes are limited or denominations are unusual. With {4: 1, 3: 2} and 6,
    greedy takes the 4 and is left with 2, which it cannot make. The right answer is 3 + 3.

Approach: dynamic programming over amounts ("bounded coin change")
    We fill a table that answers, for every amount from 0 up to the target:
        "what is the fewest notes that make exactly this amount?"
    Every possible combination of the available notes is covered, so the answer is always the
    true minimum, and "no answer" really means the amount cannot be paid exactly.

    Step 1 - bundle the notes. Eight ₹500 notes become bundles of 1, 2, 4 and 1 notes
    (powers of two, plus what is left). Any count from 0 to 8 is a sum of some of these
    bundles (5 = 4 + 1, 7 = 4 + 2 + 1), so "how many ₹500 notes?" becomes "which bundles?".
    This keeps the work small even when the machine holds thousands of notes.

    Step 2 - add the bundles one at a time. For each bundle (say 4 × ₹500 = ₹2000, 4 notes)
    and each amount T, check whether "this bundle + the best way to make T - ₹2000" uses
    fewer notes than the best way found so far. Amounts are visited from high to low, so
    each bundle is used at most once.

    Step 3 - read the answer back. For every bundle we remembered the amounts where it was
    used; walking the bundles backwards from the target tells us exactly which notes to pay.

Size reduction
    Every payable amount is a multiple of the greatest common divisor (GCD) of the
    denominations. For 500/200/100 that is 100, so 2700 becomes 27 "units" and the table has
    28 entries instead of 2,701. An amount that is not a multiple (e.g. 250) is rejected at once.

Complexity (S = amount / GCD, B = number of bundles = sum over denominations of log2(notes))
    Time:  O(S * B)
    Space: O(S * B) small flags to remember which bundles were used (plus O(S) for the table)
    Example: ₹199,990 from 7 denominations with up to 2,000 notes each -> S = 19,999 and B = 75,
    about 1.5 million simple steps.
"""

from functools import reduce
from math import gcd
from typing import Dict, List, Mapping, Optional, Tuple

_IMPOSSIBLE = float("inf")


def split_into_bundles(count: int) -> List[int]:
    """Split a note count into bundle sizes 1, 2, 4, ... plus the remainder.

    split_into_bundles(8) == [1, 2, 4, 1]; picking some of these bundles gives any count 0..8.
    """
    bundles = []
    size = 1
    while count > 0:
        take = min(size, count)
        bundles.append(take)
        count -= take
        size *= 2
    return bundles


def find_minimum_notes(amount: int, inventory: Mapping[int, int]) -> Optional[Dict[int, int]]:
    """Return {denomination: notes} that pays `amount` with the fewest notes, or None.

    `amount` must be a positive int and `inventory` a validated {denomination: count} mapping
    (see atm.validation). This function only calculates: it never changes the inventory.

    When several combinations use the same, minimal number of notes, the one using more
    high-value notes is preferred, so the result is deterministic.
    """
    # Only denominations that still have notes and are not bigger than the amount can help.
    usable = {value: count for value, count in inventory.items() if count > 0 and value <= amount}
    if not usable:
        return None
    if sum(value * count for value, count in usable.items()) < amount:
        return None

    # Work in units of the GCD to keep the table small (2700 with 500/200/100 -> 27 units).
    unit = reduce(gcd, usable)
    if amount % unit != 0:
        return None
    target = amount // unit

    # Step 1: bundles as (denomination, notes in the bundle). Smallest denomination first, so
    # the largest notes are decided last and win ties (see the "<=" below).
    bundles: List[Tuple[int, int]] = [
        (value, size) for value in sorted(usable) for size in split_into_bundles(usable[value])
    ]

    # Step 2: fewest_notes[t] = fewest notes that make exactly t units with the bundles so far.
    fewest_notes: List[float] = [0] + [_IMPOSSIBLE] * target
    used_bundle: List[bytearray] = []  # used_bundle[i][t] == 1: bundle i is used to make t units

    for value, notes in bundles:
        bundle_units = (value // unit) * notes
        used = bytearray(target + 1)
        for total in range(target, bundle_units - 1, -1):  # high to low: each bundle used once
            with_bundle = fewest_notes[total - bundle_units] + notes
            if with_bundle <= fewest_notes[total]:  # "<=": on a tie, prefer the later, larger notes
                fewest_notes[total] = with_bundle
                used[total] = 1
        used_bundle.append(used)

    if fewest_notes[target] == _IMPOSSIBLE:
        return None

    # Step 3: walk the bundles backwards, collecting the ones used to reach the target.
    dispensed: Dict[int, int] = {}
    remaining = target
    for index in range(len(bundles) - 1, -1, -1):
        if used_bundle[index][remaining]:
            value, notes = bundles[index]
            dispensed[value] = dispensed.get(value, 0) + notes
            remaining -= (value // unit) * notes

    return dict(sorted(dispensed.items(), reverse=True))
