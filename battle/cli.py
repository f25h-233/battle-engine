#!/usr/bin/env python3
"""battle — D&D 5e combat engine CLI (the DM's channel).

Every subcommand loads the encounter for the campaign, acts, saves.
"""

from __future__ import annotations
import argparse
import json
import os
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from .core import dice, monster_parser, persistence as P, resolution as R, srd
from .core.models import ActionError, Actor, AttackSpec, Encounter

def _campaign_dir(name: str) -> Path:
    # 每次调用读取 env（而非导入期绑定）：测试用 monkeypatch DND_CAMPAIGN_ROOT 才不会失效
    root = Path(os.environ.get("DND_CAMPAIGN_ROOT", str(Path.home() / ".claude" / "dnd")))
    d = root / "campaigns" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load(campaign: str) -> Encounter:
    enc = P.load_encounter(_campaign_dir(campaign))
    if enc is None:
        enc = Encounter(campaign=campaign, width=10, height=10)
    return enc


def _save(enc: Encounter) -> None:
    P.save_encounter(enc, _campaign_dir(enc.campaign))


def _print_result(r) -> None:
    for line in r.lines:
        print(f"  {line}")
    for err in r.errors:
        print(f"  !! {err}")


def _parse_pos(text: str):
    m = re.match(r"\(?(\d+)[,，](\d+)\)?$", text.strip())
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return None


# ── subcommands ──────────────────────────────────────────────────

def cmd_create(args) -> Encounter:
    m = re.match(r"(\d+)x(\d+)$", args.map)
    if not m:
        raise ActionError(f"地图格式错误: {args.map}（应为 20x15）")
    enc = Encounter(campaign=args.campaign, width=int(m.group(1)),
                    height=int(m.group(2)), ruleset=args.ruleset)
    _save(enc)
    print(f"创建战斗 {enc.campaign}（{enc.map.width}x{enc.map.height}，规则集 {enc.ruleset}）")
    return enc


def cmd_add_monster(enc: Encounter, args) -> None:
    entry = srd.find_monster(args.monster)
    if entry is None:
        raise ActionError(f"SRD 中找不到怪物: {args.monster}")
    actor = srd.monster_to_actor(entry)
    for i in range(1, args.count + 1):
        name = args.name if args.count == 1 else f"{args.name}-{i}"
        cid = enc.add_combatant(actor, x=0, y=0, cid=name)
        print(f"  + {cid}（{actor.name}，AC {actor.ac}，HP {actor.max_hp}）"
              + (f"，攻击: {', '.join(a.name for a in actor.attacks)}" if actor.attacks else "（无解析攻击，可 cast/set-hp 手动处理）"))
    _save(enc)


def cmd_add_player(enc: Encounter, args) -> None:
    attacks = []
    for spec in args.attack or []:
        # "短弓:+5:1d6+3:穿刺:80/320"
        parts = spec.split(":")
        if len(parts) >= 4:
            rng = (5, 0)
            if len(parts) >= 5 and "/" in parts[4]:
                s, l = parts[4].split("/")
                rng = (int(s), int(l))
            elif len(parts) >= 5:
                rng = (int(parts[4]), 0)
            attacks.append(AttackSpec(name=parts[0], attack_bonus=int(parts[1]),
                                      damage=parts[2], damage_type=parts[3], range_ft=rng))
    actor = Actor(name=args.name, kind="pc", ac=args.ac, max_hp=args.hp,
                  speed_ft=args.speed, dex_mod=args.dex_mod, attacks=attacks)
    cid = enc.add_combatant(actor, x=args.x, y=args.y, cid=args.name)
    print(f"  + {cid}（AC {actor.ac}，HP {actor.max_hp}，速度 {actor.speed_ft}ft）")
    _save(enc)


def cmd_place(enc: Encounter, args) -> None:
    c = enc.combatants[args.name.lower().replace(" ", "")]
    c.x, c.y = args.x, args.y
    print(f"  {c.id} → ({c.x},{c.y})")
    _save(enc)


def cmd_waypoint(enc: Encounter, args) -> None:
    if args.action == "add":
        enc.add_waypoint(args.name, args.pos)
        print(f"  地标 {args.name} = {args.pos}")
    else:
        for k, v in enc.waypoints.items():
            print(f"  {k}: {v}")
    _save(enc)


def cmd_init(enc: Encounter) -> None:
    ordered = enc.roll_initiative()
    for c in ordered:
        print(f"  {c.id}: d20 + {c.actor.dex_mod} = {c.initiative}")
    _save(enc)


