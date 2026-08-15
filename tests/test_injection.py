"""Manual-dice injection: damage faces come from the player, not the server."""
import pytest
from battle.core import dice, resolution as R
from battle.core.models import ActionError, Actor, AttackSpec, Encounter

BOW = AttackSpec(name="短弓", kind="weapon", attack_bonus=5, range_ft=(80, 320),
                 damage="1d6+3", damage_type="穿刺")


def enc_factory():
    enc = Encounter(campaign="t", width=20, height=20)
    pc = Actor(name="星沢羽", kind="pc", ac=16, max_hp=17, speed_ft=30, dex_mod=2,
               attacks=[BOW])
    # 注：哥布林 max_hp=20（比 M1 测试更肉）——注入伤害 [4,2]=9 不会把它打穿到 0，
    # 精确断言 hp_after == hp_before - 9 才能成立
    gob = Actor(name="哥布林", kind="npc", ac=15, max_hp=20, speed_ft=30, dex_mod=1)
    cpc = enc.add_combatant(pc, x=0, y=0)
    cg = enc.add_combatant(gob, x=3, y=4)
    enc.roll_initiative()
    enc.start_combat()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    return enc, cpc, cg


def test_attack_injected_damage_faces():
    enc, cpc, cg = enc_factory()
    before = cg.hp
    r = R.resolve_attack(enc, cpc.id, cg.id, BOW, injected_d20=15,
                         injected_damage=[4, 2])
    assert r.ok
    # 4+2 骰面 + 静态修正 3 = 9 伤害
    assert cg.hp == before - 9
    line = "\n".join(r.lines)
    assert "注入" in line and "9" in line


def test_attack_injected_damage_crit_not_doubled():
    enc, cpc, cg = enc_factory()
    before = cg.hp
    r = R.resolve_attack(enc, cpc.id, cg.id, BOW, injected_d20=20,
                         injected_damage=[6])
    assert r.ok
    assert cg.hp == before - 9          # 6+3，注入时不做翻倍
    assert "暴击" in "\n".join(r.lines)
    assert "注入" in "\n".join(r.lines)


def test_attack_without_injection_unchanged():
    enc, cpc, cg = enc_factory()
    dice.seed(3)
    r = R.resolve_attack(enc, cpc.id, cg.id, BOW, injected_d20=15)
    line = "\n".join(r.lines)
    assert "注入" not in line            # 原路径不出现注入文案


def test_spell_save_injected_damage():
    enc, cpc, cg = enc_factory()
    before = cg.hp
    r = R.resolve_spell(enc, cpc.id, "燃烧之手", [cg.id],
                        dc=13, stat="dex", damage="3d6", damage_type="火焰",
                        injected_d20=10, injected_damage=[3, 3, 3])
    assert r.ok
    assert cg.hp == before - 9          # 3+3+3 骰面，无静态修正
    assert "注入" in "\n".join(r.lines)


def test_static_mod_helper():
    assert R._static_mod("1d6+3") == 3
    assert R._static_mod("2d6-1") == -1
    assert R._static_mod("3d8") == 0
