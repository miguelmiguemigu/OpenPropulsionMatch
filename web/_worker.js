// propcalc - Cloudflare Pages "advanced mode" worker.
// Serves the static site and a small API that stores components shared by the team in a D1 database (binding: DB).
// build_site.py replaces __TOKEN_SHA256__ with the SHA-256 of the token derived from the access phrase
// (empty = open site, no phrase needed to share).
const TOKEN_SHA256 = "__TOKEN_SHA256__";
const KINDS = new Set(["motors", "props", "escs", "batteries"]);
const MAX_BODY = 1_000_000, MAX_ENTRIES = 5000, MAX_FILES = 12;

let schemaReady = false;
async function ensureSchema(db) {
  if (schemaReady) return;
  await db.batch([
    db.prepare(`CREATE TABLE IF NOT EXISTS entries (id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
      row TEXT NOT NULL, files TEXT NOT NULL DEFAULT '{}', author TEXT, created INTEGER, updated INTEGER, deleted INTEGER DEFAULT 0)`),
    db.prepare(`CREATE TABLE IF NOT EXISTS history (hid INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT, kind TEXT, name TEXT,
      action TEXT, author TEXT, at INTEGER, snapshot TEXT)`),
  ]);
  schemaReady = true;
}

const json = (data, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });
const fail = (msg, status = 400) => json({ error: msg }, status);

async function sha256hex(s) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}
async function authorized(request) {
  if (!TOKEN_SHA256) return true;
  const tok = request.headers.get("x-propcalc-token") || "";
  const h = await sha256hex(tok);
  let diff = h.length ^ TOKEN_SHA256.length;
  for (let i = 0; i < Math.min(h.length, TOKEN_SHA256.length); i++) diff |= h.charCodeAt(i) ^ TOKEN_SHA256.charCodeAt(i);
  return diff === 0;
}
const cleanText = (v, max) => String(v ?? "").replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, "").slice(0, max);

function validate(body) {
  if (!body || typeof body !== "object") throw new Error("bad request");
  const kind = String(body.kind || "");
  if (!KINDS.has(kind)) throw new Error("unknown component kind");
  const name = cleanText(body.name, 120).trim();
  if (!name) throw new Error("name is required");
  if (!body.row || typeof body.row !== "object") throw new Error("row is required");
  const row = {};
  const keys = Object.keys(body.row);
  if (keys.length > 40) throw new Error("too many columns");
  for (const k of keys) {
    if (!/^[A-Za-z0-9_]{1,40}$/.test(k)) throw new Error("bad column name " + k);
    row[k] = cleanText(body.row[k], 600);
  }
  row.name = name;
  const files = {};
  const fk = Object.keys(body.files || {});
  if (fk.length > MAX_FILES) throw new Error("too many files");
  for (const p of fk) {
    if (!/^props\/[A-Za-z0-9._\/-]{1,150}$/.test(p) || p.includes("..")) throw new Error("bad file path " + p);
    files[p] = String(body.files[p]);
  }
  return { kind, name, row, files, author: cleanText(body.author, 60).trim() || "someone", builtin: !!body.builtin };
}

async function api(request, env, url) {
  if (!env.DB) return fail("Sharing is not set up: no D1 database is bound to this site (see web/WEB.md).", 503);
  if (!(await authorized(request))) return fail("Wrong or missing access token.", 401);
  const db = env.DB;
  await ensureSchema(db);
  const path = url.pathname.replace(/\/+$/, "");
  const now = Date.now();

  if (path === "/api/ping") return json({ ok: true });

  if (path === "/api/shared" && request.method === "GET") {
    const r = await db.prepare("SELECT id, kind, name, row, files, author, created, updated FROM entries WHERE deleted = 0 ORDER BY updated").all();
    return json({ entries: r.results.map((e) => ({ ...e, row: JSON.parse(e.row), files: JSON.parse(e.files) })) });
  }

  if (path === "/api/shared" && request.method === "PUT") {
    const text = await request.text();
    if (text.length > MAX_BODY) return fail("too large (max 1 MB per component)", 413);
    let e;
    try { e = validate(JSON.parse(text)); } catch (err) { return fail(err.message); }
    const id = `${e.kind}:${e.name.toLowerCase()}`;
    const count = await db.prepare("SELECT COUNT(*) AS n FROM entries").first();
    const exists = await db.prepare("SELECT id FROM entries WHERE id = ?").bind(id).first();
    if (!exists && count.n >= MAX_ENTRIES) return fail("the shared library is full", 507);
    const rowJ = JSON.stringify(e.row), filesJ = JSON.stringify(e.files);
    await db.batch([
      db.prepare(`INSERT INTO entries (id, kind, name, row, files, author, created, updated, deleted) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
        ON CONFLICT(id) DO UPDATE SET name = excluded.name, row = excluded.row, files = excluded.files, author = excluded.author,
        updated = excluded.updated, deleted = 0`).bind(id, e.kind, e.name, rowJ, filesJ, e.author, now, now),
      db.prepare("INSERT INTO history (id, kind, name, action, author, at, snapshot) VALUES (?, ?, ?, ?, ?, ?, ?)")
        .bind(id, e.kind, e.name, exists || e.builtin ? "edited" : "added", e.author, now, rowJ),
    ]);
    return json({ ok: true, id, updated: now });
  }

  if ((path === "/api/delete" || path === "/api/restore") && request.method === "POST") {
    let b; try { b = JSON.parse(await request.text()); } catch (err) { return fail("bad request"); }
    const id = String(b.id || ""); const author = cleanText(b.author, 60).trim() || "someone";
    const label = path === "/api/delete" ? (b.revert ? "reverted" : "deleted") : "restored";
    const cur = await db.prepare("SELECT kind, name, row FROM entries WHERE id = ?").bind(id).first();
    if (!cur) return fail("not found", 404);
    const del = path === "/api/delete";
    await db.batch([
      db.prepare("UPDATE entries SET deleted = ?, updated = ?, author = ? WHERE id = ?").bind(del ? 1 : 0, now, author, id),
      db.prepare("INSERT INTO history (id, kind, name, action, author, at, snapshot) VALUES (?, ?, ?, ?, ?, ?, ?)")
        .bind(id, cur.kind, cur.name, label, author, now, cur.row),
    ]);
    return json({ ok: true });
  }

  if (path === "/api/history" && request.method === "GET") {
    const r = await db.prepare(`SELECT h.id, h.kind, h.name, h.action, h.author, h.at, e.deleted FROM history h
      LEFT JOIN entries e ON e.id = h.id ORDER BY h.hid DESC LIMIT 40`).all();
    return json({ history: r.results });
  }
  return fail("not found", 404);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/")) {
      try { return await api(request, env, url); }
      catch (err) { return fail("server error: " + (err && err.message ? err.message : err), 500); }
    }
    return env.ASSETS.fetch(request);
  },
};
