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

    def __init__(self, battery_cfg=None, period_s=None):
        cfg = battery_cfg or {}
        self.v_min = float(cfg.get("v_min", DEFAULT_V_MIN))
        self.v_max = float(cfg.get("v_max", DEFAULT_V_MAX))
        self.charge = ChargeEstimator(v_full=float(cfg.get("v_full", DEFAULT_V_FULL)))
        # Reading voltage now costs a ~0.2 s bus handoff (see HWI.read_battery_handoff),
        # so sample it less often. Config "period_s" overrides; default 5 s.
        self.period_s = float(period_s if period_s is not None
                              else cfg.get("period_s", 5.0))
        self._last_t = None
        self._cache = {"voltage": None, "percent": None, "charging": None}
        self._temp_c = None

    def _read(self, hwi):
        """(voltage, temp) via the bus-handoff reader if the HWI exposes one, else the
        legacy direct reads (which return None on rustypot builds that can't)."""
        handoff = getattr(hwi, "read_battery_handoff", None)
        if handoff is not None:
            return handoff()
        return hwi.get_present_voltage(), hwi.get_present_temperature()

    def sample(self, now, hwi, allow_read=True):
        """Return the battery snapshot. Actually reads (an expensive bus handoff) only
        when the throttle has elapsed AND `allow_read` is True — the caller passes
        allow_read only when it's safe to briefly stop driving the servos (paused /
        idle). Otherwise the last cached value is held."""
        if self._last_t is not None and (now - self._last_t) < self.period_s:
            return self._cache
        if hwi is None or not allow_read:
            # No hwi, or not safe to touch the bus now -> keep the last value. Don't
            # advance _last_t when merely unsafe, so we read as soon as it's allowed.
            if hwi is None:
                self._last_t = now
            return self._cache
        self._last_t = now
        v, t = None, None
        try:
            v, t = self._read(hwi)
        except Exception:  # noqa: BLE001 - never let telemetry break the loop
            v, t = None, None
        if t is not None:
            self._temp_c = t
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
                governor=None, walk_params=None, camera=None,
                antenna_anim=None, antenna_sync=None):
    """Assemble the /api/state dict. Pure -> unit-tested off-robot."""
    seconds = round(recording_frames / control_hz, 2) if control_hz else 0.0
    return {
        "mode": mode,
        "paused": bool(paused),
        "battery": dict(battery),
        "imu": imu,                       # {"pitch":..,"roll":..} or None (attitude)
        "imu_trim": imu_trim,             # {"pitch":..,"roll":..} radians, or None
        "governor": governor,             # {"enabled":..,"scale":..,"severity":..} or None
        "walk_params": walk_params,       # live walk-tuning values (walk mode) or None
        "camera": camera,                 # {"available":..,"controls":{..}} or None
        "antenna_anim": antenna_anim,     # free-animation on/off (bool) or None
        "antenna_sync": antenna_sync,     # ears-in-unison on/off (bool) or None
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
