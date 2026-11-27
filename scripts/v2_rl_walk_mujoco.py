# Say which mode is starting before the slow imports below (see mode_announce.py).
try:
    import mode_announce
    _mode_clip = mode_announce.start("walk") if __name__ == "__main__" else None
except Exception:  # noqa: BLE001 - the announcement must never stop the start
    mode_announce = _mode_clip = None

import time
import pickle

import numpy as np
from mini_bdx_runtime.rustypot_position_hwi import HWI
from mini_bdx_runtime.onnx_infer import OnnxInfer

from mini_bdx_runtime.raw_imu import Imu
from mini_bdx_runtime.poly_reference_motion import PolyReferenceMotion
from mini_bdx_runtime.xbox_controller import XBoxController
from mini_bdx_runtime.feet_contacts import FeetContacts
from mini_bdx_runtime.eyes import Eyes
from mini_bdx_runtime.sounds import Sounds
from mini_bdx_runtime.antennas import Antennas
from mini_bdx_runtime.projector import Projector
from mini_bdx_runtime.rl_utils import make_action_dict, LowPassActionFilter
from mini_bdx_runtime.duck_config import DuckConfig
from mini_bdx_runtime.walk_record import WalkRecorder
from mini_bdx_runtime.fall_detector import FallDetector
from mini_bdx_runtime.stability_governor import (
    governor_from_config, tilt_angle_deg, tilt_rate,
)
from mini_bdx_runtime.antenna_anim import AntennaAnimator
from mini_bdx_runtime.walk_defaults import WALK_TUNING_DEFAULTS, IMU_TRIM_DEFAULTS
from mini_bdx_runtime.offset_tuner import OffsetTuner
from mini_bdx_runtime import policy_slots
from mini_bdx_runtime.fall_reflex import FallReflex, TiltEstimator, tuck_pose
from mini_bdx_runtime import fall_reflex as fall_reflex_mod
from mini_bdx_runtime import getup as getup_mod
from mini_bdx_runtime.getup import GetUpController, JOINT_LO, JOINT_HI
from mini_bdx_runtime.flight_recorder import (
    FlightRecorder, MODE_CODES, REFLEX_CODES,
)
from mini_bdx_runtime.command_shaper import CommandShaper
from mini_bdx_runtime.rest_pose import RestPose
from mini_bdx_runtime.head_life import HeadLife
from mini_bdx_runtime import head_life as head_life_mod
from mini_bdx_runtime.loop_health import LoopHealth
# Optional: Web UI + scanner sound. The walk still runs if these are absent.
try:
    from mini_bdx_runtime.control_bus import ControlBus
    from mini_bdx_runtime.web_control import WebControlServer
    from mini_bdx_runtime.telemetry import BatteryMonitor, build_state
except ImportError:
    ControlBus = WebControlServer = BatteryMonitor = build_state = None
try:
    from mini_bdx_runtime.scanner_sound import ScannerSound, PygameScannerBackend
except ImportError:
    ScannerSound = PygameScannerBackend = None

import math
import os
import random
import threading
from collections import deque

HOME_DIR = os.path.expanduser("~")
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Policy switch while walking: stand in place this long (zero command), then fade the
# old policy's amplitude out, swap at zero amplitude and fade the new one in. The
# stance pose is what "zero amplitude" means, so there is no step at the swap.
SWITCH_SETTLE_S = 0.6
# Joint speed (rad/s) for scripted moves the policy does not drive: the tuck of the
# fall reflex (as fast as the servos go) and the glide back to stance afterwards.
REFLEX_TUCK_SPEED = 5.24
RECOVER_SPEED = 1.0
# After a soft landing, Resume only works once the duck is this upright again.
RECOVER_MAX_TILT_DEG = 25.0

# action_scale is ramped toward a web-set target so a live change can't step the
# leg amplitude in one tick (motor safety). Max change per control tick:
ACTION_SCALE_RAMP = 0.02
# Fading into the rest pose is deliberately slow (about 1.3 s from a scale of 0.25):
# the body swings from the policy's own stance to the plain one. Waking is fast.
REST_FADE_RAMP = 0.004
STEER_TRIM_LIMIT = 0.5
# Head command limits [neck_pitch, head_pitch, head_yaw, head_roll] (rad): the gamepad's
# own ranges, all inside what the walk policies were trained with.
HEAD_CMD_LO = np.array([-0.34, -0.78, -0.5, -0.5])
HEAD_CMD_HI = np.array([1.1, 0.3, 0.5, 0.5])

# DPAD-LEFT hold (s) to start a whole-body recording; walk_recording.pkl persists
# the last take across restarts (handy for events).
WALK_RECORD_HOLD_S = 3.0
WALK_RECORDING_PATH = os.path.join(os.getcwd(), "walk_recording.pkl")

# Live IMU-trim tuner (RB + D-pad, or the web card): step per nudge and the hard
# clamp so a stuck input can't drive the trim to a dangerous angle.
TRIM_STEP = 0.002     # rad (~0.11 deg) per D-pad tap / web +/- press
TRIM_LIMIT = 0.2      # rad (~11.5 deg) max |trim| on either axis — sized for a
                      # heavy rear payload (the big battery needs ~0.1 rad alone)


def _accel_pitch_roll(accel):
    """Best-effort pitch/roll (deg) from the accelerometer gravity vector, for the
    Web UI attitude horizon. Axis convention may need per-robot tuning — cosmetic,
    never used for control. Returns None if accel is missing."""
    if accel is None or len(accel) < 3:
        return None
    ax, ay, az = float(accel[0]), float(accel[1]), float(accel[2])
    pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az) or 1e-9))
    roll = math.degrees(math.atan2(ay, az if az != 0 else 1e-9))
    return {"pitch": round(pitch, 1), "roll": round(roll, 1)}


class _HoldDetector:
    """Fires once when a button has been held continuously for `duration` (same
    contract as head_puppet's). reset_require_release() blocks re-arming until the
    button is released."""

    def __init__(self, duration):
        self.duration = duration
        self.start = None
        self.fired = False
        self._need_release = False

    def reset_require_release(self):
        self.start = None
        self.fired = False
        self._need_release = True

    def update(self, pressed, now):
        if self._need_release:
            if not pressed:
                self._need_release = False
            return False
        if pressed:
            if self.start is None:
                self.start = now
                self.fired = False
            if not self.fired and now - self.start >= self.duration:
                self.fired = True
                return True
        else:
            self.start = None
            self.fired = False
        return False


