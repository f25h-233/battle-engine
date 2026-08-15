import io
import json
import sys
import pytest
from battle import cli
from battle.core import dice

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


def test_waypoint_add_parses_coords_and_move_succeeds(tmp_path, monkeypatch):
    """Regression: waypoint add 曾把 "12,3" 字符串直接存入 → move --to 门口
    时 in_bounds(*"12,3") 解包字符串 TypeError 崩溃。"""
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    run_cli("create", "-c", CAMP, "--map", "20x8")
    run_cli("add-player", "-c", CAMP, "--name", "星沢羽", "--ac", "16", "--hp", "17",
            "--speed", "30")
    run_cli("place", "-c", CAMP, "--name", "星沢羽", "--x", "10", "--y", "0")
    out = run_cli("waypoint", "-c", CAMP, "add", "门口", "12,3")
    assert "地标" in out and "门口" in out
    run_cli("init", "-c", CAMP)
    run_cli("start", "-c", CAMP)
    out = run_cli("move", "-c", CAMP, "--actor", "星沢羽", "--to", "门口")
    assert "移动" in out and "(12,3)" in out  # 此前在此 TypeError 崩溃


def test_cond_clear_without_condition_value(tmp_path, monkeypatch):
    """Regression: cond clear 无 condition 参数时 args.condition 为 None
    → .lower() AttributeError 崩溃。"""
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    run_cli("create", "-c", CAMP, "--map", "10x8")
    run_cli("add-player", "-c", CAMP, "--name", "星沢羽", "--ac", "16", "--hp", "17")
    out = run_cli("cond", "-c", CAMP, "add", "星沢羽", "眩晕")
    assert "眩晕" in out
    out = run_cli("cond", "-c", CAMP, "clear", "星沢羽")
    assert "条件" in out and "—" in out


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


# ── M3 CLI ────────────────────────────────────────────────────────

def test_cast_aoe_point_radius(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    run_cli("create", "-c", CAMP, "--map", "20x20")
    run_cli("add-player", "-c", CAMP, "--name", "法师", "--ac", "13", "--hp", "20",
            "--speed", "30", "--dex-mod", "2")
    run_cli("add-monster", "-c", CAMP, "--name", "哥布林1", "--monster", "goblin")
    run_cli("add-monster", "-c", CAMP, "--name", "哥布林2", "--monster", "goblin")
    run_cli("place", "-c", CAMP, "--name", "法师", "--x", "0", "--y", "0")
    run_cli("place", "-c", CAMP, "--name", "哥布林1", "--x", "3", "--y", "0")
    run_cli("place", "-c", CAMP, "--name", "哥布林2", "--x", "9", "--y", "0")
    run_cli("init", "-c", CAMP)
    run_cli("start", "-c", CAMP)
    # 推进到法师的回合（state 的 ► 标记指向当前战斗员；最多 2 次 next-turn 必然轮到她）
    for _ in range(3):
        if "► 法师" in run_cli("state", "-c", CAMP):
            break
        run_cli("next-turn", "-c", CAMP)
    out = run_cli("cast", "-c", CAMP, "--actor", "法师", "--name", "火球术",
                  "--point", "0,0", "--radius", "20", "--dc", "14",
                  "--stat", "dex", "--dmg", "4d6", "--type", "火焰")
    assert "火球术" in out and "豁免" in out
    assert "哥布林1" in out           # 15ft 内在覆盖内 → 有豁免行
    assert "哥布林2" not in out       # 45ft 外 → 无豁免行（AoE 路径不含范围外目标）


def test_cast_point_targets_mutually_exclusive(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    run_cli("create", "-c", CAMP, "--map", "20x20")
    run_cli("add-player", "-c", CAMP, "--name", "法师", "--ac", "13", "--hp", "20")
    run_cli("init", "-c", CAMP)
    run_cli("start", "-c", CAMP)
    out = run_cli("cast", "-c", CAMP, "--actor", "法师", "--name", "火球术",
                  "--targets", "哥布林1", "--point", "0,0")
    assert "互斥" in out


def test_add_player_hp_roll(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    run_cli("create", "-c", CAMP, "--map", "10x8")
    dice.seed(7)
    run_cli("add-player", "-c", CAMP, "--name", "新人", "--ac", "15", "--hp-roll", "2d6+6")
    # 种子 7: 2d6 = 3+2=5 → HP 11（brief 原写种子 3——实测种子 3 掷出 [2,5]=7 → 13，恒假）
    out = run_cli("state", "-c", CAMP, "--json")
    assert json.loads(out)["combatants"]["新人"]["hp"] == 11


def test_recover_requires_campaign(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    out = run_cli("recover")
    assert "缺少" in out and "-c" in out


def test_init_shows_d20_face(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    run_cli("create", "-c", CAMP, "--map", "10x8")
    run_cli("add-player", "-c", CAMP, "--name", "星沢羽", "--ac", "16", "--hp", "17",
            "--dex-mod", "2")
    out = run_cli("init", "-c", CAMP)
    assert "d20(" in out               # 显示 d20 面值（此前只显示总和）


def test_cli_inject_out_of_range(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    run_cli("create", "-c", CAMP, "--map", "10x8")
    run_cli("add-player", "-c", CAMP, "--name", "星沢羽", "--ac", "16", "--hp", "17",
            "--speed", "30", "--dex-mod", "2", "--attack", "短弓:+5:1d6+3:穿刺:80/320")
    run_cli("add-monster", "-c", CAMP, "--name", "哥布林1", "--monster", "goblin")
    run_cli("place", "-c", CAMP, "--name", "星沢羽", "--x", "0", "--y", "0")
    run_cli("place", "-c", CAMP, "--name", "哥布林1", "--x", "0", "--y", "3")
    run_cli("init", "-c", CAMP)
    out = run_cli("attack", "-c", CAMP, "--actor", "星沢羽", "--target", "哥布林1",
                  "--inject", "99")
    assert "1–20" in out               # 注入校验文案


def test_end_award_xp_twice_no_double_award(capsys, tmp_path, monkeypatch):
    """Regression (终审 I-2): end --award-xp 重复执行会把 XP 二次写入人物卡——
    第二次 end 必须在回写前拒绝（spec §10 不得静默重解释）。"""
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    run_cli("create", "-c", CAMP, "--map", "10x8")
    run_cli("add-player", "-c", CAMP, "--name", "法师", "--ac", "13", "--hp", "20")
    run_cli("add-monster", "-c", CAMP, "--name", "哥布林1", "--monster", "goblin")
    run_cli("place", "-c", CAMP, "--name", "法师", "--x", "0", "--y", "0")
    run_cli("place", "-c", CAMP, "--name", "哥布林1", "--x", "3", "--y", "0")
    run_cli("init", "-c", CAMP)
    run_cli("start", "-c", CAMP)
    run_cli("set-hp", "-c", CAMP, "--actor", "哥布林1", "--hp", "0")  # 消灭哥布林
    sheet = tmp_path / "campaigns" / CAMP / "characters" / "法师.md"
    sheet.parent.mkdir(parents=True)
    sheet.write_text("- **经验:** 0 / 300\n", encoding="utf-8")
    out1 = run_cli("end", "-c", CAMP, "--award-xp")
    assert "经验值" in out1             # 第一次：正常发放（0 + 哥布林 50 XP）
    assert "**经验:** 50 / 300" in sheet.read_text(encoding="utf-8")
    out2 = run_cli("end", "-c", CAMP, "--award-xp")
    assert "已结束" in out2             # 第二次：拒绝，不静默重解释
    assert "**经验:** 50 / 300" in sheet.read_text(encoding="utf-8")  # 未二次加 XP
