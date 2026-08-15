#!/usr/bin/env python3
"""测试战斗一键启动：莫德凯 vs 3 只哥布林。

双击 test-battle.bat（或 `python test_battle.py`）：
  1. 构造测试战斗（真实 SRD 哥布林 ×3 + 莫德凯，莫德凯先手）写盘
  2. 检查显示端挂载补丁，缺失则自动应用（integration/mount_display_app.py）
  3. 启动显示端 Flask 进程（测试战役根 .test-campaigns/，不碰真实战役）
  4. 打开浏览器 http://localhost:5001/battle/
  5. Ctrl+C 退出，自动关闭显示端

DM 可在另一个终端用 CLI 操作同一场战斗：
  set DND_CAMPAIGN_ROOT=%CD%/.test-campaigns
  python -m battle state -c mordekai-vs-goblins
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT))

from battle.core import persistence as P, srd  # noqa: E402
from battle.core.models import Actor, AttackSpec, Encounter  # noqa: E402
from battle.integration import mount_display_app  # noqa: E402

CAMPAIGN = "mordekai-vs-goblins"
MAP_W, MAP_H = 20, 15
CAMPAIGN_ROOT = REPO_ROOT / ".test-campaigns"
URL = "http://localhost:5001/battle/"

# 显示端位置（M2 挂载补丁打在这里）；可用环境变量 DND_DISPLAY_APP 覆盖
DISPLAY_APP = Path(os.environ.get(
    "DND_DISPLAY_APP",
    r"C:\Users\qwe13\.claude\plugins\marketplaces"
    r"\neuralinitiative-claude-dnd-skill\skills\dnd\display\dnd-display-app.py",
))


def build_mordekai() -> Actor:
    return Actor(
        name="莫德凯", kind="pc", ac=18, max_hp=27, speed_ft=30, dex_mod=2,
        attacks=[AttackSpec(name="长剑", kind="weapon", attack_bonus=5,
                            range_ft=(5, 0), damage="1d8+3", damage_type="挥砍")],
    )


def build_goblins(count: int = 3) -> list:
    """真实 SRD goblin ×N，中文名。"""
    entry = srd.find_monster("goblin")
    if entry is None:
        raise FileNotFoundError(
            "SRD 中找不到 goblin——设置 BATTLE_SRD_PATH 指向 dnd5e_srd.json")
    actors = []
    for i in range(1, count + 1):
        actor = srd.monster_to_actor(entry)
        actor.name = f"哥布林{i}"
        actors.append(actor)
    return actors


def setup_encounter() -> Encounter:
    """构造战斗：莫德凯(3,3)，哥布林1 相邻(4,3)，哥布林2/3 远处；莫德凯先手。"""
    enc = Encounter(campaign=CAMPAIGN, width=MAP_W, height=MAP_H)
    enc.add_combatant(build_mordekai(), x=3, y=3, cid="莫德凯")
    for actor, pos in zip(build_goblins(), [(4, 3), (10, 6), (12, 8)]):
        enc.add_combatant(actor, x=pos[0], y=pos[1], cid=actor.name)
    enc.add_waypoint("门口", (0, 7))
    enc.roll_initiative()
    enc.turn_order.remove("莫德凯")      # 演示可控：莫德凯先手
    enc.turn_order.insert(0, "莫德凯")
    enc.start_combat()
    return enc


def ensure_mount() -> None:
    """显示端缺挂载补丁时自动应用（插件更新后会丢）。"""
    if not DISPLAY_APP.exists():
        raise FileNotFoundError(
            f"找不到显示端: {DISPLAY_APP}\n可用环境变量 DND_DISPLAY_APP 指定路径")
    if mount_display_app.MARKER_START in DISPLAY_APP.read_text(encoding="utf-8"):
        return
    mount_display_app.apply(DISPLAY_APP)
    print(f"  [补丁] 显示端缺少 battle-engine 挂载——已自动应用（{DISPLAY_APP.name}）")


def port_free(port: int = 5001) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def wait_ready(proc: subprocess.Popen, timeout: float = 30.0) -> bool:
    """轮询 /battle/state：200 且战役名正确 = 显示端就绪且蓝图已挂载。"""
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(URL + "state", timeout=2) as r:
                data = json.load(r)
                if data.get("ok") and data.get("state", {}).get("campaign") == CAMPAIGN:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    print("=== 测试战斗：莫德凯 vs 3 只哥布林 ===")
    if not port_free():
        print("!! 端口 5001 已被占用——请先关闭正在运行的显示端再重试")
        return 1

    CAMPAIGN_ROOT.mkdir(parents=True, exist_ok=True)
    ensure_mount()

    enc = setup_encounter()
    camp_dir = CAMPAIGN_ROOT / "campaigns" / CAMPAIGN   # 引擎路径惯例：<root>/campaigns/<name>
    P.save_encounter(enc, camp_dir)
    cur = enc.current()
    print(f"  [战斗] {CAMPAIGN} 就绪：{len(enc.combatants)} 名战斗员，"
          f"当前行动者 {cur.id}（先攻 {cur.initiative}）")
    print(f"  [战斗] 战役根（CLI 用）: {CAMPAIGN_ROOT}")

    env = dict(os.environ)
    env["BATTLE_ENGINE_DIR"] = str(REPO_ROOT)
    env["DND_CAMPAIGN_ROOT"] = str(CAMPAIGN_ROOT)
    env["BATTLE_CAMPAIGN"] = CAMPAIGN
    env.pop("DND_RUNTIME_DIR", None)      # 运行时文件随测试战役根走，保持隔离

    log_file = CAMPAIGN_ROOT / "display.log"
    proc = subprocess.Popen(
        [sys.executable, str(DISPLAY_APP)],
        cwd=str(DISPLAY_APP.parent),
        env=env,
        stdout=open(log_file, "w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    print(f"  [显示端] 启动中（PID {proc.pid}，日志 {log_file}）…")

    if not wait_ready(proc):
        print("!! 显示端未就绪（30s 超时）——日志末尾：")
        try:
            print(log_file.read_text(encoding="utf-8", errors="replace")[-2000:])
        except OSError:
            pass
        proc.terminate()
        return 1

    print(f"  [显示端] 就绪：{URL}")
    webbrowser.open(URL)
    print(f"  玩家面板已打开。DM 用 CLI：python -m battle state -c {CAMPAIGN}")
    print("  Ctrl+C 退出（自动关闭显示端）")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  正在关闭显示端…")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
