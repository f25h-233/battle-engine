"""Initiative: d20 + dex modifier, descending order (always engine-rolled)."""

from __future__ import annotations
import random


def roll_initiative(combatants: list) -> list:
    """Roll d20 + dex_mod for each combatant, set c.initiative,
    return combatants sorted by initiative (desc), ties by dex_mod desc."""
    for c in combatants:
        c.initiative = random.randint(1, 20) + c.actor.dex_mod
    return sorted(combatants, key=lambda c: (-c.initiative, -c.actor.dex_mod))
