"""
Standalone tests for AntennaAnimator (pure -> off-robot).

Run:  python3 test_antenna_anim.py
"""
import random
from antenna_anim import AntennaAnimator, RAMP_S, DEADZONE


def test_manual_passthrough_overrides_animation():
    a = AntennaAnimator(enabled=True, rng=random.Random(1))
    # a clear manual push passes through verbatim (clamped)
    assert a.update(0.0, manual_left=0.7, manual_right=0.3) == (0.7, 0.3)
    assert a.update(0.1, manual_left=2.0, manual_right=-2.0) == (1.0, -1.0)


def test_disabled_and_idle_rests_at_zero():
    a = AntennaAnimator(enabled=False, rng=None)
    for t in (0.0, 0.5, 1.0, 2.0):
        assert a.update(t, 0.0, 0.0) == (0.0, 0.0)


def test_enabled_idle_moves_within_range():
    a = AntennaAnimator(enabled=True, rng=None)   # rng None -> pure sway, no twitch
    seen_nonzero = False
    for i in range(200):
        t = i * 0.05
        l, r = a.update(t, 0.0, 0.0)
        assert -1.0 <= l <= 1.0 and -1.0 <= r <= 1.0
        if abs(l) > 0.01 or abs(r) > 0.01:
            seen_nonzero = True
    assert seen_nonzero, "free animation should actually move the ears"


def test_ears_are_asymmetric():
    a = AntennaAnimator(enabled=True, rng=None)
    # at least one sampled tick has meaningfully different L and R (organic, not mirrored)
    assert any(abs(a.update(i * 0.13, 0, 0)[0] - a.update(i * 0.13 + 1e-6, 0, 0)[1]) > 0.02
               for i in range(1, 60))


def test_idle_eases_in_after_manual_release():
    a = AntennaAnimator(enabled=True, rng=None)
    a.update(10.0, manual_left=0.8)          # manual touch at t=10
    # immediately after release the idle output is ramped from ~0
    l0, r0 = a.update(10.0 + 1e-4, 0.0, 0.0)
    assert abs(l0) < 0.05 and abs(r0) < 0.05, "no snap right after release"
    # after RAMP_S it has eased in (compare magnitude envelope, not exact phase)
    big = max(abs(v) for _ in range(30)
              for v in a.update(10.0 + RAMP_S + _ * 0.02, 0.0, 0.0))
    assert big > 0.05, "idle should be present once the ramp completes"


def test_deadzone_treats_tiny_input_as_idle():
    a = AntennaAnimator(enabled=False, rng=None)
    tiny = DEADZONE * 0.5
    assert a.update(0.0, manual_left=tiny, manual_right=-tiny) == (0.0, 0.0)


def test_twitch_fires_with_rng():
    # deterministic rng -> a twitch happens within the first window and adds motion
    a = AntennaAnimator(enabled=True, rng=random.Random(0), twitch_every=(0.2, 0.2))
    outs = [a.update(i * 0.02, 0, 0) for i in range(200)]  # 4 s
    peak = max(max(abs(l), abs(r)) for l, r in outs)
    assert peak > 0.3, "a twitch should produce a visible peak above the base sway"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} antenna_anim tests passed.")
