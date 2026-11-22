#!/usr/bin/env python3
"""
droid_voice.py - generate new droid-language sounds for the Open Duck Mini.

Runs on a laptop (numpy only). Every sound is synthesized from scratch; nothing
is sampled. Output is mono 16-bit 44.1 kHz WAV, the format the robot's `Sounds`
class already plays.

    python3 droid_voice.py --all                          # starter set of everything
    python3 droid_voice.py --mood happy --count 5 --play  # five new happy utterances
    python3 droid_voice.py --mood curious --energy 0.8 --seed 12
    python3 droid_voice.py --block chatter --play         # audition one building block
    python3 droid_voice.py --list
    python3 droid_voice.py --bench                        # render speed (run this on a Pi too)

File names carry the mood, energy and seed, so any file can be regenerated
exactly: happy_e050_s3.wav == --mood happy --energy 0.5 --seed 3.
"""
import argparse
import os
import random
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# The synthesizer lives in the runtime package dir so it ships to the robot; import
# it directly from there (the package __init__ one level up needs onnxruntime).
sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..", "..", "mini_bdx_runtime", "mini_bdx_runtime")))

import droid_voice as dv  # noqa: E402

DEFAULT_OUT = os.path.join(HERE, "out")
STARTER_PER_MOOD = 3


def _fail(message):
    print(f"error: {message}", file=sys.stderr)
    sys.exit(2)


def _play(path):
    player = shutil.which("afplay") or shutil.which("aplay")
    if not player:
        print("  (not played: no afplay/aplay on this machine)")
        return
    subprocess.run([player, path], check=False)


def _write(out_dir, name, audio, play):
    path = os.path.join(out_dir, name)
    dv.write_wav(path, audio)
    print(f"{name}  {len(audio) / dv.SR:.2f}s")
    if play:
        _play(path)


MAX_COUNT = 1000


def _seeds(first, count):
    """`count` distinct seeds: counting up from `first`, or fresh random ones."""
    if first is not None:
        return [first + i for i in range(count)]
    return random.sample(range(1, 1_000_000), count)


def _bench():
    dv.say("happy", 0)   # warm-up
    total_audio = total_wall = 0.0
    for mood in dv.MOODS:
        audio = 0.0
        t0 = time.perf_counter()
        for seed in range(10):
            audio += len(dv.say(mood, seed)) / dv.SR
        wall = time.perf_counter() - t0
        total_audio += audio
        total_wall += wall
        print(f"{mood:8s} {audio / 10:5.2f}s audio in {wall / 10 * 1000:6.1f} ms  ->  {audio / wall:5.0f}x real time")
    print(f"{'overall':8s} {total_audio / total_wall:.0f}x real time")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate droid-language sounds (WAV).",
                                 epilog="Moods: " + ", ".join(dv.MOODS) + ". Blocks: " + ", ".join(dv.BLOCKS) + ".")
    ap.add_argument("--mood", help="render whole utterances in this mood")
    ap.add_argument("--block", help="render one building block on its own")
    ap.add_argument("--all", action="store_true", help=f"starter set: {STARTER_PER_MOOD} of every mood + every block (seeds 1.. unless --seed; ignores --count)")
    ap.add_argument("--count", type=int, default=1, help="how many utterances (default 1)")
    ap.add_argument("--seed", type=int, default=None, help="first seed (default: random; shown in the file name)")
    ap.add_argument("--energy", type=float, default=0.5, help="0 calm .. 1 lively (default 0.5 = neutral; moods only)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="output folder (default: tools/droid_voice/out)")
    ap.add_argument("--play", action="store_true", help="play each sound after writing it")
    ap.add_argument("--list", action="store_true", help="list moods and blocks")
    ap.add_argument("--bench", action="store_true", help="measure render speed")
    a = ap.parse_args(argv)

    if a.list:
        print("moods:  " + ", ".join(dv.MOODS))
        print("blocks: " + ", ".join(dv.BLOCKS))
        return
    if a.bench:
        _bench()
        return
    if not (a.mood or a.block or a.all):
        _fail("nothing to do - give --mood NAME, --block NAME or --all (see --list)")
    if a.mood and a.mood not in dv.MOODS:
        _fail(f"unknown mood '{a.mood}' - choose one of: {', '.join(dv.MOODS)}")
    if a.block and a.block not in dv.BLOCKS:
        _fail(f"unknown block '{a.block}' - choose one of: {', '.join(dv.BLOCKS)}")
    if sum(bool(x) for x in (a.mood, a.block, a.all)) > 1:
        _fail("give only one of --mood, --block, --all")
    if not 1 <= a.count <= MAX_COUNT:
        _fail(f"--count must be between 1 and {MAX_COUNT}")
    if a.seed is not None and a.seed < 0:
        _fail("--seed must be 0 or greater")
    if a.energy != a.energy:
        _fail("--energy must be a number between 0 and 1")

    # Quantise to the two digits the file name carries, so the name reproduces the file.
    energy = round(min(1.0, max(0.0, a.energy)) * 100) / 100
    tag = f"e{round(energy * 100):03d}"
    try:
        os.makedirs(a.out, exist_ok=True)
    except OSError as e:
        _fail(f"cannot use output folder '{a.out}': {e.strerror}")

    if a.all:       # fixed seeds (1.. unless --seed) so the starter set is the same every time
        first = a.seed if a.seed is not None else 1
        for mood in dv.MOODS:
            for seed in _seeds(first, STARTER_PER_MOOD):
                _write(a.out, f"{mood}_{tag}_s{seed}.wav", dv.say(mood, seed, energy), a.play)
        for name in dv.BLOCKS:
            _write(a.out, f"block_{name}_s{first}.wav", dv.block(name, first), a.play)
    if a.mood:
        for seed in _seeds(a.seed, a.count):
            _write(a.out, f"{a.mood}_{tag}_s{seed}.wav", dv.say(a.mood, seed, energy), a.play)
    if a.block:
        for seed in _seeds(a.seed, a.count):
            _write(a.out, f"block_{a.block}_s{seed}.wav", dv.block(a.block, seed), a.play)
    print(f"-> {os.path.abspath(a.out)}")


if __name__ == "__main__":
    main()
