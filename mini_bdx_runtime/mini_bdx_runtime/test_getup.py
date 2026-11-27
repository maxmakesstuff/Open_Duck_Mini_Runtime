"""Off-robot tests for getup.py.  Run:  python3 test_getup.py"""
import numpy as np

import getup as gu

HOME = np.array([0.002, 0.053, -0.63, 1.368, -0.784, 0, 0, 0, 0,
                 -0.003, -0.065, 0.635, 1.379, -0.796])
DT = 0.02
UP = np.array([0.0, 0.0, 9.81])
FLAT = np.array([9.81, 0.0, 0.0])          # lying on its front/back


class Policy:
    """Returns a fixed action; remembers what it was shown."""

    def __init__(self, action=None):
        self.action = np.zeros(14) if action is None else np.asarray(action, float)
        self.obs = []

    def infer(self, obs):
        self.obs.append(np.array(obs))
        return self.action


def _ctl(policy=None, **cfg):
    return gu.GetUpController(policy or Policy(), HOME, {"enabled": True, **cfg}, 50)


def _steps(ctl, n, pos, acc=UP, t0=0.0, vel=None):
    out = []
    for i in range(n):
        out.append(ctl.step(pos, np.zeros(14) if vel is None else vel,
                            np.zeros(3), acc, [1.0, 1.0], t0 + (i + 1) * DT).copy())
    return out


def test_observation_has_the_trained_layout():
    pol = Policy(np.full(14, 0.1))
    ctl = _ctl(pol)
    pos = HOME + 0.2
    ctl.start(pos, 0.0)
    ctl.step(pos, np.full(14, 2.0), np.array([0.1, 0.2, 0.3]), FLAT, [1.0, 0.0], DT)
    obs = pol.obs[0]
    assert obs.shape == (92,) and obs.dtype == np.float32
    assert np.allclose(obs[0:3], [0.1, 0.2, 0.3])
    assert np.allclose(obs[3:6], FLAT, atol=1e-5)
    assert np.allclose(obs[6:20], 0.2, atol=1e-6), "joint pos relative to home"
    assert np.allclose(obs[20:34], 0.1), "velocity x 0.05"
    assert np.allclose(obs[34:76], 0.0), "fresh action history"
    assert np.allclose(obs[76:90], np.clip(pos, gu.JOINT_LO, gu.JOINT_HI), atol=1e-6)
    assert np.allclose(obs[90:92], [1.0, 0.0])
    ctl.step(pos, np.zeros(14), np.zeros(3), FLAT, [1, 1], 2 * DT)
    assert np.allclose(pol.obs[1][34:48], 0.1), "last action is fed back"
    assert np.allclose(pol.obs[1][48:76], 0.0)


def test_starts_from_the_current_joints_without_a_jump():
    pos = HOME.copy()
    pos[3] = 0.2                                # a knee far from home
    ctl = _ctl(Policy(np.zeros(14)))
    ctl.start(pos, 0.0)
    assert np.allclose(ctl.targets, pos)
    first = _steps(ctl, 1, pos, acc=FLAT)[0]
    assert np.max(np.abs(first - pos)) <= 5.24 / 3 * DT + 1e-9, "soft start speed"


def test_soft_start_then_full_speed_and_never_faster():
    ctl = _ctl(Policy(np.full(14, 5.0)), soft_s=0.5, blend=1.0)   # policy slams the limits
    pos = HOME.copy()
    ctl.start(pos, 0.0)
    out = _steps(ctl, 60, pos, acc=FLAT)
    steps = [np.max(np.abs(b - a)) for a, b in zip([pos] + out, out)]
    assert max(steps[:24]) <= 5.24 / 3 * DT + 1e-9
    assert max(steps) <= 5.24 * DT + 1e-9
    assert max(steps[27:35]) > 5.24 / 3 * DT + 1e-6, "full speed after the soft start"


def test_targets_always_inside_the_joint_limits():
    for sign in (1.0, -1.0):
        ctl = _ctl(Policy(np.full(14, 50.0 * sign)), blend=1.0, timeout_s=30)
        ctl.start(HOME, 0.0)
        for tgt in _steps(ctl, 400, HOME, acc=FLAT):
            assert np.all(tgt <= gu.JOINT_HI + 1e-9) and np.all(tgt >= gu.JOINT_LO - 1e-9)


