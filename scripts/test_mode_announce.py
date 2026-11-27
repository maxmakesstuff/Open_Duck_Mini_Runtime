"""
Standalone tests for mode_announce.

Run:  python3 test_mode_announce.py
"""
import json
import os
import subprocess
import tempfile
import wave

import mode_announce as ma


class FakeProc:
    def __init__(self, hang=False):
        self.hang, self.killed, self.waits = hang, False, 0

    def wait(self, timeout=None):
        self.waits += 1
        if self.hang and not self.killed:
            raise subprocess.TimeoutExpired("aplay", timeout)
        return 0

    def kill(self):
        self.killed = True


def _setup(speaker=True, clips=("walk_mode.wav", "headpuppet_mode.wav")):
    d = tempfile.mkdtemp()
    cfg = os.path.join(d, "duck_config.json")
    with open(cfg, "w") as f:
        json.dump({"expression_features": {"speaker": speaker}}, f)
    for c in clips:
        open(os.path.join(d, c), "wb").close()
    return d, cfg


def _start(mode, d, cfg):
    calls = []

    def popen(cmd, **kw):
        calls.append(cmd)
        return FakeProc()

    return ma.start(mode, assets_dir=d, config_path=cfg, popen=popen), calls


def test_each_mode_plays_its_own_clip():
    d, cfg = _setup()
    p, calls = _start("walk", d, cfg)
    assert p is not None and calls == [["aplay", "-q", os.path.join(d, "walk_mode.wav")]]
    p, calls = _start("head_puppet", d, cfg)
    assert p is not None and calls == [["aplay", "-q", os.path.join(d, "headpuppet_mode.wav")]]


def test_silent_without_speaker_or_config():
    d, cfg = _setup(speaker=False)
    assert _start("walk", d, cfg) == (None, [])
    assert _start("walk", d, os.path.join(d, "missing.json")) == (None, [])
    with open(cfg, "w") as f:
        f.write("{not json")
    assert _start("walk", d, cfg) == (None, [])
    with open(cfg, "w") as f:
        f.write("[]")
    assert _start("walk", d, cfg) == (None, [])


def test_silent_for_unknown_mode_or_missing_clip():
    d, cfg = _setup(clips=())
    assert _start("walk", d, cfg) == (None, [])
    d, cfg = _setup()
    assert _start("dance", d, cfg) == (None, [])


def test_missing_player_never_raises():
    d, cfg = _setup()

    def popen(cmd, **kw):
        raise FileNotFoundError("aplay")

    assert ma.start("walk", assets_dir=d, config_path=cfg, popen=popen) is None


def test_finish_waits_and_cuts_off_an_overrun():
    ma.finish(None)
    p = FakeProc()
    ma.finish(p)
    assert p.waits == 1 and not p.killed
    p = FakeProc(hang=True)
    ma.finish(p, timeout=0.01)
    assert p.killed


def test_shipped_clips_exist_and_are_short_wavs():
    for clip in ma.CLIPS.values():
        w = wave.open(os.path.join(ma.ASSETS, clip))
        assert w.getsampwidth() == 2 and w.getframerate() == 44100
        assert w.getnframes() / w.getframerate() < ma.WAIT_S - 1.0


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
    print(f"OK - {n} tests")
