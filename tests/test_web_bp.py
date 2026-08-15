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


def test_state_endpoint_corrupt_battle_file(tmp_path, monkeypatch):
    make_encounter(tmp_path)
    battle_file = tmp_path / "campaigns" / "t" / "battle.json"
    battle_file.write_text("{ not valid json !!!", encoding="utf-8")
    client = make_client(tmp_path, monkeypatch)
    r = client.get("/battle/state")
    assert r.status_code == 404
    assert "损坏" in r.get_json()["error"]


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


# ── POST /battle/action ──────────────────────────────────────────

def post_action(client, **body):
    return client.post("/battle/action", json=body)


def test_action_attack_happy_path(tmp_path, monkeypatch):
    make_encounter(tmp_path)
    client = make_client(tmp_path, monkeypatch)
    r = post_action(client, action="attack", actor="星沢羽", target="哥布林",
                    attack="短弓", injected={"d20": 15})
    j = r.get_json()
    assert r.status_code == 200 and j["ok"]
    assert j["hp_after"]["哥布林"] < j["hp_before"]["哥布林"]
    assert j["state"]["combatants"]["哥布林"]["hp"] < 7
    # 已写盘：从磁盘重载确认（CLI 与 Web 共享单一事实源）
    enc = P.load_encounter(tmp_path / "campaigns" / "t")
    assert enc.combatants["哥布林"].hp < 7
    assert any("命中" in l for l in j["lines"])


def test_action_attack_manual_dice(tmp_path, monkeypatch):
    make_encounter(tmp_path)
    client = make_client(tmp_path, monkeypatch)
    r = post_action(client, action="attack", actor="星沢羽", target="哥布林",
                    attack="短弓", injected={"d20": 15, "damage": [4, 2]})
    j = r.get_json()
    assert r.status_code == 200 and j["ok"]
    assert j["hp_after"]["哥布林"] == 0             # 注入伤害 4+2+3=9 > 哥布林 7 HP → 归零
    line = "\n".join(j["lines"])
    assert "注入" in line and "9" in line           # 注入路径执行且骰面和修正精确


def test_action_turn_gate_rejected(tmp_path, monkeypatch):
    make_encounter(tmp_path)
    client = make_client(tmp_path, monkeypatch)
    # 把当前行动者换成 哥布林
    enc = P.load_encounter(tmp_path / "campaigns" / "t")
    enc.next_turn()
    P.save_encounter(enc, tmp_path / "campaigns" / "t")
    r = post_action(client, action="attack", actor="星沢羽", target="哥布林",
                    attack="短弓", injected={"d20": 15})
    j = r.get_json()
    assert r.status_code == 400
    assert "回合" in j["error"]
    assert j["state"]["combatants"]["哥布林"]["hp"] == 7  # 状态未被污染


def test_action_unknown_actor(tmp_path, monkeypatch):
    make_encounter(tmp_path)
    client = make_client(tmp_path, monkeypatch)
    r = post_action(client, action="attack", actor="幽灵", target="哥布林")
    assert r.status_code == 404
    assert "不存在" in r.get_json()["error"]


def test_action_unknown_target(tmp_path, monkeypatch):
    make_encounter(tmp_path)
    client = make_client(tmp_path, monkeypatch)
    r = post_action(client, action="attack", actor="星沢羽", target="幽灵")
    assert r.status_code == 404
    assert "不存在" in r.get_json()["error"]


def test_action_unknown_action(tmp_path, monkeypatch):
    make_encounter(tmp_path)
    client = make_client(tmp_path, monkeypatch)
    r = post_action(client, action="dance", actor="星沢羽")
    assert r.status_code == 400
    assert "未知动作" in r.get_json()["error"]


def test_action_move_and_dash(tmp_path, monkeypatch):
    make_encounter(tmp_path)
    client = make_client(tmp_path, monkeypatch)
    r = post_action(client, action="move", actor="星沢羽", to=[1, 0])
    assert r.status_code == 200 and r.get_json()["ok"]
    st = r.get_json()["state"]
    assert st["combatants"]["星沢羽"]["x"] == 1
    assert st["combatants"]["星沢羽"]["movement_left_ft"] == 25  # 30 - 5ft
    r2 = post_action(client, action="dash", actor="星沢羽", to=[4, 0])  # 15ft ≤ 30ft
    assert r2.status_code == 200 and r2.get_json()["ok"]
    assert r2.get_json()["state"]["combatants"]["星沢羽"]["x"] == 4
    assert r2.get_json()["state"]["combatants"]["星沢羽"]["acted"] is True


def test_action_dodge_disengage(tmp_path, monkeypatch):
    make_encounter(tmp_path)
    client = make_client(tmp_path, monkeypatch)
    r = post_action(client, action="dodge", actor="星沢羽")
    assert r.status_code == 200 and r.get_json()["ok"]
    assert r.get_json()["state"]["combatants"]["星沢羽"]["acted"] is True
    r2 = post_action(client, action="disengage", actor="星沢羽")  # 动作已用 → 拒绝
    assert r2.status_code == 400


