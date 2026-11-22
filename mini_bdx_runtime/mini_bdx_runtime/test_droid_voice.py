"""
Standalone tests for the droid_voice synthesizer (no pytest; numpy only -> runs off-robot).

Run from this directory:  python3 test_droid_voice.py
"""
import os
import tempfile
import time
import wave

import numpy as np

import droid_voice
from droid_voice import blocks, dsp, phrases, render

SR = dsp.SR


def _rng(seed=0):
    return np.random.default_rng(seed)


def _spectrum(y):
    mag = np.abs(np.fft.rfft(y * np.hanning(len(y))))
    return np.fft.rfftfreq(len(y), 1.0 / SR), mag


def _band_share(y, lo, hi):
    f, mag = _spectrum(y)
    p = mag ** 2
    return p[(f >= lo) & (f < hi)].sum() / p.sum()


def _peak_hz(y):
    f, mag = _spectrum(y)
    return f[np.argmax(mag)]


def _centroid(y):
    f, mag = _spectrum(y)
    return (f * mag ** 2).sum() / (mag ** 2).sum()


# ---- blocks ----------------------------------------------------------------
def test_every_block_renders_sane_audio():
    for name, fn in blocks.BLOCKS.items():
        for seed in range(5):
            y = fn(_rng(seed))
            assert y.ndim == 1 and y.dtype == np.float64, name
            assert np.all(np.isfinite(y)), name
            assert 0.05 <= len(y) / SR <= 4.0, (name, len(y) / SR)
            assert np.abs(y).max() > 0.05, name


def test_blocks_are_deterministic_per_seed_and_vary_across_seeds():
    for name, fn in blocks.BLOCKS.items():
        a, b, c = fn(_rng(3)), fn(_rng(3)), fn(_rng(4))
        assert np.array_equal(a, b), name
        assert len(a) != len(c) or not np.array_equal(a, c), name


def test_chatter_is_two_whistles_with_a_silent_gap_between():
    for seed in range(5):
        y = blocks.chatter(_rng(seed))
        assert 800 <= _peak_hz(y) <= 1300, _peak_hz(y)
        assert _band_share(y, 3300, 4600) >= 0.05
        assert _band_share(y, 1700, 2900) <= 0.02


def test_boopbip_is_carried_by_the_low_boop():
    assert 430 <= _peak_hz(blocks.boopbip(_rng(1))) <= 760


def test_bell_rings_at_its_fundamental():
    y = blocks.bell(_rng(1))
    assert abs(_peak_hz(y) - 2075) / 2075 < 0.02


def test_sweep_stays_out_of_the_painful_range():
    for seed in range(5):
        f, mag = _spectrum(blocks.sweep(_rng(seed)))
        assert mag[f > 2300].max() < mag.max() * 0.1   # -20 dB


def test_chat_syllable_accent_changes_the_onset_only():
    plain = blocks.chat_syl(_rng(2), 140, (845, 840), (3900, 3850))
    acc = blocks.chat_syl(_rng(2), 140, (845, 840), (3900, 3850), accent=True)
    assert len(plain) == len(acc)
    head, tail = slice(0, dsp.n(40)), slice(dsp.n(110), None)
    assert np.abs(plain[head] - acc[head]).max() > 0.05
    assert np.abs(plain[tail] - acc[tail]).max() < np.abs(plain[head] - acc[head]).max()


# ---- moods -----------------------------------------------------------------
MOOD_NAMES = ("happy", "hello", "curious", "excited", "sad", "beatbox", "alert", "yes", "no")


def test_all_planned_moods_exist():
    assert set(phrases.MOODS) == set(MOOD_NAMES)


def test_every_mood_is_safe_at_every_energy():
    for mood in MOOD_NAMES:
        for energy in (0.0, 0.5, 1.0):
            for seed in range(5):
                y = droid_voice.say(mood, seed, energy)
                assert np.all(np.isfinite(y)), (mood, energy, seed)
                assert np.abs(y).max() <= 0.72, (mood, energy, seed)
                assert 0.2 <= len(y) / SR <= 6.0, (mood, energy, seed, len(y) / SR)


