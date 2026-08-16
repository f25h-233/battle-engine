"""M4 serve: standalone app factory + CLI serve wiring."""
import pytest
from battle.web import serve


def test_create_app_registers_blueprint(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    (tmp_path / ".campaign").write_text("t", encoding="utf-8")  # 战役名可解析、无 battle.json
    app = serve.create_app(token="t", campaign_file=str(tmp_path / ".campaign"))
    r = app.test_client().get("/battle/state")
    assert r.status_code == 404            # 无 battle.json → 404 报因（不崩）
    assert "没有战斗" in r.get_json()["error"]


def test_panel_page_injects_token_meta():
    app = serve.create_app(token="sekret")
    html = app.test_client().get("/battle/").get_data(as_text=True)
    assert 'name="dnd-token"' in html and "sekret" in html


def test_token_gate_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    (tmp_path / ".campaign").write_text("t", encoding="utf-8")
    app = serve.create_app(token="sekret", campaign_file=str(tmp_path / ".campaign"))
    client = app.test_client()
    r = client.post("/battle/action", json={"action": "undo"})
    assert r.status_code == 401 and "令牌" in r.get_json()["error"]
    r2 = client.post("/battle/action", json={"action": "undo"},
                     headers={"X-DND-Token": "sekret"})
    # 控制器裁定：token 过了 → 走到战役检查（无 battle.json → _load 的 ActionError → 400）
    assert r2.status_code == 400 and "没有战斗" in r2.get_json()["error"]


def _run_cmd_serve(monkeypatch, argv):
    from battle import cli
    calls = {}

    def fake(**kw):
        calls.update(kw)

    monkeypatch.setattr("battle.web.serve.run_server", fake)
    rc = cli.main(argv)
    return rc, calls


def test_cmd_serve_writes_campaign_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    rc, calls = _run_cmd_serve(monkeypatch,
                               ["serve", "-c", "smoke", "--port", "5011",
                                "--token", "abc"])
    assert rc == 0
    assert calls["port"] == 5011 and calls["token"] == "abc"
    assert calls["host"] == "0.0.0.0"      # 默认 LAN
    camp = tmp_path / ".runtime" / ".campaign"
    assert camp.read_text(encoding="utf-8") == "smoke"
    assert calls["campaign_file"] == str(camp)


def test_cmd_serve_without_campaign_ok(monkeypatch):
    rc, calls = _run_cmd_serve(monkeypatch, ["serve"])
    assert rc == 0 and calls["campaign_file"] == ""   # 无 -c 可起（.campaign 文件模式）


def test_cmd_serve_auto_token(monkeypatch):
    rc, calls = _run_cmd_serve(monkeypatch, ["serve"])
    assert rc == 0 and len(calls["token"]) >= 16      # 自动生成 hex token


def test_cmd_serve_bind_error(monkeypatch, capsys):
    from battle import cli

    def boom(**kw):
        raise OSError("Address already in use")

    monkeypatch.setattr("battle.web.serve.run_server", boom)
    assert cli.main(["serve"]) == 1
    assert "换端口" in capsys.readouterr().err
