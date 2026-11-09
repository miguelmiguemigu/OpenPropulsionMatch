"""Model checks for propcalc v2. Plain asserts, no test framework needed.

    cd propcalc
    python3 -m tests.test_model
"""
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import propcalc as pc  # noqa: E402

DATA = os.path.join(ROOT, "data_2")
REF = ("HW 2826SL 1100kV", "APC 12x6E", "Skystars Jupiter Single 80A ESC", "GNB 930 120C")


def _lib():
    return pc.load_library(DATA)


def _system(lib, names=REF):
    cal = pc.Calibration(lib, lib["cfg"]["model"]["air_density_kg_m3"])
    f = lambda lst, n: next(x for x in lst if x.name == n)
    return pc.System(f(lib["motors"], names[0]), f(lib["props"], names[1]), f(lib["escs"], names[2]),
                     f(lib["batteries"], names[3]), cal, lib["cfg"])


def test_bench_rows_close():
    """Every bench row with a supply voltage is reproduced: RPM within 3 %, thrust within 5 %. In-sample (R_drive,
    torque and thrust factors are fitted on these rows), so this only shows the fit closes."""
    lib = _lib()
    assert pc.bench_report(lib, verbose=False), "bench rows out of tolerance - run propcalc.py --bench-check"


def test_bench_leave_one_out():
    """Out of sample for R_drive: fit it without one bench row, then predict that row (RPM within 3 %, thrust within
    5 %). The torque/thrust factors still use every row; a team thrust-stand row on the 2826SL is the real test."""
    lib = _lib()
    rows = [r for r in lib["calibration"] if r.get("supply_v")]
    assert rows
    for held in rows:
        cal_rows = [dict(r, supply_v="") if (r["motor"], r["rpm"]) == (held["motor"], held["rpm"]) else r
                    for r in lib["calibration"]]
        cal = pc.Calibration(dict(lib, calibration=cal_rows), 1.225)
        for mo, pr, _, _, rpm_m, rpm_p, gf_m, gf_p in cal.bench_check(lib, lib["cfg"]):
            if mo == held["motor"] and pr == held["prop"]:
                assert abs(rpm_p / rpm_m - 1) < 0.03, (mo, pr, rpm_m, rpm_p)
                assert abs(gf_p / gf_m - 1) < 0.05, (mo, pr, gf_m, gf_p)


def test_bench_report_fails_without_rows():
    lib = _lib()
    lib["calibration"] = [dict(r, supply_v="") for r in lib["calibration"]]
    assert not pc.bench_report(lib, verbose=False), "a bench check with no rows must not pass"


def test_only_owned_keeps_calibration():
    """--only-owned searches a few parts, but the calibration must still come from the whole library."""
    lib = _lib(); full = _system(lib)
    lib["all_motors"], lib["all_props"] = list(lib["motors"]), list(lib["props"])
    lib["motors"] = [m for m in lib["motors"] if m.name == REF[0]]
    lib["props"] = [p for p in lib["props"] if p.name == REF[1]]
    s = _system(lib)
    assert abs(s.r_drive - full.r_drive) < 1e-12 and s.r_drive > 0.01, s.r_drive
    assert abs(s.point(0.0, 1.0)["gf"] - full.point(0.0, 1.0)["gf"]) < 1e-6


def test_energy_balance():
    """Solver convergence: battery power = shaft power + copper/ESC/drive losses + no-load loss + pack/lead losses
    (within 2 %). This is an identity of the circuit equations, so it checks the solver, not the physics."""
    lib = _lib(); s = _system(lib)
    for v in (0.0, 10.0):
        for th in (0.5, 1.0):
            p = s.point(v, th, s.voc_fresh, cap=None)
            Im, Ib = p["I_motor"], p["I_batt"]
            p_batt = s.voc_fresh * Ib                          # chemical power out of the pack
            losses = Ib ** 2 * s.r_line + Im ** 2 * s.r_motor  # pack/leads + winding/ESC/drive
            noload = s.m.i0 * p["rpm"] / s.m.kv                # I0 x back-EMF
            p_shaft = (Im - s.m.i0) * s.m.kt * p["rpm"] * 2 * math.pi / 60
            err = abs(p_batt - (p_shaft + losses + noload)) / p_batt
            assert err < 0.02, f"energy balance off by {err:.1%} at v={v}, throttle={th}"


def test_thrust_monotonic_in_throttle():
    lib = _lib(); s = _system(lib)
    T = [s.point(8.0, th, s.voc_fresh, cap=None)["T"] for th in (0.3, 0.5, 0.7, 0.9, 1.0)]
    assert all(b > a for a, b in zip(T, T[1:])), T


def test_voltage_falls_with_charge_used():
    lib = _lib(); s = _system(lib)
    v = [s.voc_at(u) for u in (0, 0.2, 0.5, 0.8, 1.0)]
    assert all(b < a for a, b in zip(v, v[1:])), v
    assert 12.0 < v[0] < 12.7


def test_campaign_respects_rules():
    """Campaign: at most 3 flights, at most 3 articles per flight, each type at most 4 times, N >= 1."""
    lib = _lib(); s = _system(lib)
    plane = pc.Plane(os.path.join(DATA, "plane.json"))
    ev = pc.Evaluator(s, plane, lib["cfg"])
    res = ev.campaign()
    assert res is not None
    total, flights = res
    assert len(flights) <= plane.flights_scored
    use = {}
    for f in flights:
        assert 1 <= len(f["arts"]) <= plane.mounts
        for a in f["arts"]:
            use[a] = use.get(a, 0) + 1
    assert all(n <= plane.max_uses for n in use.values()), use
    assert abs(total - sum(f["score"] for f in flights)) < 1e-6
    for f in flights:  # score = 1.25 x payload lb x N
        assert abs(f["score"] - 1.25 * f["payload"] / pc.LB * len(f["arts"])) < 1e-6


