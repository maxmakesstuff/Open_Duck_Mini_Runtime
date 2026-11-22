# Droid Voice - design

Date: 2026-11-23. Status: awaiting approval.

## Intent

Give the Open Duck Mini its own voice: a synthesizer that speaks the BDX-droid
"language" (chirps, whistles, warbles, beatbox-style rhythm) instead of replaying
the same six WAV files. Every utterance should be new but recognisably the same
creature.

Two stages. This document covers stage 1 in full and fixes the constraints that
keep stage 2 possible.

1. **Stage 1 (this project):** a tool on the Mac that generates new sounds as WAV
   files, to be copied into `mini_bdx_runtime/assets/` and played by the existing
   `Sounds` class.
2. **Stage 2 (later, separate design):** the same synthesis code runs on the duck
   and speaks in reaction to movement and emotes.

## Goals

- Six approved sound "building blocks" (below), each a small synthesis recipe.
- A phrase generator that combines blocks into whole utterances from a mood name
  and a seed. Same mood + same seed = identical audio. Different seed = a new
  utterance in the same mood.
- A command-line tool: generate N utterances for a mood, write WAVs, optionally
  play them on the Mac.
- The synthesis core is one importable module set with no hardware imports, so
  stage 2 can call it on the Pi unchanged.

## Non-goals (stage 1)

- No hooks into the walk loop, head puppet, web UI or `TrackingChatter`.
- No mapping from IMU / movement to sound.
- No BD-1 voice (the full harmonic warbling one). Possible second voice later.
- No machine learning, no voice conversion.

## Anti-goals

- **No Disney audio in the repo or in the output.** No samples, no slices, no
  traced copies of specific phrases. The reference recordings were used only to
  measure general properties (bands, rates, durations) and to tune by ear. Do not
  commit the reference recordings, their spectrograms, or tools that resynthesize
  them.
- Sounding like a computer: hard steps, exact repetition, buzzy pulse trains.
- Hurting ears: loud sustained energy above about 2.5 kHz. The duck's speaker is
  small and close to people at a tradeshow.

## Constraints

- **Dependencies: numpy and the standard library only** for the synthesis core.
  (numpy 1.26.4 is pinned on the robot; do not use newer-only APIs.)
- **Must be importable without the `mini_bdx_runtime` package `__init__`**, which
  imports onnxruntime. The existing off-robot tests in this repo are run flat
  from their own directory (`python3 test_x.py`, no pytest); follow that pattern.
- **Output format:** mono, 16-bit PCM, 44100 Hz, to match the existing assets.
- **Stage-2 speed budget:** rendering must be fast enough for a Pi Zero 2W to
  produce an utterance before it is needed. Target on the Mac: at least 50x
  faster than real time (a 2 s utterance in under 40 ms). All synthesis is
  vectorised numpy over whole events; no per-sample Python loops.
- **Phrase-at-a-time rendering:** the unit of work is one utterance rendered to
  one array. This is what stage 2 will do in a background thread and hand to
  `pygame.mixer.Sound`.
- Repo rules: commit locally only, never push without being asked.

## The approved sound

