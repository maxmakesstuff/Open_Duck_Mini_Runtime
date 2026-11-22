"""Turn a rendered phrase into finished audio and WAV files."""
import wave

import numpy as np

from .dsp import SR, n

PEAK = 0.708          # about -3 dBFS
LEAD_MS, TAIL_MS, FADE_MS = 40.0, 80.0, 3.0


def finish(y):
    """Fade the ends, normalise the peak to about -3 dBFS and pad with a little
    silence so playback never starts or stops on a click."""
    y = np.asarray(y, dtype=np.float64).copy()
    f = min(n(FADE_MS), len(y) // 2)
    if f:
        ramp = np.linspace(0.0, 1.0, f)
        y[:f] *= ramp
        y[len(y) - f:] *= ramp[::-1]
    peak = np.abs(y).max() if len(y) else 0.0
    if peak > 0.0:
        y *= PEAK / peak
    return np.concatenate([np.zeros(n(LEAD_MS)), y, np.zeros(n(TAIL_MS))])


def transpose(y, semitones):
    """Shift the whole utterance in pitch by resampling (it gets slightly
    shorter when raised). Keeps every internal pitch ratio intact."""
    if semitones == 0.0 or len(y) < 2:
        return y
    factor = 2.0 ** (semitones / 12.0)
    return np.interp(np.arange(0.0, len(y) - 1, factor), np.arange(len(y)), y)


def to_int16(y):
    return (np.clip(y, -1.0, 1.0) * 32767.0).astype("<i2")


def write_wav(path, y):
    """Mono, 16-bit, dsp.SR - the format of the existing robot assets."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(to_int16(y).tobytes())
