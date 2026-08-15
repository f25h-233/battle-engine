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
        try:
            name = Path(camp_file).read_text(encoding="utf-8").strip()
        except (OSError, ValueError):   # 读不了/GBK 等历史编码 → 视为未设置
            name = ""
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
    try:
        enc = P.load_encounter(_campaign_dir())
    except ValueError:
        raise ActionError("battle.json 损坏——可用 CLI: battle recover 从 .bak 恢复")
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
            "initiative": c.initiative, "initiative_d20": c.initiative_d20,
            "acted": c.acted,
            "bonus_acted": c.bonus_acted, "reaction_used": c.reaction_used,
            "movement_left_ft": c.movement_left_ft,
            "dodging": c.dodging, "disengaged": c.disengaged,
            "death_saves": c.death_saves,
            "attacks": [{
                "name": a.name, "kind": a.kind, "attack_bonus": a.attack_bonus,
                "range_ft": list(a.range_ft), "damage": a.damage,
                "damage_type": a.damage_type, "save_dc": a.save_dc,
                "save_stat": a.save_stat, "note": a.note,
                "aoe_radius_ft": a.aoe_radius_ft,
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
    if not _token_ok():
        return jsonify({"ok": False, "error": "令牌无效"}), 401
    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "JSON 请求体无效"}), 400
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "JSON 请求体无效"}), 400
    try:
        enc = _load()
        result = _dispatch_action(enc, body)
        _save(enc)
        return jsonify({
            "ok": result.ok,
            "lines": result.lines,
            "errors": result.errors,
            "hp_before": result.hp_before,
            "hp_after": result.hp_after,
            "error": None,
            "state": _state_payload(enc),
        })
    except ActionError as e:
        payload = None
        try:
            payload = _state_payload(_load())
        except ActionError:
            pass
        return jsonify({"ok": False, "error": str(e), "state": payload}), 400
    except ValueError as e:            # 注入 d20 越界等（dice 层校验）
        return jsonify({"ok": False, "error": str(e), "state": None}), 400
    except KeyError as e:
        return jsonify({"ok": False, "error": f"战斗员不存在: {e}",
                        "state": None}), 404


@battle_bp.route("/roll", methods=["POST"])
def roll():
    """服务器掷骰（spec §7.2）。手动掷不经过此端点——注入走 /battle/action。"""
    if not _token_ok():
        return jsonify({"ok": False, "error": "令牌无效"}), 401
    try:
        body = request.get_json(force=True) or {}
    except Exception:
        return jsonify({"ok": False, "error": "JSON 请求体无效"}), 400
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "JSON 请求体无效"}), 400
    spec = str(body.get("spec", "1d20")).strip()
    try:
        if spec == "1d20":
            r = dice.roll_d20(mod=int(body.get("mod", 0) or 0),
                              advantage=body.get("advantage"))
            return jsonify({"ok": True, "rolls": r["rolls"], "total": r["total"],
                            "crit": r["crit"], "fumble": r["fumble"]})
        total, rolls = dice.roll_dice(spec)
        return jsonify({"ok": True, "rolls": rolls, "total": total,
                        "crit": False, "fumble": False})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


def _stream_events(camp_dir, interval: float = _POLL_SECONDS):
    """SSE 事件源：首帧立即推当前状态，此后 battle.json mtime 变化才推新状态，
    否则发 ': keepalive' 心跳字符串。文件缺失/损坏推 {"error": ...}（spec §10：
    报错，绝不静默清空）。"""
    battle_file = P.battle_path(camp_dir)
    last_mtime = object()                 # 哨兵：保证首帧必发
    while True:
        try:
            mtime = battle_file.stat().st_mtime_ns
        except OSError:
            mtime = None
        if mtime != last_mtime:
            last_mtime = mtime
            try:
                enc = P.load_encounter(camp_dir)
                if enc is None:
                    yield {"state": None, "error": "无 battle.json（战斗文件不存在）"}
                else:
                    yield {"state": _state_payload(enc)}
            except (ValueError, OSError) as e:   # 损坏→ValueError；stat/读→OSError
                yield {"error": f"battle.json 损坏: {e}（可用 CLI: battle recover 从 .bak 恢复）"}
        else:
            yield ": keepalive"
        time.sleep(interval)


