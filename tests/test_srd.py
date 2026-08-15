import pytest
from battle.core import srd


def test_find_monster_case_insensitive(monkeypatch):
    srd._SRD_CACHE = None
    fake = {"monsters": [{"name": "Goblin", "index": "goblin",
                          "cr": "1/4", "xp": 50, "ac": 15, "hp": 7,
                          "hp_dice": "2d6", "speed": "30 ft.",
                          "str": 8, "dex": 14, "con": 10, "int": 10,
                          "wis": 8, "cha": 8, "description": "x"}]}
    monkeypatch.setattr(srd, "_load_json", lambda: fake)
    m = srd.find_monster("goblin")
    assert m and m["name"] == "Goblin"
    assert srd.find_monster(" 哥布林 ") is None


def test_monster_to_actor_basic(monkeypatch):
    fake = {"monsters": [{"name": "Goblin", "index": "goblin",
                          "cr": "1/4", "xp": 50, "ac": 15, "hp": 7,
                          "hp_dice": "2d6", "speed": "30 ft., swim 30 ft.",
                          "str": 8, "dex": 14, "con": 10, "int": 10,
                          "wis": 8, "cha": 8, "description": "x"}]}
    monkeypatch.setattr(srd, "_load_json", lambda: fake)
    actor = srd.monster_to_actor(fake["monsters"][0])
    assert actor.ac == 15 and actor.max_hp == 7
    assert actor.dex_mod == 2 and actor.speed_ft == 30
    assert actor.kind == "npc"
