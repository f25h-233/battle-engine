/* battle.js — 战斗面板（M2 前端）
   职责：拉取 /battle/state → 渲染先攻条/网格/动作面板/日志；SSE 实时刷新。
   规则唯一事实源是引擎 core：前端只展示与请求，不做规则判断。
   移动=曼哈顿 / 测距=欧氏 —— 与 core 同公式，保证高亮与引擎校验一致。 */
"use strict";

const CELL = 30;   // 每格像素
const TOKEN = (document.querySelector('meta[name="dnd-token"]') || {}).content || "";
const $ = (id) => document.getElementById(id);

let S = null;          // 最新状态 payload（引擎快照）
let identity = null;   // “我是”——自我声明的身份（localStorage 持久化）
let selected = null;   // 当前选中目标 id
let selAttack = null;  // 选中的攻击名
let dashMode = false;
let manualDice = false;
let busy = false;
let gridDims = null;   // 已建网格尺寸 [w, h]
let pendingCenter = null;  // AoE 预览中心（点格子选中，点「施放」结算）

/* ── 基础工具 ─────────────────────────────────────────────────── */
function api(path, body) {
  return fetch(path, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json",
               ...(TOKEN ? { "X-DND-Token": TOKEN } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  }).then(async (r) => {
    let j = {};
    try { j = await r.json(); } catch (_) { j = { ok: false, error: "响应解析失败" }; }
    return { status: r.status, ...j };
  });
}

const fmtHP = (c) => `${c.hp}/${c.max_hp}`;
const dist = (a, b) => Math.round(Math.hypot((a.x - b.x) * 5, (a.y - b.y) * 5)); // 欧氏（引擎 distance_ft）
const mdist = (a, b) => (Math.abs(a.x - b.x) + Math.abs(a.y - b.y)) * 5;         // 曼哈顿（引擎移动校验）
const me = () => (identity && S && S.combatants[identity]) || null;
const myTurn = () => !!S && S.status === "combat_active"
  && S.turn_order[S.turn_index] === identity;

function setMsg(text, cls) {
  const m = $("action-message");
  m.textContent = text || "";
  m.className = "msg " + (cls || "");
}

/* ── 状态获取与 SSE ───────────────────────────────────────────── */
async function fetchState() {
  const r = await api("/battle/state");
  if (r.ok) { S = r.state; render(); }
  else setMsg(r.error || "状态获取失败", "err");
}

function openStream() {
  const es = new EventSource("/battle/stream");
  es.onmessage = (ev) => {
    try {
      const d = JSON.parse(ev.data);
      if (d.state) { S = d.state; render(); }
      else if (d.error) setMsg(d.error, "err");
    } catch (_) { /* 忽略非 JSON 事件 */ }
  };
  es.onerror = () => { es.close(); setTimeout(openStream, 3000); };   // 先 close 再手动重试（避免与原生自动重连叠加）
}

/* ── 渲染 ────────────────────────────────────────────────────── */
function render() {
  if (!S) return;
  $("campaign-name").textContent = S.campaign;
  const st = S.status;
  const badge = $("status-badge");
  badge.textContent = ({ setup: "未掷先攻", initiative_rolled: "先攻已掷·待开始",
                         combat_active: "战斗中", ended: "已结束" }[st] || st);
  badge.className = st === "combat_active" ? "active" : "";
  $("round-badge").textContent = st === "combat_active" ? `第 ${S.round} 回合` : "";
  renderIdentity();
  renderInitiative();
  renderGrid();
  renderActions();
  renderLog();
}

function renderIdentity() {
  const sel = $("identity-select");
  const pcs = Object.values(S.combatants).filter((c) => c.kind === "pc");
  if (!identity || !S.combatants[identity]) identity = (pcs[0] || {}).id || null;
  if (sel.dataset.built === S.campaign + (identity || "")) return;
  sel.dataset.built = S.campaign + (identity || "");
  sel.innerHTML = pcs.map((c) => `<option value="${c.id}">${c.name}</option>`).join("");
  sel.value = identity || "";
  sel.onchange = () => {
    identity = sel.value;
    selected = identity;
    selAttack = null;
    try { localStorage.setItem("battle-identity", identity || ""); } catch (_) {}
    render();
  };
  selected = selected || identity;
}

