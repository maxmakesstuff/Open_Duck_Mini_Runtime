"""Off-robot tests for rest_pose (run: python3 test_rest_pose.py)."""
from rest_pose import RestPose

DT = 0.02


def run(rp, seconds, stick=False, tilt=12.0, rate=0.05, t0=0.0, allowed=True):
    t, out = t0, None
    for _ in range(int(seconds / DT)):
        out = rp.update(stick, tilt, rate, t, allowed)
        t += DT
    return out, t


def test_off_by_default_never_rests():
    rp = RestPose()
    assert run(rp, 10.0)[0] is False


def test_rests_only_after_a_calm_idle_moment():
    rp = RestPose(enabled=True)
    assert run(rp, 1.0)[0] is False
    assert run(rp, 1.0, t0=1.0)[0] is True


def test_a_crooked_but_calm_stance_still_counts_as_calm():
    rp = RestPose(enabled=True)
    assert run(rp, 2.0, tilt=14.0)[0] is True


def test_the_stick_wakes_it_at_once_and_it_needs_a_new_calm_period():
    rp = RestPose(enabled=True)
    _, t = run(rp, 2.0)
    assert rp.update(True, 1.0, 0.05, t) is False
    assert run(rp, 1.0, t0=t + DT)[0] is False
    assert run(rp, 1.0, t0=t + 1.0)[0] is True


def test_a_push_wakes_it_by_speed_or_by_growing_lean():
    rp = RestPose(enabled=True)
    _, t = run(rp, 2.0)
    _, t = run(rp, 1.0, tilt=1.0, t0=t)              # faded to the stance: lean 1 deg
    assert rp.resting
    assert rp.update(False, 1.5, 2.0, t) is False, "a fast tip must wake it"
    rp = RestPose(enabled=True)
    _, t = run(rp, 2.0)
    _, t = run(rp, 1.0, tilt=1.0, t0=t)
    assert rp.update(False, 5.0, 0.1, t) is True, "a small sway is fine"
    assert rp.update(False, 8.0, 0.1, t + DT) is False, "a slow lean of 7 deg wakes it"


def test_fading_from_the_crooked_pose_to_the_stance_does_not_wake_it():
    rp = RestPose(enabled=True)
    _, t = run(rp, 2.0, tilt=12.6)
    tilt = 12.6
    while tilt > 0.5:                                # lean shrinks at about 10 deg/s
        tilt -= 0.2
        assert rp.update(False, tilt, 0.17, t) is True, tilt
        t += DT


def test_restless_duck_does_not_rest():
    rp = RestPose(enabled=True)
    assert run(rp, 5.0, rate=0.6)[0] is False


def test_not_allowed_during_other_activities_and_bad_numbers():
    rp = RestPose(enabled=True)
    assert run(rp, 5.0, allowed=False)[0] is False
    _, t = run(rp, 2.0)
    assert rp.resting
    assert rp.update(False, float("nan"), 0.0, t) is False
    assert rp.update(False, None, 0.0, t) is False
    assert rp.public() == {"enabled": True, "resting": False}


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); n += 1
    print(f"OK - {n} rest_pose tests passed")
