"""Battle-end writeback to PC character sheets (<campaign>/characters/<name>.md).

显示端人物卡是 markdown，中文/英文段落均可（实测 wild-sheep-chase 三张卡三种
标题）。只改三行：HP、临时 HP、死亡豁免；经验值行由 update_xp 单独改。
匹配不到 → 返回提示行，绝不 raise（spec §9：失败时提示 DM 手动改）。
"""

from __future__ import annotations
import re
from pathlib import Path

HP_LINE_RE = re.compile(
    r"^(\s*-\s*\*\*HP:?\*\*\s*)(\d+)(\s*/\s*)(\d+)"
    r"(\s*\|\s*\*\*(?:临时\s?HP|Temp\s?HP):?\*\*\s*)(\d+)(.*)$")
DEATH_LINE_RE = re.compile(r"^(\s*-\s*\*\*(?:死亡豁免|Death Saves):?\*\*\s*)(.*)$")
DEATH_CN_RE = re.compile(r"成功\s*(\d+)\s*\|\s*失败\s*(\d+)")
DEATH_EN_RE = re.compile(r"Successes:?\s*(\d+)\s*\|\s*Failures:?\s*(\d+)")
# MULTILINE：经验行在文件中部（update_xp 对全文 search/sub）。
# 实际格式为 `**经验值:** 950 / 2700`（bold 闭合 `**` 在冒号后）：
#   - (?:-\s*)?  可选 bullet（实测卡有 `- **经验:**` 与 `**经验:**` 两种）
#   - \*\*?      闭合 bold 星号（brief 原正则缺此段，无法匹配真实卡）
XP_LINE_RE = re.compile(
    r"^(\s*(?:-\s*)?\*\*(?:经验值|经验|XP)[:：]?\*\*?\s*)(\d+)(\s*/\s*\d+)\s*$",
    re.MULTILINE)


def character_sheet_path(campaign_dir, name: str) -> Path:
    return Path(campaign_dir) / "characters" / f"{name}.md"


def _rewrite(text: str, *, hp: int, temp_hp: int,
             death_saves: dict) -> tuple:
    """返回 (新文本, 说明行列表)。只改匹配到的行，其余原样保留。

    HP 行一次改三处（HP / max / 临时 HP）：格式实测三种——
    '- **HP:** 24 / 24 | **临时HP:** 0'（无空格）、
    '- **HP:** 27 / 27 | **临时 HP:** 0'（有空格）、
    '- **HP:** 25 / 25 | **Temp HP:** 0'（英文）。"""
    notes = []
    lines = text.splitlines(keepends=True)
    new_lines = []
    hp_hit = death_hit = False
    for line in lines:
        m = HP_LINE_RE.match(line)
        if m:
            new_lines.append(f"{m.group(1)}{hp}{m.group(3)}{m.group(4)}"
                             f"{m.group(5)}{temp_hp}{m.group(7)}")
            hp_hit = True
            continue
        d = DEATH_LINE_RE.match(line)
        if d:
            body = d.group(2)
            if "成功" in body or "失败" in body:
                body = DEATH_CN_RE.sub(
                    f"成功 {death_saves['successes']} | 失败 {death_saves['failures']}", body)
                death_hit = True
            elif "Successes" in body or "Failures" in body:
                body = DEATH_EN_RE.sub(
                    f"Successes: {death_saves['successes']} | Failures: {death_saves['failures']}",
                    body)
                death_hit = True
            new_lines.append(f"{d.group(1)}{body}\n")
            continue
        new_lines.append(line)
    if not hp_hit:
        notes.append("未找到 HP 行（人物卡格式不符？）——请 DM 手动改")
    if not death_hit:
        notes.append("未找到死亡豁免行——请 DM 手动改")
    return "".join(new_lines), notes


def update_sheet(path, *, hp: int, temp_hp: int, death_saves: dict) -> list:
    """回写 HP/临时HP/死亡豁免三行。返回说明行（供 CLI 打印）。"""
    src = path.read_text(encoding="utf-8")
    new_src, notes = _rewrite(src, hp=hp, temp_hp=temp_hp, death_saves=death_saves)
    path.write_text(new_src, encoding="utf-8")
    if notes:
        notes.insert(0, f"部分行未匹配（{path.name}）")
    else:
        notes.append(f"已回写 {path.name}: HP {hp} / 临时 {temp_hp} / 死亡豁免 "
                     f"{death_saves['successes']}成{death_saves['failures']}败")
    return notes


def update_xp(path, total_xp: int) -> list:
    """经验值行 '950 / 2700' → '950+N / 2700'。"""
    src = path.read_text(encoding="utf-8")
    m = XP_LINE_RE.search(src)
    if not m:
        return [f"未找到经验值行（{path.name}）——请 DM 手动加 {total_xp} XP"]
    cur = int(m.group(2))
    new_src = XP_LINE_RE.sub(f"{m.group(1)}{cur + total_xp}{m.group(3)}", src)
    path.write_text(new_src, encoding="utf-8")
    return [f"经验值 {cur} → {cur + total_xp}（+{total_xp} XP）: {path.name}"]


def writeback_combatants(enc, campaign_dir) -> list:
    """战斗结束回写：每个 PC 战斗员 → characters/<actor.name>.md（HP/临时/死亡豁免）。"""
    notes = []
    for c in enc.combatants.values():
        if c.actor.kind != "pc":
            continue
        path = character_sheet_path(campaign_dir, c.actor.name)
        if not path.exists():
            notes.append(f"未找到人物卡 {path.name}——请 DM 手动改 HP {c.hp}/{c.actor.max_hp}")
            continue
        notes.extend(update_sheet(path, hp=c.hp, temp_hp=c.temp_hp,
                                  death_saves=c.death_saves))
    return notes
