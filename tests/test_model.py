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
    """Every bench row with a supply voltage is reproduced: RPM within 3 %, thrust within 5 %."""
    lib = _lib()
    assert pc.bench_report(lib, verbose=False), "bench rows out of tolerance - run propcalc.py --bench-check"


def test_energy_balance():
    """Battery power = shaft power + copper/ESC/drive losses + no-load loss + pack/lead losses (within 2 %)."""
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
