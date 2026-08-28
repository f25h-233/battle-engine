# CLAUDE.md — battle-engine（D&D 5e 战斗引擎）

D&D 5e 战斗结算核心：`battle/core/` 零 Web 零第三方依赖的结算引擎 + CLI + Flask 玩家面板（`battle/web/`）。
**独立 git 仓库，位于 dnd skill 之外**——skill 目录会被插件更新覆盖，引擎必须住在外面，显示端经 `BATTLE_ENGINE_DIR` 挂载。

版本 0.4.0（M4 完成 + 3 个 fix，全量 155 passed）。设计规范：`D:\github\dnd\docs\superpowers\specs\2026-08-15-battle-engine-design.md`（仓库外）。

## 常用命令

```bash
python -m pytest -q                          # 全量测试（155 passed）
python test_battle.py                        # 一键联调：造战斗（真实 SRD 哥布林×3）→ 自动打挂载补丁 → 起显示端 → 开面板（test-battle.bat 双击版）
bash scripts/duel-demo.sh --smoke            # 决斗演示自测：起 serve → curl 5 断言（含攻击后移动回归）→ 自动清理
python -m battle create -c 战役 --map 20x15  # 建战役（默认根 ~/.claude/dnd）
python -m battle init -c 战役                # 掷先攻（一切攻击/施法前的状态门）
python -m battle start -c 战役               # 开始战斗
python -m battle npc-act -c 战役 "哥布林A:attack 星沢羽"   # NPC 声明意图，引擎结算
python -m battle state -c 战役               # 状态快照
python -m battle serve [--port 5002] [--token 固定值]     # 独立服务模式（LAN + 自动 token）
python -m build                              # 打包 wheel（dist/battle_engine-0.4.0-*.whl）
```

- wheel 含 Web 资产；`pip install <wheel>[web]` 才有 Flask；装后 `battle` console script 可用
- 测试配置在 pyproject `[tool.pytest.ini_options]`，testpaths=tests

## 架构

| 路径 | 职责 |
|---|---|
| `battle/core/` | 结算核心：dice、initiative、models、monster_parser、persistence、resolution、srd、writeback。**零 Web 依赖、零第三方依赖** |
| `battle/web/bp.py` | Flask 蓝图（/battle/ 页面 + state/action/roll/stream） |
| `battle/web/serve.py` | M4 独立服务模式（`python -m battle serve`） |
| `battle/cli.py` | argparse CLI，所有 `cmd_*` 函数（create/add-monster/init/start/attack/cast/move/dash/dodge/disengage/save/death-save/cond/state/log/npc-act/undo/end/recover/serve…） |
| `battle/integration/mount_display_app.py` | 显示端挂载补丁（幂等；**显示端插件更新后要重跑**） |
| `test_battle.py` | 一键联调入口（test-battle.bat 包装）：构造测试战斗 + 显示端挂载缺失时自动补丁 + 起显示端开面板，战役根隔离在 `.test-campaigns/` |
| `scripts/duel-demo.sh` | 双人决斗演示（莫德凯 vs 莉安娜 + 箱子堆场地物体）；`--smoke` 模式 curl 自测 serve 链路 |
| `tests/` | pytest 全量；`.test-campaigns/` 是冒烟用临时战役根（隔离） |
| `.superpowers/sdd/` | SDD 工作区（含各里程碑冒烟输出） |

## 关键决策（已确认，不要推翻）

1. **角色分工**：玩家在浏览器面板；DM（Claude Code）在 CLI 侧。引擎不区分身份，`npc-act` 就是 DM 的意图声明接口
2. **骰子**：服务器掷 + `--inject` 注入玩家手动结果；**先攻永远引擎掷**，注入 d20 校验 1–20
3. **状态门**：非 initiative_rolled/combat_active 拒绝攻击/施法——忘掷先攻机制上不可能；回合门：标准动作每回合一次（dash 动作后非法），但**攻击/施法后仍可移动**（5e 移动与动作独立，b2c16d6），移动只看移动力预算；面板动作已用自动取消冲刺
4. **地图**：简化网格 + 直线测距（5ft/格，曼哈顿移动），无墙体/视线，掩体靠叙事；`--force` 跳过射程/移动力检查（DM 剧情用）
5. **持久化**：`<campaign>/battle.json` 原子写盘 + .bak + undo 栈（push 在变更前）；`battle end` 自动回写人物卡 + `--award-xp` 平分 XP，重复 end 拒绝（防双发）
6. **SRD 定位**：`BATTLE_SRD_PATH` → `CLAUDE_SKILL_DIR/data/` → glob 插件目录；真实 SRD 段落标记是 em dash（—）非 ◆、速度 `walk N ft.`——已双兼容，改动时保持

