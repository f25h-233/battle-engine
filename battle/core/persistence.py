"""Atomic battle.json persistence with .bak rotation and recovery."""

from __future__ import annotations
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from .models import Encounter


def battle_path(campaign_dir) -> Path:
    return Path(campaign_dir) / "battle.json"


def _atomic_replace(src, dst, retries: int = 10, delay: float = 0.02) -> None:
    """os.replace 的 Windows 版：目标被其他进程打开读时抛 PermissionError
    （WinError 5）——短重试等待锁释放。读操作微秒级，10×20ms 上限 200ms，
    高强度并发下仍能收敛（实测 5s 满速竞争零逃逸）。"""
    for i in range(retries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == retries - 1:
                raise
            time.sleep(delay)


def save_encounter(enc: Encounter, campaign_dir=None) -> None:
    campaign_dir = campaign_dir or _resolve_campaign_dir(enc)
    path = battle_path(campaign_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 轮转用复制而非移动：主文件任何时刻都在位（move 会造成"移走→写回"窗口期，
    # SSE 轮询/并发请求会读到文件不存在——真实 bug）。.bak 失败不阻塞保存。
    bak = path.with_suffix(".json.bak")
    if path.exists():
        try:
            shutil.copy2(path, bak)
        except OSError:
            pass
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(enc.to_dict(), f, ensure_ascii=False, indent=2)
        _atomic_replace(tmp, path)          # 原子替换 + Windows 读锁重试：无窗口期
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_text_retry(path: Path, retries: int = 5, delay: float = 0.01) -> str:
    """Windows: open 恰逢 os.replace（rename）瞬间会 PermissionError（WinError 13）
    ——短重试等待写完成（SSE 轮询/并发读不报假"损坏"帧）。"""
    for i in range(retries):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            if i == retries - 1:
                raise
            time.sleep(delay)


def load_encounter(campaign_dir) -> Encounter | None:
    path = battle_path(campaign_dir)
    if not path.exists():
        return None
    try:
        data = _read_text_retry(path)
        return Encounter.from_dict(json.loads(data))
    except (json.JSONDecodeError, UnicodeDecodeError):  # GBK 历史档案 → UnicodeDecodeError
        raise ValueError(
            f"{path} 损坏——可用 battle recover 从 .bak 恢复")


def restore_backup(campaign_dir) -> Encounter:
    bak = battle_path(campaign_dir).with_suffix(".json.bak")
    if not bak.exists():
        raise FileNotFoundError(f"没有可用备份: {bak}")
    with open(bak, encoding="utf-8") as f:
        data = json.load(f)
    enc = Encounter.from_dict(data)
    save_encounter(enc, campaign_dir)       # promote backup → current
    return enc


def _resolve_campaign_dir(enc: Encounter) -> str:
    import os as _os
    root = _os.environ.get("DND_CAMPAIGN_ROOT", str(Path.home() / ".claude" / "dnd"))
    return str(Path(root) / "campaigns" / enc.campaign)
