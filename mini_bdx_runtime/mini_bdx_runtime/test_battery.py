"""
Standalone tests for battery estimation (no pytest; pure -> off-robot).

Run:  python3 test_battery.py
"""
from battery import estimate_percent, ChargeEstimator, DEFAULT_V_MIN, DEFAULT_V_MAX


def test_percent_endpoints_and_clamp():
    assert estimate_percent(DEFAULT_V_MIN) == 0
    assert estimate_percent(DEFAULT_V_MAX) == 100
    assert estimate_percent(DEFAULT_V_MIN - 1.0) == 0     # clamped
    assert estimate_percent(DEFAULT_V_MAX + 1.0) == 100   # clamped
    mid = estimate_percent((DEFAULT_V_MIN + DEFAULT_V_MAX) / 2)
    assert 49 <= mid <= 51


def test_percent_none_and_bad():
    assert estimate_percent(None) is None
    assert estimate_percent(0) is None
    assert estimate_percent("nan-ish") is None
    assert estimate_percent(7.4, v_min=8.0, v_max=8.0) is None  # degenerate range


def test_charging_when_above_full():
    ce = ChargeEstimator(v_full=8.5)
    assert ce.update(0.0, 8.6) is True
    assert ce.update(1.0, 8.55) is True


def test_charging_when_rising():
    ce = ChargeEstimator(v_full=99.0, window_s=30.0, rise_threshold=0.05)
    assert ce.update(0.0, 7.40) is False
    # rises 0.1 V over the window -> charging
    assert ce.update(20.0, 7.50) is True


def test_not_charging_when_flat_or_falling():
    ce = ChargeEstimator(v_full=99.0, window_s=30.0, rise_threshold=0.05)
    ce.update(0.0, 7.60)
    assert ce.update(10.0, 7.58) is False   # slightly falling (discharge)
    assert ce.update(20.0, 7.55) is False


def test_charging_none_voltage():
    ce = ChargeEstimator()
    assert ce.update(0.0, None) is None


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} battery tests passed.")
