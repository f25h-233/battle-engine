#!/usr/bin/env bash
# 决斗测试场景：莫德凯 vs 莉安娜，中间 4x5 箱子堆
#
# 用法:
#   bash scripts/duel-demo.sh                # 交互模式：起服务 + 打开浏览器，回车停止
#   bash scripts/duel-demo.sh --smoke [PORT] # 自测模式：起服务 → curl 验证 → 自动清理
#
# 场景（20x15 格，5ft/格）:
#   莫德凯 (2,7)  ←→  箱子堆 @ 中心 (10,7)，占地 4x5 格（约 x9-12, y5-9）  ←→  莉安娜 (17,7)
#   箱子堆 = 场地物体（kind=npc、速度 0、HP 999）：不进先攻、不参与回合、
#   不显示为可选身份；掩体/遮挡由 DM 叙事判定（引擎简化网格无墙体/视线）。
#   战斗数据落在临时目录（DND_CAMPAIGN_ROOT=mktemp），退出即弃。

set -euo pipefail
cd "$(dirname "$0")/.."                      # 仓库根（脚本放 scripts/ 下）

CAMP=duel-test
PORT=5011
SMOKE=0
if [[ "${1:-}" == "--smoke" ]]; then
    SMOKE=1
    PORT="${2:-5011}"
fi
TOKEN=dueltok

# ── 1. 造场景 ───────────────────────────────────────────────
ROOT=$(mktemp -d)
export DND_CAMPAIGN_ROOT=$ROOT
export BATTLE_CAMPAIGN=$CAMP

echo "── 造场景: 莫德凯 vs 莉安娜（箱子堆居中 4x5 格）──"
python -m battle create -c $CAMP --map 20x15
python -m battle add-player -c $CAMP --name 莫德凯 --ac 18 --hp 27 --speed 30 \
  --dex-mod 0 --attack "长剑:+5:1d8+3:挥砍:5" --attack "短弓:+5:1d6+3:穿刺:80/320"
python -m battle add-player -c $CAMP --name 莉安娜 --ac 15 --hp 22 --speed 30 \
  --dex-mod 2 --attack "细剑:+4:1d8+2:穿刺:5"
python -m battle add-player -c $CAMP --name 箱子堆 --ac 10 --hp 999 --speed 0
python -m battle place -c $CAMP --name 莫德凯 --x 2 --y 7
python -m battle place -c $CAMP --name 莉安娜 --x 17 --y 7
python -m battle place -c $CAMP --name 箱子堆 --x 10 --y 7
python -m battle init -c $CAMP

# ── 2. 箱子堆降级为场地物体：kind→npc（不进 writeback/身份列表），移出先攻 ──
python - "$ROOT" "$CAMP" <<'EOF'
import json, os, sys
root = sys.argv[1]
camp = sys.argv[2]
p = os.path.join(root, "campaigns", camp, "battle.json")
d = json.loads(open(p, encoding="utf-8").read())
c = d["combatants"]["箱子堆"]
c["actor"]["kind"] = "npc"
d["turn_order"].remove("箱子堆")
open(p, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False))
print("  箱子堆: kind=npc、移出先攻（场地物体，占地 4x5 格 @ 中心 (10,7)）")
EOF

python -m battle start -c $CAMP
echo "── 场景确认 ──"
python -m battle state -c $CAMP | head -6

# ── 3. 起服务 ───────────────────────────────────────────────
python -m battle serve -c $CAMP --port $PORT --token $TOKEN &
SERVE_PID=$!
trap 'kill $SERVE_PID 2>/dev/null; echo "已停止服务"' EXIT

for i in $(seq 1 30); do
    curl -s http://localhost:$PORT/battle/state >/dev/null 2>&1 && break
    sleep 1
done

URL="http://localhost:$PORT/battle/"
echo "── 场景已就绪 ──────────────────────────────"
echo "玩家面板: $URL"
echo "POST 令牌: $TOKEN"
echo "战斗数据: $ROOT/campaigns/$CAMP/"

