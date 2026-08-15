from battle.core import monster_parser as mp
from battle.core.models import AttackSpec

GOBLIN_DESC = (
    "Action ◆ Scimitar: Melee Weapon Attack: +4 to hit, reach 5 ft., "
    "one target. Hit: 5 (1d6 + 2) slashing damage.\n\n"
    "Action ◆ Shortbow: Ranged Weapon Attack: +4 to hit, range 80/320 ft., "
    "one target. Hit: 5 (1d6 + 2) piercing damage."
)

ABOLETH_DESC = (
    "Action ◆ Tentacle: Melee Weapon Attack: +9 to hit, reach 10 ft., one target. "
    "Hit: 12 (2d6 + 5) bludgeoning damage. If the target is a creature, it must "
    "succeed on a DC 14 Constitution saving throw or become diseased."
)


def test_parse_melee_attack():
    atks = mp.parse_monster_actions({"name": "Goblin", "description": GOBLIN_DESC})
    scim = [a for a in atks if a.name == "Scimitar"][0]
    assert scim.kind == "weapon" and scim.attack_bonus == 4
    assert scim.range_ft == (5, 0)
    assert scim.damage == "1d6+2" and scim.damage_type == "slashing"


def test_parse_ranged_range():
    atks = mp.parse_monster_actions({"name": "Goblin", "description": GOBLIN_DESC})
    bow = [a for a in atks if a.name == "Shortbow"][0]
    assert bow.range_ft == (80, 320)
    assert bow.long_range_ft() == 320


def test_parse_save_attached():
    atks = mp.parse_monster_actions({"name": "Aboleth", "description": ABOLETH_DESC})
    tent = atks[0]
    assert tent.save_dc == 14 and tent.save_stat == "constitution"
    assert "diseased" in tent.note


def test_unparseable_falls_back_to_special():
    desc = "Action ◆ Weird Ritual: The aboleth does something strange."
    atks = mp.parse_monster_actions({"name": "X", "description": desc})
    assert len(atks) == 1
    assert atks[0].kind == "special" and atks[0].attack_bonus is None
    assert "strange" in atks[0].note
