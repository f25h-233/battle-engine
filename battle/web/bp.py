"""Battle panel blueprint — mounted into the display app (or any Flask app).

REST (spec §7.2):
  GET  /battle/          面板页面
  GET  /battle/state     前端状态快照
  POST /battle/action    玩家动作（攻击/施法/移动/冲刺/闪避/脱离/死亡豁免/结束回合）
  POST /battle/roll      服务器掷骰
  GET  /battle/stream    SSE 推送（轮询 battle.json mtime）

安全：LAN 模式下 app.config["BATTLE_TOKEN"] 非空 → POST 要求 X-DND-Token；
localhost（token 为空）免检 —— 与显示端 _token_ok 语义一致。
"""

from __future__ import annotations
import hmac
import json
import os
import time
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, render_template, request

from ..core import dice, persistence as P, resolution as R
from ..core.models import ActionError, Encounter

battle_bp = Blueprint("battle", __name__, url_prefix="/battle",
                      template_folder="templates", static_folder="static")

LOG_LIMIT = 50          # 状态 payload 携带的日志条数上限
_POLL_SECONDS = 1.0     # SSE 轮询间隔（测试中 monkeypatch 缩短）


def _campaign_root() -> Path:
    return Path(os.environ.get("DND_CAMPAIGN_ROOT", str(Path.home() / ".claude" / "dnd")))


def _campaign_name() -> str:
    """战役名解析：BATTLE_CAMPAIGN 环境变量优先，否则显示端 .campaign 运行时文件。"""
    env = os.environ.get("BATTLE_CAMPAIGN", "").strip()
    if env:
        return env
    camp_file = current_app.config.get("BATTLE_CAMPAIGN_FILE") or ""
    if camp_file and os.path.exists(camp_file):
        name = Path(camp_file).read_text(encoding="utf-8").strip()
        if name:
            return name
    return ""


def _campaign_dir() -> Path:
    name = _campaign_name()
    if not name:
        raise ActionError("未设置战役：请设置环境变量 BATTLE_CAMPAIGN，"
                          "或显示端已载入战役（.campaign 文件）")
    return _campaign_root() / "campaigns" / name


def _load() -> Encounter:
    enc = P.load_encounter(_campaign_dir())
    if enc is None:
        raise ActionError("该战役没有战斗（battle.json 不存在）——"
                          "先用 CLI: python -m battle create -c <战役名>")
    return enc


def _save(enc: Encounter) -> None:
    P.save_encounter(enc, _campaign_dir())


def _token_ok() -> bool:
    token = current_app.config.get("BATTLE_TOKEN") or ""
    if not token:
        return True          # localhost 模式
    provided = request.headers.get("X-DND-Token", "")
    return hmac.compare_digest(provided, token)


def _state_payload(enc: Encounter) -> dict:
    """前端状态契约：to_dict 去掉 undo_stack，log 截断，combatants 扁平化。"""
    data = enc.to_dict()
    data.pop("undo_stack", None)
    data["log"] = enc.log[-LOG_LIMIT:]
    flat = {}
    for k, c in enc.combatants.items():
        a = c.actor
        flat[k] = {
            "id": c.id, "name": a.name, "kind": a.kind,
            "x": c.x, "y": c.y, "hp": c.hp, "max_hp": c.max_hp,
            "temp_hp": c.temp_hp, "ac": a.ac, "speed_ft": a.speed_ft,
            "conditions": c.conditions, "concentration": c.concentration,
            "initiative": c.initiative, "acted": c.acted,
            "bonus_acted": c.bonus_acted, "reaction_used": c.reaction_used,
            "movement_left_ft": c.movement_left_ft,
            "death_saves": c.death_saves,
            "attacks": [{
                "name": a.name, "kind": a.kind, "attack_bonus": a.attack_bonus,
                "range_ft": list(a.range_ft), "damage": a.damage,
                "damage_type": a.damage_type, "save_dc": a.save_dc,
                "save_stat": a.save_stat, "note": a.note,
            } for a in a.attacks],
        }
    data["combatants"] = flat
    return data


@battle_bp.route("/")
def index():
    return render_template("battle_panel.html",
                           lan_token=current_app.config.get("BATTLE_TOKEN") or "")


@battle_bp.route("/state")
def state():
    try:
        enc = _load()
    except ActionError as e:
        return jsonify({"ok": False, "error": str(e)}), 404
    return jsonify({"ok": True, "state": _state_payload(enc)})


@battle_bp.route("/action", methods=["POST"])
def action():
    """动作入口（骨架：仅令牌门禁；动作分派在后续任务实现）。"""
    if not _token_ok():
        return jsonify({"ok": False, "error": "令牌无效"}), 401
    return jsonify({"ok": True, "error": None})
