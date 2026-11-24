"""Off-robot tests for fall_reflex.py.  Run:  python3 test_fall_reflex.py

The physics check uses a rigid inverted pendulum pivoting at the feet. An
accelerometer riding on a freely tipping pendulum feels NO sideways force (the
tangential acceleration cancels gravity's tangential part exactly), so an
accelerometer-only tilt reads "upright" all the way down. That is the reason the
estimator leans on the gyro, and the first test pins it."""
import math
import random

import fall_reflex as fr

G = 9.81
DT = 0.02
L = 0.25          # IMU height above the feet (m), about right for the Mini


def pendulum(theta0_deg, rate0=0.0, t_end=3.0, noise=0.0, seed=1):
    """Yield (t, theta_deg, gyro, accel) for a duck tipping forward about its feet."""
    rng = random.Random(seed)
    th, w, t = math.radians(theta0_deg), rate0, 0.0
    while t < t_end and th < math.radians(88):
        acc_dd = (G / L) * math.sin(th)
        gyro = (0.0, w, 0.0)
        accel = (rng.gauss(0, noise), rng.gauss(0, noise),
                 G * math.cos(th) - L * w * w + rng.gauss(0, noise))
        yield t, math.degrees(th), gyro, accel
        w += acc_dd * DT
        th += w * DT
        t += DT


def _run_fall(reflex, est, **kw):
    """Return (trigger_time, tilt_at_trigger, time_at_75deg)."""
    trig = None
    t75 = None
    for t, th, gyro, accel in pendulum(**kw):
        est.update(gyro, accel, DT)
        ev = reflex.update(est.tilt_deg, est.rate_deg_s, abs(gyro[1]), t)
        if ev in ("trigger", "trigger_soft", "would_trigger") and trig is None:
            trig = (t, th)
        if th >= 75 and t75 is None:
            t75 = t
    return trig, t75


def test_accelerometer_alone_cannot_see_a_free_fall():
    est = fr.TiltEstimator(use_gyro=False)
    worst = 0.0
    for t, th, gyro, accel in pendulum(theta0_deg=4, rate0=0.3):
        est.update(gyro, accel, DT)
        worst = max(worst, est.tilt_deg)
    assert worst < 5.0, f"accel-only estimate moved to {worst:.1f} deg"


def test_fused_estimate_tracks_the_real_tilt_through_a_fall():
    est = fr.TiltEstimator()
    for t, th, gyro, accel in pendulum(theta0_deg=4, rate0=0.3):
        est.update(gyro, accel, DT)
        if th > 15:
            assert abs(est.tilt_deg - th) < 6.0, (th, est.tilt_deg)


def test_trigger_comes_well_before_the_floor():
    for theta0, rate0 in ((3, 0.2), (6, 0.0), (5, 1.0), (10, 0.5)):
        reflex = fr.FallReflex({"mode": "active"})
        trig, t75 = _run_fall(reflex, fr.TiltEstimator(), theta0_deg=theta0, rate0=rate0)
        assert trig is not None, f"no trigger for start {theta0} deg"
        lead = t75 - trig[0]
        assert lead >= 0.12, f"start {theta0} deg: only {lead:.2f} s before the floor"
        assert trig[1] < 45, f"triggered late, at {trig[1]:.0f} deg"


def test_noisy_accelerometer_does_not_break_detection():
    reflex = fr.FallReflex({"mode": "active"})
    trig, t75 = _run_fall(reflex, fr.TiltEstimator(), theta0_deg=5, rate0=0.4, noise=1.5)
    assert trig is not None and t75 - trig[0] >= 0.1


def test_ordinary_walking_wobble_does_not_trigger():
    """+-9 deg sway at 2 Hz in both axes, with foot-strike spikes on the accel."""
    est, reflex, rng = fr.TiltEstimator(), fr.FallReflex({"mode": "active"}), random.Random(3)
    t = 0.0
    while t < 30.0:
        a = math.radians(9) * math.sin(2 * math.pi * 2 * t)
        b = math.radians(6) * math.sin(2 * math.pi * 2 * t + 1.0)
        wa = math.radians(9) * 2 * math.pi * 2 * math.cos(2 * math.pi * 2 * t)
        wb = math.radians(6) * 2 * math.pi * 2 * math.cos(2 * math.pi * 2 * t + 1.0)
        spike = 6.0 if rng.random() < 0.05 else 0.0
        accel = (-G * math.sin(a) + rng.gauss(0, 0.6), G * math.sin(b) + rng.gauss(0, 0.6),
                 G * math.cos(a) * math.cos(b) + spike)
        est.update((wb, wa, 0.0), accel, DT)
        ev = reflex.update(est.tilt_deg, est.rate_deg_s, math.hypot(wa, wb), t)
        assert ev is None, f"false trigger at t={t:.2f} tilt={est.tilt_deg:.1f}"
        t += DT
    assert 5 < reflex.peak_tilt_deg < 20
    assert reflex.peak_predicted_deg >= reflex.peak_tilt_deg


