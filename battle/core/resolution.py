"""Unified resolution pipeline. Every action type resolves through a
resolve_* function that returns a structured ResolutionResult."""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional

from . import dice
from .models import ActionError, AttackSpec, Combatant

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


def _occupant(enc, x: int, y: int, exclude_id: str = None):
    """该格上的存活战斗员（排除自己）；无则 None。与前端 cellClick 判定一致。"""
    for other in enc.combatants.values():
        if other.id == exclude_id:
            continue
        if other.hp > 0 and other.x == x and other.y == y:
            return other
    return None


def _manhattan_path(enc, c: Combatant, dest) -> list:
    """曼哈顿路径逐格序列（先横向后纵向，含终点不含起点）。"""
    x, y = c.x, c.y
    dx = 1 if dest[0] >= x else -1
    dy = 1 if dest[1] >= y else -1
    steps = []
    while x != dest[0]:
        x += dx
        steps.append((x, y))
    while y != dest[1]:
        y += dy
        steps.append((x, y))
    return steps


def _melee_reach(attacker: Combatant) -> int:
    """近战触及：第一个近战攻击的 range_ft[0]（无近战攻击 → 无威胁范围）。"""
    for a in attacker.actor.attacks:
        if a.kind != "spell" and a.range_ft[0] and a.range_ft[0] <= 5:
            return a.range_ft[0]
    return 0


def _aoo_candidates(enc, mover: Combatant) -> list:
    """对移动者有威胁、有可用反应、有近战攻击的存活敌人。"""
    foes = []
    for other in enc.combatants.values():
        if other.id == mover.id or not mover.is_enemy(other):
            continue
        if other.hp <= 0 or other.reaction_used:
            continue
        if _melee_reach(other) and enc.map.distance_ft(other, mover) <= _melee_reach(other):
            foes.append(other)
    return foes


def resolve_aoo(enc, mover_id: str, attacker_id: str,
                attack: Optional[AttackSpec] = None,
                *, injected_d20: Optional[int] = None) -> ResolutionResult:
    """机会攻击：敌人用反应在移动者离开其近战范围时发起一次近战攻击。"""
    mover = enc.combatants[mover_id]
    attacker = enc.combatants[attacker_id]
    if attacker.reaction_used:
        raise ActionError(f"{attacker.id} 反应已用，无法借机攻击")
    if attack is None:
        attack = next((a for a in attacker.actor.attacks
                       if a.kind != "spell" and a.range_ft[0] and a.range_ft[0] <= 5), None)
    if attack is None:
        raise ActionError(f"{attacker.id} 没有近战攻击，无法借机攻击")
    r = ResolutionResult(hp_before={mover_id: mover.hp})
    adv = collect_advantage(enc, attacker_id, mover_id, attack, None)
    roll = dice.roll_d20(attack.attack_bonus or 0, advantage=(
        "advantage" if adv["advantage"] and not adv["disadvantage"]
        else "disadvantage" if adv["disadvantage"] and not adv["advantage"]
        else None), injected=injected_d20)
    hit = roll["crit"] or (not roll["fumble"] and roll["total"] >= mover.actor.ac)
    line = (f"借机攻击: {attacker.id} 袭击 {mover.id}: d20({roll['d20']})"
            f"{' + ' + str(roll['mod']) if roll['mod'] else ''} = {roll['total']}"
            f" vs AC {mover.actor.ac} → {'命中' if hit else '未命中'}")
    if roll["crit"]:
        line += " *** 暴击 ***"
    r.add(line)
    if hit and attack.damage:
        dmg, rolls = dice.roll_dice(attack.damage)
        if roll["crit"]:
            dmg2, rolls2 = dice.roll_dice(_dice_part(attack.damage))
            dmg += dmg2
            rolls = rolls + rolls2
        r.add(f"伤害: {attack.damage} → {rolls} = {dmg} {attack.damage_type or ''}")
        apply_damage(enc, mover_id, dmg, attack.damage_type, is_crit=roll["crit"])
        if not mover.alive:
            r.add(f"{mover.id} 生命值归零——陷入昏迷，死亡豁免 0/0")
    attacker.reaction_used = True
    r.hp_after = {mover_id: mover.hp}
    return r


