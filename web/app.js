/* ENGRAM 忆枢 — cinematic frontend.
   Expressive, but honest: every glow, fade, snap and particle is driven by a
   real engine event or a real state variable. Live mode polls the event log;
   Demo mode replays a recorded run through the identical pipeline. */
"use strict";

/* ================================ state ================================ */
const S = {
  meta: null, agent: "A",
  entities: new Map(), facts: new Map(), lessons: new Map(), episodes: [],
  lastSeq: 0, mode: "live",                  // live | demo | past
  pos: new Map(), born: new Map(),           // node entrance times
  pulses: new Map(), flashes: new Map(), draws: new Map(), particles: [],
  laneFly: [], skipChat: [], demoRun: 0, t0: null, asOf: null,
  busy: false, dream: 0,                     // dream>now → sleep tint
  lastGauge: null, dissolved: new Set(), scatter: [],
  cam: { s: 1, x: 0, y: 0, focus: null, fScale: 1, until: 0 },
};
function camPush(worldPt, scale, ms) {
  S.cam.focus = worldPt; S.cam.fScale = scale;
  S.cam.until = performance.now() + ms;
}
const sleepMs = (ms) => new Promise((r) => setTimeout(r, ms));
const $ = (id) => document.getElementById(id);
const nowS = () => Date.now() / 1000;
const TYPE_COLOR = { person: "#5eead4", place: "#a78bfa", org: "#fbbf24",
                     concept: "#f472b6", thing: "#93a3c8" };
const nodeColor = (e) => TYPE_COLOR[e.type] || TYPE_COLOR.thing;

function effSal(item, at) {
  if (item.salience_base == null) return item.salience != null ? item.salience : 0.85;
  const imp = Math.min(5, Math.max(1, item.importance || 2));
  const hl = 24 * Math.pow(imp, 1.5);
  const dt = Math.max(0, (at || nowS()) - item.last_touch) / 3600;
  return item.salience_base * Math.pow(0.5, dt / hl);
}
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body) });
  return r.json();
}

/* ========================= aurora background ========================== */
const BG = { blobs: [
  { hue: "109,93,246", r: 520, sp: .00013, ph: 0 },
  { hue: "34,211,238", r: 430, sp: .00019, ph: 2.2 },
  { hue: "244,114,182", r: 380, sp: .00011, ph: 4.4 },
], dust: [] };
for (let i = 0; i < 90; i++)
  BG.dust.push({ x: Math.random(), y: Math.random(), r: Math.random() * 1.6 + .4,
                 sp: Math.random() * .00003 + .00001, tw: Math.random() * 6.28 });

function renderBG(t) {
  const c = $("bgCanvas"), ctx = c.getContext("2d");
  const W = innerWidth, H = innerHeight, d = devicePixelRatio || 1;
  if (c.width !== W * d) { c.width = W * d; c.height = H * d; }
  ctx.setTransform(d, 0, 0, d, 0, 0);
  ctx.fillStyle = "#05060e"; ctx.fillRect(0, 0, W, H);
  const dreaming = S.dream > performance.now();
  for (const b of BG.blobs) {
    const x = W * (.5 + .38 * Math.sin(t * b.sp + b.ph));
    const y = H * (.5 + .34 * Math.cos(t * b.sp * 1.3 + b.ph));
    const g = ctx.createRadialGradient(x, y, 0, x, y, b.r);
    const a = dreaming ? .16 : .09;
    g.addColorStop(0, `rgba(${dreaming ? "109,93,246" : b.hue},${a})`);
    g.addColorStop(1, "rgba(0,0,0,0)");
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
  }
  ctx.fillStyle = "#aab6ff";
  for (const p of BG.dust) {
    p.y -= p.sp * 16; if (p.y < 0) p.y = 1;
    ctx.globalAlpha = .12 + .1 * Math.sin(t * .001 + p.tw);
    ctx.beginPath(); ctx.arc(p.x * W, p.y * H, p.r, 0, 7); ctx.fill();
  }
  ctx.globalAlpha = 1;
}

/* ============================ graph state ============================= */
function addEntity(e) {
  const had = S.entities.has(e.id);
  S.entities.set(e.id, Object.assign(S.entities.get(e.id) || {}, e));
  if (!S.pos.has(e.id)) {
    const c = stageCenter();
    S.pos.set(e.id, { x: c.x + (Math.random() - .5) * 260,
                      y: c.y + (Math.random() - .5) * 200, vx: 0, vy: 0 });
  }
  if (!had) S.born.set(e.id, performance.now());
}
function addFact(f) {
  const had = S.facts.has(f.id);
  S.facts.set(f.id, Object.assign(S.facts.get(f.id) || {}, f));
  if (!had && S.mode !== "past") S.draws.set(f.id, performance.now());
}
function addLesson(l) { S.lessons.set(l.id, Object.assign(S.lessons.get(l.id) || {}, l)); renderLessonDock(); }
function addEpisode(e) { if (!S.episodes.find((x) => x.id === e.id)) S.episodes.push(e); }

function applyGraph(g) {
  const keepBorn = new Map(S.born);
  S.entities.clear(); S.facts.clear(); S.lessons.clear(); S.episodes = [];
  g.entities.forEach((e) => { addEntity(e); if (keepBorn.has(e.id)) S.born.set(e.id, keepBorn.get(e.id)); });
  g.facts.forEach((f) => S.facts.set(f.id, f));
  g.lessons.forEach((l) => S.lessons.set(l.id, l));
  g.episodes.forEach(addEpisode);
  renderLessonDock(); updateKPIs();
}

