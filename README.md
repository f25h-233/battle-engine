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
