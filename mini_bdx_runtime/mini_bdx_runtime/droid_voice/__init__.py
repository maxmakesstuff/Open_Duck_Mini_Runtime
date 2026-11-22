"""droid_voice - a synthesizer for the duck's own BDX-style droid language.

numpy + standard library only, no hardware imports, so the same code runs on a
laptop (authoring WAVs) and on the robot.

    import droid_voice
    audio = droid_voice.say("happy", seed=3)          # float array at droid_voice.SR
    droid_voice.write_wav("happy.wav", audio)

Same mood + seed + energy always gives the same audio; a new seed gives a new
utterance in the same mood. Import this package directly from its parent
directory (not via `mini_bdx_runtime`, whose __init__ needs onnxruntime).
"""
import numpy as np

from .blocks import BLOCKS
from .dsp import SR
from .phrases import MOODS
from .render import finish, to_int16, transpose, write_wav

__all__ = ["SR", "MOODS", "BLOCKS", "say", "block", "finish", "to_int16", "write_wav"]

MAX_TRANSPOSE_SEMITONES = 1.5      # at energy 0 / 1; exactly 0 at the neutral 0.5


def _lookup(table, name, kind):
    if name not in table:
        raise ValueError(f"unknown {kind} '{name}' - choose one of: {', '.join(table)}")
    return table[name]


def say(mood, seed=None, energy=0.5):
    """Render one utterance. `energy` (0..1, clamped) makes it livelier or calmer;
    0.5 is the neutral voice. `seed=None` picks a fresh random utterance."""
    fn = _lookup(MOODS, mood, "mood")
    energy = float(min(1.0, max(0.0, energy)))
    y = fn(np.random.default_rng(seed), energy)
    y = transpose(y, (energy - 0.5) * 2.0 * MAX_TRANSPOSE_SEMITONES)
    return finish(y)


def block(name, seed=None):
    """Render one building block on its own, for auditioning."""
    return finish(_lookup(BLOCKS, name, "block")(np.random.default_rng(seed)))