def test_say_is_deterministic_and_seeds_differ():
    for mood in MOOD_NAMES:
        a, b, c = droid_voice.say(mood, 7, 0.5), droid_voice.say(mood, 7, 0.5), droid_voice.say(mood, 8, 0.5)
        assert np.array_equal(a, b), mood
        assert len(a) != len(c) or not np.array_equal(a, c), mood


def test_unseeded_say_gives_new_utterances():
    a, b = droid_voice.say("happy"), droid_voice.say("happy")
    assert len(a) != len(b) or not np.array_equal(a, b)


def test_unknown_mood_names_the_valid_ones():
    try:
        droid_voice.say("nope")
    except ValueError as e:
        assert "happy" in str(e) and "nope" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_unknown_block_names_the_valid_ones():
    try:
        droid_voice.block("nope")
    except ValueError as e:
        assert "chatter" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_energy_is_clamped():
    assert np.array_equal(droid_voice.say("happy", 1, 99), droid_voice.say("happy", 1, 1.0))
    assert np.array_equal(droid_voice.say("happy", 1, -5), droid_voice.say("happy", 1, 0.0))


def test_energy_speeds_things_up():
    for seed in range(5):
        slow, fast = droid_voice.say("beatbox", seed, 0.0), droid_voice.say("beatbox", seed, 1.0)
        assert len(fast) < len(slow), seed


def test_alert_is_short():
    for seed in range(8):
        assert len(droid_voice.say("alert", seed)) / SR < 0.8


def test_sad_is_darker_than_excited():
    sad = np.mean([_centroid(droid_voice.say("sad", s)) for s in range(6)])
    exc = np.mean([_centroid(droid_voice.say("excited", s)) for s in range(6)])
    assert sad < exc, (sad, exc)


def test_yes_rises_and_no_falls():
    for seed in range(5):
        for mood, sign in (("yes", 1), ("no", -1)):
            y = droid_voice.say(mood, seed)
            body = y[np.abs(y) > 0.02 * np.abs(y).max()]
            half = len(body) // 2
            first, second = _peak_hz(body[:half]), _peak_hz(body[half:])
            assert sign * (second - first) > 0, (mood, seed, first, second)


def test_neutral_energy_does_not_transpose():
    # energy 0.5 must leave the approved bell pitch exactly where it was tuned
    y = droid_voice.block("bell", 1)
    f, mag = _spectrum(y)
    assert abs(f[np.argmax(mag)] - 2075) / 2075 < 0.03


# ---- rendering -------------------------------------------------------------
def test_finish_normalises_and_pads():
    y = render.finish(np.sin(np.linspace(0, 900, SR // 2)) * 3.0)
    assert abs(np.abs(y).max() - 0.708) < 0.01
    assert y[0] == 0.0 and y[-1] == 0.0
    assert len(y) > SR // 2


def test_finish_survives_silence():
    y = render.finish(np.zeros(1000))
    assert np.all(np.isfinite(y)) and np.abs(y).max() == 0.0


def test_wav_is_mono_16bit_44100():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.wav")
        y = droid_voice.say("hello", 1)
        render.write_wav(p, y)
        with wave.open(p, "rb") as w:
            assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 44100)
            assert w.getnframes() == len(y)


def test_render_is_at_least_50x_real_time():
    droid_voice.say("happy", 0)  # warm-up
    audio = 0.0
    t0 = time.perf_counter()
    for mood in MOOD_NAMES:
        for seed in range(4):
            audio += len(droid_voice.say(mood, seed)) / SR
    wall = time.perf_counter() - t0
    assert audio / wall >= 50, f"only {audio / wall:.0f}x real time"


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for name, fn in tests:
        fn()
        print(f"ok  {name}")
    print(f"\nAll {len(tests)} droid_voice tests passed.")
