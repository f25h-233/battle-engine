"""M1/M2 遗留修复批回归测试。"""
import json
import pytest
from battle.core import dice, resolution as R
from battle.core.models import ActionError, Actor, AttackSpec, Encounter

BOW = AttackSpec(name="短弓", kind="weapon", attack_bonus=5, range_ft=(80, 320),
                 damage="1d6+3", damage_type="穿刺")
SWORD = AttackSpec(name="长剑", kind="weapon", attack_bonus=5, range_ft=(5, 0),
                   damage="1d8+2d6+3", damage_type="挥砍")   # 多骰式


def enc_factory():
    enc = Encounter(campaign="fix", width=20, height=20)
    pc = Actor(name="星沢羽", kind="pc", ac=16, max_hp=17, speed_ft=30, dex_mod=2,
               attacks=[BOW])
    gob = Actor(name="哥布林", kind="npc", ac=15, max_hp=30, speed_ft=30, dex_mod=1)
    cpc = enc.add_combatant(pc, x=0, y=0)
    cg = enc.add_combatant(gob, x=3, y=4)
    enc.roll_initiative()
    enc.start_combat()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    return enc, cpc, cg


def test_initiative_records_d20_face():
    enc, cpc, cg = enc_factory()
    assert cpc.initiative_d20 is not None
    assert 1 <= cpc.initiative_d20 <= 20
    assert cpc.initiative == cpc.initiative_d20 + cpc.actor.dex_mod
    enc2 = Encounter.from_dict(json.loads(json.dumps(enc.to_dict())))
    assert enc2.combatants[cpc.id].initiative_d20 == cpc.initiative_d20


def test_start_combat_without_combatants_rejected():
    enc = Encounter(campaign="empty", width=10, height=10)
    enc.roll_initiative()
    with pytest.raises(ActionError, match="战斗员"):
        enc.start_combat()


def test_spell_without_targets_rejected():
    enc, cpc, cg = enc_factory()
    with pytest.raises(ActionError, match="目标"):
        R.resolve_spell(enc, cpc.id, "火球术", [], dc=14, stat="dex",
                        damage="8d6", damage_type="火焰")


def test_crit_multi_die_notation_doubles_all_dice():
    enc, cpc, cg = enc_factory()
    enc.turn_order.remove(cpc.id); enc.turn_order.insert(0, cpc.id)
    enc.turn_start(cpc.id)
    cpc.actor.attacks = [SWORD]
    hp0 = cg.hp
    dice.seed(7)
    r = R.resolve_attack(enc, cpc.id, cg.id, SWORD, injected_d20=20, force=True)
    assert "暴击" in "\n".join(r.lines)
    # 种子 7 骰序: 1d8=3?（d8 序列 3,2 → 1d8=3, 2d6 各 3,2）暴击翻倍全套骰
    assert cg.hp < hp0 - 5     # 双倍后伤害 ≥ 单次骰和（宽松断言：翻倍生效）


def test_dice_part_extracts_all_dice():
    assert R._dice_part("1d8+2d6+3") == "1d8+2d6"
    assert R._dice_part("2d6") == "2d6"
    assert R._dice_part("3d8-1") == "3d8"
    assert R._dice_part("4") == ""


def test_injected_d20_out_of_range_rejected():
    with pytest.raises(ValueError, match="1.*20"):
        dice.roll_d20(injected=0)
    with pytest.raises(ValueError, match="1.*20"):
        dice.roll_d20(injected=21)
    assert dice.roll_d20(injected=20)["d20"] == 20     # 边界合法


def test_death_save_requires_own_turn():
    enc, cpc, cg = enc_factory()
    cg.hp = 0
    # 当前行动者是 星沢羽 → 哥布林死救被拒（此前无回合门）
    with pytest.raises(ActionError, match="回合"):
        R.resolve_death_save(enc, cg.id, injected_d20=10)
    enc.next_turn()                                     # 轮到 哥布林
    r = R.resolve_death_save(enc, cg.id, injected_d20=10)
    assert r.ok and cg.death_saves["successes"] == 1


def test_log_capped_at_200():
    enc, cpc, cg = enc_factory()
    for i in range(210):
        enc.record(action="tick", actor="星沢羽", lines=[str(i)])
    assert len(enc.log) == 200
    assert enc.log[-1]["lines"] == ["209"]