def cmd_start(enc: Encounter) -> None:
    enc.start_combat()
    cur = enc.current()
    print(f"战斗开始 —— 第 1 回合，先攻第一位: {cur.id}")
    _save(enc)


def cmd_attack(enc: Encounter, args) -> None:
    att = enc.combatants[args.actor.lower().replace(" ", "")]
    attack = att.actor.attack(args.attack) if args.attack else None
    r = R.resolve_attack(enc, att.id, args.target.lower().replace(" ", ""),
                         attack, injected_d20=args.inject,
                         advantage=args.advantage, force=args.force)
    _print_result(r)
    _save(enc)


def cmd_cast(enc: Encounter, args) -> None:
    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    r = R.resolve_spell(enc, args.actor.lower().replace(" ", ""), args.name,
                        [t.lower().replace(" ", "") for t in targets],
                        dc=args.dc, stat=args.stat, damage=args.dmg,
                        damage_type=args.type)
    _print_result(r)
    _save(enc)


def cmd_move(enc: Encounter, args) -> None:
    dest = _parse_pos(args.to) or args.to
    r = R.resolve_move(enc, args.actor.lower().replace(" ", ""), dest,
                       force=args.force)
    _print_result(r)
    _save(enc)


def cmd_dash(enc: Encounter, args) -> None:
    dest = _parse_pos(args.to) or args.to
    r = R.resolve_dash(enc, args.actor.lower().replace(" ", ""), dest)
    _print_result(r)
    _save(enc)


def cmd_dodge(enc: Encounter, args) -> None:
    r = R.resolve_dodge(enc, args.actor.lower().replace(" ", ""))
    _print_result(r)
    _save(enc)


def cmd_disengage(enc: Encounter, args) -> None:
    r = R.resolve_disengage(enc, args.actor.lower().replace(" ", ""))
    _print_result(r)
    _save(enc)


def cmd_save(enc: Encounter, args) -> None:
    r = R.resolve_save(enc, args.actor.lower().replace(" ", ""), args.dc,
                       args.stat, injected_d20=args.inject)
    _print_result(r)
    _save(enc)


def cmd_death_save(enc: Encounter, args) -> None:
    r = R.resolve_death_save(enc, args.actor.lower().replace(" ", ""),
                             injected_d20=args.inject)
    _print_result(r)
    _save(enc)


def cmd_set_hp(enc: Encounter, args) -> None:
    c = enc.combatants[args.actor.lower().replace(" ", "")]
    c.hp = max(0, min(args.hp, c.actor.max_hp))
    if args.temp is not None:
        c.temp_hp = max(0, args.temp)
    if c.hp > 0 and "unconscious" in c.conditions:
        c.conditions.remove("unconscious")
        c.death_saves = {"successes": 0, "failures": 0, "stable": False}
    print(f"  {c.id}: HP {c.hp}/{c.actor.max_hp}" + (f"，临时 {c.temp_hp}" if c.temp_hp else ""))
    _save(enc)


def cmd_cond(enc: Encounter, args) -> None:
    c = enc.combatants[args.actor.lower().replace(" ", "")]
    cond = args.condition.lower()
    if args.action == "add" and cond not in c.conditions:
        c.conditions.append(cond)
    elif args.action == "remove" and cond in c.conditions:
        c.conditions.remove(cond)
    elif args.action == "clear":
        c.conditions = []
    print(f"  {c.id} 条件: {', '.join(c.conditions) or '—'}")
    _save(enc)


def cmd_distance(enc: Encounter, args) -> None:
    a = enc.combatants[args.a.lower().replace(" ", "")]
    b = enc.combatants[args.b.lower().replace(" ", "")]
    print(f"  {a.id} ↔ {b.id}: {enc.map.distance_ft(a, b)}ft")


def cmd_state(enc: Encounter, args) -> None:
    if args.json:
        print(json.dumps(enc.to_dict(), ensure_ascii=False, indent=2))
        return
    print(f"战斗: {enc.campaign}（规则集 {enc.ruleset}，状态 {enc.status}）")
    print(f"地图: {enc.map.width}x{enc.map.height}，第 {enc.round} 回合，先攻顺序: {', '.join(enc.turn_order) or '—'}")
    cur = enc.current()
    for c in enc.combatants.values():
        marker = "►" if cur and cur.id == c.id else " "
        conds = ", ".join(c.conditions) or "—"
        print(f"  {marker} {c.id:<12} HP {c.hp}/{c.actor.max_hp}"
              f"（临时 {c.temp_hp}）AC {c.actor.ac} 位置 ({c.x},{c.y})"
              f" 先攻 {c.initiative or '—'} 移动剩 {c.movement_left_ft}ft"
              f" 条件[{conds}]")