/* ============================== events ================================ */
function handleEvent(ev, animate = true) {
  const p = ev.payload;
  switch (ev.type) {
    case "entity_created": addEntity(p.entity); break;
    case "episode_ingested":
      addEpisode(p.episode);
      if (animate) {
        S.laneFly.push({ id: p.episode.id, t0: performance.now(), mode: "in" });
        narrate(`<b>remember()</b> — stored as episodic memory · importance ${p.episode.importance}/5`, "●");
      }
      break;
    case "fact_created":
      addFact(p.fact);
      if (animate) narrate(`belief formed — <b>${esc(p.fact.subject_name)}</b> ${esc(p.fact.relation.replaceAll("_", " "))} <b>${esc(p.fact.object_name)}</b>`, "◆");
      break;
    case "marker": chapterCard(p); break;
    case "revision":
      if (animate) revisionCinematic(p);
      else { addFact(p.old_fact); addFact(p.new_fact); }
      break;
    case "disputed":
      p.facts.forEach(addFact);
      if (animate) showMoment("CONFLICT DETECTED · 记忆冲突",
        p.rationale || "Two beliefs disagree — the agent will ask you.", "dispute", 5200);
      break;
    case "lesson_created":
      addLesson(p.lesson);
      if (animate) {
        narrate("<b>lesson distilled</b> from the failure→correction pattern — it will change future behavior", "◈");
        showMoment("LESSON LEARNED · 吃一堑，长一智",
          `WHEN ${p.lesson.trigger} → ${p.lesson.guidance}`, "lesson", 5600);
      }
      break;
    case "recall":
      if (animate) {
        const col = agentColor(p.agent_id);
        Object.entries(p.activation || {}).forEach(([id, lvl], i) =>
          setTimeout(() => S.pulses.set(id, { t0: performance.now(), lvl, col }), i * 110));
        (p.recalled.facts || []).forEach((fid, i) =>
          setTimeout(() => spawnEdgeParticles(fid, col), 200 + i * 130));
        (p.recalled.lessons || []).forEach((lid) => pulseLessonCard(lid));
        const n = (p.recalled.facts || []).length + (p.recalled.episodes || []).length
                + (p.recalled.lessons || []).length;
        narrate(`<b>recall()</b> — activation spreads · ${n} memories, ` +
          `<b>${p.tokens} tok</b> of ${p.budget} budget — never full history`, "◉");
      }
      break;
    case "chat": renderChatEvent(p); break;
    case "session":
      sysNote(`— session #${p.session} · Agent ${p.agent_id} · context wiped, memory persists —`);
      if (animate) narrate("<b>new session</b> — chat context wiped · only memory survives", "⏻");
      break;
    case "sleep_report": dreamSequence(p.report, animate); break;
    case "forget":
      Object.values(p.removed).flat().forEach((id) => {
        S.facts.delete(id); S.lessons.delete(id); S.entities.delete(id);
        S.episodes = S.episodes.filter((e) => e.id !== id);
      });
      renderLessonDock();
      if (animate) showMoment("FORGOTTEN · 已遗忘",
        "Hard-deleted with derived beliefs — provenance-traced.", "dispute", 4200);
      break;
    case "benchmark_result": showBenchmark(p.result); break;
  }
  if (S.t0 === null) S.t0 = ev.ts;
  updateKPIs();
}

function revisionCinematic(p) {
  addFact(p.old_fact);
  S.flashes.set(p.old_fact.id, performance.now());
  const a = S.pos.get(p.old_fact.subject), b = S.pos.get(p.old_fact.object);
  if (a && b) camPush({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, 1.22, 5200);
  narrate(`<b>belief revision</b> — old belief closed with a validity interval, ` +
    `new belief active`, "⚠");
  showMoment("BELIEF REVISED · 温故而知新",
    `${p.old_fact.subject_name} ${p.old_fact.relation.replaceAll("_", " ")} ` +
    `${p.old_fact.object_name} → ${p.new_fact.object_name}` +
    (p.rationale ? ` — ${p.rationale}` : ""), "revision", 6500);
  setTimeout(() => { addFact(p.new_fact); }, 900);
}

/* ============================ narrator ================================ */
let narTimer = null;
function narrate(html, icon) {
  $("narText").innerHTML = html;
  $("narIcon").textContent = icon || "◈";
  $("narrator").classList.remove("hidden");
  clearTimeout(narTimer);
  if (S.mode !== "demo")
    narTimer = setTimeout(() => $("narrator").classList.add("hidden"), 6000);
}

/* ======================== chapter interstitial ======================== */
async function chapterCard(p) {
  $("chAct").textContent = p.act === "∎" ? "" : "ACT " + (p.act || "");
  $("chTitle").innerHTML = esc(p.title || "") +
    (p.cn ? ` <span class="cn">${esc(p.cn)}</span>` : "");
  $("chSub").textContent = p.sub || "";
  const ch = $("chapter");
  ch.classList.remove("hidden", "leaving");
  await sleepMs(3400);
  ch.classList.add("leaving");
  await sleepMs(480);
  ch.classList.add("hidden"); ch.classList.remove("leaving");
}
function spawnEdgeParticles(fid, col) {
  const f = S.facts.get(fid);
  if (!f) return;
  S.flashes.set(fid, performance.now());
  for (let i = 0; i < 3; i++)
    S.particles.push({ fid, t0: performance.now() + i * 260, dur: 1000, col });
}
const agentColor = (a) => (S.meta && S.meta.agents[a] ? S.meta.agents[a].color : "#8fd8ff");

/* ============================== moments =============================== */
let momentTimer = null;
function showMoment(kicker, body, cls, dur) {
  const m = $("moment");
  m.className = cls || "";
  $("mKicker").textContent = kicker;
  $("mBody").textContent = body;
  m.classList.remove("hidden");
  clearTimeout(momentTimer);
  momentTimer = setTimeout(() => m.classList.add("hidden"), dur || 5000);
}

/* ================================ KPIs ================================ */
const tweens = {};
function tweenNum(id, target, fmt) {
  const el = $(id); if (!el) return;
  const from = tweens[id] || 0;
  if (from === target) return;
  tweens[id] = target;
  const t0 = performance.now();
  (function step() {
    const k = Math.min(1, (performance.now() - t0) / 700);
    const v = Math.round(from + (target - from) * (1 - Math.pow(1 - k, 3)));
    el.textContent = fmt ? fmt(v) : v;
    if (k < 1) requestAnimationFrame(step);
  })();
}
function updateKPIs() {
  tweenNum("kFacts", [...S.facts.values()].filter((f) => f.status === "ACTIVE").length);
  tweenNum("kLessons", [...S.lessons.values()].filter((l) => l.status !== "retired").length);
  tweenNum("kEpisodes", S.episodes.length);
}
function updateSaveKPI(used, full) {
  if (full <= 0) return;
  const pct = Math.max(0, Math.round(100 * (1 - used / full)));
  tweenNum("kSave", pct, (v) => v + "%");
}

/* =============================== polling ============================== */
async function poll() {
  if (S.mode !== "live") return;
  try {
    const { events } = await api(`/api/events?after=${S.lastSeq}`);
    let structural = false;
    for (const ev of events) {
      S.lastSeq = Math.max(S.lastSeq, ev.seq);
      handleEvent(ev);
      if (["fact_created", "revision", "sleep_report", "forget", "entity_created",
           "lesson_created", "disputed"].includes(ev.type)) structural = true;
    }
    if (structural) setTimeout(refreshGraph, 1200);
  } catch (e) { /* transient */ }
}
async function refreshGraph(asOf) {
  const g = await api("/api/graph" + (asOf ? `?as_of=${asOf}` : ""));
  applyGraph(g);
  return g;
}

/* ================================ chat ================================ */
function msgEl(role, text, agentId) {
  const d = document.createElement("div");
  d.className = `msg ${role} agent${agentId || S.agent}`;
  if (role === "assistant") {
    const name = S.meta ? S.meta.agents[agentId || S.agent].name : "";
    d.innerHTML = `<div class="who">${name}</div>`;
  }
  d.appendChild(Object.assign(document.createElement("div"), { textContent: text }));
  $("messages").appendChild(d);
  $("messages").scrollTop = 1e9;
  return d;
}
function sysNote(t) {
  const d = document.createElement("div");
  d.className = "sysNote"; d.textContent = t;
  $("messages").appendChild(d); $("messages").scrollTop = 1e9;
}
function renderChatEvent(p) {
  const k = S.skipChat.indexOf(p.role + "|" + p.text);
  if (k >= 0) { S.skipChat.splice(k, 1); if (p.gauge) setGauge(p.gauge); return; }
  const el = msgEl(p.role, p.text, p.agent_id);
  if (p.receipt) attachReceipt(el, p.receipt);
  if (p.gauge) setGauge(p.gauge);
}
function attachReceipt(el, r) {
  const n = r.facts.length + r.episodes.length + r.lessons.length;
  if (!n && !(r.cut || []).length) return;
  const d = document.createElement("div");
  d.className = "receipt";
  d.innerHTML = `<div class="rTitle">memory receipt · ${n} recalled` +
    ((r.cut || []).length ? ` · ${r.cut.length} cut by budget` : "") + `</div>`;
  const strip = (t) => t.replace(/^\[[^\]]*\]\s*/, "");
  const mk = (txt, cls, id) => {
    const c = document.createElement("span");
    c.className = "chip " + cls;
    c.textContent = txt.length > 54 ? txt.slice(0, 51) + "…" : txt;
    c.title = txt;
    c.onclick = () => { inspect(id); spawnEdgeParticles(id, "#ffffff");
      S.pulses.set(id, { t0: performance.now(), lvl: 1, col: "#ffffff" }); };
    d.appendChild(c);
  };
  const chips = [];
  r.facts.forEach((t, i) => chips.push([strip(t),
    t.includes("HISTORICAL") ? "hist" : "fact", r.ids.facts[i]]));
  r.lessons.forEach((t, i) => chips.push(["◈ " + strip(t), "lesson", r.ids.lessons[i]]));
  r.episodes.forEach((t, i) => chips.push(["⏱ " + strip(t), "episode", r.ids.episodes[i]]));
  const CAP = 5;
  chips.slice(0, CAP).forEach((c) => mk(...c));
  if (chips.length > CAP) {
    const more = document.createElement("span");
    more.className = "chip";
    more.textContent = `+${chips.length - CAP} more`;
    more.onclick = () => { more.remove(); chips.slice(CAP).forEach((c) => mk(...c)); };
    d.appendChild(more);
  }
  el.appendChild(d);
}
function setGauge(g) {
  S.lastGauge = g;
  const max = Math.max(g.full_history_tokens, g.context_tokens, 1);
  $("gCtx").style.width = (100 * g.context_tokens / max) + "%";
  $("gGhost").style.width = (100 * g.full_history_tokens / max) + "%";
  $("gNums").textContent =
    `${g.context_tokens} tok vs ${g.full_history_tokens} full-history`;
  updateSaveKPI(g.context_tokens, g.full_history_tokens);
}

