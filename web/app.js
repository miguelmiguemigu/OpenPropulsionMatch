/* propcalc web front end. All maths runs in Python (propcalc.py) inside a Web Worker via Pyodide. */
"use strict";

// ------------------------------------------------------------------ helpers
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 0) => (v === null || v === undefined || Number.isNaN(v)) ? "–" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const store = {
  get(k) { try { return localStorage.getItem("propcalc." + k); } catch (e) { return null; } },
  set(k, v) { try { v === null ? localStorage.removeItem("propcalc." + k) : localStorage.setItem("propcalc." + k, v); } catch (e) { /* storage off */ } },
};
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
function toast(msg, isErr = false) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast" + (isErr ? " err" : ""); t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), isErr ? 7000 : 3500);
}
function setStatus(text, cls = "") { $("#status-text").textContent = text; $("#status").className = "status " + cls; }
function download(name, data, type = "application/octet-stream") {
  const url = URL.createObjectURL(data instanceof Blob ? data : new Blob([data], { type }));
  const a = document.createElement("a"); a.href = url; a.download = name; document.body.appendChild(a); a.click();
  a.remove(); setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// build_site.py stamps a build id into the file URLs, so a browser can never mix a new page with a cached old
// app.js / worker.js / bundle.bin (the custom domain's browser cache keeps static files for hours)
const BUILD = "__BUILD__";

// ------------------------------------------------------------------ worker RPC
const worker = new Worker("worker.js?v=" + BUILD);
let rpcId = 0; const pending = new Map();
let pyReady = false;
worker.onmessage = (ev) => {
  const m = ev.data;
  if (m.type === "log") return appendLog(m.line);
  if (m.type === "progress") return onProgress(m);
  if (m.type === "pyready") { pyReady = true; if (!state.loaded) setStatus(bundle ? "Ready – enter the phrase" : "Python ready"); return; }
  const p = pending.get(m.id); if (!p) return; pending.delete(m.id);
  if (!m.ok) return p.reject(new Error(m.error));
  if (m.result && m.result.error) { console.error(m.result.trace); return p.reject(new Error(m.result.error)); }
  p.resolve(m.result);
};
worker.onerror = (e) => { setStatus("Python failed to start", "err"); toast("Could not start Python: " + (e.message || e), true); };
function rpc(cmd, args = {}, transfer = []) {
  return new Promise((resolve, reject) => {
    const id = ++rpcId; pending.set(id, { resolve, reject }); worker.postMessage({ id, cmd, args }, transfer);
  });
}
function appendLog(line) {
  const el = $("#log"); el.textContent += line + "\n";
  if (el.textContent.length > 60000) el.textContent = el.textContent.slice(-40000);
  el.scrollTop = el.scrollHeight;
}

// ------------------------------------------------------------------ state
const state = {
  loaded: false, info: null, comps: null, enabled: { motors: new Set(), props: new Set(), escs: new Set(), batteries: new Set() },
  plane: null, config: null, dirty: false, kind: "motors", lastRun: null, charts: {}, busy: false,
};
let bundle = null;

// ------------------------------------------------------------------ access gate + decryption
async function fetchBundle() {
  const r = await fetch("bundle.bin?v=" + BUILD, { cache: "no-cache" });
  if (!r.ok) throw new Error("bundle.bin not found (" + r.status + ") – build the site with web/build_site.py");
  return new Uint8Array(await r.arrayBuffer());
}
const magic = (b) => String.fromCharCode(...b.slice(0, 4));
async function decrypt(b, phrase) {
  if (!crypto.subtle) throw new Error("This page needs https (or localhost) to decrypt.");
  const it = new DataView(b.buffer, b.byteOffset + 4, 4).getUint32(0);
  const salt = b.slice(8, 24), iv = b.slice(24, 36), ct = b.slice(36);
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(phrase), "PBKDF2", false, ["deriveKey"]);
  const key = await crypto.subtle.deriveKey({ name: "PBKDF2", salt, iterations: it, hash: "SHA-256" }, base,
    { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
  return new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv }, key, ct));
}
async function unlock(phrase, remember) {
  let zip;
  try { zip = await decrypt(bundle, phrase); } catch (e) {
    if (e.name === "OperationError") throw new Error("Wrong phrase – try again.");
    throw e;
  }
  store.set("phrase", remember ? phrase : null);
  await start(zip, phrase);
}
async function start(zip, phrase = null) {
  setStatus(pyReady ? "Loading data…" : "Loading Python (first visit can take ~10 s)…");
  const info = await rpc("load", { zip: zip.buffer }, [zip.buffer]);
  state.loaded = true;
  $("#gate").hidden = true; $("#app").hidden = false;
  applyInfo(info, true);
  setStatus("Ready", "ok");
  initShare(phrase);
}

async function boot() {
  try { bundle = await fetchBundle(); } catch (e) { setStatus("No data", "err"); toast(e.message, true); return; }
  const kind = magic(bundle);
  if (kind === "PCB0") { await start(bundle.slice(4)); return; }
  if (kind !== "PCB1") { setStatus("Bad bundle", "err"); toast("bundle.bin is not a propcalc bundle", true); return; }
  if (!pyReady) setStatus("Loading Python…");
  const saved = store.get("phrase");
  if (saved) {
    try { await unlock(saved, true); return; } catch (e) { store.set("phrase", null); }
  }
  $("#gate").hidden = false; $("#gate-input").focus();
}
$("#gate-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const btn = $("#gate-btn"); btn.disabled = true; $("#gate-error").textContent = ""; btn.textContent = "Unlocking…";
  try { await unlock($("#gate-input").value, $("#gate-remember").checked); }
  catch (e) { $("#gate-error").textContent = e.message; }
  finally { btn.disabled = false; btn.textContent = "Unlock"; }
});

