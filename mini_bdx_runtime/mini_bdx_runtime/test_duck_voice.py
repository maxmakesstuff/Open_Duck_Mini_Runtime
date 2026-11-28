"""
Standalone tests for the duck's sound system (duck_voice + sounds) with a fake mixer,
plus a check of the shipped voice library.

Run from this directory:  python3 test_duck_voice.py
"""
import json
import os
import random
import tempfile
import wave

import duck_voice as dv
import sounds as sm

HERE = os.path.dirname(os.path.abspath(__file__))
VOICE_DIR = os.path.join(HERE, "..", "assets", "voice")


class FakeSound:
    def __init__(self, mixer, path):
        if not os.path.isfile(path) or "broken" in path:
            raise RuntimeError("cannot load " + path)
        self.mixer, self.name, self.volume = mixer, os.path.basename(path), None

    def set_volume(self, v):
        self.volume = v

    def play(self):
        if self.mixer.fail_play:
            raise RuntimeError("device gone")
        self.mixer.played.append((self.name, self.volume))


class FakeMixer:
    def __init__(self, fail_init=False):
        self.fail_init, self.fail_play = fail_init, False
        self.played, self.stopped = [], 0

    def init(self):
        if self.fail_init:
            raise RuntimeError("no sound card")

    def Sound(self, path):
        return FakeSound(self, path)

    def stop(self):
        self.stopped += 1


def _folder(effects=("beep1.wav", "happy1.wav"), voice=None, manifest=True):
    d = tempfile.mkdtemp()
    for n in effects:
        open(os.path.join(d, n), "wb").close()
    voice = {"curious": 3, "notice": 2, "happy": 1} if voice is None else voice
    vd = os.path.join(d, "voice")
    os.makedirs(vd)
    cats = {}
    for cat, n in voice.items():
        cats[cat] = [{"file": f"{cat}_{i}.wav", "s": 1.5} for i in range(1, n + 1)]
        for e in cats[cat]:
            open(os.path.join(vd, e["file"]), "wb").close()
    if manifest:
        json.dump({"categories": cats}, open(os.path.join(vd, "voice.json"), "w"))
    return d


def _sounds(**kw):
    mixer = FakeMixer()
    folder = kw.pop("folder", None) or _folder(**{k: kw.pop(k) for k in ("effects", "voice", "manifest") if k in kw})
    return sm.Sounds(sound_directory=folder, mixer=mixer, rng=random.Random(1), **kw), mixer


# ------------------------------------------------------------------ DuckVoice ----
def _voice(cats=None, gap=3.0):
    played = []
    cats = cats or {"curious": [("c1", 1.0), ("c2", 1.0), ("c3", 1.0)], "notice": [("n1", 0.5)]}
    return dv.DuckVoice(cats, played.append, rng=random.Random(2), gap_s=gap), played


def test_say_plays_a_take_of_the_category():
    v, played = _voice()
    assert v.say("curious", 0.0) in ("c1", "c2", "c3") and len(played) == 1
    assert v.say("notice", 100.0) == "n1"
    assert v.say("nonsense", 200.0) is None and len(played) == 2


def test_it_does_not_talk_over_itself_or_without_a_pause():
    v, played = _voice(gap=3.0)
    assert v.say("curious", 10.0)
    assert v.busy(10.5) and not v.busy(11.1)
    assert v.say("notice", 10.5) is None                  # still talking
    assert v.say("notice", 12.0) is None                  # just finished: pause first
    assert v.say("notice", 14.1) == "n1"                  # 1 s clip + 3 s pause over
    assert len(played) == 2


def test_urgent_cuts_in_and_switching_off_silences_only_the_chatter():
    v, played = _voice()
    v.say("curious", 0.0)
    assert v.say("notice", 0.2, urgent=True) == "n1"
    v.enabled = False
    assert v.say("curious", 100.0) is None
    assert v.say("curious", 100.0, urgent=True) is not None


def test_takes_do_not_repeat_back_to_back():
    v, played = _voice()
    for i in range(60):
        v.say("curious", i * 10.0)
    assert all(a != b for a, b in zip(played, played[1:]))
    assert all(len({played[i], played[i + 1], played[i + 2]}) == 3 for i in range(len(played) - 2))
    assert set(played) == {"c1", "c2", "c3"}
    v.say("notice", 1000.0); v.say("notice", 2000.0)       # a single take may repeat
    assert played[-2:] == ["n1", "n1"]


def test_star_says_anything_and_a_failing_player_is_swallowed():
    v, played = _voice()
    assert {v.say("*", i * 10.0)[0] for i in range(40)} == {"c", "n"}

    def boom(name):
        raise RuntimeError("x")
    v = dv.DuckVoice({"a": [("a1", 1.0)]}, boom)
    assert v.say("a", 0.0) is None and not v.busy(0.1)
    v = dv.DuckVoice({"a": [("a1", 1.0)]}, lambda n: False)
    assert v.say("a", 0.0) is None


