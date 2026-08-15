"""Atomic battle.json persistence with .bak rotation and recovery."""

from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path

from .models import Encounter


def battle_path(campaign_dir) -> Path:
    return Path(campaign_dir) / "battle.json"


def save_encounter(enc: Encounter, campaign_dir=None) -> None:
    campaign_dir = campaign_dir or _resolve_campaign_dir(enc)
    path = battle_path(campaign_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():                       # rotate previous version to .bak
        path.replace(path.with_suffix(".json.bak"))
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(enc.to_dict(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_encounter(campaign_dir) -> Encounter | None:
    path = battle_path(campaign_dir)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return Encounter.from_dict(data)
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
