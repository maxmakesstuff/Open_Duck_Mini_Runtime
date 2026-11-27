"""
Standalone tests for head_life (look-around + head bob offsets).

Run from this directory:  python3 test_head_life.py
"""
import math
import random

import head_life as hl

DT = 0.02


def _run(h, seconds, t0=0.0, stepping=False, allowed=True, phase_rate=2 * math.pi / 0.54):
    """Step the module; returns the list of outputs (one per tick)."""
    out, n = [], int(round(seconds / DT))
    for i in range(n):
        t = t0 + i * DT
        out.append(h.update(t, DT, gait_phase=t * phase_rate, stepping=stepping, allowed=allowed))
    return out


def test_off_by_default_and_silent():
    h = hl.HeadLife()
    assert h.cfg["look"] is False and h.cfg["bob"] is False
    for o in _run(h, 5.0, stepping=True):
        assert o == [0.0, 0.0, 0.0, 0.0]
    assert h.public()["active"] is False


def test_look_around_moves_the_head_but_not_the_neck_and_stays_in_its_envelope():
    h = hl.HeadLife({"look": True}, rng=random.Random(3))
    out = _run(h, 120.0)
    assert max(abs(o[2]) for o in out) > 0.15                  # it does look around
    assert all(o[0] == 0.0 for o in out)                       # neck untouched
    assert max(abs(o[2]) for o in out) <= hl.LOOK_YAW + 1e-9
    assert all(hl.LOOK_PITCH[0] - 1e-9 <= o[1] <= hl.LOOK_PITCH[1] + 1e-9 for o in out)
    assert max(abs(o[3]) for o in out) <= hl.LOOK_ROLL + 1e-9
    # it also returns to straight ahead now and then
    assert any(abs(o[2]) < 0.02 and abs(o[1]) < 0.02 for o in out[500:])


def test_look_around_is_slow_and_smooth():
    h = hl.HeadLife({"look": True, "look_scale": 1.5}, rng=random.Random(7))
    out = _run(h, 120.0)
    for i in (1, 2, 3):
        vel = [(b[i] - a[i]) / DT for a, b in zip(out, out[1:])]
        assert max(abs(v) for v in vel) <= hl.MAX_RATE[i] + 1e-6
        acc = [(b - a) / DT for a, b in zip(vel, vel[1:])]
        assert max(abs(x) for x in acc) < 8.0                  # no step in speed (rad/s^2)
    assert max(abs(o[2]) for o in out) <= hl.MAX_OFFSET[2] + 1e-9


def test_bob_runs_only_while_stepping_twice_per_gait_cycle():
    h = hl.HeadLife({"bob": True, "bob_deg": 3.0})
    assert all(o[0] == 0.0 for o in _run(h, 2.0, stepping=False))
    out = _run(h, 5.4, t0=2.0, stepping=True)                  # ten gait cycles
    neck = [o[0] for o in out[100:]]                           # after the fade-in
    assert abs(max(neck) - math.radians(3.0)) < 0.004 and abs(min(neck) + math.radians(3.0)) < 0.004
    ups = sum(1 for a, b in zip(neck, neck[1:]) if a < 0 <= b)  # upward zero crossings
    cycles = (len(neck) * DT) / 0.54
    assert abs(ups - 2 * cycles) <= 1.5                         # two bobs per cycle
    assert all(o[1] == o[2] == o[3] == 0.0 for o in out)        # only the neck


def test_bob_fades_in_and_out_instead_of_snapping():
    h = hl.HeadLife({"bob": True, "bob_deg": 10.0})
    out = _run(h, 1.0, stepping=True)
    assert max(abs(o[0]) for o in out[:5]) < 0.25 * math.radians(10.0) + 1e-9   # 0.1 s in: a quarter at most
    tail = _run(h, 1.0, t0=1.0, stepping=False)
    assert abs(tail[-1][0]) < 1e-6                              # gone after the fade
    vel = [(b[0] - a[0]) / DT for a, b in zip(out + tail, (out + tail)[1:])]
    assert max(abs(v) for v in vel) <= hl.MAX_RATE[0] + 1e-6


def test_not_allowed_glides_everything_back_to_zero():
    h = hl.HeadLife({"look": True, "bob": True}, rng=random.Random(1))
    _run(h, 30.0, stepping=True)
    before = h.out[:]
    assert any(abs(v) > 0.02 for v in before)
    out = _run(h, 4.0, t0=30.0, stepping=True, allowed=False)   # puppeteer takes the head
    first = out[0]
    assert all(abs(a - b) <= hl.MAX_RATE[i] * DT + 1e-9 for i, (a, b) in enumerate(zip(first, before)))
    assert all(abs(v) < 1e-3 for v in out[-1])
    assert h.public()["active"] is False


def test_switching_off_mid_glance_also_glides_back():
    h = hl.HeadLife({"look": True}, rng=random.Random(5))
    _run(h, 20.0)
    h.set({"look": False})
    out = _run(h, 4.0, t0=20.0)
    assert all(abs(v) < 1e-3 for v in out[-1])


def test_config_is_clamped_and_junk_ignored():
    c = hl.clean_config({"look": 1, "bob": "yes", "bob_deg": 99, "bob_phase_deg": -5,
                         "look_scale": float("nan"), "nonsense": 3})
    assert c["look"] is True and c["bob"] is True
    assert c["bob_deg"] == 10.0 and c["bob_phase_deg"] == 0.0 and c["look_scale"] == 1.0
    assert "nonsense" not in c
    assert hl.clean_config(None) == hl.DEFAULTS and hl.clean_config("x") == hl.DEFAULTS


def test_bad_inputs_never_raise_or_jump():
    h = hl.HeadLife({"look": True, "bob": True, "bob_deg": 10}, rng=random.Random(2))
    for dt, ph in ((None, 0.0), (float("nan"), 1.0), (-1.0, 2.0), (5.0, float("nan")), ("x", "y")):
        prev = h.out[:]
        out = h.update(1.0, dt, gait_phase=ph, stepping=True)
        assert all(v == v for v in out)
        assert all(abs(a - b) <= hl.MAX_RATE[i] * hl.MAX_DT + 1e-9 for i, (a, b) in enumerate(zip(out, prev)))
        assert all(abs(v) <= hl.MAX_OFFSET[i] + 1e-9 for i, v in enumerate(out))


def test_bob_timing_setting_shifts_the_bob():
    a = hl.HeadLife({"bob": True}); b = hl.HeadLife({"bob": True, "bob_phase_deg": 180.0})
    oa = _run(a, 3.0, stepping=True); ob = _run(b, 3.0, stepping=True)
    assert all(abs(x[0] + y[0]) < 1e-6 for x, y in zip(oa[60:], ob[60:]))   # opposite


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
    print(f"OK - {n} head life tests")