@battle_bp.route("/stream")
def stream():
    try:
        camp_dir = _campaign_dir()
    except ActionError as e:
        return jsonify({"ok": False, "error": str(e)}), 404

    def generate():
        for ev in _stream_events(camp_dir):
            if isinstance(ev, str):
                yield ev + "\n\n"
            else:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return Response(generate(), mimetype="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Transfer-Encoding": "chunked",
        "Connection": "keep-alive",
    })


def _dispatch_action(enc: Encounter, body: dict) -> R.ResolutionResult:
    """动作分派：end_turn 无需 actor；其余需 actor + 战斗员在场。

    actor id 归一化与 CLI 相同：lower + 去空格（中文名即原样）。
    """
    action = body.get("action", "")
    injected = body.get("injected") or {}
    inj_d20 = injected.get("d20")
    inj_dmg = None
    if isinstance(injected.get("damage"), list):
        inj_dmg = [int(v) for v in injected["damage"]
                   if isinstance(v, (int, float))]

    if action == "undo":
        if enc.pop_undo():
            r = R.ResolutionResult()
            r.add("已回滚上一步")
            return r
        raise ActionError("没有可回滚的操作")

    if action == "end_turn":
        enc.next_turn()
        r = R.ResolutionResult()
        cur = enc.current()
        r.add(f"第 {enc.round} 回合 —— 轮到: {cur.id if cur else '—'}")
        return r

    cid = str(body.get("actor", "")).strip().lower().replace(" ", "")
    if not cid:
        raise ActionError("缺少 actor（你是谁？）")
    if cid not in enc.combatants:
        raise KeyError(cid)                 # 404：与目标不存在同一错误码

    if action == "attack":
        tgt = str(body.get("target", "")).strip().lower().replace(" ", "")
        if not tgt:
            raise ActionError("攻击需要目标")
        atk = _resolve_attack_spec(enc, cid, body.get("attack"))
        return R.resolve_attack(enc, cid, tgt, atk,
                                injected_d20=inj_d20, injected_damage=inj_dmg,
                                advantage=body.get("advantage"),
                                force=bool(body.get("force")))

    if action == "cast":
        atk = _resolve_attack_spec(enc, cid, body.get("attack"))
        spell = body.get("spell") or (atk.name if atk else "施法")
        center = None
        if body.get("center") is not None:
            center = _coords(body.get("center"))
        if center is not None:
            radius = body.get("radius")
            return R.resolve_spell(enc, cid, spell, [], attack=atk,
                                   center=center,
                                   radius_ft=int(radius) if radius is not None else None,
                                   injected_d20=inj_d20, injected_damage=inj_dmg,
                                   force=bool(body.get("force")))
        tgt = str(body.get("target", "")).strip().lower().replace(" ", "")
        if not tgt:
            raise ActionError("施法需要目标")
        if atk is not None and atk.save_dc is not None:
            # 豁免型法术（单目标）：走豁免路径，不再委托攻击掷骰
            return R.resolve_spell(enc, cid, spell, [tgt], dc=atk.save_dc,
                                   stat=atk.save_stat or "dex", damage=atk.damage,
                                   damage_type=atk.damage_type,
                                   injected_d20=inj_d20, injected_damage=inj_dmg,
                                   force=bool(body.get("force")))
        return R.resolve_spell(enc, cid, spell, [tgt], attack=atk,
                               injected_d20=inj_d20, injected_damage=inj_dmg,
                               force=bool(body.get("force")))

    if action == "move":
        return R.resolve_move(enc, cid, _coords(body.get("to")),
                              force=bool(body.get("force")))

    if action == "dash":
        return R.resolve_dash(enc, cid, _coords(body.get("to")))

    if action == "dodge":
        return R.resolve_dodge(enc, cid)

    if action == "disengage":
        return R.resolve_disengage(enc, cid)

    if action == "death_save":
        return R.resolve_death_save(enc, cid, injected_d20=inj_d20)

    raise ActionError(f"未知动作: {action}")


def _resolve_attack_spec(enc: Encounter, cid: str, name):
    """按名字取攻击/法术攻击；未指定 → 第一个（core 行为）；找不到 → 报因。"""
    actor = enc.combatants[cid].actor
    if not name:
        return None
    atk = actor.attack(name)
    if atk is None:
        raise ActionError(f"{cid} 没有攻击 {name}")
    return atk


def _coords(value):
    if isinstance(value, list) and len(value) == 2:
        try:
            return (int(value[0]), int(value[1]))
        except (TypeError, ValueError):
            pass
    raise ActionError("to 参数需要 [x, y] 整数坐标")
