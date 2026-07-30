import json
from typing import Optional
import os
import shutil
import time

HOME_DIR = os.path.expanduser("~")


def save_config_fields(updates, config_json_path=f"{HOME_DIR}/duck_config.json",
                       backup=True):
    """Merge `updates` (a dict of top-level keys) into the config file, preserving
    every OTHER field, and write it back. Backs the file up first (timestamped) so a
    bad write is always recoverable. Returns the backup path (or None if there was no
    existing file). Used by the offset/IMU-trim tools to persist a single field
    without disturbing the rest of ~/duck_config.json."""
    try:
        cfg = json.load(open(config_json_path, "r"))
    except FileNotFoundError:
        cfg = {}
    backup_path = None
    if backup and os.path.exists(config_json_path):
        backup_path = f"{config_json_path}.{time.strftime('%Y%m%d-%H%M%S')}.bak"
        shutil.copy2(config_json_path, backup_path)
    cfg.update(updates)
    with open(config_json_path, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")
    return backup_path


class DuckConfig:

    def __init__(
        self,
        config_json_path: Optional[str] = f"{HOME_DIR}/duck_config.json",
        ignore_default: bool = False,
    ):
        """
        Looks for duck_config.json in the home directory by default.
        If not found, uses default values.
        """
        self.default = False
        try:
            self.json_config = (
                json.load(open(config_json_path, "r")) if config_json_path else {}
            )
        except FileNotFoundError:
            print(
                f"Warning : didn't find the config json file at {config_json_path}, using default values"
            )
            self.json_config = {}
            self.default = True

        if config_json_path is None:
            print("Warning : didn't provide a config json path, using default values")
            self.default = True

        if self.default and not ignore_default:
            print("")
            print("")
            print("")
            print("")
            print("======")
            print(
                "WARNING : Running with default values probably won't work well. Please make a duck_config.json file and set the parameters."
            )
            res = input("Do you still want to run ? (y/N)")
            if res.lower() != "y":
                print("Exiting...")
                exit(1)

        self.start_paused = self.json_config.get("start_paused", False)
        self.imu_upside_down = self.json_config.get("imu_upside_down", False)
        # Residual IMU mounting-tilt trim (radians), applied to accel+gyro on top of
        # the axis_remap. Default 0 = no change. Measure it with imu_health_check.py.
        self.imu_trim = self.json_config.get("imu_trim", {"pitch": 0.0, "roll": 0.0})

        # --- walk stability levers (all default to today's behaviour) ---
        # Policy residual scale. None -> the walk keeps its own default / CLI value.
        self.action_scale = self.json_config.get("action_scale", None)
        # Per-tick motor-target slew clamp (a safety net). Off by default.
        self.velocity_clip = bool(self.json_config.get("velocity_clip", False))
        self.max_motor_velocity_rad_s = float(
            self.json_config.get("max_motor_velocity_rad_s", 5.24)
        )
        # Tilt-based stability governor (eases drive commands when tipping). The dict
        # is passed to stability_governor.governor_from_config; {} -> disabled.
        self.stability_governor = self.json_config.get("stability_governor", {})
        self.phase_frequency_factor_offset = self.json_config.get(
            "phase_frequency_factor_offset", 0.0
        )

        # Phone Web UI (on by default; set "web_ui": false to disable). Served on
        # web_port; battery mapping is per-pack (2S LiPo defaults).
        self.web_ui = self.json_config.get("web_ui", True)
        self.web_port = int(self.json_config.get("web_port", 8080))
        self.battery = self.json_config.get("battery", {})

        # Ear-antenna "free animation": a scripted idle wiggle (see antenna_anim.py).
        # On by default so the ears feel alive once the pigpio jitter fix removes the
        # accidental twitch; toggle it live from the Web UI (persists here on Save).
        self.antenna_free_anim = bool(self.json_config.get("antenna_free_anim", True))
        # Persisted libcamera controls for the head-puppet live camera view (exposure,
        # gain, brightness, ...). {} -> the camera's auto defaults. Tuned from the Web UI.
        self.camera_controls = self.json_config.get("camera_controls", {})

        expression_features = self.json_config.get("expression_features", {})

        self.eyes = expression_features.get("eyes", False)
        self.projector = expression_features.get("projector", False)
        self.antennas = expression_features.get("antennas", False)
        self.speaker = expression_features.get("speaker", False)
        self.microphone = expression_features.get("microphone", False)
        self.camera = expression_features.get("camera", False)

        # default joints offsets are 0.0
        self.joints_offset = self.json_config.get(
            "joints_offsets",
            {
                "left_hip_yaw": 0.0,
                "left_hip_roll": 0.0,
                "left_hip_pitch": 0.0,
                "left_knee": 0.0,
                "left_ankle": 0.0,
                "neck_pitch": 0.0,
                "head_pitch": 0.0,
                "head_yaw": 0.0,
                "head_roll": 0.00,
                "right_hip_yaw": 0.0,
                "right_hip_roll": 0.0,
                "right_hip_pitch": 0.0,
                "right_knee": 0.0,
                "right_ankle": 0.0,
            },
        )