def test_blend_halves_each_step_toward_the_policy_target():
    action = np.zeros(14)
    action[5] = 0.02                            # small, well under the speed limit
    ctl = _ctl(Policy(action), soft_s=0.0, blend=0.5)
    ctl.start(HOME, 0.0)
    a, b = _steps(ctl, 2, HOME, acc=FLAT)
    assert abs((a[5] - HOME[5]) - 0.01) < 1e-9
    assert abs((b[5] - HOME[5]) - 0.015) < 1e-9


def test_standing_needs_upright_and_legs_near_home_held_for_a_while():
    ctl = _ctl(stand_hold_s=0.4)
    ctl.start(HOME, 0.0)
    _steps(ctl, 10, HOME, acc=UP)               # 0.2 s upright: not yet
    assert ctl.phase == "run"
    _steps(ctl, 30, HOME, acc=UP, t0=0.2)
    assert ctl.phase in ("handoff", "done")


def test_upright_with_legs_folded_is_not_standing():
    pos = HOME.copy()
    pos[3] += 0.5
    ctl = _ctl()
    ctl.start(pos, 0.0)
    _steps(ctl, 100, pos, acc=UP)
    assert ctl.phase == "run"


def test_a_single_upright_spike_does_not_count():
    ctl = _ctl(stand_hold_s=0.4)
    ctl.start(HOME, 0.0)
    for i in range(200):
        acc = UP if i % 10 == 0 else FLAT       # one tick in ten reads upright
        ctl.step(HOME, np.zeros(14), np.zeros(3), acc, [1, 1], (i + 1) * DT)
    assert ctl.phase == "run"


def test_handoff_glides_to_home_and_finishes():
    action = np.zeros(14)
    action[5] = 0.3                             # policy holds the neck off home
    ctl = _ctl(Policy(action), soft_s=0.0, handoff_s=0.2)
    ctl.start(HOME, 0.0)
    out = _steps(ctl, 200, HOME, acc=UP)
    assert ctl.phase == "done" and ctl.result == "standing"
    assert np.allclose(out[-1], HOME)
    steps = [np.max(np.abs(b - a)) for a, b in zip(out, out[1:])]
    assert max(steps) <= 5.24 * DT + 1e-9


def test_timeout_fails_and_holds_still():
    ctl = _ctl(timeout_s=2.0)
    ctl.start(HOME, 0.0)
    out = _steps(ctl, 150, HOME, acc=FLAT)
    assert ctl.phase == "failed" and "2 s" in ctl.result
    assert np.allclose(out[-1], out[-2]), "after failing, the targets stay put"


def test_abort_stops_it():
    ctl = _ctl()
    ctl.start(HOME, 0.0)
    _steps(ctl, 5, HOME, acc=FLAT)
    ctl.abort("stopped")
    assert ctl.phase == "failed" and not ctl.active


def test_invalid_policy_output_fails_safely():
    for bad in (np.full(14, np.nan), np.zeros(10)):
        ctl = _ctl(Policy(bad))
        ctl.start(HOME, 0.0)
        before = ctl.targets.copy()
        _steps(ctl, 1, HOME, acc=FLAT)
        assert ctl.phase == "failed" and np.allclose(ctl.targets, before)


def test_imu_correction_maps_this_ducks_standing_reading_onto_the_models():
    standing = [-0.15, -4.22, 7.94]             # dk5426's own robot (their README)
    R, scale = gu.imu_correction(standing)
    out = (R @ np.array(standing)) * scale
    assert np.allclose(out, gu.SIM_STANDING_ACC, atol=1e-6)
    assert np.allclose(R @ R.T, np.eye(3), atol=1e-9) and abs(np.linalg.det(R) - 1) < 1e-9


