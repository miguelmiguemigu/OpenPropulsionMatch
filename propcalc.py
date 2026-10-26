#!/usr/bin/env python3
"""
propcalc.py - propulsion combination optimiser for SAE Aero Design Micro Class
================================================================================

Reads component libraries from a data folder, tries every legal combination of
motor x propeller x ESC x battery, ranks them and draws graphs of the best ones.

    data/motors.csv       name,brand,kv,rm_mohm,i0_a,i0_voltage,mass_g,max_current_a,...
    data/props.csv        name,diameter_in,pitch_in,mass_g,format,file,cal_group,max_rpm,include
    data/propellers/*     coefficient tables (csv: J,Ct,Cp | apc: APC PER3 .dat or .json, one table per RPM |
                          uiuc: static;tunnel) - APC tables are in data_2/propellers/APC_DAT_Files/
    data/escs.csv         name,cont_a,burst_a,mass_g,r_mohm
    data/batteries.csv    name,cells,mah,mass_g,r_mohm,c_cont[,c_burst]
    data/calibration.csv  bench points used to scale prop data to real thrust stands
    data/config.json      competition rules + model settings
    data/plane.json       airframe + mission (optional - skip with --no-plane)

Model (per operating point)
    T = Ct*rho*n^2*D^4      P = Cp*rho*n^3*D^5      Q = P/(2*pi*n)      J = V/(n*D)
    I_motor = Q/Kt + I0     I_batt = throttle*I_motor      Kt = 60/(2*pi*Kv)
    V_batt = Voc - I_batt*(R_pack + R_wiring)
    RPM = Kv*(throttle*V_batt - I_motor*(Rm + R_esc))
  An ESC current limit is modelled by reducing throttle until I_motor <= min(motor, ESC rating) and
  I_batt <= min(battery rating, max_system_current_a).

With a plane file the ranking is the estimated flight score: the heaviest
takeoff mass that still (a) lifts off within the takeoff limit, (b) climbs,
(c) finishes the mission on the usable battery charge. Payload = that mass
minus airframe, propulsion and drag articles. Score = factor * payload_lb * N.

Without a plane (--no-plane) the ranking uses a propulsion-only metric
(config.json -> ranking_without_plane, or --metric). The default, net_thrust,
is mean thrust minus the mass of motor + prop + ESC + battery.

Usage
    python propcalc.py                       # optimise with plane.json
    python propcalc.py --no-plane            # propulsion-only ranking
    python propcalc.py --no-plane --metric net_thrust
    python propcalc.py --no-plane --thrust-cal flat   # thrust factor above the bench data: hold (default), linear, flat
    python propcalc.py --data mydata --out results --top 10
    python propcalc.py --import-apc data_2/propellers   # add every APC PER3 .dat/.json in the folder to props.csv
    python propcalc.py --single "HW 2820SL 1250kV" "APC 12x8E" "Skystars Jupiter Single 80A ESC" "CNHL MiniStar 1000 100C"

Outputs (in --out folder): results.csv (every combination), ranking.png,
thrust_curves.png, and with a plane also payload_vs_current.png and
tds_power_curves.png (thrust available vs drag, the Micro TDS graph).

Requires Python 3 only (numpy is optional). Graphs need numpy + matplotlib (pip install numpy matplotlib).
Accuracy: +/-5-10 % until confirmed on a thrust stand. Pack resistances are
usually assumed - measure yours and edit batteries.csv.
"""

import argparse
import bisect
import csv
import glob
import itertools
import json
import math
import os
import re
import statistics
import sys

try:  # the calculation itself is plain Python (also runs in the browser); numpy is only used for the graphs
    import numpy as np
except ImportError:
    np = None

G = 9.80665          # standard gravity: 1 gf = 9.80665 mN by definition
IN = 0.0254
LB = 0.45359237
LBF = 4.4482216152605  # N per lbf

DEFAULT_CONFIG = {
    "rules": {"battery_cells": 3, "max_battery_mah": 1000, "max_prop_diameter_in": 12},
    "model": {"air_density_kg_m3": 1.225, "voc_per_cell_fresh": 4.0, "voc_per_cell_cruise": 3.8,
              "wiring_and_plug_mohm": 5, "usable_capacity_fraction": 0.8,
              "speeds_mps": [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20],
              "current_limit_mode": "cap", "battery_rating": "continuous", "thrust_cal_extrapolation": "hold",
              "multi_blade_props": "include"},
    "ranking_without_plane": {"metric": "net_thrust", "avg_up_to_mps": 12},
}


# =============================================================================
# Small numeric helpers (plain Python, same results as the numpy equivalents)
# =============================================================================
def _interp(x, xs, ys):
    """Linear interpolation like numpy.interp (xs ascending, clamped at the ends)."""
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect.bisect_right(xs, x)
    x0, x1 = xs[i - 1], xs[i]
    if x1 == x0:
        return ys[i]
    return ys[i - 1] + (ys[i] - ys[i - 1]) * (x - x0) / (x1 - x0)


PROGRESS = None  # optional callback(stage, i, n) - used by the web version for its progress bar


