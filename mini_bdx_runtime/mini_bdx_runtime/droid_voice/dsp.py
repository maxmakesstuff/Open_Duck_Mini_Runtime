"""Shared synthesis helpers. numpy only; everything works on whole arrays."""
import numpy as np

SR = 44100  # output sample rate; the only place it is defined


def n(ms):
    """Milliseconds -> sample count (at least 1)."""
    return max(1, int(SR * ms / 1000.0))


def osc(f):
    """Per-sample frequency in Hz -> phase in radians."""
    return 2.0 * np.pi * np.cumsum(f) / SR


def env(N, a_ms=6.0, r_ms=14.0, shape=None):
    """Raised-cosine attack and release, optionally multiplied by a loudness shape."""
    e = np.ones(N)
    a = min(n(a_ms), N // 2)
    r = min(n(r_ms), N // 2)
    if a:
        e[:a] = 0.5 - 0.5 * np.cos(np.pi * np.arange(a) / a)
    if r:
        e[N - r:] = 0.5 + 0.5 * np.cos(np.pi * np.arange(r) / r)
    return e if shape is None else e * shape


def contour(points, N):
    """[(position 0..1, Hz), ...] -> N frequencies, interpolated in log-frequency
    (so a glide sounds even to the ear)."""
    pos, hz = zip(*points)
    return np.exp(np.interp(np.linspace(0.0, 1.0, N), pos, np.log(hz)))


def sil(ms):
    return np.zeros(n(ms))


def cat(*parts):
    return np.concatenate(parts)


def tone(points, ms, amp=1.0, h=(1.0, 0.12, 0.05), a=8.0, r=20.0, vib=0.0):
    """A near-sine note following a pitch contour. `h` are overtone levels
    (fundamental first); `vib` is vibrato depth as a fraction of pitch."""
    N = n(ms)
    f = contour(points, N)
    if vib:
        f = f * (1.0 + vib * np.sin(2.0 * np.pi * 6.0 * np.arange(N) / SR))
    ph = osc(f)
    y = sum(g * np.sin((k + 1) * ph) for k, g in enumerate(h))
    return amp * y * env(N, a, r)
