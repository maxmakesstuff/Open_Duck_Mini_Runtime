"""The six building blocks of the droid voice.

Every function takes a numpy random Generator first and returns a mono float
array at dsp.SR with a peak of roughly 1. The numbers in here were tuned by ear
(see docs/superpowers/specs/2026-11-23-droid-voice-design.md); change them only
by listening.

What keeps this sounding like a creature and not a machine: every event gets a
little random pitch and timing variation, loudness swells inside a syllable
instead of sitting flat, attacks are soft, and nothing repeats exactly.
"""
import numpy as np

from .dsp import SR, cat, env, n, osc, sil, tone


def _jit(rng, pct):
    """A random factor within +-pct percent."""
    return 1.0 + rng.uniform(-pct, pct) / 100.0


# ---- chatter: the "talking" -------------------------------------------------
# Two whistles sharing one modulator, like a songbird's two-part voice: a nearly
# pure low whistle (~845 Hz) and a rough, fluttering high whistle (~3.9 kHz).
def chat_syl(rng, ms, lo, hi, fm=150.0, idx_hi=2.2, idx_lo=0.18, mix=0.6, amp=1.0, accent=False):
    """One chatter syllable. `lo`/`hi` are (start, end) Hz of the two whistles,
    `fm` the flutter rate in Hz, `idx_*` how deep the flutter goes, `mix` the
    level of the high whistle. An accent opens with ~40 ms of faster, deeper
    flutter."""
    N = n(ms)
    u = np.linspace(0.0, 1.0, N)
    t = np.arange(N) / SR
    drift = 1.0 + 0.006 * np.sin(2.0 * np.pi * rng.uniform(4.0, 7.0) * t + rng.uniform(0.0, 6.0))
    swell_pow = rng.uniform(0.5, 1.1)
    attack, release = rng.uniform(6.0, 14.0), rng.uniform(18.0, 35.0)

    fmod = np.full(N, float(fm))
    ih = np.full(N, float(idx_hi))
    il = np.full(N, float(idx_lo))
    if accent:
        k = np.exp(-np.arange(N) / n(22))
        fmod = fm + (270.0 - fm) * k
        ih = idx_hi + 2.0 * k
        il = idx_lo + 0.9 * k
    m = np.sin(osc(fmod * drift))

    f_lo = np.exp(np.interp(u, [0.0, 0.35, 1.0], np.log([lo[0], lo[1], lo[1] * 0.985]))) * drift
    f_hi = np.exp(np.interp(u, [0.0, 1.0], np.log(hi))) * drift
    y = np.sin(osc(f_lo) + il * m) + mix * np.sin(osc(f_hi) + ih * m) * (0.75 + 0.25 * m)
    swell = 0.6 + 0.4 * np.sin(np.pi * u ** swell_pow)
    return amp * y / (1.0 + mix) * env(N, attack, release, swell)


def chatter(rng, n_syl=9, tempo=1.0, accent_p=0.16):
    """A run of chatter syllables (about 7 per second at tempo 1). The first
    syllable is always accented; later ones with probability `accent_p`."""
    out = []
    base = rng.uniform(0.97, 1.03)
    for i in range(max(1, int(n_syl))):
        accented = i == 0 or (i > 2 and rng.random() < accent_p)
        ms = float(rng.choice([45, 62, 78, 100, 140, 180], p=[.2, .2, .15, .2, .15, .1]))
        if accented:
            kw = dict(lo=(float(rng.choice([1195, 1240])), float(rng.choice([945, 1000]))),
                      hi=(float(rng.choice([4250, 4450])), 3750.0), amp=1.0, accent=True, mix=0.8)
            ms = max(ms, 110.0)
        elif rng.random() < 0.14:      # growly syllable: slow deep flutter, high whistle falling
            kw = dict(lo=(840.0, 835.0), hi=(4050.0, 3200.0), fm=75.0, idx_hi=5.0, mix=1.1,
                      amp=rng.uniform(0.4, 0.6))
            ms = max(ms, 140.0)
        else:
            low = float(rng.choice([840, 840, 840, 990, 1000]))
            centre = rng.uniform(3650.0, 4150.0)
            kw = dict(lo=(low * rng.uniform(0.995, 1.012), low), hi=(centre, centre - rng.uniform(0.0, 200.0)),
                      fm=rng.uniform(142.0, 158.0), mix=rng.uniform(0.4, 0.8), amp=rng.uniform(0.35, 0.7))
        kw["lo"] = tuple(v * base for v in kw["lo"])
        gap = float(rng.choice([15, 30, 50, 50, 55, 85]))
        out += [chat_syl(rng, ms / tempo, **kw), sil(gap / tempo)]
    return cat(*out[:-1])   # no trailing gap


