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


def test_request_stop_is_idempotent():
    # Calling request_stop every tick (e.g. a held button) must NOT restart the
    # ramp, or the deceleration would never complete.
    rec = WalkRecorder(control_hz=50, stop_ramp_s=0.1)  # 5-tick ramp
    rec.start_recording()
    for _ in range(20):
        rec.record([0.15, 0, 0, 0, 0, 0, 0], 0.0, False)
    rec.stop_recording()
    rec.start_playback()
    rec.next_frame()
    finished = False
    for _ in range(12):
        rec.request_stop([0.15, 0, 0, 0, 0, 0, 0], 0.0, False)  # spam it
        r = rec.next_frame()
        if r and r[3]:
            finished = True
            break
    assert finished, "ramp must still finish even when request_stop is spammed"
    assert rec.state == "idle"


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


def _record_n(rec, n, x=0.1):
    for _ in range(n):
        rec.record([x, 0, 0, 0, 0, 0, 0], 0.0, False)


def test_sound_recorded_at_current_frame_index():
    rec = WalkRecorder(control_hz=50)
    rec.start_recording()
    _record_n(rec, 1)                      # frame 0
    rec.record_sound("quack.wav")          # lands on the frame recorded THIS tick
    _record_n(rec, 1)                      # frame 1 (the one with the sound)
    _record_n(rec, 1)                      # frame 2
    rec.stop_recording()
    assert rec.sound_events == [(1, "quack.wav")]


def test_record_sound_ignored_unless_recording():
    rec = WalkRecorder(control_hz=50)
    rec.record_sound("quack.wav")          # idle -> dropped
    assert rec.sound_events == []
    rec.start_recording()
    _record_n(rec, 2)
    rec.stop_recording()
    rec.record_sound("quack.wav")          # idle again -> dropped
    assert rec.sound_events == []


def test_new_recording_clears_sound_events():
    rec = WalkRecorder(control_hz=50)
    rec.start_recording()
    rec.record_sound("a.wav")
    _record_n(rec, 1)
    rec.stop_recording()
    rec.start_recording()
    assert rec.sound_events == []


def test_stop_recording_drops_frameless_sound_event():
    # Sound pressed on the same tick recording stops (no frame appended after
    # it) must not leave a dangling out-of-range event.
    rec = WalkRecorder(control_hz=50)
    rec.start_recording()
    _record_n(rec, 2)
    rec.record_sound("late.wav")           # would land on frame 2 - never recorded
    rec.stop_recording()
    assert rec.sound_events == []


def test_playback_fires_sound_at_its_tick_and_on_loop():
    rec = WalkRecorder(control_hz=50)
    rec.start_recording()
    _record_n(rec, 1)
    rec.record_sound("quack.wav")
    _record_n(rec, 1)
    _record_n(rec, 1)
    rec.stop_recording()
    rec.start_playback()
    fired = []
    for _ in range(6):                     # 3 frames, twice around the loop
        rec.next_frame()
        fired.append(rec.pop_sounds())
    assert fired == [[], ["quack.wav"], [], [], ["quack.wav"], []]


def test_pop_sounds_empty_when_idle_and_consumed_once():
    rec = WalkRecorder(control_hz=50)
    assert rec.pop_sounds() == []          # idle, nothing pending
    rec.start_recording()
    rec.record_sound("a.wav")
    _record_n(rec, 1)
    rec.stop_recording()
    rec.start_playback()
    rec.next_frame()
    assert rec.pop_sounds() == ["a.wav"]
    assert rec.pop_sounds() == []          # consumed


def test_stop_ramp_fires_no_sounds():
    rec = WalkRecorder(control_hz=50, stop_ramp_s=0.1)
    rec.start_recording()
    for _ in range(10):
        rec.record_sound("a.wav")
        _record_n(rec, 1)
    rec.stop_recording()
    rec.start_playback()
    rec.next_frame()
    rec.pop_sounds()
    rec.request_stop([0.1, 0, 0, 0, 0, 0, 0], 0.0, False)
    while True:
        r = rec.next_frame()
        assert rec.pop_sounds() == [], "no sounds during the stop ramp"
        if r is None or r[3]:
            break


def test_save_load_roundtrips_sound_events():
    rec = WalkRecorder(control_hz=50)
    rec.start_recording()
    _record_n(rec, 1)
    rec.record_sound("quack.wav")
    _record_n(rec, 1)
    rec.stop_recording()
    path = os.path.join(tempfile.gettempdir(), "walk_rec_snd_test.pkl")
    rec.save(path)
    rec2 = WalkRecorder(control_hz=50)
    rec2.load(path)
    os.remove(path)
    assert rec2.sound_events == [(1, "quack.wav")]


def test_load_legacy_pickle_without_sound_events():
    import pickle
    path = os.path.join(tempfile.gettempdir(), "walk_rec_legacy_test.pkl")
    with open(path, "wb") as f:
        pickle.dump({"control_hz": 50.0, "frames": [[0.1] + [0.0] * 8]}, f)
    rec = WalkRecorder(control_hz=50)
    n = rec.load(path)
    os.remove(path)
    assert n == 1
    assert rec.sound_events == []


def test_load_sanitizes_sound_events():
    # Untrusted input: bad indices / names / shapes must be dropped, good kept.
    import pickle
    path = os.path.join(tempfile.gettempdir(), "walk_rec_dirty_test.pkl")
    with open(path, "wb") as f:
        pickle.dump({
            "control_hz": 50.0,
            "frames": [[0.1] + [0.0] * 8, [0.1] + [0.0] * 8],
            "sound_events": [
                (1, "good.wav"),      # kept
                (5, "oob.wav"),       # index out of range -> dropped
                (-1, "neg.wav"),      # negative index -> dropped
                (0, 123),             # non-string name -> dropped
                ("x", "bad.wav"),     # non-int index -> dropped
                (0,),                 # wrong shape -> dropped
            ],
        }, f)
    rec = WalkRecorder(control_hz=50)
    rec.load(path)
    os.remove(path)
    assert rec.sound_events == [(1, "good.wav")]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} walk_record tests passed.")