def _progress(stage, i, n):
    if PROGRESS is not None and (i == n or i % max(1, n // 100) == 0):
        PROGRESS(stage, i, n)


def _mean(v):
    v = list(v)
    return sum(v) / len(v)


def _linspace(a, b, n):
    step = (b - a) / (n - 1)
    return [a + i * step for i in range(n - 1)] + [b]


def _trapz(y, x):
    return sum((x[i + 1] - x[i]) * (y[i + 1] + y[i]) / 2 for i in range(len(x) - 1))


def _polyval(c, x):
    """c highest power first (numpy.polyval order)."""
    acc = 0.0
    for k in c:
        acc = acc * x + k
    return acc


def _polyfit(x, y, deg):
    """Least-squares polynomial fit (numpy.polyfit order: highest power first)."""
    n = deg + 1
    A = [[sum(xi ** (i + j) for xi in x) for j in range(n)] for i in range(n)]
    b = [sum(yi * xi ** i for xi, yi in zip(x, y)) for i in range(n)]
    for c in range(n):  # Gaussian elimination with partial pivoting
        p = max(range(c, n), key=lambda k: abs(A[k][c]))
        A[c], A[p] = A[p], A[c]; b[c], b[p] = b[p], b[c]
        for k in range(c + 1, n):
            f = A[k][c] / A[c][c]
            for j in range(c, n):
                A[k][j] -= f * A[c][j]
            b[k] -= f * b[c]
    coef = [0.0] * n
    for i in range(n - 1, -1, -1):
        coef[i] = (b[i] - sum(A[i][j] * coef[j] for j in range(i + 1, n))) / A[i][i]
    return coef[::-1]


# =============================================================================
# Loading
# =============================================================================
def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(line for line in f if not line.lstrip().startswith("#"))]
    return [{k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in r.items() if k} for r in rows]


def _f(row, key, default=None):
    v = row.get(key, "")
    if v in ("", None):
        if default is None:
            raise ValueError(f"missing value '{key}' in row {row.get('name', row)}")
        return default
    try:
        return float(v)
    except ValueError:
        raise ValueError(f"'{key}' must be a number in row {row.get('name', '?')} (got '{v}')") from None


def _numeric_rows(path):
    rows = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        for line in f:
            toks = re.split(r"[,\s;]+", line.strip())
            try:
                vals = [float(t) for t in toks if t != ""]
            except ValueError:
                continue
            if vals:
                rows.append(vals)
    return rows


def _find_apc_file(path):
    """An APC table given as PER3_x.json may exist only as PER3_x.dat (or the other way round), in the same
    folder or a sub-folder of it (e.g. propellers/APC_DAT_Files/). Return the first file that exists."""
    if os.path.exists(path):
        return path
    folder, stem = os.path.dirname(path), os.path.splitext(os.path.basename(path))[0]
    for ext in (".dat", ".json"):
        for p in [os.path.join(folder, stem + ext)] + glob.glob(os.path.join(glob.escape(folder), "**", stem + ext),
                                                                 recursive=True):
            if os.path.exists(p):
                return p
    raise FileNotFoundError(f"no APC table {stem}.dat or {stem}.json in {folder} or its sub-folders")


def _apc_label(path):
    """Prop size as APC writes it ('12x4.7SF'): first line of a PER3 .dat, 'propeller' of a .json."""
    try:
        if path.lower().endswith(".json"):
            label = json.load(open(path, encoding="utf-8")).get("propeller", "")
        else:
            with open(path, encoding="utf-8", errors="ignore") as f:
                label = next((ln.split()[0] for ln in f if ln.strip()), "")
    except (OSError, ValueError):
        label = ""
    return label if re.match(r"^\d+(\.\d+)?x\d", label) else os.path.splitext(os.path.basename(path))[0][5:]


def blades_from_name(name):
    """Blade count from the prop name: APC writes 3/4 blades as a suffix ('12x7E-3', '10x6-4'), UIUC as 'n-blade'."""
    m = re.search(r"\d+(?:\.\d+)?x\d+(?:\.\d+)?[A-Za-z]*-(\d)(?![\d.])", name) or re.search(r"(\d)-blade", name, re.I)
    return int(m.group(1)) if m else 2


class Prop:
    """Propeller with one or more Ct/Cp(J) tables, optionally at several RPMs."""

    def __init__(self, row, base):
        self.name = row["name"]
        self.D_in = _f(row, "diameter_in")
        self.D = self.D_in * IN
        self.pitch = _f(row, "pitch_in", 0)
        self.mass = _f(row, "mass_g", 0)
        self.group = row.get("cal_group", "") or "default"
        self.max_rpm = _f(row, "max_rpm", 1e9)
        self.blades = int(_f(row, "blades", blades_from_name(self.name)))  # optional 'blades' column overrides the name
        self.include = (row.get("include", "yes") or "yes").lower() not in ("no", "false", "0")
        fmt = (row.get("format", "csv") or "csv").lower()
        files = [os.path.join(base, p.strip()) for p in row["file"].split(";")]
        self.tables = []  # list of (rpm or None, J[], Ct[], Cp[])
        if fmt == "csv":
            r = [v for v in _numeric_rows(files[0]) if len(v) >= 3]
            self._add(None, [v[0] for v in r], [v[1] for v in r], [v[2] for v in r])
        elif fmt == "apc":
            path = _find_apc_file(files[0])
            if path.lower().endswith(".json"):
                self._load_apc_json(path)
            else:
                self._load_apc(path)
        elif fmt == "uiuc":
            # file = "static.txt;tunnel.txt" (static: RPM CT CP, tunnel: J CT CP eta)
            st = [v for v in _numeric_rows(files[0]) if len(v) >= 3]
            top = max(st, key=lambda v: v[0])
            J, Ct, Cp = [0.0], [top[1]], [top[2]]
            for v in _numeric_rows(files[1]) if len(files) > 1 else []:
                if len(v) >= 3 and v[0] > 0:
                    J.append(v[0]); Ct.append(v[1]); Cp.append(v[2])
            self._add(None, J, Ct, Cp)
        else:
            raise ValueError(f"unknown prop format '{fmt}' for {self.name}")
        if not self.tables:
            raise ValueError(f"no coefficient data read for prop {self.name}")
        self.tables.sort(key=lambda t: (t[0] or 0))

    def _add(self, rpm, J, Ct, Cp):
        order = sorted(range(len(J)), key=lambda i: J[i])
        self.tables.append((rpm, [J[i] for i in order], [Ct[i] for i in order], [Cp[i] for i in order]))

    def _load_apc(self, path):
        """APC PER3_*.dat: blocks headed 'PROP RPM = n', rows V J Pe Ct Cp ..."""
        rpm, J, Ct, Cp = None, [], [], []
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = re.search(r"PROP\s+RPM\s*=\s*([\d.]+)", line)
                if m:
                    if rpm is not None and len(J) > 1:
                        self._add(rpm, J, Ct, Cp)
                    rpm, J, Ct, Cp = float(m.group(1)), [], [], []
                    continue
                toks = line.split()
                if rpm is None or len(toks) < 5:
                    continue
                try:
                    vals = [float(t) for t in toks[:5]]
                except ValueError:
                    continue
                if vals[3] < -0.02:  # past zero thrust
                    continue
                J.append(vals[1]); Ct.append(vals[3]); Cp.append(vals[4])
        if rpm is not None and len(J) > 1:
            self._add(rpm, J, Ct, Cp)

    def _load_apc_json(self, path):
        """APC PER3 data converted to JSON: {"data": [{"rpm", "J", "Ct", "Cp", ...}, ...]}, rows grouped by rpm."""
        by_rpm = {}
        for r in json.load(open(path, encoding="utf-8"))["data"]:
            if r["Ct"] < -0.02:  # past zero thrust
                continue
            by_rpm.setdefault(float(r["rpm"]), []).append(r)
        for rpm, rows in by_rpm.items():
            if len(rows) > 1:
                self._add(rpm, [r["J"] for r in rows], [r["Ct"] for r in rows], [r["Cp"] for r in rows])

    _interp = staticmethod(_interp)

    def coef(self, J, rpm):
        t = self.tables
        if len(t) == 1 or t[0][0] is None:
            _, Js, Ct, Cp = t[0]
            return max(self._interp(J, Js, Ct), 0.0), max(self._interp(J, Js, Cp), 1e-5)
        rpms = [x[0] for x in t]
        if rpm <= rpms[0]:
            a = b = t[0]; w = 0
        elif rpm >= rpms[-1]:
            a = b = t[-1]; w = 0
        else:
            i = bisect.bisect_right(rpms, rpm); a, b = t[i - 1], t[i]; w = (rpm - a[0]) / (b[0] - a[0])
        ct = (1 - w) * self._interp(J, a[1], a[2]) + w * self._interp(J, b[1], b[2])
        cp = (1 - w) * self._interp(J, a[1], a[3]) + w * self._interp(J, b[1], b[3])
        return max(ct, 0.0), max(cp, 1e-5)


class Motor:
    def __init__(self, r):
        self.name = r["name"]; self.brand = r.get("brand", "")
        self.kv = _f(r, "kv"); self.rm = _f(r, "rm_mohm") / 1000
        # no-load current scaled to a 10 V reference (most datasheets quote ~10 V)
        self.i0 = _f(r, "i0_a") * 10.0 / _f(r, "i0_voltage", 10.0)
        self.mass = _f(r, "mass_g"); self.imax = _f(r, "max_current_a", 1e9)
        self.rating = r.get("rating_note", "")
        self.include = (r.get("include", "yes") or "yes").lower() not in ("no", "false", "0")
        self.estimated = "estimated" in (r.get("source", "") + r.get("rating_note", "")).lower()
        self.kt = 60 / (2 * math.pi * self.kv)


class ESC:
    def __init__(self, r):
        self.name = r["name"]; self.cont = _f(r, "cont_a"); self.burst = _f(r, "burst_a", self.cont)
        self.mass = _f(r, "mass_g"); self.r = _f(r, "r_mohm", 3) / 1000


class Battery:
    def __init__(self, r):
        self.name = r["name"]; self.cells = int(_f(r, "cells")); self.mah = _f(r, "mah")
        self.mass = _f(r, "mass_g"); self.r = _f(r, "r_mohm") / 1000
        self.c_cont = _f(r, "c_cont"); self.c_burst = _f(r, "c_burst", self.c_cont)
        self.notes = r.get("notes", "")

    def imax(self, basis="continuous"):
        return (self.c_burst if basis == "burst" else self.c_cont) * self.mah / 1000


def load_config(base):
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    path = os.path.join(base, "config.json")
    if os.path.exists(path):
        for k, v in json.load(open(path, encoding="utf-8")).items():
            if isinstance(v, dict):
                cfg.setdefault(k, {}).update(v)
    return cfg


def load_library(base):
    def opt(name):
        p = os.path.join(base, name)
        return p if os.path.exists(p) else None

    def prop(r):
        try:
            return Prop(r, base)
        except FileNotFoundError as e:
            if (r.get("include", "yes") or "yes").lower() not in ("no", "false", "0"):  # switched-off props stay quiet
                print(f"  skipping prop '{r.get('name')}': {e}")

    cfg = load_config(base)
    lib = dict(
        cfg=cfg,
        motors=[Motor(r) for r in _read_csv(os.path.join(base, "motors.csv"))],
        props=[p for p in map(prop, _read_csv(os.path.join(base, "props.csv"))) if p],
        escs=[ESC(r) for r in _read_csv(os.path.join(base, "escs.csv"))],
        batteries=[Battery(r) for r in _read_csv(os.path.join(base, "batteries.csv"))],
        calibration=_read_csv(opt("calibration.csv")) if opt("calibration.csv") else [],
    )
    return lib


# =============================================================================
# Calibration
# =============================================================================
def tip_speed(rpm, D):
    """Blade tip speed (m/s) of a prop of diameter D (m) at rpm."""
    return math.pi * D * rpm / 60


class Calibration:
    """Per data-source group: torque factor and a thrust factor that depends on the blade tip speed.

    The thrust factor is keyed to tip speed (pi * D * n), not RPM, so bench points measured on a 12 in prop
    carry over to 10-11 in props at the same blade loading / tip Mach number instead of at the same RPM."""

    MODES = ("hold", "linear", "flat")  # thrust factor above the last bench point: last factor / last slope / mean

    def __init__(self, lib, rho):
        self.mode = lib.get("cfg", {}).get("model", {}).get("thrust_cal_extrapolation", "hold")
        if self.mode not in self.MODES:
            raise ValueError(f"thrust_cal_extrapolation must be one of {', '.join(self.MODES)} (got '{self.mode}')")
        self.groups = {}
        self.bench = {}  # group -> True if it has thrust bench points
        motors = {m.name: m for m in lib["motors"]}
        props = {p.name: p for p in lib["props"]}
        used = {p.group for p in lib["props"] if p.include}
        by_group = {}
        for r in lib["calibration"]:
            by_group.setdefault(r["group"], []).append(r)
        for g, rows in by_group.items():
            kq, kt_pts = [], []
            for r in rows:
                m, p = motors.get(r["motor"]), props.get(r["prop"])
                if m is None or p is None:
                    if g in used:  # rows of groups no prop uses (e.g. 'uiuc' without UIUC props) are ignored quietly
                        print(f"  calibration: skipping row ({r['motor']}, {r['prop']}) - not in library")
                    continue
                rpm = float(r["rpm"]); n = rpm / 60
                ct, cp = p.coef(0.0, rpm)
                use = (r.get("use", "both") or "both").lower()
                if use in ("both", "torque"):
                    q_data = cp / (2 * math.pi) * rho * n * n * p.D ** 5
                    kq.append(m.kt * (float(r["current_a"]) - m.i0) / q_data)
                if use in ("both", "thrust"):
                    t_data = ct * rho * n * n * p.D ** 4
                    kt_pts.append((tip_speed(rpm, p.D), float(r["thrust_gf"]) * G / 1000 / t_data))
            if not kq and not kt_pts:
                continue
            by_tip = {}  # bench points at the same tip speed are averaged (equal x would divide by zero in thrust())
            for u, k in kt_pts:
                by_tip.setdefault(round(u, 6), []).append(k)
            self.groups[g] = (_mean(kq) if kq else 1.0, sorted((u, _mean(k)) for u, k in by_tip.items()) or [(0, 1.0)])
            self.bench[g] = bool(by_tip)

    def torque(self, group):
        return self.groups.get(group, (1.0, None))[0]

    def bench_range(self, group):
        """(min, max) tip speed in m/s of the group's thrust bench points, or None if it has none."""
        if not self.bench.get(group):
            return None
        pts = self.groups[group][1]
        return pts[0][0], pts[-1][0]

    def thrust(self, group, rpm, D):
        """Thrust factor for a prop of diameter D (m) turning at rpm."""
        pts = self.groups.get(group, (None, [(0, 1.0)]))[1]
        if self.mode == "flat":  # one factor per group at every tip speed
            return _mean(k for _, k in pts)
        u = tip_speed(rpm, D)
        if len(pts) == 1 or u <= pts[0][0]:
            return pts[0][1]
        if u >= pts[-1][0]:
            if self.mode == "hold":
                return pts[-1][1]
            (u0, k0), (u1, k1) = pts[-2], pts[-1]  # linear: extend the slope of the last two points
            return max(k1 + (u - u1) * (k1 - k0) / (u1 - u0), 0.5)
        return _interp(u, [a for a, _ in pts], [b for _, b in pts])

    def describe(self, props=()):
        for g in sorted({p.group for p in props if p.include} - set(self.groups)):
            print(f"  calibration group '{g}': none - prop data used as measured")
        for g, (kq, pts) in self.groups.items():
            rng = self.bench_range(g)
            if rng is None:
                print(f"  calibration group '{g}': torque x{kq:.3f}, no thrust bench points - thrust x1.000")
                continue
            print(f"  calibration group '{g}': torque x{kq:.3f}, thrust extrapolation '{self.mode}'"
                  + (f" (x{self.thrust(g, 0, 1):.3f} at every tip speed)" if self.mode == "flat" else ""))
            print(f"      thrust bench points (tip speed m/s, factor): {[(round(u), round(k, 3)) for u, k in pts]}, "
                  f"bench range {rng[0]:.0f}-{rng[1]:.0f} m/s tip speed")


# =============================================================================
# Operating-point solver
# =============================================================================
class System:
    """Battery -> wiring -> ESC -> motor -> prop.

    Resistances: pack + wiring carry the battery current; the ESC's FETs carry the motor current (with synchronous
    rectification the motor current flows through two FETs during the whole PWM period), so R_esc is in series
    with the winding. Current limits ('cap' mode): the motor and ESC ratings limit the motor (winding/phase)
    current, the pack rating and max_system_current_a limit the battery current. At part throttle
    I_motor = I_batt / throttle, so a battery-side limit alone would let the motor run far above its rating."""

    def __init__(self, motor, prop, esc, battery, cal, cfg):
        self.m, self.p, self.e, self.b = motor, prop, esc, battery
        mdl = cfg["model"]
        self.rho = mdl["air_density_kg_m3"]
        self.r_line = battery.r + mdl["wiring_and_plug_mohm"] / 1000  # battery side: pack + leads/plugs
        self.r_motor = motor.rm + esc.r                                # motor side: winding + ESC FETs
        self.kq = cal.torque(prop.group)
        self.cal = cal
        self.voc_fresh = battery.cells * mdl["voc_per_cell_fresh"]
        self.voc_cruise = battery.cells * mdl["voc_per_cell_cruise"]
        mode = mdl.get("current_limit_mode", "cap")
        lim_m = [(motor.imax, "motor rating"), (esc.cont, "ESC rating")]
        lim_b = [(battery.imax(mdl.get("battery_rating", "continuous")), "battery rating")]
        if mdl.get("max_system_current_a"):
            lim_b.append((float(mdl["max_system_current_a"]), "max system current"))  # e.g. your ESC current limit
        on = mode in ("cap", "reject")
        (self.limit_motor, src_m), (self.limit_batt, src_b) = min(lim_m), min(lim_b)
        if not on:
            self.limit_motor = self.limit_batt = None
        # lowest rating: at full throttle without a limit I_motor = I_batt, so this is what a combo may draw
        self.limit = min(self.limit_motor, self.limit_batt) if on else None
        self.limit_src = (src_m if self.limit_motor <= self.limit_batt else src_b) if on else ""
        self.mode = mode
        self.mass = motor.mass + prop.mass + esc.mass + battery.mass

    def _coeffs(self, rpm, v):
        n = rpm / 60.0
        J = v / (n * self.p.D) if v > 0 else 0.0
        ct, cp = self.p.coef(J, rpm)
        return n, J, ct * self.cal.thrust(self.p.group, rpm, self.p.D), cp * self.kq

    def _imotor(self, rpm, v):
        n, J, ct, cp = self._coeffs(rpm, v)
        return cp / (2 * math.pi) * self.rho * n * n * self.p.D ** 5 / self.m.kt + self.m.i0

    @staticmethod
    def _bisect(f, lo, hi, it=60):
        flo = f(lo)
        for _ in range(it):
            mid = 0.5 * (lo + hi); fm = f(mid)
            if (fm > 0) == (flo > 0):
                lo, flo = mid, fm
            else:
                hi = mid
            if hi - lo < 0.5:
                break
        return 0.5 * (lo + hi)

    def _at_battery_current(self, v, voc, Ib, rpm_hi):
        """Operating point with the battery current held at Ib: solve rpm, then throttle = Ib / I_motor."""
        m, R, Rm = self.m, self.r_line, self.r_motor

        def g(r):  # motor voltage throttle * V_batt = (Ib / Im) * (voc - Ib * R)
            Imr = self._imotor(r, v)
            return Ib * (voc - Ib * R) / Imr - Imr * Rm - r / m.kv

        rpm = self._bisect(g, 50.0, rpm_hi)
        return rpm, Ib / self._imotor(rpm, v)

    def _at_motor_current(self, v, voc, Im, rpm_hi):
        """Operating point with the motor current held at Im: the prop load sets rpm, the voltage balance
        throttle * (voc - throttle * Im * R) = Im * Rm + rpm / Kv sets the throttle (smaller root)."""
        R = self.r_line
        rpm = self._bisect(lambda r: Im - self._imotor(r, v), 50.0, rpm_hi)
        need = Im * self.r_motor + rpm / self.m.kv
        a = Im * R
        th = need / voc if a <= 0 else (voc - math.sqrt(max(voc * voc - 4 * a * need, 0.0))) / (2 * a)
        return rpm, th

    def point(self, v=0.0, throttle=1.0, voc=None, cap="auto"):
        """Solve one point. cap='auto' applies the current limits in 'cap' mode, None = no limit."""
        voc = self.voc_fresh if voc is None else voc
        on = cap == "auto" and self.mode == "cap"
        m, R = self.m, self.r_line

        def f(rpm):  # fixed throttle
            Im = self._imotor(rpm, v)
            return throttle * (voc - throttle * Im * R) - Im * self.r_motor - rpm / m.kv

        rpm = self._bisect(f, 50.0, voc * m.kv * throttle)
        Im = self._imotor(rpm, v); th = throttle
        limited = ""
        if on:  # current-limited: the ESC lowers the throttle until both currents are within their limits
            cands = []
            if Im > self.limit_motor:
                cands.append(self._at_motor_current(v, voc, self.limit_motor, rpm) + ("motor",))
            if throttle * Im > self.limit_batt:
                cands.append(self._at_battery_current(v, voc, self.limit_batt, rpm) + ("battery",))
            if cands:  # both currents grow with throttle, so the lower throttle satisfies both limits
                rpm, th, limited = min(cands, key=lambda c: c[1])
                Im = self._imotor(rpm, v)
        Ib = th * Im
        n, J, ct, cp = self._coeffs(rpm, v)
        T = ct * self.rho * n * n * self.p.D ** 4
        Vb = voc - Ib * R
        return dict(v=v, rpm=rpm, J=J, T=T, gf=T / G * 1000, I_batt=Ib, I_motor=Im, V_batt=Vb,
                    P_in=Vb * Ib, P_shaft=cp * self.rho * n ** 3 * self.p.D ** 5, throttle=th,
                    limited=bool(limited), limited_by=limited)

    def curve(self, speeds, voc=None, cap="auto"):
        return [self.point(v, 1.0, voc, cap) for v in speeds]


# =============================================================================
# Plane / mission model
# =============================================================================
class Plane:
    def __init__(self, path):
        d = dict(path) if isinstance(path, dict) else json.load(open(path, encoding="utf-8"))
        self.d = d; self.name = d.get("name", "plane" if isinstance(path, dict) else os.path.basename(path))
        self.S = d["wing_area_m2"]; self.AR = d["span_m"] ** 2 / self.S
        self.CLmax = d["CLmax"]; self.CLg = d.get("CL_ground_roll", 0.5); self.CD0 = d["CD0"]
        self.e = d.get("oswald_e", 0.8); self.mu = d.get("rolling_friction", 0.04)
        self.klof = d.get("liftoff_speed_factor", 1.1)
        self.x_lim = d["takeoff_distance_limit_m"] * d.get("takeoff_margin", 1.0)
        self.empty = d["empty_mass_kg"]; self.extra = d.get("extra_propulsion_mass_g", 0) / 1000
        art = d.get("drag_articles", {"count": 0, "mass_kg_each": 0, "CdA_m2_each": 0})
        self.n_art = art["count"]; self.m_art = art["mass_kg_each"]; self.cda_art = art["CdA_m2_each"]
        self.mis = d["mission"]; self.score_factor = d.get("score_factor", 1.25)

    def drag(self, m, v, rho, CL=None, n_art=None):
        n_art = self.n_art if n_art is None else n_art
        q = 0.5 * rho * v * v
        if CL is None:
            CL = m * G / (q * self.S) if q > 0 else 0
        return q * (self.S * (self.CD0 + CL * CL / (math.pi * self.AR * self.e)) + n_art * self.cda_art)

    def vstall(self, m, rho):
        return math.sqrt(2 * m * G / (rho * self.S * self.CLmax))


class Evaluator:
    """Precomputes curves once per combination, then finds the max takeoff mass."""

    def __init__(self, sysm, plane, cfg):
        self.s, self.pl = sysm, plane
        self.rho = sysm.rho
        self.vgrid = [float(v) for v in range(31)]
        full = sysm.curve(self.vgrid)
        self.T = [p["T"] for p in full]; self.Ib = [p["I_batt"] for p in full]
        vc = plane.mis["cruise_speed_mps"]
        self.thr_grid = _linspace(0.15, 1.0, 35)
        cr = [sysm.point(vc, th, sysm.voc_cruise) for th in self.thr_grid]
        self.Tcr = [p["T"] for p in cr]; self.Icr = [p["I_batt"] for p in cr]
        self.usable = cfg["model"]["usable_capacity_fraction"] * sysm.b.mah

    def _T(self, v):
        return _interp(v, self.vgrid, self.T)

    def _I(self, v):
        return _interp(v, self.vgrid, self.Ib)

    def mission(self, m):
        pl, rho = self.pl, self.rho
        vlof = pl.klof * pl.vstall(m, rho)
        v = x = t = q_mah = 0.0; dt = 0.01
        while v < vlof:
            q = 0.5 * rho * v * v
            a = (self._T(v) - pl.drag(m, v, rho, CL=pl.CLg) - pl.mu * max(m * G - q * pl.S * pl.CLg, 0)) / m
            if a <= 0 or t > 20:
                return None
            v += a * dt; x += v * dt; t += dt; q_mah += self._I(v) * dt / 3.6
        vc = pl.mis["climb_speed_factor"] * pl.vstall(m, rho)
        rc = (self._T(vc) - pl.drag(m, vc, rho)) * vc / (m * G)
        if rc < pl.mis["min_climb_rate_mps"]:
            return None
        t_climb = pl.mis["climb_altitude_m"] / rc
        q_mah += self._I(vc) * t_climb / 3.6
        if pl.mis["cruise_speed_mps"] < pl.mis.get("min_cruise_speed_factor", 1.2) * pl.vstall(m, rho):
            return None  # too heavy to cruise this slowly (would be near/below stall)
        D = pl.drag(m, pl.mis["cruise_speed_mps"], rho)
        if D > max(self.Tcr):
            return None
        Icr = _interp(D, self.Tcr, self.Icr)
        q_mah += Icr * pl.mis["cruise_time_s"] / 3.6 + pl.mis["approach_current_a"] * pl.mis["approach_time_s"] / 3.6
        return dict(roll_m=x, roll_s=t, climb_mps=rc, cruise_A=Icr, mah=q_mah)

    def feasible(self, m):
        r = self.mission(m)
        return r is not None and r["roll_m"] <= self.pl.x_lim and r["mah"] <= self.usable, r

    def best(self):
        pl = self.pl
        m0 = pl.empty + pl.extra + self.s.mass / 1000 + pl.n_art * pl.m_art
        ok, _ = self.feasible(m0)
        if not ok:
            return dict(m_tow=m0, payload=0.0, score=0.0, m_base=m0, info=None)
        lo, hi = m0, m0 + 0.5
        while self.feasible(hi)[0] and hi < 50:
            lo, hi = hi, hi * 1.5
        for _ in range(30):
            mid = 0.5 * (lo + hi)
            if self.feasible(mid)[0]:
                lo = mid
            else:
                hi = mid
        info = self.mission(lo); payload = lo - m0
        return dict(m_tow=lo, payload=payload, m_base=m0, info=info,
                    score=pl.score_factor * payload / LB * pl.n_art)


# =============================================================================
# Optimisation run
# =============================================================================
def _rejected(s, crv):
    """'reject' mode: the (unlimited) curve draws more than the lowest rating - same test as in evaluate_all."""
    return s.mode == "reject" and max(pt["I_batt"] for pt in crv) > s.limit


def prescreen_props(props, lib, cal, cfg, bats, n_keep, avg_to, verbose=True):
    """Quick pass: each prop with each motor on the strongest ESC and lowest-resistance pack.
    Keeps the n_keep props with the best mean thrust (relative to the best prop for that motor)."""
    esc = max(lib["escs"], key=lambda e: e.cont); bat = min(bats, key=lambda b: b.r)
    vs = [v for v in cfg["model"]["speeds_mps"] if v <= avg_to] or [0]
    score = {}
    for im, m in enumerate(lib["motors"], 1):
        _progress("prescreen props" if verbose else "prescreen", im, len(lib["motors"]))
        vals = {}
        for p in props:
            s = System(m, p, esc, bat, cal, cfg)
            crv = s.curve(vs)
            if max(pt["rpm"] for pt in crv) > p.max_rpm or _rejected(s, crv):
                continue
            vals[p.name] = _mean(pt["gf"] for pt in crv) - p.mass
        top = max(vals.values(), default=0)
        if top > 0:  # relative to the best prop on this motor (a ratio is meaningless if nothing pulls its weight)
            for k, v in vals.items():
                score[k] = max(score.get(k, -1e9), v / top)
    keep = sorted(score, key=score.get, reverse=True)[:n_keep]
    if verbose:
        print(f"  prescreen: {len(props)} props -> best {len(keep)} kept for the full search "
              f"(use --prescreen 0 to search all)")
    return sorted([p for p in props if p.name in keep], key=lambda p: keep.index(p.name))


def prescreen_motors(motors, props, lib, cal, cfg, bats, n_keep, avg_to, verbose=True):
    """Each motor with the 5 best props (strongest ESC, lowest-resistance pack); keep the n_keep motors with the
    highest mean thrust minus motor + prop mass. Current is capped at the motor/ESC/battery rating as usual."""
    esc = max(lib["escs"], key=lambda e: e.cont); bat = min(bats, key=lambda b: b.r)
    vs = [v for v in cfg["model"]["speeds_mps"] if v <= avg_to] or [0]
    score = {}
    for im, m in enumerate(motors, 1):
        _progress("prescreen motors", im, len(motors))
        best = -1e9
        for p in props[:5]:
            s = System(m, p, esc, bat, cal, cfg)
            crv = s.curve(vs)
            if max(pt["rpm"] for pt in crv) > p.max_rpm or _rejected(s, crv):
                continue
            best = max(best, _mean(pt["gf"] for pt in crv) - m.mass - p.mass)
        score[m.name] = best
    keep = sorted(score, key=score.get, reverse=True)[:n_keep]
    if verbose:
        n_est = sum(1 for m in motors if m.name in keep and m.estimated)
        print(f"  prescreen: {len(motors)} motors -> best {len(keep)} kept ({n_est} with estimated Rm/I0)")
    return [m for m in motors if m.name in keep]


def two_blade_only(cfg):
    """True if config model.multi_blade_props is 'exclude' (3/4-blade props are left out of the search)."""
    mode = str(cfg["model"].get("multi_blade_props", "include")).strip().lower()
    if mode not in ("include", "exclude"):
        raise ValueError(f"multi_blade_props must be 'include' or 'exclude' (got '{mode}')")
    return mode == "exclude"


def evaluate_all(lib, plane, metric, avg_to, verbose=True, prescreen=None):
    cfg = lib["cfg"]; rules = cfg["rules"]; mdl = cfg["model"]
    cal = Calibration(lib, mdl["air_density_kg_m3"])
    if verbose:
        cal.describe(lib["props"])
    speeds = mdl["speeds_mps"]
    props = [p for p in lib["props"] if p.include and p.D_in <= rules["max_prop_diameter_in"] + 1e-9]
    bats = [b for b in lib["batteries"] if b.cells == rules["battery_cells"] and b.mah <= rules["max_battery_mah"]]
    skipped = [b.name for b in lib["batteries"] if b not in bats] + \
              [p.name for p in lib["props"] if p.include and p not in props]
    if skipped and verbose:
        print("  excluded by rules:", ", ".join(skipped))
    if two_blade_only(cfg):  # calibration comes from 2-blade bench tests - leave 3/4-blade props out
        multi = [p.name for p in props if p.blades > 2]
        props = [p for p in props if p.blades <= 2]
        if multi and verbose:
            print("  2-blade only (multi_blade_props = exclude): left out", ", ".join(multi))
    motors = [m for m in lib["motors"] if m.include]
    n_keep = mdl.get("prescreen_props", 40) if prescreen is None else prescreen
    if n_keep and len(props) > n_keep:
        ref_motors = [m for m in motors if not m.estimated] or motors
        props = prescreen_props(props, dict(lib, motors=ref_motors), cal, cfg, bats, n_keep, avg_to, verbose)
    n_mot = mdl.get("prescreen_motors", 15) if prescreen is None else prescreen
    if n_mot and len(motors) > n_mot:
        ref_motors = [m for m in motors if not m.estimated] or motors
        ref_props = prescreen_props(props, dict(lib, motors=ref_motors), cal, cfg, bats, 5, avg_to, verbose=False)
        motors = prescreen_motors(motors, ref_props, lib, cal, cfg, bats, n_mot, avg_to, verbose)
    plane_top = mdl.get("plane_evaluate_top", 400)
    combos = list(itertools.product(motors, props, lib["escs"], bats))
    if verbose:
        print(f"  evaluating {len(combos)} combinations"
              f"{' with plane: ' + plane.name if plane else ' (propulsion only)'} ...")
    results = []
    for i, (m, p, e, b) in enumerate(combos, 1):
        _progress("combinations", i, len(combos))
        s = System(m, p, e, b, cal, cfg)
        free = s.curve(speeds, cap=None)  # unlimited draw
        crv = s.curve(speeds)              # with current limit (cap mode)
        peak_free = max(pt["I_batt"] for pt in free)  # full throttle, no limit: I_batt = I_motor
        notes = []
        if mdl.get("current_limit_mode") == "reject" and peak_free > s.limit:
            continue
        if max(pt["rpm"] for pt in crv) > p.max_rpm:
            continue  # prop over its RPM limit
        if s.limit is not None and peak_free > s.limit + 0.5:
            notes.append(f"needs current limit {s.limit:.0f} A ({s.limit_src}; would draw {peak_free:.0f} A)")
        rng = cal.bench_range(p.group); u_pk = tip_speed(max(pt["rpm"] for pt in crv), p.D)
        if rng and u_pk > rng[1] + 0.5:
            notes.append(f"thrust calibration beyond bench data (tip speed {u_pk:.0f} m/s > {rng[1]:.0f} m/s bench max)")
        if m.estimated:
            notes.append("motor Rm/I0 estimated (+/-15 %) - check datasheet")
        im_pk = max(pt["I_motor"] for pt in crv)
        if im_pk > m.imax + 0.5:
            notes.append(f"motor current up to {im_pk:.0f} A (rated {m.imax:g} A, {m.rating})")
        vs = [float(v) for v in speeds]
        gf = [pt["gf"] for pt in crv]
        sel = [i for i, v in enumerate(vs) if v <= avg_to]
        if len(sel) > 1:
            xs = [vs[i] for i in sel]
            avg = _trapz([gf[i] for i in sel], xs) / (xs[-1] - xs[0])
        else:
            avg = gf[0]
        row = dict(motor=m.name, prop=p.name, esc=e.name, battery=b.name,
                   static_gf=crv[0]["gf"], avg_gf=avg, thrust_10ms=_interp(10, vs, gf),
                   peak_A=max(pt["I_batt"] for pt in crv), peak_motor_A=im_pk, uncapped_peak_A=peak_free,
                   limit_A=s.limit, motor_limit_A=s.limit_motor, batt_limit_A=s.limit_batt,
                   static_W=crv[0]["P_in"], gpw=crv[0]["gf"] / crv[0]["P_in"],
                   rpm_static=crv[0]["rpm"], prop_mass_g=s.mass,
                   full_throttle_min=mdl["usable_capacity_fraction"] * b.mah / 1000 / max(pt["I_batt"] for pt in crv) * 60,
                   net_gf=avg - s.mass, notes="; ".join(notes), _sys=s, _curve=crv)
        results.append(row)
        if verbose and len(combos) > 200 and i % max(1, len(combos) // 4) == 0:
            print(f"    {i}/{len(combos)}")
    if plane:
        results.sort(key=lambda r: r["net_gf"], reverse=True)
        if plane_top and len(results) > plane_top:
            if verbose:
                print(f"  mission simulation for the {plane_top} combinations with the best net thrust "
                      f"(of {len(results)}; set model.plane_evaluate_top in config.json, 0 = all)")
            results = results[:plane_top]
        for i, row in enumerate(results, 1):
            _progress("mission simulation", i, len(results))
            ev = Evaluator(row["_sys"], plane, cfg); best = ev.best()
            row.update(tow_kg=best["m_tow"], payload_kg=best["payload"], score=best["score"],
                       mission_mah=best["info"]["mah"] if best["info"] else float("nan"),
                       roll_m=best["info"]["roll_m"] if best["info"] else float("nan"), _eval=ev)
    key = {"avg_thrust": "avg_gf", "static_thrust": "static_gf", "net_thrust": "net_gf",
           "thrust_per_watt": "gpw", "score": "score"}[metric]
    results.sort(key=lambda r: r[key], reverse=True)
    return results, key


def write_csv(results, path, plane):
    cols = ["rank", "motor", "prop", "esc", "battery", "static_gf", "thrust_10ms", "avg_gf", "net_gf", "peak_A",
            "peak_motor_A", "uncapped_peak_A", "limit_A", "motor_limit_A", "batt_limit_A", "static_W", "gpw",
            "rpm_static", "prop_mass_g", "full_throttle_min"]
    if plane:
        cols += ["tow_kg", "payload_kg", "score", "mission_mah", "roll_m"]
    cols += ["notes"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(cols)
        for i, r in enumerate(results, 1):
            out = []
            for c in cols:
                v = i if c == "rank" else r.get(c, "")
                out.append(f"{v:.3f}" if isinstance(v, float) else ("" if v is None else v))
            w.writerow(out)


def print_top(results, key, n, plane):
    hdr = f"{'#':>2}  {'motor':18} {'prop':36} {'esc':22} {'battery':24} {'static':>6} {'@10m/s':>6} {'peak A':>6} {'mass':>5}"
    if plane:
        hdr += f" {'payload':>7} {'score':>6}"
    else:
        hdr += f" {'net':>6}"
    print("\n" + hdr)
    for i, r in enumerate(results[:n], 1):
        line = (f"{i:>2}  {r['motor'][:18]:18} {r['prop'][:36]:36} {r['esc'][:22]:22} {r['battery'][:24]:24} "
                f"{r['static_gf']:6.0f} {r['thrust_10ms']:6.0f} {r['peak_A']:6.1f} {r['prop_mass_g']:5.0f}")
        if plane:
            line += f" {r['payload_kg']:6.2f}k {r['score']:6.1f}"
        else:
            line += f" {r['net_gf']:6.0f}"
        print(line)
        if r["notes"]:
            print(f"      ! {r['notes']}")


# =============================================================================
# Graphs
# =============================================================================
def make_plots(results, key, out, plane, n_top, metric_label):
    try:
        import numpy as np
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("  numpy/matplotlib not installed - skipping graphs (pip install numpy matplotlib)")
        return []
    files = []
    accent, grey = "#2563eb", "#9ca3af"
    top = results[:n_top]
    label = lambda r: f"{r['motor']} | {r['prop']} | {r['battery']} | {r['esc']}"

    # 1. ranking dot plot (values are often within a few %, so dots + % gap read better than bars)
    fig, ax = plt.subplots(figsize=(11, 0.42 * len(top) + 1.8))
    vals = [r[key] for r in top]; best = vals[0]
    ys = list(range(len(top)))[::-1]
    for y, v, i in zip(ys, vals, range(len(top))):
        ax.plot([best, v], [y, y], color="#e5e7eb", lw=6, solid_capstyle="round", zorder=1)
        ax.scatter([v], [y], s=60 if i == 0 else 36, color=accent if i == 0 else grey, zorder=2)
        txt = (f"{v:.2f}" if key in ("gpw", "score") else f"{v:.0f}") + ("" if i == 0 or not best else f"  ({(v - best) / abs(best) * 100:+.1f} %)")
        ax.annotate(txt, (v, y), xytext=(8, -3), textcoords="offset points", fontsize=8)
    ax.set_yticks(ys, [f"#{i + 1}  {label(r)}" for i, r in enumerate(top)], fontsize=8)
    lo, hi = min(vals), max(vals); pad = max((hi - lo) * 0.25, abs(hi) * 0.01)
    ax.set_xlim(lo - pad, hi + pad * 2.5)
    ax.set_xlabel(metric_label + "  (axis does not start at zero)")
    ax.set_title(f"Top {len(top)} combinations by {metric_label}", loc="left", fontsize=12, fontweight="bold")
    ax.grid(axis="x", alpha=0.3); ax.spines[["top", "right", "left"]].set_visible(False)
    fig.tight_layout(); p = os.path.join(out, "ranking.png"); fig.savefig(p, dpi=150); plt.close(fig); files.append(p)

    # 2. thrust + current vs airspeed for the best few
    best_n = top[:min(5, len(top))]
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(10, 8), sharex=True, gridspec_kw=dict(height_ratios=[3, 2]))
    for i, r in enumerate(best_n):
        c = r["_curve"]; vs = [pt["v"] for pt in c]
        style = dict(color=accent, lw=2.8) if i == 0 else dict(color=grey, lw=1.5, alpha=1 - 0.15 * i)
        a1.plot(vs, [pt["gf"] for pt in c], marker="o", ms=3, label=f"#{i + 1} {label(r)}", **style)
        a2.plot(vs, [pt["I_batt"] for pt in c], marker="o", ms=3, **style)
    if best_n[0]["batt_limit_A"]:
        a2.axhline(best_n[0]["batt_limit_A"], color="k", ls=":", lw=1)
        a2.text(0.2, best_n[0]["batt_limit_A"] * 0.88, f"battery current limit of #1: {best_n[0]['batt_limit_A']:.0f} A "
                f"(motor current limit {best_n[0]['motor_limit_A']:.0f} A)", fontsize=8)
    a2.set_ylim(0, max(max(pt["I_batt"] for pt in r["_curve"]) for r in best_n) * 1.2)
    a1.set_ylim(0, max(max(pt["gf"] for pt in r["_curve"]) for r in best_n) * 1.1)
    a1.set_ylabel("Thrust available (gf)"); a2.set_ylabel("Battery current (A)"); a2.set_xlabel("Airspeed (m/s)")
    a1.set_title("Full-throttle thrust and current vs airspeed (fresh pack)", loc="left", fontsize=12, fontweight="bold")
    a1.legend(fontsize=7, frameon=False)
    for a in (a1, a2):
        a.grid(alpha=0.3); a.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(); p = os.path.join(out, "thrust_curves.png"); fig.savefig(p, dpi=150); plt.close(fig); files.append(p)

    # 3. trade-off scatter over all combinations
    fig, ax = plt.subplots(figsize=(10, 6))
    if plane:
        xs = [r["peak_A"] for r in results]; ys = [r["payload_kg"] for r in results]
        ax.set_ylabel("Max payload (kg)"); ttl = "Payload vs peak battery current - every combination"
    else:
        xs = [r["prop_mass_g"] for r in results]; ys = [r["avg_gf"] for r in results]
        ax.set_xlabel("Propulsion mass: motor + prop + ESC + battery (g)")
        ax.set_ylabel(f"Mean thrust (gf)"); ttl = "Mean thrust vs propulsion mass - every combination"
    ax.scatter(xs, ys, s=14, color=grey)
    if not plane and key == "net_gf":  # line of equal net thrust through #1
        xl = np.array([min(xs), max(xs)])
        ax.plot(xl, xl + results[0]["net_gf"], ls="--", color=accent, lw=1)
        ax.text(xl[0], xl[0] + results[0]["net_gf"] - 12, "  line of equal net thrust to #1 (better = above)", color=accent, fontsize=8, va="top")
    ax.scatter([xs[0]], [ys[0]], s=70, color=accent, zorder=3)
    ax.annotate(f"#1 {label(results[0])}", (xs[0], ys[0]), xytext=(-10, 10), textcoords="offset points",
                fontsize=8, color=accent, ha="right")
    if plane:
        ax.set_xlabel("Peak battery current (A)")
    ax.set_title(ttl, loc="left", fontsize=12, fontweight="bold")
    ax.grid(alpha=0.3); ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout(); name = "payload_vs_current.png" if plane else "thrust_vs_mass.png"
    p = os.path.join(out, name); fig.savefig(p, dpi=150); plt.close(fig); files.append(p)

    # 4. TDS graph: thrust available vs drag (lbf vs ft/s) for the best combination
    if plane:
        r = results[0]; ev = r["_eval"]; pl = plane; m = r["tow_kg"]
        v = np.linspace(max(pl.vstall(m, ev.rho) * 0.9, 1), 25, 80)
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.plot(v / 0.3048, [ev._T(x) / LBF for x in v], color=accent, lw=2.8, label="Thrust available (full throttle)")
        for k, sh in zip(range(1, 4), (0.45, 0.7, 1.0)):
            ax.plot(v / 0.3048, [pl.drag(m, x, ev.rho, n_art=k) / LBF for x in v], color="k", alpha=sh, lw=1.4,
                    label=f"Drag, {k} drag article{'s' if k > 1 else ''}")
        vc = pl.mis["cruise_speed_mps"]
        ax.plot([vc / 0.3048], [pl.drag(m, vc, ev.rho) / LBF], "o", color="k")
        ax.annotate("cruise design point", (vc / 0.3048, pl.drag(m, vc, ev.rho) / LBF), xytext=(8, -14),
                    textcoords="offset points", fontsize=8)
        ax.set_xlabel("Flight speed (ft/s)"); ax.set_ylabel("Force (lbf)")
        ax.set_title(f"Thrust available vs drag (TDS power available / required) at {m:.2f} kg - #1 combination", loc="left", fontsize=12,
                     fontweight="bold")
        ax.legend(fontsize=8, frameon=False); ax.grid(alpha=0.3); ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout(); p = os.path.join(out, "tds_power_curves.png"); fig.savefig(p, dpi=150); plt.close(fig)
        files.append(p)
    return files


# =============================================================================
# Wind-tunnel import (Tyto Robotics / RCbenchmark CSV logs)
# =============================================================================
def _load_rcbench(path):
    """Read one RCbenchmark log (comma or tab separated). Returns rows with sig, Q, T, V, I, rpm."""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            txt = raw.decode(enc); break
        except UnicodeDecodeError:
            continue
    lines = txt.splitlines()
    delim = "\t" if lines[0].count("\t") > lines[0].count(",") else ","
    rows = list(csv.reader(lines, delimiter=delim))
    hdr = [h.strip() for h in rows[0]]

    def col(*prefixes):
        for p in prefixes:
            for i, h in enumerate(hdr):
                if h.lower().startswith(p.lower()):
                    return i
        return None

    idx = dict(sig=col("ESC signal"), Q=col("Torque"), T=col("Thrust"), V=col("Voltage"), I=col("Current"),
               rpm_o=col("Motor Optical Speed"), rpm_e=col("Motor Electrical Speed"))
    if idx["Q"] is None or idx["T"] is None:
        raise ValueError("no Torque/Thrust columns")
    t_unit = hdr[idx["T"]].lower()
    t_scale = G if "kgf" in t_unit else (G / 1000 if "gf" in t_unit else (LBF if "lbf" in t_unit else 1.0))
    out = []
    for r in rows[1:]:
        try:
            get = lambda k: float(r[idx[k]]) if idx[k] is not None and r[idx[k]].strip() else float("nan")
            rpm = get("rpm_o")
            if not rpm or math.isnan(rpm):
                rpm = get("rpm_e")
            out.append(dict(sig=get("sig"), Q=get("Q"), T_N=get("T") * t_scale, V=get("V"), I=get("I"), rpm=rpm))
        except (ValueError, IndexError):
            continue
    return out


def _speed_from_name(name):
    if re.search(r"static", name, re.I):
        return 0.0
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m[-_ /]?s", name, re.I)
    return float(m.group(1).replace(",", ".")) if m else None


def _robust_polyfit(x, y, deg, it=4):
    keep = [True] * len(x)
    for _ in range(it):
        c = _polyfit([a for a, k in zip(x, keep) if k], [b for b, k in zip(y, keep) if k], deg)
        r = [b - _polyval(c, a) for a, b in zip(x, y)]
        s = 1.4826 * statistics.median(abs(e) for e, k in zip(r, keep) if k) + 1e-6
        keep = [abs(e) < 3 * s for e in r]
    return c, keep


def import_tunnel(folder, data_dir, rho, min_rpm, mass_g, out_dir, cfg, plots=True):
    """Turn wind-tunnel logs into prop tables (J, Ct, Cp), add them to props.csv, plot vs library props.

    Layout: <folder>/<prop>/<files>.csv, one sub-folder per propeller. The sub-folder name gives the size:
    '1208' -> 12 x 8 in, or '12x8'. Optional <folder>/tunnel.json maps a sub-folder to
    {"name": ..., "diameter_in": ..., "pitch_in": ..., "mass_g": ...}. Airspeed comes from each file
    name ('10m-s', '6 m/s', 'static'). Thrust must be tared with the wind on and motor stopped.
    """
    import hashlib
    mapping = {}
    mp = os.path.join(folder, "tunnel.json")
    if os.path.exists(mp):
        mapping = json.load(open(mp, encoding="utf-8"))
    props_dir = os.path.join(data_dir, "props"); os.makedirs(props_dir, exist_ok=True)
    os.makedirs(out_dir, exist_ok=True)
    made = []
    for sub in sorted(d for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d))):
        info = dict(mapping.get(sub, {}))
        m = re.fullmatch(r"(\d{1,2})(\d{2})", sub) or re.fullmatch(r"(\d+(?:\.\d+)?)\s*[xX]\s*(\d+(?:\.\d+)?)", sub)
        if "diameter_in" not in info:
            if not m:
                print(f"  skipping '{sub}': cannot read size from folder name (add it to tunnel.json)")
                continue
            info["diameter_in"] = float(m.group(1)); info["pitch_in"] = float(m.group(2))
        D_in, P_in = float(info["diameter_in"]), float(info.get("pitch_in", 0))
        D = D_in * IN
        name = info.get("name", f"Tunnel {D_in:g}x{P_in:g} ({sub})")
        pts, seen, n_files = [], set(), 0
        for f in sorted(glob.glob(os.path.join(folder, sub, "*.csv"))):
            h = hashlib.md5(open(f, "rb").read()).hexdigest()
            if h in seen:
                continue  # identical duplicate file
            seen.add(h)
            v = _speed_from_name(os.path.basename(f))
            if v is None:
                print(f"    {os.path.basename(f)}: no airspeed in file name - skipped"); continue
            try:
                rows = _load_rcbench(f)
            except Exception as e:  # noqa
                print(f"    {os.path.basename(f)}: {e} - skipped"); continue
            n_files += 1
            for r in rows:
                if not r["rpm"] or math.isnan(r["rpm"]) or r["rpm"] < min_rpm or math.isnan(r["Q"]):
                    continue
                n = r["rpm"] / 60
                pts.append((v / (n * D), r["T_N"] / (rho * n * n * D ** 4),
                            r["Q"] * 2 * math.pi * n / (rho * n ** 3 * D ** 5), r["rpm"], v, r["I"], r["V"]))
        if len(pts) < 10:
            print(f"  {sub}: only {len(pts)} usable points (rpm >= {min_rpm}) - skipped"); continue
        P = pts; J, ct, cp = [p[0] for p in P], [p[1] for p in P], [p[2] for p in P]
        col = lambda k: [p[k] for p in P]
        span = max(J) - min(J)
        deg = 3 if span > 0.45 else 2
        cct, kt = _robust_polyfit(J, ct, deg); ccp, kp = _robust_polyfit(J, cp, deg)
        jmax = max(j for j, k in zip(J, kt) if k)
        jgrid = [i * 0.025 for i in range(math.ceil((jmax + 1e-9) / 0.025))]
        ctg = [_polyval(cct, j) for j in jgrid]; cpg = [max(_polyval(ccp, j), 1e-4) for j in jgrid]
        if any(c <= 0 for c in ctg):  # stop at zero thrust
            k = next(i for i, c in enumerate(ctg) if c <= 0); jgrid, ctg, cpg = jgrid[:k + 1], ctg[:k + 1], cpg[:k + 1]
        fname = f"tunnel_{re.sub(r'[^A-Za-z0-9]+', '_', sub)}.csv"
        with open(os.path.join(props_dir, fname), "w", encoding="utf-8") as fo:
            fo.write(f"# {name}: measured in wind tunnel, {n_files} runs, {len(P)} points >= {min_rpm} RPM, "
                     f"air density {rho} kg/m3, RPM {min(col(3)):.0f}-{max(col(3)):.0f}, "
                     f"airspeed {min(col(4)):g}-{max(col(4)):g} m/s. Fitted polynomial deg {deg}.\n")
            fo.write("J,Ct,Cp\n")
            for j, a, b in zip(jgrid, ctg, cpg):
                fo.write(f"{j:.4f},{a:.5f},{b:.5f}\n")
        fm = 0.7979 * max(ctg[0], 0) ** 1.5 / cpg[0]
        eta = [j * a / b for j, a, b in zip(jgrid[1:], ctg[1:], cpg[1:])]
        i_eta = max(range(len(eta)), key=lambda i: eta[i]) if eta else 0
        rec = dict(name=name, sub=sub, D_in=D_in, P_in=P_in, file=fname, n=len(P), files=n_files, fm=fm,
                   eta_max=max(eta) if eta else 0, j_eta=jgrid[1:][i_eta] if eta else 0,
                   j0=float(jgrid[-1]), ct0=float(ctg[0]), cp0=float(cpg[0]), cct=cct, ccp=ccp, P=P, kt=kt, kp=kp,
                   rpm_rng=(min(col(3)), max(col(3))), mass=float(info.get("mass_g", mass_g)))
        made.append(rec)

    # --- add / update rows in props.csv
    pcsv = os.path.join(data_dir, "props.csv")
    rows = _read_csv(pcsv) if os.path.exists(pcsv) else []
    cols = list(rows[0].keys()) if rows else ["name", "diameter_in", "pitch_in", "mass_g", "format", "file",
                                               "cal_group", "max_rpm", "include", "notes"]
    for c in ("include", "notes"):
        if c not in cols:
            cols.append(c)
    max_d = cfg["rules"]["max_prop_diameter_in"]
    for rec in made:
        new = dict(name=rec["name"], diameter_in=f"{rec['D_in']:g}", pitch_in=f"{rec['P_in']:g}", mass_g=f"{rec['mass']:g}",
                   format="csv", file=f"props/{rec['file']}", cal_group="tunnel", max_rpm="15000",
                   include="yes" if rec["D_in"] <= max_d else "no",
                   notes=f"own wind tunnel, uncalibrated; measured {rec['rpm_rng'][0]:.0f}-{rec['rpm_rng'][1]:.0f} RPM; EDIT mass")
        for i, r in enumerate(rows):
            if r.get("name") == rec["name"]:
                new["mass_g"] = r.get("mass_g") or new["mass_g"]; new["include"] = r.get("include") or new["include"]
                rows[i] = new; break
        else:
            rows.append(new)
    with open(pcsv, "w", newline="", encoding="utf-8") as fo:
        w = csv.DictWriter(fo, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})

    # --- summary + comparison with library props of the same size
    lib = load_library(data_dir); cal = Calibration(lib, cfg["model"]["air_density_kg_m3"])
    print(f"\nImported {len(made)} propellers from {folder}  (air density {rho} kg/m3, points >= {min_rpm} RPM)")
    print(f"{'prop':26} {'pts':>4} {'static Ct':>9} {'static Cp':>9} {'static FM':>9} {'peak eff':>9} {'zero-thrust J':>13}  compared with")
    files = []
    for rec in made:
        twins = [p for p in lib["props"] if p.group != "tunnel" and abs(p.D_in - rec["D_in"]) < 0.01
                 and abs(p.pitch - rec["P_in"]) <= 0.6]
        cmp_txt = ""
        if twins:
            p = twins[0]; kq = cal.torque(p.group); ktr = cal.thrust(p.group, 9000, p.D)
            r = []
            for j in (0.0, 0.2, 0.4):
                a, b = p.coef(j, 9000)
                r.append(f"J{j:.1f}: thrust {_polyval(rec['cct'], j) / (a * ktr) * 100 - 100:+.0f}%")
            cmp_txt = f"{p.name}: " + ", ".join(r)
        print(f"{rec['name'][:26]:26} {rec['n']:4d} {rec['ct0']:9.4f} {rec['cp0']:9.4f} {rec['fm']:9.3f} "
              f"{rec['eta_max']:9.2f} {rec['j0']:13.2f}  {cmp_txt}")
        if plots:
            try:
                import numpy as np
                import matplotlib
                matplotlib.use("Agg")
                import matplotlib.pyplot as plt
            except ImportError:
                plots = False; print("  numpy/matplotlib not installed - no tunnel plots"); continue
            fig, axs = plt.subplots(1, 3, figsize=(15, 4.6))
            P = np.array(rec["P"]); g = np.linspace(0, rec["j0"], 80)
            sc = axs[0].scatter(P[:, 0], P[:, 1], c=P[:, 4], s=8, cmap="viridis", alpha=0.6)
            axs[0].plot(g, np.polyval(rec["cct"], g), color="#2563eb", lw=2.5, label="fit (used by calculator)")
            axs[1].scatter(P[:, 0], P[:, 2], c=P[:, 4], s=8, cmap="viridis", alpha=0.6)
            axs[1].plot(g, np.clip(np.polyval(rec["ccp"], g), 1e-4, None), color="#2563eb", lw=2.5)
            gg = g[1:]; axs[2].plot(gg, gg * np.polyval(rec["cct"], gg) / np.clip(np.polyval(rec["ccp"], gg), 1e-4, None),
                                    color="#2563eb", lw=2.5, label=rec["name"])
            for p, ls in zip(twins[:2], ("--", ":")):
                kq = cal.torque(p.group); jm = max(p.tables[0][1]); js = np.linspace(0, jm, 60)
                cc = [p.coef(j, 9000) for j in js]
                axs[0].plot(js, [a * cal.thrust(p.group, 9000, p.D) for a, b in cc], ls, color="#ef4444", lw=1.6,
                            label=f"{p.name} (library, calibrated)")
                axs[1].plot(js, [b * kq for a, b in cc], ls, color="#ef4444", lw=1.6)
                axs[2].plot(js[1:], [j * a * cal.thrust(p.group, 9000, p.D) / (b * kq) for j, (a, b) in zip(js[1:], cc[1:])],
                            ls, color="#ef4444", lw=1.6, label=p.name)
            for ax, t in zip(axs, ("Thrust coefficient Ct", "Power coefficient Cp", "Propeller efficiency  J*Ct/Cp")):
                ax.set_xlabel("Advance ratio J = V / (n D)"); ax.set_title(t, loc="left", fontweight="bold")
                ax.grid(alpha=0.3); ax.spines[["top", "right"]].set_visible(False)
            axs[2].set_ylim(0, 1); axs[0].set_ylim(bottom=min(0, P[:, 1].min())); axs[0].legend(fontsize=8, frameon=False)
            axs[2].legend(fontsize=8, frameon=False)
            cb = fig.colorbar(sc, ax=axs[1]); cb.set_label("tunnel airspeed (m/s)")
            fig.suptitle(f"{rec['name']} - wind tunnel ({rec['n']} points, {rec['files']} runs, "
                         f"{rec['rpm_rng'][0]:.0f}-{rec['rpm_rng'][1]:.0f} RPM)", x=0.01, ha="left", fontsize=12)
            fig.tight_layout(); pth = os.path.join(out_dir, f"tunnel_{rec['sub']}.png"); fig.savefig(pth, dpi=140)
            plt.close(fig); files.append(pth)
    print("\nAdded/updated in props.csv with cal_group 'tunnel' (used as measured, no calibration). "
          "Props over the rule diameter are added with include=no.")
    for f in files:
        print("  plot:", f)
    return made