def cmd_log(enc: Encounter, args) -> None:
    entries = enc.log if args.round is None else [e for e in enc.log if e.get("round") == args.round]
    for e in entries:
        lines = e.get("lines", [])
        head = f"[R{e.get('round')}] {e.get('actor') or ''} {e.get('action')}"
        print(head)
        for line in lines:
            print(f"    {line}")


def cmd_npc_act(enc: Encounter, spec: str) -> None:
    """'哥布林1:attack 星沢羽; 哥布林2:move 门口; 哥布林3:dash 门口'"""
    tokens = [t.strip() for t in spec.split(";") if t.strip()]
    for token in tokens:
        m = re.match(r"^([^:]+):(\S+)\s*(.*)$", token)
        if not m:
            print(f"  !! 无法解析: {token}")
            continue
        actor, action, rest = m.group(1).strip(), m.group(2), m.group(3).strip()
        cid = actor.lower().replace(" ", "")
        try:
            if action == "attack":
                tgt = rest.split()[0] if rest else None
                if not tgt:
                    print(f"  !! {actor}: attack 需要目标")
                    continue
                r = R.resolve_attack(enc, cid, tgt.lower().replace(" ", ""))
            elif action == "move":
                r = R.resolve_move(enc, cid, _parse_pos(rest) or rest)
            elif action == "dash":
                r = R.resolve_dash(enc, cid, _parse_pos(rest) or rest)
            elif action == "dodge":
                r = R.resolve_dodge(enc, cid)
            elif action == "disengage":
                r = R.resolve_disengage(enc, cid)
            else:
                print(f"  !! {actor}: 未知动作 {action}")
                continue
            _print_result(r)
        except ActionError as e:
            print(f"  !! {actor}: {e}")
    _save(enc)


def cmd_undo(enc: Encounter) -> None:
    if enc.pop_undo():
        print("  已回滚上一步")
    else:
        print("  没有可回滚的操作")
    _save(enc)


def cmd_next_turn(enc: Encounter) -> None:
    enc.next_turn()
    cur = enc.current()
    print(f"  第 {enc.round} 回合 —— 轮到: {cur.id}")
    _save(enc)


def cmd_end(enc: Encounter) -> None:
    enc.end()
    print(f"  战斗结束。共 {enc.round} 回合，{len(enc.log)} 条动作日志。")
    _save(enc)


def cmd_recover(args) -> None:
    enc = P.restore_backup(_campaign_dir(args.campaign))
    print(f"  已从 .bak 恢复战斗（状态 {enc.status}，{len(enc.combatants)} 名战斗员）")


