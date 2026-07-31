"""
Standalone tests for AntennaAnimator (pure -> off-robot).

Run:  python3 test_antenna_anim.py
"""
import random
from antenna_anim import AntennaAnimator, RAMP_S, DEADZONE


def _run_idle(a, n=240, dt=0.02, t0=0.0):
    """Sample n idle ticks, returning the list of (l, r)."""
    return [a.update(t0 + i * dt, 0.0, 0.0) for i in range(n)]


def test_manual_passthrough_overrides_animation():
    a = AntennaAnimator(enabled=True, rng=random.Random(1))
    assert a.update(0.0, manual_left=0.7, manual_right=0.3) == (0.7, 0.3)
    assert a.update(0.02, manual_left=2.0, manual_right=-2.0) == (1.0, -1.0)


def test_disabled_and_idle_rests_at_zero():
    a = AntennaAnimator(enabled=False, rng=random.Random(2))
    for l, r in _run_idle(a, n=50):
        assert l == 0.0 and r == 0.0


def test_enabled_idle_moves_within_range_and_is_lively():
    a = AntennaAnimator(enabled=True, rng=random.Random(3))
    outs = _run_idle(a, n=400)
    assert all(-1.0 <= l <= 1.0 and -1.0 <= r <= 1.0 for l, r in outs)
    peak = max(max(abs(l), abs(r)) for l, r in outs)
    assert peak > 0.2, "free animation should visibly move the ears"


def test_sync_true_moves_both_ears_together():
    a = AntennaAnimator(enabled=True, rng=random.Random(4), sync=True)
    outs = _run_idle(a, n=300)
    assert all(abs(l - r) < 1e-9 for l, r in outs), "sync=True -> both ears identical"


def test_sync_false_ears_are_independent():
    a = AntennaAnimator(enabled=True, rng=random.Random(5), sync=False)
    outs = _run_idle(a, n=300)
    # independent wander channels -> the two ears differ at least sometimes
    assert any(abs(l - r) > 0.05 for l, r in outs), "sync=False -> ears drift apart"


def test_set_sync_toggles_live():
    a = AntennaAnimator(enabled=True, rng=random.Random(6), sync=False)
    # warm up a little so the wanders have diverged
    _run_idle(a, n=60)
    a.set_sync(True)
    outs = [a.update(2.0 + i * 0.02, 0.0, 0.0) for i in range(120)]
    assert all(abs(l - r) < 1e-9 for l, r in outs)


def test_non_repeating_across_distant_windows():
    a = AntennaAnimator(enabled=True, rng=random.Random(7))
    early = _run_idle(a, n=100, t0=0.0)
    late = _run_idle(a, n=100, t0=200.0)   # much later
    # random-waypoint motion should not reproduce the same trajectory
    assert early[50] != late[50]


def test_idle_eases_in_after_manual_release():
    a = AntennaAnimator(enabled=True, rng=random.Random(8))
    a.update(10.0, manual_left=0.8)            # manual touch at t=10
    l0, r0 = a.update(10.0 + 1e-4, 0.0, 0.0)   # immediately after release
    assert abs(l0) < 0.05 and abs(r0) < 0.05, "no snap right after release"
    big = max(max(abs(l), abs(r))
              for l, r in (a.update(10.0 + RAMP_S + i * 0.02, 0.0, 0.0) for i in range(60)))
    assert big > 0.05, "idle should be present once the ramp completes"


def test_deadzone_treats_tiny_input_as_idle():
    a = AntennaAnimator(enabled=False, rng=random.Random(9))
    tiny = DEADZONE * 0.5
    assert a.update(0.0, manual_left=tiny, manual_right=-tiny) == (0.0, 0.0)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} antenna_anim tests passed.")