def test_a_recovered_stumble_does_not_trigger():
    """Tips to 18 deg, then comes back: below min_tilt it must stay quiet."""
    est, reflex = fr.TiltEstimator(), fr.FallReflex({"mode": "active"})
    t = 0.0
    while t < 2.0:
        th = math.radians(18) * math.sin(math.pi * t / 2.0)
        w = math.radians(18) * (math.pi / 2.0) * math.cos(math.pi * t / 2.0)
        est.update((0.0, w, 0.0), (-G * math.sin(th), 0.0, G * math.cos(th)), DT)
        assert reflex.update(est.tilt_deg, est.rate_deg_s, abs(w), t) is None
        t += DT


def test_hard_limit_triggers_even_when_tipping_slowly():
    reflex = fr.FallReflex({"mode": "active", "tuck_s": 0.15})
    assert reflex.update(39.0, 0.0, 0.0, 0.0) is None
    assert reflex.update(41.0, 0.0, 0.0, 0.02) == "trigger"
    assert reflex.phase == "tuck"


def test_observe_mode_reports_once_and_changes_nothing():
    reflex = fr.FallReflex({"mode": "observe"})
    events = [reflex.update(60.0, 200.0, 3.0, i * DT) for i in range(20)]
    assert events.count("would_trigger") == 1
    assert reflex.phase == "armed" and reflex.triggers == 1
    # upright again, then a second fall is a second event
    for i in range(5):
        reflex.update(2.0, 0.0, 0.0, 1 + i * DT)
    events = [reflex.update(60.0, 200.0, 3.0, 2 + i * DT) for i in range(5)]
    assert events.count("would_trigger") == 1 and reflex.triggers == 2


def test_off_mode_never_triggers():
    reflex = fr.FallReflex({"mode": "off"})
    assert all(reflex.update(80.0, 300.0, 5.0, i * DT) is None for i in range(50))
    assert reflex.triggers == 0


def test_disabled_ticks_do_not_trigger_or_count():
    reflex = fr.FallReflex({"mode": "active"})
    assert all(reflex.update(80.0, 300.0, 5.0, i * DT, enabled=False) is None
               for i in range(10))
    assert reflex.phase == "armed"


def test_phases_run_tuck_soft_down_in_order():
    reflex = fr.FallReflex({"mode": "active", "tuck_s": 0.1, "rest_s": 0.2,
                            "rest_rate": 0.35})
    t = 0.0
    assert reflex.update(50.0, 300.0, 5.0, t) == "trigger"
    seen = []
    for i in range(1, 200):
        t = i * DT
        gyro = 5.0 if t < 0.5 else 0.05          # tumbling, then at rest
        ev = reflex.update(80.0, 0.0, gyro, t)
        if ev:
            seen.append((ev, round(t, 2)))
    assert [e for e, _ in seen] == ["soft", "down"], seen
    assert abs(seen[0][1] - 0.1) < 0.03
    assert 0.65 <= seen[1][1] <= 0.75, "down = 0.2 s after the gyro went quiet at 0.5 s"
    assert reflex.phase == "down"
    assert reflex.update(80.0, 0.0, 0.0, 10.0) is None, "down waits for rearm()"
    reflex.rearm()
    assert reflex.phase == "armed"


def test_zero_tuck_time_goes_straight_to_soft():
    reflex = fr.FallReflex({"mode": "active", "tuck_s": 0.0})
    assert reflex.update(50.0, 300.0, 5.0, 0.0) == "trigger_soft"
    assert reflex.phase == "soft"