# =============================================================================
# UIUC Propeller Database import (volumes 1-4, https://m-selig.ae.illinois.edu/props/propDB.html)
# =============================================================================
UIUC_MM_SERIES = {"ef", "kpf", "pl", "vp"}
UIUC_SERIES = {  # file prefix -> (display name, RPM-limit constant / D_in, purchasable)
    "ance": ("Aeronaut CAM Carbon Electric", 150000, True), "ancf": ("Aeronaut CAM Carbon Folding", 150000, True),
    "apce": ("APC Thin Electric", 150000, True), "apcsp": ("APC Sport", 190000, True),
    "apcsf": ("APC Slow Flyer", 65000, True), "apccf": ("APC Carbon Fiber", 190000, True),
    "apcff": ("APC Free Flight", 65000, True), "apc29ff": ("APC 29 Free Flight", 65000, True),
    "grcp": ("Graupner CAM Prop", 150000, True), "grcsp": ("Graupner CAM Slim", 150000, True),
    "grsn": ("Graupner Super Nylon", 150000, True), "gwsdd": ("GWS Direct Drive", 65000, True),
    "gwssf": ("GWS Slow Flyer", 65000, True), "kavfk": ("Kavon FK Folding", 150000, True),
    "kyosho": ("Kyosho", 150000, True), "ma": ("Master Airscrew", 150000, True),
    "mae": ("Master Airscrew Electric", 150000, True), "magf": ("Master Airscrew G/F", 150000, True),
    "mas": ("Master Airscrew Scimitar", 150000, True), "rusp": ("Rev Up Sport", 150000, True),
    "zin": ("Zingali", 150000, True), "ef": ("E-Flite", 150000, True), "kpf": ("KP Folding", 150000, True),
    "mi": ("Micro Invent 2-blade", 150000, True), "mit": ("Micro Invent 3-blade", 150000, True),
    "vp": ("Vapor", 150000, True), "pl": ("Plantraco", 150000, True),
    "da4002": ("UIUC DA4002 (research)", 150000, False), "da4022": ("UIUC DA4022 (research)", 150000, False),
    "da4052": ("UIUC DA4052 (research)", 150000, False),
}


