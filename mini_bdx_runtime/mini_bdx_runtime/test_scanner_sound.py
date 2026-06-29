"""
Tests for the ScannerSound sequencer (no pytest, no pygame, no audio device).

ScannerSound's audio I/O is behind an injectable backend, so the looping /
crossfade / immediate-stop logic is verified here with a recording fake.

Run from this directory:  python3 test_scanner_sound.py
"""
from scanner_sound import ScannerSound


class FakeBackend:
    def __init__(self, lengths):
        self.lengths = lengths
        self.calls = []
        self._h = 0

    def count(self):
        return len(self.lengths)

    def length(self, i):
        return self.lengths[i]

    def play(self, i, fade_ms):
        handle = ("ch", self._h)
        self._h += 1
        self.calls.append(("play", i, fade_ms))
        return handle

    def fadeout(self, handle, ms):
        self.calls.append(("fadeout", handle, ms))

    def stop_all(self):
        self.calls.append(("stop_all",))

    def plays(self):
        return [c[1] for c in self.calls if c[0] == "play"]


def test_start_plays_first_sound_with_fade_in():
    b = FakeBackend([2.0, 2.0, 2.0])
    s = ScannerSound(b, crossfade_s=0.5)
    s.start(100.0)
    assert s.is_active
    assert b.calls[0] == ("play", 0, 500)


def test_inactive_update_is_noop():
    b = FakeBackend([2.0, 2.0, 2.0])
    s = ScannerSound(b, crossfade_s=0.5)
    s.update(999.0)
    assert b.calls == []
    assert not s.is_active


def test_no_advance_before_threshold():
    b = FakeBackend([2.0, 2.0, 2.0])
    s = ScannerSound(b, crossfade_s=0.5)
    s.start(0.0)
    # threshold = 2.0 - 0.5 = 1.5; updates before that must not advance
    s.update(1.0)
    s.update(1.49)
    assert b.plays() == [0]  # only the initial play


def test_advances_in_order_and_loops():
    b = FakeBackend([2.0, 2.0, 2.0])
    s = ScannerSound(b, crossfade_s=0.5)
    s.start(0.0)              # play 0 at t=0,  threshold 1.5
    s.update(1.5)             # -> play 1, fadeout 0 ; cur_start=1.5
    s.update(1.51)            # no advance yet
    s.update(3.0)             # -> play 2 (1.5+1.5)
    s.update(4.5)             # -> play 0 (loop back)
    assert b.plays() == [0, 1, 2, 0]


def test_advance_crossfades_previous_out():
    b = FakeBackend([2.0, 2.0, 2.0])
    s = ScannerSound(b, crossfade_s=0.5)
    s.start(0.0)
    prev_handle = s._cur_handle  # the channel for sound 0
    s.update(1.5)
    # advancing both starts the next (fade in) and fades the previous out
    assert ("play", 1, 500) in b.calls
    assert ("fadeout", prev_handle, 500) in b.calls


def test_stop_is_immediate_and_deactivates():
    b = FakeBackend([2.0, 2.0, 2.0])
    s = ScannerSound(b, crossfade_s=0.5)
    s.start(0.0)
    s.update(1.5)
    s.stop()
    assert not s.is_active
    assert ("stop_all",) in b.calls
    # further updates do nothing
    n = len(b.calls)
    s.update(5.0)
    assert len(b.calls) == n


def test_stop_when_inactive_is_noop():
    b = FakeBackend([2.0, 2.0, 2.0])
    s = ScannerSound(b, crossfade_s=0.5)
    s.stop()
    assert b.calls == []


def test_restart_after_stop_begins_at_first_sound():
    b = FakeBackend([2.0, 2.0, 2.0])
    s = ScannerSound(b, crossfade_s=0.5)
    s.start(0.0)
    s.update(1.5)   # now on sound 1
    s.stop()
    s.start(10.0)   # restart
    assert b.plays()[-1] == 0  # most recent play is sound 0 (restarts from the top)


def _run():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if _run() else 0)