## 环境变量

| 变量 | 用途 |
|---|---|
| `BATTLE_ENGINE_DIR` | 显示端挂载引擎的路径（指向本仓库） |
| `BATTLE_SRD_PATH` | SRD JSON 覆盖路径（找不到时提示设置） |
| `DND_CAMPAIGN_ROOT` | 战役根（默认 `~/.claude/dnd`） |
| `BATTLE_CAMPAIGN` | serve 战役解析优先级：此变量 > 显示端 `.runtime/.campaign` |
| `DND_DISPLAY_APP` | test_battle.py 的显示端路径覆盖（默认指向本机插件 marketplace 补丁版） |

## 已知坑（Windows 实测）

- **持久化加固（bce36b6，勿回退）**：battle.json 曾因「move 轮转」出现不存在窗口期、读锁冲突——现为 copy2 轮转 + 原子替换/读取短重试三层防护，主文件任何时刻在位，SSE 不再报「battle.json 不存在」假帧。改 persistence 时保持这三层
- **curl 命令行中文 JSON body 会损坏** → 冒烟/脚本一律 `--data @file`（duel-demo.sh 内已遵守）
- **GBK 控制台显示乱码是假象**，文件是干净 UTF-8（引擎全链路 UTF-8，勿为显示去动编码）
- 端口占用：serve 报错提示换 `--port`，不静默换
- 显示端 POST 需 `X-DND-Token`（无/错 → 401）；serve 的 token 不持久化，重启生成新 token

## 永不修（记录在案，README 已注明）

豁免裸 d20 无加值、`_reaction_spent` KeyError、8/334 无 walk 怪物 speed=30、temp 吸收不计失败、Multiattack 段丢弃、blinded 文案、force 跳过移动力但不过占格（DM 需要时用 place）。改这些区域前先确认是否在"永不修"名单——不是 bug，是记录在案的简化面。

## 开发流程（SDD，已两轮验证）

新功能按此走：

1. **先读记忆钩子**：`C:\Users\qwe13\.claude\projects\D--github-dnd\memory\battle-engine-progress.md`（进度）+ `sdd-development-workflow.md`（流程细则）
2. **writing-plans 写计划**：含完整代码 + TDD 红绿步骤 + 每任务独立 commit + 显式逃逸舱；计划放 `D:\github\dnd\docs\superpowers\plans\`，命名 `2026-MM-DD-battle-engine-mN.md`
3. **subagent-driven-development 执行**：task-brief/review-package 必须从**仓库根目录**调用；implementer=haiku/sonnet，task reviewer=sonnet，**最终全分支 review=opus**
4. **每任务闭环**：task-brief → implementer → 独立 reviewer 双裁决 → fix loop（≤3 轮）→ ledger 记录
5. **冒烟 = 里程碑出口证据**：真实 SRD + 真实进程，输出附最终报告；workspace 收尾时删除，git 历史即记录
6. **记忆埋点**：更新 battle-engine-progress.md 的进度与"下次会话钩子"

单文件小改/修 bug 不需要整套 SDD，直接改 + 跑全量测试。

## 当前状态与下一步

- ✅ M4（serve 独立服务 + wheel 打包）+ 3 个 fix（bce36b6 持久化加固 / b2c16d6 攻击后移动 / 00066f5 面板 dash 自动取消），全量 155 passed
- M5 候选（未规划，先 writing-plans）：TLS/设备审批补全、AoE 锥形、法术位回写
- 冒烟模板建议：攻击前循环 `end_turn` 推进到目标回合（随机先攻下断言确定性）
