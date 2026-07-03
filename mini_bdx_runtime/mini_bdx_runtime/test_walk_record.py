"""
Standalone tests for walk_record (no pytest, pure stdlib -> runs off-robot).

Focus: the safety-critical bits — clamping untrusted frames, the stop-ramp that
decelerates to a stand, looping, and round-trip persistence.

Run from this directory:  python3 test_walk_record.py
"""
import os
import tempfile

from walk_record import (
    WalkRecorder, clamp_frame, commands_to_frame, frame_to_commands,
    FRAME_LEN, LIN_X_RANGE, ANG_RANGE, HEAD_YAW_RANGE,
)

EPS = 1e-9


def test_clamp_frame_bounds_everything():
    huge = [99] * FRAME_LEN
    c = clamp_frame(huge)
    assert c[0] == LIN_X_RANGE[1]
    assert c[2] == ANG_RANGE[1]
    assert c[5] == HEAD_YAW_RANGE[1]
    neg = [-99] * FRAME_LEN
    c = clamp_frame(neg)
    assert c[0] == LIN_X_RANGE[0]
    assert c[8] == 0.0  # sprint clamped to [0,1]


def test_clamp_frame_pads_short_and_truncates_long():
    assert len(clamp_frame([0.1])) == FRAME_LEN            # padded
    assert len(clamp_frame([0.0] * (FRAME_LEN + 5))) == FRAME_LEN  # truncated


def test_commands_roundtrip():
    lc = [0.1, -0.1, 0.5, 0.0, 0.2, -0.3, 0.1]
    frame = commands_to_frame(lc, 0.05, True)
    out_lc, off, sprint = frame_to_commands(frame)
    assert sprint is True
    assert abs(off - 0.05) < EPS
    for a, b in zip(out_lc, lc):
        assert abs(a - b) < EPS


def test_record_caps_at_max():
    rec = WalkRecorder(control_hz=50, max_record_s=0.1)  # 5 frames
    rec.start_recording()
    results = [rec.record([0, 0, 0, 0, 0, 0, 0], 0.0, False) for _ in range(10)]
    assert rec.state == "idle"
    assert len(rec.frames) == 5
    assert results[:4] == [True, True, True, True]
    assert results[4] is False  # the frame that hit the cap flips to idle


def test_playback_loops():
    rec = WalkRecorder(control_hz=50)
    rec.start_recording()
    for x in (0.1, 0.12, 0.14):
        rec.record([x, 0, 0, 0, 0, 0, 0], 0.0, False)
    rec.stop_recording()
    assert rec.start_playback()
    seen = []
    for _ in range(7):  # 3 frames, loops -> cursor wraps
        lc, off, spr, fin = rec.next_frame()
        seen.append(round(lc[0], 3))
    assert seen == [0.1, 0.12, 0.14, 0.1, 0.12, 0.14, 0.1]


def test_stop_ramp_decelerates_to_zero():
    rec = WalkRecorder(control_hz=50, stop_ramp_s=0.1)  # 5-tick ramp
    rec.start_recording()
    for _ in range(20):
        rec.record([0.15, 0, 1.0, 0, 0, 0, 0], 0.0, True)
    rec.stop_recording()
    rec.start_playback()
    rec.next_frame()  # advance once
    rec.request_stop([0.15, 0, 1.0, 0, 0, 0, 0], 0.0, True)
    xs = []
    finished = False
    for _ in range(10):
        r = rec.next_frame()
        if r is None:
            break
        lc, off, spr, fin = r
        xs.append(lc[0])
        if fin:
            finished = True
            break
    assert finished, "ramp must finish"
    assert rec.state == "idle"
    assert xs[0] > xs[-1], "velocity must decrease across the ramp"
    assert abs(xs[-1]) < 0.05, f"ends near zero, got {xs[-1]}"
    # monotonic non-increasing
    for a, b in zip(xs, xs[1:]):
        assert b <= a + EPS


def test_non_looping_end_ramps_down():
    rec = WalkRecorder(control_hz=50, stop_ramp_s=0.1, loop=False)
    rec.start_recording()
    for _ in range(3):
        rec.record([0.15, 0, 0, 0, 0, 0, 0], 0.0, False)
    rec.stop_recording()
    rec.start_playback()
    saw_finish = False
    for _ in range(20):
        r = rec.next_frame()
        if r is None:
            break
        if r[3]:
            saw_finish = True
    assert saw_finish, "a non-looping recording must end (finished=True) via the ramp"
    assert rec.state == "idle"


def test_next_frame_none_when_idle():
    rec = WalkRecorder(control_hz=50)
    assert rec.next_frame() is None


def test_save_load_reclamps():
    rec = WalkRecorder(control_hz=50)
    rec.frames = [[9, 9, 9, 9, 9, 9, 9, 9, 9]]  # deliberately out of range
    path = os.path.join(tempfile.gettempdir(), "walk_rec_test.pkl")
    rec.save(path)
    rec2 = WalkRecorder(control_hz=50)
    n = rec2.load(path)
    os.remove(path)
    assert n == 1
    assert rec2.frames[0] == clamp_frame([9] * 9), "loaded frames must be re-clamped"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} walk_record tests passed.")