def test_legacy_voltage_path():
    """Without a voltage curve: fresh voltage at the start, cruise voltage from half used on (v1 behaviour)."""
    lib = _lib()
    lib["cfg"]["model"].update(voc_curve_used_fraction=[], voc_curve_cell_v=[], voc_per_cell_fresh=4.0,
                               voc_per_cell_cruise=3.8)
    s = _system(lib)
    assert abs(s.voc_at(0.0) - 12.0) < 1e-9 and abs(s.voc_at(0.5) - 11.4) < 1e-9 and abs(s.voc_at(0.9) - 11.4) < 1e-9
    assert 11.4 < s.voc_at(0.25) < 12.0
    assert abs(s.voc_cruise - 11.4) < 1e-9


def test_v1_config_file_keeps_fixed_voltages():
    """A config.json that sets voc_per_cell_* but has no curve keys must not silently get the v2 curve."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "config.json"), "w") as f:
            json.dump({"model": {"voc_per_cell_fresh": 4.0, "voc_per_cell_cruise": 3.8}}, f)
        cfg = pc.load_config(d)
    assert pc.voc_curve(cfg["model"]) is None
    assert pc.voc_curve(pc.load_config(DATA)["model"]) is not None  # the v2 data folder keeps its curve


def test_campaign_with_fewer_scored_flights():
    """If the use cap rules out 3 scored flights, the campaign still scores 1 or 2."""
    lib = _lib(); s = _system(lib)
    d = json.load(open(os.path.join(DATA, "plane.json"), encoding="utf-8"))
    art = d["drag_articles"]
    art["types"] = {"Banana": art["types"]["Banana"]}
    art["reference_set"] = ["Banana"]; art["max_scored_uses_per_type"] = 2
    total, flights = pc.Evaluator(s, pc.Plane(d), lib["cfg"]).campaign()
    assert total > 0 and 1 <= len(flights) <= 2
    assert sum(len(f["arts"]) for f in flights) <= 2


def test_soc_grid_covers_usable_capacity():
    lib = _lib(); lib["cfg"]["model"]["usable_capacity_fraction"] = 1.0
    ev = pc.Evaluator(_system(lib), pc.Plane(os.path.join(DATA, "plane.json")), lib["cfg"])
    assert ev.SOC_GRID[-1] == 1.0
    assert ev._T(5.0, 0.95 * ev.mah) < ev._T(5.0, 0.8 * ev.mah)


def test_plane_validation():
    base = json.load(open(os.path.join(DATA, "plane.json"), encoding="utf-8"))
    bad = []
    d = json.loads(json.dumps(base)); d["CL_rotate"] = 0; bad.append(d)
    d = json.loads(json.dumps(base)); d["drag_articles"]["reference_set"] = ["Typo"]; bad.append(d)
    d = json.loads(json.dumps(base)); d["drag_articles"]["reference_set"] = ["Waffle"] * 4; bad.append(d)
    d = json.loads(json.dumps(base)); del d["drag_articles"]["reference_set"]
    for k in ("count", "mass_kg_each", "CdA_m2_each"):
        d["drag_articles"].pop(k, None)
    bad.append(d)
    for d in bad:
        try:
            pc.Plane(d)
        except ValueError:
            continue
        raise AssertionError("Plane accepted an invalid plane.json")


def test_r_drive_setting_parsing():
    p = pc.Calibration.parse_r_drive
    assert p("auto") is None and p("Auto ") is None and p("") is None and p(None) is None
    assert p(20) == 20.0 and p("20") == 20.0 and p("20 mOhm") == 20.0 and p("12.5") == 12.5
    try:
        p("twenty")
    except ValueError:
        pass
    else:
        raise AssertionError("bad r_drive_mohm accepted")


def test_pack_r_scale_applies_to_measured_dc_resistance():
    lib = _lib()
    b = next(x for x in lib["batteries"] if x.name == REF[3]); b.r_dc = 0.030
    r1 = _system(lib).r_pack
    lib["cfg"]["model"]["pack_r_scale"] = 1.5
    assert abs(_system(lib).r_pack - 1.5 * r1) < 1e-12 and abs(r1 - 0.030) < 1e-12


def test_liftoff_never_below_rotate_speed():
    lib = _lib(); s = _system(lib)
    d = json.load(open(os.path.join(DATA, "plane.json"), encoding="utf-8"))
    pl = pc.Plane(d); m = 2.5
    v_rot = math.sqrt(2 * m * pc.G / (1.225 * pl.S * pl.CL_rotate))
    assert pl.vliftoff(m, 1.225) >= v_rot - 1e-9


def test_golden_reference():
    """Reference combination (the team's pick) on a fresh pack. Update these numbers on purpose only, and say
    why in the commit message: they are the before/after anchor for the v2 model."""
    lib = _lib(); s = _system(lib)
    p = s.point(0.0, 1.0)
    assert abs(p["gf"] - 2394) < 25, p["gf"]
    assert abs(p["I_batt"] - 55.8) < 1.0, p["I_batt"]


def main():
    tests = [(k, v) for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn(); print(f"  ok    {name}")
        except AssertionError as e:
            failed += 1; print(f"  FAIL  {name}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
