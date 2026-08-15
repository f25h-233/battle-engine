"""End-to-end: create → add → init → start → fight → end, all gates enforced."""
import pytest
from battle.core import dice, resolution as R
from battle.core.models import ActionError, Actor, AttackSpec, Encounter

BOW = AttackSpec(name="短弓", attack_bonus=5, range_ft=(80, 320), damage="1d6+3", damage_type="穿刺")
SWORD = AttackSpec(name="弯刀", attack_bonus=4, range_ft=(5, 0), damage="1d6+2", damage_type="挥砍")


def test_full_encounter_flow():
    enc = Encounter(campaign="flow", width=10, height=10)
    pc = Actor(name="星沢羽", kind="pc", ac=16, max_hp=17, speed_ft=30, dex_mod=2, attacks=[BOW])
    gob = Actor(name="哥布林", kind="npc", ac=15, max_hp=7, speed_ft=30, dex_mod=1, attacks=[SWORD])
    cpc = enc.add_combatant(pc, x=0, y=0)
    cg = enc.add_combatant(gob, x=4, y=0)

    # 1. 未掷先攻 → 攻击被拒
    with pytest.raises(ActionError, match="先攻"):
        R.resolve_attack(enc, cpc.id, cg.id, BOW, injected_d20=15, force=True)

    # 2. 掷先攻并开战
    enc.roll_initiative()
    assert enc.status == "initiative_rolled"
    enc.start_combat()
    assert enc.status == "combat_active" and enc.round == 1
    first = enc.current()

    # 3. 非当前行动者攻击 → 被拒
    other = cg.id if first.id == cpc.id else cpc.id
    with pytest.raises(ActionError, match="回合"):
        R.resolve_attack(enc, other, first.id, SWORD, injected_d20=15, force=True)

    # 4. 当前行动者攻击（注入骰子）
    dice.seed(3)
    r = R.resolve_attack(enc, first.id, other, BOW, injected_d20=18)
    assert r.ok
    tgt = enc.combatants[other]
    assert tgt.hp < tgt.actor.max_hp

    # 5. 反击：轮到对方时（推进到对方回合后普通攻击 + undo 验证）
    enc.next_turn()  # 推进到对方回合
    r2 = R.resolve_attack(enc, other, first.id, SWORD, injected_d20=15, force=True)
    assert r2.ok
    # 用 force=True 走射程门之外（弯刀近战 5ft，双方相距 20ft），验证 undo 存在
    hp_before = tgt.hp
    enc.push_undo()
    R.apply_damage(enc, other, 3)
    assert enc.combatants[other].hp == max(0, hp_before - 3)  # HP 下限 0
    assert enc.pop_undo()
    assert enc.combatants[other].hp == hp_before

    # 6. 打穿 0 HP → 昏迷 + 死亡豁免流程
    R.apply_damage(enc, other, 999)
    assert enc.combatants[other].hp == 0
    assert "unconscious" in enc.combatants[other].conditions
    R.resolve_death_save(enc, other, injected_d20=9)
    R.resolve_death_save(enc, other, injected_d20=9)
    R.resolve_death_save(enc, other, injected_d20=9)
    assert enc.combatants[other].death_saves["failures"] == 3

    # 7. 回合推进与绕回
    n = len(enc.turn_order)
    for _ in range(n):
        enc.next_turn()
    assert enc.round == 2

    # 8. 结束
    enc.end()
    assert enc.status == "ended"
    assert enc.log  # 有动作日志（叙事素材）


def test_ranged_attack_range_gate_mid_fight():
    enc = Encounter(campaign="flow2", width=10, height=10)
    pc = Actor(name="甲", kind="pc", ac=16, max_hp=17, speed_ft=30, dex_mod=2, attacks=[BOW])
    gob = Actor(name="乙", kind="npc", ac=15, max_hp=7, speed_ft=30, dex_mod=1)
    cpc = enc.add_combatant(pc, x=0, y=0)
    cg = enc.add_combatant(gob, x=9, y=0)   # 45 ft —— 短弓 80ft 内，合法
    enc.roll_initiative(); enc.start_combat()
    if enc.current().id != cpc.id:
        enc.next_turn()
    r = R.resolve_attack(enc, cpc.id, cg.id, BOW, injected_d20=12)
    assert r.ok and r.hp_after[cg.id] < r.hp_before[cg.id]
