#!/usr/bin/env python3
"""
duck_voice.py - OFF-ROBOT authoring tool for "droid speech" sound effects
in the style of Disney's BD-1 / BDX droids, for the Open Duck Mini robot.

This is a *dev-laptop* tool. It has ZERO robot dependencies and uses only
numpy + the Python standard library (`wave`, `argparse`, ...). Every filter is
implemented in pure numpy (no scipy/librosa/soundfile) so the tool always runs.

It produces 16-bit PCM mono WAV files at 44100 Hz to match the existing
Open Duck Mini assets in mini_bdx_runtime/assets/ so generated files drop in
alongside them (played on-robot by the `B` button via the `Sounds` class).

Two generators
--------------
1) synth   - PROCEDURAL droid-speech synthesis. Builds a short utterance as a
             sequence of pitched "syllables" with portamento glides, vibrato,
             random intonation steps, per-syllable formant coloring (vowels),
             ring modulation (metallic timbre), consonant-like noise transients
             and a rising ("question") or falling ("statement") global contour.
             Mood presets change pitch range, tempo and contour. Deterministic
             via --seed.

2) convert - VOICE CONVERSION. Robotizes an input voice .wav into BD-style
             speech: pitch shift up (length-preserving OLA time-stretch +
             resample), ring modulation, bitcrush/quantize, small-speaker
             band-pass, and amplitude "warble".

CLI examples
------------
    python3 duck_voice.py synth --mood happy --seed 3 --out out/happy_gen1.wav
    python3 duck_voice.py synth --mood curious --count 5 --out-dir out/
    python3 duck_voice.py convert --in myvoice.wav --semitones 7 --out out/me_as_duck.wav

Run `python3 duck_voice.py <synth|convert> -h` for the full flag list.
"""

import argparse
import math
import os
import sys
import wave

import numpy as np

# NOTE: every DSP block below is implemented in pure numpy + stdlib `wave`.
# scipy is intentionally NOT imported, so the tool runs on a bare numpy install.

SR = 44100  # sample rate of the existing asset set (and our output)


# ---------------------------------------------------------------------------
# WAV I/O (stdlib `wave` only)
# ---------------------------------------------------------------------------
def read_wav(path):
    """Read a WAV file into a mono float64 array in [-1, 1] plus its samplerate.

    Handles 8/16/24/32-bit PCM and multi-channel input (channels are averaged
    to mono).
    """
    with wave.open(path, "rb") as w:
        nch = w.getnchannels()
        sw = w.getsampwidth()
        sr = w.getframerate()
        nframes = w.getnframes()
        raw = w.readframes(nframes)

    if sw == 1:  # 8-bit PCM is unsigned
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    elif sw == 2:
        a = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
    elif sw == 3:  # 24-bit little-endian, packed 3 bytes/sample
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        vals = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        vals = np.where(vals & 0x800000, vals - (1 << 24), vals)
        a = vals.astype(np.float64) / (2 ** 23)
    elif sw == 4:
        a = np.frombuffer(raw, dtype=np.int32).astype(np.float64) / (2 ** 31)
    else:
        raise ValueError("Unsupported sample width: %d bytes" % sw)

    if nch > 1:
        a = a.reshape(-1, nch).mean(axis=1)
    return a, sr


def write_wav(path, x, sr=SR, target_dbfs=-3.0, drive=2.0):
    """Normalize to ~target dBFS, soft-clip, and write 16-bit mono PCM.

    `drive` sets how hard the tanh soft-clip is pushed (character vs. clean).
    """
    x = finalize(np.asarray(x, dtype=np.float64), target_dbfs=target_dbfs, drive=drive)
    pcm = np.clip(np.round(x * 32767.0), -32768, 32767).astype("<i2")
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def finalize(x, target_dbfs=-3.0, drive=2.0):
    """DC-block, soft-clip (tanh) and normalize to a target peak level."""
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return x
    x = x - np.mean(x)
    peak = np.max(np.abs(x))
    if peak > 0:
        x = x / peak * 0.95
    if drive and drive > 0:
        x = np.tanh(drive * x) / math.tanh(drive)  # soft-clip, unity-ish gain
    peak = np.max(np.abs(x))
    if peak > 0:
        target = 10.0 ** (target_dbfs / 20.0)
        x = x / peak * target
    return x


