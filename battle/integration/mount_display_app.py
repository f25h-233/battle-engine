#!/usr/bin/env python3
"""Idempotently apply the battle-engine blueprint mount to the dnd display app.

为什么存在：显示端在插件目录（marketplaces），`/plugin update` 会整目录刷新、
抹掉手工修改。本脚本在插件更新后重跑即可恢复挂载（配合记忆约定
[[dnd-utf8-sweep]] 的重查清单）。

用法:
    python integration/mount_display_app.py [--dry-run] [display_app.py]

幂等：挂载块由 MARKER_START/MARKER_END 界定，已存在时零改动。
"""

from __future__ import annotations
import sys
import textwrap
from pathlib import Path

MARKER_START = "# ── battle-engine blueprint (M2)"
MARKER_END = "# ── end battle-engine mount (M2)"

MOUNT_BLOCK = textwrap.dedent(f"""\
    {MARKER_START}
    if os.environ.get("BATTLE_ENGINE_DIR"):
        _eng = os.environ["BATTLE_ENGINE_DIR"]
        if os.path.isdir(_eng) and _eng not in sys.path:
            sys.path.insert(0, _eng)
        try:
            from battle.web.bp import battle_bp
            app.register_blueprint(battle_bp)
            app.config["BATTLE_TOKEN"] = _lan_token or ""
            app.config["BATTLE_CAMPAIGN_FILE"] = CAMP_FILE
            print("battle-engine 蓝图已挂载（M2）")
        except ImportError as _e:
            print(f"!! battle-engine 蓝图挂载失败（忽略）: {{_e}}")
    {MARKER_END}
""")

ANCHOR = "app = Flask(__name__)"

DEFAULT_TARGET = (r"C:\Users\qwe13\.claude\plugins\marketplaces"
                  r"\neuralinitiative-claude-dnd-skill\skills\dnd\display"
                  r"\dnd-display-app.py")


def apply(path: Path, *, dry_run: bool = False) -> bool:
    """Insert the mount block after the Flask-app anchor. Returns True if changed."""
    src = path.read_text(encoding="utf-8")
    if MARKER_START in src:
        return False
    if ANCHOR not in src:
        raise ValueError(f"{path} 中找不到锚点 {ANCHOR!r}")
    new = src.replace(ANCHOR, ANCHOR + "\n" + MOUNT_BLOCK, 1)
    if not dry_run:
        path.write_text(new, encoding="utf-8")
    return True


def main(argv=None) -> int:
    argv = list(argv) if argv is not None else sys.argv[1:]
    dry = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    target = argv[0] if argv else DEFAULT_TARGET
    p = Path(target)
    try:
        changed = apply(p, dry_run=dry)
    except ValueError as e:
        print(f"!! {e}", file=sys.stderr)
        return 1
    if changed:
        print(("将应用" if dry else "已应用") + f" 挂载补丁: {p}")
    else:
        print(f"已是补丁状态（无改动）: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
