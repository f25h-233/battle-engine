"""Dice with deterministic seeding and result injection (players roll)."""

from __future__ import annotations
import random
import re
from typing import Optional

_rng = random.Random()


def seed(n: int) -> None:
    _rng.seed(n)


def roll_dice(notation: str) -> tuple:
    """Parse NdS+M notation (multi-group like '1d8+2d6+3' also supported,
    crit doubling needs to roll all dice groups); return (total, rolls)."""
    m = re.match(r"^((?:\d*d\d+)(?:\+\d*d\d+)*)([+-]\d+)?$", notation.strip().lower())
    if not m:
        raise ValueError(f"Bad dice notation: {notation}")
    mod = int(m.group(2)) if m.group(2) else 0
    rolls = []
    for part in m.group(1).split("+"):
        n_part, s_part = part.split("d")
        n = int(n_part) if n_part else 1
        s = int(s_part)
        rolls.extend(_rng.randint(1, s) for _ in range(n))
    return sum(rolls) + mod, rolls


def roll_d20(mod: int = 0, advantage: Optional[str] = None,
             injected: Optional[int] = None) -> dict:
    """Roll d20. advantage: None | 'advantage' | 'disadvantage'.
    injected: player-provided die face — bypasses randomness (roll_mode: players)."""
    if injected is not None:
        if not isinstance(injected, int) or not (1 <= injected <= 20):
            raise ValueError(f"注入的 d20 必须是 1–20 之间的整数（收到 {injected!r}）")
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
