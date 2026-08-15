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


def test_consecutive_undo_rolls_back_each_action():
    """Critical: undo 可连续回滚——restore 不得清空历史栈（此前弹顶后栈被清空）。"""
    enc, cpc, cg1, cg2 = enc_factory()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    hp0 = cg1.hp
    R.resolve_attack(enc, cpc.id, cg1.id, BOW, injected_d20=15)
    assert cg1.hp < hp0
    for _ in range(len(enc.turn_order)):  # 推进一整圈 → 回到星沢羽且 acted 已重置
        enc.next_turn()
    R.resolve_attack(enc, cpc.id, cg2.id, BOW, injected_d20=15)
    assert cg2.hp < cg2.actor.max_hp
    assert enc.pop_undo()
    # restore 重建 combatants 对象 → 必须按 id 重新取（旧引用指向被替换的对象）
    assert enc.combatants[cg2.id].hp == cg2.actor.max_hp  # 第二次攻击被回滚
    assert enc.pop_undo()
    assert enc.combatants[cg1.id].hp == hp0               # 历史栈保留 → 第一次也可回滚


def test_second_attack_same_turn_rejected():
    """Important: 攻击必须消耗标准动作——同回合第二次攻击被拒（此前不置 acted）。"""
    enc, cpc, cg1, cg2 = enc_factory()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    r = R.resolve_attack(enc, cpc.id, cg1.id, BOW, injected_d20=15)
    assert r.ok and cpc.acted  # 首次攻击成功且消耗动作
    with pytest.raises(ActionError, match="动作"):
        R.resolve_attack(enc, cpc.id, cg2.id, BOW, injected_d20=15)


def test_miss_sets_hp_after():
    """Important: miss 时 hp_after 破损——此前只在命中分支赋值，miss 后取键 KeyError。"""
    enc, cpc, cg1, cg2 = enc_factory()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    r = R.resolve_attack(enc, cpc.id, cg1.id, BOW, injected_d20=1)
    assert "未命中" in "\n".join(r.lines)
    assert r.hp_after[cg1.id] == cg1.hp == r.hp_before[cg1.id]


def test_dash_out_of_bounds_rejected():
    """Important: resolve_dash 缺 in_bounds 检查（与 resolve_move 对齐）。"""
    enc, cpc, cg1, cg2 = enc_factory()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    with pytest.raises(ActionError, match="超出地图"):
        R.resolve_dash(enc, cpc.id, (50, 50))


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
    assert r.hp_after[cg1.id] <= r.hp_before[cg1.id] - 7  # 种子 7: 1d4 序列 3,2 → 暴击伤害 (3+3)+2=8


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


def test_death_save_nat20_recovers_hp():
    enc, cpc, cg1, cg2 = enc_factory()
    cg1.hp = 0
    cg1.conditions.append("unconscious")
    R.resolve_death_save(enc, cg1.id, injected_d20=20)
    assert cg1.hp == 1
    assert "unconscious" not in cg1.conditions
    assert cg1.death_saves["failures"] == 0


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


def test_move_into_occupied_rejected():
    enc, cpc, cg1, cg2 = enc_factory()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    # (1,0) 被 cg2 占据，5ft 在移动预算内 → 是占据而非超速拒绝
    with pytest.raises(ActionError, match="占据"):
        R.resolve_move(enc, cpc.id, (1, 0))


def test_dash_into_occupied_rejected():
    enc, cpc, cg1, cg2 = enc_factory()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    # (1,0) 被 cg2 占据，5ft 在冲刺速度内 → 是占据而非超速拒绝
    with pytest.raises(ActionError, match="占据"):
        R.resolve_dash(enc, cpc.id, (1, 0))


def test_ranged_attack_adjacent_enemy_disadvantage():
    enc, cpc, cg1, cg2 = enc_factory()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    adv = R.collect_advantage(enc, cpc.id, cg1.id, BOW, explicit=None)
    # cg2 在 5ft 内 → 远程攻击劣势
    assert not adv["advantage"] and adv["disadvantage"]
    assert any("近身" in reason for reason in adv["reasons"])
