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

## M3：AoE 施法 + 规则补全 + 回写（2026-08-15）

- **AoE 施法**：`battle cast 法师 "火球术" --point 5,5 --radius 20 --dc 14 --stat dex --dmg 8d6 --type 火焰`——覆盖格内目标逐个豁免，成功豁免半伤（`--no-half` 关闭）。半径缺省从 SRD 描述解析（`20-foot-radius`），解析不到=仅中心格。
- **面板 AoE**：选 AoE 法术 → 点地图格子（红色高亮覆盖格）→ 点「施放」结算。豁免型法术点敌方 token 直接结算。
- **借机攻击**：移动/冲刺离开敌人近战范围触发（曼哈顿路径逐格判定）；`battle disengage` 免触发；敌人每回合一次反应；被击倒时移动终止。简化：单路径、首近战攻击、不打断移动。
- **闪避/脱离**：dodge 后攻击掷骰劣势 + 敏捷豁免优势（直到自己下回合开始）；disengage 本回合移动免借机攻击。
- **回写**：`battle end` 自动回写人物卡（HP/临时HP/死亡豁免，中英文卡兼容）；`--award-xp` 把已消灭 NPC 的 SRD XP 平分给存活 PC 并更新卡上经验值行。
- **面板 undo**：动作面板「撤销」按钮（CLI `battle undo` 已有）。
- **修复批**：注入 d20 服务端校验 1–20（CLI/web 均拒绝越界）、死亡豁免回合门（只能自己回合掷）、`battle init` 显示 d20 面值、`--hp-roll` 掷 HP、`cast` 无目标拒绝、`recover` 必须 -c、暴击多骰式翻倍、log 上限 200 条。

### M3 REST 扩展
| 端点 | 说明 |
|---|---|
| `POST /battle/action` | 新增 `{"action":"undo"}`；cast 支持 `center:[x,y]` + `radius` |