async function send() {
  const t = $("chatInput").value.trim();
  if (!t || S.busy || S.mode === "demo") return;
  $("chatInput").value = ""; S.busy = true; $("sendBtn").disabled = true;
  msgEl("user", t); S.skipChat.push("user|" + t);
  const typing = msgEl("assistant", "remembering");
  typing.classList.add("typing");
  try {
    const out = await api("/api/chat", { agent_id: S.agent, message: t });
    typing.remove();
    if (out.error) sysNote("error: " + out.error);
    else {
      S.skipChat.push("assistant|" + out.reply);
      const el = msgEl("assistant", out.reply);
      attachReceipt(el, out.receipt); setGauge(out.gauge);
    }
  } catch (e) { typing.remove(); sysNote("network error: " + e); }
  S.busy = false; $("sendBtn").disabled = false; $("chatInput").focus();
}

/* ============================ lesson dock ============================= */
function renderLessonDock() {
  const dock = $("lessonDock");
  dock.innerHTML = "";
  for (const l of S.lessons.values()) {
    if (l.status === "retired") continue;
    const d = document.createElement("div");
    d.className = "lessonCard"; d.id = "lc_" + l.id;
    d.innerHTML = `<div class="lk">lesson · 经验
        <span class="score">✓ ${l.times_helpful || 0}/${l.times_applied || 0}</span></div>
      <div class="lt">WHEN ${esc(l.trigger)}</div>
      <div class="lg">→ ${esc(l.guidance)}</div>`;
    d.onclick = () => inspect(l.id);
    dock.appendChild(d);
  }
}
function pulseLessonCard(lid) {
  const el = $("lc_" + lid);
  if (!el) return;
  el.classList.add("pulse");
  setTimeout(() => el.classList.remove("pulse"), 2600);
}
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));

/* ============================== physics =============================== */
function stageCenter() {
  return { x: innerWidth * .56, y: innerHeight * .46 };
}
function stepPhysics() {
  const ids = [...S.entities.keys()];
  const ctr = stageCenter();
  for (const id of ids) if (!S.pos.has(id)) addEntity(S.entities.get(id));
  for (let i = 0; i < ids.length; i++) {
    const a = S.pos.get(ids[i]);
    for (let j = i + 1; j < ids.length; j++) {
      const b = S.pos.get(ids[j]);
      let dx = a.x - b.x, dy = a.y - b.y;
      const d2 = dx * dx + dy * dy + .01, d = Math.sqrt(d2);
      const f = Math.min(9, 5200 / d2);
      dx /= d; dy /= d;
      a.vx += dx * f; a.vy += dy * f; b.vx -= dx * f; b.vy -= dy * f;
    }
  }
  for (const f of S.facts.values()) {
    const a = S.pos.get(f.subject), b = S.pos.get(f.object);
    if (!a || !b) continue;
    const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) + .01;
    const k = .0045 * (d - 150);
    a.vx += (dx / d) * k; a.vy += (dy / d) * k;
    b.vx -= (dx / d) * k; b.vy -= (dy / d) * k;
  }
  for (const id of ids) {
    const p = S.pos.get(id);
    p.vx += (ctr.x - p.x) * .0015; p.vy += (ctr.y - p.y) * .0015;
    p.vx *= .85; p.vy *= .85; p.x += p.vx; p.y += p.vy;
  }
}

