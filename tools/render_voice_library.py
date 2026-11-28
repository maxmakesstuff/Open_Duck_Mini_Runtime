#!/usr/bin/env python3
"""Render the duck's voice library from the droid-voice synthesizer.

The duck plays ready-made WAV files; it does not synthesize while it walks (the
synthesizer needs bursts of CPU that the 50 Hz walk loop cannot spare on a Pi Zero).
This script makes those files on a laptop, once, from fixed seeds, so the library can
be rebuilt bit for bit by anyone who has the synthesizer.

    python3 tools/render_voice_library.py --droid-voice ~/Git_Projects/droid-voice

Writes mini_bdx_runtime/assets/voice/<category>_<n>.wav (22.05 kHz, mono, 16 bit)
and voice.json, the list the runtime reads.

What is in it: only the synthesizer's composed "phrases" (moods), and only the moods
that are built from its generic building blocks. Not used: the note-for-note scores
transcribed from reference recordings ("exact"), and the moods that contain the
signature whistle or figures taken from single reference clips (whistle, trot,
grumble, angry, bored, cheer, play). That keeps this library safe to publish.
"""
import argparse
import hashlib
import json
import os
import sys
import wave

import numpy as np

OUT_RATE = 22050
# category -> list of (mood, energy, seed). Categories are what the runtime asks for.
LIBRARY = {
    "curious": [("curious", 0.45, 1), ("curious", 0.55, 2), ("curious", 0.65, 3),
                ("curious", 0.50, 4), ("curious", 0.70, 5), ("curious", 0.40, 6)],
    "notice":  [("alert", 0.40, 1), ("alert", 0.50, 2), ("alert", 0.60, 3),
                ("alert", 0.45, 4), ("alert", 0.55, 5)],
    "happy":   [("happy", 0.50, 1), ("happy", 0.60, 2), ("happy", 0.70, 3),
                ("happy", 0.55, 4), ("happy", 0.65, 5), ("happy", 0.45, 6)],
    "excited": [("excited", 0.60, 1), ("excited", 0.70, 2), ("excited", 0.80, 3),
                ("excited", 0.65, 4)],
    "hello":   [("hello", 0.50, 1), ("hello", 0.60, 2), ("hello", 0.55, 3), ("hello", 0.65, 4)],
    "yes":     [("yes", 0.50, 1), ("yes", 0.60, 2), ("yes", 0.55, 3), ("yes", 0.65, 4)],
    "no":      [("no", 0.50, 1), ("no", 0.60, 2), ("no", 0.55, 3), ("no", 0.45, 4)],
    "sad":     [("sad", 0.40, 1), ("sad", 0.50, 2), ("sad", 0.45, 3)],
    "worried": [("worried", 0.50, 1), ("worried", 0.60, 2), ("worried", 0.55, 3),
                ("worried", 0.65, 4)],
    "amazed":  [("amazed", 0.50, 1), ("amazed", 0.60, 2), ("amazed", 0.55, 3), ("amazed", 0.65, 4)],
    "babble":  [("babble", 0.50, 1), ("babble", 0.60, 2), ("babble", 0.55, 3),
                ("babble", 0.65, 4), ("babble", 0.45, 5)],
    "scan":    [("scan", 0.50, 1), ("scan", 0.55, 2), ("scan", 0.45, 3)],
    "beatbox": [("beatbox", 0.55, 1), ("beatbox", 0.65, 2), ("beatbox", 0.60, 3)],
}
NOT_USED = ("whistle", "trot", "grumble", "angry", "bored", "cheer", "play")


def halve_rate(y):
    """44.1 kHz -> 22.05 kHz: remove everything above 10.5 kHz, keep every 2nd sample."""
    n = len(y)
    spec = np.fft.rfft(y)
    freqs = np.fft.rfftfreq(n, 1.0 / 44100.0)
    spec[freqs > 10500.0] = 0.0
    roll = (freqs > 9500.0) & (freqs <= 10500.0)
    spec[roll] *= 0.5 * (1.0 + np.cos(np.pi * (freqs[roll] - 9500.0) / 1000.0))
    return np.fft.irfft(spec, n)[::2]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--droid-voice", required=True, help="path to a droid-voice checkout")
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--out", default=os.path.join(here, "..", "mini_bdx_runtime", "assets", "voice"))
    args = ap.parse_args()

    os.environ["DROID_VOICE_CHARACTER"] = "/nonexistent/character.json"   # the default voice
    sys.path.insert(0, os.path.abspath(os.path.expanduser(args.droid_voice)))
    import droid_voice
    from droid_voice import dsp, render

    assert dsp.SR == 44100, f"expected a 44.1 kHz synthesizer, got {dsp.SR}"
    for moods in LIBRARY.values():
        for mood, _, _ in moods:
            assert mood not in NOT_USED, mood
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    for old in os.listdir(out):
        if old.endswith(".wav") or old == "voice.json":
            os.unlink(os.path.join(out, old))

    manifest = {"rate": OUT_RATE, "source": "droid-voice moods (see tools/render_voice_library.py)",
                "categories": {}}
    total = 0.0
    for category, takes in LIBRARY.items():
        entries = []
        for n, (mood, energy, seed) in enumerate(takes, 1):
            y = droid_voice.say(mood, seed=seed, energy=energy)
            y = halve_rate(np.asarray(y, dtype=float))
            pcm = render.to_int16(np.clip(y, -1.0, 1.0))
            name = f"{category}_{n}.wav"
            with wave.open(os.path.join(out, name), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(OUT_RATE)
                w.writeframes(pcm.tobytes())
            seconds = round(len(pcm) / OUT_RATE, 2)
            total += seconds
            entries.append({"file": name, "s": seconds, "mood": mood, "energy": energy,
                            "seed": seed,
                            "sha256": hashlib.sha256(pcm.tobytes()).hexdigest()[:16]})
        manifest["categories"][category] = entries
    with open(os.path.join(out, "voice.json"), "w") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")
    count = sum(len(v) for v in manifest["categories"].values())
    size = sum(os.path.getsize(os.path.join(out, n)) for n in os.listdir(out))
    print(f"{count} clips in {len(manifest['categories'])} categories, {total:.0f} s of audio, "
          f"{size / 1e6:.1f} MB -> {out}")


if __name__ == "__main__":
    main()
