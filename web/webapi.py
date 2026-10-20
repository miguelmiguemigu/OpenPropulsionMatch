"""Bridge between the web page (Pyodide, in the browser) and propcalc.py.

Every public function takes / returns JSON-friendly values. The data folder lives in Pyodide's in-memory file
system, so changes made on the site last until the page is reloaded (download the zip to keep them).
"""
import csv
import io
import json
import math
import os
import re
import shutil
import traceback
import zipfile

import propcalc as pc

ROOT = "/work"
DATA = os.path.join(ROOT, "data")
FILES = {"motors": "motors.csv", "props": "props.csv", "escs": "escs.csv", "batteries": "batteries.csv"}
EDITABLE = {"motors.csv", "props.csv", "escs.csv", "batteries.csv", "calibration.csv", "config.json", "plane.json"}
LABELS = {"score": "estimated flight score", "avg_thrust": "mean thrust 0-{a} m/s (gf)",
          "static_thrust": "static thrust (gf)", "net_thrust": "mean thrust 0-{a} m/s minus propulsion mass (gf)",
          "thrust_per_watt": "static thrust per watt (g/W)"}


def _safe(fn):
    def wrap(*a, **k):
        try:
            return json.dumps(fn(*a, **k), allow_nan=False, default=_clean)
        except Exception as e:  # noqa
            return json.dumps({"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()})
    wrap.__name__ = fn.__name__
    return wrap


def _clean(v):
    if isinstance(v, float):
        return None if (math.isnan(v) or math.isinf(v)) else v
    return str(v)


def _num(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _read_json(name):
    p = os.path.join(DATA, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def _write_json(name, d):
    with open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)


def _rows(kind):
    return pc._read_csv(os.path.join(DATA, FILES[kind]))


def _write_rows(kind, rows, cols=None):
    path = os.path.join(DATA, FILES[kind])
    if cols is None:
        cols = list(pc._read_csv(path)[0].keys()) if os.path.exists(path) and pc._read_csv(path) else list(rows[0].keys())
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def _components():
    return {k: _rows(k) for k in FILES}


# ----------------------------------------------------------------------------------------------------------------
_BASE = None        # rows of the bundled library (to restore one when a shared override is removed)
_BASE_FILES = {}    # original content of bundled files that a shared component overwrote


def _snapshot_base():
    global _BASE
    if _BASE is None:
        _BASE = {k: {r["name"]: r for r in _rows(k)} for k in FILES}


@_safe
def info():
    _snapshot_base()
    cfg = _read_json("config.json") or pc.DEFAULT_CONFIG
    return dict(config=pc.load_config(DATA), config_file=cfg, plane=_read_json("plane.json"),
                components=_components(), has_motor_list=os.path.exists(os.path.join(DATA, "motor_list_all.csv")),
                defaults=pc.DEFAULT_CONFIG)


def _apply(lib, o):
    """Apply run options from the page to a freshly loaded library."""
    mdl = lib["cfg"]["model"]
    if o.get("limit_mode"):
        mdl["current_limit_mode"] = o["limit_mode"]
    mdl["max_system_current_a"] = float(o["max_current"]) if o.get("max_current") else None
    for k in ("prescreen_props", "prescreen_motors", "plane_evaluate_top"):
        if o.get(k) is not None and o.get(k) != "":
            mdl[k] = int(o[k])
    en = o.get("enabled") or {}
    if "motors" in en:
        s = set(en["motors"])
        for m in lib["motors"]:
            m.include = m.name in s
    if "props" in en:
        s = set(en["props"])
        for p in lib["props"]:
            p.include = p.name in s
    if "escs" in en:
        s = set(en["escs"]); lib["escs"] = [e for e in lib["escs"] if e.name in s]
    if "batteries" in en:
        s = set(en["batteries"]); lib["batteries"] = [b for b in lib["batteries"] if b.name in s]


def _save_settings(o):
    if o.get("config") is not None:
        _write_json("config.json", o["config"])
    if o.get("plane") is not None:
        _write_json("plane.json", o["plane"])


@_safe
def save_settings(opts_json):
    _save_settings(json.loads(opts_json))
    return {"ok": True}


ROW_KEYS = ["motor", "prop", "esc", "battery", "static_gf", "thrust_10ms", "avg_gf", "net_gf", "peak_A",
            "uncapped_peak_A", "limit_A", "static_W", "gpw", "rpm_static", "prop_mass_g", "full_throttle_min",
            "tow_kg", "payload_kg", "score", "mission_mah", "roll_m", "notes"]


@_safe
def run(opts_json):
    o = json.loads(opts_json)
    _save_settings(o)
    lib = pc.load_library(DATA)
    _apply(lib, o)
    if not lib["escs"] or not lib["batteries"] or not any(m.include for m in lib["motors"]) \
            or not any(p.include for p in lib["props"]):
        raise ValueError("select at least one motor, prop, ESC and battery")
    plane = pc.Plane(_read_json("plane.json")) if o.get("use_plane") and _read_json("plane.json") else None
    rk = lib["cfg"]["ranking_without_plane"]
    avg_to = rk.get("avg_up_to_mps", 12)
    metric = "score" if plane else (o.get("metric") or rk.get("metric", "net_thrust"))
    results, key = pc.evaluate_all(lib, plane, metric, avg_to, prescreen=None)
    if not results:
        raise ValueError("no combination passed the rules / current limits")
    top_n = int(o.get("top") or 10)
    rows = [{k: _num(r.get(k)) for k in ROW_KEYS if k in r} for r in results[:top_n]]

    # compact list of every combination for the scatter plot
    names = {k: [] for k in ("motor", "prop", "esc", "battery")}
    idx = {k: {} for k in names}

    def ix(k, v):
        if v not in idx[k]:
            idx[k][v] = len(names[k]); names[k].append(v)
        return idx[k][v]

    if plane:
        xy = [(r["peak_A"], r["payload_kg"]) for r in results]
        scatter_axes = ("Peak battery current (A)", "Max payload (kg)")
    else:
        xy = [(r["prop_mass_g"], r["avg_gf"]) for r in results]
        scatter_axes = ("Propulsion mass: motor + prop + ESC + battery (g)", "Mean thrust (gf)")
    pts = [[ix("motor", r["motor"]), ix("prop", r["prop"]), ix("esc", r["esc"]), ix("battery", r["battery"]),
            round(x, 3), round(y, 4)] for r, (x, y) in zip(results, xy)]

    curves = [dict(v=[p["v"] for p in r["_curve"]], gf=[p["gf"] for p in r["_curve"]],
                   I=[p["I_batt"] for p in r["_curve"]], limit=_num(r["limit_A"])) for r in results[:5]]

    tds = None
    if plane:
        r = results[0]; ev = r["_eval"]; m = r["tow_kg"]
        vmin = max(plane.vstall(m, ev.rho) * 0.9, 1)
        vs = pc._linspace(vmin, 25, 60)
        tds = dict(v_fts=[v / 0.3048 for v in vs], thrust_lbf=[ev._T(v) / 4.448 for v in vs],
                   drag_lbf=[[plane.drag(m, v, ev.rho, n_art=k) / 4.448 for v in vs] for k in (1, 2, 3)],
                   cruise=[plane.mis["cruise_speed_mps"] / 0.3048,
                           plane.drag(m, plane.mis["cruise_speed_mps"], ev.rho) / 4.448],
                   n_art=plane.n_art, tow_kg=m, vstall=plane.vstall(m, ev.rho) / 0.3048)

    buf = "/tmp/results.csv"
    pc.write_csv(results, buf, plane)
    return dict(metric=metric, key=key, label=LABELS[metric].format(a=f"{avg_to:g}"), n=len(results), rows=rows,
                names=names, points=pts, scatter_axes=scatter_axes, curves=curves, tds=tds,
                csv=open(buf, encoding="utf-8").read(), plane=bool(plane),
                net_line=results[0]["net_gf"] if (not plane and key == "net_gf") else None)


@_safe
def single(opts_json):
    o = json.loads(opts_json)
    _save_settings(o)
    lib = pc.load_library(DATA)
    _apply(lib, {k: v for k, v in o.items() if k != "enabled"})
    cfg = lib["cfg"]
    cal = pc.Calibration(lib, cfg["model"]["air_density_kg_m3"])
    find = lambda lst, n, kind: next((x for x in lst if x.name == n), None) or (_ for _ in ()).throw(
        ValueError(f"{kind} '{n}' not found"))
    m = find(lib["motors"], o["motor"], "motor"); p = find(lib["props"], o["prop"], "prop")
    e = find(lib["escs"], o["esc"], "ESC"); b = find(lib["batteries"], o["battery"], "battery")
    s = pc.System(m, p, e, b, cal, cfg)
    crv = s.curve(cfg["model"]["speeds_mps"])
    free = s.curve(cfg["model"]["speeds_mps"], cap=None)
    warn = []
    rules = cfg["rules"]
    if p.D_in > rules["max_prop_diameter_in"] + 1e-9:
        warn.append(f"prop is larger than the {rules['max_prop_diameter_in']:g} in rule")
    if b.cells != rules["battery_cells"] or b.mah > rules["max_battery_mah"]:
        warn.append("battery is outside the rules (cells / mAh)")
    if max(pt["rpm"] for pt in crv) > p.max_rpm:
        warn.append(f"prop over its RPM limit ({p.max_rpm:.0f})")
    pk = max(pt["I_batt"] for pt in free)
    if s.limit is not None and pk > s.limit + 0.5:
        warn.append(f"needs current limit {s.limit:.0f} A (would draw {pk:.0f} A)")
    if m.estimated:
        warn.append("motor Rm/I0 estimated (+/-15 %) - check datasheet")
    out = dict(limit=_num(s.limit), r_line_mohm=s.r_line * 1000, mass_g=s.mass, warnings=warn,
               curve=[{k: _num(pt[k]) for k in ("v", "rpm", "gf", "I_batt", "I_motor", "V_batt", "P_in", "throttle", "limited")}
                      for pt in crv],
               uncapped=[pt["I_batt"] for pt in free])
    pl = _read_json("plane.json")
    if o.get("use_plane") and pl:
        plane = pc.Plane(pl)
        ev = pc.Evaluator(s, plane, cfg); best = ev.best()
        info_ = best["info"] or {}
        out["plane"] = dict(tow_kg=best["m_tow"], payload_kg=best["payload"], payload_lb=best["payload"] / pc.LB,
                            score=best["score"], base_kg=best["m_base"], roll_m=_num(info_.get("roll_m")),
                            climb_mps=_num(info_.get("climb_mps")), cruise_A=_num(info_.get("cruise_A")),
                            mah=_num(info_.get("mah")), usable_mah=ev.usable)
    return out


# ----------------------------------------------------------------------------------------------------------------
# Editing the library (in memory)
# ----------------------------------------------------------------------------------------------------------------
def _validate():
    pc.load_library(DATA)  # raises with a readable message if something is wrong


@_safe
def upsert_row(kind, row_json, old_name=None):
    row = {k: ("" if v is None else str(v)) for k, v in json.loads(row_json).items()}
    if not row.get("name"):
        raise ValueError("name is required")
    path = os.path.join(DATA, FILES[kind]); backup = open(path, encoding="utf-8").read()
    rows = _rows(kind)
    target = old_name or row["name"]
    for i, r in enumerate(rows):
        if r.get("name") == target:
            merged = dict(r); merged.update(row); rows[i] = merged; break
    else:
        rows.append(row)
    _write_rows(kind, rows)
    try:
        _validate()
    except Exception:
        open(path, "w", encoding="utf-8").write(backup)
        raise
    return {"components": _components()}


@_safe
def delete_row(kind, name):
    rows = [r for r in _rows(kind) if r.get("name") != name]
    _write_rows(kind, rows)
    return {"components": _components()}


def _size_from_name(fn):
    m = re.search(r"(\d+(?:\.\d+)?)\s*[xX]\s*(\d+(?:\.\d+)?)", fn)
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


@_safe
def add_prop_file(filename, src_path, meta_json):
    """Upload a prop table: APC PER3 .dat / .json (all RPM blocks) or a csv/txt with columns J, Ct, Cp."""
    meta = json.loads(meta_json or "{}")
    raw = open(src_path, "rb").read()
    txt = raw.decode("utf-8", errors="ignore")
    is_apc = "PROP RPM" in txt.upper() or (filename.lower().endswith(".json") and '"rpm"' in txt)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)
    os.makedirs(os.path.join(DATA, "props", "uploaded"), exist_ok=True)
    rel = f"props/uploaded/{safe}"
    open(os.path.join(DATA, rel), "wb").write(raw)
    d, ptc = _size_from_name(filename)
    d = float(meta.get("diameter_in") or d or 0); ptc = float(meta.get("pitch_in") or ptc or 0)
    if not d:
        raise ValueError("could not read the diameter from the file name - enter it")
    base = os.path.splitext(filename)[0].replace("PER3_", "")
    row = dict(name=meta.get("name") or (f"APC {base} (uploaded)" if is_apc else f"{base} (uploaded)"),
               diameter_in=f"{d:g}", pitch_in=f"{ptc:g}", mass_g=str(meta.get("mass_g") or 30),
               format="apc" if is_apc else "csv", file=rel,
               cal_group=meta.get("cal_group") or ("apc_per3" if is_apc else "uploaded"),
               max_rpm=str(meta.get("max_rpm") or (round(145000 / d) if is_apc else 15000)), include="yes",
               notes="uploaded on the website" + ({"apc_per3": "; scaled like APC data", "uiuc": "; scaled like UIUC data"}
                                                  .get(meta.get("cal_group") or ("apc_per3" if is_apc else ""), "; used as measured")))
    res = json.loads(upsert_row("props", json.dumps(row)))
    if "error" in res:
        raise ValueError(res["error"])
    res["name"] = row["name"]
    return res


@_safe
def import_tunnel(folder, rho, min_rpm, mass_g):
    """folder: one sub-folder per prop (size from the folder name, e.g. 1208), log files named with the airspeed."""
    cfg = pc.load_config(DATA)
    made = pc.import_tunnel(folder, DATA, float(rho), float(min_rpm), float(mass_g), "/tmp/tunnel_out", cfg, plots=False)
    if not made:
        raise ValueError("no usable logs found - one folder per prop named like 1208 (12x8), files named with the "
                         "airspeed (e.g. 'static', '10m-s')")
    return {"components": _components(), "imported": [r["name"] for r in made]}


@_safe
def replace_file(name, src_path):
    if name not in EDITABLE:
        raise ValueError(f"{name} cannot be replaced here (allowed: {', '.join(sorted(EDITABLE))})")
    path = os.path.join(DATA, name)
    backup = open(path, "rb").read() if os.path.exists(path) else None
    open(path, "wb").write(open(src_path, "rb").read())
    try:
        if name.endswith(".json"):
            json.load(open(path, encoding="utf-8"))
        _validate()
    except Exception:
        if backup is not None:
            open(path, "wb").write(backup)
        raise
    return info_dict()


def info_dict():
    return json.loads(info())


@_safe
def motor_search(q, limit=40):
    p = os.path.join(DATA, "motor_list_all.csv")
    if not os.path.exists(p):
        return {"rows": []}
    words = [w for w in q.lower().split() if w]
    out = []
    for r in pc._read_csv(p):
        hay = f"{r.get('brand', '')} {r.get('type', '')} {r.get('kv', '')}".lower()
        if all(w in hay for w in words):
            out.append(r)
            if len(out) >= limit:
                break
    return {"rows": out}


@_safe
def estimate(kv, mass_g):
    rm, i0 = pc.estimate_rm_i0(float(kv), float(mass_g))
    return {"rm_mohm": round(rm, 1), "i0_a": round(i0, 2)}


@_safe
def export_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, _, fs in os.walk(DATA):
            for f in fs:
                full = os.path.join(dp, f)
                z.write(full, os.path.relpath(full, ROOT))
    open("/tmp/data_export.zip", "wb").write(buf.getvalue())
    return {"path": "/tmp/data_export.zip", "bytes": len(buf.getvalue())}


# ----------------------------------------------------------------------------------------------------------------
# Shared (team) library: the page stores components in the site's database and applies them here on load
# ----------------------------------------------------------------------------------------------------------------
def _safe_rel(rel):
    rel = rel.replace("\\", "/")
    if not rel.startswith("props/") or ".." in rel.split("/") or not re.fullmatch(r"[A-Za-z0-9._/-]+", rel):
        raise ValueError(f"bad file path {rel}")
    return rel


@_safe
def entry_payload(kind, names_json):
    """Row + data files of components, ready to be stored in the shared library."""
    out = []
    rows = {r["name"]: r for r in _rows(kind)}
    for n in json.loads(names_json):
        row = rows.get(n)
        if row is None:
            continue
        files = {}
        if kind == "props":
            for rel in (row.get("file") or "").split(";"):
                rel = rel.strip()
                if rel:
                    files[_safe_rel(rel)] = open(os.path.join(DATA, rel), encoding="utf-8", errors="replace").read()
        out.append(dict(kind=kind, name=n, row=row, files=files))
    return {"entries": out}


def _apply_one(e):
    kind = e["kind"]
    if kind not in FILES:
        raise ValueError(f"unknown kind {kind}")
    for rel, text in (e.get("files") or {}).items():
        rel = _safe_rel(rel)
        p = os.path.join(DATA, rel)
        if os.path.exists(p) and rel not in _BASE_FILES:
            _BASE_FILES[rel] = open(p, "rb").read()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
    row = {k: ("" if v is None else str(v)) for k, v in e["row"].items()}
    row["name"] = e["name"]
    rows = _rows(kind)
    for i, x in enumerate(rows):
        if x.get("name") == row["name"]:
            rows[i] = row
            break
    else:
        rows.append(row)
    _write_rows(kind, rows)


@_safe
def apply_shared(entries_json):
    """Merge shared components into the library. A component that would break the library is skipped."""
    _snapshot_base()
    entries = json.loads(entries_json)
    snap = {k: open(os.path.join(DATA, f), encoding="utf-8").read() for k, f in FILES.items()}
    failed = []
    for e in entries:
        _apply_one(e)
    try:
        _validate()
    except Exception:  # find the bad one(s): restore and apply one by one
        for k, f in FILES.items():
            open(os.path.join(DATA, f), "w", encoding="utf-8").write(snap[k])
        for e in entries:
            before = {k: open(os.path.join(DATA, f), encoding="utf-8").read() for k, f in FILES.items()}
            try:
                _apply_one(e)
                _validate()
            except Exception as ex:  # noqa
                for k, f in FILES.items():
                    open(os.path.join(DATA, f), "w", encoding="utf-8").write(before[k])
                failed.append(f"{e.get('name')}: {ex}")
    return {"components": _components(), "failed": failed}


@_safe
def remove_shared(kind, name):
    """A shared component was deleted: drop it, or go back to the bundled version if there was one."""
    _snapshot_base()
    rows = [r for r in _rows(kind) if r.get("name") != name]
    base = (_BASE or {}).get(kind, {}).get(name)
    if base is not None:
        rows.append(base)
        if kind == "props":
            for rel in (base.get("file") or "").split(";"):
                rel = rel.strip()
                if rel in _BASE_FILES:
                    open(os.path.join(DATA, rel), "wb").write(_BASE_FILES[rel])
    _write_rows(kind, rows)
    return {"components": _components(), "restored_builtin": base is not None}


@_safe
def builtin_names():
    _snapshot_base()
    return {k: sorted(v) for k, v in _BASE.items()}
