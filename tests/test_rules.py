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