def collect_advantage(enc, attacker_id: str, target_id: str,
                      attack: Optional[AttackSpec], explicit: Optional[str]) -> dict:
    """Advantage sources (M1): explicit flag, prone, unconscious,
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
    if tgt.hp > 0 and tgt.dodging:
        disadv, reasons = True, reasons + ["目标闪避中"]
    if att.hp <= 0 or "unconscious" in att.conditions:
        disadv = True
        reasons.append("昏迷")
    if attack and attack.kind == "weapon" and attack.range_ft[1] and _enemy_within(enc, attacker_id, 5):
        disadv = True
        reasons.append("近身敌人威胁（远程劣势）")
    return {"advantage": adv, "disadvantage": disadv, "reasons": reasons}


def _dice_part(notation: str) -> str:
    """Dice notation 的骰子部分（'+' 连接，去掉静态修正）：
    '1d8+2d6+3' → '1d8+2d6'；暴击翻倍时对这串再掷一次。"""
    return "+".join(re.findall(r"\d*d\d+", notation))


_STATIC_RE = re.compile(r"([+-]\d+)\s*$")


def _static_mod(notation: str) -> int:
    """Dice notation's trailing static modifier: '1d6+3' → 3, '3d8' → 0."""
    m = _STATIC_RE.search(notation.strip())
    return int(m.group(1)) if m else 0


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
                   injected_damage: Optional[list] = None,
                   advantage: Optional[str] = None,
                   force: bool = False) -> ResolutionResult:
    """One attack: gates → range → advantage → roll → hit → damage → apply."""
    _guard_turn(enc, attacker_id, reaction=False)
    att = enc.combatants[attacker_id]
    tgt = enc.combatants[target_id]
    if att.acted:
        raise ActionError(f"{att.id} 本回合已用动作")
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

    enc.push_undo()  # 快照必须在首个状态变更之前 → undo 才能真正回滚

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
        if injected_damage is not None:
            dmg = sum(injected_damage) + _static_mod(attack.damage)
            rolls = list(injected_damage)
            note = "（手动掷骰——暴击翻倍请玩家自行掷双倍）" if roll["crit"] else ""
            r.add(f"伤害: {attack.damage} → 注入 {rolls} = {dmg} {attack.damage_type or ''}{note}")
        else:
            dmg, rolls = dice.roll_dice(attack.damage)
            crit_note = ""
            if roll["crit"]:
                dmg2, rolls2 = dice.roll_dice(_dice_part(attack.damage))
                dmg += dmg2
                rolls = rolls + rolls2
                crit_note = " (暴击翻倍骰)"
            r.add(f"伤害: {attack.damage} → {rolls} = {dmg}{crit_note} {attack.damage_type or ''}")
        apply_damage(enc, target_id, dmg, attack.damage_type, is_crit=roll["crit"])
        if not tgt.alive:
            r.add(f"{tgt.id} 生命值归零——陷入昏迷，死亡豁免 0/0")

    r.hp_after = {target_id: tgt.hp}  # miss 也记录当前 HP（此前只在命中分支赋值）
    att.acted = True                  # 标准动作已消耗（此前攻击不置位 → 可无限攻击）
    enc.record(action="attack", attacker=attacker_id, target=target_id,
               attack=attack.name, lines=r.lines)
    return r


