"""Off-robot tests for flight_recorder.py.  Run:  python3 test_flight_recorder.py"""
import os
import shutil
import tempfile
import time

import numpy as np

import flight_recorder as fl


def _rec(**cfg):
    d = tempfile.mkdtemp(prefix="blackbox-")
    r = fl.FlightRecorder(50, cfg, log_dir=d, meta={"policy": "stock"}, threaded=False,
                          clock=lambda: 1_700_000_000.0)
    return r, d


def _tick(r, i):
    r.add(t=i * 0.02, dt=0.02, gyro=[i, 0, 0], acc=[0, 0, 9.81], commands=[0.1] * 7,
          pos=np.full(14, i), vel=np.zeros(14), action=np.zeros(14),
          targets=np.zeros(14), feet=[1, 0], tilt=float(i), mode=0)


def _files(d):
    return sorted(f for f in os.listdir(d) if f.endswith(".npz"))


def test_mark_saves_the_seconds_before_and_after():
    r, d = _rec(pre_s=2.0, post_s=1.0)
    try:
        for i in range(300):
            _tick(r, i)
        assert r.mark("fall", "tilt 60")
        assert _files(d) == [], "nothing is written until the post window has passed"
        for i in range(300, 349):
            _tick(r, i)
        assert _files(d) == []
        _tick(r, 349)
        files = _files(d)
        assert len(files) == 1 and files[0].endswith("_fall.npz")
        cols, meta = fl.load(os.path.join(d, files[0]))
        assert len(cols["t"]) == 150, "2 s before + 1 s after at 50 Hz"
        assert cols["tilt"][0] == 200 and cols["tilt"][-1] == 349
        assert cols["gyro"].shape == (150, 3) and cols["pos"].shape == (150, 14)
        assert meta["reason"] == "fall" and meta["note"] == "tilt 60"
        assert meta["policy"] == "stock" and meta["control_hz"] == 50
    finally:
        shutil.rmtree(d)


def test_ring_wraps_and_keeps_only_the_newest():
    r, d = _rec(pre_s=18.0, post_s=0.0)
    try:
        for i in range(2500):                   # 50 s into a 20 s ring
            _tick(r, i)
        r.mark("mark")
        _tick(r, 2500)
        cols, _ = fl.load(os.path.join(d, _files(d)[0]))
        assert len(cols["t"]) == 18 * 50 + 1
        assert cols["tilt"][-1] == 2500 and np.all(np.diff(cols["tilt"]) == 1), \
            "rows come out oldest first with no gap across the wrap"
    finally:
        shutil.rmtree(d)


def test_mark_early_in_a_session_saves_what_exists():
    r, d = _rec(pre_s=8.0, post_s=0.0)
    try:
        for i in range(10):
            _tick(r, i)
        r.mark("fall")
        _tick(r, 10)
        cols, _ = fl.load(os.path.join(d, _files(d)[0]))
        assert len(cols["t"]) == 11
    finally:
        shutil.rmtree(d)


def test_flush_writes_pending_events_on_shutdown():
    r, d = _rec(post_s=3.0)
    try:
        for i in range(50):
            _tick(r, i)
        r.mark("fall")
        r.flush()
        assert len(_files(d)) == 1
    finally:
        shutil.rmtree(d)


def test_take_records_start_to_stop():
    r, d = _rec()
    try:
        for i in range(20):
            _tick(r, i)
        assert r.start_take("take_stock") and r.taking
        assert not r.start_take("again")
        for i in range(20, 120):
            _tick(r, i)
        assert abs(r.take_seconds - 2.0) < 1e-9
        assert r.stop_take() and not r.taking and not r.stop_take()
        cols, meta = fl.load(os.path.join(d, _files(d)[0]))
        assert len(cols["t"]) == 100 and cols["tilt"][0] == 20
        assert meta["reason"] == "take_stock"
    finally:
        shutil.rmtree(d)