function renderInitiative() {
  const bar = $("initiative-bar");
  const cur = S.turn_order[S.turn_index] || null;
  bar.innerHTML = S.turn_order.map((id) => {
    const c = S.combatants[id];
    if (!c) return "";
    const pct = Math.max(0, Math.min(100, (c.hp / c.max_hp) * 100));
    const cls = ["chip", id === cur ? "cur" : "", c.hp <= 0 ? "dead" : ""].join(" ");
    return `<div class="${cls}" title="AC ${c.ac} · 先攻 ${c.initiative ?? "—"}">
      <div class="ch-init">${c.initiative ?? "—"}</div>
      <div class="ch-name">${c.name}${c.acted ? " ✓" : ""}</div>
      <div class="ch-hp"><div class="ch-hp-fill" style="width:${pct}%"></div></div>
    </div>`;
  }).join("");
}

function renderGrid() {
  const g = $("grid");
  const w = S.map.width, h = S.map.height;
  if (!gridDims || gridDims[0] !== w || gridDims[1] !== h) {
    gridDims = [w, h];
    g.style.width = (w * CELL) + "px";
    g.style.height = (h * CELL) + "px";
    g.innerHTML = "";
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        const d = document.createElement("div");
        d.className = "grid-cell";
        d.dataset.x = x; d.dataset.y = y;
        d.style.left = (x * CELL) + "px"; d.style.top = (y * CELL) + "px";
        g.appendChild(d);
      }
  }
  g.querySelectorAll(".waypoint").forEach((e) => e.remove());
  Object.entries(S.waypoints || {}).forEach(([name, pos]) => {
    const wd = document.createElement("div");
    wd.className = "waypoint";
    wd.style.left = (pos[0] * CELL + 2) + "px"; wd.style.top = (pos[1] * CELL + 2) + "px";
    wd.textContent = name;
    g.appendChild(wd);
  });
  g.querySelectorAll(".token").forEach((e) => e.remove());
  Object.values(S.combatants).forEach((c) => {
    const t = document.createElement("div");
    t.className = ["token", c.kind, c.id === selected ? "selected" : "",
                   c.id === identity ? "self" : "", c.hp <= 0 ? "dead" : ""]
      .join(" ").trim();
    t.style.left = (c.x * CELL + 2) + "px"; t.style.top = (c.y * CELL + 2) + "px";
    t.dataset.id = c.id;
    t.title = `${c.name}  HP ${fmtHP(c)}${c.temp_hp ? ` +${c.temp_hp}临时` : ""}` +
      `  AC ${c.ac}  (${c.x},${c.y})` +
      (c.conditions.length ? `  [${c.conditions.join(",")}]` : "");
    t.innerHTML = `<span class="t-name">${c.name}</span>
      <span class="t-hp"><b style="width:${Math.max(0, Math.min(100, (c.hp / c.max_hp) * 100))}%"></b></span>`;
    g.appendChild(t);
  });
  paintOverlays();
  paintAoe();
}

function paintAoe() {
  const g = $("grid");
  g.querySelectorAll(".aoe-cell").forEach((e) => e.classList.remove("aoe-cell"));
  const who = me();
  if (!who || !pendingCenter) return;
  const atk = (who.attacks || []).find((a) => a.name === selAttack);
  if (!atk || !atk.aoe_radius_ft) return;
  const r = atk.aoe_radius_ft;
  const [cx, cy] = pendingCenter;
  for (let y = 0; y < S.map.height; y++)
    for (let x = 0; x < S.map.width; x++) {
      const d = Math.round(Math.hypot((x - cx) * 5, (y - cy) * 5));
      if (d <= r) {
        const cell = g.querySelector(`.grid-cell[data-x="${x}"][data-y="${y}"]`);
        if (cell) cell.classList.add("aoe-cell");
      }
    }
}