def test_never_resting_still_ends_in_down():
    reflex = fr.FallReflex({"mode": "active", "tuck_s": 0.0, "max_soft_s": 1.0})
    reflex.update(50.0, 300.0, 5.0, 0.0)
    events = [reflex.update(80.0, 0.0, 5.0, i * DT) for i in range(1, 80)]
    assert "down" in events


def test_config_is_clamped_and_junk_ignored():
    c = fr.clean_config({"mode": "bogus", "hard_deg": 500, "horizon_s": "x",
                         "soft_kp": float("nan"), "confirm_ticks": 2.6, "evil": 1})
    assert c["mode"] == "observe" and c["hard_deg"] == 90.0
    assert c["horizon_s"] == fr.DEFAULTS["horizon_s"]
    assert c["soft_kp"] == fr.DEFAULTS["soft_kp"]
    assert c["confirm_ticks"] == 3 and "evil" not in c
    assert fr.clean_config(None) == fr.DEFAULTS
    assert fr.clean_config("nope") == fr.DEFAULTS


def test_live_set_validates():
    reflex = fr.FallReflex()
    assert reflex.set("mode", "active") and reflex.mode == "active"
    assert not reflex.set("mode", "wild")
    assert reflex.set("hard_deg", 33) and reflex.cfg["hard_deg"] == 33.0
    assert not reflex.set("hard_deg", float("inf"))
    assert not reflex.set("nope", 1)


def test_a_single_gyro_spike_is_ignored_but_a_real_fast_tip_is_not():
    for spike in (25.0, -60.0, 400.0):
        est, reflex = fr.TiltEstimator(), fr.FallReflex({"mode": "active"})
        for i in range(200):
            w = spike if i == 100 else 0.0
            est.update((0.0, w, 0.0), (0.0, 0.0, G), DT)
            assert reflex.update(est.tilt_deg, est.rate_deg_s, abs(w), i * DT) is None, spike
        assert est.tilt_deg < 1.0, f"spike {spike} moved the estimate to {est.tilt_deg:.1f}"
    # a sustained jump (the duck really got knocked) is accepted one tick later
    est = fr.TiltEstimator()
    for i in range(10):
        est.update((0.0, 0.0, 0.0), (0.0, 0.0, G), DT)
    for i in range(5):
        est.update((0.0, 6.0, 0.0), (0.0, 0.0, G), DT)
    assert est.tilt_deg > 20.0, est.tilt_deg


def test_estimator_survives_bad_samples():
    est = fr.TiltEstimator()
    est.update((0, 0, 0), (0, 0, 0), DT)                 # the IMU's pre-first-sample zeros
    assert est.tilt_deg == 0.0
    est.update((0, 0, 0), (0, 0, G), DT)
    est.update((float("nan"), 0, 0), (0, 0, G), DT)
    est.update(None, None, DT)
    est.update((0, 0, 0), (0, 0, G), -1)
    assert abs(est.tilt_deg) < 1e-6


def test_estimator_reads_a_static_lean_from_gravity():
    est = fr.TiltEstimator()
    th = math.radians(30)
    for _ in range(400):
        est.update((0, 0, 0), (-G * math.sin(th), 0, G * math.cos(th)), DT)
    assert abs(est.tilt_deg - 30) < 0.5


def test_tuck_pose_bends_the_knees_and_stays_in_limits():
    from getup import JOINT_LO, JOINT_HI
    init = [0.002, 0.053, -0.63, 1.368, -0.784, 0, 0, 0, 0,
            -0.003, -0.065, 0.635, 1.379, -0.796]
    pose = fr.tuck_pose(init, JOINT_LO, JOINT_HI)
    assert pose[3] > init[3] and pose[12] > init[12], "knees bend further"
    assert pose[2] < init[2] and pose[11] > init[11], "hips follow, mirrored"
    assert pose[5:9] == [0.0] * 4
    assert all(lo - 1e-9 <= p <= hi + 1e-9 for p, lo, hi in zip(pose, JOINT_LO, JOINT_HI))
    # the foot stays flat: knee - |hip| + ankle is unchanged on both legs
    for hip, knee, ankle in ((2, 3, 4), (11, 12, 13)):
        before = init[knee] - abs(init[hip]) + init[ankle]
        after = pose[knee] - abs(pose[hip]) + pose[ankle]
        assert abs(before - after) < 1e-9


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} fall_reflex tests passed.")
