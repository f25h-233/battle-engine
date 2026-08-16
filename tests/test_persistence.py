import json
import os
import pytest
from pathlib import Path
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


def test_gbk_bytes_raise_corrupt_value_error(tmp_path):
    """Minor: GBK 编码的历史 battle.json 是 UnicodeDecodeError 而非 JSONDecodeError
    ——load 必须把两者都转成"损坏"提示（此前 UnicodeDecodeError 裸抛）。"""
    P.save_encounter(make_enc(tmp_path), str(tmp_path))
    path = P.battle_path(str(tmp_path))
    path.write_bytes('{"战斗": 1}'.encode("gbk"))
    with pytest.raises(ValueError, match="损坏"):
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


def test_save_failure_keeps_current_file(tmp_path, monkeypatch):
    """主文件永存：save 中途失败（os.replace 抛）时 battle.json 必须仍在——
    此前轮转用 move（先移走主文件再写新文件），失败窗口期 battle.json 不存在，
    SSE 轮询频繁报「battle.json 不存在」（真实 bug）。"""
    enc = make_enc(tmp_path)
    P.save_encounter(enc, str(tmp_path))          # 第一版（hp 20）
    path = P.battle_path(str(tmp_path))

    def boom(src, dst):
        raise OSError("模拟写盘失败")

    monkeypatch.setattr("os.replace", boom)
    with pytest.raises(OSError):
        enc.combatants["甲"].hp = 5
        P.save_encounter(enc, str(tmp_path))
    monkeypatch.undo()
    assert path.exists()                          # 主文件仍在（未丢失）
    cur = json.loads(path.read_text(encoding="utf-8"))
    assert cur["combatants"]["甲"]["hp"] == 20    # 且内容仍是上一版
    assert path.with_suffix(".json.bak").exists()  # .bak 也已就位


def test_replace_retries_on_permission_error(tmp_path, monkeypatch):
    """Windows: os.replace 在目标被其他进程打开读时 PermissionError（WinError 5）——
    save 必须短重试等待锁释放，而非裸抛（并发实证抓到的真实竞争）。"""
    enc = make_enc(tmp_path)
    P.save_encounter(enc, str(tmp_path))
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("模拟 Windows 文件锁")
        return real_replace(src, dst)

    monkeypatch.setattr("os.replace", flaky)
    enc.combatants["甲"].hp = 5
    P.save_encounter(enc, str(tmp_path))          # 不抛
    assert calls["n"] == 2                        # 首次失败 → 重试成功
    cur = json.loads(P.battle_path(str(tmp_path)).read_text(encoding="utf-8"))
    assert cur["combatants"]["甲"]["hp"] == 5


def test_load_retries_on_permission_error(tmp_path, monkeypatch):
    """Windows: 读 battle.json 恰逢 os.replace（rename）瞬间 → PermissionError——
    load 必须短重试（否则 SSE 轮询把写锁窗口报成"损坏"帧，频繁提示）。"""
    enc = make_enc(tmp_path)
    P.save_encounter(enc, str(tmp_path))
    real_read = Path.read_text
    calls = {"n": 0}

    def flaky(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("模拟 rename 瞬间读锁")
        return real_read(self, *a, **kw)

    monkeypatch.setattr("pathlib.Path.read_text", flaky)
    loaded = P.load_encounter(str(tmp_path))
    assert loaded is not None and calls["n"] == 2
    assert loaded.combatants["甲"].hp == 20
