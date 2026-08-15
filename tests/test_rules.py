"""M3 rules: dodge/disengage take real effect, not log-only placeholders."""
import pytest
from battle.core import resolution as R
from battle.core.models import Actor, AttackSpec, Encounter

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
    cg = enc.add_combatant(gob, x=1, y=0)   # 5 ft 相邻
    enc.roll_initiative()
    enc.start_combat()
    return enc, cpc, cg


def make_current(enc, cid):
    enc.turn_order.remove(cid)
    enc.turn_order.insert(0, cid)
    enc.turn_start(cid)


def test_dodge_sets_flag_and_clears_next_turn():
    enc, cpc, cg = enc_factory()
    make_current(enc, cpc.id)
    r = R.resolve_dodge(enc, cpc.id)
    assert r.ok and cpc.dodging and cpc.acted
    enc.next_turn()
    enc.next_turn()          # 绕回 星沢羽 的下一回合
    assert cpc.dodging is False


def test_dodge_imposes_disadvantage_on_attackers():
    enc, cpc, cg = enc_factory()
    make_current(enc, cpc.id)
    R.resolve_dodge(enc, cpc.id)          # 星沢羽 闪避中
    adv = R.collect_advantage(enc, cg.id, cpc.id, DAGGER, explicit=None)
    assert adv["disadvantage"] and not adv["advantage"]
    assert any("闪避" in x for x in adv["reasons"])


def test_dodge_gives_advantage_on_dex_saves():
    """5e: 闪避直到下回合开始，敏捷豁免也优势。"""
    enc, cpc, cg = enc_factory()
    make_current(enc, cpc.id)
    R.resolve_dodge(enc, cpc.id)
    enc.next_turn()                       # 回合让给 哥布林（施法者）
    assert enc.current().id == cg.id
    r = R.resolve_spell(enc, cg.id, "测试冲击", [cpc.id], dc=13, stat="dex",
                        damage="2d6", damage_type="力场")
    line = "\n".join(r.lines)
    assert "闪避中——敏捷豁免优势" in line        # 行为级断言：优势来源被记录
    # 对照：非 dex 豁免无优势字样
    enc.next_turn()                       # 回到 星沢羽
    cpc.dodging = True
    enc.next_turn()                       # 又轮到 哥布林
    r2 = R.resolve_spell(enc, cg.id, "测试冲击2", [cpc.id], dc=13, stat="con",
                         damage="2d6", damage_type="力场")
    assert "闪避中——敏捷豁免优势" not in "\n".join(r2.lines)


def test_disengage_sets_flag():
    enc, cpc, cg = enc_factory()
    make_current(enc, cpc.id)
    r = R.resolve_disengage(enc, cpc.id)
    assert r.ok and cpc.disengaged and cpc.acted


def test_disengage_clears_next_turn():
    enc, cpc, cg = enc_factory()
    make_current(enc, cpc.id)
    R.resolve_disengage(enc, cpc.id)
    enc.next_turn()
    enc.next_turn()
    assert cpc.disengaged is False


def test_serialization_roundtrip():
    import json
    enc, cpc, cg = enc_factory()
    cpc.dodging = True
    cpc.disengaged = True
    enc2 = Encounter.from_dict(json.loads(json.dumps(enc.to_dict())))
    assert enc2.combatants[cpc.id].dodging is True
    assert enc2.combatants[cpc.id].disengaged is True


# ── 借机攻击（AoO）──────────────────────────────────────────────

def make_adjacent_factory():
    """pc 与 gob 相邻（5ft），gob 持匕首近战。"""
    enc = Encounter(campaign="aoo", width=20, height=20)
    pc = Actor(name="星沢羽", kind="pc", ac=16, max_hp=17, speed_ft=30, dex_mod=2)
    gob = Actor(name="哥布林", kind="npc", ac=15, max_hp=7, speed_ft=30, dex_mod=1,
                attacks=[DAGGER])
    cpc = enc.add_combatant(pc, x=3, y=3)
    cg = enc.add_combatant(gob, x=4, y=3)
    enc.roll_initiative()
    enc.start_combat()
    return enc, cpc, cg


def test_manhattan_path_steps():
    enc, cpc, cg = make_adjacent_factory()
    steps = R._manhattan_path(enc, cpc, (3, 6))
    assert steps == [(3, 4), (3, 5), (3, 6)]     # 每步 1 格，含终点


def test_move_out_of_reach_triggers_aoo():
    enc, cpc, cg = make_adjacent_factory()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    hp0 = cpc.hp
    r = R.resolve_move(enc, cpc.id, (3, 6))       # 从 gob 邻格离开 15ft
    lines = "\n".join(r.lines)
    assert "借机攻击" in lines
    assert cg.reaction_used is True               # 哥布林反应被消耗
    assert cpc.hp <= hp0                          # 机会攻击可能造成伤害
    assert (cpc.x, cpc.y) == (3, 6)               # 移动未被打断（5e）