def test_manifest_loading_is_forgiving():
    d = _folder(voice={"curious": 2})
    cats = dv.load_manifest(os.path.join(d, "voice"))
    assert cats == {"curious": [("curious_1.wav", 1.5), ("curious_2.wav", 1.5)]}
    os.unlink(os.path.join(d, "voice", "curious_2.wav"))   # a listed file that is missing
    assert dv.load_manifest(os.path.join(d, "voice")) == {"curious": [("curious_1.wav", 1.5)]}
    open(os.path.join(d, "voice", "voice.json"), "w").write("{broken")
    assert dv.load_manifest(os.path.join(d, "voice")) == {"curious": [("curious_1.wav", 2.0)]}
    assert dv.load_manifest("/nonexistent/folder") == {}
    d = _folder(voice={"happy": 2}, manifest=False)        # no list at all: by file name
    assert sorted(dv.load_manifest(os.path.join(d, "voice"))) == ["happy"]


# --------------------------------------------------------------------- Sounds ----
def test_sounds_loads_effects_and_voice_and_plays_both():
    s, mixer = _sounds()
    assert s.ok and sorted(s.sounds) == ["beep1.wav", "happy1.wav"]
    assert s.public()["categories"] == ["curious", "happy", "notice"]
    assert s.play("beep1.wav") is True and mixer.played[-1][0] == "beep1.wav"
    name = s.say("curious", now=0.0)
    assert name.startswith("curious_") and mixer.played[-1][0] == name
    assert s.play(name) is True                            # a voice clip by its file name
    assert s.play("say:notice") is True and mixer.played[-1][0].startswith("notice_")
    assert s.play("nope.wav") is False and s.play(None) is False and s.play("say:nope") is False


def test_b_button_says_anything_or_falls_back_to_an_effect():
    s, mixer = _sounds()
    assert s.play_random(now=0.0) in s.voice.names()
    s, mixer = _sounds(voice={})
    assert not s.voice.ok and s.play_random(now=0.0) in ("beep1.wav", "happy1.wav")
    s, mixer = _sounds(voice={}, effects=())
    assert s.ok is False and s.play_random(now=0.0) is None


def test_volume_applies_to_everything_and_is_clamped():
    s, mixer = _sounds(volume=0.5)
    s.play("beep1.wav"); s.say("happy", now=0.0)
    assert [v for _, v in mixer.played] == [0.5, 0.5]
    s.set_volume(3)
    s.play("beep1.wav")
    assert mixer.played[-1][1] == 1.0
    s.set_volume("x"); s.set_volume(float("nan"))
    assert s.volume == 1.0
    s.set_volume(-1)
    assert s.volume == 0.0 and s.public()["volume"] == 0.0


def test_no_sound_card_or_broken_files_never_raise():
    d = _folder()
    s = sm.Sounds(sound_directory=d, mixer=FakeMixer(fail_init=True))
    assert s.ok is False and s.play("beep1.wav") is False and s.say("curious", now=0.0) is None
    s.set_volume(0.3); s.stop(); s.play_random(); s.play_happy()
    d = _folder(effects=("beep1.wav", "broken.wav"))
    s = sm.Sounds(sound_directory=d, mixer=FakeMixer())
    assert sorted(s.sounds) == ["beep1.wav"]
    s, mixer = _sounds()
    mixer.fail_play = True
    assert s.play("beep1.wav") is False and s.say("curious", now=0.0) is None
    assert sm.Sounds(sound_directory="/nonexistent", mixer=FakeMixer()).ok is False


# ------------------------------------------------------------ shipped library ----
def test_the_shipped_voice_library_is_complete_and_playable():
    cats = dv.load_manifest(VOICE_DIR)
    manifest = json.load(open(os.path.join(VOICE_DIR, "voice.json")))
    assert set(cats) == set(manifest["categories"]) and len(cats) >= 10
    assert sum(len(t) for t in cats.values()) == sum(len(e) for e in manifest["categories"].values())
    for cat, takes in cats.items():
        assert len(takes) >= 3, cat
        for name, seconds in takes:
            w = wave.open(os.path.join(VOICE_DIR, name))
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 22050), name
            assert abs(w.getnframes() / 22050 - seconds) < 0.02 and 0.3 < seconds < 4.5, name
    for e in (x for es in manifest["categories"].values() for x in es):
        assert e["mood"] not in ("whistle", "trot", "grumble", "angry", "bored", "cheer", "play")


def test_every_voice_category_an_animation_asks_for_exists():
    import head_life
    cats = dv.load_manifest(VOICE_DIR)
    for name, a in head_life.ANIMATIONS.items():
        if a["voice"]:
            assert a["voice"][0] in cats, (name, a["voice"])


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
    print(f"OK - {n} sound tests")
