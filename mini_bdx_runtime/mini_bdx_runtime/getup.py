"""
Get-up controller: runs a get-up policy (92 inputs -> 14 outputs) from the floor
until the duck stands, then blends to the stance pose and hands back to the walk.

The policy contract follows dk5426/open-duck-curation (Apache-2.0), whose get-up
policies ship in policies/:
  inputs   gyro(3) + accel(3) + (joint pos - home)(14) + 0.05 * joint vel(14)
           + last three actions(3x14) + motor targets(14) + foot contacts(2) = 92
  targets  home + 1.0 * action, clamped to the joint limits of the training model
           and to 5.24 rad/s

The runtime behaviour around the policy follows what atlantis235 reported made it
work on a real duck (Open Duck Discord, 2026-11-23): each new target is blended
50/50 with the previous one, the first 0.5 s run at a third of the speed (14 servos
starting at full speed browned out their Pi), "standing" has to hold for 0.4 s on a
filtered upright test, and afterwards the WALK policy balances for a few seconds at
zero command instead of freezing in a pose. That last step lives in the walk loop
(RLWalk._getup_tick); everything else is here.

Hardware-free (numpy only) so it unit-tests off-robot.
"""
import math

import numpy as np

# Joint limits of the training model (Open_Duck_Playground open_duck_mini_v2
# actuator ctrlrange), HWI order: left leg(5), neck_pitch, head_pitch, head_yaw,
# head_roll, right leg(5).
JOINT_LO = np.array([-0.52, -0.44, -1.22, -1.57, -1.57, -0.35, -0.79, -2.79, -0.52,
                     -0.52, -0.44, -0.52, -1.57, -1.57])
JOINT_HI = np.array([0.52, 0.44, 0.52, 1.57, 1.57, 1.13, 0.79, 2.79, 0.52,
                     0.52, 0.44, 1.22, 1.57, 1.57])
LEG_IDX = np.r_[0:5, 9:14]
OBS_SIZE = 92

# Accelerometer reading of the TRAINING model standing at the home pose (m/s^2), as
# measured by dk5426. A real duck's IMU sits at a different angle; with a standing
# reference captured on the duck, the get-up's IMU input is rotated to match.
SIM_STANDING_ACC = (-1.22, -1.95, 9.54)

DEFAULTS = {
    "enabled": False,          # show/allow the get-up at all
    "auto": False,             # start it by itself after a fall (else: button)
    "policy": "dk5426-getup-real2",
    "action_scale": 1.0,
    "max_velocity": 5.24,      # rad/s, as in training
    "blend": 0.5,              # share of the NEW target per tick (1 = no smoothing)
    "soft_s": 0.5,             # soft start: this long ...
    "soft_factor": 1.0 / 3.0,  # ... at this fraction of max_velocity
    "upright_min": 0.85,       # accel z / |accel| needed to count as upright
    "leg_tolerance": 0.35,     # rad every leg joint may be from home
    "stand_hold_s": 0.4,
    "timeout_s": 10.0,
    "handoff_s": 0.2,          # blend to the stance pose
    "balance_s": 3.0,          # walk policy at zero command afterwards
    "max_auto_attempts": 2,
    "imu_standing_acc": None,  # [x, y, z] captured on this duck, or None
}

LIMITS = {
    "action_scale": (0.1, 1.0), "max_velocity": (0.5, 8.0), "blend": (0.1, 1.0),
    "soft_s": (0.0, 3.0), "soft_factor": (0.1, 1.0), "upright_min": (0.5, 0.99),
    "leg_tolerance": (0.05, 1.0), "stand_hold_s": (0.1, 3.0), "timeout_s": (2.0, 30.0),
    "handoff_s": (0.05, 2.0), "balance_s": (0.0, 10.0), "max_auto_attempts": (1, 5),
}