# ---- glides: near-pure sliding tones ----------------------------------------
def glide_run(rng, n_syl=6, tempo=1.0):
    """A run of short notes climbing from about 900 Hz to about 2.8 kHz; the
    last one droops. The classic rising 'beep-beep-bip-BEEP'."""
    n_syl = max(2, int(n_syl))
    start, top = 920.0 * _jit(rng, 5), 2850.0 * _jit(rng, 5)
    out = []
    for i in range(n_syl):
        last = i == n_syl - 1
        edge = i == 0 or i >= n_syl - 2
        if last:
            f0 = top * 0.975
            pts = [(0.0, f0), (0.45, f0 * 0.99), (1.0, f0 * rng.uniform(0.78, 0.84))]
            ms, amp, rel = rng.uniform(85, 110), 0.9, 45.0
        else:
            f0 = start * (top / start) ** (i / max(n_syl - 2.0, 1.0)) * _jit(rng, 2)
            if i < n_syl - 2 and rng.random() < 0.45:      # scoop up at the end
                pts = [(0.0, f0), (0.55, f0 * 1.01), (1.0, f0 * rng.uniform(1.12, 1.3))]
            else:
                pts = [(0.0, f0), (1.0, f0 * rng.uniform(0.985, 1.01))]
            ms = rng.uniform(60, 115) if edge else float(rng.choice([25, 45, 60, 70]))
            amp, rel = (1.0 if edge else rng.uniform(0.2, 0.7)), 20.0
        out += [tone(pts, ms / tempo, amp, r=rel), sil(rng.uniform(16, 48) / tempo)]
    return cat(*out[:-1])


def glide_word(rng, low=610.0, peak=1500.0, end=700.0, ms=640.0):
    """One long arched note: up quickly, then a slow fall. Reads as a 'word'."""
    j = _jit(rng, 5)
    pts = [(0.0, low * j), (rng.uniform(0.18, 0.26), peak * j * _jit(rng, 4)),
           (0.45, peak * j * 0.67), (1.0, end * j)]
    return tone(pts, ms * _jit(rng, 12), 1.0, h=(1.0, 0.1, 0.04), r=80.0)


def glide_question(rng):
    """A low hum followed by a short note that rises and stays up: 'hm? ...eh?'"""
    j = _jit(rng, 5)
    hum = tone([(0.0, 360 * j), (0.5, 380 * j), (1.0, 430 * j)], 330 * _jit(rng, 10), 1.0, h=(1.0, 0.7, 0.35, 0.15))
    ask = tone([(0.0, 1030 * j), (0.45, 1550 * j), (1.0, 1520 * j)], 160 * _jit(rng, 10), 0.5)
    return cat(hum, sil(rng.uniform(45, 70)), ask)


# ---- boop-bip: the beatbox --------------------------------------------------
def boop(rng, amp=1.0):
    """Low rising 'boop' - the kick drum of the voice."""
    j = _jit(rng, 2)
    return tone([(0.0, 455 * j), (1.0, 720 * j)], 88, amp, h=(1.0, 0.1), a=18.0, r=30.0)


def bip(rng, amp=0.42):
    """Quiet high answer: one falling breath with two softer parallel partials."""
    N = n(85)
    u = np.linspace(0.0, 1.0, N)
    j = _jit(rng, 2)
    f = np.exp(np.interp(u, [0.0, 0.25, 0.7, 1.0], np.log([4500.0, 4300.0, 3100.0, 2550.0]))) * j
    f = f * (1.0 + 0.02 * np.sign(np.sin(2.0 * np.pi * 55.0 * np.arange(N) / SR)))   # faint step ripple
    y = np.sin(osc(f)) + 0.45 * np.sin(osc(f * 0.79)) + 0.2 * np.sin(osc(f * 0.61))
    return amp * y / 1.65 * env(N, 5.0, 45.0, 1.0 - 0.45 * u)


def boopbip(rng, reps=4, period=332.0):
    """`reps` boop-bip pairs, one per `period` ms, the bip on the off-beat."""
    half = period / 2.0
    out = []
    for _ in range(max(1, int(reps))):
        b, p = boop(rng), bip(rng)
        swing = rng.uniform(-6.0, 6.0)          # the off-beat is never exactly on the grid
        out += [b, np.zeros(max(n(half + swing) - len(b), 0)),
                p, np.zeros(max(n(half - swing) - len(p), 0))]
    return cat(*out)


