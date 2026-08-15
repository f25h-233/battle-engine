"""Data models and the encounter state machine."""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Optional


class ActionError(Exception):
    """Raised when an action violates a state gate (order/range/resources)."""


@dataclass
class AttackSpec:
    name: str
    kind: str = "weapon"                  # weapon | spell | special
    attack_bonus: Optional[int] = None
    range_ft: tuple = (5, 0)              # (melee reach or short range, long range; 0 = none)
    damage: Optional[str] = None          # dice notation, e.g. "2d6+5"
    damage_type: Optional[str] = None     # 穿刺/挥砍/钝击/火焰...
    save_dc: Optional[int] = None
    save_stat: Optional[str] = None       # str|dex|con|int|wis|cha
    aoe_radius_ft: Optional[int] = None   # >0 时该施法为 AoE（面板预览与覆盖判定用）
    note: str = ""

    def long_range_ft(self) -> Optional[int]:
        return self.range_ft[1] if self.range_ft and self.range_ft[1] else None

    def max_range_ft(self) -> int:
        return self.range_ft[1] if self.long_range_ft() else self.range_ft[0]


@dataclass
class Actor:
    """Static stat source (monster block or player sheet snapshot)."""
    name: str
    kind: str                              # "pc" | "npc"
    ac: int
    max_hp: int
    speed_ft: int
    dex_mod: int
    attacks: list = field(default_factory=list)   # list[AttackSpec]
    conditions: list = field(default_factory=list)

    def attack(self, name: str) -> Optional[AttackSpec]:
        for atk in self.attacks:
            if atk.name == name:
                return atk
        return None


@dataclass
class Combatant:
    """Battle instance of an actor: position, current HP, turn resources."""
    id: str
    actor: Actor
    x: int
    y: int
    hp: int
    temp_hp: int = 0
    conditions: list = field(default_factory=list)
    concentration: Optional[str] = None
    initiative: Optional[int] = None
    initiative_d20: Optional[int] = None
    acted: bool = False
    bonus_acted: bool = False
    reaction_used: bool = False
    movement_left_ft: int = 0
    dodging: bool = False
    disengaged: bool = False
    death_saves: dict = field(default_factory=lambda: {"successes": 0, "failures": 0, "stable": False})

    @property
    def alive(self) -> bool:
        return self.hp > 0

    @property
    def max_hp(self) -> int:
        return self.actor.max_hp

    def is_enemy(self, other: "Combatant") -> bool:
        return self.actor.kind != other.actor.kind


@dataclass
class GridMap:
    width: int
    height: int
    grid_size_ft: int = 5

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def distance_ft(self, a: Combatant, b: Combatant) -> int:
        """Euclidean distance rounded to nearest foot, grid-aligned."""
        dx = (a.x - b.x) * self.grid_size_ft
        dy = (a.y - b.y) * self.grid_size_ft
        return int(math.hypot(dx, dy) + 0.5)

    def in_range(self, a: Combatant, b: Combatant, range_ft: int) -> bool:
        return self.distance_ft(a, b) <= range_ft

    def cells_in_radius(self, cx: int, cy: int, radius_ft: int) -> list:
        """圆形覆盖格：格心欧氏距离 ×5 ≤ radius_ft（与 distance_ft 同公式）。"""
        r = max(0, radius_ft)
        cells = []
        for y in range(self.height):
            for x in range(self.width):
                dx = (x - cx) * self.grid_size_ft
                dy = (y - cy) * self.grid_size_ft
                if int(math.hypot(dx, dy) + 0.5) <= r:
                    cells.append((x, y))
        return cells


