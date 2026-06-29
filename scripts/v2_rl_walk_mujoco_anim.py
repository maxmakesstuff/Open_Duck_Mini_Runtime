"""
Alternate walk runtime with an idle-ANIMATION mode.

Same RL walking controller as v2_rl_walk_mujoco.py, plus a sticky "animation
mode": pressing a d-pad direction stops walking (the robot stands, still
balanced by the policy) and plays one of four data-driven idle animations
(head + antennas + sound + side lamp) from animations.json. Pushing the walk
stick instantly aborts the animation and resumes walking. The control loop
never stops, so walk -> stand -> animate -> walk is seamless and the robot is
balanced the whole time.

Safety: animations only ever drive the 4 head joints + antennas (legs are
policy-controlled). Head targets are hard-clamped to safe ranges and slew-rate
limited every tick (both modes), so no animation or mode transition can slam a
servo. See animation_player.py and docs/superpowers/specs/.
"""
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
from mini_bdx_runtime.animation_player import (
    AnimationPlayer,
    clamp_head_targets,
    slew_limit,
    DEFAULT_MAX_HEAD_VELOCITY,
)
from mini_bdx_runtime.scanner_sound import ScannerSound, PygameScannerBackend

import os

HOME_DIR = os.path.expanduser("~")

# Modes
WALK = "walk"
ANIMATION = "animation"

# Scanner-light loop sounds live here (out of the normal Sounds rotation).
# Relative path, resolved from scripts/ like the assets/ sounds dir.
SCANNER_SOUND_DIR = "../mini_bdx_runtime/assets/scanner/"

# Walk-stick magnitude (in scaled-command space) above which we treat the stick
# as "pushed" -> exit animation mode / resume walking. Per-axis (x, y, yaw).
WALK_STICK_DEADZONE = (0.02, 0.02, 0.05)

# Head joints occupy motor_targets[5:9] = neck_pitch, head_pitch, head_yaw, head_roll
HEAD_SLICE = slice(5, 9)