/* =============================== render =============================== */
const easeOutBack = (k) => 1 + 2.4 * Math.pow(k - 1, 3) + 1.4 * Math.pow(k - 1, 2);
function curveMid(a, b) {
  const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
  const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) + .01;
  return { x: mx - dy / d * 22, y: my + dx / d * 22 };
}
const qPoint = (a, c, b, t) => ({
  x: (1 - t) * (1 - t) * a.x + 2 * (1 - t) * t * c.x + t * t * b.x,
  y: (1 - t) * (1 - t) * a.y + 2 * (1 - t) * t * c.y + t * t * b.y });

function render(tms) {
  renderBG(tms);
  const c = $("canvas"), ctx = c.getContext("2d");
  const W = innerWidth, H = innerHeight, d = devicePixelRatio || 1;
  if (c.width !== W * d) { c.width = W * d; c.height = H * d; }
  ctx.setTransform(d, 0, 0, d, 0, 0);
  ctx.clearRect(0, 0, W, H);
  stepPhysics();
  const at = S.asOf || nowS();
  const tNow = performance.now();

  /* ---- camera: slow breathing zoom; focus-push on revision; exhale on
     sleep. Graph layers render under the camera, HUD stays fixed. ---- */
  const cam = S.cam, ctr = stageCenter();
  let tS, tX, tY;
  if (cam.until > tNow && cam.focus) {
    tS = cam.fScale;
    tX = W * .56 - cam.focus.x * tS;
    tY = H * .44 - cam.focus.y * tS;
  } else if (S.dream > tNow) {
    tS = .94; tX = ctr.x * (1 - tS); tY = ctr.y * (1 - tS);
  } else {
    tS = 1 + .035 * (.5 + .5 * Math.sin(tms * .00006));
    tX = ctr.x * (1 - tS); tY = ctr.y * (1 - tS);
  }
  cam.s += (tS - cam.s) * .05;
  cam.x += (tX - cam.x) * .05;
  cam.y += (tY - cam.y) * .05;
  ctx.setTransform(d * cam.s, 0, 0, d * cam.s, d * cam.x, d * cam.y);

  /* ---- edges (curved, gradient, dash for historical) ---- */
  for (const f of S.facts.values()) {
    const a = S.pos.get(f.subject), b = S.pos.get(f.object);
    if (!a || !b) continue;
    if (S.dissolved.has(f.subject) || S.dissolved.has(f.object)) continue;
    const sal = effSal(f, at);
    const ea = S.entities.get(f.subject), eb = S.entities.get(f.object);
    const mid = curveMid(a, b);
    const flash = S.flashes.get(f.id);
    const fl = flash != null ? Math.max(0, 1 - (tNow - flash) / 1600) : 0;
    let drawK = 1;
    const drawT = S.draws.get(f.id);
    if (drawT != null) {
      drawK = Math.min(1, (tNow - drawT) / 900);
      if (drawK >= 1) S.draws.delete(f.id);
    }
    ctx.save();
    let alpha = sal < .06 ? sal * 4 : .18 + .6 * sal, width = 1.8;
    let grad = ctx.createLinearGradient(a.x, a.y, b.x, b.y);
    if (f.status === "HISTORICAL") {
      ctx.setLineDash([4, 7]); alpha = .34; width = 1.2;
      grad.addColorStop(0, "#475569"); grad.addColorStop(1, "#475569");
    } else if (f.status === "DISPUTED") {
      ctx.setLineDash([2, 4]); alpha = .85; width = 1.8;
      grad.addColorStop(0, "#fb7185"); grad.addColorStop(1, "#fb7185");
    } else {
      grad.addColorStop(0, ea ? nodeColor(ea) : "#7dd3fc");
      grad.addColorStop(1, eb ? nodeColor(eb) : "#7dd3fc");
    }
    if (fl > 0) { width += 2.6 * fl; alpha = Math.max(alpha, .35 + .6 * fl);
      if (f.status !== "HISTORICAL") { grad = ctx.createLinearGradient(a.x, a.y, b.x, b.y);
        grad.addColorStop(0, "#fbbf24"); grad.addColorStop(1, "#f59e0b"); } }
    ctx.strokeStyle = grad; ctx.globalAlpha = Math.min(1, alpha); ctx.lineWidth = width;
    ctx.shadowColor = f.status === "DISPUTED" ? "#fb7185" : "#7ea2ff";
    ctx.shadowBlur = fl > 0 ? 18 : 6;
    if (drawK < 1) {  // edge draws itself in with a bright head
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      const steps = 18;
      for (let i = 1; i <= steps * drawK; i++) {
        const p = qPoint(a, mid, b, i / steps); ctx.lineTo(p.x, p.y);
      }
      ctx.stroke();
      const hp = qPoint(a, mid, b, drawK);
      ctx.globalAlpha = 1; ctx.fillStyle = "#fff"; ctx.shadowBlur = 16;
      ctx.beginPath(); ctx.arc(hp.x, hp.y, 3, 0, 7); ctx.fill();
    } else {
      ctx.beginPath(); ctx.moveTo(a.x, a.y);
      ctx.quadraticCurveTo(mid.x, mid.y, b.x, b.y); ctx.stroke();
    }
    // relation label
    if (S.facts.size < 40 || fl > 0) {
      ctx.setLineDash([]); ctx.shadowBlur = 0;
      ctx.globalAlpha = Math.min(1, alpha + .25);
      ctx.fillStyle = f.status === "HISTORICAL" ? "#626a8c" : "#aab2d4";
      ctx.font = "10px " + FONT; ctx.textAlign = "center";
      const lp = qPoint(a, mid, b, .5);
      ctx.fillText(f.relation.replaceAll("_", " ") +
        (f.status === "HISTORICAL" ? " ·was" : ""), lp.x, lp.y - 5);
    }
    ctx.restore();
  }

  /* ---- recall particles traveling along edges ---- */
  S.particles = S.particles.filter((pt) => tNow - pt.t0 < pt.dur);
  for (const pt of S.particles) {
    if (tNow < pt.t0) continue;
    const f = S.facts.get(pt.fid);
    if (!f) continue;
    const a = S.pos.get(f.subject), b = S.pos.get(f.object);
    if (!a || !b) continue;
    const k = (tNow - pt.t0) / pt.dur;
    const p = qPoint(a, curveMid(a, b), b, k);
    ctx.save();
    ctx.globalAlpha = .9 * (1 - Math.abs(k - .5) * .6);
    ctx.fillStyle = pt.col; ctx.shadowColor = pt.col; ctx.shadowBlur = 14;
    ctx.beginPath(); ctx.arc(p.x, p.y, 2.6, 0, 7); ctx.fill();
    ctx.restore();
  }

  /* ---- entity nodes: glowing orbs — salience IS size & brightness.
     Reinforced memories swell with a ripple; decayed ones shrink below the
     floor and dissolve into dust. ---- */
  for (const [id, e] of S.entities) {
    const p = S.pos.get(id);
    const sal = effSal(e, at);
    const col = nodeColor(e);
    if (sal < .02) {           // fully decayed → dissolve once, stay hidden
      if (!S.dissolved.has(id)) {
        S.dissolved.add(id);
        for (let i = 0; i < 12; i++) {
          const a2 = Math.random() * 6.28, sp = .4 + Math.random() * 1.2;
          S.scatter.push({ x: p.x, y: p.y, vx: Math.cos(a2) * sp,
            vy: Math.sin(a2) * sp, t0: tNow, dur: 1100, col });
        }
      }
      continue;
    }
    S.dissolved.delete(id);
    let r = 4 + 20 * sal;                       // stronger salience mapping
    if (sal < .07) r *= sal / .07;              // shrink toward the floor
    const born = S.born.get(id);
    let bloom = 0;
    if (born != null) {
      const k = (tNow - born) / 800;
      if (k < 1) { r *= easeOutBack(Math.max(.01, k)); bloom = 1 - k; }
      else S.born.delete(id);
    }
    const pulse = S.pulses.get(id);
    let glow = 0, gcol = col;
    if (pulse) {
      const ph = (tNow - pulse.t0) / 1700;
      if (ph < 1) {
        glow = (1 - ph) * pulse.lvl; gcol = pulse.col;
        r *= 1 + .45 * glow;                    // reinforcement visibly swells
        // expanding ripple ring
        ctx.save();
        ctx.globalAlpha = glow * .5;
        ctx.strokeStyle = gcol; ctx.lineWidth = 1.6;
        ctx.beginPath(); ctx.arc(p.x, p.y, r + 10 + 55 * ph, 0, 7); ctx.stroke();
        ctx.restore();
      } else S.pulses.delete(id);
    }
    ctx.save();
    if (glow > 0 || bloom > 0) {
      const rr = r + 34 * Math.max(glow, bloom * .8);
      const g = ctx.createRadialGradient(p.x, p.y, r * .4, p.x, p.y, rr);
      g.addColorStop(0, hexA(gcol, .55 * Math.max(glow, bloom)));
      g.addColorStop(1, hexA(gcol, 0));
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(p.x, p.y, rr, 0, 7); ctx.fill();
    }
    const core = ctx.createRadialGradient(p.x - r * .3, p.y - r * .3, 0, p.x, p.y, r);
    core.addColorStop(0, "#ffffff");
    core.addColorStop(.35, col);
    core.addColorStop(1, hexA(col, .25));
    ctx.globalAlpha = .28 + .72 * sal;
    ctx.fillStyle = core; ctx.shadowColor = col; ctx.shadowBlur = 10 + 22 * sal;
    ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 7); ctx.fill();
    ctx.shadowBlur = 0;
    ctx.globalAlpha = Math.min(1, .5 + .65 * sal);
    ctx.fillStyle = "#eef1ff";
    ctx.font = `600 ${(10 + 3 * sal).toFixed(1)}px ` + FONT;
    ctx.textAlign = "center";
    ctx.fillText(e.name, p.x, p.y - r - 8);
    ctx.restore();
  }

  /* ---- dissolution dust ---- */
  S.scatter = S.scatter.filter((s) => tNow - s.t0 < s.dur);
  for (const s of S.scatter) {
    const k = (tNow - s.t0) / s.dur;
    ctx.save();
    ctx.globalAlpha = (1 - k) * .8;
    ctx.fillStyle = s.col;
    ctx.beginPath();
    ctx.arc(s.x + s.vx * k * 60, s.y + s.vy * k * 60, 1.8 * (1 - k), 0, 7);
    ctx.fill();
    ctx.restore();
  }

  ctx.setTransform(d, 0, 0, d, 0, 0);   // HUD layer: lane is not zoomed
  renderLane(ctx, W, H, at, tNow);
  requestAnimationFrame(render);
}
const FONT = getComputedStyle(document.body).fontFamily || "sans-serif";
function hexA(hex, a) {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${n >> 16},${(n >> 8) & 255},${n & 255},${a})`;
}

/* ---- episodic stream lane ---- */
function laneDots(W, H) {
  const eps = S.episodes.slice(-44);
  const y = H - 58;
  const x0 = 440, x1 = W - 300;
  return eps.map((e, i) => ({ e, x: x0 + i * ((x1 - x0) / Math.max(28, eps.length)), y }));
}
function renderLane(ctx, W, H, at, tNow) {
  ctx.save();
  for (const { e, x, y } of laneDots(W, H)) {
    const sal = effSal(e, at);
    const fly = S.laneFly.find((f) => f.id === e.id);
    let yy = y, scale = 1, extra = 0;
    if (fly) {
      const ph = (tNow - fly.t0) / (fly.mode === "sleep" ? 1300 : 700);
      if (ph >= 1) S.laneFly.splice(S.laneFly.indexOf(fly), 1);
      else if (fly.mode === "in") { yy = y + 36 * (1 - ph); scale = .4 + .6 * ph; extra = 1 - ph; }
      else { const c = stageCenter();
        const k = ph * ph;
        yy = y + (c.y - y) * k; scale = 1 - .6 * ph; extra = .8; }
    }
    const col = e.status === "summary" ? "#fbbf24"
      : e.role === "assistant" ? "#7ea2ff" : "#5eead4";
    ctx.globalAlpha = e.status === "archived" ? .14 : .35 + .6 * sal;
    ctx.fillStyle = col; ctx.shadowColor = col; ctx.shadowBlur = 8 + 14 * extra;
    ctx.beginPath();
    ctx.arc(x, yy, (3 + 3.4 * (e.importance || 2) / 5) * scale, 0, 7); ctx.fill();
    if (e.status === "summary") {
      ctx.globalAlpha = .75; ctx.strokeStyle = col; ctx.lineWidth = 1; ctx.shadowBlur = 0;
      ctx.beginPath(); ctx.arc(x, yy, 7.5 * scale, 0, 7); ctx.stroke();
    }
  }
  ctx.restore();
}

/* =========================== dream / sleep ============================ */
function dreamSequence(rep, animate) {
  if (animate) {
    S.dream = performance.now() + 3600;
    narrate("<b>sleep()</b> — clustering episodes → distilling beliefs & lessons → compressing", "☾");
    showMoment("SLEEP · 记忆巩固中", "distilling episodes into beliefs and lessons…", "sleep", 3400);
    S.episodes.filter((e) => e.status === "raw").forEach((e, i) =>
      setTimeout(() => S.laneFly.push({ id: e.id, t0: performance.now(), mode: "sleep" }), i * 90));
    setTimeout(() => sleepModal(rep), 3400);
  } else sleepModal(rep);
}
function sleepModal(rep) {
  const ratio = rep.tokens_before > 0
    ? Math.round(100 * (1 - rep.tokens_after / rep.tokens_before)) : 0;
  showOverlay(`
    <h2>☾ Sleep report <span class="cn">睡眠报告</span></h2>
    <div class="sub2">consolidation cycle · ${new Date(rep.ts * 1000).toLocaleTimeString()}</div>
    <div class="hugeNum"><span id="odoA">0</span> → <span id="odoB">0</span> tokens</div>
    <div class="sub2">episodic memory compressed by <b style="color:var(--ok)">${ratio}%</b></div>
    <ul>
      <li><b>${rep.episodes_compressed}</b> episodes consolidated into <b>${rep.clusters}</b> summaries</li>
      <li><b>${rep.facts_created.length}</b> new beliefs · <b>${rep.facts_revised.length}</b> revised</li>
      <li><b>${rep.lessons.length}</b> lesson(s) learned from failure patterns</li>
      <li><b>${rep.decayed.length}</b> stale memories decayed away</li>
    </ul>
    <div class="quote"><span class="cn">温故而知新，可以为师矣</span>
      "Review the old and learn the new — that is how one becomes a teacher." — Confucius</div>
    <div class="rowBtns"><button onclick="hideOverlay()">Continue</button></div>`);
  odometer("odoA", rep.tokens_before); odometer("odoB", rep.tokens_after);
  if (S.mode === "demo") setTimeout(hideOverlay, 5200);
}
function odometer(id, target) {
  const el = $(id); const t0 = performance.now();
  (function step() {
    const k = Math.min(1, (performance.now() - t0) / 1200);
    el.textContent = Math.round(target * (1 - Math.pow(1 - k, 3))).toLocaleString();
    if (k < 1) requestAnimationFrame(step);
  })();
}

/* =============================== modal ================================ */
function showOverlay(html) { $("overlayCard").innerHTML = html; $("overlay").classList.remove("hidden"); }
function hideOverlay() { $("overlay").classList.add("hidden"); }
window.hideOverlay = hideOverlay;
$("overlay").onclick = (e) => { if (e.target.id === "overlay") hideOverlay(); };

/* ============================= benchmark ============================== */
function showBenchmark(res) {
  const order = ["full_history", "naive_rag", "engram"];
  const names = { full_history: "Full history", naive_rag: "Naive RAG", engram: "Engram" };
  const maxTok = Math.max(...order.map((k) => res.strategies[k].tokens), 1);
  const row = (k, i, val, maxV, fmt) => `
    <div class="brow"><span class="bl">${names[k]}</span>
    <div class="btrack"><div class="bfill s${i + 1}" style="width:${100 * val / maxV}%"></div></div>
    <span class="bv">${fmt}</span></div>`;
  showOverlay(`<div class="bench">
    <h2>▤ Benchmark <span class="cn">基准测试</span></h2>
    <div class="sub2">same scenario · three memory strategies · LLM-graded vs answer key</div>
    <h4>Answer accuracy</h4>` +
    order.map((k, i) => row(k, i, res.strategies[k].accuracy, 1,
      Math.round(res.strategies[k].accuracy * 100) + "%")).join("") +
    `<h4>Context tokens to answer ${res.questions.length} questions</h4>` +
    order.map((k, i) => row(k, i, res.strategies[k].tokens, maxTok,
      res.strategies[k].tokens.toLocaleString())).join("") +
    `<div class="verdict">${res.verdict}</div>
    <h4>Engram per question</h4><table class="qtable">
    <tr><th>type</th><th>question</th><th></th></tr>` +
    res.strategies.engram.rows.map((r) =>
      `<tr><td>${r.type}</td><td>${esc(r.q)}</td>
       <td class="${r.correct ? "okC" : "badC"}">${r.correct ? "✓" : "✗"}</td></tr>`).join("") +
    `</table><div class="rowBtns"><button onclick="hideOverlay()">Close</button></div></div>`);
}
async function runBenchmark() {
  const st = await api("/api/benchmark/status");
  if (st.state === "running") { toast(st.progress || "benchmark running…"); return; }
  await api("/api/benchmark/start", {});
  toast("Benchmark started — three strategies, real model calls.");
  const iv = setInterval(async () => {
    const s = await api("/api/benchmark/status");
    $("benchBtn").textContent = s.state === "running"
      ? "▤ " + (s.progress || "…").slice(0, 20) : "▤ benchmark";
    if (s.state === "done") { clearInterval(iv); showBenchmark(s.result); }
    if (s.state === "error") { clearInterval(iv); toast("benchmark failed: " + s.error); }
  }, 2500);
}
function toast(t) {
  const d = document.createElement("div");
  d.className = "toast"; d.textContent = t;
  $("toasts").appendChild(d);
  setTimeout(() => d.remove(), 6000);
}

/* =============================== forget =============================== */
async function forgetFlow() {
  const scope = prompt("Forget what? · 遗忘什么？ (e.g. “everything about Lin”)");
  if (!scope) return;
  const { preview } = await api("/api/forget/preview", { scope });
  const n = preview.entities.length + preview.facts.length +
            preview.episodes.length + preview.lessons.length;
  if (!n) { toast("Nothing in memory matches that scope."); return; }
  showOverlay(`<h2>⌫ Forget <span class="cn">遗忘预览</span></h2>
    <div class="sub2">hard delete, provenance-traced — this cannot be undone</div><ul>` +
    preview.entities.map((e) => `<li>entity · ${esc(e.name)}</li>`).join("") +
    preview.facts.map((f) => `<li>belief · ${esc(f.subject_name)} ${esc(f.relation)} ${esc(f.object_name)}</li>`).join("") +
    `<li>${preview.episodes.length} episode(s) · ${preview.lessons.length} lesson(s)</li></ul>
    <div class="rowBtns"><button onclick="hideOverlay()">Cancel</button>
    <button class="danger" id="confirmForget">Delete permanently</button></div>`);
  $("confirmForget").onclick = async () => {
    await api("/api/forget/confirm", { preview });
    hideOverlay(); refreshGraph();
  };
}

/* ============================= demo mode ==============================
   A directed 3-minute replay: human typing, streamed replies, act chapter
   cards, a narrator explaining every engine step, and a time-lapse finale
   where real decay math (accelerated clock, labeled) dissolves the trivia
   while the critical beliefs survive. Every event is from a recorded run. */
async function toggleDemo() {
  if (S.mode === "demo") { exitDemo(); return; }
  const rec = await api("/api/demo/replay");
  if (rec.error) { toast(rec.error); return; }
  S.mode = "demo";
  const run = ++S.demoRun;
  document.body.classList.add("demoFocus");
  $("demoBtn").classList.add("on"); $("demoBtn").textContent = "■ stop";
  $("demoProgress").classList.remove("hidden");
  setPill("demo", "REPLAY · 真实录制回放");
  S.entities.clear(); S.facts.clear(); S.lessons.clear();
  S.episodes = []; S.pos.clear(); S.born.clear();
  S.dissolved.clear(); S.asOf = null;
  $("messages").innerHTML = ""; renderLessonDock();
  narrate("replaying a <b>recorded live run</b> on Qwen — nothing is faked", "▶");
  await sleepMs(900);
  playEvents(rec.events, run);
}

async function playEvents(events, run) {
  const total = events.length;
  for (let i = 0; i < total; i++) {
    if (S.demoRun !== run || S.mode !== "demo") return;
    $("dpFill").style.width = (100 * (i + 1) / total) + "%";
    await playEvent(events[i]);
  }
  if (S.demoRun !== run || S.mode !== "demo") return;
  await timeLapse(run);
  if (S.demoRun !== run || S.mode !== "demo") return;
  closingCard();
  narrate("that's Engram — beliefs that revise · sleep that distills · " +
          "forgetting that protects", "◆");
}

function closingCard() {
  const beliefs = [...S.facts.values()].filter((f) => f.status === "ACTIVE").length;
  const hist = [...S.facts.values()].filter((f) => f.status === "HISTORICAL").length;
  const lessons = [...S.lessons.values()].length;
  const g = S.lastGauge;
  const save = g && g.full_history_tokens > 0
    ? Math.max(0, Math.round(100 * (1 - g.context_tokens / g.full_history_tokens)))
    : null;
  const stat = (v, l) => `<div style="text-align:center"><div class="hugeNum" ` +
    `style="font-size:34px">${v}</div><div class="sub2" style="letter-spacing:.18em;` +
    `text-transform:uppercase;font-size:10px">${l}</div></div>`;
  showOverlay(`
    <div style="text-align:center">
      <div class="chAct">3 MINUTES AGO THIS MIND WAS EMPTY</div>
      <h2 style="font-size:30px;margin-top:10px">A mind, not a database
        <span class="cn">忆枢</span></h2>
      <div style="display:flex;justify-content:center;gap:34px;margin:22px 0 6px">
        ${stat(beliefs, "beliefs held · 信念")}
        ${stat(hist, "beliefs revised · 修正")}
        ${stat(lessons, "lessons learned · 经验")}
        ${save != null ? stat(save + "%", "tokens saved · 省耗") : ""}
      </div>
      <div class="sub2" style="margin-top:10px">every event genuinely produced by
      the engine — recorded live on Qwen · replayed with zero API calls</div>
      <div class="rowBtns" style="justify-content:center">
        <button onclick="window.replayDemo()">▶ replay</button>
        <button onclick="window.endDemo()">explore live</button>
      </div>
    </div>`);
}
window.replayDemo = () => { hideOverlay(); exitDemo(); toggleDemo(); };
window.endDemo = () => { hideOverlay(); exitDemo(); };