function paintOverlays() {
  const g = $("grid");
  g.querySelectorAll(".reachable,.dash-cell,.in-range").forEach((e) =>
    e.classList.remove("reachable", "dash-cell", "in-range"));
  const who = me();
  if (!who || !myTurn()) return;          // 非本回合不画移动格（引擎仍会拒绝越权请求）
  const walk = Math.floor((who.movement_left_ft || 0) / 5);
  const dash = Math.floor(who.speed_ft / 5);
  for (let y = 0; y < S.map.height; y++)
    for (let x = 0; x < S.map.width; x++) {
      const m = Math.abs(x - who.x) + Math.abs(y - who.y);   // 曼哈顿格数
      if (m === 0) continue;
      const cell = g.querySelector(`.grid-cell[data-x="${x}"][data-y="${y}"]`);
      if (!cell) continue;
      if (dashMode) { if (m <= dash) cell.classList.add("dash-cell"); }
      else if (m <= walk) cell.classList.add("reachable");
    }
  const atk = selAttack ? (who.attacks || []).find((a) => a.name === selAttack) : null;
  if (atk) {
    const rng = atk.range_ft[1] || atk.range_ft[0];
    g.querySelectorAll(".token").forEach((t) => {
      const tgt = S.combatants[t.dataset.id];
      if (tgt && tgt.id !== who.id && tgt.hp > 0 && dist(who, tgt) <= rng)
        t.classList.add("in-range");
    });
  }
}

function renderActions() {
  const who = me();
  const info = $("selected-info");
  const banner = $("turn-banner");
  if (selected && S.combatants[selected]) {
    const t = S.combatants[selected];
    const d = who ? dist(who, t) : 0;
    info.textContent = `目标: ${t.name}  HP ${fmtHP(t)}  AC ${t.ac}${who ? `  ${d}ft` : ""}`;
  } else {
    info.textContent = who ? "未选择目标" : "选择身份后可操作";
  }
  const cur = S.turn_order[S.turn_index] ? S.combatants[S.turn_order[S.turn_index]] : null;
  if (S.status === "combat_active") {
    if (myTurn()) { banner.textContent = `▶ 你的回合（${who ? who.name : "—"}）`; banner.className = "yours"; }
    else if (cur) { banner.textContent = `等待 ${cur.name} 行动…`; banner.className = "wait"; }
    else { banner.textContent = "—"; banner.className = "wait"; }
  } else if (S.status === "setup") {
    banner.textContent = "尚未掷先攻——等待 DM 用 CLI（battle init / start）"; banner.className = "wait";
  } else if (S.status === "initiative_rolled") {
    banner.textContent = "先攻已掷——等待 DM 开始战斗（battle start）"; banner.className = "wait";
  } else {
    banner.textContent = "战斗已结束"; banner.className = "wait";
  }
  const ab = $("attack-buttons");
  ab.innerHTML = "";
  if (who) {
    (who.attacks || []).forEach((a) => {
      if (a.kind === "special") return;   // 特殊动作由 DM 处理（CLI）
      const btn = document.createElement("button");
      btn.dataset.action = a.kind === "spell" ? "cast" : "attack";
      btn.dataset.name = a.name;
      btn.dataset.aoe = a.aoe_radius_ft ? String(a.aoe_radius_ft) : "";
      const rng = a.range_ft[1] || a.range_ft[0];
      btn.textContent = `${a.kind === "spell" ? "施法" : "攻击"} ${a.name}` +
        (a.damage ? ` ${a.damage}` : "");
      if (a.aoe_radius_ft) btn.textContent += `（${a.aoe_radius_ft}ft 半径）`;
      btn.title = `射程 ${rng}ft · ${a.damage_type || "—"}` + (a.note ? ` · ${a.note}` : "");
      btn.className = selAttack === a.name ? "primary" : "";
      btn.disabled = !myTurn() || who.acted;
      ab.appendChild(btn);
    });
  }
  const mb = $("misc-buttons");
  const canAct = !!who && myTurn() && !who.acted;
  mb.innerHTML = [
    { action: "dodge", label: "闪避" },
    { action: "disengage", label: "脱离" },
  ].map((b) => `<button data-action="${b.action}" ${canAct ? "" : "disabled"}>${b.label}</button>`).join("");
  const downed = who && who.hp === 0 && who.death_saves.failures < 3 && !who.death_saves.stable;
  mb.innerHTML += `<button data-action="death_save" class="danger" ` +
    `${downed && myTurn() ? "" : "disabled"}>死亡豁免（成${who ? who.death_saves.successes : 0}` +
    `/败${who ? who.death_saves.failures : 0}）</button>`;
  mb.innerHTML += `<button data-action="undo">撤销</button>`;
  // 仅当前选中攻击本身是 AoE 时才显示「施放」——否则陈旧 pendingCenter
  // 会把非 AoE 攻击渲染成假 AoE 按钮（M3 终审 I-1）
  const aoeAtk = who && selAttack ? (who.attacks || []).find((a) => a.name === selAttack) : null;
  if (aoeAtk && aoeAtk.aoe_radius_ft && pendingCenter && S) {
    mb.innerHTML += `<button data-action="cast_aoe" data-name="${selAttack}" class="primary">施放 ${selAttack} @(${pendingCenter[0]},${pendingCenter[1]})</button>`;
    mb.innerHTML += `<button data-action="cancel_aoe">取消 AoE</button>`;
  }
  mb.innerHTML += `<button data-action="end_turn" class="primary" ` +
    `${myTurn() ? "" : "disabled"}>结束回合</button>`;
  $("dash-toggle").disabled = !myTurn();
}