A throwaway spike was tuned by ear over two rounds and approved ("these sound
honestly pretty great"). Its code is kept as the reference for the recipes and
starting parameter values:
`docs/superpowers/specs/2026-11-23-droid-voice-reference/approved_spike.py`.
It is a reference, not code to import. The real implementation must reproduce
its sound; rewrite it cleanly.

| Block | Recipe (as approved) |
|---|---|
| **Chatter** (the "talking") | Two whistles sharing one modulator, like a songbird. Low: a nearly pure sine near 845 Hz (sometimes 1000 Hz, or starting at 1200 Hz and falling to about 950 Hz on accents), lightly modulated. High: a sine near 3.9 kHz, frequency-modulated at about 150 Hz (index about 2.2), giving a rough flutter. Syllables 45-180 ms, gaps 15-85 ms, about 7 per second. Accents open with 40 ms of faster, deeper modulation (270 Hz). Occasional "growly" syllable: 75 Hz modulation, index 5, high voice falling 4050 to 3200 Hz. |
| **Glide** | Near-sine with faint 2nd and 3rd overtones. Runs of 25-115 ms syllables climbing 900 Hz to 2.8 kHz and drooping at the end; arched "words" of 350-680 ms (e.g. 600, 1500, 700 Hz); a low-then-rising question shape. |
| **Boop-bip** (beatbox) | Low "boop": sine rising 455 to 720 Hz in 88 ms. Answer "bip": 85 ms smooth fall 4500 to 2550 Hz with two quieter parallel partials (x0.79, x0.61) and a faint step ripple. One pair every 332 ms, bip on the off-beat. |
| **Chirp** | Rising "whip" 1750 to 3700 Hz in about 115 ms, 65 % smooth glide blended with 35 % stepped pitch, soft swell, then a 390 Hz thump. Each repeat varies a few percent in pitch and timing. |
| **Bell** | Held 2075 Hz tone, 350 ms, with inharmonic partials at x1.41, x2.26, x2.62, x3.03 that each wobble slowly at their own rate; led in by a 65 ms grace note at 2290 Hz. |
| **Sweep** | Sine sliding 700 Hz up to 2100 Hz and settling back to 1600 Hz over 400 ms, getting quieter as it rises, almost no overtones. |

What made it creature-like rather than machine-like, and must be preserved:
small random variation of pitch and timing on every event, swelling (not flat)
loudness within a syllable, soft attacks, and no exact repeats.

## Approaches considered

1. **Voice kit plus phrase grammar (chosen).** Each block is a synthesis recipe;
   a grammar strings blocks into utterances from mood controls. Fully generated,
   numpy only, renders an utterance at a time, so it carries over to the duck.
2. **Trace and resynthesize the references.** Closest fastest, but the output
   would be copies of Disney's phrases and it cannot invent new ones. Rejected;
   conflicts with the release anti-goal.
3. **Continuous live synth modulated sample-by-sample by the IMU.** Most
   reactive, but risky in Python on a Pi Zero 2W, and real droids speak in
   discrete utterances. Rejected for now; approach 1 does not close it off.

Maximilian picked approach 1.

## Design

Three layers, each usable without the one above.

**1. Blocks.** One function per building block. Input: parameters (durations,
pitches, a random generator). Output: a float numpy array at 44.1 kHz. Shared
helpers for oscillator phase, envelopes and log-frequency contours.

**2. Phrases.** A phrase is a list of events (block, parameters, gap) produced by
a mood's grammar from a seeded random generator. Moods are data plus small rule
functions, not hand-written fixed sequences, so each seed gives a new utterance.
Starting moods (my proposal, taken from the spike phrases plus what the duck's
existing behaviours need):

| Mood | Character | Used for (stage 2) |
|---|---|---|
| `happy` | chatter with bright accents, a chirp or two | default chatter while tracking a face |
| `hello` | bell call, then short chatter | greeting a new face |
| `curious` | short chatter ending in a rising question glide | head tilt, something new |
| `excited` | sweep, fast chatter, chirps, rising glide run | sprint, being played with |
| `sad` | slow falling glides with slight vibrato | being ignored, low battery |
| `beatbox` | boop-bip pattern with chirps on top | walking rhythm, showing off |
| `alert` | one sharp chirp or short rising glide | startled, fall detected |
| `yes` / `no` | two quick rising notes / two falling notes | acknowledging a button |

Each mood also accepts `energy` (0-1); 0.5 is the approved sound. Energy steers
the grammar, not the recipes (my call): tempo and gap length, number of
syllables, how often accents and chirps occur, which blocks are chosen, and the
contour targets of glides. It may also transpose a whole utterance by at most
plus or minus 1.5 semitones. It never changes the internal ratios of a block
(modulation rates, partial ratios, the chatter's two-voice spacing). This is the
handle stage 2 will drive from movement.

**3. Rendering and output.** Render a phrase to one array, peak normalise to
about -3 dBFS, short fades at both ends, write WAV with the standard library.
There is **no global high-frequency filter** (my call): the approved blocks
already contain their own loudness choices (the sweep fades as it rises, the bip
is quieter than the boop) and a filter would change what was approved by ear.
The ear-safety rule is applied where material is created instead: any *new*
sustained tone (longer than about 150 ms) above 2.5 kHz that a grammar
introduces gets a per-event gain of at most 0.6. With `energy` at 0.5 and no
transposition, the six blocks must match the approved spike.

**Command-line tool.** In `tools/droid_voice/`. Roughly:

    python3 tools/droid_voice/droid_voice.py --mood happy --count 5 --seed 1 --out out/
    python3 tools/droid_voice/droid_voice.py --mood curious --energy 0.8 --play
    python3 tools/droid_voice/droid_voice.py --block chatter --play      # audition one block
    python3 tools/droid_voice/droid_voice.py --all --out out/            # a starter set of every mood

`--play` uses `afplay` on macOS. File names carry mood and seed so any sound can
be regenerated exactly.

**Where the code lives.** Synthesis core in
`mini_bdx_runtime/mini_bdx_runtime/droid_voice/` (so it ships to the robot with
everything else and stage 2 can import it); the CLI in `tools/droid_voice/` as a
thin wrapper. The old `tools/duck_voice/` is deleted completely, including its
voice-conversion mode and its `out/` WAVs, with no replacement (Maximilian:
"fully discarded").

**No change to what the robot plays.** Stage 1 adds no files to
`mini_bdx_runtime/assets/` (the B button picks randomly from every WAV there and
`animation_player.py` names the current six). The tool writes to an output
folder that is git-ignored; Maximilian picks by ear what to copy to a duck.

**Pi speed is not proven in stage 1** (my call). The tool gets a `--bench` flag
that prints render time versus audio length per mood, so the same command can be
run on a Pi later. The sample rate is one constant in one place so a 22.05 kHz
fallback stays cheap.

**Tests** (flat, no pytest, like the neighbouring `test_*.py`):
same seed gives identical samples; different seeds differ; every mood and block
renders without NaN or clipping; durations fall in sane bounds (0.15-5 s; a slow `beatbox` runs about 4.5 s); each
block has its energy where the recipe says (e.g. chatter: a peak near 845 Hz and
a band at 3.4-4.4 kHz, near silence between 1.6 and 3 kHz); render speed meets
the 50x budget; WAV header is mono/16-bit/44100.

## Stage 2 outline (not built now, shapes the above)

A `DroidVoice` object on the robot with `say(mood, energy)`: renders in a
background thread, plays through the existing pygame mixer, never blocks the
50 Hz control loop. Callers would be `TrackingChatter`, the B button, greeting,
fall detection, and a movement-to-energy mapping. Gated by the existing
`speaker` feature flag. If Pi Zero 2W rendering is too slow, fall back to
22.05 kHz or pre-render a pool at start-up.

## Left to the builder

- Internal module split, function names, parameter data structures.
- Exact grammar rules per mood, as long as each matches its character above and
  uses the approved block recipes.
- Exact shape of the high-frequency loudness reduction.
- Exact CLI flag names.

## Acceptance

Maximilian listens. The blocks must sound like the approved spike files, and the
moods must be told apart by ear. Automated tests cover everything except taste.