async function playEvent(ev) {
  const p = ev.payload;
  switch (ev.type) {
    case "chat":
      if (p.role === "user") await typeUser(p.text, p.agent_id);
      else await streamAssistant(p);
      return;
    case "marker": await chapterCard(p); return;
    case "revision": handleEvent(ev); await sleepMs(3600); return;
    case "sleep_report": handleEvent(ev); await sleepMs(5200); return;
    case "lesson_created": handleEvent(ev); await sleepMs(2200); return;
    case "recall": handleEvent(ev); await sleepMs(1500); return;
    case "session": handleEvent(ev); await sleepMs(1000); return;
    case "disputed": handleEvent(ev); await sleepMs(2400); return;
    default: handleEvent(ev); await sleepMs(300);
  }
}

/* human typing into the real input box, then the bubble */
async function typeUser(text, agentId) {
  const input = $("chatInput");
  input.classList.add("demoTyping"); input.placeholder = "";
  for (const ch of text) {
    if (S.mode !== "demo") { input.value = ""; return; }
    input.value += ch;
    input.scrollLeft = 1e9;
    await sleepMs(",.!?;:—".includes(ch) ? 130 : 20 + Math.random() * 38);
  }
  await sleepMs(320);
  input.value = ""; input.classList.remove("demoTyping");
  input.placeholder = "Say something worth remembering…";
  msgEl("user", text, agentId);
  await sleepMs(250);
}

