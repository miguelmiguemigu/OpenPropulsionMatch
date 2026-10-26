#!/usr/bin/env python3
"""
thrust_graphs.py - thrust graphs of one propulsion combination for the aero section
================================================================================

Uses the propcalc model and data folder, draws every thrust graph for one
motor + prop + ESC + battery and writes the numbers behind them as csv.

    python3 thrust_graphs.py                                  # best combination of results/results.csv
    python3 thrust_graphs.py "X2820 1250kV" "APC 12x8E" "Skystars Jupiter Single 80A ESC" "GNB 930 120C"
    python3 thrust_graphs.py --compare "APC 12x8E" "APC 12x6E" --out results/aero_12x8E

Graphs 09-11 need the airframe (data_2/plane.json); skip them with --no-plane.
Graphs 13-15: motor torque, propwash, cruise current and flight time for a given drag.
"""
import argparse
import csv
import math
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import propcalc as pc

G, LBF = pc.G, pc.LBF
SURFACE, INK, INK2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SEQ = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"]
ISA = [(0, 1.225), (500, 1.167), (1000, 1.112), (1500, 1.058)]  # altitude m, density kg/m3

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "text.color": INK, "xtick.color": MUTED, "ytick.color": MUTED,
    "xtick.labelcolor": INK2, "ytick.labelcolor": INK2, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.grid": True, "axes.axisbelow": True, "axes.spines.top": False, "axes.spines.right": False,
    "font.size": 9, "lines.linewidth": 2, "legend.frameon": False, "legend.fontsize": 8,
})


def seq(n):
    """n ordered steps of the one-hue ramp, light -> dark."""
    return [SEQ[round(i * (len(SEQ) - 1) / max(n - 1, 1))] for i in range(n)]


def title(fig, main, sub):
    fig.suptitle(main, x=0.01, ha="left", fontsize=12, fontweight="bold")
    fig.text(0.01, 0.01, sub, fontsize=7.5, color=MUTED, ha="left", va="bottom")


def gf_axis(ax):
    sec = ax.secondary_yaxis("right", functions=(lambda n: n / G * 1000, lambda g: g * G / 1000))
    sec.set_ylabel("Thrust (gf)"); sec.spines["right"].set_visible(False)


def save(fig, out, name, files):
    fig.tight_layout(rect=(0, 0.035, 1, 0.95))
    p = os.path.join(out, name); fig.savefig(p, dpi=150); plt.close(fig); files.append(p)


def find(lst, name, kind):
    x = next((x for x in lst if x.name == name), None)
    if x is None:
        sys.exit(f"{kind} '{name}' not found in library")
    return x


