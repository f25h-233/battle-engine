import pytest
from battle.core import dice, resolution as R
from battle.core.models import ActionError, Actor, AttackSpec, Encounter

BOW = AttackSpec(name="短弓", kind="weapon", attack_bonus=5, range_ft=(80, 320),
                 damage="1d6+3", damage_type="穿刺")
DAGGER = AttackSpec(name="匕首", kind="weapon", attack_bonus=5, range_ft=(5, 0),
                    damage="1d4+3", damage_type="穿刺")


def enc_factory():
    enc = Encounter(campaign="t", width=20, height=20)
    pc = Actor(name="星沢羽", kind="pc", ac=16, max_hp=17, speed_ft=30, dex_mod=2,
               attacks=[BOW, DAGGER])
    gob = Actor(name="哥布林", kind="npc", ac=15, max_hp=7, speed_ft=30, dex_mod=1)
    cpc = enc.add_combatant(pc, x=0, y=0)
    cg1 = enc.add_combatant(gob, x=3, y=4)   # 25 ft away
    cg2 = enc.add_combatant(Actor(name="哥布林2", kind="npc", ac=15, max_hp=7,
                                  speed_ft=30, dex_mod=1), x=1, y=0)  # 5 ft away
    enc.roll_initiative()
    enc.start_combat()
    return enc, cpc, cg1, cg2


def test_attack_out_of_range_rejected():
    enc, cpc, cg1, cg2 = enc_factory()
    # 强制让 cpc 当前行动
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    with pytest.raises(ActionError, match="射程"):
        R.resolve_attack(enc, cpc.id, cg1.id, DAGGER)  # 匕首近战, 目标 25ft


def test_attack_hit_applies_damage():
    enc, cpc, cg1, cg2 = enc_factory()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    r = R.resolve_attack(enc, cpc.id, cg1.id, BOW, injected_d20=15)
    assert r.ok and r.errors == []
    assert r.hp_after[cg1.id] < r.hp_before[cg1.id]
    line = "\n".join(r.lines)
    assert "命中" in line and "d20" in line


def test_attack_crit_doubles_dice():
    enc, cpc, cg1, cg2 = enc_factory()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    dice.seed(7)
    # force: 匕首近战无法从 25ft 外攻击，本测试只关心暴击机制
    r = R.resolve_attack(enc, cpc.id, cg1.id, DAGGER, injected_d20=20, force=True)
    assert "暴击" in "\n".join(r.lines)
    # 暴击翻倍骰后伤害应大于单次骰+修正
    assert r.hp_after[cg1.id] <= r.hp_before[cg1.id] - 7  # 宽松区间（种子 7: 1d4=3,3 → 伤害 8）


def test_temp_hp_absorbed_first():
    enc, cpc, cg1, cg2 = enc_factory()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    cg1.temp_hp = 3
    dice.seed(7)
    before = cg1.hp
    r = R.resolve_attack(enc, cpc.id, cg1.id, BOW, injected_d20=15)
    # 种子 7 首个 1d6 = 3 → 伤害 3+3=6；临时 3 先被吸收，HP 只掉 6-3=3
    assert cg1.temp_hp == 0
    assert r.hp_after[cg1.id] == cg1.hp
    assert cg1.hp == before - 3


def test_drop_to_zero_triggers_unconscious_and_death_saves():
    enc, cpc, cg1, cg2 = enc_factory()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    cg2.hp = 1
    R.resolve_attack(enc, cpc.id, cg2.id, BOW, injected_d20=15, force=True)
    assert "unconscious" in cg2.conditions
    assert cg2.hp == 0


def test_death_save_success_and_failure():
    enc, cpc, cg1, cg2 = enc_factory()
    cg1.hp = 0
    r1 = R.resolve_death_save(enc, cg1.id, injected_d20=10)
    assert r1.ok and cg1.death_saves["successes"] == 1
    r2 = R.resolve_death_save(enc, cg1.id, injected_d20=5)
    assert cg1.death_saves["failures"] == 1
    for _ in range(2):
        R.resolve_death_save(enc, cg1.id, injected_d20=8)
    assert cg1.death_saves["failures"] == 3 and not cg1.alive
    assert cg1.death_saves.get("stable") is False  # 3 失败 = 死亡


def test_save_resolution():
    enc, cpc, cg1, cg2 = enc_factory()
    r = R.resolve_save(enc, cg1.id, dc=13, stat="con", injected_d20=10)
    assert r.ok and r.lines and "豁免" in r.lines[0]
    assert r.lines and ("成功" in r.lines[0] or "失败" in r.lines[0])


def test_move_beyond_speed_rejected():
    enc, cpc, cg1, cg2 = enc_factory()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    # 30 ft 速度 → 最多 6 格；目标 (9,0) 为 9 格 = 45ft
    with pytest.raises(ActionError, match="移动"):
        R.resolve_move(enc, cpc.id, (9, 0))


def test_ranged_attack_adjacent_enemy_disadvantage():
    enc, cpc, cg1, cg2 = enc_factory()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    adv = R.collect_advantage(enc, cpc.id, cg1.id, BOW, explicit=None)
    # cg2 在 5ft 内 → 远程攻击劣势
    assert not adv["advantage"] and adv["disadvantage"]
    assert any("近身" in reason for reason in adv["reasons"])