/* assistant reply streams in word by word */
async function streamAssistant(p) {
  const el = msgEl("assistant", "", p.agent_id);
  const body = el.lastChild;
  const words = p.text.split(" ");
  for (let i = 0; i < words.length; i++) {
    if (S.mode !== "demo") return;
    body.textContent += (i ? " " : "") + words[i];
    $("messages").scrollTop = 1e9;
    await sleepMs(26 + Math.random() * 44);
  }
  if (p.receipt) attachReceipt(el, p.receipt);
  if (p.gauge) setGauge(p.gauge);
  await sleepMs(700);
}

/* time-lapse finale: REAL decay math on an accelerated, labeled clock */
async function timeLapse(run) {
  await sleepMs(1800);                       // let the last recall glow settle
  setPill("demo", "TIME-LAPSE · 遗忘 ×30000");
  narrate("<b>time-lapse</b> — real decay curves, accelerated clock: " +
          "unused memories dissolve, reinforced beliefs survive", "☄");
  showMoment("FORGETTING · 大浪淘沙",
    "three simulated weeks pass — trivia decays below the salience floor and " +
    "dissolves into dust; the dose, the allergy, and Grandpa survive", "sleep", 11500);
  const t0 = nowS(), days = 21, dur = 10500, start = performance.now();
  while (performance.now() - start < dur) {
    if (S.demoRun !== run || S.mode !== "demo") { S.asOf = null; return; }
    const k = (performance.now() - start) / dur;
    S.asOf = t0 + k * k * days * 86400;
    await sleepMs(50);
  }
  await sleepMs(1600);
  setPill("demo", "REPLAY · 真实录制回放");
}