def test_action_death_save(tmp_path, monkeypatch):
    enc = make_encounter(tmp_path)
    enc.combatants["星沢羽"].hp = 0
    P.save_encounter(enc, tmp_path / "campaigns" / "t")
    client = make_client(tmp_path, monkeypatch)
    r = post_action(client, action="death_save", actor="星沢羽", injected={"d20": 10})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert r.get_json()["state"]["combatants"]["星沢羽"]["death_saves"]["successes"] == 1


def test_action_end_turn_advances(tmp_path, monkeypatch):
    make_encounter(tmp_path)
    client = make_client(tmp_path, monkeypatch)
    r = post_action(client, action="end_turn")
    j = r.get_json()
    assert r.status_code == 200 and j["ok"]
    assert j["state"]["turn_index"] == 1
    assert j["state"]["turn_order"][1] in ("哥布林",)  # 星沢羽 后必是 哥布林


# ── POST /battle/roll ────────────────────────────────────────────

def test_roll_d20(tmp_path, monkeypatch):
    make_encounter(tmp_path)
    client = make_client(tmp_path, monkeypatch)
    r = client.post("/battle/roll", json={"spec": "1d20", "mod": 5})
    j = r.get_json()
    assert r.status_code == 200 and j["ok"]
    assert len(j["rolls"]) == 1 and 1 <= j["rolls"][0] <= 20
    assert j["total"] == j["rolls"][0] + 5


