"""Parse SRD monster description prose into structured AttackSpec entries.

SRD monster attacks are prose, e.g.:
  "Action ◆ Scimitar: Melee Weapon Attack: +4 to hit, reach 5 ft.,
   one target. Hit: 5 (1d6 + 2) slashing damage."
Parse per-paragraph; failures degrade to 'special' actions (semi-auto fallback).
"""

from __future__ import annotations
import re
from .models import AttackSpec

_ATK_RE = re.compile(
    r"(Melee|Ranged)\s+(Weapon|Spell)\s+Attack:\s*([+-]?\d+)\s+to hit,\s*"
    r"(?:reach\s+(\d+)\s+ft\.|range\s+(\d+)/(\d+)\s+ft\.)"
)
_DMG_RE = re.compile(r"Hit:\s*\d+\s*\((\d+d\d+(?:\s*[+-]\s*\d+)?)\)\s+(\w+)\s+damage")
_SAVE_RE = re.compile(r"DC\s+(\d+)\s+(\w+)\s+saving throw")


def parse_monster_actions(entry: dict) -> list:
    """entry: {'name', 'description'} → list[AttackSpec]."""
    paras = [p.strip() for p in entry.get("description", "").split("\n\n") if p.strip()]
    specs = []
    for para in paras:
        spec = _parse_paragraph(para)
        if spec:
            specs.append(spec)
    return specs


def _parse_paragraph(para: str) -> AttackSpec | None:
    # Section header: "Action ◆ Name: ..." / "Bonus Action ◆ ..." / "Reaction ◆ ..." / "Legendary ◆ ..."
    # The bundled SRD file uses U+2014 (—) as the marker; the brief's prose uses U+25C6 (◆).
    m = re.match(r"^(?:Bonus\s+)?(Action|Reaction|Legendary)\s*[◆—]\s+([^:]+):\s*(.*)$", para, re.S)
    if not m:
        return None
    kind = m.group(1)
    name = m.group(2).strip()
    body = m.group(3).strip()

    atk = _ATK_RE.search(body)
    dmg = _DMG_RE.search(body)
    sav = _SAVE_RE.search(body)

    spec = AttackSpec(name=name, note="")
    if atk:
        spec.kind = "spell" if atk.group(2) == "Spell" else "weapon"
        spec.attack_bonus = int(atk.group(3))
        if atk.group(4):                      # melee reach
            spec.range_ft = (int(atk.group(4)), 0)
        else:                                 # ranged short/long
            spec.range_ft = (int(atk.group(5)), int(atk.group(6)))
    else:
        spec.kind = "special"                 # no attack roll — degrade gracefully

    if dmg:
        spec.damage = dmg.group(1).replace(" ", "")
        spec.damage_type = dmg.group(2)
    if sav:
        spec.save_dc = int(sav.group(1))
        spec.save_stat = sav.group(2).lower()

    spec.note = body
    return spec