function exitDemo() {
  S.demoRun++;
  S.mode = "live"; S.asOf = null;
  document.body.classList.remove("demoFocus");
  $("demoBtn").classList.remove("on"); $("demoBtn").textContent = "▶ demo";
  $("demoProgress").classList.add("hidden");
  $("narrator").classList.add("hidden");
  $("chapter").classList.add("hidden");
  $("chatInput").value = "";
  $("chatInput").classList.remove("demoTyping");
  setPill("live", "LIVE");
  $("messages").innerHTML = ""; hideOverlay();
  S.entities.clear(); S.facts.clear(); S.lessons.clear(); S.episodes = [];
  S.dissolved.clear();
  refreshGraph();
}
function setPill(cls, txt) {
  const b = $("modePill"); b.className = "pill " + cls; b.textContent = txt;
}

/* ============================== timeline ============================== */
let tlTimer = null;
function timelineInput() {
  if (S.mode === "demo") return;
  const v = +$("timeline").value;
  if (v >= 995) { goLive(); return; }
  const t0 = S.t0 || nowS() - 3600;
  const at = t0 + (v / 1000) * (nowS() - t0);
  S.mode = "past"; S.asOf = at;
  setPill("past", "TIME TRAVEL · " + new Date(at * 1000).toLocaleTimeString());
  clearTimeout(tlTimer);
  tlTimer = setTimeout(() => refreshGraph(at), 130);
}
function goLive() {
  S.mode = "live"; S.asOf = null;
  $("timeline").value = 1000;
  setPill("live", "LIVE");
  refreshGraph();
}

