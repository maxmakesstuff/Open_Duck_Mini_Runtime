"""Off-robot tests for imu_sanity (run: python3 test_imu_sanity.py)."""
import imu_sanity as s


def test_normal_samples_pass():
    assert s.plausible([0.1, -0.2, 0.0], [0.3, -0.5, 9.8])
    # a hard fall and a hard foot strike are still real
    assert s.plausible([0.4, -8.3, -1.3], [9.4, 6.0, 33.4])


def test_the_corrupted_values_seen_on_the_duck_are_rejected():
    assert not s.plausible([-35.5, 0.4, 0.4], [2.3, -0.7, 12.8])
    assert not s.plausible([0.1, -35.53, -2.5], [2.0, -1.8, 8.1])
    assert not s.plausible([-0.2, 2.3, 35.1], [-0.5, 0.3, 9.0])
    assert not s.plausible([0.1, 0.2, 0.0], [-2.9, 18.4, -317.1])
    assert not s.plausible([0.1, 0.2, 0.0], [326.8, 0.0, 9.8])


def test_broken_input_is_rejected_not_raised():
    for g, a in (([0, 0], [0, 0, 9.8]), (None, [0, 0, 9.8]), ([0, 0, float("nan")], [0, 0, 9.8]),
                 ([0, 0, 0], [0, float("inf"), 9.8]), (["x", 0, 0], [0, 0, 9.8]),
                 ([0, 0, 0], [None, 0, 9.8])):
        assert not s.plausible(g, a), (g, a)


def test_numpy_arrays_work():
    import numpy as np
    assert s.plausible(np.array([0.1, 0.2, 0.3]), np.array([0.0, 0.0, 9.8]))
    assert not s.plausible(np.array([0.1, 35.2, 0.3]), np.array([0.0, 0.0, 9.8]))


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); n += 1
    print(f"OK - {n} imu_sanity tests passed")