# ---------------------------------------------------------------------------
# DSP building blocks (pure numpy)
# ---------------------------------------------------------------------------
def resample_to(x, n_out):
    """Linear-interpolation resample of `x` to exactly `n_out` samples."""
    n_out = max(1, int(n_out))
    if len(x) == n_out:
        return x.astype(np.float64)
    if len(x) < 2:
        return np.full(n_out, x[0] if len(x) else 0.0)
    old = np.linspace(0.0, 1.0, len(x))
    new = np.linspace(0.0, 1.0, n_out)
    return np.interp(new, old, x)


def resample_sr(x, sr_in, sr_out):
    """Resample from sr_in to sr_out (changes length, preserves pitch)."""
    if sr_in == sr_out:
        return x.astype(np.float64)
    n_out = int(round(len(x) * sr_out / float(sr_in)))
    return resample_to(x, n_out)


def time_stretch(x, rate, win=2048):
    """Phase-vocoder time-stretch. `rate` > 1 lengthens, < 1 shortens; pitch is
    preserved.

    A "phase-vocoder-lite": STFT with a fixed analysis hop, per-bin true
    instantaneous frequency estimated from the wrapped phase difference, then
    resynthesis at a scaled hop with phase accumulation. This keeps tonal
    content (a voice) intelligible instead of the pitch-cancelling artifact that
    plain overlap-add produces.
    """
    x = np.asarray(x, dtype=np.float64)
    if abs(rate - 1.0) < 1e-6 or len(x) < win:
        return x.copy()

    ha = win // 4                       # analysis hop
    hs = max(1, int(round(ha * rate)))  # synthesis hop
    w = np.hanning(win)
    nbins = win // 2 + 1
    omega = 2.0 * np.pi * np.arange(nbins) / win   # bin center freq (rad/sample)

    n_frames = 1 + (len(x) - win) // ha
    out_len = hs * (n_frames - 1) + win
    out = np.zeros(out_len)
    norm = np.zeros(out_len)

    prev_phase = np.zeros(nbins)
    syn_phase = np.zeros(nbins)
    for i in range(n_frames):
        seg = x[i * ha:i * ha + win] * w
        X = np.fft.rfft(seg)
        mag = np.abs(X)
        phase = np.angle(X)
        if i == 0:
            syn_phase = phase.copy()
        else:
            dphi = phase - prev_phase - omega * ha       # heterodyned phase diff
            dphi = np.mod(dphi + np.pi, 2 * np.pi) - np.pi  # principal value
            true_freq = omega + dphi / ha                # rad/sample
            syn_phase = syn_phase + hs * true_freq
        prev_phase = phase
        grain = np.fft.irfft(mag * np.exp(1j * syn_phase), win) * w
        s = i * hs
        out[s:s + win] += grain
        norm[s:s + win] += w * w
    norm[norm < 1e-6] = 1e-6
    out = out / norm
    return out[:int(round(len(x) * rate))]


def pitch_shift(x, semitones, win=2048):
    """Pitch-shift by `semitones`, preserving length.

    Implemented the classic resample-based way: WOLA time-stretch by the pitch
    ratio, then linear-resample back to the original length.
    """
    if abs(semitones) < 1e-6:
        return np.asarray(x, dtype=np.float64)
    rate = 2.0 ** (semitones / 12.0)
    stretched = time_stretch(x, rate, win=win)
    return resample_to(stretched, len(x))


def _band_mask(freqs, lo, hi, tw_lo, tw_hi):
    """Cosine-tapered band mask in [0,1] for the given rfft frequency axis."""
    m = np.zeros_like(freqs)
    m[(freqs >= lo) & (freqs <= hi)] = 1.0
    if tw_lo > 0:
        idx = (freqs >= lo - tw_lo) & (freqs < lo)
        m[idx] = 0.5 - 0.5 * np.cos(np.pi * (freqs[idx] - (lo - tw_lo)) / tw_lo)
    if tw_hi > 0:
        idx = (freqs > hi) & (freqs <= hi + tw_hi)
        m[idx] = 0.5 + 0.5 * np.cos(np.pi * (freqs[idx] - hi) / tw_hi)
    return m


def bandpass(x, sr, lo, hi, taper=0.4):
    """Zero-phase FFT band-pass with smooth cosine edges."""
    n = len(x)
    if n < 4:
        return x
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    mask = _band_mask(f, lo, hi, lo * taper, hi * taper)
    return np.fft.irfft(X * mask, n)