def clean_config(cfg):
    """DEFAULTS overlaid with the valid parts of `cfg`. Never raises."""
    out = dict(DEFAULTS)
    cfg = cfg if isinstance(cfg, dict) else {}
    for key in ("enabled", "auto"):
        if key in cfg:
            out[key] = bool(cfg[key])
    if isinstance(cfg.get("policy"), str) and cfg["policy"]:
        out["policy"] = cfg["policy"]
    for key, (lo, hi) in LIMITS.items():
        if key not in cfg:
            continue
        try:
            v = float(cfg[key])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(v):
            continue
        v = min(hi, max(lo, v))
        out[key] = int(round(v)) if key == "max_auto_attempts" else v
    out["imu_standing_acc"] = clean_acc(cfg.get("imu_standing_acc"))
    return out


def clean_acc(value):
    """A plausible standing accelerometer reading as [x, y, z], or None."""
    try:
        v = [float(x) for x in value]
    except (TypeError, ValueError):
        return None
    if len(v) != 3 or not all(math.isfinite(x) for x in v):
        return None
    mag = math.sqrt(sum(x * x for x in v))
    if not 7.0 <= mag <= 12.5:        # not gravity: refuse rather than mis-rotate
        return None
    return v


def imu_correction(standing_acc, sim_acc=SIM_STANDING_ACC):
    """Rotation (3x3) and scale that turn THIS duck's IMU readings into what the
    training model would read in the same posture, from one pair of standing
    readings. Returns (identity, 1.0) when there is no reference.

    One vector pair fixes the rotation only up to a spin about gravity; the smallest
    rotation is used, which is right as long as the two IMU frames are roughly
    aligned (same axis remap), as they are on an Open Duck Mini."""
    ref = clean_acc(standing_acc)
    if ref is None:
        return np.eye(3), 1.0
    a = np.asarray(ref, dtype=float)
    b = np.asarray(sim_acc, dtype=float)
    scale = float(np.linalg.norm(b) / np.linalg.norm(a))
    a /= np.linalg.norm(a)
    b /= np.linalg.norm(b)
    axis = np.cross(a, b)
    s = float(np.linalg.norm(axis))
    c = float(np.dot(a, b))
    if s < 1e-9:
        return np.eye(3), scale       # already aligned (anti-aligned is refused above)
    k = axis / s
    K = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    R = np.eye(3) + s * K + (1.0 - c) * (K @ K)
    return R, scale


