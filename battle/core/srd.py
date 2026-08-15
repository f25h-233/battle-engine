"""Load the skill's bundled 5e SRD dataset (dnd5e_srd.json).

Resolution order for the SRD file:
  1. $BATTLE_SRD_PATH env override
  2. $CLAUDE_SKILL_DIR/data/dnd5e_srd.json  (Claude Code sessions)
  3. glob ~/.claude/plugins/**/neuralinitiative-claude-dnd-skill/skills/dnd/data/
  4. Fail with a clear message (user can set BATTLE_SRD_PATH)
"""

from __future__ import annotations
import glob
import json
import os
import re
from pathlib import Path

from . import monster_parser
from .models import Actor

_SRD_CACHE = None
_MONSTER_INDEX = None
_SPELL_INDEX = None


def srd_path() -> Path:
    env = os.environ.get("BATTLE_SRD_PATH", "").strip()
    if env:
        return Path(env)
    skill_dir = os.environ.get("CLAUDE_SKILL_DIR", "").strip()
    if skill_dir:
        p = Path(skill_dir) / "data" / "dnd5e_srd.json"
        if p.exists():
            return p
    hits = glob.glob(str(Path.home() / ".claude/plugins" / "**" /
                         "neuralinitiative-claude-dnd-skill/skills/dnd/data/dnd5e_srd.json"),
                     recursive=True)
    if hits:
        return Path(hits[0])
    raise FileNotFoundError(
        "找不到 dnd5e_srd.json。设置环境变量 BATTLE_SRD_PATH 指向 SRD 文件，"
        "或在 Claude Code 会话中运行（CLAUDE_SKILL_DIR 自动定位）。")


def _load_json() -> dict:
    with open(srd_path(), encoding="utf-8") as f:
        return json.load(f)


def load_srd() -> dict:
    global _SRD_CACHE
    if _SRD_CACHE is None:
        _SRD_CACHE = _load_json()
    return _SRD_CACHE


def _norm(name: str) -> str:
    return name.strip().lower().replace(" ", "")


def find_monster(name: str):
    srd_data = load_srd()
    key = _norm(name)
    for m in srd_data.get("monsters", []):
        if _norm(m.get("name", "")) == key:
            return m
    return None


def find_spell(name: str):
    srd_data = load_srd()
    key = _norm(name)
    for s in srd_data.get("spells", []):
        if _norm(s.get("name", "")) == key:
            return s
    return None


_AOE_RADIUS_RE = re.compile(r"(\d+)\s*-?\s*foot-radius")


def spell_aoe_radius(spell: dict) -> Optional[int]:
    """从法术描述散文解析半径（SRD 无结构化 AoE 字段）：
    '20-foot-radius sphere' → 20；锥形/直线/无 → None（DM 用 --radius 手动给）。"""
    m = _AOE_RADIUS_RE.search(str(spell.get("description", "")))
    return int(m.group(1)) if m else None


def _dex_mod(dex: int) -> int:
    return (dex - 10) // 2


def monster_to_actor(entry: dict, *, hp_avg: bool = True) -> Actor:
    """SRD monster entry → Actor. Attacks parsed from description prose."""
    attacks = monster_parser.parse_monster_actions(entry)
    hp = entry.get("hp", 1)
    speed = 30
    sp = str(entry.get("speed", ""))
    m = re.search(r"walk\s*(\d+)\s*ft\.", sp) or re.match(r"(\d+)\s*ft\.", sp)
    if m:
        speed = int(m.group(1))
    return Actor(
        name=entry["name"],
        kind="npc",
        ac=entry.get("ac", 10),
        max_hp=hp,
        speed_ft=speed,
        dex_mod=_dex_mod(entry.get("dex", 10)),
        attacks=attacks,
    )
