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

HOME_DIR = os.path.expanduser("~")

# DPAD-LEFT hold (s) to start a whole-body recording; walk_recording.pkl persists
# the last take across restarts (handy for events).
WALK_RECORD_HOLD_S = 3.0
WALK_RECORDING_PATH = os.path.join(os.getcwd(), "walk_recording.pkl")

# Live IMU-trim tuner (RB + D-pad, or the web card): step per nudge and the hard
# clamp so a stuck input can't drive the trim to a dangerous angle.
TRIM_STEP = 0.002     # rad (~0.11 deg) per D-pad tap / web +/- press
TRIM_LIMIT = 0.1      # rad (~5.7 deg) max |trim| on either axis


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
        self.policy = OnnxInfer(self.onnx_model_path, awd=True)

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

        # Scales
        # action_scale precedence: explicit CLI > duck_config > 0.25 default.
        if action_scale is not None:
            self.action_scale = action_scale
        elif self.duck_config.action_scale is not None:
            self.action_scale = float(self.duck_config.action_scale)
        else:
            self.action_scale = 0.25
        print(f"action_scale = {self.action_scale}")

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
        print(f"[trim] SAVED imu_trim={trim} (backup {backup})")

    def _publish_telemetry(self, now):
        if self.web_bus is None or build_state is None:
            return
        batt = (self.battery_mon.sample(now, self.hwi) if self.battery_mon
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
                      "roll": float(self.imu.roll_trim)},
            governor={"enabled": self.governor.enabled,
                      "scale": round(float(self._gov_scale), 3),
                      "severity": round(float(self._gov_severity), 3)},
        ))

    def get_obs(self):

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
                    self.loop_hz = 0.9 * self.loop_hz + 0.1 * (
                        1.0 / max(1e-6, t - self._last_tick_t)
                    )
                self._last_tick_t = t

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

                    if self.buttons.B.triggered:
                        if self.duck_config.speaker:
                            self.sounds.play_random_sound()

                    if self.duck_config.antennas:
                        self.antennas.set_position_left(right_trigger)
                        self.antennas.set_position_right(left_trigger)

                    if self.buttons.A.triggered:
                        self.paused = not self.paused
                        if self.paused:
                            print("PAUSE")
                        else:
                            print("UNPAUSE")
                            self.fall_detector.reset(t)  # re-arm after righting

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

                # scanner sound follows the LED even while paused; publish telemetry
                self._couple_scanner(t)
                self._publish_telemetry(t)

                if self.paused:
                    time.sleep(0.1)
                    continue

                # ---- fall detection -> auto-pause ----
                feet = self.feet_contacts.get()
                if self.fall_detector.update(feet, t):
                    self.paused = True
                    if self.recorder.state == "playing":
                        self.recorder.state = "idle"  # stop playback if we fell
                    print("⚠ FALL DETECTED — auto-paused. Right the duck, then "
                          "press A (or web Resume) to continue.")
                    if self.duck_config.speaker:
                        try:
                            self.sounds.play("beep2.wav")
                        except Exception:  # noqa: BLE001
                            pass
                    time.sleep(0.1)
                    continue

                # ---- choose command source: recorded playback vs live control ----
                self.last_commands = self._commands_for_tick(raw_commands, sprint, t)

                # Throttle drive velocity if the duck is tipping (no-op unless enabled).
                self._apply_stability_governor(self.last_commands)

                obs = self.get_obs()
                if obs is None:
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
                if self.duck_config.velocity_clip:
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
                # print("Full loop took", took, "fps : ", np.around(1 / took, 2))
                if (1 / self.control_freq - took) < 0:
                    print(
                        "Policy control budget exceeded by",
                        np.around(took - 1 / self.control_freq, 3),
                    )
                time.sleep(max(0, 1 / self.control_freq - took))

        except KeyboardInterrupt:
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