// ------------------------------------------------------------------ tabs
function showTab(name) {
  $$(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  $$(".panel").forEach((p) => (p.hidden = p.dataset.panel !== name));
  store.set("tab", name);
  if (name === "components") { renderComponents(); if (share.available && Date.now() - share.last > 60000) refreshShared().catch(() => {}); }
}
$$(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
function markDirty() { state.dirty = true; $("#dirty").hidden = false; }
window.addEventListener("beforeunload", (e) => { if (state.dirty) { e.preventDefault(); e.returnValue = ""; } });

// ------------------------------------------------------------------ apply loaded info
function applyInfo(info, first = false) {
  state.info = info;
  setComponents(info.components, first);
  state.config = JSON.parse(JSON.stringify(info.config));
  const pl = info.plane;
  state.plane = pl ? JSON.parse(JSON.stringify(pl)) : null;
  if (first) {
    const mdl = state.config.model;
    $("#opt-maxcur").value = mdl.max_system_current_a ?? 80;
    $("#opt-limit").value = mdl.current_limit_mode || "cap";
    $("#opt-pp").value = mdl.prescreen_props ?? 40;
    $("#opt-pm").value = mdl.prescreen_motors ?? 15;
    $("#opt-pt").value = mdl.plane_evaluate_top ?? 400;
    $("#opt-2blade").checked = String(mdl.multi_blade_props || "include").toLowerCase() === "exclude";
    $("#opt-metric").value = "net_thrust";
    if (!state.plane) { $("input[name=mode][value=noplane]").checked = true; }
    updateMode();
    const t = store.get("tab"); if (t && $(`.tabs button[data-tab="${t}"]`)) showTab(t);
  }
  renderSettings();
}
const isOn = (r) => !["no", "false", "0"].includes(String(r.include ?? "yes").toLowerCase());
function setComponents(comps, resetEnabled) {
  const old = state.comps; state.comps = comps;
  for (const k of ["motors", "props", "escs", "batteries"]) {
    const en = state.enabled[k];
    if (resetEnabled || !old) {
      en.clear();
      comps[k].forEach((r) => { if (k === "escs" || k === "batteries" || isOn(r)) en.add(r.name); });
    } else {
      const before = new Set(old[k].map((r) => r.name));
      comps[k].forEach((r) => { if (!before.has(r.name) && (k === "escs" || k === "batteries" || isOn(r))) en.add(r.name); });
      for (const n of [...en]) if (!comps[k].some((r) => r.name === n)) en.delete(n);
    }
  }
  fillSingleSelects(); updateSelSummary(); renderComponents();
}
function updateSelSummary() {
  const e = state.enabled, c = state.comps; if (!c) return;
  $("#sel-summary").innerHTML = `Trying <b>${e.motors.size}</b>/${c.motors.length} motors, <b>${e.props.size}</b>/${c.props.length} props, ` +
    `<b>${e.escs.size}</b>/${c.escs.length} ESCs, <b>${e.batteries.size}</b>/${c.batteries.length} batteries ` +
    `– <a href="#" id="to-comp">change</a>`;
  $("#to-comp").onclick = (ev) => { ev.preventDefault(); showTab("components"); };
  $$(".subtabs button").forEach((b) => { $(".count", b).textContent = `${e[b.dataset.kind].size}/${c[b.dataset.kind].length}`; });
}

// ------------------------------------------------------------------ run options
function updateMode() {
  const plane = $("input[name=mode]:checked").value === "plane";
  $("#metric-wrap").style.display = plane ? "none" : "";
}
$$("input[name=mode]").forEach((r) => r.addEventListener("change", updateMode));
function runOptions() {
  const num = (id) => { const v = $(id).value; return v === "" ? null : Number(v); };
  return {
    use_plane: $("input[name=mode]:checked").value === "plane",
    metric: $("#opt-metric").value, max_current: num("#opt-maxcur"), limit_mode: $("#opt-limit").value,
    prescreen_props: num("#opt-pp"), prescreen_motors: num("#opt-pm"), plane_evaluate_top: num("#opt-pt"),
    two_blade_only: $("#opt-2blade").checked,
    top: num("#opt-top") || 10, config: state.config, plane: state.plane,
    enabled: Object.fromEntries(Object.entries(state.enabled).map(([k, v]) => [k, [...v]])),
  };
}

// ------------------------------------------------------------------ progress
const STAGES = { "prescreen props": "Pre-screening props", "prescreen": "Picking reference props", "prescreen motors": "Pre-screening motors",
  "combinations": "Evaluating combinations", "mission simulation": "Simulating the mission" };
let runStart = 0;
function onProgress(m) {
  const pct = m.n ? (m.i / m.n) * 100 : 0;
  $("#progress-fill").style.width = pct.toFixed(1) + "%";
  $("#progress-text").textContent = `${STAGES[m.stage] || m.stage}: ${fmt(m.i)} / ${fmt(m.n)} · ${((performance.now() - runStart) / 1000).toFixed(0)} s`;
}
function busy(on, btn) {
  state.busy = on;
  $$("#run-btn, #s-run").forEach((b) => (b.disabled = on));
  if (btn) btn.textContent = on ? "Working…" : btn.dataset.label;
}
$("#run-btn").dataset.label = "Run search"; $("#s-run").dataset.label = "Calculate";

// ------------------------------------------------------------------ search
$("#run-btn").addEventListener("click", async () => {
  if (state.busy) return;
  const btn = $("#run-btn"); busy(true, btn);
  $("#log").textContent = ""; $("#progress").hidden = false; $("#progress-fill").style.width = "0";
  $("#progress-text").textContent = "Starting…"; runStart = performance.now(); $("#run-time").textContent = "";
  try {
    const res = await rpc("run", runOptions());
    state.lastRun = res;
    $("#run-time").textContent = `${fmt(res.n)} combinations in ${((performance.now() - runStart) / 1000).toFixed(1)} s`;
    renderResults(res);
  } catch (e) { toast(e.message, true); $("#run-time").textContent = ""; }
  finally { busy(false, btn); $("#progress").hidden = true; }
});

const COLS_BASE = [
  ["#", null, "num"], ["Motor", "motor"], ["Prop", "prop"], ["ESC", "esc"], ["Battery", "battery"],
  ["Static gf", "static_gf", "num", 0], ["@10 m/s gf", "thrust_10ms", "num", 0], ["Mean gf", "avg_gf", "num", 0],
  ["Net gf", "net_gf", "num", 0], ["Peak A", "peak_A", "num", 1], ["Motor A", "peak_motor_A", "num", 1],
  ["Mass g", "prop_mass_g", "num", 0], ["g/W", "gpw", "num", 2],
];
const COLS_PLANE = [["Payload kg", "payload_kg", "num", 2], ["Takeoff kg", "tow_kg", "num", 2], ["Mission mAh", "mission_mah", "num", 0], ["Score", "score", "num", 1]];

function renderResults(res) {
  $("#results").hidden = false;
  const noPlane = new Set(["avg_gf", "net_gf", "gpw"]);  // propulsion-only columns, hidden when ranking by flight score
  const cols = res.plane ? [...COLS_BASE.filter(([, k]) => !noPlane.has(k)), ...COLS_PLANE] : COLS_BASE;
  $("#res-title").textContent = `Top ${res.rows.length} of ${fmt(res.n)} by ${res.label}`;
  const head = "<thead><tr>" + cols.map(([l, k, c]) => `<th class="${c || ""} ${k === res.key ? "key" : ""}">${esc(l)}</th>`).join("") + "</tr></thead>";
  const body = res.rows.map((r, i) => {
    const tds = cols.map(([l, k, c, d]) => {
      const v = k === null ? i + 1 : r[k];
      return `<td class="${c || ""} ${k === res.key ? "key" : ""}">${c === "num" && k ? fmt(v, d) : esc(v)}</td>`;
    }).join("");
    const notes = r.notes ? `<tr class="notes"><td></td><td colspan="${cols.length - 1}">⚠ ${esc(r.notes)}</td></tr>` : "";
    return `<tr class="res ${r.notes ? "has-notes" : ""}" data-i="${i}">${tds}</tr>${notes}`;
  }).join("");
  $("#res-table").innerHTML = head + "<tbody>" + body + "</tbody>";
  $$("#res-table tr.res").forEach((tr) => tr.addEventListener("click", () => openSingle(res.rows[+tr.dataset.i])));
  renderBest(res);
  drawResultCharts(res);
}

function renderBest(res) {
  const r = res.rows[0];
  const stats = [
    [fmt(r.static_gf), "static thrust (gf)"], [fmt(r.thrust_10ms), "thrust at 10 m/s (gf)"],
    [fmt(r.net_gf), "net thrust (gf)"], [fmt(r.peak_A, 1), "peak battery current (A)"], [fmt(r.prop_mass_g), "propulsion mass (g)"],
  ];
  if (res.plane) stats.unshift([fmt(r.score, 1), "flight score"], [`${fmt(r.payload_kg, 2)} kg`, `payload (${fmt(r.payload_kg / 0.45359237, 2)} lb)`],
    [`${fmt(r.tow_kg, 2)} kg`, "max takeoff mass"]);
  $("#best-card").innerHTML = `<h2>Best combination</h2>
    <dl class="combo"><dt>Motor</dt><dd>${esc(r.motor)}</dd><dt>Prop</dt><dd>${esc(r.prop)}</dd>
    <dt>ESC</dt><dd>${esc(r.esc)}</dd><dt>Battery</dt><dd>${esc(r.battery)}</dd></dl>
    <div class="stats">${stats.map(([v, l]) => `<div class="stat"><div class="v">${v}</div><div class="l">${esc(l)}</div></div>`).join("")}</div>
    ${r.notes ? `<ul class="warns">${r.notes.split("; ").map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
    <div class="actions"><button class="btn small" id="best-open">Open in “One combination”</button></div>`;
  $("#best-open").onclick = () => openSingle(r);
}

// ------------------------------------------------------------------ charts
function chartBase() {
  const ink = css("--ink"), muted = css("--muted"), line = css("--line");
  Chart.defaults.color = muted; Chart.defaults.borderColor = line;
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  return { ink, muted, line, accent: css("--accent"), grey: css("--grey-series") };
}
function makeChart(id, cfg) {
  if (state.charts[id]) state.charts[id].destroy();
  cfg.options = Object.assign({ responsive: true, maintainAspectRatio: false, animation: false }, cfg.options || {});
  state.charts[id] = new Chart($("#" + id), cfg);
}
const shortLabel = (r) => `${r.motor} · ${r.prop} · ${r.battery}`;

function drawResultCharts(res) {
  const C = chartBase();
  const top = res.rows; const key = res.key; const best = top[0][key];
  // 1. ranking
  $("#ch-rank-title").textContent = `Ranking – ${res.label}`;
  const vals = top.map((r) => r[key]); const lo = Math.min(...vals), hi = Math.max(...vals);
  const pad = Math.max((hi - lo) * 0.3, Math.abs(hi) * 0.01);
  makeChart("ch-rank", {
    type: "bar",
    data: { labels: top.map((r, i) => `#${i + 1} ${shortLabel(r)}`), datasets: [{ data: vals,
      backgroundColor: top.map((_, i) => (i === 0 ? C.accent : C.grey)), borderRadius: 4, barPercentage: 0.75 }] },
    options: { indexAxis: "y", plugins: { legend: { display: false }, tooltip: { callbacks: {
      label: (c) => `${fmt(c.raw, key === "gpw" || key === "score" ? 2 : 0)}  (${c.dataIndex === 0 ? "best" : ((c.raw - best) / best * 100).toFixed(1) + " %"})` } } },
      scales: { x: { min: lo - pad, max: hi + pad * 0.5, title: { display: true, text: res.label + " – axis does not start at zero" } },
        y: { ticks: { autoSkip: false, font: { size: 11 }, callback: function (v) { const s = this.getLabelForValue(v); if (window.innerWidth < 700) return s.split(" ")[0]; return s.length > 40 ? s.slice(0, 38) + "…" : s; } } } } },
  });
  // 2. curves
  const ds = [];
  res.curves.forEach((c, i) => {
    const col = i === 0 ? C.accent : C.grey; const w = i === 0 ? 2.6 : 1.4;
    const name = `#${i + 1} ${shortLabel(top[i])}`;
    ds.push({ label: name, data: c.v.map((v, j) => ({ x: v, y: c.gf[j] })), borderColor: col, backgroundColor: col, borderWidth: w, pointRadius: 2, yAxisID: "y", order: i });
    ds.push({ label: name + " – current", data: c.v.map((v, j) => ({ x: v, y: c.I[j] })), borderColor: col, backgroundColor: col, borderWidth: w * 0.8, borderDash: [5, 4], pointRadius: 0, yAxisID: "y2", order: 10 + i });
  });
  makeChart("ch-curves", {
    type: "line", data: { datasets: ds },
    options: { parsing: true, interaction: { mode: "nearest", intersect: false },
      plugins: { legend: { labels: { filter: (it) => !it.text.endsWith("current"), boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.parsed.y, c.dataset.yAxisID === "y2" ? 1 : 0)} ${c.dataset.yAxisID === "y2" ? "A" : "gf"}` } } },
      scales: { x: { type: "linear", title: { display: true, text: "Airspeed (m/s)" } },
        y: { min: 0, title: { display: true, text: "Thrust (gf) – solid" } },
        y2: { position: "right", min: 0, grid: { display: false }, title: { display: true, text: "Battery current (A) – dashed" } } } },
  });
  // 3. scatter
  const [xl, yl] = res.scatter_axes; $("#ch-scatter-title").textContent = res.plane ? "Payload vs peak current – every combination" : "Mean thrust vs propulsion mass – every combination";
  const pts = res.points.map((p) => ({ x: p[4], y: p[5], p }));
  const sds = [{ label: "combinations", data: pts.slice(1), backgroundColor: C.grey + "99", pointRadius: 2, pointHoverRadius: 5 },
    { label: "#1", data: [pts[0]], backgroundColor: C.accent, pointRadius: 6, pointHoverRadius: 8 }];
  if (res.net_line !== null && res.net_line !== undefined) {
    const xs = pts.map((p) => p.x); const a = Math.min(...xs), b = Math.max(...xs);
    sds.push({ type: "line", label: "equal net thrust to #1 (better = above)", data: [{ x: a, y: a + res.net_line }, { x: b, y: b + res.net_line }],
      borderColor: C.accent, borderDash: [6, 4], borderWidth: 1.2, pointRadius: 0 });
  }
  const nm = res.names;
  makeChart("ch-scatter", {
    type: "scatter", data: { datasets: sds },
    options: { plugins: { legend: { labels: { boxWidth: 10, font: { size: 11 } } }, tooltip: { callbacks: { label: (c) => {
      const p = c.raw.p; if (!p) return c.dataset.label;
      return [`${nm.motor[p[0]]} · ${nm.prop[p[1]]}`, `${nm.esc[p[2]]} · ${nm.battery[p[3]]}`, `${fmt(c.raw.x, 1)} / ${fmt(c.raw.y, res.plane ? 2 : 0)}`]; } } } },
      scales: { x: { title: { display: true, text: xl } }, y: { title: { display: true, text: yl } } } },
  });
  // 4. TDS
  $("#tds-card").hidden = !res.tds;
  if (res.tds) {
    const t = res.tds;
    $("#ch-tds-title").textContent = `Thrust available vs drag at ${fmt(t.tow_kg, 2)} kg – #1 combination (TDS graph)`;
    const shades = [0.45, 0.7, 1.0];
    const dd = [{ label: "Thrust available (full throttle)", data: t.v_fts.map((v, i) => ({ x: v, y: t.thrust_lbf[i] })), borderColor: C.accent, backgroundColor: C.accent, borderWidth: 2.6, pointRadius: 0 }];
    t.drag_lbf.forEach((arr, k) => dd.push({ label: `Drag, ${k + 1} drag article${k ? "s" : ""}`, data: t.v_fts.map((v, i) => ({ x: v, y: arr[i] })),
      borderColor: C.ink, backgroundColor: C.ink, borderWidth: 1.4, pointRadius: 0, borderDash: k === 2 ? [] : (k === 1 ? [6, 3] : [2, 3]) }));
    dd.push({ type: "scatter", label: "cruise design point", data: [{ x: t.cruise[0], y: t.cruise[1] }], backgroundColor: C.ink, pointRadius: 5 });
    makeChart("ch-tds", {
      type: "line", data: { datasets: dd },
      options: { interaction: { mode: "nearest", intersect: false }, plugins: { legend: { labels: { boxWidth: 12, font: { size: 11 } } },
        tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmt(c.parsed.y, 2)} lbf at ${fmt(c.parsed.x, 0)} ft/s` } } },
        scales: { x: { type: "linear", title: { display: true, text: "Flight speed (ft/s)" } }, y: { min: 0, title: { display: true, text: "Force (lbf)" } } } },
    });
  }
}
$("#dl-csv").addEventListener("click", () => state.lastRun && download("results.csv", state.lastRun.csv, "text/csv"));

// ------------------------------------------------------------------ single combination
function fillSingleSelects() {
  const map = { "#s-motor": "motors", "#s-prop": "props", "#s-esc": "escs", "#s-bat": "batteries" };
  for (const [sel, k] of Object.entries(map)) {
    const el = $(sel); const cur = el.value;
    el.innerHTML = state.comps[k].map((r) => `<option>${esc(r.name)}</option>`).join("");
    if (cur && state.comps[k].some((r) => r.name === cur)) el.value = cur;
  }
}
function openSingle(r) {
  $("#s-motor").value = r.motor; $("#s-prop").value = r.prop; $("#s-esc").value = r.esc; $("#s-bat").value = r.battery;
  $("#s-plane").checked = !!(state.lastRun && state.lastRun.plane) || $("#s-plane").checked;
  showTab("single"); runSingle();
}
$("#s-run").addEventListener("click", runSingle);
async function runSingle() {
  if (state.busy) return;
  const btn = $("#s-run"); busy(true, btn);
  try {
    const o = runOptions();
    const res = await rpc("single", { ...o, use_plane: $("#s-plane").checked && !!state.plane,
      motor: $("#s-motor").value, prop: $("#s-prop").value, esc: $("#s-esc").value, battery: $("#s-bat").value });
    renderSingle(res);
  } catch (e) { toast(e.message, true); }
  finally { busy(false, btn); }
}
function renderSingle(res) {
  $("#s-out").hidden = false;
  const c = res.curve; const at = (v) => c.find((p) => p.v === v) || c[0];
  const s = [[fmt(c[0].gf), "static thrust (gf)"], [fmt(at(10).gf), "thrust at 10 m/s (gf)"],
    [fmt(Math.max(...c.map((p) => p.I_batt)), 1), "peak battery current (A)"], [fmt(c[0].rpm), "static RPM"],
    [fmt(c[0].P_in), "static input power (W)"], [fmt(res.mass_g), "propulsion mass (g)"],
    [res.limit_motor ? `${fmt(res.limit_motor)} / ${fmt(res.limit_batt)}` : "none", "current limit motor / battery (A)"],
    [fmt(Math.max(...res.uncapped), 0), "draw without limit (A)"]];
  if (res.plane) {
    const p = res.plane;
    s.unshift([fmt(p.score, 1), "flight score"], [`${fmt(p.payload_kg, 2)} kg`, `payload (${fmt(p.payload_lb, 2)} lb)`],
      [`${fmt(p.tow_kg, 2)} kg`, "max takeoff mass"], [`${fmt(p.roll_m, 1)} m`, "ground roll"],
      [`${fmt(p.climb_mps, 1)} m/s`, "climb rate"], [`${fmt(p.mah)} / ${fmt(p.usable_mah)}`, "mission mAh / usable"]);
  }
  $("#s-summary").innerHTML = `<div class="stats">${s.map(([v, l]) => `<div class="stat"><div class="v">${v}</div><div class="l">${esc(l)}</div></div>`).join("")}</div>` +
    (res.warnings.length ? `<ul class="warns">${res.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : "") +
    (res.plane && res.plane.payload_kg <= 0 ? `<p class="error">This combination cannot fly the mission with the current plane settings.</p>` : "");
  const cols = [["Airspeed m/s", "v", 0], ["RPM", "rpm", 0], ["Thrust gf", "gf", 0], ["Battery A", "I_batt", 1], ["Motor A", "I_motor", 1],
    ["Pack V", "V_batt", 2], ["Input W", "P_in", 0], ["Throttle", "throttle", 2]];
  $("#s-table").innerHTML = "<thead><tr>" + cols.map(([l]) => `<th class="num">${l}</th>`).join("") + "<th></th></tr></thead><tbody>" +
    c.map((p) => "<tr>" + cols.map(([, k, d]) => `<td class="num">${fmt(p[k], d)}</td>`).join("") + `<td>${p.limited ? '<span class="tag grey">current-limited</span>' : ""}</td></tr>`).join("") + "</tbody>";
  const C = chartBase();
  makeChart("ch-single", {
    type: "line",
    data: { datasets: [
      { label: "Thrust (gf)", data: c.map((p) => ({ x: p.v, y: p.gf })), borderColor: C.accent, backgroundColor: C.accent, borderWidth: 2.6, pointRadius: 2, yAxisID: "y" },
      { label: "Battery current (A)", data: c.map((p) => ({ x: p.v, y: p.I_batt })), borderColor: C.ink, backgroundColor: C.ink, borderWidth: 1.4, borderDash: [5, 4], pointRadius: 0, yAxisID: "y2" }] },
    options: { interaction: { mode: "index", intersect: false }, plugins: { legend: { labels: { boxWidth: 12 } } },
      scales: { x: { type: "linear", title: { display: true, text: "Airspeed (m/s)" } }, y: { min: 0, title: { display: true, text: "Thrust (gf)" } },
        y2: { min: 0, position: "right", grid: { display: false }, title: { display: true, text: "Battery current (A)" } } } },
  });
}

// ------------------------------------------------------------------ components
const COMP_COLS = {
  motors: [["Name", "name"], ["Brand", "brand"], ["Kv", "kv", "num"], ["Rm mΩ", "rm_mohm", "num"], ["I0 A", "i0_a", "num"], ["Mass g", "mass_g", "num"], ["Max A", "max_current_a", "num"], ["Rating", "rating_note"]],
  props: [["Name", "name"], ["Dia in", "diameter_in", "num"], ["Pitch in", "pitch_in", "num"], ["Mass g", "mass_g", "num"], ["Data group", "cal_group"], ["Max RPM", "max_rpm", "num"], ["Notes", "notes", "wrap"]],
  escs: [["Name", "name"], ["Cont. A", "cont_a", "num"], ["Burst A", "burst_a", "num"], ["Mass g", "mass_g", "num"], ["R mΩ", "r_mohm", "num"], ["Notes", "notes", "wrap"]],
  batteries: [["Name", "name"], ["Cells", "cells", "num"], ["mAh", "mah", "num"], ["Mass g", "mass_g", "num"], ["R mΩ", "r_mohm", "num"], ["C cont.", "c_cont", "num"], ["Notes", "notes", "wrap"]],
};
const FIELD_META = {
  name: ["Name", "", true], brand: ["Brand"], kv: ["Kv", "rpm/V", true], rm_mohm: ["Winding resistance Rm", "mΩ", true],
  i0_a: ["No-load current I0", "A", true], i0_voltage: ["…measured at", "V"], mass_g: ["Mass", "g", true],
  max_current_a: ["Max current", "A"], rating_note: ["Rating note", "e.g. 70 A / 1 min"], source: ["Source"], include: ["Include (yes/no)"],
  cont_a: ["Continuous current", "A", true], burst_a: ["Burst current", "A"], r_mohm: ["Resistance", "mΩ"],
  cells: ["Cells", "S", true], mah: ["Capacity", "mAh", true], c_cont: ["Continuous C rating", "C", true], c_burst: ["Burst C rating", "C"],
  notes: ["Notes"], diameter_in: ["Diameter", "in", true], pitch_in: ["Pitch", "in"], format: ["Data format"], file: ["Data file"],
  cal_group: ["Calibration group"], max_rpm: ["Max RPM"],
};
const DEFAULT_COLS = {
  motors: ["name", "brand", "kv", "rm_mohm", "i0_a", "i0_voltage", "mass_g", "max_current_a", "rating_note", "source", "include"],
  escs: ["name", "cont_a", "burst_a", "mass_g", "r_mohm", "notes"],
  batteries: ["name", "cells", "mah", "mass_g", "r_mohm", "c_cont", "notes"],
};

$$(".subtabs button").forEach((b) => b.addEventListener("click", () => {
  state.kind = b.dataset.kind; $$(".subtabs button").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
  $("#c-filter").value = ""; renderComponents();
}));
$("#c-filter").addEventListener("input", renderComponents);
$("#c-only").addEventListener("change", renderComponents);
function visibleRows() {
  const q = $("#c-filter").value.toLowerCase().split(/\s+/).filter(Boolean);
  const en = state.enabled[state.kind];
  return state.comps[state.kind].filter((r) => {
    if ($("#c-only").checked && !en.has(r.name)) return false;
    const hay = Object.values(r).join(" ").toLowerCase();
    return q.every((w) => hay.includes(w));
  });
}
function renderComponents() {
  if (!state.comps) return;
  const k = state.kind; const cols = COMP_COLS[k]; const en = state.enabled[k]; const rows = visibleRows();
  $("#c-add").textContent = k === "props" ? "Add (upload below)" : "Add";
  $("#c-add").disabled = k === "props";
  const head = "<thead><tr><th></th>" + cols.map(([l, , c]) => `<th class="${c === "num" ? "num" : ""}">${l}</th>`).join("") + "<th></th></tr></thead>";
  const body = rows.map((r) => {
    const sh = share.entries.get(shareId(k, r.name));
    const est = (k === "motors" && /estimated/i.test((r.source || "") + (r.rating_note || "")) ? '<span class="tag">est.</span>' : "") +
      (sh ? `<span class="tag share" title="${esc(new Date(sh.updated).toLocaleString())}">${share.builtin[k]?.includes(r.name) ? "edited" : "shared"} · ${esc(sh.author)}</span>` : "");
    return `<tr><td><input type="checkbox" data-n="${esc(r.name)}" ${en.has(r.name) ? "checked" : ""} aria-label="use ${esc(r.name)}"></td>` +
      cols.map(([, key, c], i) => `<td class="${c || ""}">${esc(r[key])}${i === 0 ? est : ""}</td>`).join("") +
      `<td><button class="icon-btn" data-edit="${esc(r.name)}">Edit</button><button class="icon-btn" data-del="${esc(r.name)}">Delete</button></td></tr>`;
  }).join("");
  $("#c-table").innerHTML = head + "<tbody>" + (body || `<tr><td colspan="${cols.length + 2}" class="muted">Nothing matches.</td></tr>`) + "</tbody>";
  $$("#c-table input[type=checkbox]").forEach((cb) => cb.addEventListener("change", () => {
    cb.checked ? en.add(cb.dataset.n) : en.delete(cb.dataset.n); updateSelSummary();
  }));
  $$("#c-table [data-edit]").forEach((b) => b.addEventListener("click", () => openRowForm(k, state.comps[k].find((r) => r.name === b.dataset.edit))));
  $$("#c-table [data-del]").forEach((b) => b.addEventListener("click", async () => {
    const name = b.dataset.del; const sh = share.entries.get(shareId(k, name));
    try {
      if (sh) {
        const builtin = share.builtin[k]?.includes(name);
        if (!window.confirm(builtin ? `Remove the team's changes to “${name}” for everyone? (The built-in version comes back.)`
          : `Delete “${name}” from the team library for everyone? (It can be restored under Recent changes.)`)) return;
        await api("delete", { method: "POST", body: JSON.stringify({ id: sh.id, author: author() || "someone", revert: !!builtin }) });
        await refreshShared(); toast(builtin ? "Back to the built-in " + name : "Deleted " + name + " for everyone");
      } else {
        if (!window.confirm(`Remove “${name}” from this browser tab? Built-in components come back when you reload.`)) return;
        const r = await rpc("delete_row", { kind: k, name }); setComponents(r.components); markDirty(); toast("Removed " + name);
      }
    } catch (e) { toast(e.message, true); }
  }));
  updateSelSummary();
}
$("#c-all").addEventListener("click", () => { visibleRows().forEach((r) => state.enabled[state.kind].add(r.name)); renderComponents(); });
$("#c-none").addEventListener("click", () => { visibleRows().forEach((r) => state.enabled[state.kind].delete(r.name)); renderComponents(); });
$("#c-add").addEventListener("click", () => openRowForm(state.kind, null));

// add / edit form (modal)
let modalCtx = null;
function openRowForm(kind, row) {
  const rows = state.comps[kind];
  const cols = rows.length ? Object.keys(rows[0]) : DEFAULT_COLS[kind];
  modalCtx = { kind, old: row ? row.name : null };
  $("#modal-title").textContent = (row ? "Edit " : "Add ") + { motors: "motor", props: "propeller", escs: "ESC", batteries: "battery" }[kind];
  $("#modal-error").textContent = "";
  const initial = row || (kind === "motors" ? { i0_voltage: "10", include: "yes", source: "added on the website" } :
    kind === "batteries" ? { cells: "3" } : {});
  $("#modal-fields").innerHTML = cols.map((c) => {
    const [label, unit, req] = FIELD_META[c] || [c];
    return `<label class="field"><span>${esc(label)}${unit ? ` <span class="muted">(${esc(unit)})</span>` : ""}${req ? " *" : ""}</span>
      <input name="${esc(c)}" value="${esc(initial[c] ?? "")}" ${req ? "required" : ""}></label>`;
  }).join("");
  $("#modal-extra").innerHTML = kind === "motors" ? `
    <div class="row-fields">
      <label class="field"><span>Find in the rcplanes.online list</span><input id="m-search" type="search" placeholder="e.g. sunnysky x2820 1250"></label>
      <div class="field" style="flex:0 0 auto"><span>&nbsp;</span><button type="button" class="btn small" id="m-est">Estimate Rm and I0 from Kv + mass</button></div>
    </div><div id="m-results" class="search-list" hidden></div>` : "";
  if (kind === "motors") {
    let t;
    $("#m-search").addEventListener("input", () => { clearTimeout(t); t = setTimeout(motorSearch, 250); });
    $("#m-est").addEventListener("click", estimateRmI0);
  }
  $("#modal").hidden = false; $("#modal-fields input")?.focus();
}
async function motorSearch() {
  const q = $("#m-search").value.trim(); const box = $("#m-results");
  if (q.length < 2) { box.hidden = true; return; }
  try {
    const r = await rpc("motor_search", { q });
    box.hidden = false;
    box.innerHTML = r.rows.length ? r.rows.map((m, i) => `<button type="button" data-i="${i}">${esc(m.brand)} ${esc(m.type)} – ${fmt(+m.kv)} kV, ${fmt(+m.mass_g)} g, ${m.imax_a ? fmt(+m.imax_a) + " A" : "? A"}${m.discontinued ? " (discontinued)" : ""}</button>`).join("")
      : `<p class="muted small" style="padding:6px 10px;margin:0">No match.</p>`;
    $$("button", box).forEach((b) => b.addEventListener("click", () => {
      const m = r.rows[+b.dataset.i]; const f = $("#modal-form");
      const set = (n, v) => { if (f.elements[n]) f.elements[n].value = v; };
      set("name", `${m.brand} ${m.type}`.trim()); set("brand", m.brand); set("kv", +m.kv); set("mass_g", +m.mass_g);
      set("max_current_a", m.imax_a ? +m.imax_a : ""); set("rating_note", `${m.imax_a ? +m.imax_a : "?"} A, ${m.power_w ? +m.power_w : "?"} W (duration unknown)`);
      set("rm_mohm", m.rm_mohm_est); set("i0_a", m.i0_a_est); set("i0_voltage", 10); set("source", "rcplanes.online list; Rm/I0 estimated");
      box.hidden = true; toast("Filled in – Rm and I0 are estimates, use datasheet values if you have them");
    }));
  } catch (e) { toast(e.message, true); }
}
async function estimateRmI0() {
  const f = $("#modal-form"); const kv = +f.elements.kv.value, m = +f.elements.mass_g.value;
  if (!kv || !m) { $("#modal-error").textContent = "Enter Kv and mass first."; return; }
  const r = await rpc("estimate", { kv, mass_g: m });
  f.elements.rm_mohm.value = r.rm_mohm; f.elements.i0_a.value = r.i0_a; if (f.elements.i0_voltage) f.elements.i0_voltage.value = 10;
  if (f.elements.source && !/estimated/i.test(f.elements.source.value)) f.elements.source.value = (f.elements.source.value ? f.elements.source.value + "; " : "") + "Rm/I0 estimated";
}
function closeModal() { $("#modal").hidden = true; modalCtx = null; }
$("#modal-x").addEventListener("click", closeModal); $("#modal-cancel").addEventListener("click", closeModal);
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#modal").hidden) closeModal(); });
$("#modal-form").addEventListener("submit", async (ev) => {
  ev.preventDefault(); if (!modalCtx) return;
  const row = Object.fromEntries([...new FormData(ev.target).entries()].filter(([k]) => k !== "" && !k.startsWith("m-")));
  delete row["m-search"];
  try {
    const { kind, old } = modalCtx;
    const r = await rpc("upsert_row", { kind, row, old_name: old });
    if (old && old !== row.name && state.enabled[kind].has(old)) state.enabled[kind].add(row.name);
    setComponents(r.components); closeModal();
    const oldShared = old && old !== row.name ? share.entries.get(shareId(kind, old)) : null;
    if (await shareComponents(kind, [row.name])) {
      if (oldShared) await api("delete", { method: "POST", body: JSON.stringify({ id: oldShared.id, author: author() }) }).then(refreshShared).catch(() => {});
      toast("Saved and shared " + row.name);
    } else { markDirty(); toast("Saved " + row.name + " (only in this tab)"); }
  } catch (e) { $("#modal-error").textContent = e.message; }
});

// uploads
const readFile = (f) => f.arrayBuffer();
$("#up-prop-btn").addEventListener("click", async () => {
  const f = $("#up-prop-file").files[0]; if (!f) return toast("Choose a file first", true);
  const meta = { name: $("#up-prop-name").value.trim(), diameter_in: $("#up-prop-d").value, pitch_in: $("#up-prop-p").value, mass_g: $("#up-prop-m").value, cal_group: $("#up-prop-cal").value };
  try {
    const data = await readFile(f);
    const r = await rpc("add_prop_file", { filename: f.name, data, meta }, [data]);
    setComponents(r.components); $("#up-prop-file").value = "";
    if (await shareComponents("props", [r.name])) toast("Propeller added and shared"); else { markDirty(); toast("Propeller added (only in this tab)"); }
    state.kind = "props"; $$(".subtabs button").forEach((x) => x.setAttribute("aria-selected", String(x.dataset.kind === "props"))); renderComponents();
  } catch (e) { toast(e.message, true); }
});
$("#up-tun-btn").addEventListener("click", async () => {
  const all = [...$("#up-tun").files].filter((f) => /\.csv$/i.test(f.name)); if (!all.length) return toast("Choose the folder with the logs first", true);
  const files = [];
  for (const f of all) {
    const parts = (f.webkitRelativePath || f.name).split("/");
    files.push({ path: parts.slice(-2).join("/"), data: await readFile(f) });
  }
  $("#log").textContent = "";
  try {
    const r = await rpc("import_tunnel", { files, rho: $("#up-tun-rho").value, min_rpm: $("#up-tun-rpm").value, mass_g: $("#up-tun-m").value }, files.map((f) => f.data));
    setComponents(r.components);
    const shared = await shareComponents("props", r.imported);
    if (!shared) markDirty();
    toast("Imported" + (shared ? " and shared" : "") + ": " + r.imported.join(", ") + " (details in the Search tab log)");
  } catch (e) { toast(e.message, true); }
});
$("#rep-btn").addEventListener("click", async () => {
  const f = $("#rep-file").files[0]; if (!f) return toast("Choose a file first", true);
  const name = $("#rep-name").value;
  try {
    const data = await readFile(f);
    const info = await rpc("replace_file", { name, data }, [data]);
    applyInfo(info, false); if (name === "motors.csv" || name === "props.csv") setComponents(info.components, true);
    markDirty(); toast(name + " replaced"); $("#rep-file").value = "";
  } catch (e) { toast(e.message, true); }
});
$$("[data-action=export]").forEach((b) => b.addEventListener("click", async () => {
  try {
    await rpc("save_settings", { config: state.config, plane: state.plane });
    const r = await rpc("export_zip");
    download("propcalc-data.zip", new Blob([r.bytes], { type: "application/zip" }));
    toast("Unzip it and replace the data folder in propcalc with it");
  } catch (e) { toast(e.message, true); }
}));

// ------------------------------------------------------------------ team library (shared components)
const SHARE_SALT = "propcalc-share-v1", SHARE_ITER = 100000;   // must match web/build_site.py
const share = { available: false, token: "", entries: new Map(), builtin: {}, last: 0, reason: "" };
const shareId = (kind, name) => `${kind}:${String(name).toLowerCase()}`;
const author = () => $("#share-author").value.trim();
async function shareToken(phrase) {
  if (!phrase) return "";
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(phrase), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", salt: new TextEncoder().encode(SHARE_SALT), iterations: SHARE_ITER, hash: "SHA-256" }, base, 256);
  return [...new Uint8Array(bits)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
async function api(path, opts = {}) {
  const r = await fetch("api/" + path, { ...opts, cache: "no-store",
    headers: { "content-type": "application/json", "x-propcalc-token": share.token } });
  let j = null; try { j = await r.json(); } catch (e) { /* not our API */ }
  if (!j) throw new Error("This host has no team library (see web/WEB.md).");
  if (!r.ok) throw new Error(j.error || "server error " + r.status);
  return j;
}
async function initShare(phrase) {
  $("#share-author").value = store.get("author") || "";
  try {
    share.token = await shareToken(phrase);
    await api("ping");
    share.available = true;
    share.builtin = await rpc("builtin_names");
    await refreshShared();
  } catch (e) { share.available = false; share.reason = e.message; }
  renderShareBar();
}
async function refreshShared() {
  const r = await api("shared");
  share.last = Date.now();
  const fresh = new Map(r.entries.map((e) => [e.id, e]));
  let comps = null;
  for (const [id, e] of share.entries) {
    if (!fresh.has(id)) comps = (await rpc("remove_shared", { kind: e.kind, name: e.name })).components;
  }
  const changed = r.entries.filter((e) => { const o = share.entries.get(e.id); return !o || o.updated !== e.updated || o.name !== e.name; });
  share.entries = fresh;
  if (changed.length) {
    const res = await rpc("apply_shared", { entries: changed });
    comps = res.components;
    if (res.failed.length) toast("Some shared components could not be loaded: " + res.failed.join("; "), true);
  }
  if (comps) setComponents(comps);
  renderShareBar(); renderComponents();
}
async function shareComponents(kind, names) {
  if (!share.available || !$("#share-on").checked || !names.length) return false;
  if (!author()) { toast("Enter your name in the Team library box to share – kept in this tab for now", true); return false; }
  try {
    const p = await rpc("entry_payload", { kind, names });
    for (const e of p.entries) await api("shared", { method: "PUT", body: JSON.stringify({ ...e, author: author(), builtin: !!share.builtin[kind]?.includes(e.name) }) });
    await refreshShared();
    return true;
  } catch (e) { toast("Not shared: " + e.message, true); return false; }
}
const ago = (t) => { const s = (Date.now() - t) / 1000; return s < 90 ? "just now" : s < 5400 ? Math.round(s / 60) + " min ago" : s < 129600 ? Math.round(s / 3600) + " h ago" : new Date(t).toLocaleDateString(); };
function renderShareBar() {
  $("#share-controls").hidden = !share.available;
  if (!share.available) {
    $("#share-status").innerHTML = `<b>Team library off</b> – what you add stays in this browser tab. <span class="muted">${esc(share.reason || "")}</span>`;
    return;
  }
  const n = share.entries.size;
  const lastE = [...share.entries.values()].sort((a, b) => b.updated - a.updated)[0];
  $("#share-status").innerHTML = `<b>Team library</b> – ${n} shared component${n === 1 ? "" : "s"}` +
    (lastE ? ` · last change by ${esc(lastE.author)}, ${ago(lastE.updated)}` : "");
}
$("#share-author").addEventListener("change", () => store.set("author", author() || null));
$("#share-refresh").addEventListener("click", async () => {
  try { await refreshShared(); toast("Team library up to date"); } catch (e) { toast(e.message, true); }
});
$("#share-history").addEventListener("toggle", loadHistory);
async function loadHistory() {
  if (!$("#share-history").open) return;
  const box = $("#share-hist-list"); box.innerHTML = '<p class="muted small">Loading…</p>';
  try {
    const h = (await api("history")).history;
    const kindName = { motors: "motor", props: "prop", escs: "ESC", batteries: "battery" };
    const lastAct = new Map();
    h.forEach((x) => { if (!lastAct.has(x.id)) lastAct.set(x.id, x); });
    box.innerHTML = h.length ? `<table class="data"><tbody>${h.map((x) => `<tr><td class="muted">${ago(x.at)}</td><td>${esc(x.author)}</td>
      <td>${esc(x.action)} ${kindName[x.kind] || x.kind}</td><td>${esc(x.name)}</td>
      <td>${x.deleted && lastAct.get(x.id) === x && (x.action === "deleted" || x.action === "reverted") ? `<button class="icon-btn" data-restore="${esc(x.id)}">Restore</button>` : ""}</td></tr>`).join("")}</tbody></table>`
      : '<p class="muted small">Nothing shared yet.</p>';
    $$("[data-restore]", box).forEach((b) => b.addEventListener("click", async () => {
      try { await api("restore", { method: "POST", body: JSON.stringify({ id: b.dataset.restore, author: author() || "someone" }) });
        await refreshShared(); await loadHistory(); toast("Restored"); } catch (e) { toast(e.message, true); }
    }));
  } catch (e) { box.innerHTML = `<p class="error">${esc(e.message)}</p>`; }
}

// ------------------------------------------------------------------ settings forms
const PLANE_META = {
  "": { title: "Airframe", keys: {
    name: ["Name"], wing_area_m2: ["Wing area", "m²"], span_m: ["Span", "m"], CLmax: ["CL max"], CL_ground_roll: ["CL during ground roll"],
    CD0: ["CD0 – zero-lift drag coefficient"], oswald_e: ["Oswald efficiency e"], rolling_friction: ["Rolling friction μ"],
    liftoff_speed_factor: ["Lift-off speed", "× stall"], takeoff_distance_limit_m: ["Takeoff distance limit", "m (15.24 = 50 ft)"],
    takeoff_margin: ["Takeoff margin", "× limit"], empty_mass_kg: ["Empty airframe mass", "kg, no payload / propulsion"],
    extra_propulsion_mass_g: ["Extra propulsion mass", "g – mount, wires, spinner"], score_factor: ["Score factor", "rules: 1.25"] } },
  drag_articles: { title: "Drag articles", keys: { count: ["Number of articles"], mass_kg_each: ["Mass each", "kg"], CdA_m2_each: ["Drag area CdA each", "m²"] } },
  mission: { title: "Mission", keys: {
    climb_altitude_m: ["Climb to", "m"], climb_speed_factor: ["Climb speed", "× stall"], min_climb_rate_mps: ["Minimum climb rate", "m/s"],
    cruise_speed_mps: ["Cruise speed", "m/s"], min_cruise_speed_factor: ["Cruise at least", "× stall"], cruise_time_s: ["Cruise time", "s"],
    approach_time_s: ["Approach time", "s"], approach_current_a: ["Approach current", "A"] } },
};
const CONFIG_META = {
  rules: { title: "Competition rules", keys: { battery_cells: ["Battery cells", "S"], max_battery_mah: ["Max battery capacity", "mAh"], max_prop_diameter_in: ["Max prop diameter", "in"] } },
  model: { title: "Model", keys: {
    air_density_kg_m3: ["Air density", "kg/m³"], voc_per_cell_fresh: ["Cell voltage, fresh pack", "V"], voc_per_cell_cruise: ["Cell voltage, cruise", "V"],
    wiring_and_plug_mohm: ["Wiring + plugs", "mΩ"], usable_capacity_fraction: ["Usable battery fraction", "0–1"],
    speeds_mps: ["Airspeeds for curves", "m/s, comma separated"], battery_rating: ["Battery rating used", "continuous / burst"] },
    skip: ["current_limit_mode", "max_system_current_a", "prescreen_props", "prescreen_motors", "plane_evaluate_top", "multi_blade_props"] },
  ranking_without_plane: { title: "Propulsion-only ranking", keys: { avg_up_to_mps: ["Average thrust up to", "m/s"] }, skip: ["metric"] },
};
function buildForm(container, obj, meta, onChange) {
  let html = "";
  const field = (path, key, val, m) => {
    const [label, unit] = m || [key];
    const isNum = typeof val === "number"; const isArr = Array.isArray(val);
    const v = isArr ? val.join(", ") : (val ?? "");
    return `<label class="field"><span>${esc(label)}${unit ? ` <span class="muted">(${esc(unit)})</span>` : ""}</span>
      <input data-path="${esc(path)}" data-type="${isArr ? "arr" : isNum ? "num" : "str"}" value="${esc(v)}" ${isNum ? 'type="number" step="any"' : ""}></label>`;
  };
  const groups = Object.keys(meta);
  for (const g of groups) {
    const src = g === "" ? obj : (obj[g] || {});
    const keys = Object.keys(src).filter((k) => !k.startsWith("_") && (g !== "" || typeof src[k] !== "object" || src[k] === null) && !(meta[g].skip || []).includes(k));
    if (!keys.length) continue;
    html += `<div class="fgroup"><h4>${esc(meta[g].title)}</h4><div class="row-fields">` +
      keys.map((k) => field(g ? `${g}.${k}` : k, k, src[k], meta[g].keys[k])).join("") + "</div></div>";
  }
  for (const g of Object.keys(obj).filter((k) => !(k in meta) && !k.startsWith("_") && typeof obj[k] === "object" && obj[k] && !Array.isArray(obj[k]))) {
    const keys = Object.keys(obj[g]).filter((k) => !k.startsWith("_"));
    html += `<div class="fgroup"><h4>${esc(g)}</h4><div class="row-fields">` + keys.map((k) => field(`${g}.${k}`, k, obj[g][k])).join("") + "</div></div>";
  }
  container.innerHTML = html;
  $$("input", container).forEach((inp) => inp.addEventListener("change", () => {
    const parts = inp.dataset.path.split("."); let o = obj;
    for (const p of parts.slice(0, -1)) o = o[p];
    const k = parts[parts.length - 1]; const t = inp.dataset.type;
    if (t === "num") { if (inp.value === "" || Number.isNaN(+inp.value)) { inp.value = o[k]; return toast("Enter a number", true); } o[k] = +inp.value; }
    else if (t === "arr") { const a = inp.value.split(/[,\s]+/).filter(Boolean).map(Number); if (a.some(Number.isNaN) || a.length < 2) { inp.value = o[k].join(", "); return toast("Enter numbers separated by commas", true); } o[k] = a; }
    else o[k] = inp.value;
    onChange();
  }));
}
function renderSettings() {
  if (state.plane) buildForm($("#plane-form"), state.plane, PLANE_META, markDirty);
  else $("#plane-form").innerHTML = '<p class="muted">No plane.json in the data – upload one under Components → Replace a whole data file.</p>';
  buildForm($("#config-form"), state.config, CONFIG_META, markDirty);
}
$("#pl-reset").addEventListener("click", () => { state.plane = JSON.parse(JSON.stringify(state.info.plane)); renderSettings(); });
$("#cf-reset").addEventListener("click", () => { state.config = JSON.parse(JSON.stringify(state.info.config)); renderSettings(); });
$("#pl-dl").addEventListener("click", () => download("plane.json", JSON.stringify(state.plane, null, 2), "application/json"));
$("#cf-dl").addEventListener("click", () => download("config.json", JSON.stringify(state.config, null, 2), "application/json"));

// ------------------------------------------------------------------ go
setStatus("Loading…");
boot().catch((e) => { setStatus("Error", "err"); toast(e.message, true); });
