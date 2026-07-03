"""
Battery estimation for the Web UI telemetry (pure stdlib -> unit-tested off-robot).

The bus voltage is read from a servo's present-voltage register (see
HWI.get_present_voltage). From that single number we derive a rough state-of-charge
percentage and a *heuristic* charging indicator. There is no dedicated charge-sense
pin, so "charging" is a best-effort guess (voltage sitting above the pack's full
level, or trending up) and is labelled as such in the UI.

Defaults assume a 2S LiPo (STS3215 7.4 V servos). Override per-robot via
duck_config `battery: {"v_min": .., "v_max": .., "v_full": ..}`.
"""

DEFAULT_V_MIN = 6.6      # 2S LiPo empty (3.3 V/cell) — stop here
DEFAULT_V_MAX = 8.4      # 2S LiPo full (4.2 V/cell)
DEFAULT_V_FULL = 8.5     # above this the pack is almost certainly on a charger


def estimate_percent(voltage, v_min=DEFAULT_V_MIN, v_max=DEFAULT_V_MAX):
    """Rough state-of-charge %, linearly mapped and clamped to [0, 100].

    Linear-in-voltage is crude for LiPo but fine for a "roughly how full" bar.
    Returns None if voltage is None/nonsensical."""
    if voltage is None:
        return None
    try:
        v = float(voltage)
    except (TypeError, ValueError):
        return None
    if v <= 0 or v_max <= v_min:
        return None
    pct = (v - v_min) / (v_max - v_min) * 100.0
    return int(round(_clamp(pct, 0.0, 100.0)))


class ChargeEstimator:
    """Heuristic 'is it charging?' from a short voltage history.

    Charging if EITHER the voltage sits above `v_full` (clearly on a charger), OR
    it has risen by more than `rise_threshold` volts across the recent window.
    Kept simple and honest — the UI shows this as a hint, not ground truth."""

    def __init__(self, v_full=DEFAULT_V_FULL, window_s=30.0, rise_threshold=0.05):
        self.v_full = v_full
        self.window_s = window_s
        self.rise_threshold = rise_threshold
        self._hist = []   # list of (now, voltage)

    def update(self, now, voltage):
        if voltage is None:
            return None
        self._hist.append((now, float(voltage)))
        cutoff = now - self.window_s
        self._hist = [(t, v) for (t, v) in self._hist if t >= cutoff]

        latest = self._hist[-1][1]
        if latest >= self.v_full:
            return True
        if len(self._hist) >= 2:
            oldest = self._hist[0][1]
            if latest - oldest >= self.rise_threshold:
                return True
        return False


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v