/* ============================= inspector ============================== */
async function inspect(id) {
  const it = await api(`/api/inspect?id=${encodeURIComponent(id)}`);
  if (!it || it.error) return;
  const kv = (k, v) => v != null ? `<div class="kv">${k} · <b>${esc(v)}</b></div>` : "";
  let h = `<h3>${it.kind === "fact"
    ? `${esc(it.subject_name)} —${esc(it.relation)}→ ${esc(it.object_name)}`
    : esc(it.name || it.trigger || it.summary || id)}</h3>`;
  h += kv("kind", it.kind) + kv("status", it.status) +
       kv("salience now", it.salience_now) + kv("confidence", it.confidence) +
       kv("importance", it.importance) +
       kv("valid", it.valid_from_iso ? `${it.valid_from_iso} → ${it.valid_to_iso || "open"}` : null) +
       kv("applied / helped", it.times_applied != null ? `${it.times_applied} / ${it.times_helpful}` : null) +
       kv("guidance", it.guidance);
  const prov = it.provenance_episodes || it.source;
  if (prov && prov.length)
    h += `<div class="prov"><b>provenance · 来源</b><br>` +
      prov.map((p) => `• ${esc(p.summary)}`).join("<br>") + `</div>`;
  h += sparkline(it);
  $("inspBody").innerHTML = h;
  $("inspector").classList.remove("hidden");
}
function sparkline(it) {
  if (it.salience_base == null) return "";
  const pts = [];
  const t0 = it.created_at, t1 = nowS() + 48 * 3600;
  for (let i = 0; i <= 40; i++) {
    const t = t0 + (i / 40) * (t1 - t0);
    pts.push(t < it.last_touch
      ? effSal({ ...it, last_touch: t0 }, t) : effSal(it, t));
  }
  const w = 276, h = 42;
  const path = pts.map((v, i) =>
    `${i ? "L" : "M"}${(i / 40 * w).toFixed(1)},${(h - 3 - v * (h - 8)).toFixed(1)}`).join("");
  return `<div class="prov"><b>salience · 显著度 (→ +48 h)</b>
    <svg width="${w}" height="${h}"><path d="${path}" fill="none"
    stroke="#7ea2ff" stroke-width="2" stroke-linecap="round"/></svg></div>`;
}

/* ================================ init ================================ */
async function init() {
  S.meta = await api("/api/meta");
  $("providerPill").textContent = S.meta.provider + " · " + S.meta.model;
  await refreshGraph();
  const evs = (await api("/api/events?after=0")).events;
  if (evs.length) { S.t0 = evs[0].ts; S.lastSeq = evs[evs.length - 1].seq; }
  requestAnimationFrame(render);
  setInterval(poll, 1300);

  $("sendBtn").onclick = send;
  $("chatInput").onkeydown = (e) => { if (e.key === "Enter") send(); };
  document.querySelectorAll(".agent").forEach((b) => b.onclick = () => {
    S.agent = b.dataset.agent;
    document.querySelectorAll(".agent").forEach((x) =>
      x.classList.toggle("active", x === b));
    sysNote(`— talking to ${S.meta.agents[S.agent].name} —`);
  });
  $("newSessionBtn").onclick = () => api("/api/session/new", { agent_id: S.agent });
  $("sleepBtn").onclick = async () => {
    $("sleepBtn").disabled = true; $("sleepBtn").textContent = "☾ dreaming…";
    try { await api("/api/sleep", {}); } finally {
      $("sleepBtn").disabled = false; $("sleepBtn").textContent = "☾ sleep"; }
  };
  $("forgetBtn").onclick = forgetFlow;
  $("benchBtn").onclick = runBenchmark;
  $("demoBtn").onclick = toggleDemo;
  $("resetBtn").onclick = async () => {
    if (confirm("Wipe ALL memory?")) { await api("/api/reset", {}); location.reload(); }
  };
  $("inspClose").onclick = () => $("inspector").classList.add("hidden");
  $("timeline").oninput = timelineInput;
  $("liveBtn").onclick = goLive;
  $("canvas").onclick = (e) => {
    const id = hitTest(e.clientX, e.clientY);
    if (id) inspect(id);
  };
  $("heroLive").onclick = () => { $("hero").remove();
    sysNote("Say something worth remembering — watch the mind grow on the right."); };
  $("heroDemo").onclick = async () => { $("hero").remove(); toggleDemo(); };
  // filming/judging shortcuts: ?stage skips the hero, ?demo autostarts replay
  if (location.search.includes("stage")) $("hero").remove();
  if (location.search.includes("demo")) { $("hero").remove(); toggleDemo(); }
}
function hitTest(mx, my) {
  // graph layers live under the camera transform; lane is HUD (raw coords)
  const wx = (mx - S.cam.x) / S.cam.s, wy = (my - S.cam.y) / S.cam.s;
  for (const [id] of S.entities) {
    const p = S.pos.get(id);
    if (p && Math.hypot(wx - p.x, wy - p.y) < 18) return id;
  }
  for (const { e, x, y } of laneDots(innerWidth, innerHeight))
    if (Math.hypot(mx - x, my - y) < 9) return e.id;
  for (const f of S.facts.values()) {
    const a = S.pos.get(f.subject), b = S.pos.get(f.object);
    if (!a || !b) continue;
    const m = curveMid(a, b), p = qPoint(a, m, b, .5);
    if (Math.hypot(wx - p.x, wy - p.y) < 15) return f.id;
  }
  return null;
}
init();