def formant_shape(x, sr, formants, floor=0.06):
    """Color `x` with resonant formant peaks (Gaussian magnitude weighting).

    `formants` is a list of (center_hz, gain, bandwidth_hz). This is what turns
    a raw buzz into a vowel-like "voiced" sound.
    """
    n = len(x)
    if n < 4:
        return x
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    env = np.full_like(f, floor)
    for fc, gain, bw in formants:
        env += gain * np.exp(-0.5 * ((f - fc) / bw) ** 2)
    env /= env.max()
    return np.fft.irfft(X * env, n)


def ring_mod(x, sr, carrier_hz, depth, t=None):
    """Ring modulation: mixes the signal with signal*sin(carrier). The classic
    'talking robot' metallic timbre. depth in [0,1] sets the wet amount."""
    if t is None:
        t = np.arange(len(x)) / float(sr)
    car = np.sin(2.0 * np.pi * carrier_hz * t)
    return x * (1.0 - depth) + (x * car) * depth


def bitcrush(x, bits=8, downsample=1):
    """Quantize amplitude to `bits` and optionally sample-and-hold decimate."""
    x = np.asarray(x, dtype=np.float64)
    if bits and bits < 16:
        levels = 2 ** bits
        x = np.round(x * (levels / 2)) / (levels / 2)
    if downsample and downsample > 1:
        n = len(x)
        idx = np.clip((np.arange(n) // downsample) * downsample, 0, n - 1)
        x = x[idx]
    return x


def warble(x, sr, rate_hz, depth):
    """Amplitude 'warble'/tremolo: multiply by a slow LFO in [1-depth, 1]."""
    if depth <= 0:
        return x
    t = np.arange(len(x)) / float(sr)
    lfo = 1.0 - depth * (0.5 + 0.5 * np.sin(2.0 * np.pi * rate_hz * t))
    return x * lfo


def syllable_env(n, sr, attack, release, floor=0.0):
    """Raised-cosine attack / sustain / cosine release amplitude envelope."""
    env = np.ones(n)
    na = min(int(attack * sr), n // 2)
    nr = min(int(release * sr), n - na)
    if na > 0:
        env[:na] = 0.5 - 0.5 * np.cos(np.pi * np.arange(na) / na)
    if nr > 0:
        env[n - nr:] = 0.5 + 0.5 * np.cos(np.pi * np.arange(nr) / nr)
    if floor > 0:
        env = floor + (1.0 - floor) * env
    return env


# ---------------------------------------------------------------------------
# Procedural droid-speech synthesis
# ---------------------------------------------------------------------------
# Vowel formant table (F1, F2, F3 in Hz). Picked per-syllable to imply that the
# droid is articulating different vowels -> reads as speech, not beeps.
VOWELS = {
    "a": (800, 1200, 2600),
    "e": (500, 1900, 2600),
    "i": (320, 2400, 3100),
    "o": (500, 900, 2500),
    "u": (350, 800, 2400),
    "y": (420, 1600, 2400),
}
_FORMANT_GAINS = (1.0, 0.7, 0.4)
_FORMANT_BW = (100, 120, 150)

# Mood palette. Each preset shapes pitch range, tempo, contour and timbre.
#   pitch      : (lo, hi) Hz  base-pitch range for the utterance
#   syl        : (min, max)   number of syllables
#   syl_dur    : (min, max)   seconds per syllable
#   gap        : (min, max)   seconds of silence between syllables
#   glide      : max +/- semitones of within-syllable portamento
#   step       : std-dev (semitones) of the between-syllable intonation walk
#   contour    : global end-shift in semitones (+ rises "?", - falls ".")
#   vib_rate   : (min, max) Hz vibrato/warble rate
#   vib_depth  : vibrato depth as a fraction of pitch
#   harmonics  : source brightness (number of summed harmonics)
#   ring_hz    : ring-mod carrier (Hz) - lower = more metallic/gravelly
#   ring_depth : ring-mod wet amount
#   trem       : (rate_hz, depth) overall amplitude warble
#   cons_prob  : probability of a consonant-like noise transient per syllable
#   drive      : soft-clip drive at write time
MOODS = {
    "happy": dict(
        pitch=(520, 1300), syl=(3, 6), syl_dur=(0.08, 0.18), gap=(0.02, 0.06),
        glide=4.0, step=3.5, contour=+5.0, vib_rate=(6.0, 9.0), vib_depth=0.03,
        harmonics=12, ring_hz=95, ring_depth=0.22, trem=(9.0, 0.12),
        cons_prob=0.45, drive=2.0,
    ),
    "curious": dict(
        pitch=(420, 1050), syl=(2, 5), syl_dur=(0.09, 0.20), gap=(0.03, 0.08),
        glide=3.0, step=2.5, contour=+8.0, vib_rate=(5.0, 7.5), vib_depth=0.025,
        harmonics=10, ring_hz=85, ring_depth=0.24, trem=(6.0, 0.10),
        cons_prob=0.5, drive=1.8,
    ),
    "sad": dict(
        pitch=(240, 620), syl=(2, 4), syl_dur=(0.14, 0.30), gap=(0.04, 0.10),
        glide=2.0, step=1.8, contour=-7.0, vib_rate=(4.0, 6.0), vib_depth=0.035,
        harmonics=8, ring_hz=70, ring_depth=0.20, trem=(4.5, 0.16),
        cons_prob=0.3, drive=1.6,
    ),
    "alarm": dict(
        pitch=(720, 1750), syl=(4, 8), syl_dur=(0.05, 0.11), gap=(0.015, 0.04),
        glide=2.0, step=4.0, contour=0.0, vib_rate=(9.0, 13.0), vib_depth=0.02,
        harmonics=14, ring_hz=110, ring_depth=0.30, trem=(13.0, 0.18),
        cons_prob=0.55, drive=2.6,
    ),
    "affirmative": dict(
        pitch=(460, 1000), syl=(2, 4), syl_dur=(0.12, 0.22), gap=(0.03, 0.07),
        glide=3.0, step=2.0, contour=+4.0, vib_rate=(6.0, 8.0), vib_depth=0.025,
        harmonics=10, ring_hz=90, ring_depth=0.22, trem=(8.0, 0.10),
        cons_prob=0.4, drive=1.9,
    ),
    "negative": dict(
        pitch=(340, 780), syl=(2, 3), syl_dur=(0.14, 0.26), gap=(0.04, 0.09),
        glide=2.5, step=2.2, contour=-6.0, vib_rate=(5.0, 7.0), vib_depth=0.03,
        harmonics=9, ring_hz=75, ring_depth=0.24, trem=(5.5, 0.14),
        cons_prob=0.4, drive=1.8,
    ),
}


def _pick_formants(rng, brightness=1.0):
    """Choose a vowel and return its (fc, gain, bw) formant list."""
    key = rng.choice(list(VOWELS.keys()))
    f1, f2, f3 = VOWELS[key]
    jitter = lambda v: v * rng.uniform(0.92, 1.08)  # noqa: E731
    return [
        (jitter(f1), _FORMANT_GAINS[0], _FORMANT_BW[0]),
        (jitter(f2), _FORMANT_GAINS[1] * brightness, _FORMANT_BW[1]),
        (jitter(f3), _FORMANT_GAINS[2] * brightness, _FORMANT_BW[2]),
    ]


def make_syllable(rng, p, f_start, f_end, dur):
    """Render one voiced syllable with glide + vibrato + formants + ring-mod."""
    n = max(4, int(dur * SR))
    t = np.arange(n) / float(SR)

    # Portamento glide with an ease-in/out curve (cosine).
    ease = 0.5 - 0.5 * np.cos(np.pi * np.linspace(0.0, 1.0, n))
    f_glide = f_start + (f_end - f_start) * ease

    # Vibrato / warble on the pitch.
    vib_rate = rng.uniform(*p["vib_rate"])
    vib = p["vib_depth"] * f_glide * np.sin(2 * np.pi * vib_rate * t + rng.uniform(0, 2 * np.pi))
    f_inst = np.clip(f_glide + vib, 40.0, SR * 0.45)

    # Voiced source: band-limited harmonic stack (glottal-ish saw), built from
    # the instantaneous phase so pitch can glide continuously.
    phase = 2 * np.pi * np.cumsum(f_inst) / SR
    src = np.zeros(n)
    f_top = float(np.max(f_inst))
    for h in range(1, p["harmonics"] + 1):
        if f_top * h > SR * 0.45:
            break
        src += (1.0 / h) * np.sin(h * phase)

    # Amplitude envelope (attack/release), then formant vowel coloring.
    attack = min(0.02, dur * 0.35)
    release = min(0.05, dur * 0.5)
    env = syllable_env(n, SR, attack, release)
    y = src * env
    y = formant_shape(y, SR, _pick_formants(rng, brightness=rng.uniform(0.8, 1.3)))

    # Metallic ring modulation.
    y = ring_mod(y, SR, p["ring_hz"] * rng.uniform(0.9, 1.1), p["ring_depth"], t=t)

    # Optional consonant-like transient at the onset (short band-passed noise).
    if rng.random() < p["cons_prob"]:
        nb = min(int(SR * rng.uniform(0.004, 0.012)), n)
        noise = rng.normal(0, 1, nb)
        noise = bandpass(noise, SR, rng.uniform(1500, 3000), rng.uniform(4000, 6500))
        noise *= np.linspace(1.0, 0.0, nb) * 0.5 * np.max(np.abs(y) + 1e-9)
        y[:nb] += noise

    # Normalize the syllable so envelope/level is consistent across syllables.
    m = np.max(np.abs(y))
    if m > 0:
        y = y / m
    return y


def synth_utterance(mood, seed):
    """Build one full droid-speech utterance for a mood, deterministic by seed."""
    if mood not in MOODS:
        raise ValueError("Unknown mood '%s'. Choices: %s" % (mood, ", ".join(MOODS)))
    p = MOODS[mood]
    rng = np.random.default_rng(seed)

    n_syl = int(rng.integers(p["syl"][0], p["syl"][1] + 1))
    base = rng.uniform(*p["pitch"])
    lo, hi = p["pitch"][0] * 0.7, p["pitch"][1] * 1.3

    # Global contour applied across syllables (question rises, statement falls),
    # with a little random flip so a mood isn't perfectly predictable.
    contour = p["contour"]
    if rng.random() < 0.25:  # occasional variety
        contour *= rng.uniform(-0.4, 0.6)

    pieces = []
    for i in range(n_syl):
        frac = i / max(1, n_syl - 1)
        # Between-syllable intonation random walk + global contour trend.
        base *= 2.0 ** (rng.normal(0.0, p["step"]) / 12.0)
        base = float(np.clip(base, lo, hi))
        f0 = base * 2.0 ** ((contour * frac) / 12.0)
        f0 = float(np.clip(f0, lo, hi))

        # Within-syllable glide direction.
        glide = rng.uniform(-p["glide"], p["glide"])
        # Nudge the last syllable's glide to reinforce the global contour.
        if i == n_syl - 1:
            glide += math.copysign(min(abs(contour), 4.0), contour)
        f_start = f0
        f_end = float(np.clip(f0 * 2.0 ** (glide / 12.0), lo, hi))

        dur = rng.uniform(*p["syl_dur"])
        pieces.append(make_syllable(rng, p, f_start, f_end, dur))

        # Inter-syllable gap (skip after the last syllable).
        if i < n_syl - 1:
            gap = int(rng.uniform(*p["gap"]) * SR)
            if gap > 0:
                pieces.append(np.zeros(gap))

    y = np.concatenate(pieces) if pieces else np.zeros(int(0.2 * SR))

    # Overall tremolo warble for liveliness.
    tr_rate, tr_depth = p["trem"]
    y = warble(y, SR, tr_rate, tr_depth)

    # Gentle utterance-level fade so it never clicks on/off.
    y *= syllable_env(len(y), SR, 0.006, 0.02)
    return y, p["drive"]


# ---------------------------------------------------------------------------
# Voice conversion
# ---------------------------------------------------------------------------
def convert_voice(x, sr_in, semitones=7.0, carrier_hz=80.0, ring_depth=0.5,
                  bits=7, downsample=2, band_lo=300.0, band_hi=3200.0,
                  warble_hz=9.0, warble_depth=0.18):
    """Robotize a voice signal into BD-style droid speech.

    Pipeline: resample to 44100 -> pitch-shift up -> band-pass to a small
    speaker range -> ring modulation -> bitcrush/quantize -> amplitude warble.
    Returns the processed signal (unnormalized; write_wav does final gain).
    """
    x = resample_sr(x, sr_in, SR)
    x = pitch_shift(x, semitones)
    x = bandpass(x, SR, band_lo, band_hi)
    x = ring_mod(x, SR, carrier_hz, ring_depth)
    x = bitcrush(x, bits=bits, downsample=downsample)
    x = warble(x, SR, warble_hz, warble_depth)
    return x


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _report(path):
    """Open a just-written WAV and print duration / peak (sanity check)."""
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        nf = w.getnframes()
        raw = w.readframes(nf)
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
    peak = float(np.max(np.abs(a))) if a.size else 0.0
    print("  wrote %-40s  %.2fs  sr=%d  peak=%.3f  frames=%d"
          % (path, nf / float(sr), sr, peak, nf))
    return nf, peak


def cmd_synth(args):
    if not args.out and not args.out_dir:
        print("error: give --out <file> (single) or --out-dir <dir> (batch)", file=sys.stderr)
        return 2
    moods = list(MOODS) if args.mood == "all" else [args.mood]

    if args.out and args.mood != "all" and args.count <= 1:
        y, drive = synth_utterance(args.mood, args.seed)
        write_wav(args.out, y, drive=drive)
        _report(args.out)
        return 0

    out_dir = args.out_dir or (os.path.dirname(args.out) or ".")
    os.makedirs(out_dir, exist_ok=True)
    made = 0
    for mood in moods:
        for i in range(max(1, args.count)):
            seed = args.seed + i
            y, drive = synth_utterance(mood, seed)
            path = os.path.join(out_dir, "%s_gen%d.wav" % (mood, i + 1))
            write_wav(path, y, drive=drive)
            _report(path)
            made += 1
    print("synth: wrote %d file(s) to %s" % (made, out_dir))
    return 0


def cmd_convert(args):
    if not os.path.isfile(args.inp):
        print("error: input file not found: %s" % args.inp, file=sys.stderr)
        return 2
    x, sr_in = read_wav(args.inp)
    y = convert_voice(
        x, sr_in,
        semitones=args.semitones, carrier_hz=args.carrier, ring_depth=args.ring_depth,
        bits=args.bits, downsample=args.downsample,
        band_lo=args.band_lo, band_hi=args.band_hi,
        warble_hz=args.warble_hz, warble_depth=args.warble_depth,
    )
    write_wav(args.out, y, drive=args.drive)
    _report(args.out)
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="duck_voice.py",
        description="Generate BD-1 / BDX-style 'droid speech' WAVs for the Open Duck Mini.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("synth", help="procedural droid-speech synthesis",
                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    s.add_argument("--mood", default="curious",
                   choices=list(MOODS) + ["all"],
                   help="emotional palette (or 'all' to render every mood)")
    s.add_argument("--seed", type=int, default=0, help="deterministic RNG seed")
    s.add_argument("--count", type=int, default=1,
                   help="how many utterances per mood (batch, uses --out-dir)")
    s.add_argument("--out", default=None, help="output .wav path (single)")
    s.add_argument("--out-dir", default=None, help="output directory (batch)")
    s.set_defaults(func=cmd_synth)

    c = sub.add_parser("convert", help="robotize an input voice .wav",
                       formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    c.add_argument("--in", dest="inp", required=True, help="input voice .wav")
    c.add_argument("--out", required=True, help="output .wav path")
    c.add_argument("--semitones", type=float, default=7.0, help="pitch shift up (semitones)")
    c.add_argument("--carrier", type=float, default=80.0, help="ring-mod carrier Hz (50-120)")
    c.add_argument("--ring-depth", dest="ring_depth", type=float, default=0.5,
                   help="ring-mod wet amount [0-1]")
    c.add_argument("--bits", type=int, default=7, help="bitcrush bit depth")
    c.add_argument("--downsample", type=int, default=2, help="sample-and-hold decimation factor")
    c.add_argument("--band-lo", dest="band_lo", type=float, default=300.0,
                   help="band-pass low edge Hz")
    c.add_argument("--band-hi", dest="band_hi", type=float, default=3200.0,
                   help="band-pass high edge Hz")
    c.add_argument("--warble-hz", dest="warble_hz", type=float, default=9.0,
                   help="amplitude warble rate Hz")
    c.add_argument("--warble-depth", dest="warble_depth", type=float, default=0.18,
                   help="amplitude warble depth [0-1]")
    c.add_argument("--drive", type=float, default=2.2, help="output soft-clip drive")
    c.set_defaults(func=cmd_convert)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