class GetUpController:
    """One get-up attempt. Create, start(), then step() once per control tick.

    phase: "idle" -> "run" -> "handoff" -> "done"   (success)
                           \\-> "failed"            (timeout / abort)
    step() returns the 14 joint targets to send this tick."""

    def __init__(self, policy, home, config=None, control_freq=50.0):
        self.policy = policy
        self.home = np.asarray(home, dtype=float)
        self.cfg = clean_config(config)
        self.dt = 1.0 / float(control_freq)
        self.R, self.acc_scale = imu_correction(self.cfg["imu_standing_acc"])
        self.phase = "idle"
        self.result = ""              # human-readable outcome
        self.last_obs = None
        self._reset_history()

    def _reset_history(self):
        n = len(self.home)
        self.a1 = np.zeros(n)
        self.a2 = np.zeros(n)
        self.a3 = np.zeros(n)
        self.targets = self.home.copy()
        self.t0 = None
        self.up_since = None
        self.up_filtered = None
        self.handoff_from = None
        self.handoff_t = None
        self.elapsed = 0.0
        self.upright = 0.0

    # ---------------------------------------------------------------- IMU ----
    def correct_imu(self, gyro, acc):
        """Rotate/scale this duck's IMU reading into the training model's frame."""
        g = self.R @ np.clip(np.asarray(gyro, dtype=float), -20.0, 20.0)
        a = (self.R @ np.asarray(acc, dtype=float)) * self.acc_scale
        return g, np.clip(a, -30.0, 30.0)

    def build_obs(self, pos, vel, gyro, acc, contacts):
        obs = np.concatenate([
            gyro, acc, pos - self.home, vel * 0.05,
            self.a1, self.a2, self.a3, self.targets,
            np.asarray(contacts, dtype=float),
        ]).astype(np.float32)
        assert obs.shape[0] == OBS_SIZE, obs.shape
        return obs

    # -------------------------------------------------------------- phases ----
    def start(self, pos, now):
        """Begin from the joints' CURRENT positions, with a fresh action history."""
        self._reset_history()
        self.targets = np.clip(np.asarray(pos, dtype=float), JOINT_LO, JOINT_HI)
        self.t0 = now
        self.phase = "run"
        self.result = ""

    def abort(self, why="aborted"):
        if self.phase in ("run", "handoff"):
            self.phase, self.result = "failed", why

    @property
    def active(self):
        return self.phase in ("run", "handoff")

    def _speed_limit(self):
        c = self.cfg
        factor = c["soft_factor"] if self.elapsed < c["soft_s"] else 1.0
        return c["max_velocity"] * factor * self.dt

    def _standing(self, pos, acc, now):
        c = self.cfg
        up = float(acc[2] / max(float(np.linalg.norm(acc)), 1e-6))
        # light filter: one hard knock must not read as "upright" or "fallen"
        self.up_filtered = up if self.up_filtered is None else (
            self.up_filtered + 0.3 * (up - self.up_filtered))
        self.upright = self.up_filtered
        legs_ok = float(np.max(np.abs(pos[LEG_IDX] - self.home[LEG_IDX]))) < c["leg_tolerance"]
        if self.up_filtered > c["upright_min"] and legs_ok:
            if self.up_since is None:
                self.up_since = now
            return now - self.up_since >= c["stand_hold_s"]
        self.up_since = None
        return False

    def step(self, pos, vel, gyro, acc, contacts, now):
        """Advance one tick. Returns the joint targets (always inside the limits and
        at most one speed-limit step from the previous ones)."""
        pos = np.asarray(pos, dtype=float)
        vel = np.asarray(vel, dtype=float)
        c = self.cfg
        if self.phase == "handoff":
            k = min(1.0, (now - self.handoff_t) / max(c["handoff_s"], 1e-6))
            want = self.handoff_from + (self.home - self.handoff_from) * k
            lim = c["max_velocity"] * self.dt
            self.targets = np.clip(want, self.targets - lim, self.targets + lim)
            if k >= 1.0 and float(np.max(np.abs(self.targets - self.home))) < 1e-3:
                self.targets = self.home.copy()
                self.phase, self.result = "done", "standing"
            return self.targets
        if self.phase != "run":
            return self.targets

        self.elapsed = now - self.t0
        gyro_c, acc_c = self.correct_imu(gyro, acc)
        obs = self.build_obs(pos, vel, gyro_c, acc_c, contacts)
        self.last_obs = obs
        action = np.asarray(self.policy.infer(obs), dtype=float)
        if action.shape != self.home.shape or not np.isfinite(action).all():
            self.phase, self.result = "failed", "the policy returned an invalid action"
            return self.targets
        self.a3, self.a2, self.a1 = self.a2, self.a1, action

        want = np.clip(self.home + c["action_scale"] * action, JOINT_LO, JOINT_HI)
        want = self.targets + c["blend"] * (want - self.targets)
        lim = self._speed_limit()
        self.targets = np.clip(want, self.targets - lim, self.targets + lim)

        if self._standing(pos, acc_c, now):
            self.phase = "handoff"
            self.handoff_from = self.targets.copy()
            self.handoff_t = now
        elif self.elapsed > c["timeout_s"]:
            self.phase = "failed"
            self.result = f"not standing after {c['timeout_s']:g} s"
        return self.targets

    def public(self):
        return {"phase": self.phase, "result": self.result,
                "elapsed_s": round(float(self.elapsed), 1),
                "upright": round(float(self.upright), 2)}
