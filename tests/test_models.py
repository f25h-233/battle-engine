import pytest
from battle.core.models import ActionError, Actor, AttackSpec, Combatant, Encounter, GridMap


def make_encounter(width=10, height=10):
    enc = Encounter(campaign="test", width=width, height=height)
    a = Actor(name="甲", kind="pc", ac=15, max_hp=20, speed_ft=30, dex_mod=2,
              attacks=[AttackSpec(name="短剑", attack_bonus=4, range_ft=(5, 0),
                                  damage="1d6+2", damage_type="穿刺")])
    b = Actor(name="哥布林", kind="npc", ac=15, max_hp=7, speed_ft=30, dex_mod=1)
    return enc, a, b


def test_grid_distance():
    enc, a, b = make_encounter()
    ca = enc.add_combatant(a, x=0, y=0)
    cb = enc.add_combatant(b, x=3, y=4)   # 3-4-5 三角形
    assert enc.map.distance_ft(ca, cb) == 25
    assert enc.map.in_range(ca, cb, 25)
    assert not enc.map.in_range(ca, cb, 20)


def test_add_combatant_assigns_id_and_position():
    enc, a, _ = make_encounter()
    c = enc.add_combatant(a, x=1, y=2)
    assert c.id == "甲"
    assert c.hp == 20 and c.max_hp == 20
    assert enc.combatants["甲"] is c


def test_attack_before_init_rejected():
    enc, a, b = make_encounter()
    ca = enc.add_combatant(a, x=0, y=0)
    cb = enc.add_combatant(b, x=1, y=0)
    with pytest.raises(ActionError, match="先攻"):
        enc.assert_turn(ca.id)


def test_turn_lifecycle_resets_resources():
    enc, a, b = make_encounter()
    ca = enc.add_combatant(a, x=0, y=0)
    cb = enc.add_combatant(b, x=1, y=0)
    enc.roll_initiative()
    enc.start_combat()
    cur = enc.current()
    assert cur.movement_left_ft == 30
    cur.acted = True
    cur.movement_left_ft = 5
    enc.next_turn()
    nxt = enc.current()
    assert nxt.id != cur.id
    assert not nxt.acted and nxt.movement_left_ft == 30


def test_round_wraps():
    enc, a, b = make_encounter()
    ca = enc.add_combatant(a, x=0, y=0)
    cb = enc.add_combatant(b, x=1, y=0)
    enc.roll_initiative()
    enc.start_combat()
    first = enc.current().id
    enc.next_turn()
    enc.next_turn()   # 两人战斗绕回
    assert enc.round == 2
    assert enc.current().id == first


def test_turn_gate_active_combat():
    enc, a, b = make_encounter()
    ca = enc.add_combatant(a, x=0, y=0)
    cb = enc.add_combatant(b, x=1, y=0)
    enc.roll_initiative()
    enc.start_combat()
    cur = enc.current()
    enc.assert_turn(cur.id)                     # 本回合标准动作通过
    other = cb.id if cur.id == ca.id else ca.id
    with pytest.raises(ActionError, match="回合"):
        enc.assert_turn(other)                  # 非本回合被拒
    enc.assert_turn(other, reaction=True)       # 反应豁免通过
    enc.assert_turn(other, legendary=True)      # 传说豁免通过
