"""Web layer tests: blueprint mounting, campaign resolution, state payload contract."""
import pytest
from flask import Flask

from battle.core import persistence as P
from battle.core.models import Actor, AttackSpec, Encounter
from battle.web import bp


def make_encounter(tmp_path, campaign="t", *, width=10, height=10):
    """Encounter in combat_active with 星沢羽 acting first, saved to disk."""
    camp_dir = tmp_path / "campaigns" / campaign
    enc = Encounter(campaign=campaign, width=width, height=height)
    pc = Actor(name="星沢羽", kind="pc", ac=16, max_hp=17, speed_ft=30, dex_mod=2,
               attacks=[AttackSpec(name="短弓", kind="weapon", attack_bonus=5,
                                   range_ft=(80, 320), damage="1d6+3", damage_type="穿刺")])
    gob = Actor(name="哥布林", kind="npc", ac=15, max_hp=7, speed_ft=30, dex_mod=1)
    enc.add_combatant(pc, x=0, y=0)
    enc.add_combatant(gob, x=3, y=4)
    enc.add_waypoint("门口", (2, 2))
    enc.roll_initiative()
    enc.start_combat()
    enc.turn_order.remove("星沢羽")          # 强制 星沢羽 先手，测试可控
    enc.turn_order.insert(0, "星沢羽")
    enc.turn_start("星沢羽")
    P.save_encounter(enc, camp_dir)
    return enc


def make_client(tmp_path, monkeypatch, *, campaign="t", token=None):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    app = Flask(__name__)
    app.register_blueprint(bp.battle_bp)
    if token:
        app.config["BATTLE_TOKEN"] = token
    app.config["BATTLE_CAMPAIGN_FILE"] = str(tmp_path / ".campaign")
    (tmp_path / ".campaign").write_text(campaign, encoding="utf-8")
    app.config["TESTING"] = True
    return app.test_client()


def test_state_payload_contract(tmp_path, monkeypatch):
    make_encounter(tmp_path)
    client = make_client(tmp_path, monkeypatch)
    r = client.get("/battle/state")
    assert r.status_code == 200
    st = r.get_json()["state"]
    assert st["campaign"] == "t" and st["status"] == "combat_active"
    assert st["turn_order"][0] == "星沢羽"
    assert "undo_stack" not in st                     # 重型嵌套快照不进 payload
    assert len(st["log"]) <= bp.LOG_LIMIT
    c = st["combatants"]["星沢羽"]
    assert c["name"] == "星沢羽" and c["max_hp"] == 17 and c["hp"] == 17
    assert c["ac"] == 16 and c["speed_ft"] == 30
    assert c["attacks"][0]["name"] == "短弓"
    assert c["attacks"][0]["range_ft"] == [80, 320]
    assert st["waypoints"]["门口"] == [2, 2]
    assert st["map"]["width"] == 10


def test_state_endpoint_without_battle_file(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, campaign="empty")
    r = client.get("/battle/state")
    assert r.status_code == 404
    assert "没有战斗" in r.get_json()["error"]


def test_state_endpoint_without_campaign(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    app = Flask(__name__)                             # 无 BATTLE_CAMPAIGN_FILE、无 env
    app.register_blueprint(bp.battle_bp)
    app.config["TESTING"] = True
    r = app.test_client().get("/battle/state")
    assert r.status_code == 404
    assert "未设置战役" in r.get_json()["error"]


def test_campaign_env_override(tmp_path, monkeypatch):
    make_encounter(tmp_path, campaign="other")
    monkeypatch.setenv("BATTLE_CAMPAIGN", "other")
    client = make_client(tmp_path, monkeypatch, campaign="ignored")  # 文件里写 ignored
    r = client.get("/battle/state")
    assert r.status_code == 200
    assert r.get_json()["state"]["campaign"] == "other"


def test_index_renders_panel(tmp_path, monkeypatch, token="s3cret"):
    make_encounter(tmp_path)
    client = make_client(tmp_path, monkeypatch, token=token)
    r = client.get("/battle/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "战斗面板" in html or "战斗引擎" in html
    assert f'name="dnd-token" content="{token}"' in html


def test_post_requires_token_in_lan_mode(tmp_path, monkeypatch):
    make_encounter(tmp_path)
    client = make_client(tmp_path, monkeypatch, token="s3cret")
    r = client.post("/battle/action", json={"action": "end_turn"})
    assert r.status_code == 401
    r = client.post("/battle/action", json={"action": "end_turn"},
                    headers={"X-DND-Token": "s3cret"})
    assert r.status_code == 200


def test_post_no_token_in_localhost_mode(tmp_path, monkeypatch):
    make_encounter(tmp_path)
    client = make_client(tmp_path, monkeypatch)       # token=None → localhost 免检
    r = client.post("/battle/action", json={"action": "end_turn"})
    assert r.status_code == 200