class Encounter:
    """Combat state machine, persisted to <campaign>/battle.json."""

    VALID_STATUS = ("setup", "initiative_rolled", "combat_active", "ended")

    def __init__(self, campaign: str, width: int, height: int, ruleset: str = "2014"):
        self.campaign = campaign
        self.ruleset = ruleset
        self.map = GridMap(width, height)
        self.combatants: dict[str, Combatant] = {}
        self.turn_order: list[str] = []
        self.round: int = 0
        self.turn_index: int = 0
        self.status: str = "setup"
        self.log: list[dict] = []
        self.undo_stack: list[dict] = []
        self.waypoints: dict[str, tuple] = {}

    # ── construction ────────────────────────────────────────────
    def add_combatant(self, actor: Actor, x: int, y: int,
                      cid: Optional[str] = None) -> Combatant:
        if not self.map.in_bounds(x, y):
            raise ActionError(f"位置 ({x},{y}) 超出地图 {self.map.width}x{self.map.height}")
        cid = cid or actor.name
        key = cid.lower().replace(" ", "")
        if key in self.combatants:
            raise ActionError(f"战斗员 {cid} 已存在")
        c = Combatant(id=key, actor=actor, x=x, y=y, hp=actor.max_hp)
        self.combatants[key] = c
        return c

    def add_waypoint(self, name: str, pos: tuple) -> None:
        if not (isinstance(pos, tuple) and len(pos) == 2):
            raise ActionError(f"地标位置必须是 (x,y) 坐标: {pos}")
        self.waypoints[name] = pos

    def waypoint(self, name: str) -> tuple:
        try:
            return self.waypoints[name]
        except KeyError:
            raise ActionError(f"未命名地标: {name}（可用 battle waypoint add 定义）")

    def cells_in_radius(self, cx: int, cy: int, radius_ft: int) -> list:
        return self.map.cells_in_radius(cx, cy, radius_ft)

    # ── state gates ─────────────────────────────────────────────
    def assert_combat_ready(self) -> None:
        if self.status not in ("initiative_rolled", "combat_active"):
            raise ActionError(f"战斗尚未掷先攻（当前状态: {self.status}）")

    def assert_turn(self, cid: str, *, reaction: bool = False,
                    legendary: bool = False) -> None:
        """Gate: standard actions require initiative + the actor's own turn."""
        self.assert_combat_ready()
        if self.status == "combat_active":
            cur = self.current()
            if cur and cur.id == cid:
                return
            if reaction and not self._reaction_spent(cid):
                return
            if legendary:
                return
            raise ActionError(f"不是 {cid} 的回合（当前: {self.current().id if self.current() else '—'}）")

    def _reaction_spent(self, cid: str) -> bool:
        return self.combatants[cid].reaction_used

    # ── lifecycle ───────────────────────────────────────────────
    def roll_initiative(self) -> list:
        """Roll d20+dex for all combatants; status → initiative_rolled."""
        from . import initiative
        if self.status != "setup":
            raise ActionError(f"先攻已在 {self.status} 状态下掷过")
        ordered = initiative.roll_initiative(list(self.combatants.values()))
        self.turn_order = [c.id for c in ordered]
        self.status = "initiative_rolled"
        return ordered

    def start_combat(self) -> None:
        if self.status == "combat_active":
            raise ActionError("战斗已在进行中")
        self.assert_combat_ready()
        if not self.turn_order:
            raise ActionError("没有战斗员——无法开始战斗（先 add-monster/add-player）")
        self.status = "combat_active"
        self.round = 1
        self.turn_index = 0
        self.turn_start(self.turn_order[0])

    def current(self):
        if self.status != "combat_active" or not self.turn_order:
            return None
        return self.combatants.get(self.turn_order[self.turn_index])

    def turn_start(self, cid: str) -> None:
        c = self.combatants[cid]
        c.acted = False
        c.bonus_acted = False
        c.reaction_used = False
        c.movement_left_ft = c.actor.speed_ft
        c.dodging = False          # 闪避效果直到自己的下回合开始（本回合结束时重置）
        c.disengaged = False       # 脱离效果只覆盖本回合

    def turn_end(self, cid: str) -> None:
        c = self.combatants[cid]
        c.acted = True

    def next_turn(self) -> None:
        if self.status != "combat_active":
            raise ActionError("战斗未开始")
        self.turn_end(self.turn_order[self.turn_index])
        if self.turn_index >= len(self.turn_order) - 1:
            self.round += 1
            self.turn_index = 0
        else:
            self.turn_index += 1
        self.turn_start(self.turn_order[self.turn_index])

    def end(self) -> None:
        self.status = "ended"

    # ── log / undo ──────────────────────────────────────────────
    def record(self, **entry) -> None:
        entry.setdefault("round", self.round)
        entry.setdefault("actor", self.current().id if self.current() else None)
        self.log.append(entry)
        if len(self.log) > 200:
            del self.log[: len(self.log) - 200]

    def snapshot(self) -> dict:
        return self.to_dict()

    def restore(self, snap: dict) -> None:
        """Restore from a snapshot dict (undo). Rebuilds in place.
        快照内嵌恢复时刻的历史栈拷贝 → 弹顶恢复后剩余历史保留，可连续多次 undo。"""
        fresh = Encounter.from_dict(snap)
        self.__dict__.update(fresh.__dict__)

    def push_undo(self) -> None:
        self.undo_stack.append(self.to_dict())
        if len(self.undo_stack) > 50:
            self.undo_stack.pop(0)

    def pop_undo(self) -> bool:
        if not self.undo_stack:
            return False
        self.restore(self.undo_stack.pop())
        return True

    # ── serialization ───────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "campaign": self.campaign,
            "ruleset": self.ruleset,
            "status": self.status,
            "round": self.round,
            "turn_index": self.turn_index,
            "turn_order": self.turn_order,
            "map": {"width": self.map.width, "height": self.map.height,
                    "grid_size_ft": self.map.grid_size_ft},
            "waypoints": {k: list(v) for k, v in self.waypoints.items()},
            "combatants": {k: {
                "id": c.id, "x": c.x, "y": c.y, "hp": c.hp, "temp_hp": c.temp_hp,
                "conditions": c.conditions, "concentration": c.concentration,
                "initiative": c.initiative, "initiative_d20": c.initiative_d20,
                "acted": c.acted,
                "bonus_acted": c.bonus_acted, "reaction_used": c.reaction_used,
                "movement_left_ft": c.movement_left_ft,
                "dodging": c.dodging, "disengaged": c.disengaged,
                "death_saves": c.death_saves,
                "actor": {
                    "name": c.actor.name, "kind": c.actor.kind, "ac": c.actor.ac,
                    "max_hp": c.actor.max_hp, "speed_ft": c.actor.speed_ft,
                    "dex_mod": c.actor.dex_mod,
                    "attacks": [{
                        "name": a.name, "kind": a.kind, "attack_bonus": a.attack_bonus,
                        "range_ft": list(a.range_ft), "damage": a.damage,
                        "damage_type": a.damage_type, "save_dc": a.save_dc,
                        "save_stat": a.save_stat, "aoe_radius_ft": a.aoe_radius_ft,
                        "note": a.note,
                    } for a in c.actor.attacks],
                    "conditions": c.actor.conditions,
                },
            } for k, c in self.combatants.items()},
            "log": self.log,
            "undo_stack": list(self.undo_stack),  # 拷贝：避免 push_undo 后快照自引用 → json 循环引用
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Encounter":
        m = d["map"]
        enc = cls(d["campaign"], m["width"], m["height"], d.get("ruleset", "2014"))
        enc.status = d["status"]
        enc.round = d["round"]
        enc.turn_index = d["turn_index"]
        enc.turn_order = d["turn_order"]
        enc.waypoints = {k: tuple(v) for k, v in d.get("waypoints", {}).items()}
        for k, cd in d["combatants"].items():
            ad = cd["actor"]
            actor = Actor(name=ad["name"], kind=ad["kind"], ac=ad["ac"],
                          max_hp=ad["max_hp"], speed_ft=ad["speed_ft"],
                          dex_mod=ad["dex_mod"],
                          attacks=[AttackSpec(**{**a, "range_ft": tuple(a["range_ft"])})
                                   for a in ad.get("attacks", [])],
                          conditions=ad.get("conditions", []))
            c = Combatant(id=cd["id"], actor=actor, x=cd["x"], y=cd["y"],
                          hp=cd["hp"], temp_hp=cd.get("temp_hp", 0),
                          conditions=cd.get("conditions", []),
                          concentration=cd.get("concentration"),
                          initiative=cd.get("initiative"),
                          initiative_d20=cd.get("initiative_d20"),
                          acted=cd.get("acted", False),
                          bonus_acted=cd.get("bonus_acted", False),
                          reaction_used=cd.get("reaction_used", False),
                          movement_left_ft=cd.get("movement_left_ft", 0),
                          dodging=cd.get("dodging", False),
                          disengaged=cd.get("disengaged", False),
                          death_saves=cd.get("death_saves", {"successes": 0, "failures": 0, "stable": False}))
            enc.combatants[k] = c
        enc.log = d.get("log", [])
        enc.undo_stack = d.get("undo_stack", [])
        return enc
