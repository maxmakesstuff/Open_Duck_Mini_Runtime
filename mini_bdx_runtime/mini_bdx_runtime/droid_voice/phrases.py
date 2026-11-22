"""Moods: small grammars that string blocks into whole utterances.

Each mood is a function (rng, energy) -> raw audio. `energy` runs 0..1 and 0.5
is the neutral, ear-approved sound. Energy steers the *phrasing* - tempo, how
many syllables, how often accents and chirps happen, which blocks get picked -
and never the inner recipe of a block.
"""
from . import blocks as B
from .dsp import cat, sil, tone


def _tempo(energy):
    return 0.8 + 0.4 * energy          # 0.8x .. 1.2x; durations and gaps are divided by it


def _count(rng, lo, hi, energy):
    """Syllable/repeat count that grows with energy, with +-1 of chance."""
    return max(1, int(round(lo + (hi - lo) * energy)) + int(rng.integers(-1, 2)))


def _chirp_tag(rng):
    return cat(B.chirp(rng), sil(rng.uniform(10, 30)), B.thump(rng))


def happy(rng, energy):
    t = _tempo(energy)
    parts = [B.chatter(rng, _count(rng, 5, 11, energy), t, accent_p=0.10 + 0.15 * energy)]
    roll = rng.random()
    if roll < 0.35 + 0.3 * energy:                       # a chirp to finish
        parts += [sil(rng.uniform(70, 120) / t), _chirp_tag(rng)]
        if energy > 0.6 and rng.random() < 0.5:
            parts += [sil(rng.uniform(150, 210) / t), _chirp_tag(rng)]
    elif roll > 0.85:                                    # or a little rising run
        parts += [sil(rng.uniform(80, 130) / t), B.glide_run(rng, int(rng.integers(3, 5)), t)]
    return cat(*parts)


def hello(rng, energy):
    t = _tempo(energy)
    calls = 2 if rng.random() < 0.25 + 0.4 * energy else 1
    return cat(B.bell_call(rng, calls), sil(rng.uniform(120, 170) / t),
               B.chatter(rng, _count(rng, 4, 8, energy), t))


def curious(rng, energy):
    t = _tempo(energy)
    if rng.random() < 0.3:
        lead = B.glide_word(rng, ms=rng.uniform(380, 520) / t)
    else:
        lead = B.chatter(rng, _count(rng, 3, 5, energy), t, accent_p=0.05)
    return cat(lead, sil(rng.uniform(100, 160) / t), B.glide_question(rng))


def excited(rng, energy):
    t = _tempo(energy)
    parts = [B.sweep(rng, 400.0 / t), sil(rng.uniform(50, 80) / t),
             B.chatter(rng, _count(rng, 5, 9, energy), t, accent_p=0.25), sil(rng.uniform(70, 110) / t)]
    parts.append(B.chirps(rng, 1 + int(rng.random() < 0.3 + 0.5 * energy), gap=190.0 / t))
    parts += [sil(rng.uniform(90, 140) / t), B.glide_run(rng, int(rng.integers(4, 7)), t)]
    return cat(*parts)


def sad(rng, energy):
    t = _tempo(energy)
    j = rng.uniform(0.94, 1.06)
    parts = [tone([(0.0, 900 * j), (0.3, 950 * j), (1.0, 520 * j)], rng.uniform(470, 560) / t, 1.0,
                  h=(1.0, 0.1), r=120.0, vib=0.012),
             sil(rng.uniform(150, 220) / t),
             tone([(0.0, 640 * j), (1.0, 410 * j)], rng.uniform(380, 460) / t, 0.7,
                  h=(1.0, 0.1), r=150.0, vib=0.015)]
    if rng.random() < 0.35:                              # a last small sigh
        parts += [sil(rng.uniform(180, 260) / t),
                  tone([(0.0, 470 * j), (1.0, 360 * j)], rng.uniform(260, 340) / t, 0.45,
                       h=(1.0, 0.1), r=140.0, vib=0.015)]
    return cat(*parts)


def beatbox(rng, energy):
    t = _tempo(energy)
    # counts come from the seed alone so energy only changes the tempo
    parts = [B.boopbip(rng, int(rng.integers(3, 5)), 332.0 / t),
             B.chirps(rng, int(rng.integers(2, 4)), gap=200.0 / t), sil(170.0 / t),
             B.boop(rng), sil(80.0 / t), B.boop(rng)]
    if rng.random() < 0.6:
        parts += [sil(140.0 / t), B.sweep(rng, 400.0 / t)]
    return cat(*parts)


def alert(rng, energy):
    t = _tempo(energy)
    if rng.random() < 0.5:
        one = lambda: B.chirp(rng, ms=100.0 / t)
    else:
        j = rng.uniform(0.95, 1.05)
        one = lambda: tone([(0.0, 1200 * j), (0.7, 2000 * j), (1.0, 2100 * j)], 120.0 / t, 0.8, a=5.0, r=30.0)
    first = one()
    if rng.random() < 0.3 + 0.4 * energy:                # startled twice
        return cat(first, sil(rng.uniform(60, 90) / t), one())
    return first


def yes(rng, energy):
    t = _tempo(energy)
    j = rng.uniform(0.94, 1.06)
    return cat(tone([(0.0, 900 * j), (1.0, 1150 * j)], rng.uniform(80, 100) / t, 0.8),
               sil(rng.uniform(50, 70) / t),
               tone([(0.0, 1300 * j), (0.6, 1700 * j), (1.0, 1750 * j)], rng.uniform(120, 145) / t, 1.0, r=35.0))


def no(rng, energy):
    t = _tempo(energy)
    j = rng.uniform(0.94, 1.06)
    return cat(tone([(0.0, 1400 * j), (1.0, 1150 * j)], rng.uniform(100, 120) / t, 1.0),
               sil(rng.uniform(60, 80) / t),
               tone([(0.0, 1000 * j), (0.5, 800 * j), (1.0, 720 * j)], rng.uniform(160, 190) / t, 0.9, r=60.0))


MOODS = {
    "happy": happy, "hello": hello, "curious": curious, "excited": excited, "sad": sad,
    "beatbox": beatbox, "alert": alert, "yes": yes, "no": no,
}
