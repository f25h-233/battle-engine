# battle-engine — D&D 5e 战斗引擎（结算核心）

独立于 skill 可运行的 D&D 5e 战斗引擎核心（M1：无 Web 前端，纯 CLI）。
设计文档：`docs/superpowers/specs/2026-08-15-battle-engine-design.md`。

## 运行要求
- Python 3.10+
- 零第三方依赖；测试用 pytest

## 数据来源
SRD 怪物数据自动定位：
1. `BATTLE_SRD_PATH` 环境变量
2. `CLAUDE_SKILL_DIR/data/dnd5e_srd.json`（Claude Code 会话内）
3. `~/.claude/plugins/**/neuralinitiative-claude-dnd-skill/skills/dnd/data/`
找不到时设置 `BATTLE_SRD_PATH` 指向任意 `dnd5e_srd.json`。

## 快速上手
```bash
export DND_CAMPAIGN_ROOT=~/.claude/dnd   # 默认就是这个

python3 -m battle create -c my-campaign --map 20x15
python3 -m battle add-monster -c my-campaign --name 哥布林A --monster goblin
python3 -m battle add-player -c my-campaign --name 星沢羽 --ac 16 --hp 17 \
  --speed 30 --dex-mod 2 --attack "短弓:+5:1d6+3:穿刺:80/320"
python3 -m battle place -c my-campaign --name 哥布林A --x 3 --y 5
python3 -m battle init -c my-campaign
python3 -m battle start -c my-campaign
python3 -m battle npc-act -c my-campaign "哥布林A:attack 星沢羽"
python3 -m battle state -c my-campaign
```

## 规则要点
- 未掷先攻 → 一切攻击/施法被拒（状态门）
- 非本回合标准动作被拒（回合门不可跳过）；`--force` 跳过射程/移动力检查（DM 剧情用）
- 射程/移动力/回合资源由引擎机械校验
- 骰子：`--inject` 注入玩家手动结果（roll_mode: players 对齐）

## 测试
```bash
python3 -m pytest -q
```

## Web 战斗面板（M2）

玩家面板：浏览器打开 `http://<显示端>:5001/battle/`，选「我是」→ 自己的回合点攻击/点敌方 token 结算；黄色格=移动、蓝格=冲刺（勾选冲刺开关）、红圈=射程内；手动掷勾选后用实体骰输入结果。DM 照旧走 CLI（`battle npc-act` 等），面板自动同步。

### 挂载（一次性）
1. 设置环境变量 `BATTLE_ENGINE_DIR` 指向本仓库（如 `D:\github\dnd\battle-engine`）
2. 给显示端打补丁：`python integration/mount_display_app.py`（幂等；**插件更新后重跑**）
3. 重启显示端 —— 启动日志出现「battle-engine 蓝图已挂载（M2）」

未设置 `BATTLE_ENGINE_DIR` 时显示端行为不变。

### REST（/battle 前缀）
| 端点 | 说明 |
|---|---|
| `GET /battle/` | 面板页面 |
| `GET /battle/state` | 状态快照（无 undo_stack，日志 ≤50 条） |
| `POST /battle/action` | 玩家动作：`{"action":"attack|cast|move|dash|dodge|disengage|death_save|end_turn","actor":"…","target":"…","attack":"…","to":[x,y],"injected":{"d20":n,"damage":[..]}}` |
| `POST /battle/roll` | 服务器掷骰 `{"spec":"1d20|2d6+3","advantage":…（advantage 仅 1d20 生效）}` |
| `GET /battle/stream` | SSE：状态变化推送（轮询 battle.json mtime） |

战役解析：`BATTLE_CAMPAIGN` 环境变量优先，否则显示端 `.campaign` 运行时文件。LAN 模式 POST 需 `X-DND-Token`（显示端自动注入页面）。