def ground_roll(T_of_v, plane, m, rho):
    """Distance to liftoff speed at mass m (same model as propcalc.Evaluator.mission), None if it never gets there."""
    vlof = plane.klof * plane.vstall(m, rho)
    v = x = t = 0.0; dt = 0.01
    while v < vlof:
        q = 0.5 * rho * v * v
        a = (T_of_v(v) - plane.drag(m, v, rho, CL=plane.CLg) - plane.mu * max(m * G - q * plane.S * plane.CLg, 0)) / m
        if a <= 0 or t > 30:
            return None
        v += a * dt; x += v * dt; t += dt
    return x


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("combo", nargs="*", metavar="NAME", help="MOTOR PROP ESC BATTERY (default: #1 of results/results.csv)")
    ap.add_argument("--data", default=os.path.join(here, "data_2"), help="data folder")
    ap.add_argument("--plane", help="plane json (default: <data>/plane.json)")
    ap.add_argument("--no-plane", action="store_true", help="skip the graphs that need the airframe")
    ap.add_argument("--out", default=os.path.join(here, "results", "aero"), help="output folder")
    ap.add_argument("--vmax", type=float, default=30.0, help="highest airspeed, m/s")
    ap.add_argument("--compare", nargs="*", default=["APC 12x8E", "APC 12x6E", "APC 12x7E-3", "APC 11x8E"],
                    help="props drawn against the chosen one on the same motor/ESC/battery")
    a = ap.parse_args()

    names = a.combo
    if not names:
        res = os.path.join(here, "results", "results.csv")
        if not os.path.exists(res):
            sys.exit("no combination given and no results/results.csv - run propcalc.py first or pass MOTOR PROP ESC BATTERY")
        with open(res, newline="", encoding="utf-8") as f:
            r = next(csv.DictReader(f))
        names = [r["motor"], r["prop"], r["esc"], r["battery"]]
    if len(names) != 4:
        sys.exit("give exactly four names: MOTOR PROP ESC BATTERY")

    lib = pc.load_library(a.data); cfg = lib["cfg"]
    rho0 = cfg["model"]["air_density_kg_m3"]
    cal = pc.Calibration(lib, rho0)
    motor, prop = find(lib["motors"], names[0], "motor"), find(lib["props"], names[1], "prop")
    esc, bat = find(lib["escs"], names[2], "esc"), find(lib["batteries"], names[3], "battery")
    s = pc.System(motor, prop, esc, bat, cal, cfg)
    plane = None
    if not a.no_plane:
        pp = a.plane or os.path.join(a.data, "plane.json")
        plane = pc.Plane(pp) if os.path.exists(pp) else None

    os.makedirs(a.out, exist_ok=True)
    files = []
    V = np.arange(0.0, a.vmax + 0.01, 1.0)
    cells = bat.cells
    combo = " + ".join(names)
    lim = (f"current limit motor {s.limit_motor:.0f} A / battery {s.limit_batt:.0f} A" if s.limit and s.mode == "cap"
           else "no current limit")
    sub = f"{combo}  |  {lim}, rho {rho0} kg/m3  |  propcalc model, not a measurement"
    N = lambda crv: np.array([p["T"] for p in crv])

    full = s.curve(V)               # full stick, fresh pack, current limit as configured
    free = s.curve(V, cap=None)     # same without any current limit
    T = N(full)

    # ---- 01 thrust vs airspeed + fit --------------------------------------------------------
    ok = T > 0.02 * T[0]
    c2, c1, c0 = np.polyfit(V[ok], T[ok], 2)
    fit = c0 + c1 * V + c2 * V * V
    err = float(np.max(np.abs(fit[ok] - T[ok])))
    fig, ax = plt.subplots(figsize=(10, 6))
    if any(p["limited"] for p in full):
        ax.plot(V, N(free), color=MUTED, lw=1.5, ls="--", label="without current limit")
    ax.plot(V, T, color=CAT[0], lw=2.5, marker="o", ms=4, label="thrust available (full stick, fresh pack)")
    ax.plot(V[ok], fit[ok], color=INK, lw=1, ls=":", label=f"fit  T = {c0:.2f} {c1:+.4f} V {c2:+.5f} V^2  [N, m/s]  (max error {err:.2f} N)")
    ax.annotate(f"static {T[0]:.1f} N = {T[0] / G * 1000:.0f} gf", (0, T[0]), xytext=(8, 8), textcoords="offset points", fontsize=8, color=INK2)
    ax.set_ylim(0, max(N(free).max(), T.max()) * 1.12); ax.set_xlim(0, a.vmax)
    ax.set_xlabel("Airspeed (m/s)"); ax.set_ylabel("Thrust (N)"); gf_axis(ax); ax.legend(loc="lower left")
    title(fig, "Thrust available vs airspeed", sub)
    save(fig, a.out, "01_thrust_vs_airspeed.png", files)

    # ---- 02 thrust vs airspeed per throttle setting -----------------------------------------
    throttles = [0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    by_thr = {th: [s.point(v, th) for v in V] for th in throttles}
    fig, ax = plt.subplots(figsize=(10, 6))
    marks = []  # [static thrust, label] - settings that land on the same line (current limit) share one label
    for th, col in zip(throttles, seq(len(throttles))):
        y = N(by_thr[th]); ax.plot(V, y, color=col, label=f"{th * 100:.0f} %")
        if marks and abs(y[0] - marks[-1][0]) < 0.03 * T[0]:
            marks[-1][1] = marks[-1][1].split(" ")[0].split("-")[0] + f"-{th * 100:.0f} %  (current-limited)"
        else:
            marks.append([y[0], f"{th * 100:.0f} %"])
    for y0, lab in marks:
        ax.annotate(lab, (0, y0), xytext=(4, 3), textcoords="offset points", fontsize=7.5, color=INK2)
    ax.set_ylim(0, None); ax.set_xlim(0, a.vmax)
    ax.set_xlabel("Airspeed (m/s)"); ax.set_ylabel("Thrust (N)"); gf_axis(ax); ax.legend(title="throttle", ncols=2)
    title(fig, "Thrust vs airspeed at part throttle (fresh pack)", sub)
    save(fig, a.out, "02_thrust_vs_throttle_setting.png", files)

    # ---- 03 thrust vs airspeed as the pack empties ------------------------------------------
    vcell = [4.2, 4.0, 3.8, 3.6, 3.4]
    by_voc = {vc: s.curve(V, voc=vc * cells) for vc in vcell}
    fig, ax = plt.subplots(figsize=(10, 6))
    for vc, col in zip(vcell, seq(len(vcell))[::-1]):
        ax.plot(V, N(by_voc[vc]), color=col, label=f"{vc:.1f} V/cell ({vc * cells:.1f} V open circuit)")
    ax.set_ylim(0, None); ax.set_xlim(0, a.vmax)
    ax.set_xlabel("Airspeed (m/s)"); ax.set_ylabel("Thrust (N)"); gf_axis(ax); ax.legend(title="pack state")
    title(fig, "Full-stick thrust as the battery empties", sub)
    save(fig, a.out, "03_thrust_vs_battery_state.png", files)

    # ---- 04 thrust vs airspeed per air density ----------------------------------------------
    by_rho = {}
    for alt, rho in ISA:
        s.rho = rho; by_rho[alt] = s.curve(V)
    s.rho = rho0
    fig, ax = plt.subplots(figsize=(10, 6))
    for (alt, rho), col in zip(ISA, seq(len(ISA))[::-1]):
        ax.plot(V, N(by_rho[alt]), color=col, label=f"{alt} m ISA  ({rho:.3f} kg/m3)")
    ax.set_ylim(0, None); ax.set_xlim(0, a.vmax)
    ax.set_xlabel("Airspeed (m/s)"); ax.set_ylabel("Thrust (N)"); gf_axis(ax); ax.legend(title="field elevation")
    title(fig, "Full-stick thrust vs air density (field elevation)", sub)
    save(fig, a.out, "04_thrust_vs_air_density.png", files)

    # ---- 05 power and efficiency ------------------------------------------------------------
    P_in = np.array([p["P_in"] for p in full]); P_sh = np.array([p["P_shaft"] for p in full]); P_thr = T * V
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 5.5))
    a1.plot(V, P_in, color=CAT[0], label="electrical (battery)")
    a1.plot(V, P_sh, color=CAT[1], label="shaft (into the prop)")
    a1.plot(V, P_thr, color=CAT[2], label="thrust power  T x V  (power available)")
    a1.set_ylim(0, None); a1.set_xlim(0, a.vmax); a1.set_xlabel("Airspeed (m/s)"); a1.set_ylabel("Power (W)"); a1.legend()
    a1.set_title("Power", loc="left", fontsize=10)
    with np.errstate(divide="ignore", invalid="ignore"):
        a2.plot(V, 100 * P_thr / P_sh, color=CAT[1], label="propeller  (T x V / shaft)")
        a2.plot(V, 100 * P_thr / P_in, color=CAT[0], label="whole system  (T x V / electrical)")
    a2.set_ylim(0, 100); a2.set_xlim(0, a.vmax); a2.set_xlabel("Airspeed (m/s)"); a2.set_ylabel("Efficiency (%)"); a2.legend()
    a2.set_title("Efficiency", loc="left", fontsize=10)
    title(fig, "Power available and efficiency vs airspeed (full stick, fresh pack)", sub)
    save(fig, a.out, "05_power_and_efficiency.png", files)

    # ---- 06 operating point -----------------------------------------------------------------
    fig, axs = plt.subplots(1, 3, figsize=(13, 4.6))
    for ax, key, lab, k in zip(axs, ("rpm", "I_batt", "throttle"), ("Prop speed (RPM)", "Battery current (A)", "Effective throttle (%)"), (1, 1, 100)):
        ax.plot(V, [p[key] * k for p in full], color=CAT[0], label="with current limit")
        if any(p["limited"] for p in full):
            ax.plot(V, [p[key] * k for p in free], color=MUTED, lw=1.5, ls="--", label="without current limit")
        ax.set_ylim(0, None); ax.set_xlim(0, a.vmax); ax.set_xlabel("Airspeed (m/s)"); ax.set_title(lab, loc="left", fontsize=10)
    if prop.max_rpm < 1e8:
        axs[0].axhline(prop.max_rpm, color=INK, lw=1, ls=":"); axs[0].set_ylim(0, max(prop.max_rpm * 1.08, axs[0].get_ylim()[1]))
        axs[0].annotate(f"prop RPM limit {prop.max_rpm:.0f}", (a.vmax, prop.max_rpm), xytext=(-4, -11), textcoords="offset points", ha="right", fontsize=7.5, color=INK2)
    axs[0].legend(loc="lower left")
    title(fig, "Operating point at full stick vs airspeed (fresh pack)", sub)
    save(fig, a.out, "06_operating_point.png", files)

    # ---- 07 propeller coefficients ----------------------------------------------------------
    tabs = [t for t in prop.tables if t[0] is None or t[0] <= max(prop.max_rpm, 1)] or prop.tables
    if len(tabs) > 5:
        tabs = [tabs[round(i * (len(tabs) - 1) / 4)] for i in range(5)]
    fig, axs = plt.subplots(1, 3, figsize=(13, 4.6))
    for (rpm, J, Ct, Cp), col in zip(tabs, seq(len(tabs)) if len(tabs) > 1 else [CAT[0]]):
        J, Ct, Cp = np.array(J), np.array(Ct), np.array(Cp); lab = f"{rpm:.0f} RPM" if rpm else "data table"
        axs[0].plot(J, Ct, color=col, label=lab); axs[1].plot(J, Cp, color=col)
        keep = Ct > 0
        axs[2].plot(J[keep], 100 * Ct[keep] * J[keep] / Cp[keep], color=col)
    for ax, lab in zip(axs, ("Thrust coefficient Ct", "Power coefficient Cp", "Efficiency  Ct J / Cp  (%)")):
        ax.set_ylim(0, None); ax.set_xlim(0, None); ax.set_xlabel("Advance ratio  J = V / (n D)"); ax.set_title(lab, loc="left", fontsize=10)
    if len(tabs) > 1:
        axs[0].legend()
    kt = cal.thrust(prop.group, full[0]["rpm"], prop.D)
    title(fig, f"Propeller data: {prop.name}  ({prop.D_in:g} x {prop.pitch:g} in)",
          f"raw data file, group '{prop.group}'  |  the model multiplies Ct by {kt:.3f} (at {full[0]['rpm']:.0f} RPM) and Cp by {s.kq:.3f} to match bench tests")
    save(fig, a.out, "07_prop_coefficients.png", files)

    # ---- 08 static thrust vs throttle (bench check) -----------------------------------------
    TH = np.linspace(0.2, 1.0, 33)
    st = [s.point(0.0, th) for th in TH]
    fig, axs = plt.subplots(1, 3, figsize=(13, 4.6))
    for ax, y, lab in zip(axs, (N(st), [p["I_batt"] for p in st], [p["rpm"] for p in st]), ("Static thrust (N)", "Battery current (A)", "Prop speed (RPM)")):
        ax.plot(TH * 100, y, color=CAT[0]); ax.set_ylim(0, None); ax.set_xlim(20, 100)
        ax.set_xlabel("Throttle stick (%)"); ax.set_title(lab, loc="left", fontsize=10)
    gf_axis(axs[0])
    title(fig, "Static thrust vs throttle - compare with a thrust-stand run (fresh pack)", sub)
    save(fig, a.out, "08_static_thrust_vs_throttle.png", files)

    # ---- 09-11 against the airframe ---------------------------------------------------------
    Tv = lambda v: pc._interp(v, list(V), list(T))
    if plane:
        ev = pc.Evaluator(s, plane, cfg); best = ev.best()
        m_tow, m_base = best["m_tow"], best["m_base"]
        psub = f"{combo}  |  airframe '{plane.name}'  |  model, not a measurement"
        vs_ = plane.vstall(m_tow, rho0)
        vv = np.linspace(max(0.9 * vs_, 1.0), a.vmax, 200)
        Ta = np.array([Tv(x) for x in vv]); D = np.array([plane.drag(m_tow, x, rho0) for x in vv])
        Tc = np.array([pc._interp(x, list(V), list(N(by_voc[3.8]))) for x in vv])
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.plot(vv, Ta, color=CAT[0], lw=2.5, label="thrust available, fresh pack")
        ax.plot(vv, Tc, color=CAT[0], lw=1.5, ls="--", label="thrust available, 3.8 V/cell")
        ax.plot(vv, D, color=CAT[1], lw=2.5, label=f"drag in level flight at {m_tow:.2f} kg")
        ax.axvline(vs_, color=INK, lw=1, ls=":")
        ax.annotate(f"stall {vs_:.1f} m/s", (vs_, 0), xytext=(4, 6), textcoords="offset points", fontsize=8, color=INK2)
        cross = np.where(np.diff(np.sign(Ta - D)) < 0)[0]
        if len(cross):
            i = cross[-1]; vmx = vv[i] + (vv[i + 1] - vv[i]) * (Ta[i] - D[i]) / ((Ta[i] - D[i]) - (Ta[i + 1] - D[i + 1]))
            ax.plot([vmx], [plane.drag(m_tow, vmx, rho0)], "o", color=INK, ms=6)
            ax.annotate(f"max level speed {vmx:.1f} m/s", (vmx, plane.drag(m_tow, vmx, rho0)), xytext=(10, -12), textcoords="offset points", fontsize=8, color=INK2)
        ax.set_ylim(0, None); ax.set_xlim(0, a.vmax); ax.set_xlabel("Airspeed (m/s)"); ax.set_ylabel("Force (N)"); ax.legend()
        title(fig, f"Thrust available vs drag at max takeoff mass {m_tow:.2f} kg", psub)
        save(fig, a.out, "09_thrust_vs_drag.png", files)

        masses = [m for m in np.linspace(m_base, m_tow, 4)] if m_tow > m_base + 0.05 else [m_tow]
        fig, ax = plt.subplots(figsize=(10, 6))
        for m, col in zip(masses, seq(len(masses)) if len(masses) > 1 else [CAT[0]]):
            x = np.linspace(plane.vstall(m, rho0), a.vmax, 150)
            roc = np.array([(Tv(v) - plane.drag(m, v, rho0)) * v / (m * G) for v in x])
            ax.plot(x[roc >= 0], roc[roc >= 0], color=col, label=f"{m:.2f} kg  (payload {m - m_base:.2f} kg)")
        ax.set_ylim(0, None); ax.set_xlim(0, a.vmax); ax.set_xlabel("Airspeed (m/s)"); ax.set_ylabel("Rate of climb (m/s)")
        ax.legend(title="takeoff mass")
        title(fig, "Rate of climb from excess thrust, stall speed to top speed (full stick, fresh pack)", psub)
        save(fig, a.out, "10_climb_rate_vs_airspeed.png", files)

        mm = np.linspace(m_base * 0.9, m_tow * 1.25, 60)
        roll = [ground_roll(Tv, plane, m, rho0) for m in mm]
        pts = [(m, r) for m, r in zip(mm, roll) if r is not None]
        fig, ax = plt.subplots(figsize=(10, 6))
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=CAT[0], lw=2.5, label="ground roll to liftoff")
        full_lim = plane.d["takeoff_distance_limit_m"]
        ax.axhline(full_lim, color=INK, lw=1, ls=":"); ax.axhline(plane.x_lim, color=INK, lw=1, ls="--")
        ax.annotate(f"takeoff limit {full_lim:.2f} m", (mm[0], full_lim), xytext=(2, 4), textcoords="offset points", fontsize=8, color=INK2)
        if plane.x_lim != full_lim:
            ax.annotate(f"limit with margin {plane.x_lim:.2f} m", (mm[0], plane.x_lim), xytext=(2, 4), textcoords="offset points", fontsize=8, color=INK2)
        ax.axvline(m_tow, color=MUTED, lw=1)
        ax.annotate(f"max takeoff mass {m_tow:.2f} kg", (m_tow, 0), xytext=(4, 6), textcoords="offset points", fontsize=8, color=INK2)
        ax.set_ylim(0, None); ax.set_xlabel("Takeoff mass (kg)"); ax.set_ylabel("Ground roll (m)")
        title(fig, "Takeoff ground roll vs takeoff mass (full stick, fresh pack)", psub)
        save(fig, a.out, "11_takeoff_roll_vs_mass.png", files)

    # ---- 12 propeller comparison ------------------------------------------------------------
    others = [p for p in lib["props"] if p.name in a.compare and p.name != prop.name][:len(CAT) - 1]
    if others:
        fig, ax = plt.subplots(figsize=(10, 6))
        for p, col in zip([prop] + others, CAT):
            sp = pc.System(motor, p, esc, bat, cal, cfg)
            ax.plot(V, N(sp.curve(V)), color=col, lw=2.5 if p is prop else 1.8, label=f"{p.name}  [{p.group}]")
        ax.set_ylim(0, None); ax.set_xlim(0, a.vmax)
        ax.set_xlabel("Airspeed (m/s)"); ax.set_ylabel("Thrust (N)"); gf_axis(ax); ax.legend(title="propeller  [data source]")
        title(fig, "Full-stick thrust vs airspeed - propellers on the same motor, ESC and battery",
              f"{names[0]} + {names[2]} + {names[3]}  |  {lim}  |  different data sources are scaled differently, compare with care")
        save(fig, a.out, "12_prop_comparison.png", files)

    # ---- 13 motor torque (reaction on the airframe) ------------------------------------------
    torque = lambda p: p["P_shaft"] / (2 * math.pi * p["rpm"] / 60)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 5.5))
    for th, col in zip(throttles, seq(len(throttles))):
        a1.plot(V, [torque(p) if p["T"] > 0 else np.nan for p in by_thr[th]], color=col, label=f"{th * 100:.0f} %")  # no data past zero thrust
    a1.set_ylim(0, None); a1.set_xlim(0, a.vmax); a1.set_xlabel("Airspeed (m/s)"); a1.set_ylabel("Torque (N m)")
    a1.set_title("Torque vs airspeed", loc="left", fontsize=10); a1.legend(title="throttle", ncols=2, loc="lower left")
    a2.plot(TH * 100, [torque(p) for p in st], color=CAT[0])
    a2.set_ylim(0, None); a2.set_xlim(20, 100); a2.set_xlabel("Throttle stick (%)"); a2.set_ylabel("Torque (N m)")
    a2.set_title("Static torque vs throttle", loc="left", fontsize=10)
    q_max = max(torque(p) for th in throttles for p in by_thr[th])
    title(fig, f"Motor torque = roll reaction on the airframe (peak {q_max:.3f} N m, fresh pack)",
          sub + "  |  acts opposite to prop rotation")
    save(fig, a.out, "13_motor_torque.png", files)

    # ---- 14 propwash (ideal momentum theory) ------------------------------------------------
    A_disc = math.pi * prop.D ** 2 / 4

    def wash(p):  # induced speed at the disc; far behind the prop the stream has V + 2w
        return 0.5 * (-p["v"] + math.sqrt(p["v"] ** 2 + 2 * max(p["T"], 0) / (s.rho * A_disc)))

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 5.5))
    for th, col in zip(throttles, seq(len(throttles))):
        w = np.array([wash(p) if p["T"] > 0 else np.nan for p in by_thr[th]])
        a1.plot(V, V + 2 * w, color=col, label=f"{th * 100:.0f} %")
        a2.plot(V, 1000 * prop.D * np.sqrt((V + w) / np.maximum(V + 2 * w, 1e-9)), color=col)
    a1.plot(V, V, color=INK, lw=1, ls=":", label="free stream (no prop)")
    a1.set_ylim(0, None); a1.set_xlim(0, a.vmax); a1.set_xlabel("Airspeed (m/s)"); a1.set_ylabel("Air speed in the propwash (m/s)")
    a1.set_title("Propwash speed, fully developed", loc="left", fontsize=10); a1.legend(title="throttle", ncols=2, loc="lower right")
    a2.axhline(1000 * prop.D, color=INK, lw=1, ls=":")
    a2.annotate(f"prop diameter {1000 * prop.D:.0f} mm", (0, 1000 * prop.D), xytext=(4, 4), textcoords="offset points", fontsize=7.5, color=INK2)
    a2.set_ylim(0, 1000 * prop.D * 1.1); a2.set_xlim(0, a.vmax); a2.set_xlabel("Airspeed (m/s)"); a2.set_ylabel("Propwash diameter (mm)")
    a2.set_title("Diameter of the contracted propwash", loc="left", fontsize=10)
    title(fig, "Propwash over the wing root and tail (fresh pack)",
          f"{combo}  |  {lim}  |  ideal momentum theory (no swirl, no fuselage): an upper bound")
    save(fig, a.out, "14_propwash.png", files)

    # ---- 15 current and endurance for a required thrust -------------------------------------
    usable = cfg["model"]["usable_capacity_fraction"] * bat.mah
    vcr = [v for v in (0, 8, 12, 16, 20, 24) if v <= a.vmax]
    thr_fine = np.linspace(0.15, 1.0, 35)
    cruise = {v: [s.point(float(v), th, s.voc_cruise) for th in thr_fine] for v in vcr}
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 5.5))
    for v, col in zip(vcr, seq(len(vcr))):
        pts = [p for p in cruise[v] if p["T"] > 0.2]
        a1.plot([p["T"] for p in pts], [p["I_batt"] for p in pts], color=col, label=f"{v} m/s")
        a2.plot([p["T"] for p in pts], [usable / p["I_batt"] * 0.06 for p in pts], color=col)
    a1.set_ylim(0, None); a1.set_xlim(0, None); a1.set_xlabel("Thrust required = drag (N)"); a1.set_ylabel("Battery current (A)")
    a1.set_title("Current needed to hold a thrust", loc="left", fontsize=10); a1.legend(title="airspeed")
    a2.set_ylim(0, 15); a2.set_xlim(0, None); a2.set_xlabel("Thrust required = drag (N)"); a2.set_ylabel("Flight time (min)")
    a2.set_title(f"Time on {usable:.0f} mAh usable - axis cut at 15 min", loc="left", fontsize=10)
    title(fig, "Cruise current and flight time for a given drag",
          f"{combo}  |  pack at {cfg['model']['voc_per_cell_cruise']} V/cell, {cfg['model']['usable_capacity_fraction'] * 100:.0f} % of {bat.mah:.0f} mAh usable  |  model, not a measurement")
    save(fig, a.out, "15_cruise_current_and_endurance.png", files)

    p = os.path.join(a.out, "cruise_data.csv")
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["airspeed_mps", "throttle", "thrust_N", "thrust_gf", "I_batt_A", "P_elec_W", "rpm", "torque_Nm", "propwash_mps", "flight_time_min"])
        for v in vcr:
            for pt in cruise[v]:
                w.writerow([v, f"{pt['throttle']:.3f}", f"{pt['T']:.3f}", f"{pt['gf']:.1f}", f"{pt['I_batt']:.2f}", f"{pt['P_in']:.1f}",
                            f"{pt['rpm']:.0f}", f"{torque(pt):.4f}", f"{pt['v'] + 2 * wash(pt):.2f}", f"{usable / pt['I_batt'] * 0.06:.2f}"])
    files.append(p)

    # ---- parts list: what the library knows, and what is only a guess ------------------------
    p = os.path.join(a.out, "components.csv")
    raw = {k: {r["name"]: r for r in pc._read_csv(os.path.join(a.data, f))}
           for k, f in (("motor", "motors.csv"), ("prop", "props.csv"), ("esc", "escs.csv"), ("battery", "batteries.csv"))}
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["part", "name", "mass_g", "key_data", "notes_from_library"])
        mr, pr, er, br = raw["motor"][motor.name], raw["prop"][prop.name], raw["esc"][esc.name], raw["battery"][bat.name]
        w.writerow(["motor", motor.name, f"{motor.mass:g}", f"{motor.kv:g} kV, max {motor.imax:g} A ({motor.rating})", mr.get("source", "")])
        w.writerow(["prop", prop.name, f"{prop.mass:g}", f"{prop.D_in:g} x {prop.pitch:g} in = {1000 * prop.D:.0f} mm diameter, RPM limit {prop.max_rpm:.0f}", pr.get("notes", "")])
        w.writerow(["esc", esc.name, f"{esc.mass:g}", f"{esc.cont:g} A continuous, {esc.burst:g} A burst", er.get("notes", "")])
        w.writerow(["battery", bat.name, f"{bat.mass:g}", f"{bat.cells}S {bat.mah:g} mAh, {bat.imax():.0f} A continuous", br.get("notes", "")])
        w.writerow(["total", "motor + prop + ESC + battery", f"{s.mass:g}", f"current limits: motor {s.limit_motor:g} A, battery {s.limit_batt:g} A" if s.limit else "", "excludes wiring, receiver, servos, mount"])
    files.append(p)

    # ---- numbers ----------------------------------------------------------------------------
    cols = ["case", "airspeed_mps", "thrust_N", "thrust_gf", "thrust_lbf", "rpm", "J", "throttle", "I_batt_A", "V_batt",
            "P_elec_W", "P_shaft_W", "P_thrust_W", "torque_Nm", "propwash_mps", "current_limited"]
    rows = []

    def add(case, crv):
        for p in crv:
            rows.append([case, f"{p['v']:.1f}", f"{p['T']:.3f}", f"{p['gf']:.1f}", f"{p['T'] / LBF:.4f}", f"{p['rpm']:.0f}",
                         f"{p['J']:.4f}", f"{p['throttle']:.3f}", f"{p['I_batt']:.2f}", f"{p['V_batt']:.2f}", f"{p['P_in']:.1f}",
                         f"{p['P_shaft']:.1f}", f"{p['T'] * p['v']:.1f}", f"{torque(p):.4f}", f"{p['v'] + 2 * wash(p):.2f}",
                         int(p["limited"])])

    add("full stick, fresh pack", full); add("full stick, fresh pack, no current limit", free)
    for th in throttles[:-1]:
        add(f"throttle {th * 100:.0f} %, fresh pack", by_thr[th])
    for vc in vcell:
        add(f"full stick, {vc:.1f} V/cell", by_voc[vc])
    for alt, rho in ISA[1:]:
        add(f"full stick, fresh pack, {alt} m ISA", by_rho[alt])
    p = os.path.join(a.out, "thrust_data.csv")
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(cols); w.writerows(rows)
    files.append(p)

    print(f"{combo}\n  {lim}, R pack+wiring {s.r_line * 1000:.1f} mOhm, R motor+ESC {s.r_motor * 1000:.1f} mOhm, "
          f"propulsion mass {s.mass:.0f} g")
    print(f"  static thrust {T[0]:.2f} N ({T[0] / G * 1000:.0f} gf), at 10 m/s {Tv(10):.2f} N, at 20 m/s {Tv(20):.2f} N")
    print(f"  fit  T[N] = {c0:.3f} {c1:+.5f} V {c2:+.6f} V^2   (V in m/s, valid 0-{V[ok][-1]:.0f} m/s, max error {err:.2f} N)")
    if plane:
        print(f"  airframe '{plane.name}': max takeoff mass {m_tow:.2f} kg, payload {best['payload']:.2f} kg")
    print("Wrote " + "\n      ".join(files))


if __name__ == "__main__":
    main()