# ── parser ───────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="battle", description="D&D 5e combat engine")
    sub = p.add_subparsers(dest="cmd")

    def c(name, help_, **kw):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("-c", "--campaign", default="", help="战役名（缺省用 DND_CAMPAIGN_ROOT 或 ~/.claude/dnd）")
        return sp

    s = c("create", "创建战斗")
    s.add_argument("--map", required=True, help="如 20x15")
    s.add_argument("--ruleset", default="2014", choices=["2014", "2024"])

    s = c("add-monster", "从 SRD 添加怪物")
    s.add_argument("--name", required=True)
    s.add_argument("--monster", required=True, help="SRD 怪物名（如 goblin）")
    s.add_argument("--count", type=int, default=1)

    s = c("add-player", "添加玩家（手动数值；人物卡解析在 M3）")
    s.add_argument("--name", required=True)
    s.add_argument("--ac", type=int, required=True)
    s.add_argument("--hp", type=int, required=True)
    s.add_argument("--speed", type=int, default=30)
    s.add_argument("--dex-mod", type=int, default=0)
    s.add_argument("--x", type=int, default=0)
    s.add_argument("--y", type=int, default=0)
    s.add_argument("--attack", action="append", default=[], help="名称:加值:伤害骰:类型:射程(短/长)")

    s = c("place", "摆放 token")
    s.add_argument("--name", required=True)
    s.add_argument("--x", type=int, required=True)
    s.add_argument("--y", type=int, required=True)

    s = c("waypoint", "地标管理")
    s.add_argument("action", choices=["add", "list"])
    s.add_argument("name", nargs="?")
    s.add_argument("pos", nargs="?", help="如 12,3")

    c("init", "掷先攻")
    c("start", "开始战斗（第 1 回合）")

    s = c("attack", "攻击结算")
    s.add_argument("--actor", required=True)
    s.add_argument("--target", required=True)
    s.add_argument("--attack", help="攻击名（缺省用第一个）")
    s.add_argument("--inject", type=int, help="注入 d20 结果（玩家手动掷）")
    s.add_argument("--advantage", choices=["advantage", "disadvantage"])
    s.add_argument("--force", action="store_true", help="跳过状态门（DM 剧情用）")

    s = c("cast", "施法结算（M1: 豁免型；M3 补 AoE 几何）")
    s.add_argument("--actor", required=True)
    s.add_argument("--name", required=True, help="法术名（日志用）")
    s.add_argument("--targets", required=True, help="逗号分隔目标 id")
    s.add_argument("--dc", type=int)
    s.add_argument("--stat", default="dex")
    s.add_argument("--dmg", help="如 3d6")
    s.add_argument("--type", default="", help="伤害类型")

    s = c("move", "移动")
    s.add_argument("--actor", required=True)
    s.add_argument("--to", required=True, help="坐标 12,3 或地标名")
    s.add_argument("--force", action="store_true")

    for name in ("dash", "dodge", "disengage"):
        s = c(name, f"{name} 动作")
        s.add_argument("--actor", required=True)
        if name == "dash":
            s.add_argument("--to", required=True, help="坐标或地标名")

    s = c("save", "豁免")
    s.add_argument("--actor", required=True)
    s.add_argument("--dc", type=int, required=True)
    s.add_argument("--stat", required=True, choices=["str", "dex", "con", "int", "wis", "cha"])
    s.add_argument("--inject", type=int)

    s = c("death-save", "死亡豁免")
    s.add_argument("--actor", required=True)
    s.add_argument("--inject", type=int)

    s = c("set-hp", "DM 修正 HP")
    s.add_argument("--actor", required=True)
    s.add_argument("--hp", type=int, required=True)
    s.add_argument("--temp", type=int)

    s = c("cond", "条件管理")
    s.add_argument("action", choices=["add", "remove", "clear"])
    s.add_argument("actor")
    s.add_argument("condition", nargs="?")

    s = c("distance", "查询距离")
    s.add_argument("a")
    s.add_argument("b")

    s = c("state", "打印战斗状态")
    s.add_argument("--json", action="store_true")

    s = c("log", "动作日志")
    s.add_argument("--round", type=int)

    s = c("npc-act", "NPC 批量意图: '哥布林1:attack 星沢羽; 哥布林2:move 门口'")
    s.add_argument("spec")

    c("undo", "回滚上一步")
    c("next-turn", "推进回合")
    c("end", "结束战斗")
    c("recover", "从 .bak 恢复战斗")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not args.cmd:
        build_parser().print_help()
        return 1
    if args.cmd == "recover":
        cmd_recover(args)
        return 0
    if not args.campaign:
        print("错误: 缺少 -c/--campaign", file=sys.stderr)
        return 1
    enc = _load(args.campaign)
    try:
        {"create": lambda: cmd_create(args),
         "add-monster": lambda: cmd_add_monster(enc, args),
         "add-player": lambda: cmd_add_player(enc, args),
         "place": lambda: cmd_place(enc, args),
         "waypoint": lambda: cmd_waypoint(enc, args),
         "init": lambda: cmd_init(enc),
         "start": lambda: cmd_start(enc),
         "attack": lambda: cmd_attack(enc, args),
         "cast": lambda: cmd_cast(enc, args),
         "move": lambda: cmd_move(enc, args),
         "dash": lambda: cmd_dash(enc, args),
         "dodge": lambda: cmd_dodge(enc, args),
         "disengage": lambda: cmd_disengage(enc, args),
         "save": lambda: cmd_save(enc, args),
         "death-save": lambda: cmd_death_save(enc, args),
         "set-hp": lambda: cmd_set_hp(enc, args),
         "cond": lambda: cmd_cond(enc, args),
         "distance": lambda: cmd_distance(enc, args),
         "state": lambda: cmd_state(enc, args),
         "log": lambda: cmd_log(enc, args),
         "npc-act": lambda: cmd_npc_act(enc, args.spec),
         "undo": lambda: cmd_undo(enc),
         "next-turn": lambda: cmd_next_turn(enc),
         "end": lambda: cmd_end(enc),
         }[args.cmd]()
    except ActionError as e:
        print(f"!! {e}", file=sys.stderr)
        return 1
    except KeyError as e:
        print(f"!! 战斗员不存在: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
