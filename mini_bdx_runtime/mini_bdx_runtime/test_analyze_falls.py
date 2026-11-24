"""Off-robot tests for scripts/analyze_falls.py.  Run:  python3 test_analyze_falls.py

Builds recordings with the real FlightRecorder from a simulated fall and a simulated
walk, then checks what the analysis says about them."""
import importlib.util
import math
import os
import shutil
import tempfile

import numpy as np

import fall_reflex
import flight_recorder as fl

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "analyze_falls", os.path.join(os.path.dirname(os.path.dirname(HERE)), "scripts",
                                  "analyze_falls.py"))
af = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(af)

G, DT = 9.81, 0.02


def _walk_tick(i, sway=8.0):
    a = math.radians(sway) * math.sin(2 * math.pi * 2 * i * DT)
    w = math.radians(sway) * 2 * math.pi * 2 * math.cos(2 * math.pi * 2 * i * DT)
    return (0.0, w, 0.0), (-G * math.sin(a), 0.0, G * math.cos(a))


def _record(d, reason, fall_at=None, seconds=6.0, sway=8.0, policy="stock"):
    r = fl.FlightRecorder(50, {"pre_s": 18, "post_s": 0}, log_dir=d,
                          meta={"policy": policy}, threaded=False)
    th, w = math.radians(5), 0.3
    for i in range(int(seconds / DT)):
        mode = 0
        if fall_at is not None and i >= fall_at:
            if th < math.radians(88):
                w += (G / 0.25) * math.sin(th) * DT
                th += w * DT
                gyro, acc = (0.0, w, 0.0), (0.0, 0.0, G * math.cos(th) - 0.25 * w * w)
            else:
                gyro, acc = (0.0, 0.0, 0.0), (-G, 0.0, 0.0)
                mode = 1 if i > fall_at + 150 else 0      # the old check pauses ~2.5 s later
        else:
            gyro, acc = _walk_tick(i, sway)
        r.add(t=i * DT, dt=DT, gyro=gyro, acc=acc, commands=[0.1, 0, 0, 0, 0, 0, 0], mode=mode)
    r.mark(reason)
    r.add(t=seconds, dt=DT, gyro=(0, 0, 0), acc=(-G, 0, 0) if fall_at else (0, 0, G),
          mode=1 if fall_at else 0)
    return os.path.join(d, sorted(os.listdir(d))[-1])


def test_a_fall_is_caught_with_a_lead_time_and_beats_the_old_check():
    d = tempfile.mkdtemp()
    try:
        path = _record(d, "fall", fall_at=100)
        cols, meta = fl.load(path)
        r = af.analyse(cols, meta, fall_reflex.clean_config({}))
        assert r["fell"] and r["lead_s"] is not None and 0.08 <= r["lead_s"] <= 0.5
        assert r["trigger_tilt"] < 45 and not r["false_alarm"]
        assert r["old_detect_s"] > 2.0, "the old check only reacts seconds later"
    finally:
        shutil.rmtree(d)


def test_plain_walking_is_not_a_fall_and_raises_no_alarm():
    d = tempfile.mkdtemp()
    try:
        cols, meta = fl.load(_record(d, "take_stock"))
        r = af.analyse(cols, meta, fall_reflex.clean_config({}))
        assert not r["fell"] and not r["false_alarm"] and r["lead_s"] is None
        assert 6 < r["tilt_peak"] < 12 and r["wobble"] > 0.5 and r["late_pct"] == 0.0
    finally:
        shutil.rmtree(d)


def test_too_low_thresholds_show_up_as_false_alarms():
    d = tempfile.mkdtemp()
    try:
        cols, meta = fl.load(_record(d, "take_stock", sway=14.0))
        low = fall_reflex.clean_config({"min_tilt_deg": 5, "predict_deg": 15, "hard_deg": 15})
        assert af.analyse(cols, meta, low)["false_alarm"]
        assert not af.analyse(cols, meta, fall_reflex.clean_config({}))["false_alarm"]
    finally:
        shutil.rmtree(d)


def test_too_high_thresholds_miss_the_fall():
    d = tempfile.mkdtemp()
    try:
        cols, meta = fl.load(_record(d, "fall", fall_at=100))
        high = fall_reflex.clean_config({"min_tilt_deg": 60, "predict_deg": 90, "hard_deg": 90})
        r = af.analyse(cols, meta, high)
        assert r["fell"] and (r["lead_s"] is None or r["lead_s"] < 0.06), \
            "nothing useful left: it fires at the floor or not at all"
    finally:
        shutil.rmtree(d)


def test_report_and_suggestion_run_over_a_folder():
    d = tempfile.mkdtemp()
    try:
        _record(d, "fall", fall_at=100)
        _record(d, "take_stock", sway=8.0)
        _record(d, "take_dk5426-walk", sway=5.0, policy="dk5426-walk")
        open(os.path.join(d, "broken.npz"), "w").write("not a recording")
        lines = []
        results = af.report(af.find_files([d]), fall_reflex.clean_config({}),
                            want_suggest=True, out=lines.append)
        text = "\n".join(lines)
        assert len(results) == 3 and "cannot read" in text
        assert "caught 1 of 1" in text and "false alarms: 0 of 2" in text
        assert "PER POLICY" in text and "dk5426-walk" in text
        assert "Suggested from this data: min_tilt_deg=" in text
        s = af.suggest(results, fall_reflex.clean_config({}))
        assert 10 <= s["min_tilt_deg"] <= 20 and s["predict_deg"] >= 30
    finally:
        shutil.rmtree(d)


def test_cli_rejects_unknown_settings_and_empty_input():
    d = tempfile.mkdtemp()
    try:
        assert af.main([d]) == 1
        try:
            af.main([d, "--set", "nonsense=1"])
            raise AssertionError("should have exited")
        except SystemExit as e:
            assert e.code == 2
    finally:
        shutil.rmtree(d)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} analyze_falls tests passed.")