def test_aoo_damage_deterministic_direct_call():
    enc, cpc, cg = make_adjacent_factory()
    hp0 = cpc.hp
    r = R.resolve_aoo(enc, cpc.id, cg.id, DAGGER, injected_d20=15)
    assert r.ok and cg.reaction_used is True
    # d20(15)+5=20 ≥ AC16 命中；伤害 1d4+3 随机但 ≤ 7
    assert cpc.hp <= hp0 and "借机攻击" in "\n".join(r.lines)


def test_aoo_miss_no_damage():
    enc, cpc, cg = make_adjacent_factory()
    hp0 = cpc.hp
    r = R.resolve_aoo(enc, cpc.id, cg.id, DAGGER, injected_d20=1)
    assert "未命中" in "\n".join(r.lines)   # d20(1)+5=6 < AC16 → miss
    assert cpc.hp == hp0                    # miss 不掉血


def test_move_within_reach_no_aoo():
    enc, cpc, cg = make_adjacent_factory()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    r = R.resolve_move(enc, cpc.id, (5, 3))       # 横向移动仍相邻——不离开范围
    assert "借机攻击" not in "\n".join(r.lines)
    assert cg.reaction_used is False


def test_disengage_prevents_aoo():
    enc, cpc, cg = make_adjacent_factory()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    R.resolve_disengage(enc, cpc.id)
    cpc.acted = False   # 引擎简化：脱离消耗动作；本测试只验证"脱离状态下移动不触发 AoO"
    r = R.resolve_move(enc, cpc.id, (3, 6))
    assert "借机攻击" not in "\n".join(r.lines)
    assert cg.reaction_used is False


def test_dash_triggers_aoo():
    enc, cpc, cg = make_adjacent_factory()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    r = R.resolve_dash(enc, cpc.id, (3, 8))       # 冲刺离开
    assert "借机攻击" in "\n".join(r.lines)
    assert cg.reaction_used is True


def test_move_multiple_steps_single_aoo():
    """Step 1 补记：替换原 test_aoo_only_once_per_enemy_per_move——
    直线曼哈顿路径无法"进出再进出"，原版退化为方向性断言（且 (5,5)→(4,5)
    实际会离开 gob 范围触发 AoO，断言必挂）。此测试用 5 步长移验证
    "一次移动中每个敌人最多触发一次 AoO"（foes.remove 语义）。"""
    enc = Encounter(campaign="aoo2", width=20, height=20)
    pc = Actor(name="星沢羽", kind="pc", ac=16, max_hp=30, speed_ft=30, dex_mod=2)
    gob = Actor(name="哥布林", kind="npc", ac=15, max_hp=7, speed_ft=30, dex_mod=1,
                attacks=[DAGGER])
    cpc = enc.add_combatant(pc, x=3, y=3)
    cg = enc.add_combatant(gob, x=4, y=3)
    enc.roll_initiative()
    enc.start_combat()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    r = R.resolve_move(enc, cpc.id, (3, 8))       # 走 5 步垂直离开 gob 范围
    assert "\n".join(r.lines).count("借机攻击") == 1
    assert cg.reaction_used is True


def test_two_enemies_both_trigger_aoo():
    """双敌夹击：同一格步同时离开两个敌人的近战范围 → 各触发一次。
    两敌分列左右（A(2,3)、B(4,3)），pc 单步移到 (3,4) 同时离开两者——
    路径终点即离开步。修复前 for foe in foes 迭代中 remove 会跳过
    被删元素之后的下一个（此处为 B），B 永不出手；list(foes) 副本修复。
    （reviewer 原几何 A(4,3)、B(3,4)→(3,6) 实测修复前也触发 2 次——
    B 只是晚一步触发，抓不到该 bug，故改用本几何。）"""
    enc = Encounter(campaign="aoo3", width=20, height=20)
    pc = Actor(name="星沢羽", kind="pc", ac=16, max_hp=30, speed_ft=30, dex_mod=2)
    gobA = Actor(name="哥布林A", kind="npc", ac=15, max_hp=7, speed_ft=30, dex_mod=1,
                 attacks=[DAGGER])
    gobB = Actor(name="哥布林B", kind="npc", ac=15, max_hp=7, speed_ft=30, dex_mod=1,
                 attacks=[DAGGER])
    cpc = enc.add_combatant(pc, x=3, y=3)
    ca = enc.add_combatant(gobA, x=2, y=3)
    cb = enc.add_combatant(gobB, x=4, y=3)
    enc.roll_initiative()
    enc.start_combat()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    r = R.resolve_move(enc, cpc.id, (3, 4))
    lines = "\n".join(r.lines)
    assert lines.count("借机攻击") == 2
    assert ca.reaction_used is True
    assert cb.reaction_used is True