def resolve_spell(enc, caster_id: str, spell_name: str, targets: list,
                  *, dc: Optional[int] = None, stat: str = "dex",
                  damage: Optional[str] = None, damage_type: Optional[str] = None,
                  attack_bonus: Optional[int] = None,
                  attack: Optional[AttackSpec] = None,
                  injected_d20: Optional[int] = None,
                  injected_damage: Optional[list] = None,
                  force: bool = False,
                  center: Optional[tuple] = None,
                  radius_ft: Optional[int] = None,
                  half_on_save: Optional[bool] = None) -> ResolutionResult:
    """Spell resolution: per-target save (DC) or spell attack roll;
    center 给出时走 AoE 路径（覆盖格逐目标豁免、成功豁免半伤）。"""
    _guard_turn(enc, caster_id)
    cast = enc.combatants[caster_id]
    r = ResolutionResult()   # AoE 块也要写 r（brief 原文把 r 初始化放在 AoE 块后 → UnboundLocalError，此处提前）
    if center is not None:
        # ── AoE 路径：覆盖格逐目标豁免，成功豁免半伤（5e 火球/燃烧之手等）──
        if attack is not None:
            dc = dc if dc is not None else attack.save_dc
            stat = stat if stat else (attack.save_stat or "dex")
            damage = damage or attack.damage
            damage_type = damage_type or attack.damage_type
        radius_ft = radius_ft if radius_ft is not None else 0
        half = True if half_on_save is None else half_on_save
        cells = enc.cells_in_radius(*center, radius_ft)
        victims = [c for c in enc.combatants.values()
                   if c.hp > 0 and (c.x, c.y) in cells]
        if not victims:
            raise ActionError(
                f"{spell_name} 半径 {radius_ft}ft 内没有存活目标（中心 {center}）")
        if cast.acted:
            raise ActionError(f"{cast.id} 本回合已用动作")
        enc.push_undo()  # 首个状态变更之前
        r.hp_before = _snapshot_hp(enc, *[v.id for v in victims])
        r.add(f"施法 {spell_name}（AoE 半径 {radius_ft}ft，中心 {center}，覆盖 {len(cells)} 格）")
        # 5e 区域伤害规则：掷骰一次、全体共享（半伤同样同值）
        if damage and injected_damage is not None:
            dmg_full = sum(injected_damage) + _static_mod(damage)
            rolls = list(injected_damage)
        elif damage:
            dmg_full, rolls = dice.roll_dice(damage)
        else:
            dmg_full, rolls = 0, []
        for v in victims:
            adv = "advantage" if (v.dodging and stat == "dex") else None
            d20 = dice.roll_d20(injected=injected_d20, advantage=adv)
            passed = d20["total"] >= (dc or 10)
            r.add(f"{v.id} 豁免 {stat} vs DC {dc}: d20({d20['d20']})"
                  f" + mod = {d20['total']} → {'成功' if passed else '失败'}")
            if damage and not passed:
                r.add(f"伤害 {damage} → {rolls} = {dmg_full} {damage_type or ''}")
                apply_damage(enc, v.id, dmg_full, damage_type)
            elif damage and passed and half:
                dmg = dmg_full // 2
                r.add(f"伤害 {damage} → {rolls} = {dmg}（成功豁免半伤） {damage_type or ''}")
                apply_damage(enc, v.id, dmg, damage_type)
        r.hp_after = {v.id: enc.combatants[v.id].hp for v in victims}
        cast.acted = True
        enc.record(action="cast", caster=caster_id, spell=spell_name,
                   center=list(center), radius_ft=radius_ft, targets=[v.id for v in victims],
                   lines=r.lines)
        return r

    if not targets and center is None:
        raise ActionError("施法需要目标（targets 或 center/radius）")
    if attack is not None:
        # spell attack against a single target（委托 resolve_attack：其内部置 acted）
        tgt = targets[0]
        sub = resolve_attack(enc, caster_id, tgt, attack,
                             injected_d20=injected_d20,
                             injected_damage=injected_damage, force=force)
        sub.lines.insert(0, f"施法 {spell_name}")
        return sub
    if cast.acted:
        raise ActionError(f"{cast.id} 本回合已用动作")
    enc.push_undo()  # 豁免路径自己 push（首个状态变更之前）
    r.hp_before = _snapshot_hp(enc, *targets)
    for tid in targets:
        t = enc.combatants[tid]
        adv = "advantage" if (t.dodging and stat == "dex") else None
        d20 = dice.roll_d20(injected=injected_d20, advantage=adv)
        if adv:
            r.add(f"{t.id} 闪避中——敏捷豁免优势")
        passed = d20["total"] >= (dc or 10)
        r.add(f"{t.id} 豁免 {stat} vs DC {dc}: d20({d20['d20']}) + mod = {d20['total']} → {'成功' if passed else '失败'}")
        if not passed and damage:
            if injected_damage is not None:
                dmg = sum(injected_damage) + _static_mod(damage)
                rolls = list(injected_damage)
                r.add(f"伤害 {damage} → 注入 {rolls} = {dmg} {damage_type or ''}")
            else:
                dmg, rolls = dice.roll_dice(damage)
                r.add(f"伤害 {damage} → {rolls} = {dmg} {damage_type or ''}")
            apply_damage(enc, tid, dmg, damage_type)
    r.hp_after = {tid: enc.combatants[tid].hp for tid in targets}
    cast.acted = True  # 施法消耗标准动作（豁免路径自己置位）
    enc.record(action="cast", caster=caster_id, spell=spell_name,
               targets=targets, lines=r.lines)
    return r


def resolve_move(enc, combatant_id: str, dest, *, force: bool = False) -> ResolutionResult:
    """Move to dest (coords tuple or waypoint name). Checks bounds + movement budget."""
    _guard_turn(enc, combatant_id)
    c = enc.combatants[combatant_id]
    if isinstance(dest, str):
        dest = enc.waypoint(dest)
    if not enc.map.in_bounds(*dest):
        raise ActionError(f"目标 ({dest[0]},{dest[1]}) 超出地图")
    occ = _occupant(enc, dest[0], dest[1], exclude_id=combatant_id)
    if occ:
        raise ActionError(f"目标格 ({dest[0]},{dest[1]}) 被 {occ.actor.name} 占据")
    dx = (abs(dest[0] - c.x) + abs(dest[1] - c.y)) * enc.map.grid_size_ft
    if not force and dx > c.movement_left_ft:
        raise ActionError(f"移动 {dx}ft 超出剩余移动力 {c.movement_left_ft}ft")
    # 5e：移动与动作独立——用动作（攻击/施法）后仍可移动剩余移动力；
    # 动作资源由 resolve_attack/cast/dash 各自的 acted 门管理，移动只看移动力。
    enc.push_undo()  # 首个状态变更之前（移动 + 途中所有 AoO 作为一个 undo 单元）
    r = ResolutionResult(hp_before={combatant_id: c.hp})
    if not c.disengaged:
        foes = _aoo_candidates(enc, c)
    else:
        foes = []
    walked = 0
    for step in _manhattan_path(enc, c, dest):
        r.add(f"{c.id} 移动 → ({step[0]},{step[1]})")
        c.x, c.y = step
        walked += 1
        if not c.disengaged:
            for foe in list(foes):        # 副本迭代：remove 后迭代器不跳号（多敌场景）
                if enc.map.distance_ft(foe, c) > _melee_reach(foe):
                    sub = resolve_aoo(enc, c.id, foe.id, injected_d20=None)
                    r.lines.extend(sub.lines)
                    if sub.hp_after:
                        r.hp_after.update(sub.hp_after)
                    foes.remove(foe)      # 一次移动每敌人最多一次
                    if not c.alive:
                        break             # 被击倒 → 移动终止（5e：昏迷不能移动）
        if not c.alive:
            break
    dx = walked * enc.map.grid_size_ft  # 实走距离（被击倒提前终止时少于计划距离）
    r.add(f"{c.id} 本次移动 {dx}ft（剩余 {c.movement_left_ft}ft）")
    c.movement_left_ft -= dx
    enc.record(action="move", combatant=combatant_id, dest=list(dest), lines=r.lines)
    return r


