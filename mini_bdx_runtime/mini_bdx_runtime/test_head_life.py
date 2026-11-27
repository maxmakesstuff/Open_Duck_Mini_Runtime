"""
Standalone tests for head_life (look-around offsets).

Run from this directory:  python3 test_head_life.py
"""
import math
import random

import head_life as hl

DT = 0.02


def _run(h, seconds, t0=0.0, allowed=True):
    """Step the module; returns the list of outputs (one per tick)."""
    out, n = [], int(round(seconds / DT))
    for i in range(n):
        out.append(h.update(t0 + i * DT, DT, allowed=allowed))
    return out


def test_off_by_default_and_silent():
    h = hl.HeadLife()
    assert h.cfg["look"] is False
    for o in _run(h, 5.0):
        assert o == [0.0, 0.0, 0.0, 0.0]
    assert h.public()["active"] is False


def test_look_around_moves_the_head_never_the_neck_and_stays_in_its_envelope():
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


def test_not_allowed_glides_everything_back_to_zero():
    h = hl.HeadLife({"look": True}, rng=random.Random(1))
    _run(h, 30.0)
    before = h.out[:]
    assert any(abs(v) > 0.02 for v in before)
    out = _run(h, 4.0, t0=30.0, allowed=False)   # puppeteer takes the head
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
    c = hl.clean_config({"look": 1, "look_scale": float("nan"), "nonsense": 3,
                         "bob": True, "bob_deg": 5})          # keys of the removed head bob
    assert c == {"look": True, "look_scale": 1.0}
    assert hl.clean_config({"look_scale": 9})["look_scale"] == 1.5
    assert hl.clean_config(None) == hl.DEFAULTS and hl.clean_config("x") == hl.DEFAULTS


def test_bad_inputs_never_raise_or_jump():
    h = hl.HeadLife({"look": True}, rng=random.Random(2))
    for dt in (None, float("nan"), -1.0, 5.0, "x"):
        prev = h.out[:]
        out = h.update(1.0, dt)
        assert all(v == v for v in out)
        assert all(abs(a - b) <= hl.MAX_RATE[i] * hl.MAX_DT + 1e-9 for i, (a, b) in enumerate(zip(out, prev)))
        assert all(abs(v) <= hl.MAX_OFFSET[i] + 1e-9 for i, v in enumerate(out))


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
    print(f"OK - {n} head life tests")