def import_uiuc(db_folder, data_dir, cfg, ref_name="APC Thin Electric 12x6 (UIUC)"):
    """Convert every UIUC prop that passes the diameter rule into a J,Ct,Cp table and add it to props.csv.

    Static data: mean of the rows within 10 % of the highest RPM tested (closest to flight Reynolds number).
    Wind-tunnel data: the run(s) within 5 % of the highest RPM tested. If a prop appears in several volumes
    the newest volume is used. All rows get cal_group 'uiuc' (same SunnySky bench calibration as before).
    """
    max_d = cfg["rules"]["max_prop_diameter_in"]
    out_dir = os.path.join(data_dir, "props", "uiuc"); os.makedirs(out_dir, exist_ok=True)
    pat = re.compile(r"^([a-z0-9]+?)_([0-9.]+)x([0-9.]+)[a-z]*_(.+)\.txt$", re.I)
    found = {}  # (prefix, D, P) -> {vol: {"static": [files], "wt": [(rpm, file)]}}
    skipped_names = set()
    for vol_dir in sorted(glob.glob(os.path.join(db_folder, "volume-*"))):
        vol = int(re.sub(r"\D", "", os.path.basename(vol_dir)) or 0)
        for f in sorted(glob.glob(os.path.join(vol_dir, "data", "*.txt"))):
            m = pat.match(os.path.basename(f))
            if not m:
                skipped_names.add(os.path.basename(f).split("_")[0]); continue
            pre, D, P, rest = m.group(1).lower(), float(m.group(2)), float(m.group(3)), m.group(4)
            if rest.startswith("geom"):
                continue
            slot = found.setdefault((pre, D, P), {}).setdefault(vol, {"static": [], "wt": []})
            if rest.startswith("static"):
                slot["static"].append(f)
            else:
                r = re.search(r"_(\d+)$", rest)
                if r:
                    slot["wt"].append((float(r.group(1)), f))
    rows, too_big, no_data = [], [], []
    for (pre, D, P), vols in sorted(found.items()):
        vol = max(vols); d = vols[vol]
        mm = pre in UIUC_MM_SERIES  # these makers give sizes in millimetres
        Dv, Pv = D, P
        if not mm:  # UIUC drops the decimal point: 125x6 = 12.5 x 6, 12x65 = 12 x 6.5
            if Dv >= 30:
                Dv = Dv / 10
            if Pv > 3 * Dv:
                Pv = Pv / 10
        D_in, P_in = (Dv / 25.4, Pv / 25.4) if mm else (Dv, Pv)
        series, kmax, buyable = UIUC_SERIES.get(pre, (pre.upper(), 120000, True))
        label = f"{Dv:g}x{Pv:g}{'mm' if mm else ''}"
        name = f"{series} {label} (UIUC)"
        if D_in > max_d + 1e-9:
            too_big.append(name); continue
        J, Ct, Cp, rpm_st, rpm_wt = [], [], [], 0, 0
        st = [v for f in d["static"] for v in _numeric_rows(f) if len(v) >= 3]
        if st:
            rpm_st = max(v[0] for v in st)
            top = [v for v in st if v[0] >= 0.9 * rpm_st]
            J.append(0.0); Ct.append(_mean(v[1] for v in top)); Cp.append(_mean(v[2] for v in top))
        if d["wt"]:
            rpm_wt = max(r for r, _ in d["wt"])
            pts = [v for r, f in d["wt"] if r >= 0.95 * rpm_wt for v in _numeric_rows(f) if len(v) >= 3 and v[0] > 0]
            pts.sort()
            # average points closer than 0.01 in J
            merged = []
            for v in pts:
                if merged and abs(v[0] - merged[-1][0][0]) < 0.01:
                    merged[-1].append(v)
                else:
                    merged.append([v])
            for grp in merged:
                J.append(_mean(g[0] for g in grp)); Ct.append(_mean(g[1] for g in grp))
                Cp.append(_mean(g[2] for g in grp))
        if len(J) < 2:
            no_data.append(name)
        include, notes = "yes", []
        if not d["wt"]:
            include = "no"; notes.append("static data only - no thrust-vs-airspeed data")
        if not st:
            notes.append("no static data - static thrust taken from lowest tested J (slightly low)")
        if not buyable:
            include = "no"; notes.append("research prop, not sold")
        if not J:
            continue
        fname = f"{pre}_{D:g}x{P:g}.csv".replace("/", "_")
        with open(os.path.join(out_dir, fname), "w", encoding="utf-8") as fo:
            fo.write(f"# {name}: UIUC Propeller Database volume {vol}; static {rpm_st:.0f} RPM, wind tunnel {rpm_wt:.0f} RPM\n")
            fo.write("J,Ct,Cp\n")
            for j, a, b in sorted(zip(J, Ct, Cp)):
                fo.write(f"{j:.5f},{a:.6f},{b:.6f}\n")
        mass = 30 * (D_in / 12) ** 3  # rough estimate scaled from an APC 12" E prop
        rows.append(dict(name=name, diameter_in=f"{D_in:.3g}", pitch_in=f"{P_in:.3g}", mass_g=f"{mass:.0f}", format="csv",
                         file=f"props/uiuc/{fname}", cal_group="uiuc", max_rpm=f"{kmax / D_in:.0f}", include=include,
                         notes="; ".join(["UIUC vol " + str(vol), "mass estimated", "RPM limit estimated"] + notes)))

    # --- merge into props.csv: drop old 'uiuc' rows, add the new ones
    pcsv = os.path.join(data_dir, "props.csv")
    old = _read_csv(pcsv) if os.path.exists(pcsv) else []
    cols = list(old[0].keys()) if old else list(rows[0].keys())
    for c in ("include", "notes"):
        if c not in cols:
            cols.append(c)
    kept = [r for r in old if (r.get("cal_group") or "") != "uiuc"]
    prev = {r["name"]: r for r in old if (r.get("cal_group") or "") == "uiuc"}
    for r in rows:  # keep a user's own edits of mass / include on re-import
        if r["name"] in prev:
            r["mass_g"] = prev[r["name"]].get("mass_g") or r["mass_g"]
            r["include"] = prev[r["name"]].get("include") or r["include"]
    with open(pcsv, "w", newline="", encoding="utf-8") as fo:
        w = csv.DictWriter(fo, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for r in kept + rows:
            w.writerow({c: r.get(c, "") for c in cols})

    # --- point the 'uiuc' calibration rows at the APC 12x6E reference from the import
    ccsv = os.path.join(data_dir, "calibration.csv")
    if os.path.exists(ccsv) and any(r["name"] == ref_name for r in rows):
        cal_rows = _read_csv(ccsv)
        for r in cal_rows:
            if r.get("group") == "uiuc":
                r["prop"] = ref_name
        with open(ccsv, "w", newline="", encoding="utf-8") as fo:
            w = csv.DictWriter(fo, fieldnames=list(cal_rows[0].keys())); w.writeheader(); w.writerows(cal_rows)

    n_on = sum(r["include"] == "yes" for r in rows)
    print(f"\nUIUC import from {db_folder}")
    print(f"  {len(rows)} props within the {max_d:g} in rule written to data/props/uiuc/ and props.csv "
          f"({n_on} switched on, {len(rows) - n_on} off: static-only data or research props)")
    print(f"  {len(too_big)} props over {max_d:g} in left out: {', '.join(sorted(too_big))}")
    if skipped_names:
        print(f"  non-standard file names not imported (research / micro props): {', '.join(sorted(skipped_names))}")
    by_series = {}
    for r in rows:
        if r["include"] == "yes":
            base = r["name"][:-len(" (UIUC)")]
            ser, size = base.rsplit(" ", 1)
            by_series.setdefault(ser, []).append(size)
    for s, sizes in sorted(by_series.items()):
        print(f"    {s}: {', '.join(sizes)}")
    return rows


# APC max RPM = constant / D_in (apcprop.com/technical-information/rpm-limits): slow flyer 65,000, multi-rotor 105,000,
# folding electric 120,000, thin electric 150,000. Anything else (sport/glow, rated 190,000+) gets the 150,000 of the E series.
APC_RPM_LIMIT = (("SF", 65000), ("MR", 105000), ("F", 120000))
APC_RPM_DEFAULT = 150000


def import_apc(folder, data_dir, cfg):
    """Add every rule-legal APC PER3 table (.dat or .json, sub-folders too) in folder to props.csv.

    Props already in props.csv (same name or same PER3_ file stem) are left as they are, so mass / include
    edits survive a re-import. If a prop exists as both .dat and .json the .dat is used.
    """
    max_d = cfg["rules"]["max_prop_diameter_in"]
    files = {}
    for ext in (".json", ".dat"):  # .dat last so it wins
        for p in glob.glob(os.path.join(glob.escape(folder), "**", "PER3_*" + ext), recursive=True):
            files[os.path.splitext(os.path.basename(p))[0]] = p
    pcsv = os.path.join(data_dir, "props.csv")
    old = _read_csv(pcsv) if os.path.exists(pcsv) else []
    have_names = {r["name"] for r in old}
    have_stems = {os.path.splitext(os.path.basename(r.get("file", "")))[0] for r in old if r.get("format") == "apc"}
    rows, too_big, bad = [], [], []
    for stem, path in sorted(files.items()):
        label = _apc_label(path)
        m = re.match(r"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)", label)
        name = f"APC {label}"
        if stem in have_stems or name in have_names:
            continue
        if not m:
            bad.append(os.path.basename(path)); continue
        D, P = float(m.group(1)), float(m.group(2))
        if D > max_d + 1e-9:
            too_big.append(label); continue
        try:
            Prop(dict(name=name, diameter_in=str(D), format="apc", file=os.path.abspath(path)), data_dir)
        except (ValueError, OSError):
            bad.append(os.path.basename(path)); continue
        suffix = label[m.end():]
        k = next((c for s, c in APC_RPM_LIMIT if suffix.upper().startswith(s)), APC_RPM_DEFAULT)
        rows.append(dict(name=name, diameter_in=f"{D:g}", pitch_in=f"{P:g}", mass_g=f"{30 * (D / 12) ** 3:.0f}",
                         format="apc", file=os.path.relpath(path, data_dir).replace(os.sep, "/"), cal_group="apc_per3",
                         max_rpm=f"{k / D:.0f}", include="yes",
                         notes=f"APC PER3 performance; mass estimate - EDIT; RPM limit {k}/D estimated"))
    if rows:
        cols = list(old[0].keys()) if old else list(rows[0].keys())
        for c in ("include", "notes"):
            if c not in cols:
                cols.append(c)
        with open(pcsv, "w", newline="", encoding="utf-8") as fo:
            w = csv.DictWriter(fo, fieldnames=cols, extrasaction="ignore"); w.writeheader()
            for r in old + rows:
                w.writerow({c: r.get(c, "") for c in cols})
    print(f"\nAPC import from {folder}: {len(files)} tables found")
    print(f"  {len(rows)} new props within the {max_d:g} in rule added to props.csv (cal_group apc_per3), "
          f"{len(files) - len(rows) - len(too_big) - len(bad)} already listed")
    if too_big:
        print(f"  {len(too_big)} over {max_d:g} in left out: {', '.join(too_big)}")
    if bad:
        print(f"  unreadable, left out: {', '.join(bad)}")
    return rows



# =============================================================================
# Motor list import (rcplanes.online search export, or a CSV with brand,type,power,imax,kv,mass)
# =============================================================================
# Rm and I0 are not in such lists. They are estimated from Kv and mass with a fit to 16 motors whose datasheets
# give both (SunnySky X2814/X2820, Hobbywing SkyWalker 2814/2820/2826 SL, T-Motor AT2820/AT2826, 109-176 g):
#   Rm [mOhm] = exp(24.43) * Kv^-2.02 * mass_g^-1.48     (rms error 13 %, worst 29 %)
#   I0 [A @ 10 V] = exp(-16.06) * Kv^1.56 * mass_g^1.19  (rms error 7 %, worst 14 %)
# Only valid for outrunners of similar size - the import limits the shortlist to that range.
def estimate_rm_i0(kv, mass_g):
    return math.exp(24.43) * kv ** -2.02 * mass_g ** -1.48, math.exp(-16.06) * kv ** 1.56 * mass_g ** 1.19


def _parse_motor_list(path):
    rows = []
    for line in open(path, encoding="utf-8", errors="replace"):
        parts = [x.strip() for x in line.rstrip("\n").split("\t")]
        if len(parts) != 7:
            continue
        brand, typ, pw, imax, kv, _oz, g = parts
        try:
            pw, kv, g = float(pw), float(kv), float(g)
        except ValueError:
            continue
        try:
            imax = float(imax)
        except ValueError:
            imax = float("nan")
        rows.append(dict(brand=brand, type=typ.rstrip("*").strip(), discontinued="*" in typ or "*" in brand,
                         power_w=pw, imax_a=imax, kv=kv, mass_g=g))
    return rows


def import_motors(path, data_dir, kv_min, kv_max, i_min, m_min, m_max, keep_discontinued=False):
    rows = _parse_motor_list(path)
    if not rows:
        sys.exit(f"no motors read from {path} (expected the tab-separated rcplanes.online result table)")
    # full clean list, with estimates, for reference
    allp = os.path.join(data_dir, "motor_list_all.csv")
    with open(allp, "w", newline="", encoding="utf-8") as fo:
        w = csv.writer(fo)
        w.writerow(["brand", "type", "power_w", "imax_a", "kv", "mass_g", "discontinued", "rm_mohm_est", "i0_a_est"])
        for r in rows:
            rm, i0 = estimate_rm_i0(r["kv"], r["mass_g"])
            w.writerow([r["brand"], r["type"], r["power_w"], "" if math.isnan(r["imax_a"]) else r["imax_a"], r["kv"],
                        r["mass_g"], "yes" if r["discontinued"] else "", f"{rm:.1f}", f"{i0:.2f}"])
    cand = [r for r in rows if kv_min <= r["kv"] <= kv_max and m_min <= r["mass_g"] <= m_max
            and not math.isnan(r["imax_a"]) and r["imax_a"] >= i_min and (keep_discontinued or not r["discontinued"])]
    mcsv = os.path.join(data_dir, "motors.csv")
    old = _read_csv(mcsv) if os.path.exists(mcsv) else []
    cols = list(old[0].keys()) if old else ["name", "brand", "kv", "rm_mohm", "i0_a", "i0_voltage", "mass_g",
                                            "max_current_a", "rating_note", "source"]
    if "include" not in cols:
        cols.append("include")
    measured = [r for r in old if "estimated" not in (r.get("source", "") or "").lower()]
    norm = lambda b: re.sub(r"[^a-z]", "", (b or "").lower())
    added, dup = [], 0
    for r in cand:
        # skip motors already in the library with real datasheet values (same brand, Kv, mass within 4 g)
        if any(norm(o.get("brand")) == norm(r["brand"]) and abs(float(o["kv"]) - r["kv"]) < 1
               and abs(float(o["mass_g"]) - r["mass_g"]) <= 4 for o in measured):
            dup += 1; continue
        rm, i0 = estimate_rm_i0(r["kv"], r["mass_g"])
        added.append(dict(name=f"{r['brand']} {r['type']}", brand=r["brand"], kv=f"{r['kv']:g}", rm_mohm=f"{rm:.1f}",
                          i0_a=f"{i0:.2f}", i0_voltage="10", mass_g=f"{r['mass_g']:g}", max_current_a=f"{r['imax_a']:g}",
                          rating_note=f"{r['imax_a']:g} A, {r['power_w']:g} W (duration unknown)",
                          source="rcplanes.online list; Rm/I0 estimated", include="yes"))
    names, uniq = set(), []
    for a in added:
        if a["name"] not in names:
            names.add(a["name"]); uniq.append(a)
    with open(mcsv, "w", newline="", encoding="utf-8") as fo:
        w = csv.DictWriter(fo, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for r in measured + uniq:
            w.writerow({c: r.get(c, "") for c in cols})
    print(f"\nMotor list import from {path}: {len(rows)} motors read")
    print(f"  full list with estimated Rm/I0 -> {allp}")
    print(f"  shortlist Kv {kv_min:g}-{kv_max:g}, >= {i_min:g} A, {m_min:g}-{m_max:g} g"
          f"{'' if keep_discontinued else ', not discontinued'}: {len(cand)} motors")
    print(f"  {dup} already in motors.csv with datasheet values (kept as they are); {len(uniq)} added to motors.csv "
          f"with estimated Rm/I0 (earlier estimated rows replaced)")
    return uniq

# =============================================================================
# Single-combination detail
# =============================================================================
def single(lib, names, voc=None):
    cfg = lib["cfg"]; cal = Calibration(lib, cfg["model"]["air_density_kg_m3"])
    find = lambda lst, n: next((x for x in lst if x.name == n), None)
    parts = [find(lib["motors"], names[0]), find(lib["props"], names[1]), find(lib["escs"], names[2]),
             find(lib["batteries"], names[3])]
    for kind, p, n in zip(("motor", "prop", "esc", "battery"), parts, names):
        if p is None:
            sys.exit(f"{kind} '{n}' not found in library")
    s = System(*parts, cal, cfg)
    if parts[1].blades > 2 and two_blade_only(cfg):
        print(f"  note: {parts[1].name} is a {parts[1].blades}-blade prop - left out of the search in 2-blade-only mode, "
              f"its calibration (from 2-blade bench tests) is unverified")
    lim = (f"limits: motor {s.limit_motor:g} A, battery {s.limit_batt:g} A" if s.mode == "cap"
           else f"limit {s.limit:g} A (reject)" if s.limit else "no current limit")
    print(f"\n{names[0]} + {names[1]} + {names[2]} + {names[3]}\n  ({lim}; R pack+wiring {s.r_line * 1000:.1f} mOhm, "
          f"R motor+ESC {s.r_motor * 1000:.1f} mOhm)")
    print(f"{'V m/s':>6} {'RPM':>6} {'thrust gf':>9} {'I_batt':>7} {'I_motor':>7} {'V_batt':>6} {'P_in W':>6} {'thr':>5}")
    for pt in s.curve(cfg["model"]["speeds_mps"], voc):
        print(f"{pt['v']:6.1f} {pt['rpm']:6.0f} {pt['gf']:9.0f} {pt['I_batt']:7.1f} {pt['I_motor']:7.1f} "
              f"{pt['V_batt']:6.2f} {pt['P_in']:6.0f} {pt['throttle']:5.2f}"
              + (f"  {pt['limited_by']}-limited" if pt["limited"] else ""))


# =============================================================================
def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default=os.path.join(here, "data_2"), help="data folder")
    ap.add_argument("--plane", default=None, help="plane JSON (default: <data>/plane.json)")
    ap.add_argument("--no-plane", action="store_true", help="ignore plane stats; rank by a propulsion metric")
    ap.add_argument("--metric", choices=["avg_thrust", "static_thrust", "net_thrust", "thrust_per_watt"],
                    help="ranking metric without a plane (default from config.json)")
    ap.add_argument("--limit-mode", choices=["cap", "reject", "ignore"], help="override current_limit_mode")
    ap.add_argument("--out", default=os.path.join(here, "results"), help="output folder")
    ap.add_argument("--top", type=int, default=10, help="how many to print / plot")
    ap.add_argument("--no-plots", action="store_true")
    ap.add_argument("--single", nargs=4, metavar=("MOTOR", "PROP", "ESC", "BATTERY"),
                    help="print the curve of one combination instead of optimising")
    ap.add_argument("--import-tunnel", metavar="FOLDER",
                    help="convert wind-tunnel logs (one sub-folder per prop) into prop tables, then stop")
    ap.add_argument("--tunnel-rho", type=float, default=1.20, help="air density during the tunnel tests (kg/m3)")
    ap.add_argument("--tunnel-min-rpm", type=float, default=4000, help="ignore tunnel points below this RPM")
    ap.add_argument("--tunnel-prop-mass", type=float, default=30, help="prop mass (g) written to props.csv")
    ap.add_argument("--import-uiuc", metavar="FOLDER",
                    help="add every rule-legal prop from an unzipped UIUC-propDB folder to props.csv, then stop")
    ap.add_argument("--import-apc", metavar="FOLDER",
                    help="add every rule-legal APC PER3 .dat / .json table in FOLDER to props.csv, then stop")
    ap.add_argument("--import-motors", metavar="FILE",
                    help="add motors from an rcplanes.online result list (Rm/I0 estimated), then stop")
    ap.add_argument("--kv-min", type=float, default=900); ap.add_argument("--kv-max", type=float, default=1450)
    ap.add_argument("--min-current", type=float, default=55, help="motor list import: minimum rated current (A)")
    ap.add_argument("--min-mass", type=float, default=90); ap.add_argument("--max-mass", type=float, default=220)
    ap.add_argument("--keep-discontinued", action="store_true")
    ap.add_argument("--max-current", type=float, default=None,
                    help="cap battery current for every combination (A), e.g. 80, to compare motors at equal current")
    ap.add_argument("--thrust-cal", choices=Calibration.MODES,
                    help="thrust calibration factor above the last bench point: hold (last factor), linear "
                         "(extend last slope), flat (group mean at every rpm); overrides thrust_cal_extrapolation")
    ap.add_argument("--two-blade-only", action="store_true",
                    help="leave 3/4-blade props out of the search (their calibration from 2-blade bench tests is "
                         "unverified); same as model.multi_blade_props = exclude in config.json")
    ap.add_argument("--prescreen", type=int, default=None,
                    help="keep only the N best props (by thrust with each motor) before the full search; 0 = off")
    a = ap.parse_args()

    if a.import_motors:
        import_motors(a.import_motors, a.data, a.kv_min, a.kv_max, a.min_current, a.min_mass, a.max_mass,
                      a.keep_discontinued)
        return

    if a.import_uiuc:
        import_uiuc(a.import_uiuc, a.data, load_config(a.data))
        return

    if a.import_apc:
        import_apc(a.import_apc, a.data, load_config(a.data))
        return

    if a.import_tunnel:
        cfg = load_config(a.data)
        import_tunnel(a.import_tunnel, a.data, a.tunnel_rho, a.tunnel_min_rpm, a.tunnel_prop_mass,
                      a.out, cfg, plots=not a.no_plots)
        return

    lib = load_library(a.data)
    if a.limit_mode:
        lib["cfg"]["model"]["current_limit_mode"] = a.limit_mode
    if a.max_current:
        lib["cfg"]["model"]["max_system_current_a"] = a.max_current
    if a.thrust_cal:
        lib["cfg"]["model"]["thrust_cal_extrapolation"] = a.thrust_cal
    if a.two_blade_only:
        lib["cfg"]["model"]["multi_blade_props"] = "exclude"
    if a.single:
        single(lib, a.single); return

    plane = None
    if not a.no_plane:
        pp = a.plane or os.path.join(a.data, "plane.json")
        if os.path.exists(pp):
            plane = Plane(pp)
        else:
            print(f"  no plane file at {pp} - running propulsion-only ranking")
    rk = lib["cfg"]["ranking_without_plane"]
    metric = "score" if plane else (a.metric or rk.get("metric", "net_thrust"))
    avg_to = rk.get("avg_up_to_mps", 12)
    labels = {"score": "estimated flight score", "avg_thrust": f"mean thrust 0-{avg_to:g} m/s (gf)",
              "static_thrust": "static thrust (gf)", "net_thrust": f"mean thrust 0-{avg_to:g} m/s minus propulsion mass (gf)",
              "thrust_per_watt": "static thrust per watt (g/W)"}

    print(f"propcalc - data: {a.data}")
    results, key = evaluate_all(lib, plane, metric, avg_to, prescreen=a.prescreen)
    if not results:
        sys.exit("no combination passed the rules / limits")
    os.makedirs(a.out, exist_ok=True)
    write_csv(results, os.path.join(a.out, "results.csv"), plane)
    print_top(results, key, a.top, plane)
    best = results[0]
    print(f"\nBest by {labels[metric]}: {best['motor']} + {best['prop']} + {best['esc']} + {best['battery']}")
    if plane:
        print(f"  max takeoff mass {best['tow_kg']:.2f} kg, payload {best['payload_kg']:.2f} kg "
              f"({best['payload_kg'] / LB:.2f} lb), score {best['score']:.1f}, mission {best['mission_mah']:.0f} mAh")
    files = [] if a.no_plots else make_plots(results, key, a.out, plane, a.top, labels[metric])
    print(f"\nWrote {os.path.join(a.out, 'results.csv')}" + "".join(f"\n      {f}" for f in files))


if __name__ == "__main__":
    main()