function renderLog() {
  const list = $("log-list");
  list.innerHTML = S.log.map((e) => {
    const head = `[R${e.round}] ${e.actor || "—"} ${e.action}`;
    const lines = (e.lines || []).map((l) => `<div class="log-line">${l}</div>`).join("");
    return `<div class="log-entry"><div class="log-head">${head}</div>${lines}</div>`;
  }).join("");
  list.scrollTop = list.scrollHeight;
}

/* ── 交互（M2 闭环） ──────────────────────────────────────────── */
function registerHandlers() {
  // 动作面板：按钮委托（按钮在 renderActions 每次重建，监听器挂在面板上）
  $("action-panel").addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-action]");
    if (!btn || btn.disabled) return;
    const name = btn.dataset.name;
    const act = btn.dataset.action;
    if (act === "attack" || act === "cast") {
      selAttack = name;
      const aoe = btn.dataset.aoe;
      if (aoe) {
        pendingCenter = null;                 // AoE 预览态：点格子选中心后「施放」结算
        setMsg(`已选 ${name}（${aoe}ft 半径）——点击地图格子选中心`, "ok");
        renderGrid();
        return;
      }
      pendingCenter = null;                 // 非 AoE 攻击：清掉陈旧 AoE 中心，防误「施放」（M3 终审 I-1）
      const tgt = selected && S.combatants[selected];
      if (tgt && tgt.id !== identity && tgt.hp > 0) {
        postAction(act, { target: tgt.id, attack: name });
      } else {
        setMsg(`已选 ${name} —— 点击敌方 token 结算`, "ok");
        renderGrid();
      }
    } else if (act === "undo") {
      postAction("undo", {});
    } else if (act === "cast_aoe") {
      const caster = me();
      const atkAoe = caster && (caster.attacks || []).find((a) => a.name === name);
      postAction("cast", { attack: name, center: pendingCenter,
                           radius: atkAoe ? atkAoe.aoe_radius_ft : undefined });
    } else if (act === "cancel_aoe") {
      pendingCenter = null;
      renderGrid();
      setMsg("已取消 AoE 预览", "");
    } else if (act === "end_turn") {
      postAction("end_turn", {});
    } else {
      postAction(act, {});
    }
  });
  // 网格：token 点击优先，其次格点击（事件委托，DOM 重建不丢监听）
  $("grid").addEventListener("click", (ev) => {
    const t = ev.target.closest(".token");
    if (t) { tokenClick(t.dataset.id); return; }
    const cell = ev.target.closest(".grid-cell");
    if (cell) cellClick(parseInt(cell.dataset.x, 10), parseInt(cell.dataset.y, 10));
  });
  // 冲刺 / 手动掷开关
  $("dash-toggle").addEventListener("change", (e) => {
    dashMode = e.target.checked;
    renderGrid();
  });
  $("manual-dice").addEventListener("change", (e) => {
    manualDice = e.target.checked;
    try { localStorage.setItem("battle-manual", manualDice ? "1" : "0"); } catch (_) {}
    $("manual-dice-box").hidden = !manualDice;
  });
  // 手动掷 d20 输入限 1–20（服务端注入校验归 M3）
  $("inject-d20").addEventListener("input", (e) => {
    const v = parseInt(e.target.value, 10);
    if (isNaN(v)) return;
    if (v > 20) e.target.value = 20;
    if (v < 1) e.target.value = 1;
  });
}

