"""M3 AoE: radius geometry, per-target saves, half damage on success."""
import pytest
from battle.core import dice, resolution as R, srd
from battle.core.models import ActionError, Actor, AttackSpec, Encounter

FIREBALL = AttackSpec(name="火球术", kind="spell", save_dc=14, save_stat="dex",
                      damage="8d6", damage_type="火焰", range_ft=(150, 0),
                      aoe_radius_ft=20)


def enc_factory():
    enc = Encounter(campaign="aoe", width=20, height=20)
    pc = Actor(name="法师", kind="pc", ac=13, max_hp=20, speed_ft=30, dex_mod=2,
               attacks=[FIREBALL])
    g1 = Actor(name="哥布林1", kind="npc", ac=15, max_hp=7, speed_ft=30, dex_mod=1)
    g2 = Actor(name="哥布林2", kind="npc", ac=15, max_hp=7, speed_ft=30, dex_mod=1)
    g3 = Actor(name="哥布林3", kind="npc", ac=15, max_hp=7, speed_ft=30, dex_mod=1)
    cpc = enc.add_combatant(pc, x=0, y=0)
    c1 = enc.add_combatant(g1, x=3, y=0)    # 15ft
    c2 = enc.add_combatant(g2, x=0, y=3)    # 15ft
    c3 = enc.add_combatant(g3, x=10, y=10)  # 70ft —— 范围外
    enc.roll_initiative()
    enc.start_combat()
    return enc, cpc, c1, c2, c3


def make_current(enc, cid):
    enc.turn_order.remove(cid)
    enc.turn_order.insert(0, cid)
    enc.turn_start(cid)


def test_cells_in_radius_geometry():
    enc, *_ = enc_factory()
    cells = enc.cells_in_radius(0, 0, 20)
    assert (0, 0) in cells and (3, 0) in cells and (0, 3) in cells
    # 3格*5=15, 15*sqrt(2)≈21.2 > 20 —— 不在
    assert (3, 3) not in cells
    # 20ft 边界：4格=20 ≤ 20 —— 在
    assert (4, 0) in cells


def test_aoe_targets_each_creature_in_radius():
    enc, cpc, c1, c2, c3 = enc_factory()
    make_current(enc, cpc.id)
    r = R.resolve_spell(enc, cpc.id, "火球术", [], center=(0, 0), radius_ft=20,
                        dc=14, stat="dex", damage="8d6", damage_type="火焰")
    assert r.ok
    assert c1.hp < 7 or c1.death_saves["failures"] > 0    # c1 在 15ft 内被打
    assert c2.hp < 7 or c2.death_saves["failures"] > 0
    assert c3.hp == 7                                     # 70ft 外毫发无损
    line = "\n".join(r.lines)
    assert "豁免" in line and "哥布林1" in line and "哥布林2" in line


def test_aoe_half_damage_on_successful_save():
    enc, cpc, c1, c2, c3 = enc_factory()
    make_current(enc, cpc.id)
    dice.seed(11)
    # 豁免注入：全成功（d20=20 必过 DC14）→ 全半伤
    c1.hp = 20; c1.actor = Actor(name="肉盾1", kind="npc", ac=15, max_hp=20,
                                 speed_ft=30, dex_mod=2)
    c2.hp = 20; c2.actor = Actor(name="肉盾2", kind="npc", ac=15, max_hp=20,
                                 speed_ft=30, dex_mod=2)
    r = R.resolve_spell(enc, cpc.id, "火球术", [], center=(0, 0), radius_ft=20,
                        dc=10, stat="dex", damage="4d6", damage_type="火焰",
                        injected_d20=20)
    # 全成功 → 伤害 = 4d6 骰和 // 2（半伤向下取整）
    assert "半伤" in "\n".join(r.lines)
    assert c1.hp > 0 and c1.hp < 20     # 有伤害但未全吃
    assert c1.hp == c2.hp               # 两人同骰同豁免 → 同伤害


def test_aoe_no_creatures_in_radius_rejected():
    enc, cpc, c1, c2, c3 = enc_factory()
    make_current(enc, cpc.id)
    with pytest.raises(ActionError, match="目标"):
        R.resolve_spell(enc, cpc.id, "火球术", [], center=(15, 15), radius_ft=10,
                        dc=14, stat="dex", damage="8d6", damage_type="火焰")


def test_aoe_caster_hit_by_own_fireball():
    enc, cpc, c1, c2, c3 = enc_factory()
    make_current(enc, cpc.id)
    cpc.hp = 30
    dice.seed(3)
    r = R.resolve_spell(enc, cpc.id, "火球术", [], center=(0, 0), radius_ft=20,
                        dc=14, stat="dex", damage="8d6", damage_type="火焰")
    # 施法者自己也在覆盖格内（几何覆盖，5e 火球炸自己）
    assert "法师" in "\n".join(r.lines)
    assert cpc.hp < 30


def test_attack_spec_aoe_radius_serialized():
    import json
    enc = Encounter(campaign="t", width=10, height=10)
    a = Actor(name="法师", kind="pc", ac=13, max_hp=20, speed_ft=30, dex_mod=2,
              attacks=[FIREBALL])
    enc.add_combatant(a, x=0, y=0)
    d = json.loads(json.dumps(enc.to_dict()))
    assert d["combatants"]["法师"]["actor"]["attacks"][0]["aoe_radius_ft"] == 20
    enc2 = Encounter.from_dict(d)
    assert enc2.combatants["法师"].actor.attacks[0].aoe_radius_ft == 20


def test_spell_aoe_radius_from_description():
    assert srd.spell_aoe_radius({"description": "Each creature in a 20-foot-radius sphere..."}) == 20
    assert srd.spell_aoe_radius({"description": "15-foot cone originating from you"}) is None
    assert srd.spell_aoe_radius({}) is None