# ── 4a. smoke 自测 ─────────────────────────────────────────
if [[ $SMOKE == 1 ]]; then
    PASS=0
    # 页面 meta token 注入
    curl -s $URL | grep -q 'name="dnd-token"' && PASS=$((PASS+1))
    # state: 三战斗员 + 箱子堆不在先攻
    STATE=$(curl -s http://localhost:$PORT/battle/state)
    echo "$STATE" | python -c "
import json, sys
sys.stdout.reconfigure(encoding='utf-8')   # Windows GBK 控制台兼容
s = json.load(sys.stdin)['state']
assert s['status'] == 'combat_active', s['status']
assert set(s['combatants']) == {'莫德凯', '莉安娜', '箱子堆'}, list(s['combatants'])
assert '箱子堆' not in s['turn_order'], s['turn_order']
print('  state: combat_active, 战斗员 莫德凯/莉安娜/箱子堆, 箱子堆不在先攻 ✓')
" && PASS=$((PASS+1))
    # 真实攻击闭环：推进到莫德凯回合（随机先攻）→ 攻击莉安娜 → hp 变化
    for i in $(seq 1 10); do
        CUR=$(curl -s http://localhost:$PORT/battle/state | python -c "
import json, sys
s = json.load(sys.stdin)['state']
print(s['turn_order'][s['turn_index']])")
        [[ "$CUR" == "莫德凯" ]] && break
        # end_turn 无需 actor（bp 直接 next_turn）；纯 ASCII body 避免 Windows curl 中文损坏
        curl -s -X POST http://localhost:$PORT/battle/action \
            -H "Content-Type: application/json" -H "X-DND-Token: $TOKEN" \
            -d '{"action":"end_turn"}' >/dev/null
    done
    # 双方隔 75ft（箱子堆在中间）→ 长剑 5ft 够不到，用短弓（80ft 内）
    printf '{"action":"attack","actor":"莫德凯","target":"莉安娜","attack":"短弓","injected":{"d20":15}}' > "$ROOT/req.json"
    curl -s -X POST http://localhost:$PORT/battle/action \
        -H "Content-Type: application/json" -H "X-DND-Token: $TOKEN" \
        --data @"$ROOT/req.json" | python -c "
import json, sys
sys.stdout.reconfigure(encoding='utf-8')
j = json.load(sys.stdin)
hp = j['state']['combatants']['莉安娜']['hp']
assert j['ok'] and hp < 22, (j['ok'], hp, j.get('error'))
print(f'  莫德凯 短弓 攻击 莉安娜 → ok, 莉安娜 HP {hp}/22 ✓')
" && PASS=$((PASS+1))
    # 攻击后移动（动作已用 → 普通移动仍可用；5e 移动与动作独立——b2c16d6 回归保护）
    printf '{"action":"move","actor":"莫德凯","to":[3,7]}' > "$ROOT/move.json"
    curl -s -X POST http://localhost:$PORT/battle/action \
        -H "Content-Type: application/json" -H "X-DND-Token: $TOKEN" \
        --data @"$ROOT/move.json" | python -c "
import json, sys
sys.stdout.reconfigure(encoding='utf-8')
j = json.load(sys.stdin)
x = j['state']['combatants']['莫德凯']['x']
assert j['ok'] and x == 3, (j['ok'], x, j.get('error'))
print(f'  攻击后移动 → ok, 莫德凯 x={x}（移动 5ft）✓')
" && PASS=$((PASS+1))
    # 无 token → 401
    code=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:$PORT/battle/action \
        -H "Content-Type: application/json" -d '{"action":"undo"}')
    [[ "$code" == "401" ]] && PASS=$((PASS+1))
    echo "── smoke 完成: $PASS/5 断言通过 ──"
    [[ $PASS == 5 ]]
    exit $?
fi

# ── 4b. 交互模式：打开浏览器，回车停止 ─────────────────────
(cmd //c start "" "$URL" 2>/dev/null || explorer.exe "$URL" 2>/dev/null) || true
echo "（已尝试打开浏览器；也可手动访问上面的地址）"
echo "按回车停止服务…"
read -r _
