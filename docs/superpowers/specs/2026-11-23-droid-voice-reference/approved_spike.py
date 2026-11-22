"""THROWAWAY SPIKE - droid voice building blocks. numpy only."""
import numpy as np, wave, sys, os
SR = 44100
rng = np.random.default_rng(7)

def n(ms): return max(1, int(SR * ms / 1000))
def osc(f):  # f: per-sample Hz array -> phase
    return 2 * np.pi * np.cumsum(f) / SR
def env(N, a_ms=6, r_ms=14, shape=None):
    e = np.ones(N); a = min(n(a_ms), N // 2); r = min(n(r_ms), N // 2)
    e[:a] = 0.5 - 0.5 * np.cos(np.pi * np.arange(a) / a)
    e[N - r:] = 0.5 + 0.5 * np.cos(np.pi * np.arange(r) / r)
    return e if shape is None else e * shape
def contour(points, N):  # [(pos0..1, Hz)] interpolated in log-frequency
    p, f = zip(*points)
    return np.exp(np.interp(np.linspace(0, 1, N), p, np.log(f)))
def sil(ms): return np.zeros(n(ms))
def cat(*xs): return np.concatenate(xs)
def res(f, F, B): return np.exp(-0.5 * ((f - F) / (B / 2)) ** 2)

# 1 ---- chatter voice v2: two-voice "syrinx" - pure low whistle + rough high whistle sharing one modulator
def chat_syl(ms, lo=(845, 840), hi=(3900, 3850), fm=150, idx_hi=2.2, idx_lo=0.18, mix=0.6, amp=1.0, accent=False):
    N = n(ms); u = np.linspace(0, 1, N)
    drift = 1 + 0.006 * np.sin(2 * np.pi * rng.uniform(4, 7) * np.arange(N) / SR + rng.uniform(0, 6))
    fmod = np.full(N, float(fm)); ih = np.full(N, idx_hi); il = np.full(N, idx_lo)
    if accent:  # onset: faster, deeper modulation for ~40 ms, then settle
        k = np.exp(-np.arange(N) / n(22)); fmod = fm + (270 - fm) * k; ih = idx_hi + 2.0 * k; il = idx_lo + 0.9 * k
    m = np.sin(osc(fmod * drift))
    f_lo = np.exp(np.interp(u, [0, .35, 1], np.log([lo[0], lo[1], lo[1] * .985]))) * drift
    f_hi = np.exp(np.interp(u, [0, 1], np.log(hi))) * drift
    y = np.sin(osc(f_lo) + il * m) + mix * np.sin(osc(f_hi) + ih * m) * (0.75 + 0.25 * m)
    swell = 0.6 + 0.4 * np.sin(np.pi * u ** rng.uniform(.5, 1.1))
    return amp * y / (1 + mix) * env(N, rng.uniform(6, 14), rng.uniform(18, 35), swell)
def chatter(n_syl=9, seed=None):
    r = np.random.default_rng(seed); out = []; base = r.uniform(.97, 1.03)
    for i in range(n_syl):
        first = i == 0 or (i > 2 and r.random() < 0.16)
        ms = r.choice([45, 62, 78, 100, 140, 180], p=[.2, .2, .15, .2, .15, .1])
        if first:
            l0 = r.choice([1195, 1240]); kw = dict(lo=(l0, r.choice([945, 1000])), hi=(r.choice([4250, 4450]), 3750), amp=1.0, accent=True, mix=.8); ms = max(ms, 110)
        elif r.random() < .14:  # growly syllable: slow deep modulation, falling high voice
            kw = dict(lo=(840, 835), hi=(4050, 3200), fm=75, idx_hi=5.0, mix=1.1, amp=r.uniform(.4, .6)); ms = max(ms, 140)
        else:
            l = r.choice([840, 840, 840, 990, 1000]); c = r.uniform(3650, 4150)
            kw = dict(lo=(l * r.uniform(.995, 1.012), l), hi=(c, c - r.uniform(0, 200)), fm=r.uniform(142, 158), mix=r.uniform(.4, .8), amp=r.uniform(.35, .7))
        kw["lo"] = tuple(v * base for v in kw["lo"])
        out += [chat_syl(ms, **kw), sil(r.choice([15, 30, 50, 50, 55, 85]))]
    return cat(*out)

# 2 ---- pure glide (R2-ish near-sine) ----------------------------------------
def tone(points, ms, amp=1.0, h=(1, .12, .05), a=8, r=20, vib=0.0):
    N = n(ms); f = contour(points, N)
    if vib: f = f * (1 + vib * np.sin(2 * np.pi * 6 * np.arange(N) / SR))
    ph = osc(f); y = sum(g * np.sin((k + 1) * ph) for k, g in enumerate(h))
    return amp * y * env(N, a, r)
def glide_up():
    return cat(tone([(0, 920), (.6, 950), (1, 1230)], 115), sil(26), tone([(0, 1380), (1, 1420)], 60, .35), sil(16),
               tone([(0, 1615), (1, 1620)], 25, .2), sil(48), tone([(0, 1725), (.4, 1730), (1, 2070)], 70, .7), sil(46),
               tone([(0, 2860), (1, 2815)], 66, 1), sil(47), tone([(0, 2790), (.45, 2760), (1, 2250)], 98, .9, r=45))
def glide_word():
    return cat(tone([(0, 610), (.22, 1500), (.45, 1000), (1, 700)], 640, 1, r=80, h=(1, .1, .04)))
def glide_question():
    return cat(tone([(0, 360), (.5, 380), (1, 430)], 330, 1, h=(1, .7, .35, .15)), sil(55),
               tone([(0, 1030), (.45, 1550), (1, 1520)], 160, .5))

# 3 ---- boop-bip (beatbox) -----------------------------------------------------
def boop(amp=1.0): return tone([(0, 455), (1, 720)], 88, amp, h=(1, .1), a=18, r=30)
def bip(amp=.42):  # v2: one breath - smooth falling chord instead of 13 ms steps
    N = n(85); u = np.linspace(0, 1, N); f = np.exp(np.interp(u, [0, .25, .7, 1], np.log([4500, 4300, 3100, 2550])))
    f = f * (1 + 0.02 * np.sign(np.sin(2 * np.pi * 55 * np.arange(N) / SR)))   # faint step ripple
    y = np.sin(osc(f)) + 0.45 * np.sin(osc(f * 0.79)) + 0.2 * np.sin(osc(f * 0.61))
    return amp * y / 1.65 * env(N, 5, 45, 1 - .45 * u)
def boopbip(reps=4, period=332):
    out = []
    for _ in range(reps):
        b, p = boop(), bip(); gap1 = n(165) - len(b); gap2 = n(period) - n(165) - len(p)
        out += [b, np.zeros(max(gap1, 0)), p, np.zeros(max(gap2, 0))]
    return cat(*out)

# 4 ---- staircase chirp + thump ---------------------------------------------
def staircase(lo=1750, hi=3700, ms=115, amp=.7):  # v2: a rising "whip" with soft step ripple, not hard steps
    lo *= rng.uniform(.96, 1.04); hi *= rng.uniform(.95, 1.05); N = n(ms * rng.uniform(.9, 1.1)); u = np.linspace(0, 1, N)
    lf = np.log(lo) + (np.log(hi) - np.log(lo)) * u ** 0.8
    steps = np.round(lf * 9) / 9; f = np.exp(0.65 * lf + 0.35 * steps)
    y = np.sin(osc(f)) + 0.3 * np.sin(osc(f * 1.5)) + 0.12 * np.sin(2 * osc(f))
    return amp * y / 1.4 * env(N, 10, 30, 0.5 + 0.5 * np.sin(np.pi * u ** .8))
def thump(f=390, ms=60, amp=.6): return tone([(0, f * 1.04), (1, f)], ms, amp, h=(1, .08), a=6, r=25)
def chirps(reps=4):
    return cat(*[cat(staircase(), sil(rng.uniform(10, 30)), thump(390 * rng.uniform(.97, 1.03)), sil(rng.uniform(170, 230))) for _ in range(reps)])

# 5 ---- bell tone ------------------------------------------------------------
def bell(f=2075, ms=350, amp=1.0, bend=0.0):
    N = n(ms); t = np.arange(N) / SR; y = np.zeros(N)
    for ratio, g, wr in [(1, 1, 0), (1.41, .22, 3.1), (2.26, .3, 4.2), (2.62, .38, 3.6), (3.03, .16, 5.0)]:
        f_ = f * ratio * (1 + 0.018 * np.sin(2 * np.pi * wr * t + ratio)) * (1 + bend * np.sin(np.pi * t / t[-1]))
        y += g * np.sin(osc(f_))
    return amp * y / 1.6 * env(N, 25, 40, 0.85 + 0.15 * np.sin(2 * np.pi * 3.3 * t))
def bell_call():
    return cat(bell(2290, 65, .6, bend=.05), sil(105), bell(2075, 350, .9), sil(110), bell(2290, 72, .7, bend=.06), sil(90), bell(2075, 360, 1))

# 6 ---- sweep ------------------------------------------------------------------
def sweep():  # v2: lower peak (2.1 kHz), softer at the top, almost no overtones
    N = n(400); u = np.linspace(0, 1, N); pts = [(0, 700), (.3, 1050), (.62, 1750), (.74, 2100), (.84, 2050), (1, 1600)]
    return tone(pts, 400, 1, h=(1, .06), r=60) * np.interp(u, [0, .5, .75, 1], [1, .9, .6, .55])

# ---- phrases (how the blocks combine) ---------------------------------------
def phrase_hello():   return cat(bell(2290, 65, .6, .05), sil(100), bell(2075, 300, .9), sil(140), chatter(7, 3))
def phrase_beatbox(): return cat(boopbip(4), chirps(3), boop(), sil(80), boop(), sil(140), sweep())
def phrase_excited(): return cat(sweep(), sil(60), chatter(6, 11), sil(90), staircase(), sil(20), thump(), sil(120), glide_up())
def phrase_curious(): return cat(chatter(4, 5), sil(120), glide_question())
def phrase_sad():     return cat(tone([(0, 900), (.3, 950), (1, 520)], 520, 1, h=(1, .1), r=120, vib=.012), sil(180),
                                 tone([(0, 640), (1, 410)], 420, .7, h=(1, .1), r=150, vib=.015))

def save(name, y, out):
    y = cat(sil(40), y, sil(80)); y = 0.7 * y / np.abs(y).max()
    with wave.open(os.path.join(out, name + ".wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes((y * 32767).astype("<i2").tobytes())
    print(f"{name}.wav  {len(y)/SR:.2f}s")

if __name__ == "__main__":
    out = sys.argv[1]
    for name, y in [("01_chatter_a", chatter(10, 1)), ("02_chatter_b", chatter(14, 2)), ("03_glide_up", glide_up()),
                    ("04_glide_word", glide_word()), ("05_glide_question", glide_question()), ("06_boop_bip", boopbip(4)),
                    ("07_staircase_chirps", chirps(4)), ("08_bell_call", bell_call()), ("09_sweep", sweep()),
                    ("10_phrase_hello", phrase_hello()), ("11_phrase_beatbox", phrase_beatbox()),
                    ("12_phrase_excited", phrase_excited()), ("13_phrase_curious", phrase_curious()), ("14_phrase_sad", phrase_sad())]:
        save(name, y, out)