class RLWalk:
    def __init__(
        self,
        onnx_model_path: str,
        duck_config_path: str = f"{HOME_DIR}/duck_config.json",
        animations_path: str = "./animations.json",
        serial_port: str = "/dev/ttyACM0",
        control_freq: float = 50,
        pid=[30, 0, 0],
        action_scale=0.25,
        commands=False,
        pitch_bias=0,
        save_obs=False,
        replay_obs=None,
        cutoff_frequency=None,
        max_head_velocity=DEFAULT_MAX_HEAD_VELOCITY,
    ):

        self.duck_config = DuckConfig(config_json_path=duck_config_path)

        self.commands = commands
        self.pitch_bias = pitch_bias

        self.onnx_model_path = onnx_model_path
        self.policy = OnnxInfer(self.onnx_model_path, awd=True)

        self.num_dofs = 14
        self.max_motor_velocity = 5.24  # rad/s

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
        )

        self.feet_contacts = FeetContacts()

        # Scales
        self.action_scale = action_scale

        self.last_action = np.zeros(self.num_dofs)
        self.last_last_action = np.zeros(self.num_dofs)
        self.last_last_last_action = np.zeros(self.num_dofs)

        self.init_pos = list(self.hwi.init_pos.values())

        self.motor_targets = np.array(self.init_pos.copy())
        self.prev_motor_targets = np.array(self.init_pos.copy())

        self.last_commands = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

        self.paused = self.duck_config.start_paused

        self.command_freq = 20  # hz
        if self.commands:
            self.xbox_controller = XBoxController(self.command_freq)

        # Reference motion, but we only really need the length of one phase
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

        # --- Animation mode ---
        self.mode = WALK
        # Safety: max head movement per control tick (rad). Guards keyframe gaps
        # and WALK<->ANIMATION transitions so we never step a head servo hard.
        self.max_head_delta = max_head_velocity / self.control_freq
        # Head targets carried across ticks/modes for the slew limiter. init_pos
        # head is neutral (0,0,0,0).
        self.prev_head_targets = list(self.init_pos[HEAD_SLICE])

        known_sounds = None
        if self.duck_config.speaker:
            known_sounds = set(self.sounds.sounds.keys())
        self.anim_player = AnimationPlayer(animations_path, known_sounds=known_sounds)
        for w in self.anim_player.warnings:
            print(f"[animations] WARNING: {w}")
        print(f"[animations] dpad map: {self.anim_player.dpad_map}")

        # Scanner-light sound loop, toggled with the LED by the X button. Needs
        # the speaker; the LED itself is the projector. A missing scanner dir
        # just disables the sound (the LED still toggles).
        self.scanner_sound = None
        if self.duck_config.speaker:
            try:
                backend = PygameScannerBackend(SCANNER_SOUND_DIR)
                self.scanner_sound = ScannerSound(backend)
            except Exception as e:  # noqa: BLE001
                print(f"[scanner] disabled (could not load scanner sounds): {e}")

    def get_obs(self):

        imu_data = self.imu.get_data()

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

    def _dpad_triggered(self):
        """Return 'up'/'down'/'left'/'right' if a d-pad direction was just
        pressed (debounced), else None."""
        b = self.buttons
        if b.dpad_up.triggered:
            return "up"
        if b.dpad_down.triggered:
            return "down"
        if b.dpad_left.triggered:
            return "left"
        if b.dpad_right.triggered:
            return "right"
        return None

    def _walk_stick_active(self):
        """True when the walk stick is pushed past the deadzone (= intent to
        walk / leave animation mode)."""
        c = self.last_commands
        return (
            abs(c[0]) > WALK_STICK_DEADZONE[0]
            or abs(c[1]) > WALK_STICK_DEADZONE[1]
            or abs(c[2]) > WALK_STICK_DEADZONE[2]
        )

    def _toggle_scanner(self, now):
        """X pressed: toggle the scanner LED and its looping sound together.
        LED on -> start the lamp loop; LED off -> stop it immediately."""
        self.projector.switch()
        if self.scanner_sound is not None:
            if self.projector.on:
                self.scanner_sound.start(now)
            else:
                self.scanner_sound.stop()

    def _apply_animation_peripherals(self, anim):
        """Drive antennas/sound/lamp from the animation, each gated by its
        expression-feature flag (a robot without a given feature just skips it)."""
        if self.duck_config.antennas and anim["antennas"] is not None:
            left, right = anim["antennas"]
            self.antennas.set_position_left(left)
            self.antennas.set_position_right(right)
        if self.duck_config.speaker:
            for name in anim["sounds"]:
                self.sounds.play(name)
        if self.duck_config.projector and anim["lamp"] is not None:
            if anim["lamp"] != self.projector.on:
                self.projector.switch()

    def run(self):
        i = 0
        try:
            print("Starting")
            start_t = time.time()
            while True:
                left_trigger = 0
                right_trigger = 0
                t = time.time()

                if self.commands:
                    self.last_commands, self.buttons, left_trigger, right_trigger = (
                        self.xbox_controller.get_last_command()
                    )

                    # Global: pause toggle
                    if self.buttons.A.triggered:
                        self.paused = not self.paused
                        print("PAUSE" if self.paused else "UNPAUSE")

                    # Mode transitions.
                    # A fresh d-pad press enters/switches animation mode and wins
                    # over the stick-interrupt this tick (avoids flicker).
                    dpad_dir = self._dpad_triggered()
                    if dpad_dir is not None:
                        anim_name = self.anim_player.dpad_map.get(dpad_dir)
                        if anim_name and self.anim_player.has(anim_name):
                            self.mode = ANIMATION
                            self.anim_player.start(anim_name, t)
                            print(f"ANIMATION: {anim_name}")
                        else:
                            print(f"[animations] no animation for dpad '{dpad_dir}'")
                    elif self.mode == ANIMATION and self._walk_stick_active():
                        self.mode = WALK
                        print("WALK")

                    # LB sprint (only affects walking)
                    if self.buttons.LB.is_pressed:
                        self.phase_frequency_factor = 1.3
                    else:
                        self.phase_frequency_factor = 1.0

                    # X: toggle the scanner LED + its looping sound together.
                    if self.buttons.X.triggered and self.duck_config.projector:
                        self._toggle_scanner(t)
                    if self.buttons.B.triggered and self.duck_config.speaker:
                        self.sounds.play_random_sound()

                # Drive the scanner sound loop (runs regardless of mode/pause).
                if self.scanner_sound is not None:
                    self.scanner_sound.update(t)

                if self.paused:
                    time.sleep(0.1)
                    continue

                # In animation mode the robot stands: feed the policy a zero
                # command (same regime as releasing the stick while walking).
                if self.mode == ANIMATION:
                    self.last_commands = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

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

                self.motor_targets = self.init_pos + action * self.action_scale

                if self.action_filter is not None:
                    self.action_filter.push(self.motor_targets)
                    filtered_motor_targets = self.action_filter.get_filtered_action()
                    if (
                        time.time() - start_t > 1
                    ):  # give time to the filter to stabilize
                        self.motor_targets = filtered_motor_targets

                self.prev_motor_targets = self.motor_targets.copy()

                # ---- Head + expressive peripherals, per mode ----
                if self.mode == ANIMATION:
                    anim = self.anim_player.update(t)
                    if anim["head"] is not None:
                        desired_head = list(anim["head"])
                    else:
                        desired_head = list(self.init_pos[HEAD_SLICE])
                    self._apply_animation_peripherals(anim)
                else:
                    # Walk mode: human head overlay on top of the policy head,
                    # antennas driven by the triggers (cross-wired, as original).
                    desired_head = [
                        self.last_commands[3 + j] + self.motor_targets[5 + j]
                        for j in range(4)
                    ]
                    if self.duck_config.antennas:
                        self.antennas.set_position_left(right_trigger)
                        self.antennas.set_position_right(left_trigger)

                # ---- SAFETY: clamp to limits, then slew-limit vs previous ----
                desired_head = clamp_head_targets(desired_head)
                new_head = slew_limit(
                    self.prev_head_targets, desired_head, self.max_head_delta
                )
                self.prev_head_targets = list(new_head)
                self.motor_targets[HEAD_SLICE] = new_head

                action_dict = make_action_dict(
                    self.motor_targets, list(self.hwi.joints.keys())
                )

                self.hwi.set_position_all(action_dict)

                i += 1

                took = time.time() - t
                if (1 / self.control_freq - took) < 0:
                    print(
                        "Policy control budget exceeded by",
                        np.around(took - 1 / self.control_freq, 3),
                    )
                time.sleep(max(0, 1 / self.control_freq - took))

        except KeyboardInterrupt:
            if self.scanner_sound is not None:
                self.scanner_sound.stop()
            if self.duck_config.antennas:
                self.antennas.stop()
            if self.duck_config.eyes:
                self.eyes.stop()
            if self.duck_config.projector:
                self.projector.stop()
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
    parser.add_argument(
        "--animations_path",
        type=str,
        required=False,
        default="./animations.json",
        help="idle animation definitions (run from scripts/ so the default resolves)",
    )
    parser.add_argument("-a", "--action_scale", type=float, default=0.25)
    parser.add_argument("-p", type=int, default=30)
    parser.add_argument("-i", type=int, default=0)
    parser.add_argument("-d", type=int, default=0)
    parser.add_argument("-c", "--control_freq", type=int, default=50)
    parser.add_argument("--pitch_bias", type=float, default=0, help="deg")
    parser.add_argument(
        "--commands",
        action="store_true",
        default=True,
        help="external commands, keyboard or gamepad.",
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
        help="replay the observations from a previous run (robot or mujoco)",
    )
    parser.add_argument("--cutoff_frequency", type=float, default=None)
    parser.add_argument(
        "--max_head_velocity",
        type=float,
        default=DEFAULT_MAX_HEAD_VELOCITY,
        help="rad/s cap on head motion (animation safety slew limit)",
    )

    args = parser.parse_args()
    pid = [args.p, args.i, args.d]

    print("Done parsing args")
    rl_walk = RLWalk(
        args.onnx_model_path,
        duck_config_path=args.duck_config_path,
        animations_path=args.animations_path,
        action_scale=args.action_scale,
        pid=pid,
        control_freq=args.control_freq,
        commands=args.commands,
        pitch_bias=args.pitch_bias,
        save_obs=args.save_obs,
        replay_obs=args.replay_obs,
        cutoff_frequency=args.cutoff_frequency,
        max_head_velocity=args.max_head_velocity,
    )
    print("Done instantiating RLWalk")
    rl_walk.run()
