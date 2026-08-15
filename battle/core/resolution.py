"""Unified resolution pipeline. Every action type resolves through a
resolve_* function that returns a structured ResolutionResult."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional

from . import dice
from .models import ActionError, AttackSpec

_UNCONSCIOUS = "unconscious"


@dataclass
class ResolutionResult:
    ok: bool = True
    lines: list = field(default_factory=list)      # human-readable per-step lines
    errors: list = field(default_factory=list)
    hp_before: dict = field(default_factory=dict)
    hp_after: dict = field(default_factory=dict)

    def add(self, line: str) -> None:
        self.lines.append(line)

    def summary(self) -> str:
        return "\n".join(self.lines)


def _guard_turn(enc, cid, **kw) -> None:
    enc.assert_turn(cid, **kw)


def _snapshot_hp(enc, *ids) -> dict:
    return {i: enc.combatants[i].hp for i in ids}


def _enemy_within(enc, cid: str, dist_ft: int) -> list:
    me = enc.combatants[cid]
    foes = []
    for other in enc.combatants.values():
        if other.id == me.id or not me.is_enemy(other):
            continue
        if other.hp <= 0:
            continue
        if enc.map.distance_ft(me, other) <= dist_ft:
            foes.append(other)
    return foes


def collect_advantage(enc, attacker_id: str, target_id: str,
                      attack: Optional[AttackSpec], explicit: Optional[str]) -> dict:
    """Advantage sources (M1): explicit flag, prone, blinded, unconscious,
    ranged-while-adjacent-enemy. Returns {advantage, disadvantage, reasons}."""
    att = enc.combatants[attacker_id]
    tgt = enc.combatants[target_id]
    adv, disadv, reasons = False, False, []

    if explicit == "advantage":
        adv, reasons = True, ["明确优势"]
    elif explicit == "disadvantage":
        disadv, reasons = True, ["明确劣势"]

    if "prone" in att.conditions and not att.hp <= 0:
        disadv = True
        reasons.append("俯卧攻击劣势")
    if tgt.hp > 0 and "prone" in tgt.conditions:
        if attack and attack.kind == "weapon" and attack.range_ft[0] <= 5:
            adv, reasons = True, reasons + ["目标俯卧（近战优势）"]
        else:
            disadv, reasons = True, reasons + ["目标俯卧（远程劣势）"]
    if att.hp <= 0 or "unconscious" in att.conditions:
        disadv = True
        reasons.append("昏迷")
    if attack and attack.kind == "weapon" and attack.range_ft[1] and _enemy_within(enc, attacker_id, 5):
        disadv = True
        reasons.append("近身敌人威胁（远程劣势）")
    return {"advantage": adv, "disadvantage": disadv, "reasons": reasons}


def apply_damage(enc, cid: str, amount: int, damage_type: Optional[str] = None,
                 *, is_crit: bool = False) -> dict:
    """Apply damage: temp HP first; 0 HP → unconscious + death-save failure."""
    c = enc.combatants[cid]
    before = c.hp
    remaining = amount
    if c.temp_hp > 0:
        absorbed = min(c.temp_hp, remaining)
        c.temp_hp -= absorbed
        remaining -= absorbed
    c.hp = max(0, c.hp - remaining)

    # 0-HP bookkeeping: entering 0 → unconscious + reset saves
    if c.hp == 0 and before > 0:
        if "unconscious" not in c.conditions:
            c.conditions.append(_UNCONSCIOUS)
        c.death_saves = {"successes": 0, "failures": 0, "stable": False}
    elif c.hp == 0 and before == 0 and remaining > 0:
        fails = 2 if is_crit else 1
        c.death_saves["failures"] = min(3, c.death_saves["failures"] + fails)
    return {"hp_before": before, "hp_after": c.hp, "temp_hp": c.temp_hp}


def resolve_attack(enc, attacker_id: str, target_id: str,
                   attack: Optional[AttackSpec] = None,
                   *, injected_d20: Optional[int] = None,
                   advantage: Optional[str] = None,
                   force: bool = False) -> ResolutionResult:
    """One attack: gates → range → advantage → roll → hit → damage → apply."""
    _guard_turn(enc, attacker_id, reaction=False)
    att = enc.combatants[attacker_id]
    tgt = enc.combatants[target_id]
    r = ResolutionResult(hp_before=_snapshot_hp(enc, target_id))

    if attack is None:
        attack = att.actor.attacks[0] if att.actor.attacks else None
    if attack is None:
        raise ActionError(f"{att.id} 没有任何可用攻击")
    if not force:
        dist = enc.map.distance_ft(att, tgt)
        if dist > attack.max_range_ft():
            raise ActionError(
                f"{attack.name} 射程 {attack.max_range_ft()}ft，实际距离 {dist}ft —— 射程外")
        r.add(f"射程检查: {attack.name} {attack.max_range_ft()}ft vs 距离 {dist}ft ✓")

    adv = collect_advantage(enc, attacker_id, target_id, attack, advantage)
    roll = dice.roll_d20(attack.attack_bonus or 0, advantage=(
        "advantage" if adv["advantage"] and not adv["disadvantage"]
        else "disadvantage" if adv["disadvantage"] and not adv["advantage"]
        else None), injected=injected_d20)
    if adv["reasons"]:
        r.add(f"优劣势: {', '.join(adv['reasons'])}")

    hit = roll["crit"] or (not roll["fumble"] and roll["total"] >= tgt.actor.ac)
    line = f"{att.id} 攻击 {tgt.id}: d20({roll['d20']}){' + ' + str(roll['mod']) if roll['mod'] else ''} = {roll['total']} vs AC {tgt.actor.ac} → {'命中' if hit else '未命中'}"
    if roll["crit"]:
        line += " *** 暴击 ***"
    elif roll["fumble"]:
        line += " (失手)"
    r.add(line)

    if hit and attack.damage:
        dmg, rolls = dice.roll_dice(attack.damage)
        crit_note = ""
        if roll["crit"]:
            dmg2, rolls2 = dice.roll_dice(attack.damage.split("+")[0].split("-")[0])
            dmg += dmg2
            rolls = rolls + rolls2
            crit_note = " (暴击翻倍骰)"
        r.add(f"伤害: {attack.damage} → {rolls} = {dmg}{crit_note} {attack.damage_type or ''}")
        apply_damage(enc, target_id, dmg, attack.damage_type, is_crit=roll["crit"])
        r.hp_after = {target_id: tgt.hp}
        if not tgt.alive:
            r.add(f"{tgt.id} 生命值归零——陷入昏迷，死亡豁免 0/0")

    enc.record(action="attack", attacker=attacker_id, target=target_id,
               attack=attack.name, lines=r.lines)
    enc.push_undo()
    return r


def resolve_spell(enc, caster_id: str, spell_name: str, targets: list,
                  *, dc: Optional[int] = None, stat: str = "dex",
                  damage: Optional[str] = None, damage_type: Optional[str] = None,
                  attack_bonus: Optional[int] = None,
                  attack: Optional[AttackSpec] = None,
                  injected_d20: Optional[int] = None,
                  force: bool = False) -> ResolutionResult:
    """M1 spell resolution: per-target save (DC) or spell attack roll.
    Geometry/AoE arrives in M3; M1 resolves an explicit target list."""
    _guard_turn(enc, caster_id)
    cast = enc.combatants[caster_id]
    r = ResolutionResult()

    if attack is not None:
        # spell attack against a single target
        tgt = targets[0]
        sub = resolve_attack(enc, caster_id, tgt, attack,
                             injected_d20=injected_d20, force=force)
        sub.lines.insert(0, f"施法 {spell_name}")
        return sub
    r.hp_before = _snapshot_hp(enc, *targets)
    for tid in targets:
        t = enc.combatants[tid]
        d20 = dice.roll_d20(injected=injected_d20)
        passed = d20["total"] >= (dc or 10)
        r.add(f"{t.id} 豁免 {stat} vs DC {dc}: d20({d20['d20']}) + mod = {d20['total']} → {'成功' if passed else '失败'}")
        if not passed and damage:
            dmg, rolls = dice.roll_dice(damage)
            apply_damage(enc, tid, dmg, damage_type)
            r.add(f"伤害 {damage} → {rolls} = {dmg} {damage_type or ''}")
    r.hp_after = {tid: enc.combatants[tid].hp for tid in targets}
    enc.record(action="cast", caster=caster_id, spell=spell_name,
               targets=targets, lines=r.lines)
    enc.push_undo()
    return r


def resolve_move(enc, combatant_id: str, dest, *, force: bool = False) -> ResolutionResult:
    """Move to dest (coords tuple or waypoint name). Checks bounds + movement budget."""
    _guard_turn(enc, combatant_id)
    c = enc.combatants[combatant_id]
    if isinstance(dest, str):
        dest = enc.waypoint(dest)
    if not enc.map.in_bounds(*dest):
        raise ActionError(f"目标 ({dest[0]},{dest[1]}) 超出地图")
    dx = (abs(dest[0] - c.x) + abs(dest[1] - c.y)) * enc.map.grid_size_ft
    if not force and dx > c.movement_left_ft:
        raise ActionError(f"移动 {dx}ft 超出剩余移动力 {c.movement_left_ft}ft")
    if c.acted:
        raise ActionError(f"{c.id} 本回合已用动作")
    r = ResolutionResult()
    r.add(f"{c.id} 移动 {dx}ft → ({dest[0]},{dest[1]})")
    c.movement_left_ft -= dx
    c.x, c.y = dest
    enc.record(action="move", combatant=combatant_id, dest=list(dest), lines=r.lines)
    enc.push_undo()
    return r


def resolve_dash(enc, combatant_id: str, dest) -> ResolutionResult:
    _guard_turn(enc, combatant_id)
    c = enc.combatants[combatant_id]
    if c.acted:
        raise ActionError(f"{c.id} 本回合已用动作")
    speed = c.actor.speed_ft
    if isinstance(dest, str):
        dest = enc.waypoint(dest)
    dx = (abs(dest[0] - c.x) + abs(dest[1] - c.y)) * enc.map.grid_size_ft
    if dx > speed:
        raise ActionError(f"冲刺移动 {dx}ft 超出速度 {speed}ft")
    r = ResolutionResult()
    r.add(f"{c.id} 冲刺 {dx}ft → ({dest[0]},{dest[1]})（动作已用）")
    c.x, c.y = dest
    c.acted = True
    c.movement_left_ft = 0
    enc.record(action="dash", combatant=combatant_id, dest=list(dest), lines=r.lines)
    enc.push_undo()
    return r


def resolve_dodge(enc, combatant_id: str) -> ResolutionResult:
    _guard_turn(enc, combatant_id)
    c = enc.combatants[combatant_id]
    if c.acted:
        raise ActionError(f"{c.id} 本回合已用动作")
    r = ResolutionResult()
    r.add(f"{c.id} 闪避（本回合攻击劣势）")
    c.acted = True
    enc.record(action="dodge", combatant=combatant_id, lines=r.lines)
    enc.push_undo()
    return r


def resolve_disengage(enc, combatant_id: str) -> ResolutionResult:
    _guard_turn(enc, combatant_id)
    c = enc.combatants[combatant_id]
    if c.acted:
        raise ActionError(f"{c.id} 本回合已用动作")
    r = ResolutionResult()
    r.add(f"{c.id} 脱离（本回合移动不触发借机攻击）")
    c.acted = True
    enc.record(action="disengage", combatant=combatant_id, lines=r.lines)
    enc.push_undo()
    return r


def resolve_save(enc, target_id: str, dc: int, stat: str,
                 *, injected_d20: Optional[int] = None) -> ResolutionResult:
    """One saving throw; result recorded for the DM to narrate."""
    t = enc.combatants[target_id]
    d20 = dice.roll_d20(injected=injected_d20)
    passed = d20["total"] >= dc
    r = ResolutionResult()
    r.add(f"{t.id} {stat}豁免 vs DC {dc}: d20({d20['d20']}) + mod = {d20['total']} → {'成功' if passed else '失败'}")
    enc.record(action="save", combatant=target_id, stat=stat, dc=dc,
               passed=passed, lines=r.lines)
    return r


def resolve_death_save(enc, combatant_id: str,
                       *, injected_d20: Optional[int] = None) -> ResolutionResult:
    """Death saving throw: 10+ success, 9- failure, 20 → stable, 1 → 2 failures."""
    c = enc.combatants[combatant_id]
    if c.hp > 0:
        raise ActionError(f"{c.id} 尚未倒下")
    d20 = dice.roll_d20(injected=injected_d20)
    r = ResolutionResult()
    if d20["d20"] == 20:
        c.hp = 1
        if "unconscious" in c.conditions:
            c.conditions.remove("unconscious")
        c.death_saves = {"successes": 0, "failures": 0, "stable": False}
        r.add(f"{c.id} 死亡豁免: d20({d20['d20']}) = 20 —— 恢复 1 HP 并苏醒!")
    elif d20["d20"] == 1:
        c.death_saves["failures"] = min(3, c.death_saves["failures"] + 2)
        r.add(f"{c.id} 死亡豁免: d20({d20['d20']}) = 1 —— 两次失败!")
    elif d20["total"] >= 10:
        c.death_saves["successes"] = min(3, c.death_saves["successes"] + 1)
        r.add(f"{c.id} 死亡豁免: d20({d20['d20']}) = {d20['total']} → 成功 {c.death_saves['successes']}/3")
    else:
        c.death_saves["failures"] = min(3, c.death_saves["failures"] + 1)
        r.add(f"{c.id} 死亡豁免: d20({d20['d20']}) = {d20['total']} → 失败 {c.death_saves['failures']}/3")
    if c.death_saves["successes"] >= 3 and not c.death_saves["stable"]:
        c.death_saves["stable"] = True
        r.add(f"{c.id} 稳定（3 次成功）")
    if c.death_saves["failures"] >= 3:
        r.add(f"{c.id} 死亡（3 次失败）")
    enc.record(action="death_save", combatant=combatant_id, lines=r.lines)
    return r
