"""
Telemetry assembly for the Web UI (pure logic + a throttled battery sampler).

Both the walk loop and the head-puppet loop publish a snapshot to the ControlBus
each tick via this module, so the phone sees a consistent shape (see
docs/webui-api.md). Servo voltage/temperature reads go over the single-owner
serial bus, so BatteryMonitor throttles them to a few seconds apart — negligible
next to the per-tick position reads the loop already does.
"""
# Absolute-package import is how the runtime loads this (mini_bdx_runtime.telemetry);
# the flat fallback is for the off-robot standalone tests that run from this dir.
try:
    from mini_bdx_runtime.battery import (
        estimate_percent, ChargeEstimator,
        DEFAULT_V_MIN, DEFAULT_V_MAX, DEFAULT_V_FULL,
    )
except ImportError:
    from battery import (
        estimate_percent, ChargeEstimator,
        DEFAULT_V_MIN, DEFAULT_V_MAX, DEFAULT_V_FULL,
    )


class BatteryMonitor:
    """Samples bus voltage + servo temp at most every `period_s` and derives
    percent + a heuristic charging flag. `hwi` may be None (-> all null)."""

    def __init__(self, battery_cfg=None, period_s=2.0):
        cfg = battery_cfg or {}
        self.v_min = float(cfg.get("v_min", DEFAULT_V_MIN))
        self.v_max = float(cfg.get("v_max", DEFAULT_V_MAX))
        self.charge = ChargeEstimator(v_full=float(cfg.get("v_full", DEFAULT_V_FULL)))
        self.period_s = period_s
        self._last_t = None
        self._cache = {"voltage": None, "percent": None, "charging": None}
        self._temp_c = None

    def sample(self, now, hwi):
        if self._last_t is not None and (now - self._last_t) < self.period_s:
            return self._cache
        self._last_t = now
        v = None
        try:
            v = hwi.get_present_voltage() if hwi is not None else None
            self._temp_c = hwi.get_present_temperature() if hwi is not None else None
        except Exception:  # noqa: BLE001 - never let telemetry break the loop
            v = None
        self._cache = {
            "voltage": v,
            "percent": estimate_percent(v, self.v_min, self.v_max),
            "charging": self.charge.update(now, v),
        }
        return self._cache

    @property
    def temp_c(self):
        return self._temp_c


def build_state(mode, paused, battery, loop_hz, temp_c, fallen,
                recording_state, recording_frames, control_hz,
                features, flags, sounds, uptime_s,
                imu=None, gait_offset=0.0, message="", imu_trim=None,
                governor=None):
    """Assemble the /api/state dict. Pure -> unit-tested off-robot."""
    seconds = round(recording_frames / control_hz, 2) if control_hz else 0.0
    return {
        "mode": mode,
        "paused": bool(paused),
        "battery": dict(battery),
        "imu": imu,                       # {"pitch":..,"roll":..} or None (attitude)
        "imu_trim": imu_trim,             # {"pitch":..,"roll":..} radians, or None
        "governor": governor,             # {"enabled":..,"scale":..,"severity":..} or None
        "loop_hz": round(float(loop_hz), 1),
        "temp_c": temp_c,
        "fallen": bool(fallen),
        "recording": {
            "state": recording_state,     # "idle" | "recording" | "playing"
            "frames": int(recording_frames),
            "seconds": seconds,
        },
        "features": dict(features),
        "flags": dict(flags),
        "gait_offset": round(float(gait_offset), 3),
        "sounds": list(sounds),
        "uptime_s": round(float(uptime_s), 1),
        "message": message,
    }
