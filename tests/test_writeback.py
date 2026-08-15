"""M3 writeback: battle end pushes HP/temp/death-saves to PC markdown sheets."""
import json
import pytest
from battle.core import writeback as W
from battle.core.models import Actor, Encounter

CN_SHEET = """# 莫德凯
**玩家:** DM

## 战斗数值
- **HP:** 27 / 27 | **临时 HP:** 0
- **AC:** 18 | **先攻:** +0 | **速度:** 30 英尺
- **死亡豁免:** 成功 0 | 失败 0

## 身份
- **经验:** 950 / 2700
"""

EN_SHEET = """# 星沢羽
## Combat Stats
- **HP:** 25 / 25 | **Temp HP:** 0
- **AC:** 15 | **Initiative:** +3
- **Death Saves:** Successes: 0 | Failures: 0
"""


def make_enc():
    enc = Encounter(campaign="wb", width=10, height=10)
    pc = Actor(name="莫德凯", kind="pc", ac=18, max_hp=27, speed_ft=30, dex_mod=0)
    npc = Actor(name="哥布林", kind="npc", ac=15, max_hp=7, speed_ft=30, dex_mod=1, xp=50)
    cpc = enc.add_combatant(pc, x=0, y=0)
    cg = enc.add_combatant(npc, x=3, y=0)
    return enc, cpc, cg


def test_update_sheet_cn(tmp_path):
    p = tmp_path / "莫德凯.md"
    p.write_text(CN_SHEET, encoding="utf-8")
    out = W.update_sheet(p, hp=12, temp_hp=3, death_saves={"successes": 1, "failures": 2, "stable": False})
    assert "HP" in "\n".join(out)
    src = p.read_text(encoding="utf-8")
    assert "- **HP:** 12 / 27 | **临时 HP:** 3" in src
    assert "成功 1 | 失败 2" in src
    assert "950" in src                      # 非目标行不动


def test_update_sheet_en(tmp_path):
    p = tmp_path / "星沢羽.md"
    p.write_text(EN_SHEET, encoding="utf-8")
    W.update_sheet(p, hp=10, temp_hp=0, death_saves={"successes": 0, "failures": 1, "stable": False})
    src = p.read_text(encoding="utf-8")
    assert "- **HP:** 10 / 25 | **Temp HP:** 0" in src
    assert "Successes: 0 | Failures: 1" in src


def test_update_sheet_missing_hp_line_reports(tmp_path):
    p = tmp_path / "破损.md"
    p.write_text("# 破损\n没有战斗数据段\n", encoding="utf-8")
    out = W.update_sheet(p, hp=1, temp_hp=0, death_saves={"successes": 0, "failures": 0, "stable": False})
    assert any("未找到" in x for x in out)    # 失败提示而非崩溃


def test_update_xp(tmp_path):
    p = tmp_path / "莫德凯.md"
    p.write_text(CN_SHEET, encoding="utf-8")
    out = W.update_xp(p, 120)
    assert "经验" in "\n".join(out)
    assert "1070 / 2700" in p.read_text(encoding="utf-8")   # 950 + 120


def test_writeback_combatants_skips_npc(tmp_path):
    enc, cpc, cg = make_enc()
    (tmp_path / "characters").mkdir()
    (tmp_path / "characters" / "莫德凯.md").write_text(CN_SHEET, encoding="utf-8")
    cpc.hp = 9
    out = W.writeback_combatants(enc, tmp_path)
    assert any("莫德凯" in x for x in out)
    src = (tmp_path / "characters" / "莫德凯.md").read_text(encoding="utf-8")
    assert "- **HP:** 9 / 27" in src


def test_actor_xp_serialized():
    enc, cpc, cg = make_enc()
    d = json.loads(json.dumps(enc.to_dict()))
    assert d["combatants"]["哥布林"]["actor"]["xp"] == 50
    enc2 = Encounter.from_dict(d)
    assert enc2.combatants["哥布林"].actor.xp == 50


def test_cli_end_award_xp(capsys, tmp_path, monkeypatch):
    """端到端：end --award-xp 更新 PC 卡经验行。"""
    from battle import cli
    import io, sys
    monkeypatch.setenv("DND_CAMPAIGN_ROOT", str(tmp_path))
    def run_cli(*argv):
        out, err = io.StringIO(), io.StringIO()
        old_o, old_e = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            cli.main(list(argv))
        finally:
            sys.stdout, sys.stderr = old_o, old_e
        return out.getvalue() + err.getvalue()
    run_cli("create", "-c", "wbcli", "--map", "10x8")
    run_cli("add-player", "-c", "wbcli", "--name", "莫德凯", "--ac", "18", "--hp", "27")
    run_cli("add-monster", "-c", "wbcli", "--name", "哥布林1", "--monster", "goblin")
    run_cli("init", "-c", "wbcli")
    run_cli("start", "-c", "wbcli")
    # 打死哥布林（用 set-hp 模拟消灭）
    run_cli("set-hp", "-c", "wbcli", "--actor", "哥布林1", "--hp", "0")
    char_dir = tmp_path / "campaigns" / "wbcli" / "characters"
    char_dir.mkdir(parents=True)
    (char_dir / "莫德凯.md").write_text(CN_SHEET, encoding="utf-8")
    out = run_cli("end", "-c", "wbcli", "--award-xp")
    assert "经验" in out or "XP" in out
    src = (char_dir / "莫德凯.md").read_text(encoding="utf-8")
    assert "1000 / 2700" in src          # 950 + 哥布林 xp 50