def test_take_stops_itself_at_the_cap():
    r, d = _rec(max_take_s=10.0)
    try:
        r.start_take()
        for i in range(600):
            _tick(r, i)
        assert not r.taking and len(_files(d)) == 1
        cols, _ = fl.load(os.path.join(d, _files(d)[0]))
        assert len(cols["t"]) == 500
    finally:
        shutil.rmtree(d)


def test_disabled_recorder_writes_nothing():
    r, d = _rec(enabled=False)
    try:
        for i in range(100):
            _tick(r, i)
        assert not r.mark("fall") and not r.start_take()
        r.flush()
        assert _files(d) == [] and r.count == 0
    finally:
        shutil.rmtree(d)


def test_wrong_width_or_unknown_field_never_raises():
    r, d = _rec(post_s=0.0)
    try:
        r.add(t=1.0, gyro=[1, 2], pos=None, nonsense=5, acc="x")
        r.mark("mark")
        r.add(t=2.0)
        cols, _ = fl.load(os.path.join(d, _files(d)[0]))
        assert list(cols["t"]) == [1.0, 2.0] and np.all(cols["gyro"] == 0)
    finally:
        shutil.rmtree(d)


def test_same_second_files_do_not_overwrite_each_other():
    r, d = _rec(post_s=0.0)
    try:
        for k in range(3):
            _tick(r, k)
            r.mark("fall")
            _tick(r, 100 + k)
        assert len(_files(d)) == 3
    finally:
        shutil.rmtree(d)


def test_old_files_are_pruned():
    r, d = _rec(post_s=0.0, max_files=5)
    try:
        for k in range(12):
            _tick(r, k)
            r.mark(f"m{k:02d}")
            _tick(r, 100 + k)
        assert len(_files(d)) == 5
    finally:
        shutil.rmtree(d)


def test_a_burst_of_marks_is_bounded():
    r, d = _rec(post_s=3.0)
    try:
        _tick(r, 0)
        assert [r.mark("fall") for _ in range(6)] == [True] * 4 + [False] * 2
    finally:
        shutil.rmtree(d)


def test_unwritable_folder_is_reported_not_raised():
    r = fl.FlightRecorder(50, {"post_s": 0.0}, log_dir="/dev/null/nope", threaded=False)
    _tick(r, 0)
    r.mark("fall")
    _tick(r, 1)
    assert "not saved" in r.last_error and r.saved == 0


def test_listing_and_safe_paths():
    r, d = _rec(post_s=0.0)
    try:
        _tick(r, 0)
        r.mark("fall")
        _tick(r, 1)
        open(os.path.join(d, "notes.txt"), "w").close()
        logs = fl.list_logs(d)
        assert len(logs) == 1 and logs[0]["name"].endswith("_fall.npz")
        assert logs[0]["bytes"] > 0
        name = logs[0]["name"]
        assert fl.log_path(name, d) == os.path.join(d, name)
        for bad in ("../" + name, "/etc/passwd", "notes.txt", ".hidden.npz", "", None,
                    "sub/x.npz", "missing.npz"):
            assert fl.log_path(bad, d) is None, bad
        assert fl.list_logs("/nonexistent") == []
    finally:
        shutil.rmtree(d)


def test_threaded_save_lands_on_disk():
    d = tempfile.mkdtemp(prefix="blackbox-")
    try:
        r = fl.FlightRecorder(50, {"post_s": 0.0}, log_dir=d)
        _tick(r, 0)
        r.mark("fall")
        _tick(r, 1)
        for _ in range(200):
            if _files(d):
                break
            time.sleep(0.01)
        assert len(_files(d)) == 1 and r.saved == 1
    finally:
        shutil.rmtree(d)


def test_add_is_cheap():
    r, d = _rec()
    try:
        start = time.perf_counter()
        for i in range(2000):
            _tick(r, i)
        per_tick = (time.perf_counter() - start) / 2000
        assert per_tick < 0.001, f"{per_tick * 1e6:.0f} us per tick"
    finally:
        shutil.rmtree(d)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} flight_recorder tests passed.")
