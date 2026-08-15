"""Dice with deterministic seeding and result injection (players roll)."""

from __future__ import annotations
import random
import re
from typing import Optional

_rng = random.Random()


def seed(n: int) -> None:
    _rng.seed(n)


def roll_dice(notation: str) -> tuple:
    """Parse NdS+M notation; return (total, individual_rolls)."""
    m = re.match(r"^(\d*)d(\d+)([+-]\d+)?$", notation.strip().lower())
    if not m:
        raise ValueError(f"Bad dice notation: {notation}")
    n = int(m.group(1)) if m.group(1) else 1
    s = int(m.group(2))
    mod = int(m.group(3)) if m.group(3) else 0
    rolls = [_rng.randint(1, s) for _ in range(n)]
    return sum(rolls) + mod, rolls


def roll_d20(mod: int = 0, advantage: Optional[str] = None,
             injected: Optional[int] = None) -> dict:
    """Roll d20. advantage: None | 'advantage' | 'disadvantage'.
    injected: player-provided die face — bypasses randomness (roll_mode: players)."""
    if injected is not None:
        raw = injected
        rolls = [injected]
    elif advantage == "advantage":
        rolls = [_rng.randint(1, 20), _rng.randint(1, 20)]
        raw = max(rolls)
    elif advantage == "disadvantage":
        rolls = [_rng.randint(1, 20), _rng.randint(1, 20)]
        raw = min(rolls)
    else:
        rolls = [_rng.randint(1, 20)]
        raw = rolls[0]
    return {"d20": raw, "rolls": rolls, "total": raw + mod, "mod": mod,
            "crit": raw == 20, "fumble": raw == 1}


def roll_hp(hp_dice: str) -> int:
    """'2d6+6' → total HP roll (monster creation)."""
    total, _ = roll_dice(hp_dice)
    return total
