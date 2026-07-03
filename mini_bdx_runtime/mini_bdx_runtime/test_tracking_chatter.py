"""
Standalone tests for TrackingChatter (no pytest; pure -> runs off-robot).

Run from this directory:  python3 test_tracking_chatter.py
"""
import random
from face_tracker import TrackingChatter


def _seeded(**kw):
    return TrackingChatter(rng=random.Random(1234), **kw)


def test_no_output_while_inactive():
    ch = _seeded()
    ch.reset(0.0)
    for t in range(0, 2000):
        out = ch.update(t * 0.02, active=False)
        assert out.play_sound is None and out.antenna is None


def test_fires_a_sound_within_the_window():
    ch = _seeded(sound_every=(4.0, 9.0))
    ch.reset(0.0)
    sounds = []
    t = 0.0
    while t < 30.0:
        out = ch.update(t, active=True)
        if out.play_sound:
            sounds.append((round(t, 2), out.play_sound))
        t += 0.02
    assert sounds, "should fire at least one sound in 30s"
    # first sound lands within the max interval
    assert sounds[0][0] <= 9.0 + 0.05
    # spacing between consecutive sounds stays within [min, max]
    for (t0, _), (t1, _) in zip(sounds, sounds[1:]):
        gap = t1 - t0
        assert 4.0 - 0.05 <= gap <= 9.0 + 0.05, f"gap {gap} out of range"
    # only sounds from the pool
    for _, name in sounds:
        assert name in ch.sound_pool


def test_wiggle_is_bounded_and_periodic():
    ch = _seeded(wiggle_every=(6.0, 12.0), wiggle_amp=0.5, wiggle_s=0.8)
    ch.reset(0.0)
    wiggling_ticks = 0
    t = 0.0
    while t < 40.0:
        out = ch.update(t, active=True)
        if out.antenna is not None:
            wiggling_ticks += 1
            assert -0.5 - 1e-9 <= out.antenna <= 0.5 + 1e-9
        t += 0.02
    assert wiggling_ticks > 0, "should wiggle at least once in 40s"


def test_reset_rearms_so_reacquire_does_not_instantly_fire():
    ch = _seeded()
    ch.reset(0.0)
    # run active a while (consumes the first scheduled events)
    t = 0.0
    while t < 20.0:
        ch.update(t, active=True)
        t += 0.02
    # face lost -> inactive; then re-acquired at t=100. Should not fire immediately.
    ch.update(100.0, active=False)
    out = ch.update(100.0, active=True)
    assert out.play_sound is None, "must not fire a sound on the first re-acquire tick"
    # next sound is at least the min interval away
    assert ch._next_sound >= 100.0 + 4.0 - 1e-6


def test_empty_pool_never_crashes():
    ch = TrackingChatter(sound_pool=[], rng=random.Random(1))
    ch.reset(0.0)
    t = 0.0
    while t < 20.0:
        out = ch.update(t, active=True)
        assert out.play_sound is None
        t += 0.02


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} tracking_chatter tests passed.")
