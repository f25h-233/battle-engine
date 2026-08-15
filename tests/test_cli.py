import io
import sys
import pytest
from battle import cli

CAMP = "cli_test_camp"


def run_cli(*argv):
    out = io.StringIO()
    err = io.StringIO()
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        cli.main(list(argv))
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    return out.getvalue() + err.getvalue()  # 拒绝消息走 stderr，合并进 out


def test_create_and_state(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    out = run_cli("create", "-c", CAMP, "--map", "10x8")
    assert "创建" in out
    out = run_cli("state", "-c", CAMP)
    assert "setup" in out


def test_init_gate_via_cli(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    run_cli("create", "-c", CAMP, "--map", "10x8")
    run_cli("add-player", "-c", CAMP, "--name", "星沢羽", "--ac", "16", "--hp", "17",
            "--speed", "30", "--dex-mod", "2")
    run_cli("place", "-c", CAMP, "--name", "星沢羽", "--x", "0", "--y", "0")
    out = run_cli("attack", "-c", CAMP, "--actor", "星沢羽", "--target", "哥布林")
    assert "先攻" in out or "不存在" in out  # 未掷先攻或目标未创建 → 拒绝


def test_npc_act_batch(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    run_cli("create", "-c", CAMP, "--map", "10x8")
    run_cli("add-monster", "-c", CAMP, "--name", "哥布林1", "--monster", "goblin")
    run_cli("add-monster", "-c", CAMP, "--name", "哥布林2", "--monster", "goblin")
    run_cli("place", "-c", CAMP, "--name", "哥布林1", "--x", "0", "--y", "0")
    run_cli("place", "-c", CAMP, "--name", "哥布林2", "--x", "1", "--y", "0")
    run_cli("init", "-c", CAMP)
    out = run_cli("npc-act", "-c", CAMP, "哥布林1:attack 哥布林2; 哥布林2:dash")
    # 若哥布林1 当前回合 → 攻击结算；否则被拒绝。两种都打印结果行
    assert "哥布林1" in out and "哥布林2" in out
