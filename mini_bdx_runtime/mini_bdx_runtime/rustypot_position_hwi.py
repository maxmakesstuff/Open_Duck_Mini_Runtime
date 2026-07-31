import time

import numpy as np
import rustypot
from mini_bdx_runtime.duck_config import DuckConfig


class HWI:
    def __init__(self, duck_config: DuckConfig, usb_port: str = "/dev/ttyACM0"):

        self.duck_config = duck_config

        # Order matters here
        self.joints = {
            "left_hip_yaw": 20,
            "left_hip_roll": 21,
            "left_hip_pitch": 22,
            "left_knee": 23,
            "left_ankle": 24,
            "neck_pitch": 30,
            "head_pitch": 31,
            "head_yaw": 32,
            "head_roll": 33,
            # "left_antenna": None,
            # "right_antenna": None,
            "right_hip_yaw": 10,
            "right_hip_roll": 11,
            "right_hip_pitch": 12,
            "right_knee": 13,
            "right_ankle": 14,
        }

        self.zero_pos = {
            "left_hip_yaw": 0,
            "left_hip_roll": 0,
            "left_hip_pitch": 0,
            "left_knee": 0,
            "left_ankle": 0,
            "neck_pitch": 0,
            "head_pitch": 0,
            "head_yaw": 0,
            "head_roll": 0,
            # "left_antenna":0,
            # "right_antenna":0,
            "right_hip_yaw": 0,
            "right_hip_roll": 0,
            "right_hip_pitch": 0,
            "right_knee": 0,
            "right_ankle": 0,
        }

        self.init_pos = {
            "left_hip_yaw": 0.002,
            "left_hip_roll": 0.053,
            "left_hip_pitch": -0.63,
            "left_knee": 1.368,
            "left_ankle": -0.784,
            "neck_pitch": 0.0,
            "head_pitch": 0.0,
            "head_yaw": 0,
            "head_roll": 0,
            # "left_antenna": 0,
            # "right_antenna": 0,
            "right_hip_yaw": -0.003,
            "right_hip_roll": -0.065,
            "right_hip_pitch": 0.635,
            "right_knee": 1.379,
            "right_ankle": -0.796,
        }

        self.joints_offsets = self.duck_config.joints_offset

        self.kps = np.ones(len(self.joints)) * 32  # default kp
        self.kds = np.ones(len(self.joints)) * 0  # default kd
        self.low_torque_kps = np.ones(len(self.joints)) * 2

        self.usb_port = usb_port
        self.io = rustypot.feetech(usb_port, 1000000)

    def set_kps(self, kps):
        self.kps = kps
        self.io.set_kps(list(self.joints.values()), self.kps)

    def set_kds(self, kds):
        self.kds = kds
        self.io.set_kds(list(self.joints.values()), self.kds)

    def set_kp(self, id, kp):
        self.io.set_kps([id], [kp])

    def turn_on(self):
        self.io.set_kps(list(self.joints.values()), self.low_torque_kps)
        print("turn on : low KPS set")
        time.sleep(1)

        self.set_position_all(self.init_pos)
        print("turn on : init pos set")

        time.sleep(1)

        self.io.set_kps(list(self.joints.values()), self.kps)
        print("turn on : high kps")

    def turn_off(self):
        self.io.disable_torque(list(self.joints.values()))

    def set_position(self, joint_name, pos):
        """
        pos is in radians
        """
        id = self.joints[joint_name]
        pos = pos + self.joints_offsets[joint_name]
        self.io.write_goal_position([id], [pos])

    def set_position_all(self, joints_positions):
        """
        joints_positions is a dictionary with joint names as keys and joint positions as values
        Warning: expects radians
        """
        ids_positions = {
            self.joints[joint]: position + self.joints_offsets[joint]
            for joint, position in joints_positions.items()
        }

        self.io.write_goal_position(
            list(self.joints.values()), list(ids_positions.values())
        )

    def get_present_positions(self, ignore=[]):
        """
        Returns the present positions in radians
        """

        try:
            present_positions = self.io.read_present_position(
                list(self.joints.values())
            )
        except Exception as e:
            print(e)
            return None

        present_positions = [
            pos - self.joints_offsets[joint]
            for joint, pos in zip(self.joints.keys(), present_positions)
            if joint not in ignore
        ]
        return np.array(np.around(present_positions, 3))

    # STS3215 present-voltage register is in 0.1 V units (see check_voltage.py).
    # If a future rustypot build already returns volts, set this to 1.0 (the
    # probe_battery.py script makes the right value obvious on-robot).
    VOLTAGE_SCALE = 0.1

    def get_present_voltage(self):
        """Mean bus voltage (V) read through the SAME rustypot connection the loop
        already owns (the serial bus is single-owner — no second connection). Best
        effort: returns None if this rustypot build doesn't expose the register, so
        telemetry degrades to 'n/a' instead of crashing the loop."""
        reader = getattr(self.io, "read_present_voltage", None)
        if reader is None:
            return None
        try:
            vals = reader(list(self.joints.values()))
        except Exception as e:  # noqa: BLE001
            print(f"[hwi] voltage read failed: {e}")
            return None
        vals = [float(v) * self.VOLTAGE_SCALE for v in vals if v is not None]
        if not vals:
            return None
        return round(sum(vals) / len(vals), 2)

    def get_present_temperature(self):
        """Hottest servo temperature (°C), or None if unavailable. Same guarded,
        single-connection approach as get_present_voltage."""
        reader = getattr(self.io, "read_present_temperature", None)
        if reader is None:
            return None
        try:
            vals = reader(list(self.joints.values()))
        except Exception as e:  # noqa: BLE001
            print(f"[hwi] temperature read failed: {e}")
            return None
        vals = [float(v) for v in vals if v is not None]
        if not vals:
            return None
        return round(max(vals), 1)

    def read_battery_handoff(self):
        """Read servo bus voltage (V, mean) + hottest temp (°C) via a brief BUS
        HANDOFF: this rustypot build can't read those registers, so we momentarily
        RELEASE the serial port, read them through pypot (which can), then ALWAYS
        re-acquire rustypot so the control loop keeps the bus.

        Costs ~0.2 s during which NO goal positions are sent — the servos simply hold
        their last goal (torque + internal PID stay on; verified: position unchanged
        across the handoff). So the CALLER must only invoke this when it's safe to skip
        writes briefly (paused / idle) — NEVER mid-stride. Returns (voltage, temp),
        each possibly None. Single-threaded use only (call it from the control loop)."""
        import gc
        ids = list(self.joints.values())
        voltage = temp = None
        try:
            self.io = None            # drop the rustypot handle -> releases the port
            gc.collect()
            time.sleep(0.02)
            from pypot.feetech import FeetechSTS3215IO
            pio = FeetechSTS3215IO(self.usb_port, baudrate=1000000, use_sync_read=True)
            try:
                vv = pio.get_present_voltage(ids)
                if vv:
                    voltage = round(sum(vv) / len(vv) * self.VOLTAGE_SCALE, 2)
                try:
                    tt = pio.get_present_temperature(ids)
                    if tt:
                        temp = round(max(tt), 1)
                except Exception:  # noqa: BLE001 - temp is a bonus, never block on it
                    temp = None
            finally:
                try:
                    pio.close()
                except Exception:  # noqa: BLE001
                    pass
                pio = None
                gc.collect()
                time.sleep(0.02)
        except Exception as e:  # noqa: BLE001
            print(f"[hwi] battery handoff read failed: {e}")
        finally:
            # ALWAYS re-acquire rustypot so control resumes, even if pypot errored.
            if self.io is None:
                try:
                    self.io = rustypot.feetech(self.usb_port, 1000000)
                except Exception:  # noqa: BLE001 - one retry; losing the bus is fatal
                    time.sleep(0.1)
                    self.io = rustypot.feetech(self.usb_port, 1000000)
                try:  # re-assert gains (servos keep them across reconnect; belt+braces)
                    self.io.set_kps(list(self.joints.values()), self.kps)
                    self.io.set_kds(list(self.joints.values()), self.kds)
                except Exception as e:  # noqa: BLE001
                    print(f"[hwi] re-apply gains after handoff failed: {e}")
        return voltage, temp

    def get_present_velocities(self, rad_s=True, ignore=[]):
        """
        Returns the present velocities in rad/s (default) or rev/min
        """
        try:
            present_velocities = self.io.read_present_velocity(
                list(self.joints.values())
            )
        except Exception as e:
            print(e)
            return None

        present_velocities = [
            vel
            for joint, vel in zip(self.joints.keys(), present_velocities)
            if joint not in ignore
        ]

        return np.array(np.around(present_velocities, 3))
