"""Off-robot tests for loop_health.py.  Run:  python3 test_loop_health.py"""
from loop_health import LoopHealth


def test_on_time_ticks_are_not_late():
    h = LoopHealth(50)
    assert not any(h.update(0.02) for _ in range(100))
    assert h.late_pct == 0.0 and h.public()["late_total"] == 0
    assert abs(h.worst_ms - 20.0) < 1e-6


def test_late_ticks_are_counted_and_the_worst_is_kept():
    h = LoopHealth(50)
    for dt in [0.02] * 90 + [0.03] * 9 + [0.12]:
        h.update(dt)
    p = h.public()
    assert p["late_total"] == 10 and p["late_pct"] == 10.0 and p["worst_ms"] == 120.0


def test_small_jitter_is_tolerated():
    h = LoopHealth(50)
    assert not h.update(0.0249) and h.update(0.0251)


def test_percentage_is_over_a_sliding_window():
    h = LoopHealth(50, window_s=2.0)           # 100 ticks
    for _ in range(100):
        h.update(0.05)
    assert h.late_pct == 100.0
    for _ in range(100):
        h.update(0.02)
    assert h.late_pct == 0.0 and h.late_total == 100


def test_bad_dt_and_read_failures_and_reset():
    h = LoopHealth(50)
    assert not h.update(None) and not h.update(0.0) and not h.update(-1.0)
    assert h.ticks == 0
    h.note_read_failure()
    h.update(0.5)
    assert h.public()["read_failures"] == 1 and h.late_total == 1
    h.reset()
    assert h.public() == {"late_pct": 0.0, "late_total": 0, "worst_ms": 0.0,
                          "read_failures": 0, "ticks": 0, "imu_repeat_pct": 0.0,
                          "imu_longest_repeat": 0}


def test_repeated_imu_samples_are_counted_with_the_longest_run():
    h = LoopHealth(50)
    a, b = ((0.1, 0, 0), (0, 0, 9.8)), ((0.2, 0, 0), (0, 0, 9.8))
    runs = [h.note_imu(*s) for s in (a, b, b, b, b, a, a, b)]
    assert runs == [0, 0, 1, 2, 3, 0, 1, 0]
    p = h.public()
    assert p["imu_longest_repeat"] == 3 and p["imu_repeat_pct"] == 50.0
    assert h.note_imu(None, None) == 0 and h.imu_samples == 8, "junk is ignored"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} loop_health tests passed.")