function tokenClick(id) {
  const tgt = S.combatants[id];
  if (!tgt) return;
  selected = id;
  const who = me();
  if (who && myTurn() && !who.acted && id !== identity && tgt.hp > 0 && selAttack) {
    const atk = (who.attacks || []).find((a) => a.name === selAttack);
    if (atk) {
      if (atk.aoe_radius_ft) {
        pendingCenter = [tgt.x, tgt.y];   // AoE 预览：点敌方 token 也只是选中心
        renderGrid();
        setMsg(`AoE 中心 (${tgt.x},${tgt.y})——点「施放」结算`, "ok");
        return;
      }
      const rng = atk.range_ft[1] || atk.range_ft[0];
      const d = dist(who, tgt);
      if (d <= rng) {
        postAction(atk.kind === "spell" ? "cast" : "attack",
                   { target: id, attack: selAttack });
        return;
      }
      setMsg(`目标 ${tgt.name}: ${d}ft，超出 ${atk.name} 射程 ${rng}ft`, "err");
    }
  }
  renderGrid();   // 纯查看：更新选中高亮与信息
}

function cellClick(x, y) {
  const who = me();
  if (!who || !myTurn() || busy) return;
  const atk = selAttack ? (who.attacks || []).find((a) => a.name === selAttack) : null;
  if (atk && atk.aoe_radius_ft) {
    pendingCenter = [x, y];        // AoE 预览：点格子选中心（可覆盖敌方 token 格）
    renderGrid();
    setMsg(`AoE 中心 (${x},${y})——点「施放」结算`, "ok");
    return;
  }
  const occ = Object.values(S.combatants).find((c) => c.x === x && c.y === y && c.hp > 0);
  if (occ) { setMsg(`该格被 ${occ.name} 占据`, "err"); return; }
  if (dashMode) postAction("dash", { to: [x, y] });
  else postAction("move", { to: [x, y] });
}

function postAction(action, extra) {
  if (busy) return;
  busy = true;
  const body = Object.assign({ action, actor: identity }, extra);
  if (manualDice) {
    const d20v = parseInt($("inject-d20").value, 10);
    const dmg = parseDmg($("inject-damage").value);
    const inj = {};
    if (!isNaN(d20v)) inj.d20 = d20v;
    if (dmg) inj.damage = dmg;
    if (Object.keys(inj).length) body.injected = inj;
  }
  setMsg("结算中…", "");
  api("/battle/action", body).then((r) => {
    busy = false;
    if (r.ok) {
      S = r.state;
      pendingCenter = null;
      render();
      flashRoll(r.lines);
      // 手动掷值已消费，清空避免陈旧注入泄漏到下一次动作
      $("inject-d20").value = "";
      $("inject-damage").value = "";
    } else {
      setMsg(r.error || "动作被拒绝", "err");
      if (r.state) { S = r.state; render(); }
    }
  }).catch(() => { busy = false; setMsg("网络错误", "err"); });
}

function parseDmg(text) {
  const parts = String(text || "").split(/[,，\s]+/)
    .map(Number)
    .filter((n) => Number.isInteger(n) && n >= 1 && n <= 20);
  return parts.length ? parts : null;
}

let chipTimer = null;
function flashRoll(lines) {
  // “掷骰 → 结果即上屏”：从结算行提取 d20 值，右上角短暂闪现骰子结果
  const m = (lines || []).map((l) => l.match(/d20\((\d+)\)/)).filter(Boolean)[0];
  if (!m) return;
  const chip = document.getElementById("dice-chip") || (() => {
    const el = document.createElement("div");
    el.id = "dice-chip";
    document.body.appendChild(el);
    return el;
  })();
  chip.textContent = `🎲 d20 = ${m[1]}`;
  clearTimeout(chipTimer);   // 复用 chip：旧计时器链不再影响新闪现
  requestAnimationFrame(() => { chip.style.opacity = "1"; });
  chipTimer = setTimeout(() => {
    chip.style.opacity = "0";
    setTimeout(() => { if (chip.style.opacity === "0") chip.remove(); }, 350);
  }, 1200);
}

/* ── 启动 ────────────────────────────────────────────────────── */
function init() {
  try { identity = localStorage.getItem("battle-identity") || null; } catch (_) {}
  try { manualDice = localStorage.getItem("battle-manual") === "1"; } catch (_) {}
  $("manual-dice").checked = manualDice;
  $("manual-dice-box").hidden = !manualDice;
  registerHandlers();
  fetchState();
  openStream();
}
document.addEventListener("DOMContentLoaded", init);