# ---- chirp: a rising whip, then a low thump ---------------------------------
def chirp(rng, lo=1750.0, hi=3700.0, ms=115.0, amp=0.7):
    """Fast upward whip. Mostly a smooth glide with a faint stepped ripple;
    hard steps sound like a computer error."""
    lo, hi = lo * _jit(rng, 4), hi * _jit(rng, 5)
    N = n(ms * _jit(rng, 10))
    u = np.linspace(0.0, 1.0, N)
    lf = np.log(lo) + (np.log(hi) - np.log(lo)) * u ** 0.8
    f = np.exp(0.65 * lf + 0.35 * np.round(lf * 9.0) / 9.0)
    ph = osc(f)
    y = np.sin(ph) + 0.3 * np.sin(osc(f * 1.5)) + 0.12 * np.sin(2.0 * ph)
    return amp * y / 1.4 * env(N, 10.0, 30.0, 0.5 + 0.5 * np.sin(np.pi * u ** 0.8))


def thump(rng, f=390.0, ms=60.0, amp=0.6):
    f = f * _jit(rng, 3)
    return tone([(0.0, f * 1.04), (1.0, f)], ms, amp, h=(1.0, 0.08), a=6.0, r=25.0)


def chirps(rng, reps=4, gap=200.0):
    """`reps` chirp-and-thump pairs."""
    out = []
    for _ in range(max(1, int(reps))):
        out += [chirp(rng), sil(rng.uniform(10, 30)), thump(rng), sil(gap * _jit(rng, 15))]
    return cat(*out[:-1])


# ---- bell: a held ringing tone ----------------------------------------------
_BELL_PARTIALS = ((1.0, 1.0, 0.0), (1.41, 0.22, 3.1), (2.26, 0.3, 4.2), (2.62, 0.38, 3.6), (3.03, 0.16, 5.0))


def bell(rng, f=2075.0, ms=350.0, amp=1.0, bend=0.0):
    """Ringing tone with inharmonic overtones (ratio, level, wobble Hz) that
    each waver at their own rate. `bend` arches the pitch (used for grace notes)."""
    N = n(ms)
    t = np.arange(N) / SR
    arch = 1.0 + bend * np.sin(np.pi * np.linspace(0.0, 1.0, N))
    phase0 = rng.uniform(0.0, 6.0)
    y = np.zeros(N)
    for ratio, level, wobble in _BELL_PARTIALS:
        y += level * np.sin(osc(f * ratio * (1.0 + 0.018 * np.sin(2.0 * np.pi * wobble * t + ratio + phase0)) * arch))
    return amp * y / 1.6 * env(N, 25.0, 40.0, 0.85 + 0.15 * np.sin(2.0 * np.pi * 3.3 * t))


def bell_call(rng, calls=2):
    """Grace note, then a held bell tone - `calls` times. A greeting."""
    j = _jit(rng, 1.5)
    out = []
    for i in range(max(1, int(calls))):
        out += [bell(rng, 2290 * j, rng.uniform(62, 74), 0.6 + 0.1 * i, bend=rng.uniform(0.045, 0.06)),
                sil(rng.uniform(90, 110)),
                bell(rng, 2075 * j, rng.uniform(335, 365), 0.9 + 0.1 * i),
                sil(rng.uniform(100, 125))]
    return cat(*out[:-1])


# ---- sweep: one long upward slide -------------------------------------------
def sweep(rng, ms=400.0):
    """Slides up to about 2.1 kHz and settles back, getting quieter as it rises
    (anything louder or higher hurts on a small speaker)."""
    j = _jit(rng, 4)
    pts = [(0.0, 700 * j), (0.3, 1050 * j), (0.62, 1750 * j), (0.74, 2100 * j), (0.84, 2050 * j), (1.0, 1600 * j)]
    y = tone(pts, ms, 1.0, h=(1.0, 0.06), r=60.0)
    return y * np.interp(np.linspace(0.0, 1.0, len(y)), [0.0, 0.5, 0.75, 1.0], [1.0, 0.9, 0.6, 0.55])


# name -> a ready-to-audition version of each block
BLOCKS = {
    "chatter": lambda rng: chatter(rng, 10),
    "glide": glide_run,
    "word": glide_word,
    "question": glide_question,
    "boopbip": boopbip,
    "chirp": lambda rng: chirps(rng, 3),
    "bell": bell_call,
    "sweep": sweep,
}
