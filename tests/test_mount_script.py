"""Idempotent mount-patch script tests (fake display app text)."""
import pytest

from battle.integration import mount_display_app as M

FAKE = (
    "from flask import Flask\n"
    "CAMP_FILE = 'x'\n"
    "_lan_token = None\n"
    "app = Flask(__name__)\n"
    "app.config['TEMPLATES_AUTO_RELOAD'] = True\n"
    "print('done')\n"
)


def test_apply_inserts_mount_block(tmp_path):
    p = tmp_path / "app.py"
    p.write_text(FAKE, encoding="utf-8")
    assert M.apply(p) is True
    src = p.read_text(encoding="utf-8")
    assert M.MARKER_START in src and M.MARKER_END in src
    assert src.index("app = Flask(__name__)") < src.index(M.MARKER_START)


def test_apply_is_idempotent(tmp_path):
    p = tmp_path / "app.py"
    p.write_text(FAKE, encoding="utf-8")
    M.apply(p)
    assert M.apply(p) is False          # 已是补丁状态 → 不再改动
    src = p.read_text(encoding="utf-8")
    assert src.count(M.MARKER_START) == 1


def test_apply_dry_run_writes_nothing(tmp_path):
    p = tmp_path / "app.py"
    p.write_text(FAKE, encoding="utf-8")
    M.apply(p, dry_run=True)
    assert M.MARKER_START not in p.read_text(encoding="utf-8")


def test_apply_missing_anchor_raises(tmp_path):
    p = tmp_path / "app.py"
    p.write_text("no flask here", encoding="utf-8")
    with pytest.raises(ValueError, match="锚点"):
        M.apply(p)