def test_the_model_stands_level_and_a_level_duck_is_left_alone():
    """The training model reads (0, 0, 9.81) standing (measured in MuJoCo). A duck
    whose IMU is mounted level must not be rotated: the old reference turned the
    MAX duck's readings by 15 degrees, which kept "standing" from latching."""
    assert gu.SIM_STANDING_ACC == (0.0, 0.0, 9.81)
    R, scale = gu.imu_correction([0.251, 0.127, 10.213])      # the MAX duck
    angle = np.degrees(np.arccos(min(1.0, (np.trace(R) - 1) / 2)))
    assert angle < 2.0 and 0.95 < scale < 0.97
    up = (R @ np.array([0.251, 0.127, 10.213]))
    assert up[2] / np.linalg.norm(up) > 0.9999


def test_fused_tilt_latches_standing_through_accelerometer_knocks():
    """A duck that has just landed on its feet rocks: the accelerometer throws single
    samples far off while it stands. With the fused tilt the standing test holds."""
    knocks = [np.array([0.0, 0.0, 9.81]), np.array([9.0, 0.0, 2.5])]   # every 2nd sample bad
    ctl = _ctl()
    ctl.start(HOME, 0.0)
    for i in range(40):                                # 0.8 s, accelerometer only
        ctl.step(HOME, np.zeros(14), np.zeros(3), knocks[i % 2], [1, 1], (i + 1) * 0.02)
    assert ctl.phase == "run"                          # never latched
    ctl = _ctl()
    ctl.start(HOME, 0.0)
    for i in range(40):
        ctl.step(HOME, np.zeros(14), np.zeros(3), knocks[i % 2], [1, 1], (i + 1) * 0.02,
                 tilt_deg=5.0)
    assert ctl.phase in ("handoff", "done")
    ctl = _ctl()                                       # lying down stays lying down
    ctl.start(HOME, 0.0)
    for i in range(40):
        ctl.step(HOME, np.zeros(14), np.zeros(3), np.array([0.0, 0.0, 9.81]), [1, 1],
                 (i + 1) * 0.02, tilt_deg=80.0)
    assert ctl.phase == "run"


def test_a_blocked_joint_is_named():
    ctl = _ctl()
    ctl.start(HOME, 0.0)
    assert ctl.blocked() == []
    stuck = HOME.copy()
    stuck[2] -= 0.6                              # left hip pitch held away from target
    ctl.targets = HOME.copy()
    for _ in range(10):                          # 0.2 s at 50 Hz, not moving
        ctl._note_stalls(stuck, np.zeros(14))
    assert [i for i, _ in ctl.blocked()] == [2] and ctl.blocked()[0][1] >= 0.19
    ctl._note_stalls(stuck, np.full(14, 2.0))    # moving again: stretch ends, max stays
    assert ctl._stall_s[2] == 0.0 and ctl.blocked()[0][0] == 2
    assert ctl.public()["blocked"] == ctl.blocked()


def test_imu_correction_is_identity_without_or_with_a_bad_reference():
    for ref in (None, [0, 0, 0], [1, 2], "x", [float("nan"), 0, 9.8], [0, 0, 30]):
        R, scale = gu.imu_correction(ref)
        assert np.allclose(R, np.eye(3)) and scale == 1.0


def test_corrected_standing_reading_counts_as_upright():
    """With the reference, a duck standing in ITS home pose reads like the model."""
    standing = [0.4, -0.3, 9.79]
    ctl = _ctl(imu_standing_acc=standing)
    ctl.start(HOME, 0.0)
    _steps(ctl, 60, HOME, acc=np.array(standing))
    assert ctl.phase in ("handoff", "done")
    assert ctl.upright > 0.99


def test_config_is_clamped_and_junk_ignored():
    c = gu.clean_config({"enabled": 1, "blend": 9, "timeout_s": "x", "policy": "",
                         "soft_factor": float("nan"), "imu_standing_acc": [1, 2, 3],
                         "max_auto_attempts": 2.7})
    assert c["enabled"] is True and c["blend"] == 1.0
    assert c["timeout_s"] == gu.DEFAULTS["timeout_s"]
    assert c["policy"] == gu.DEFAULTS["policy"]
    assert c["imu_standing_acc"] is None and c["max_auto_attempts"] == 3
    assert gu.clean_config(None) == gu.DEFAULTS
    assert gu.DEFAULTS["enabled"] is False and gu.DEFAULTS["auto"] is False


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} getup tests passed.")