class RLWalk:
    def __init__(
        self,
        onnx_model_path: str,
        duck_config_path: str = f"{HOME_DIR}/duck_config.json",
        serial_port: str = "/dev/ttyACM0",
        control_freq: float = 50,
        pid=[30, 0, 0],
        action_scale=None,
        commands=False,
        pitch_bias=0,
        save_obs=False,
        replay_obs=None,
        cutoff_frequency=None,
    ):

        self.duck_config = DuckConfig(config_json_path=duck_config_path)

        self.commands = commands
        self.pitch_bias = pitch_bias

        self.onnx_model_path = onnx_model_path
        self._init_policies()

        self.num_dofs = 14
        self.max_motor_velocity = self.duck_config.max_motor_velocity_rad_s  # rad/s

        # Control
        self.control_freq = control_freq
        self.pid = pid

        self.save_obs = save_obs
        if self.save_obs:
            self.saved_obs = []

        self.replay_obs = replay_obs
        if self.replay_obs is not None:
            self.replay_obs = pickle.load(open(self.replay_obs, "rb"))

        self.action_filter = None
        if cutoff_frequency is not None:
            self.action_filter = LowPassActionFilter(
                self.control_freq, cutoff_frequency
            )

        self.hwi = HWI(self.duck_config, serial_port)

        self.start()

        self.imu = Imu(
            sampling_freq=int(self.control_freq),
            user_pitch_bias=self.pitch_bias,
            upside_down=self.duck_config.imu_upside_down,
            pitch_trim=float(self.duck_config.imu_trim.get("pitch", 0.0)),
            roll_trim=float(self.duck_config.imu_trim.get("roll", 0.0)),
        )

        self.feet_contacts = FeetContacts()

        # Live joint-offset tuning from the phone (webui/offsets.html). It edits the
        # HWI's own offsets dict, so there is exactly one set of offsets in the process.
        self.offset_tuner = OffsetTuner(self.hwi.joints_offsets)
        self._tick_dt = 0.0
        # Last persisted IMU trim, reported next to the live one so the page can show
        # unsaved changes without re-reading the config file.
        self._imu_trim_saved = {
            "pitch": float(self.duck_config.imu_trim.get("pitch", 0.0)),
            "roll": float(self.duck_config.imu_trim.get("roll", 0.0)),
        }

        # Scales
        # action_scale precedence: explicit CLI > duck_config > 0.25 default.
        if action_scale is not None:
            self.action_scale = action_scale
        elif self.duck_config.action_scale is not None:
            self.action_scale = float(self.duck_config.action_scale)
        else:
            self.action_scale = 0.25
        # Live-tunable target the loop ramps `action_scale` toward (Web UI edits it).
        self._action_scale_target = self.action_scale
        print(f"action_scale = {self.action_scale}")
        self._cli_action_scale = action_scale is not None

        # Per-tick slew clamp: mirror the config flag into a live-tunable attr so the
        # Web UI can flip it without a restart (the loop reads self.velocity_clip).
        self.velocity_clip = bool(self.duck_config.velocity_clip)

        # Ear-antenna free-animation driver (idle wiggle when not hand-controlled).
        self.antenna_anim = AntennaAnimator(
            enabled=self.duck_config.antenna_free_anim,
            sync=self.duck_config.antenna_sync, rng=random.Random(),
        )

        self.last_action = np.zeros(self.num_dofs)
        self.last_last_action = np.zeros(self.num_dofs)
        self.last_last_last_action = np.zeros(self.num_dofs)

        self.init_pos = list(self.hwi.init_pos.values())

        self.motor_targets = np.array(self.init_pos.copy())
        self.prev_motor_targets = np.array(self.init_pos.copy())

        self.last_commands = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        self.paused = self.duck_config.start_paused

        self.command_freq = 20  # hz
        # Phone Web UI shares one ControlBus with the gamepad (parallel control).
        self.web_bus = None
        if getattr(self.duck_config, "web_ui", False) and ControlBus is not None:
            self.web_bus = ControlBus()
        if self.commands:
            self.xbox_controller = XBoxController(
                self.command_freq, web_bus=self.web_bus
            )

        # Reference motion, but we only really need the length of one phase
        # TODO
        self.PRM = PolyReferenceMotion("./polynomial_coefficients.pkl")
        self.imitation_i = 0
        self.imitation_phase = np.array([0, 0])
        self.phase_frequency_factor = 1.0
        self.phase_frequency_factor_offset = (
            self.duck_config.phase_frequency_factor_offset
        )
        # Steering trim: a constant turn command added while driving, to cancel a
        # walk that curves by itself. Belongs to the policy, like the two above.
        try:
            trim = float(getattr(self.duck_config, "steer_trim", 0.0) or 0.0)
        except (TypeError, ValueError):
            trim = 0.0
        self.steer_trim = float(np.clip(trim, -STEER_TRIM_LIMIT, STEER_TRIM_LIMIT))
        # Values the STOCK policy runs with; another slot may carry its own.
        self._stock_tuning = {
            "action_scale": float(self._action_scale_target),
            "phase_frequency_factor_offset": float(self.phase_frequency_factor_offset),
            "steer_trim": float(self.steer_trim),
        }
        if self.policy_manager.active_id != policy_slots.STOCK_ID:
            self._apply_slot_tuning(self.policy_manager.active_id, ramp=False)

        # Optional expression features
        if self.duck_config.eyes:
            self.eyes = Eyes()
        if self.duck_config.projector:
            self.projector = Projector()
        if self.duck_config.speaker:
            self.sounds = Sounds(
                volume=1.0, sound_directory="../mini_bdx_runtime/assets/"
            )
        if self.duck_config.antennas:
            self.antennas = Antennas()

        # ---- record/playback (whole-body command stream), fall detection ----
        self.recorder = WalkRecorder(self.control_freq)
        try:
            if os.path.exists(WALK_RECORDING_PATH):
                n = self.recorder.load(WALK_RECORDING_PATH)
                print(f"[walk] loaded {n} recorded frames "
                      f"({self.recorder.duration_s:.1f}s) — DPAD-RIGHT to play")
        except Exception as e:  # noqa: BLE001
            print(f"[walk] could not load recording: {e}")
        self._record_hold = _HoldDetector(WALK_RECORD_HOLD_S)
        self._record_stop_armed = False

        self.fall_detector = FallDetector()
        self._init_stability_suite()
        # Tilt-based stability governor (disabled unless duck_config enables it).
        self.governor = governor_from_config(self.duck_config.stability_governor)
        self._gov_scale = 1.0
        self._gov_severity = 0.0

        # ---- scanner lamp-loop coupled to the projector LED ----
        self.scanner = None
        if self.duck_config.speaker and ScannerSound is not None:
            try:
                self.scanner = ScannerSound(
                    PygameScannerBackend("../mini_bdx_runtime/assets/scanner/")
                )
            except Exception as e:  # noqa: BLE001
                print(f"[walk] scanner sound unavailable: {e}")
                self.scanner = None

        # ---- telemetry / web server ----
        self.loop_hz = float(self.control_freq)
        self._last_imu = None
        self._start_t = time.time()
        self.battery_mon = (
            BatteryMonitor(getattr(self.duck_config, "battery", {}))
            if BatteryMonitor is not None else None
        )
        self._features = {
            "antennas": self.duck_config.antennas,
            "projector": self.duck_config.projector,
            "speaker": self.duck_config.speaker,
            "camera": self.duck_config.camera,
        }
        self._sound_names = (
            list(self.sounds.sounds.keys())
            if self.duck_config.speaker and getattr(self, "sounds", None)
            and self.sounds.ok else []
        )
        self.web_server = None
        if self.web_bus is not None and WebControlServer is not None:
            try:
                self.web_server = WebControlServer(
                    self.web_bus, port=getattr(self.duck_config, "web_port", 8080)
                )
                self.web_server.start()
            except Exception as e:  # noqa: BLE001
                print(f"[walk] web UI unavailable: {e}")
                self.web_server = None

    # ============================================================ stability suite ====
    # Policy slots, fall reflex, get-up, black-box recorder. Each piece is a pure
    # module in mini_bdx_runtime/ (unit-tested off-robot); the methods below only
    # connect them to the servos and to the Web UI.
    def _init_policies(self):
        """Find the selectable policies and load the one this duck last ran. Falls
        back to the start-up (--onnx_model_path) policy if that fails."""
        def loader(path):
            return OnnxInfer(path, awd=True)

        cfg = self.duck_config.policies if isinstance(self.duck_config.policies, dict) else {}
        slots = policy_slots.discover(
            policy_slots.default_folders(REPO_ROOT, HOME_DIR), self.onnx_model_path)
        self._policy_loader = loader
        self.policy_manager = policy_slots.PolicyManager(
            slots, loader, tuning=cfg.get("tuning"))
        self.policy = None
        self._session_tuning = {}
        want = str(cfg.get("active", policy_slots.STOCK_ID))
        if want != policy_slots.STOCK_ID:
            slot = slots.get(want)
            if slot is None or slot.kind != "walk":
                print(f"[policy] saved choice '{want}' not found; using the stock walk")
            else:
                policy, problem = policy_slots.load_slot(slot, loader)
                if policy is None:
                    print(f"[policy] {slot.name}: {problem}; using the stock walk")
                    self.policy_manager.message = f"{slot.name}: {problem}"
                else:
                    self.policy = policy
                    self.policy_manager.active_id = slot.id
        if self.policy is None:
            self.policy = loader(self.onnx_model_path)
            self.policy_manager.active_id = policy_slots.STOCK_ID
        print(f"[policy] running: {slots[self.policy_manager.active_id].name}  "
              f"({len(self.policy_manager.walk_slots())} walk / "
              f"{len(self.policy_manager.getup_slots())} get-up slots found)")

    def _init_stability_suite(self):
        self.tilt = TiltEstimator()
        self.reflex = FallReflex(self.duck_config.fall_reflex)
        self._tuck_pose = np.array(tuck_pose(self.init_pos, JOINT_LO, JOINT_HI))
        self.getup_cfg = getup_mod.clean_config(self.duck_config.getup)
        self.getup = None                 # GetUpController of the current attempt
        self._getup_policy = None
        self._getup_policy_id = None
        self._getup_note = ""
        self._getup_attempts = 0
        self._getup_take = False
        # Automatic get-ups since the duck last walked for a while or the operator
        # stepped in. Bounds a stand-up / fall-over / stand-up loop.
        self._auto_getups = 0
        self._walk_since = time.time()
        self.shaper = CommandShaper(self.duck_config.command_slew)
        pol_cfg = self.duck_config.policies if isinstance(self.duck_config.policies, dict) else {}
        self.rest_pose = RestPose(enabled=bool(pol_cfg.get("rest_straight", False)))
        self.head_life = HeadLife(getattr(self.duck_config, "head_life", {}))
        self.loop_health = LoopHealth(self.control_freq)
        self.blackbox = FlightRecorder(
            self.control_freq, self.duck_config.flight_recorder,
            meta={"policy": self.policy_manager.active_id,
                  "joints": list(self.hwi.joints.keys()),
                  "init_pos": [float(v) for v in self.init_pos],
                  "kp": list(getattr(self, "_kps_full", []))})
        # What the loop is doing besides plain walking / being paused:
        # None | "reflex" | "recover" | "getup" | "balance" | "switch"
        self._activity = None
        self._soft = False                # servo stiffness currently lowered
        # The servos hold (or hang in) a pose the walk policy must not start from:
        # after a soft landing, a stopped get-up or recovery glide, or a policy swap
        # while paused. The next resume then glides to the stance first.
        self._needs_recover = False
        self._balance_until = 0.0
        self._switch_until = 0.0
        self._acc_window = deque(maxlen=50)
        self._prev_tick_active = False
        self._notice = ""
        self._notice_until = 0.0
        self._save_lock = threading.Lock()
        if self.getup_cfg["enabled"]:
            self._load_getup_policy()

    def _notify(self, text, seconds=5.0):
        """One line for the operator: printed, and shown in the Web UI for a while."""
        print(f"[duck] {text}")
        self._notice = str(text)
        self._notice_until = time.time() + seconds

    def _save_async(self, fields):
        """Persist config fields off the control thread (an SD-card fsync can take
        longer than a control tick). Backup-first, other keys untouched."""
        def work():
            from mini_bdx_runtime.duck_config import save_config_fields
            with self._save_lock:
                try:
                    save_config_fields(fields)
                except Exception as e:  # noqa: BLE001
                    print(f"[duck] could not save {list(fields)}: {e}")
        threading.Thread(target=work, daemon=True).start()

    def _sense(self, now):
        """Read the IMU once per tick and keep the tilt estimate current (also while
        paused, so the horizon, the reflex and the get-up all see the same data)."""
        imu = self.imu.get_data()
        self._last_imu = imu
        dt = self._tick_dt if 0.0 < self._tick_dt < 0.2 else 1.0 / self.control_freq
        try:
            self.tilt.update(imu["gyro"], imu["accelero"], dt)
            self._acc_window.append(tuple(float(v) for v in imu["accelero"]))
        except (KeyError, TypeError, ValueError):
            pass

    def _read_joints(self):
        pos = self.hwi.get_present_positions(ignore=["left_antenna", "right_antenna"])
        vel = self.hwi.get_present_velocities(ignore=["left_antenna", "right_antenna"])
        if (pos is None or vel is None or len(pos) != self.num_dofs
                or len(vel) != self.num_dofs):
            self.loop_health.note_read_failure()
            return None, None
        return np.array(pos, dtype=float), np.array(vel, dtype=float)

    def _write_targets(self, targets):
        self.motor_targets = np.array(targets, dtype=float)
        self.prev_motor_targets = self.motor_targets.copy()
        self.hwi.set_position_all(
            make_action_dict(self.motor_targets, list(self.hwi.joints.keys())))

    def _reset_policy_history(self):
        self.last_action = np.zeros(self.num_dofs)
        self.last_last_action = np.zeros(self.num_dofs)
        self.last_last_last_action = np.zeros(self.num_dofs)
        self.imitation_i = 0
        self.imitation_phase = np.array([1.0, 0.0])
        self.shaper.reset()

    def _reset_policy_state(self):
        """Give the walk policy the clean start of a fresh boot. Only call this when
        the servos ARE at the stance pose: it declares the stance as the last target."""
        self._reset_policy_history()
        self.motor_targets = np.array(self.init_pos, dtype=float)
        self.prev_motor_targets = self.motor_targets.copy()
        if getattr(self, "head_life", None) is not None:
            self.head_life.reset()      # the head is at the stance: start from there

    def _apply_head_life(self, now, driving):
        """Add the look-around / head-bob offsets to this tick's head command. They
        go in BEFORE the observation is built, so the policy sees the head command it
        was trained to carry. Off, or not allowed, the offsets glide to zero."""
        allowed = (self._activity is None
                   and not getattr(self.xbox_controller, "head_control_mode", False)
                   and self.recorder.state != "playing")
        dt = self._tick_dt if 0.0 < self._tick_dt < 0.2 else 1.0 / self.control_freq
        phase = self.imitation_i / self.PRM.nb_steps_in_period * 2.0 * np.pi
        off = self.head_life.update(now, dt, gait_phase=phase, stepping=driving,
                                    allowed=allowed)
        if not any(abs(v) > 1e-6 for v in off):
            return
        cmds = np.array(self.last_commands, dtype=float)    # never edit in place
        cmds[3:7] = np.clip(cmds[3:7] + np.array(off), HEAD_CMD_LO, HEAD_CMD_HI)
        self.last_commands = cmds

    def _end_tick(self, t):
        self._note_control_tick()
        took = time.time() - t
        time.sleep(max(0, 1 / self.control_freq - took))

    def _note_control_tick(self):
        """Bookkeeping for a tick that drove the motors."""
        self._prev_tick_active = True
        imu = self._last_imu or {}
        self.loop_health.note_imu(imu.get("gyro"), imu.get("accelero"))

    def _mode_name(self):
        if self._activity:
            return self._activity
        if self.paused:
            return "stance" if self.offset_tuner.holding else "paused"
        return "walk"

    def _rec_tick(self, t, pos=None, vel=None, action=None, took=0.0):
        imu = self._last_imu or {}
        self.blackbox.add(
            t=t - self._start_t, dt=self._tick_dt, gyro=imu.get("gyro"),
            acc=imu.get("accelero"), commands=self.last_commands, pos=pos, vel=vel,
            action=action, targets=self.motor_targets,
            feet=self.feet_contacts.get(), tilt=self.tilt.tilt_deg,
            tilt_rate=self.tilt.rate_deg_s, predicted=self.reflex.predicted_deg,
            gov_scale=self._gov_scale, mode=MODE_CODES.get(self._mode_name(), 0),
            reflex=REFLEX_CODES.get(self.reflex.phase, 0), took=took)

    # ---------------------------------------------------------- policy switch ----
    def _live_tuning(self):
        return {"action_scale": float(self._action_scale_target),
                "phase_frequency_factor_offset": float(self.phase_frequency_factor_offset),
                "steer_trim": float(self.steer_trim)}

    def _policies_config(self):
        """The "policies" entry of duck_config: choice, per-policy tuning, rest pose."""
        return self.policy_manager.config_fields(
            extra={"rest_straight": bool(self.rest_pose.enabled)})

    def _apply_slot_tuning(self, slot_id, ramp=True):
        """Load the values a slot runs with: this session's edits, else what was
        saved for it, else its own defaults, else the stock values."""
        if slot_id in self._session_tuning:
            t = self._session_tuning[slot_id]
        elif slot_id == policy_slots.STOCK_ID:
            t = self._stock_tuning
        else:
            t = self.policy_manager.tuning_for(slot_id, fallback=self._stock_tuning)
        if "action_scale" in t and not self._cli_action_scale:
            self._action_scale_target = float(t["action_scale"])
            if not ramp:
                self.action_scale = self._action_scale_target
        if "phase_frequency_factor_offset" in t:
            self.phase_frequency_factor_offset = float(t["phase_frequency_factor_offset"])
        self.steer_trim = float(np.clip(float(t.get("steer_trim", 0.0)),
                                        -STEER_TRIM_LIMIT, STEER_TRIM_LIMIT))

    def _service_policy_switch(self, now):
        """A requested policy loads on a background thread; once it is ready, swap it
        in at a standstill: at once if paused, else after standing still for a
        moment. Never mid-stride, never during a reflex or a get-up."""
        if not self.policy_manager.ready() or self._activity is not None:
            return
        if self.paused:
            self._swap_policy(now)
        else:
            self._activity = "switch"
            self._switch_until = now + SWITCH_SETTLE_S
            if self.recorder.state == "playing":
                self.recorder.state = "idle"

    def _swap_policy(self, now):
        old_id = self.policy_manager.active_id
        taken = self.policy_manager.take()
        if taken is None:
            return
        slot, policy = taken
        self._session_tuning[old_id] = self._live_tuning()
        self.policy = policy
        if self.paused:
            # The servos hold whatever pose the pause froze. Keep the targets as
            # they are (the speed limit and a stance glide measure from them) and
            # let the next resume glide to the stance before the new policy starts.
            self._reset_policy_history()
            self._needs_recover = True
        else:
            self._reset_policy_state()    # standing at zero amplitude = the stance
        self._apply_slot_tuning(slot.id)
        self.action_scale = 0.0           # the new policy's amplitude fades in
        self.blackbox.meta["policy"] = slot.id
        self.blackbox.mark("switch", slot.id)
        self._save_async({"policies": self._policies_config()})
        self._notify(f"Now running: {slot.name}")

    # ------------------------------------------------------------ fall reflex ----
    def _update_reflex(self, now, enabled=True):
        gyro = (self._last_imu or {}).get("gyro")
        try:
            gyro_mag = float(np.linalg.norm(gyro))
        except (TypeError, ValueError):
            gyro_mag = 0.0
        return self.reflex.update(self.tilt.tilt_deg, self.tilt.rate_deg_s, gyro_mag,
                                  now, enabled=enabled)

    def _go_soft(self):
        """Lower the servo stiffness so the joints give way instead of fighting."""
        c = self.reflex.cfg
        kps = [float(c["soft_kp"])] * self.num_dofs
        kps[5:9] = [float(c["soft_kp_head"])] * 4
        self._needs_recover = True        # whatever the write does: no plain resume
        for attempt in (1, 2):
            try:
                self.hwi.set_kps(kps)
                self._soft = True
                return
            except Exception as e:  # noqa: BLE001
                print(f"[reflex] could not lower the stiffness (try {attempt}): {e}")

    def _stiffen(self):
        for attempt in (1, 2):
            try:
                self.hwi.set_kps(list(self._kps_full))
                self._soft = False
                return True
            except Exception as e:  # noqa: BLE001
                print(f"[reflex] could not restore the stiffness (try {attempt}): {e}")
        return False

    def _begin_reflex(self, now, event):
        self._activity = "reflex"
        self._end_getup_take()
        if self.recorder.state == "playing":
            self.recorder.state = "idle"
        lt = self.reflex.last_trigger or {}
        self.blackbox.mark("reflex", f"tilt {lt.get('tilt')} predicted {lt.get('predicted')}")
        print(f"⚠ FALLING (tilt {lt.get('tilt')}°, predicted {lt.get('predicted')}°)"
              " — soft landing")
        if event == "trigger_soft":
            self._go_soft()

    def _reflex_tick(self, now):
        event = self._update_reflex(now)
        if self.reflex.phase == "tuck":
            step = REFLEX_TUCK_SPEED / self.control_freq
            self._write_targets(np.clip(self._tuck_pose, self.motor_targets - step,
                                        self.motor_targets + step))
        if event == "soft":
            self._go_soft()
        # Read the joints only once the time-critical writes are out: the recording
        # then shows the legs giving way.
        pos, vel = self._read_joints()
        self._rec_tick(now, pos=pos, vel=vel)
        if self.reflex.phase == "down":
            self._activity = None
            self.paused = True
            self.fall_detector.fallen = True
            self._notify("Down. Stand the duck up and press A, or use GET UP.", 30.0)
            if self.duck_config.speaker:
                try:
                    self.sounds.play("beep2.wav")
                except Exception:  # noqa: BLE001
                    pass
            self._maybe_auto_getup(now)

    def _begin_recover(self, now):
        """After a soft landing: stiffen up where the joints are, then glide to the
        stance pose. Only once the duck has been stood upright again."""
        if self.tilt.tilt_deg > RECOVER_MAX_TILT_DEG:
            self._notify("Stand the duck upright first (or use GET UP).")
            return False
        if self._soft:
            pos, _ = self._read_joints()
            if pos is None:
                self._notify("Cannot read the joints; try again.")
                return False
            self._write_targets(pos)        # goal = exactly where it is, then stiffen
            if not self._stiffen():
                self._notify("Could not stiffen the servos; try again.")
                return False
        # Not soft: the servos already hold motor_targets; glide on from there.
        if self.offset_tuner.holding:
            self.offset_tuner.release()
        self._activity = "recover"
        self.paused = False
        return True

    def _recover_tick(self, now):
        step = RECOVER_SPEED / self.control_freq
        home = np.array(self.init_pos, dtype=float)
        self._write_targets(np.clip(home, self.motor_targets - step,
                                    self.motor_targets + step))
        pos, vel = self._read_joints()
        self._rec_tick(now, pos=pos, vel=vel)
        if float(np.max(np.abs(self.motor_targets - home))) < 1e-6:
            self._reset_policy_state()
            self.action_scale = 0.0
            self._activity = None
            self._needs_recover = False
            self.fall_detector.reset(now)
            self.reflex.rearm()
            self._notify("Back in stance. Walking again.")

    # ----------------------------------------------------------------- get-up ----
    def _load_getup_policy(self):
        """Load the configured get-up policy on a background thread."""
        want = self.getup_cfg["policy"]
        if self._getup_policy is not None and self._getup_policy_id == want:
            return
        slot = self.policy_manager.slots.get(want)
        if slot is None or slot.kind != "getup":
            self._getup_note = f"get-up policy '{want}' not found"
            return
        self._getup_policy = None
        self._getup_note = "loading"

        def work():
            policy, problem = policy_slots.load_slot(slot, self._policy_loader)
            if self.getup_cfg["policy"] != slot.id:
                return                      # the choice changed meanwhile
            if policy is None:
                self._getup_note = problem
                print(f"[getup] {slot.name}: {problem}")
            else:
                self._getup_policy, self._getup_policy_id = policy, slot.id
                self._getup_note = ""
                print(f"[getup] policy loaded: {slot.name}")
        threading.Thread(target=work, daemon=True).start()

    def _lying(self):
        return self.tilt.tilt_deg > 50.0

    def _maybe_auto_getup(self, now):
        """After a fall, start the get-up by itself if the operator asked for that.
        Only when the duck is really lying down: being lifted also opens both foot
        switches, and a get-up in someone's hands must not happen."""
        if not (self.getup_cfg["enabled"] and self.getup_cfg["auto"] and self._lying()):
            return
        if self._auto_getups >= self.getup_cfg["max_auto_attempts"]:
            self._notify("Fell again right after getting up. Waiting for you now.", 30.0)
            return
        self._auto_getups += 1
        self._getup_attempts = 0
        self._start_getup(now, auto=True)

    def _start_getup(self, now, auto=False):
        if not self.getup_cfg["enabled"]:
            self._notify("Get-up is switched off (TUNE → Get-up).")
            return False
        if self._activity in ("getup", "reflex", "recover"):
            return False
        if not auto and not (self.paused or self.fall_detector.fallen):
            self._notify("Pause first: the get-up only starts from a standstill.")
            return False
        if not self._lying():
            self._notify("The get-up only starts with the duck lying down.")
            return False
        if self._getup_policy is None:
            self._load_getup_policy()
            self._notify(f"Get-up policy not ready ({self._getup_note or 'loading'}).")
            return False
        pos, _ = self._read_joints()
        if pos is None:
            self._notify("Cannot read the joints; get-up not started.")
            return False
        if not auto:
            self._getup_attempts = 0
        self._getup_attempts += 1
        ctl = GetUpController(self._getup_policy, self.init_pos, self.getup_cfg,
                              self.control_freq)
        ctl.start(pos, now)
        self._write_targets(ctl.targets)        # goal = current pose, then stiffen
        if self._soft and not self._stiffen():
            return False
        if self.offset_tuner.holding:
            self.offset_tuner.release()
        if self.recorder.state == "playing":
            self.recorder.state = "idle"
        self.getup = ctl
        self._activity = "getup"
        self.paused = False
        self._getup_take = self.blackbox.start_take("getup")
        self._notify(f"Get-up running (attempt {self._getup_attempts}). A aborts.", 12.0)
        return True

    def _end_getup_take(self):
        if self._getup_take:
            self.blackbox.stop_take()
            self._getup_take = False

    def _abort_getup(self, why):
        if self.getup is not None:
            self.getup.abort(why)
        self._activity = None
        self.paused = True
        self._go_soft()                 # do not stall the servos against the floor
        self._end_getup_take()
        blocked = self._getup_blocked_text()
        print(f"[getup] stopped: {why}{blocked}", flush=True)
        self._notify(f"Get-up stopped: {why}.{blocked}", 15.0)

    def _getup_blocked_text(self):
        """' Blocked: left_hip_pitch 0.3 s' for the joints that stood still far from
        their target during the attempt, or ''."""
        if self.getup is None:
            return ""
        names = list(self.hwi.joints.keys())
        parts = [f"{names[i]} {s:g} s" for i, s in self.getup.blocked()[:3]
                 if i < len(names)]
        return (" Blocked: " + ", ".join(parts) + ".") if parts else ""

    def _getup_tick(self, now):
        ctl = self.getup
        pos, vel = self._read_joints()
        if pos is None:
            return
        imu = self._last_imu
        targets = ctl.step(pos, vel, imu["gyro"], imu["accelero"],
                           self.feet_contacts.get(), now, tilt_deg=self.tilt.tilt_deg)
        self._write_targets(targets)
        self._rec_tick(now, pos=pos, vel=vel, action=ctl.a1)
        if ctl.phase == "done":
            # Standing: let the WALK policy balance at zero command instead of
            # freezing in a pose (leftover momentum tips a frozen duck over).
            self._reset_policy_state()
            self._needs_recover = False
            self.fall_detector.reset(now)
            self.reflex.rearm()
            self._activity = "balance"
            self._balance_until = now + self.getup_cfg["balance_s"]
            self._walk_since = now
            self._notify("Standing. Balancing.")
        elif ctl.phase == "failed":
            auto = self.getup_cfg["auto"]
            if (auto and self._lying()
                    and self._getup_attempts < self.getup_cfg["max_auto_attempts"]):
                self._end_getup_take()
                self._activity = None
                # A refused retry (switched off meanwhile, policy changed, a dropped
                # joint read) must end paused and soft, never as a walking tick.
                if not self._start_getup(now, auto=True):
                    self._abort_getup(ctl.result)
            else:
                self._abort_getup(ctl.result)

    def _capture_standing_reference(self):
        """Store what THIS duck's accelerometer reads standing in the stance pose, so
        the get-up policy can be fed readings in the frame it was trained with."""
        home = np.array(self.init_pos, dtype=float)
        at_stance = (self.paused and self._activity is None and not self._soft
                     and self.offset_tuner.holding
                     and float(np.max(np.abs(self.motor_targets - home))) < 0.02)
        if not at_stance:
            self._notify("Hold the stance first, then capture.")
            return
        if len(self._acc_window) < 25:
            self._notify("Keep holding still for a moment, then capture again.")
            return
        window = np.array(self._acc_window, dtype=float)
        if float(np.max(np.std(window, axis=0))) > 0.25:
            self._notify("Too much movement. Let the duck stand still and retry.")
            return
        acc = getup_mod.clean_acc(np.median(window, axis=0))
        if acc is None:
            self._notify("That reading is not gravity; reference not saved.")
            return
        self.getup_cfg["imu_standing_acc"] = [round(v, 3) for v in acc]
        self._save_async({"getup": self._getup_config_dict()})
        self._notify("Standing reference saved.")

    def _getup_config_dict(self):
        c = self.getup_cfg
        out = {k: (round(float(c[k]), 4) if isinstance(c[k], float) else c[k])
               for k in getup_mod.DEFAULTS if k != "imu_standing_acc"}
        out["imu_standing_acc"] = c["imu_standing_acc"]
        return out

    # ------------------------------------------------------------ pause button ----
    def _on_pause_button(self, t):
        """A on the pad / PAUSE on the phone. Besides pause and resume it is the one
        'stop what you are doing' button for the get-up and the recovery glide."""
        if self._activity == "getup":
            self._abort_getup("stopped by the operator")
            return
        if self._activity == "reflex":
            return                          # already heading for the floor
        if self._activity == "recover":
            self._activity = None           # _needs_recover stays set: the next A
            self.paused = True              # continues the glide, it does not walk
            print("PAUSE")
            return
        if self.paused and (self._soft or self._needs_recover):
            if self._begin_recover(t):
                self._auto_getups = 0
                self._walk_since = t
            return
        self.paused = not self.paused
        if self.paused:
            if self._activity in ("balance", "switch"):
                self._activity = None
                self._end_getup_take()
            print("PAUSE")
        else:
            print("UNPAUSE")
            self.fall_detector.reset(t)     # re-arm after righting
            self.reflex.rearm()
            self.shaper.reset()
            self._auto_getups = 0           # the operator stepped in
            self._walk_since = t

    # -------------------------------------------- live settings (stability suite) ----
    def _consume_suite_settings(self, settings, saves, resets, now):
        pol = settings.get("policy") or {}
        if "active" in pol:
            if not self.policy_manager.request(pol["active"]):
                self._notify(self.policy_manager.message)
        if pol.get("cancel"):
            self.policy_manager.cancel()
        if "rest_straight" in pol:
            want = pol["rest_straight"]
            self.rest_pose.enabled = (want is True or (
                isinstance(want, str) and want.lower() in ("straight", "on", "true")))
            self.rest_pose.reset()
            self._save_async({"policies": self._policies_config()})
            self._notify("Rest pose: straight stance when idle." if self.rest_pose.enabled
                         else "Rest pose: the policy's own.")

        safety = settings.get("safety") or {}
        for key, value in safety.items():
            if key == "reset_peaks":
                self.reflex.reset_peaks()
                self.loop_health.reset()
            elif not self.reflex.set(key, value):
                print(f"[reflex] ignoring bad setting {key}={value!r}")
        if "safety" in resets:
            self.reflex.cfg = fall_reflex_mod.clean_config({})
            self._notify("Fall reflex reset to defaults (Save to keep).")
        if "safety" in saves:
            self._save_async({"fall_reflex": dict(self.reflex.cfg)})
            self._notify("Fall reflex settings saved.")

        gu = settings.get("getup") or {}
        for key, value in gu.items():
            if key in ("start", "abort", "capture_reference"):
                continue
            merged = getup_mod.clean_config({**self.getup_cfg, key: value})
            merged["imu_standing_acc"] = self.getup_cfg["imu_standing_acc"]
            self.getup_cfg = merged
        if "enabled" in gu or "policy" in gu:
            if self.getup_cfg["enabled"]:
                self._load_getup_policy()
        if gu.get("capture_reference"):
            self._capture_standing_reference()
        if gu.get("abort") and self._activity == "getup":
            self._abort_getup("stopped by the operator")
        if gu.get("start"):
            self._start_getup(now)
        if "getup" in resets:
            keep = self.getup_cfg["imu_standing_acc"]
            self.getup_cfg = getup_mod.clean_config({"imu_standing_acc": keep})
            self._notify("Get-up reset to defaults (Save to keep).")
        if "getup" in saves:
            self._save_async({"getup": self._getup_config_dict()})
            self._notify("Get-up settings saved.")

        head = settings.get("head") or {}
        if head:
            self.head_life.set(head)
        if "head" in resets:
            self.head_life.cfg = head_life_mod.clean_config({})
            self._notify("Head motion reset to defaults (Save to keep).")
        if "head" in saves:
            self._save_async({"head_life": dict(self.head_life.cfg)})
            self._notify("Head motion saved.")

        rec = settings.get("recorder") or {}
        if rec.get("mark"):
            ok = self.blackbox.mark("mark")
            self._notify("Marked: the last seconds will be saved." if ok
                         else "Recorder is off or busy.")
        if "take" in rec:
            if rec["take"]:
                if self.blackbox.start_take("take_" + self.policy_manager.active_id):
                    self._notify("Recording a take. Stop it when done.", 3.0)
            elif self.blackbox.stop_take():
                self._notify("Take saved.")
        if "enabled" in rec:
            self.blackbox.cfg["enabled"] = bool(rec["enabled"])
        if "recorder" in saves:
            self._save_async({"flight_recorder": dict(self.blackbox.cfg)})

    def _suite_telemetry(self, now):
        """The extra /api/state entries of the stability suite."""
        gc = self.getup_cfg
        return {
            "activity": self._mode_name(),
            "soft": bool(self._soft),
            "head_life": self.head_life.public(),
            "tilt_deg": round(float(self.tilt.tilt_deg), 1),
            "policy": self.policy_manager.status(),
            "safety": self.reflex.public(),
            "getup": {
                "enabled": bool(gc["enabled"]), "auto": bool(gc["auto"]),
                "policy": gc["policy"], "ready": self._getup_policy is not None,
                "note": self._getup_note,
                "has_reference": gc["imu_standing_acc"] is not None,
                "attempts": self._getup_attempts,
                "run": self.getup.public() if self.getup is not None else None,
                "config": {k: gc[k] for k in getup_mod.LIMITS},
            },
            "blackbox": self.blackbox.public(),
            "loop": dict(self.loop_health.public(),
                         imu_glitches=int(getattr(self.imu, "glitches", 0) or 0)),
            "command_slew": self.shaper.public(),
            "rest": self.rest_pose.public(),
            "servo_voltages": dict(getattr(self.hwi, "servo_voltages", {}) or {}),
        }

    # ------------------------------------------------------ helpers (walk) ----
    def _walk_stick_active(self, c):
        """True if the operator is meaningfully pushing a walk stick (used to break
        out of playback). Deadzones scaled per command range to ignore drift."""
        return abs(c[0]) > 0.03 or abs(c[1]) > 0.04 or abs(c[2]) > 0.1

    def _couple_scanner(self, now):
        if self.scanner is None:
            return
        want = self.duck_config.projector and self.projector.on
        if want and not self.scanner.is_active:
            self.scanner.start(now)
        elif not want and self.scanner.is_active:
            self.scanner.stop()
        self.scanner.update(now)

    def _save_recording(self):
        try:
            self.recorder.save(WALK_RECORDING_PATH)
        except Exception as e:  # noqa: BLE001
            print(f"[walk] could not save recording: {e}")

    def _handle_record_playback(self, raw_commands, now):
        """DPAD-LEFT hold 3s = record; DPAD-RIGHT = play/stop. Mirrors head_puppet."""
        dl = getattr(self.buttons, "dpad_left", None)
        dr = getattr(self.buttons, "dpad_right", None)
        if dl is None or dr is None:
            return
        speaker = self.duck_config.speaker

        if self.recorder.state == "idle":
            if self._record_hold.update(dl.is_pressed, now):
                self.recorder.start_recording()
                if self.duck_config.projector:
                    # Baseline event so playback reproduces the lamp's current
                    # on/off state from frame 0 (the scanner sound follows the
                    # lamp, so it comes along automatically).
                    self.recorder.record_projector(self.projector.on)
                self._record_stop_armed = False
                cap_s = self.recorder.max_frames / self.control_freq
                print(f"● WALK RECORDING (max {cap_s:.0f}s) — tap DPAD-LEFT to stop")
                if speaker:
                    self.sounds.play("happy1.wav")
            elif dr.triggered:
                if self.recorder.has_recording():
                    self.recorder.start_playback()
                    print(f"▶ WALK PLAYBACK ({self.recorder.duration_s:.1f}s) — "
                          "looping; DPAD-RIGHT or a stick to stop")
                    if speaker:
                        self.sounds.play("beep1.wav")
                else:
                    print("(nothing recorded — hold DPAD-LEFT 3s first)")
        elif self.recorder.state == "recording":
            if not dl.is_pressed:
                self._record_stop_armed = True
            if self._record_stop_armed and dl.triggered:
                self.recorder.stop_recording()
                self._record_hold.reset_require_release()
                self._record_stop_armed = False
                self._save_recording()
                print(f"⏹ stored {self.recorder.duration_s:.1f}s. DPAD-RIGHT to play.")
                if speaker:
                    self.sounds.play("beep2.wav")
        elif self.recorder.state == "playing":
            if dr.triggered:
                self.recorder.request_stop(
                    raw_commands, self.phase_frequency_factor_offset,
                    self.buttons.LB.is_pressed,
                )
                print("■ stopping playback (ramping down)")

    def _commands_for_tick(self, raw_commands, sprint, now):
        """Pick this tick's command vector: recorded playback (through the live
        policy) or live. Recording captures the live stream; playback overrides
        the gait params too."""
        if self.recorder.state == "recording":
            still = self.recorder.record(
                raw_commands, self.phase_frequency_factor_offset, sprint
            )
            if not still:  # hit the length cap
                self._save_recording()
                print(f"⏹ walk recording full ({self.recorder.duration_s:.1f}s). "
                      "DPAD-RIGHT to play.")
                if self.duck_config.speaker:
                    self.sounds.play("beep2.wav")
            return raw_commands
        if self.recorder.state == "playing":
            # Operator grabs a stick -> hand live control back IMMEDIATELY (like
            # head_puppet). The graceful ramp-to-stand is reserved for the
            # DPAD-RIGHT "stop with no input" case (handled via request_stop).
            if self._walk_stick_active(raw_commands):
                self.recorder.state = "idle"
                return raw_commands
            pf = self.recorder.next_frame()
            if self.duck_config.speaker:
                for name in self.recorder.pop_sounds():
                    self.sounds.play(name)  # unknown names are safely ignored
            if self.duck_config.projector:
                for state in self.recorder.pop_projector():
                    if state != self.projector.on:
                        self.projector.switch()  # _couple_scanner follows the lamp
            if pf is not None:
                lc, off, spr, _fin = pf
                self.phase_frequency_factor_offset = off
                self.phase_frequency_factor = 1.3 if spr else 1.0
                return list(lc)
            return raw_commands
        return raw_commands

    def _apply_stability_governor(self, commands):
        """Ease the DRIVE commands (lin_vel_x/y, ang_vel — indices 0:3) when the duck
        is tipping, using the PREVIOUS tick's IMU. Head commands (3:7) are untouched.
        Mutates `commands` in place and returns the scale applied (1.0 = full speed;
        the governor is a pure pass-through while disabled). Stores the scale/severity
        on self for telemetry."""
        scale = 1.0
        if self.governor.enabled and self._last_imu is not None:
            # Lean comes from the gyro-fused estimate (the one the soft landing
            # uses), not from the bare accelerometer: while walking that mostly
            # shows foot impacts. Measured on a duck, the bare value read more than
            # 10 deg on 71 % of steps when the real lean did so on 1 %, so the
            # governor cut and jittered the stick commands almost all the time.
            if getattr(self.tilt, "up", None) is not None:
                tilt_deg = float(self.tilt.tilt_deg)
            else:
                pr = _accel_pitch_roll(self._last_imu.get("accelero"))
                tilt_deg = tilt_angle_deg(pr["pitch"], pr["roll"]) if pr else 0.0
            gyro = self._last_imu.get("gyro")
            rate = tilt_rate(gyro) if gyro is not None else 0.0
            scale = self.governor.update(tilt_deg, rate)
            for k in range(3):   # lin_vel_x, lin_vel_y, ang_vel only
                commands[k] = commands[k] * scale
        self._gov_scale = scale
        self._gov_severity = self.governor.severity
        return scale

    def _nudge_trim(self, axis, delta):
        """Adjust the LIVE IMU mounting trim (the worker re-reads it every loop, so
        this applies instantly). Clamped to +/-TRIM_LIMIT so a stuck button can't
        drive it wild. Not persisted until _save_imu_trim()."""
        if axis == "pitch":
            self.imu.pitch_trim = float(np.clip(self.imu.pitch_trim + delta,
                                                -TRIM_LIMIT, TRIM_LIMIT))
            val = self.imu.pitch_trim
        elif axis == "roll":
            self.imu.roll_trim = float(np.clip(self.imu.roll_trim + delta,
                                               -TRIM_LIMIT, TRIM_LIMIT))
            val = self.imu.roll_trim
        else:
            return
        print(f"[trim] {axis} = {val:+.4f} rad ({math.degrees(val):+.2f} deg)  "
              f"(RB+Y or web Save to persist)")

    def _save_imu_trim(self):
        """Persist the current live trim to ~/duck_config.json (backup-first, other
        fields untouched)."""
        from mini_bdx_runtime.duck_config import save_config_fields
        trim = {"pitch": float(self.imu.pitch_trim), "roll": float(self.imu.roll_trim)}
        backup = save_config_fields({"imu_trim": trim})
        self._imu_trim_saved = trim
        print(f"[trim] SAVED imu_trim={trim} (backup {backup})")

    # ------------------------------------------------- live web settings (walk) ---
    def _consume_web_settings(self):
        """Drain and apply the Web UI's live-settings edits + save requests. Cheap:
        one lock, then plain attribute writes (no hardware in the hot path)."""
        if self.web_bus is None:
            return
        settings, saves, resets = self.web_bus.consume_settings()
        self._consume_suite_settings(settings, saves, resets, time.time())
        # Stance first: a hold and an edit can arrive in the same tick.
        if (settings.get("stance") or {}).get("hold"):
            self._enter_stance_hold()
        for joint, value in (settings.get("joint_offsets") or {}).items():
            if not self.offset_tuner.set_target(joint, value):
                print(f"[offsets] refused {joint}={value!r} (hold stance first; "
                      "value must be a finite number)")
        if "joint_offsets" in resets and not self.offset_tuner.revert():
            print("[offsets] refused revert (hold stance first)")
        if "joint_offsets" in saves:
            self._save_joint_offsets()
        if settings.get("walk"):
            self._apply_walk_settings(settings["walk"])
        if settings.get("antenna"):
            self._apply_antenna_settings(settings["antenna"])
        if "walk" in resets:
            self._reset_walk_settings()
        if "imu_trim" in resets:
            self._reset_imu_trim()
        if "antenna" in resets:
            self.antenna_anim.set_enabled(True)
            self.antenna_anim.set_sync(False)
            print("[antennas] RESET to defaults (free-anim on, sync off)")
        if "walk" in saves:
            self._save_walk_settings()
        if "antenna" in saves:
            self._save_antenna_settings()

    def _apply_walk_settings(self, d):
        """Apply live walk-tuning edits. action_scale is ramped (not stepped)."""
        for key, v in d.items():
            try:
                if key == "action_scale":
                    self._action_scale_target = float(np.clip(float(v), 0.0, 0.6))
                elif key in ("gait_offset", "phase_frequency_factor_offset"):
                    self.phase_frequency_factor_offset = float(np.clip(float(v), -0.5, 0.5))
                elif key == "steer_trim":
                    self.steer_trim = float(np.clip(float(v), -STEER_TRIM_LIMIT,
                                                    STEER_TRIM_LIMIT))
                elif key == "velocity_clip":
                    self.velocity_clip = bool(v)
                elif key == "max_motor_velocity_rad_s":
                    self.max_motor_velocity = float(np.clip(float(v), 0.5, 12.0))
                elif key == "governor_enabled":
                    self.governor.enabled = bool(v)
                elif key == "governor_tilt_lo_deg":
                    self.governor.tilt_lo_deg = float(np.clip(float(v), 0.0, 45.0))
                elif key == "governor_tilt_hi_deg":
                    self.governor.tilt_hi_deg = float(np.clip(float(v), 0.0, 60.0))
                elif key == "governor_rate_lo":
                    self.governor.rate_lo = float(np.clip(float(v), 0.0, 20.0))
                elif key == "governor_rate_hi":
                    self.governor.rate_hi = float(np.clip(float(v), 0.0, 30.0))
                elif key == "governor_floor":
                    self.governor.floor = float(np.clip(float(v), 0.0, 1.0))
                elif key == "governor_smooth":
                    self.governor.smooth = float(np.clip(float(v), 0.01, 1.0))
                elif key.startswith("slew_"):
                    if not self.shaper.set(key[len("slew_"):], v):
                        print(f"[walk] ignoring bad setting {key}={v!r}")
            except (ValueError, TypeError, OverflowError):
                print(f"[walk] ignoring bad setting {key}={v!r}")

    def _governor_config_dict(self):
        g = self.governor
        return {
            "enabled": bool(g.enabled),
            "tilt_lo_deg": round(float(g.tilt_lo_deg), 3),
            "tilt_hi_deg": round(float(g.tilt_hi_deg), 3),
            "rate_lo": round(float(g.rate_lo), 3),
            "rate_hi": round(float(g.rate_hi), 3),
            "floor": round(float(g.floor), 3),
            "smooth": round(float(g.smooth), 3),
        }

    def _save_walk_settings(self):
        from mini_bdx_runtime.duck_config import save_config_fields
        # action_scale and the gait offset belong to the POLICY: the top-level keys
        # stay the stock walk's values; any other slot keeps its own under "policies".
        active = self.policy_manager.active_id
        live = self._live_tuning()
        if active == policy_slots.STOCK_ID:
            self._stock_tuning = dict(live)
        else:
            self.policy_manager.remember_tuning(active, live)
        self._session_tuning[active] = dict(live)
        stock = self._stock_tuning
        fields = {
            "action_scale": round(float(stock["action_scale"]), 4),
            "phase_frequency_factor_offset": round(
                float(stock["phase_frequency_factor_offset"]), 4),
            "velocity_clip": bool(self.velocity_clip),
            "max_motor_velocity_rad_s": round(float(self.max_motor_velocity), 3),
            "stability_governor": self._governor_config_dict(),
            "command_slew": self.shaper.public(),
            "steer_trim": round(float(stock.get("steer_trim", 0.0)), 4),
            "policies": self._policies_config(),
        }
        backup = save_config_fields(fields)
        print(f"[walk] SAVED walk tuning {fields} (backup {backup})")

    def _reset_walk_settings(self):
        """Load the known-good walk-tuning defaults live (shared source of truth;
        NOT persisted until the user hits Save). action_scale still ramps in."""
        d = WALK_TUNING_DEFAULTS
        self._action_scale_target = float(d["action_scale"])
        self.phase_frequency_factor_offset = float(d["phase_frequency_factor_offset"])
        self.steer_trim = 0.0
        self.velocity_clip = bool(d["velocity_clip"])
        self.max_motor_velocity = float(d["max_motor_velocity_rad_s"])
        g = d["stability_governor"]
        self.governor.enabled = bool(g["enabled"])
        self.governor.tilt_lo_deg = float(g["tilt_lo_deg"])
        self.governor.tilt_hi_deg = float(g["tilt_hi_deg"])
        self.governor.rate_lo = float(g["rate_lo"])
        self.governor.rate_hi = float(g["rate_hi"])
        self.governor.floor = float(g["floor"])
        self.governor.smooth = float(g["smooth"])
        self.shaper.cfg = dict(self.shaper.cfg, **d.get("command_slew", {}))
        print(f"[walk] RESET walk tuning to known-good defaults {d} (Save to persist)")

    def _reset_imu_trim(self):
        """Reset the live IMU mounting trim to the neutral 0 baseline (not persisted
        until Save). Applies instantly (the IMU worker re-reads it each loop)."""
        self.imu.pitch_trim = float(IMU_TRIM_DEFAULTS["pitch"])
        self.imu.roll_trim = float(IMU_TRIM_DEFAULTS["roll"])
        print("[trim] RESET imu trim to 0 (nudge to re-tune, Save to persist)")

    def _apply_antenna_settings(self, d):
        if "free_anim" in d:
            self.antenna_anim.set_enabled(bool(d["free_anim"]))
            print(f"[antennas] free animation {'ON' if self.antenna_anim.enabled else 'OFF'}")
        if "sync" in d:
            self.antenna_anim.set_sync(bool(d["sync"]))
            print(f"[antennas] sync {'ON (unison)' if self.antenna_anim.sync else 'OFF (independent)'}")

    def _save_antenna_settings(self):
        from mini_bdx_runtime.duck_config import save_config_fields
        fields = {"antenna_free_anim": bool(self.antenna_anim.enabled),
                  "antenna_sync": bool(self.antenna_anim.sync)}
        backup = save_config_fields(fields)
        print(f"[antennas] SAVED {fields} (backup {backup})")

    # ------------------------------------------- joint-offset page (stance hold) ---
    def _enter_stance_hold(self):
        """Pause and glide to the standing pose, the only pose where an offset error
        can be judged. A plain pause keeps the last mid-stride goal, which can't be."""
        if self._activity is not None or self._soft:
            self._notify("Not now: stand the duck up and resume first.")
            return
        self.paused = True
        if self.recorder.state == "playing":
            self.recorder.state = "idle"
        if not self.offset_tuner.holding:
            print("[offsets] HOLD STANCE: gliding to the standing pose. Support the duck.")
        self.offset_tuner.hold()
        # The pose is about to be init_pos, which is what action 0 means. Clearing the
        # history gives the policy the same clean start as a fresh boot on resume.
        self.last_action = np.zeros(self.num_dofs)
        self.last_last_action = np.zeros(self.num_dofs)
        self.last_last_last_action = np.zeros(self.num_dofs)

    def _hold_stance_tick(self):
        self.motor_targets = np.array(
            self.offset_tuner.glide(self.motor_targets, self.init_pos, self._tick_dt)
        )
        self.prev_motor_targets = self.motor_targets.copy()
        self.hwi.set_position_all(
            make_action_dict(self.motor_targets, list(self.hwi.joints.keys()))
        )

    def _save_joint_offsets(self):
        from mini_bdx_runtime.duck_config import save_config_fields
        if self.offset_tuner.save(save_config_fields):
            print("[offsets] SAVED joints_offsets (previous config backed up)")
        else:
            print(f"[offsets] {self.offset_tuner.error}")

    def _publish_telemetry(self, now):
        if self.web_bus is None or build_state is None:
            return
        # Voltage read = a ~0.2 s bus handoff -> only safe while PAUSED (servos hold).
        # While walking we hold the last reading; it refreshes each time you pause.
        allow_batt = (self.paused and self._activity is None
                      and self.duck_config.battery_servo_read)
        batt = (self.battery_mon.sample(now, self.hwi, allow_read=allow_batt)
                if self.battery_mon
                else {"voltage": None, "percent": None, "charging": None})
        flags = {
            "projector_on": bool(self.duck_config.projector and self.projector.on),
            "head_control": getattr(self.xbox_controller, "head_control_mode", False)
            if self.commands else False,
            "sprint": self.phase_frequency_factor > 1.0,
            "tracking": False,
        }
        self.web_bus.set_telemetry(build_state(
            mode="walk", paused=self.paused, battery=batt, loop_hz=self.loop_hz,
            temp_c=(self.battery_mon.temp_c if self.battery_mon else None),
            fallen=self.fall_detector.fallen,
            recording_state=self.recorder.state,
            recording_frames=len(self.recorder.frames),
            control_hz=self.control_freq, features=self._features, flags=flags,
            sounds=self._sound_names, uptime_s=now - self._start_t,
            imu=_accel_pitch_roll(self._last_imu.get("accelero") if self._last_imu else None),
            gait_offset=self.phase_frequency_factor_offset,
            imu_trim={"pitch": float(self.imu.pitch_trim),
                      "roll": float(self.imu.roll_trim),
                      "saved": self._imu_trim_saved},
            joint_offsets=self.offset_tuner.snapshot(),
            governor={"enabled": self.governor.enabled,
                      "scale": round(float(self._gov_scale), 3),
                      "severity": round(float(self._gov_severity), 3)},
            walk_params={
                "action_scale": round(float(self.action_scale), 3),
                "action_scale_target": round(float(self._action_scale_target), 3),
                "velocity_clip": bool(self.velocity_clip),
                "max_motor_velocity_rad_s": round(float(self.max_motor_velocity), 2),
                "gait_offset": round(float(self.phase_frequency_factor_offset), 3),
                "steer_trim": round(float(self.steer_trim), 3),
                "governor": self._governor_config_dict(),
            },
            antenna_anim=bool(self.antenna_anim.enabled),
            antenna_sync=bool(self.antenna_anim.sync),
            message=self._notice if now < self._notice_until else "",
            extra=self._suite_telemetry(now),
        ))

    def get_obs(self, imu_data=None):

        if imu_data is None:
            imu_data = self.imu.get_data()
            self._last_imu = imu_data

        dof_pos = self.hwi.get_present_positions(
            ignore=[
                "left_antenna",
                "right_antenna",
            ]
        )  # rad

        dof_vel = self.hwi.get_present_velocities(
            ignore=[
                "left_antenna",
                "right_antenna",
            ]
        )  # rad/s

        if dof_pos is None or dof_vel is None:
            return None

        if len(dof_pos) != self.num_dofs:
            print(f"ERROR len(dof_pos) != {self.num_dofs}")
            return None

        if len(dof_vel) != self.num_dofs:
            print(f"ERROR len(dof_vel) != {self.num_dofs}")
            return None

        cmds = self.last_commands

        feet_contacts = self.feet_contacts.get()

        obs = np.concatenate(
            [
                imu_data["gyro"],
                imu_data["accelero"],
                cmds,
                dof_pos - self.init_pos,
                dof_vel * 0.05,
                self.last_action,
                self.last_last_action,
                self.last_last_last_action,
                self.motor_targets,
                feet_contacts,
                self.imitation_phase,
            ]
        )

        return obs

    def start(self):
        kps = [self.pid[0]] * 14
        kds = [self.pid[2]] * 14

        # lower head kps
        kps[5:9] = [8, 8, 8, 8]

        self._kps_full = list(kps)
        self.hwi.set_kps(kps)
        self.hwi.set_kds(kds)
        self.hwi.turn_on()

        time.sleep(2)

    def get_phase_frequency_factor(self, x_velocity):

        max_phase_frequency = 1.2
        min_phase_frequency = 1.0

        # Perform linear interpolation
        freq = min_phase_frequency + (abs(x_velocity) / 0.15) * (
            max_phase_frequency - min_phase_frequency
        )

        return freq

    def run(self):
        i = 0
        try:
            print("Starting")
            start_t = time.time()
            self._start_t = start_t
            self._last_tick_t = None
            self.fall_detector.reset(start_t)
            while True:
                left_trigger = 0
                right_trigger = 0
                t = time.time()

                # loop-rate estimate for the Web UI
                if self._last_tick_t is not None:
                    self._tick_dt = t - self._last_tick_t
                    self.loop_hz = 0.9 * self.loop_hz + 0.1 * (
                        1.0 / max(1e-6, self._tick_dt)
                    )
                    # Only ticks that follow a controlling tick count: a pause
                    # sleeps 100 ms on purpose and is not a late tick.
                    if self._prev_tick_active:
                        self.loop_health.update(self._tick_dt)
                self._prev_tick_active = False
                self._last_tick_t = t
                self._sense(t)

                raw_commands = list(self.last_commands)
                sprint = False
                if self.commands:
                    raw_commands, self.buttons, left_trigger, right_trigger = (
                        self.xbox_controller.get_last_command()
                    )
                    raw_commands = list(raw_commands)

                    # RB = IMU-trim modifier. While held, the D-pad tunes the trim
                    # (up/down = pitch, right/left = roll) and Y saves it; gait and
                    # record/playback are suppressed so nothing double-fires.
                    trim_mode = self.buttons.RB.is_pressed
                    if trim_mode:
                        if self.buttons.dpad_up.triggered:
                            self._nudge_trim("pitch", TRIM_STEP)
                        if self.buttons.dpad_down.triggered:
                            self._nudge_trim("pitch", -TRIM_STEP)
                        if self.buttons.dpad_right.triggered:
                            self._nudge_trim("roll", TRIM_STEP)
                        if self.buttons.dpad_left.triggered:
                            self._nudge_trim("roll", -TRIM_STEP)
                        if self.buttons.Y.triggered:
                            self._save_imu_trim()
                    else:
                        if self.buttons.dpad_up.triggered:
                            self.phase_frequency_factor_offset += 0.05
                            print(
                                f"Phase frequency factor offset {round(self.phase_frequency_factor_offset, 3)}"
                            )
                        if self.buttons.dpad_down.triggered:
                            self.phase_frequency_factor_offset -= 0.05
                            print(
                                f"Phase frequency factor offset {round(self.phase_frequency_factor_offset, 3)}"
                            )

                    sprint = self.buttons.LB.is_pressed
                    self.phase_frequency_factor = 1.3 if sprint else 1.0

                    if self.buttons.X.triggered:
                        if self.duck_config.projector:
                            self.projector.switch()
                            # While recording, remember the RESULTING state so
                            # playback sets (not blind-toggles) the lamp.
                            self.recorder.record_projector(self.projector.on)

                    if trim_mode and self.buttons.B.triggered:
                        self._start_getup(t)        # RB + B = get up
                    elif self.buttons.B.triggered:
                        if (self.duck_config.speaker and self.sounds.ok
                                and self.sounds.sounds):
                            # Pick the sound HERE (not inside play_random_sound)
                            # so a recording can capture which one played and
                            # playback repeats it at the same tick.
                            name = random.choice(list(self.sounds.sounds.keys()))
                            self.sounds.play(name)
                            self.recorder.record_sound(name)

                    if self.duck_config.antennas:
                        # Manual trigger input (cross-wired as before) overrides; when
                        # idle the animator adds the free-animation wiggle, or rests.
                        ant_l, ant_r = self.antenna_anim.update(
                            t, manual_left=right_trigger, manual_right=left_trigger
                        )
                        self.antennas.set_position_left(ant_l)
                        self.antennas.set_position_right(ant_r)

                    if self.buttons.A.triggered:
                        self._on_pause_button(t)

                    # whole-body record / playback (DPAD left/right) -- suppressed
                    # while RB (trim mode) borrows the D-pad.
                    if not trim_mode:
                        self._handle_record_playback(raw_commands, t)

                # web IMU-trim tuner (drains alongside the gamepad; both nudge the
                # SAME live IMU trim and the same save path)
                if self.web_bus is not None:
                    pd, rd, save_trim = self.web_bus.consume_trim()
                    if pd:
                        self._nudge_trim("pitch", pd)
                    if rd:
                        self._nudge_trim("roll", rd)
                    if save_trim:
                        self._save_imu_trim()

                # live walk-tuning / antenna edits from the Web UI (apply + save)
                self._consume_web_settings()
                self._service_policy_switch(t)

                # Offsets only slew on ticks that write to the servos. Advancing them
                # during a plain pause would bank a jump for the next write.
                if not self.paused:
                    self.offset_tuner.release()
                if not self.paused or self.offset_tuner.holding:
                    self.offset_tuner.step(self._tick_dt)

                # ramp action_scale toward its web-set target (no sudden amplitude jump);
                # a policy swap fades it to zero first (see _service_policy_switch)
                scale_goal = self._action_scale_target
                if (self._activity == "switch" and not self.paused
                        and t >= self._switch_until):
                    scale_goal = 0.0
                ramp_down = ACTION_SCALE_RAMP
                if self.paused:
                    self.rest_pose.reset()
                elif self.rest_pose.resting and self._activity is None:
                    # Nobody is driving: let the amplitude fade out, which leaves
                    # the servos at the plain stance. Any wake fades it back in.
                    scale_goal = 0.0
                    ramp_down = REST_FADE_RAMP
                if self.action_scale != scale_goal:
                    self.action_scale += float(np.clip(
                        scale_goal - self.action_scale,
                        -ramp_down, ACTION_SCALE_RAMP))

                # scanner sound follows the LED even while paused; publish telemetry
                self._couple_scanner(t)
                self._publish_telemetry(t)

                # Get-up, fall reflex and the glide back to stance own the motors
                # while they run; the walk policy is not consulted on those ticks.
                if self._activity == "getup":
                    self._getup_tick(t)
                    self._end_tick(t)
                    continue
                if self._activity == "reflex":
                    self._reflex_tick(t)
                    self._end_tick(t)
                    continue
                if self._activity == "recover":
                    self._recover_tick(t)
                    self._end_tick(t)
                    continue

                if self.paused:
                    # Nothing more is recorded while paused, so write out any event
                    # still waiting for its "seconds after" (a fall ends paused).
                    self.blackbox.flush()
                    if self.offset_tuner.holding:
                        self._hold_stance_tick()
                        time.sleep(1 / self.control_freq)
                    else:
                        time.sleep(0.1)
                    continue

                # ---- fall detection -> auto-pause ----
                feet = self.feet_contacts.get()
                if self.fall_detector.update(feet, t):
                    self.paused = True
                    self._activity = None       # ends a balance / pending swap too
                    self._end_getup_take()
                    if self.recorder.state == "playing":
                        self.recorder.state = "idle"  # stop playback if we fell
                    print("⚠ FALL DETECTED — auto-paused. Right the duck, then "
                          "press A (or web Resume) to continue.")
                    self.blackbox.mark("fall", f"tilt {self.tilt.tilt_deg:.0f}")
                    if self.duck_config.speaker:
                        try:
                            self.sounds.play("beep2.wav")
                        except Exception:  # noqa: BLE001
                            pass
                    self._maybe_auto_getup(t)
                    time.sleep(0.1)
                    continue

                # ---- choose command source: recorded playback vs live control ----
                self.last_commands = self._commands_for_tick(raw_commands, sprint, t)

                # Standing still on purpose: balancing after a get-up, or settling
                # before a policy swap. The policy runs, the sticks are ignored.
                if self._activity == "balance":
                    self.last_commands = [0.0] * 7
                    if t >= self._balance_until:
                        self._activity = None
                        self._end_getup_take()
                        self._notify("Up again. You have control.")
                elif self._activity == "switch":
                    self.last_commands = [0.0] * 7
                    if t >= self._switch_until and self.action_scale <= 1e-9:
                        self._activity = None
                        self._swap_policy(t)

                # Rest pose (off unless enabled) and steering trim (0 unless set).
                driving = any(abs(float(c)) > 0.01 for c in self.last_commands[:3])
                gyro_now = (self._last_imu or {}).get("gyro")
                self.rest_pose.update(
                    driving, self.tilt.tilt_deg,
                    tilt_rate(gyro_now) if gyro_now is not None else 0.0, t,
                    allowed=self._activity is None)
                if driving and self.steer_trim:
                    self.last_commands[2] = float(np.clip(
                        float(self.last_commands[2]) + self.steer_trim, -1.0, 1.0))

                # Ramp the drive commands (no-op unless enabled), then throttle them
                # if the duck is tipping (no-op unless enabled).
                self.shaper.apply(self.last_commands, 1 / self.control_freq)
                self._apply_stability_governor(self.last_commands)

                # Past the point of no return? Stop driving the legs and land softly
                # (or, in observe mode, only log that it would have).
                event = self._update_reflex(t)
                if event == "would_trigger":
                    lt = self.reflex.last_trigger or {}
                    self.blackbox.mark("reflex_observed",
                                       f"tilt {lt.get('tilt')} predicted {lt.get('predicted')}")
                    print(f"[reflex] observe: would have triggered at tilt "
                          f"{lt.get('tilt')}° (predicted {lt.get('predicted')}°)")
                elif event in ("trigger", "trigger_soft"):
                    self._begin_reflex(t, event)
                    self._end_tick(t)
                    continue

                self._apply_head_life(t, driving)

                obs = self.get_obs(self._last_imu)
                if obs is None:
                    self.loop_health.note_read_failure()
                    continue

                self.imitation_i += 1 * (
                    self.phase_frequency_factor + self.phase_frequency_factor_offset
                )
                self.imitation_i = self.imitation_i % self.PRM.nb_steps_in_period
                self.imitation_phase = np.array(
                    [
                        np.cos(
                            self.imitation_i / self.PRM.nb_steps_in_period * 2 * np.pi
                        ),
                        np.sin(
                            self.imitation_i / self.PRM.nb_steps_in_period * 2 * np.pi
                        ),
                    ]
                )

                if self.save_obs:
                    self.saved_obs.append(obs)

                if self.replay_obs is not None:
                    if i < len(self.replay_obs):
                        obs = self.replay_obs[i]
                    else:
                        print("BREAKING ")
                        break

                action = self.policy.infer(obs)

                self.last_last_last_action = self.last_last_action.copy()
                self.last_last_action = self.last_action.copy()
                self.last_action = action.copy()

                # action = np.zeros(10)

                self.motor_targets = self.init_pos + action * self.action_scale

                # Optional per-tick slew clamp: bound how far any joint target can move
                # in one control dt (a safety net against a bad policy spike). Off by
                # default -> today's behaviour; enable via duck_config "velocity_clip".
                if self.velocity_clip:
                    max_delta = self.max_motor_velocity * (1 / self.control_freq)
                    self.motor_targets = np.clip(
                        self.motor_targets,
                        self.prev_motor_targets - max_delta,
                        self.prev_motor_targets + max_delta,
                    )

                if self.action_filter is not None:
                    self.action_filter.push(self.motor_targets)
                    filtered_motor_targets = self.action_filter.get_filtered_action()
                    if (
                        time.time() - start_t > 1
                    ):  # give time to the filter to stabilize
                        self.motor_targets = filtered_motor_targets

                self.prev_motor_targets = self.motor_targets.copy()

                head_motor_targets = self.last_commands[3:] + self.motor_targets[5:9]
                self.motor_targets[5:9] = head_motor_targets

                action_dict = make_action_dict(
                    self.motor_targets, list(self.hwi.joints.keys())
                )

                self.hwi.set_position_all(action_dict)

                i += 1

                took = time.time() - t
                self._rec_tick(t, pos=obs[13:27] + self.init_pos, vel=obs[27:41] / 0.05,
                               action=action, took=took)
                self._note_control_tick()
                # a stretch of plain walking earns the automatic get-up its tries back
                if self._activity is None and t - self._walk_since > 20.0:
                    self._auto_getups = 0
                took = time.time() - t
                # print("Full loop took", took, "fps : ", np.around(1 / took, 2))
                if (1 / self.control_freq - took) < 0:
                    print(
                        "Policy control budget exceeded by",
                        np.around(took - 1 / self.control_freq, 3),
                    )
                time.sleep(max(0, 1 / self.control_freq - took))

        except KeyboardInterrupt:
            self.blackbox.stop_take()
            self.blackbox.flush()
            if self.duck_config.antennas:
                self.antennas.stop()
            if self.duck_config.eyes:
                self.eyes.stop()
            if self.duck_config.projector:
                self.projector.stop()
            if self.scanner is not None:
                self.scanner.stop()
            if self.web_server is not None:
                self.web_server.stop()
            self.feet_contacts.stop()

        if self.save_obs:
            pickle.dump(self.saved_obs, open("robot_saved_obs.pkl", "wb"))
        print("TURNING OFF")


