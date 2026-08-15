import json
import pytest
from battle.core import persistence as P
from battle.core.models import Actor, Encounter


def make_enc(tmp_path):
    enc = Encounter(campaign="t", width=8, height=8)
    enc.add_combatant(Actor(name="甲", kind="pc", ac=15, max_hp=20, speed_ft=30, dex_mod=2), x=0, y=0)
    return enc


def test_roundtrip(tmp_path):
    enc = make_enc(tmp_path)
    enc.roll_initiative()
    P.save_encounter(enc, str(tmp_path))
    loaded = P.load_encounter(str(tmp_path))
    assert loaded is not None
    assert loaded.status == enc.status
    assert loaded.turn_order == enc.turn_order
    assert loaded.combatants["甲"].hp == 20
    assert loaded.map.width == 8


def test_missing_file_returns_none(tmp_path):
    assert P.load_encounter(str(tmp_path)) is None


def test_corrupt_file_raises_with_backup_hint(tmp_path):
    P.save_encounter(make_enc(tmp_path), str(tmp_path))
    path = P.battle_path(str(tmp_path))
    path.write_text("{ broken json", encoding="utf-8")
    with pytest.raises(Exception, match="损坏|backup|bak"):
        P.load_encounter(str(tmp_path))


def test_backup_created_and_restorable(tmp_path):
    enc = make_enc(tmp_path)
    P.save_encounter(enc, str(tmp_path))
    enc.combatants["甲"].hp = 5
    P.save_encounter(enc, str(tmp_path))
    bak = P.battle_path(str(tmp_path)).with_suffix(".json.bak")
    assert bak.exists()
    restored = P.restore_backup(str(tmp_path))
    assert restored.combatants["甲"].hp == 20
