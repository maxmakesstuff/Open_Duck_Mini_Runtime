"""
Standalone tests for telemetry (no pytest; pure -> off-robot).

Run:  python3 test_telemetry.py
"""
from telemetry import BatteryMonitor, build_state


class FakeHWI:
    def __init__(self, v, t):
        self.v = v
        self.t = t
        self.reads = 0

    def get_present_voltage(self):
        self.reads += 1
        return self.v

    def get_present_temperature(self):
        return self.t


def test_battery_monitor_throttles_reads():
    hwi = FakeHWI(7.9, 40.0)
    bm = BatteryMonitor(period_s=2.0)
    c0 = bm.sample(0.0, hwi)
    bm.sample(0.5, hwi)   # within period -> cached, no new read
    bm.sample(1.9, hwi)
    assert hwi.reads == 1, "should not re-read within the throttle window"
    bm.sample(2.1, hwi)   # past window -> new read
    assert hwi.reads == 2
    assert c0["voltage"] == 7.9 and 0 <= c0["percent"] <= 100
    assert bm.temp_c == 40.0


def test_battery_monitor_none_hwi():
    bm = BatteryMonitor()
    c = bm.sample(0.0, None)
    assert c == {"voltage": None, "percent": None, "charging": None}
    assert bm.temp_c is None


def test_battery_monitor_survives_hwi_exception():
    class Boom:
        def get_present_voltage(self):
            raise RuntimeError("bus busy")

        def get_present_temperature(self):
            raise RuntimeError("bus busy")
    bm = BatteryMonitor()
    c = bm.sample(0.0, Boom())
    assert c["voltage"] is None  # degrades, does not raise


def test_build_state_shape():
    s = build_state(
        mode="walk", paused=False,
        battery={"voltage": 7.9, "percent": 68, "charging": False},
        loop_hz=49.66, temp_c=41.0, fallen=False,
        recording_state="recording", recording_frames=100, control_hz=50,
        features={"antennas": True}, flags={"sprint": True},
        sounds=["a.wav"], uptime_s=12.34, imu={"pitch": 1.0, "roll": 2.0},
        gait_offset=0.05, message="hi",
    )
    assert s["mode"] == "walk"
    assert s["loop_hz"] == 49.7
    assert s["recording"] == {"state": "recording", "frames": 100, "seconds": 2.0}
    assert s["imu"] == {"pitch": 1.0, "roll": 2.0}
    assert s["gait_offset"] == 0.05
    assert s["flags"]["sprint"] is True


def test_build_state_imu_none_ok():
    s = build_state("head_puppet", False, {"voltage": None, "percent": None, "charging": None},
                    60.0, None, False, "idle", 0, 60, {}, {}, [], 0.0)
    assert s["imu"] is None and s["temp_c"] is None


def test_build_state_governor_default_and_passthrough():
    # default: no governor arg -> field present as None (UI hides the readout)
    s = build_state("walk", False, {"voltage": None, "percent": None, "charging": None},
                    50.0, None, False, "idle", 0, 50, {}, {}, [], 0.0)
    assert s["governor"] is None
    # explicit governor payload is carried through verbatim
    gov = {"enabled": True, "scale": 0.6, "severity": 0.5}
    s2 = build_state("walk", False, {"voltage": None, "percent": None, "charging": None},
                     50.0, None, False, "idle", 0, 50, {}, {}, [], 0.0, governor=gov)
    assert s2["governor"] == gov


def test_build_state_new_fields_default_none_and_passthrough():
    base = ("walk", False, {"voltage": None, "percent": None, "charging": None},
            50.0, None, False, "idle", 0, 50, {}, {}, [], 0.0)
    s = build_state(*base)
    assert s["walk_params"] is None and s["camera"] is None and s["antenna_anim"] is None
    wp = {"action_scale": 0.2, "velocity_clip": True}
    cam = {"available": True, "controls": {"Brightness": 0.0}}
    s2 = build_state(*base, walk_params=wp, camera=cam, antenna_anim=True)
    assert s2["walk_params"] == wp and s2["camera"] == cam and s2["antenna_anim"] is True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} telemetry tests passed.")