def resolve_dash(enc, combatant_id: str, dest) -> ResolutionResult:
    _guard_turn(enc, combatant_id)
    c = enc.combatants[combatant_id]
    if c.acted:
        raise ActionError(f"{c.id} 本回合已用动作")
    speed = c.actor.speed_ft
    if isinstance(dest, str):
        dest = enc.waypoint(dest)
    if not enc.map.in_bounds(*dest):
        raise ActionError(f"目标 ({dest[0]},{dest[1]}) 超出地图")
    occ = _occupant(enc, dest[0], dest[1], exclude_id=combatant_id)
    if occ:
        raise ActionError(f"目标格 ({dest[0]},{dest[1]}) 被 {occ.actor.name} 占据")
    dx = (abs(dest[0] - c.x) + abs(dest[1] - c.y)) * enc.map.grid_size_ft
    if dx > speed:
        raise ActionError(f"冲刺移动 {dx}ft 超出速度 {speed}ft")
    enc.push_undo()  # 首个状态变更之前
    r = ResolutionResult(hp_before={combatant_id: c.hp})
    if not c.disengaged:
        foes = _aoo_candidates(enc, c)
    else:
        foes = []
    for step in _manhattan_path(enc, c, dest):
        c.x, c.y = step
        if not c.disengaged:
            for foe in list(foes):        # 副本迭代：remove 后迭代器不跳号（多敌场景）
                if enc.map.distance_ft(foe, c) > _melee_reach(foe):
                    sub = resolve_aoo(enc, c.id, foe.id, injected_d20=None)
                    r.lines.extend(sub.lines)
                    if sub.hp_after:
                        r.hp_after.update(sub.hp_after)
                    foes.remove(foe)
                    if not c.alive:
                        break
        if not c.alive:
            break
    r.add(f"{c.id} 冲刺 → ({dest[0]},{dest[1]})（动作已用）")
    c.acted = True
    c.movement_left_ft = 0
    enc.record(action="dash", combatant=combatant_id, dest=list(dest), lines=r.lines)
    return r


def resolve_dodge(enc, combatant_id: str) -> ResolutionResult:
    _guard_turn(enc, combatant_id)
    c = enc.combatants[combatant_id]
    if c.acted:
        raise ActionError(f"{c.id} 本回合已用动作")
    enc.push_undo()  # 首个状态变更之前
    r = ResolutionResult()
    r.add(f"{c.id} 闪避（直到下回合开始：攻击掷骰劣势、敏捷豁免优势）")
    c.dodging = True
    c.acted = True
    enc.record(action="dodge", combatant=combatant_id, lines=r.lines)
    return r


def resolve_disengage(enc, combatant_id: str) -> ResolutionResult:
    _guard_turn(enc, combatant_id)
    c = enc.combatants[combatant_id]
    if c.acted:
        raise ActionError(f"{c.id} 本回合已用动作")
    enc.push_undo()  # 首个状态变更之前
    r = ResolutionResult()
    r.add(f"{c.id} 脱离（本回合移动不触发借机攻击）")
    c.disengaged = True
    c.acted = True
    enc.record(action="disengage", combatant=combatant_id, lines=r.lines)
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
    """Death saving throw: 10+ success, 9- failure, 20 → 恢复 1 HP 并苏醒, 1 → 2 failures."""
    _guard_turn(enc, combatant_id)   # 死亡豁免在自己的回合做
    c = enc.combatants[combatant_id]
    if c.hp > 0:
        raise ActionError(f"{c.id} 尚未倒下")
    enc.push_undo()  # 首个状态变更之前
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