def test_roll_advantage_uses_two(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    r = client.post("/battle/roll", json={"spec": "1d20", "advantage": "advantage"})
    j = r.get_json()
    assert len(j["rolls"]) == 2


def test_roll_dice_notation(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    r = client.post("/battle/roll", json={"spec": "2d6+3"})
    j = r.get_json()
    assert j["ok"] and len(j["rolls"]) == 2
    assert j["total"] == sum(j["rolls"]) + 3


def test_roll_bad_spec(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch)
    r = client.post("/battle/roll", json={"spec": "xyz"})
    assert r.status_code == 400 and not r.get_json()["ok"]


# ── GET /battle/stream (SSE) ─────────────────────────────────────

def test_stream_events_initial_then_changes(tmp_path, monkeypatch):
    """_stream_events：首帧立即给当前状态；文件变化推新状态；未变发心跳。"""
    make_encounter(tmp_path)
    camp_dir = tmp_path / "campaigns" / "t"
    events = bp._stream_events(camp_dir, interval=0.001)
    first = next(events)
    assert isinstance(first, dict) and first.get("state", {}).get("campaign") == "t"
    keep = next(events)
    assert keep == ": keepalive"
    enc = P.load_encounter(camp_dir)
    enc.combatants["哥布林"].hp = 3
    P.save_encounter(enc, camp_dir)
    changed = next(events)
    assert isinstance(changed, dict)
    assert changed["state"]["combatants"]["哥布林"]["hp"] == 3


def test_stream_events_file_missing(tmp_path, monkeypatch):
    make_encounter(tmp_path)
    camp_dir = tmp_path / "campaigns" / "t"
    events = bp._stream_events(camp_dir, interval=0.001)
    next(events)                                     # 初始状态
    (camp_dir / "battle.json").unlink()
    err = next(events)
    assert isinstance(err, dict) and "无 battle.json" in err.get("error", "")


def test_stream_route_headers(tmp_path, monkeypatch):
    make_encounter(tmp_path)
    client = make_client(tmp_path, monkeypatch)
    with client.get("/battle/stream") as resp:
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"


# ── M3 web ────────────────────────────────────────────────────────

def test_action_undo_rolls_back(tmp_path, monkeypatch):
    make_encounter(tmp_path)
    client = make_client(tmp_path, monkeypatch)
    r = post_action(client, action="attack", actor="星沢羽", target="哥布林",
                    attack="短弓", injected={"d20": 15})
    assert r.get_json()["state"]["combatants"]["哥布林"]["hp"] < 7
    r2 = post_action(client, action="undo")
    j2 = r2.get_json()
    assert r2.status_code == 200 and j2["ok"]
    assert j2["state"]["combatants"]["哥布林"]["hp"] == 7
    # undo 无 actor 也可用（不要求身份）；快照回滚 → acted 一并还原
    assert j2["state"]["combatants"]["星沢羽"]["acted"] is False   # 动作已回滚
    assert j2["state"]["turn_order"][j2["state"]["turn_index"]] == "星沢羽"  # undo 后回合未动


def test_action_cast_save_based_spell(tmp_path, monkeypatch):
    """豁免型法术（AttackSpec.save_dc）→ 豁免路径而非攻击掷骰。"""
    from battle.core.models import AttackSpec as AS
    enc = make_encounter(tmp_path)
    c = enc.combatants["星沢羽"]
    c.actor.attacks = [AS(name="剧毒喷射", kind="spell", save_dc=13, save_stat="con",
                          range_ft=(30, 0), damage="1d12", damage_type="毒素")]
    P.save_encounter(enc, tmp_path / "campaigns" / "t")
    client = make_client(tmp_path, monkeypatch)
    r = post_action(client, action="cast", actor="星沢羽", target="哥布林",
                    attack="剧毒喷射", injected={"d20": 5})
    j = r.get_json()
    assert r.status_code == 200 and j["ok"]
    assert "豁免" in "\n".join(j["lines"])
    assert "命中" not in "\n".join(j["lines"])   # 豁免路径不走攻击掷骰（无"命中"行）


def test_action_cast_aoe_center(tmp_path, monkeypatch):
    from battle.core.models import AttackSpec as AS
    enc = make_encounter(tmp_path)
    c = enc.combatants["星沢羽"]
    c.actor.attacks = [AS(name="火球术", kind="spell", save_dc=14, save_stat="dex",
                          range_ft=(150, 0), damage="8d6", damage_type="火焰",
                          aoe_radius_ft=20)]
    P.save_encounter(enc, tmp_path / "campaigns" / "t")
    client = make_client(tmp_path, monkeypatch)
    # 中心取哥布林所在格 (3,4) → 覆盖到它；注入 d20=5 必不过 DC14 → 全伤
    r = post_action(client, action="cast", actor="星沢羽", attack="火球术",
                    center=[3, 4], radius=20, injected={"d20": 5})
    j = r.get_json()
    assert r.status_code == 200 and j["ok"]
    line = "\n".join(j["lines"])
    assert "半径" in line and "豁免" in line
    assert j["state"]["combatants"]["哥布林"]["hp"] < 7   # 命中结算落盘


def test_action_inject_out_of_range_400(tmp_path, monkeypatch):
    make_encounter(tmp_path)
    client = make_client(tmp_path, monkeypatch)
    r = post_action(client, action="attack", actor="星沢羽", target="哥布林",
                    attack="短弓", injected={"d20": 99})
    j = r.get_json()
    assert r.status_code == 400
    assert "1" in j["error"] and "20" in j["error"]


def test_state_payload_new_fields(tmp_path, monkeypatch):
    make_encounter(tmp_path)
    client = make_client(tmp_path, monkeypatch)
    st = client.get("/battle/state").get_json()["state"]
    c = st["combatants"]["星沢羽"]
    assert c["dodging"] is False and c["disengaged"] is False
    assert c["initiative_d20"] is not None


def test_action_cast_aoe_radius_fallback(tmp_path, monkeypatch):
    """cast center 未带 radius → 服务端回退 attack.aoe_radius_ft。"""
    from battle.core.models import AttackSpec as AS
    enc = make_encounter(tmp_path)
    c = enc.combatants["星沢羽"]
    c.actor.attacks = [AS(name="火球术", kind="spell", save_dc=14, save_stat="dex",
                          range_ft=(150, 0), damage="8d6", damage_type="火焰",
                          aoe_radius_ft=20)]
    P.save_encounter(enc, tmp_path / "campaigns" / "t")
    client = make_client(tmp_path, monkeypatch)
    r = post_action(client, action="cast", actor="星沢羽", attack="火球术",
                    center=[3, 4], injected={"d20": 5})
    j = r.get_json()
    assert r.status_code == 200 and j["ok"]
    assert "半径 20" in "\n".join(j["lines"])           # 回退生效
    assert j["state"]["combatants"]["哥布林"]["hp"] < 7


# ── M3 回归补全 ───────────────────────────────────────────────────

def test_stream_events_corrupt_file_pushes_error(tmp_path, monkeypatch):
    """M2 简化面：battle.json 损坏 → SSE 推 error 帧而非静默清空（spec §10）。"""
    make_encounter(tmp_path)
    camp_dir = tmp_path / "campaigns" / "t"
    events = bp._stream_events(camp_dir, interval=0.001)
    next(events)                                     # 初始状态帧
    (camp_dir / "battle.json").write_text("{ 坏 json", encoding="utf-8")
    ev = next(events)
    assert isinstance(ev, dict) and "损坏" in ev.get("error", "")


def test_action_cast_spell_attack_passthrough(tmp_path, monkeypatch):
    """M2 简化面：cast → spell attack 委托 resolve_attack 时注入透传链完整。"""
    from battle.core.models import AttackSpec as AS
    enc = make_encounter(tmp_path)
    c = enc.combatants["星沢羽"]
    c.actor.attacks = [AS(name="灼热射线", kind="spell", attack_bonus=5,
                          range_ft=(120, 0), damage="2d6", damage_type="火焰")]
    P.save_encounter(enc, tmp_path / "campaigns" / "t")
    client = make_client(tmp_path, monkeypatch)
    r = post_action(client, action="cast", actor="星沢羽", target="哥布林",
                    attack="灼热射线", injected={"d20": 15, "damage": [4, 2]})
    j = r.get_json()
    assert r.status_code == 200 and j["ok"]
    line = "\n".join(j["lines"])
    assert "注入" in line                     # 伤害注入透传到 resolve_attack
    assert j["hp_after"]["哥布林"] == 7 - 6   # 4+2 骰面（无静态修正）=6 伤害 → 剩 1
