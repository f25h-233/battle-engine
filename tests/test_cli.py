import io
import json
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


def test_npc_act_missing_combatant_continues(tmp_path, monkeypatch):
    """批次中 token 引用的战斗员不存在 → 打印拒绝原因，批次继续后续 token。"""
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    run_cli("create", "-c", CAMP, "--map", "10x8")
    run_cli("add-monster", "-c", CAMP, "--name", "哥布林1", "--monster", "goblin")
    run_cli("init", "-c", CAMP)
    out = run_cli("npc-act", "-c", CAMP, "不存在者:attack 哥布林1; 哥布林1:dodge")
    assert "不存在者" in out  # 缺失战斗员的拒绝原因被打印
    assert "哥布林1" in out   # 批次未中断，后续 token 正常结算


def test_undo_rolls_back_attack_hp(tmp_path, monkeypatch):
    """Critical regression: undo 必须真正回滚 HP。此前 push_undo 在状态变更
    之后执行 → 快照=变更后状态，undo 是空转，且 restore 清空历史栈。"""
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    run_cli("create", "-c", CAMP, "--map", "10x8")
    run_cli("add-player", "-c", CAMP, "--name", "星沢羽", "--ac", "16", "--hp", "17",
            "--speed", "30", "--dex-mod", "2", "--attack", "短弓:+5:1d6+3:穿刺:80/320")
    run_cli("add-monster", "-c", CAMP, "--name", "哥布林1", "--monster", "goblin")
    run_cli("place", "-c", CAMP, "--name", "星沢羽", "--x", "0", "--y", "0")
    run_cli("place", "-c", CAMP, "--name", "哥布林1", "--x", "0", "--y", "3")
    run_cli("init", "-c", CAMP)
    out = run_cli("start", "-c", CAMP)
    if "星沢羽" not in out:  # 星沢羽未先手 → 推进到她的回合
        run_cli("next-turn", "-c", CAMP)
    hp_before = json.loads(run_cli("state", "-c", CAMP, "--json"))["combatants"]["哥布林1"]["hp"]
    out = run_cli("attack", "-c", CAMP, "--actor", "星沢羽", "--target", "哥布林1",
                  "--inject", "15")
    assert "命中" in out
    hp_after = json.loads(run_cli("state", "-c", CAMP, "--json"))["combatants"]["哥布林1"]["hp"]
    assert hp_after < hp_before
    out = run_cli("undo", "-c", CAMP)
    assert "回滚" in out
    hp_restored = json.loads(run_cli("state", "-c", CAMP, "--json"))["combatants"]["哥布林1"]["hp"]
    assert hp_restored == hp_before  # 核心断言：undo 真正回滚而非空转
    out = run_cli("undo", "-c", CAMP)
    assert "没有可回滚的操作" in out  # 栈已空 → 第二次 undo 优雅拒绝


def test_undo_stack_persistable_after_push(tmp_path, monkeypatch):
    """Regression: undo_stack 快照曾按引用嵌入自身列表 → push_undo 后 json.dumps
    Circular reference 崩溃（CLI 每次动作后 _save 即触发）。"""
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    run_cli("create", "-c", CAMP, "--map", "10x8")
    run_cli("add-player", "-c", CAMP, "--name", "星沢羽", "--ac", "16", "--hp", "17",
            "--speed", "30", "--dex-mod", "2", "--attack", "短弓:+5:1d6+3:穿刺:80/320")
    run_cli("place", "-c", CAMP, "--name", "星沢羽", "--x", "0", "--y", "0")
    run_cli("init", "-c", CAMP)
    # 结算动作触发 push_undo → cmd_attack 的 _save 立即 json 序列化（修复前在此崩溃）
    out = run_cli("attack", "-c", CAMP, "--actor", "星沢羽", "--target", "星沢羽",
                  "--inject", "10")
    assert "攻击" in out
    out2 = run_cli("state", "-c", CAMP, "--json")
    assert '"undo_stack"' in out2