if __name__ == "__main__":
    # The clip must be over before pygame opens the speaker.
    if mode_announce is not None:
        mode_announce.finish(_mode_clip)
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--onnx_model_path", type=str, required=True)
    parser.add_argument(
        "--duck_config_path",
        type=str,
        required=False,
        default=f"{HOME_DIR}/duck_config.json",
    )
    # default None -> fall back to duck_config "action_scale", else 0.25 (see ctor).
    parser.add_argument("-a", "--action_scale", type=float, default=None)
    parser.add_argument("-p", type=int, default=30)
    parser.add_argument("-i", type=int, default=0)
    parser.add_argument("-d", type=int, default=0)
    parser.add_argument("-c", "--control_freq", type=int, default=50)
    parser.add_argument("--pitch_bias", type=float, default=0, help="deg")
    parser.add_argument(
        "--commands",
        action="store_true",
        default=True,
        help="external commands, keyboard or gamepad. Launch control_server.py on host computer",
    )
    parser.add_argument(
        "--save_obs",
        type=str,
        required=False,
        default=False,
        help="save the run's observations",
    )
    parser.add_argument(
        "--replay_obs",
        type=str,
        required=False,
        default=None,
        help="replay the observations from a previous run (can be from the robot or from mujoco)",
    )
    parser.add_argument("--cutoff_frequency", type=float, default=None)

    args = parser.parse_args()
    pid = [args.p, args.i, args.d]

    print("Done parsing args")
    rl_walk = RLWalk(
        args.onnx_model_path,
        duck_config_path=args.duck_config_path,
        action_scale=args.action_scale,
        pid=pid,
        control_freq=args.control_freq,
        commands=args.commands,
        pitch_bias=args.pitch_bias,
        save_obs=args.save_obs,
        replay_obs=args.replay_obs,
        cutoff_frequency=args.cutoff_frequency,
    )
    print("Done instantiating RLWalk")
    rl_walk.run()
