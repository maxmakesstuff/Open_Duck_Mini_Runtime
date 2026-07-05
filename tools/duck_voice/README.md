# duck_voice — BD-1 / BDX "droid speech" sound generator

An **off-robot** authoring tool that generates little emotive "droid speech"
sound effects in the style of Disney's BD-1 / BDX droids — the same vibe as the
Open Duck Mini's existing `beep*.wav` / `happy*.wav` clips (short, expressive,
speech-like, *not* musical arpeggios).

It runs on a plain dev laptop. **No robot hardware, no `board`/`pygame`/`librosa`/
`scipy` required** — just Python 3 + `numpy` and the standard library (`wave`).
Every DSP block is pure numpy, so the tool always runs.

Output matches the existing asset set so files drop straight in:
**mono, 16-bit PCM, 44100 Hz**, peak-normalized to ~**-3 dBFS** with a tanh
soft-clip.

---

## Install-free usage

Nothing to install beyond numpy (which you already have). From this directory:

```bash
# Procedural synthesis — one utterance
python3 duck_voice.py synth --mood happy --seed 3 --out out/happy_gen1.wav

# Batch — 5 "curious" utterances into a directory (files: curious_gen1.wav …)
python3 duck_voice.py synth --mood curious --count 5 --out-dir out/

# One of every mood at once
python3 duck_voice.py synth --mood all --seed 1 --out-dir out/

# Robotize a real voice recording into droid speech
python3 duck_voice.py convert --in myvoice.wav --semitones 7 --out out/me_as_duck.wav
```

Every write prints the file's duration / sample-rate / peak so you can confirm
it's non-silent and correctly formatted. Full flag list:
`python3 duck_voice.py synth -h` and `python3 duck_voice.py convert -h`.

`--seed` makes `synth` fully deterministic (same seed → byte-identical WAV). In
batch mode each file uses `seed, seed+1, …`.

---

## Mood palette (`--mood`)

Each mood shapes pitch range, tempo (syllable count / duration / gaps), the
global intonation contour, and timbre (brightness, ring-mod carrier, tremolo).

| mood          | feel                                   | pitch range | contour        |
|---------------|----------------------------------------|-------------|----------------|
| `happy`       | bright, bouncy, chatty                 | ~520–1300 Hz| rising "!"     |
| `curious`     | inquisitive, moderate                   | ~420–1050 Hz| rising "?"     |
| `sad`         | low, slow, sighing                     | ~240–620 Hz | falling        |
| `alarm`       | high, fast, urgent, staccato           | ~720–1750 Hz| flat & jittery |
| `affirmative` | short "mm-hm", agreeing                 | ~460–1000 Hz| rising         |
| `negative`    | short "uh-uh", declining               | ~340–780 Hz | falling        |

`--mood all` renders one file per mood.

---

## How it sounds like *speech*, not beeps (the DSP)

**Procedural synth** (`synth`) — an utterance is a sequence of "syllables":

- **Voiced source**: a band-limited harmonic stack (a glottal-buzz / sawtooth)
  built from *instantaneous phase*, so pitch can glide continuously.
- **Portamento glide** within each syllable (cosine-eased start→end pitch).
- **Vibrato / warble** — a small pitch LFO (~4–13 Hz depending on mood).
- **Intonation** — a random-walk pitch step between syllables plus a global
  rising/falling **contour** (question vs. statement).
- **Formant coloring** — per syllable a random vowel (a/e/i/o/u/y) is imposed as
  resonant spectral peaks (F1/F2/F3), so the droid "articulates" vowels.
- **Consonant transients** — short band-passed noise bursts at some syllable
  onsets, for a percussive speech-like attack.
- **Ring modulation** — multiply by a low-frequency carrier (~70–110 Hz) for the
  metallic droid timbre.
- **Envelopes** — raised-cosine attack/release per syllable + an overall tremolo.

**Voice conversion** (`convert`) — robotize a real recording:

1. resample to 44100 Hz (handles any input rate / channel count),
2. **pitch-shift up** `--semitones` (default +7) via a *phase-vocoder* time-stretch
   followed by resampling — length-preserving and intelligible,
3. **band-pass** to a small-speaker range (`--band-lo`/`--band-hi`, default 300–3200 Hz),
4. **ring modulation** (`--carrier`, default 80 Hz; the metallic edge),
5. **bitcrush / quantize** (`--bits`, default 7) + sample-and-hold decimation
   (`--downsample`),
6. **amplitude warble** (`--warble-hz`, `--warble-depth`).

---

## Feed it a voice sample

Any WAV works (`convert` resamples and downmixes to mono automatically):

```bash
python3 duck_voice.py convert --in ~/Desktop/hello.wav --semitones 9 \
    --out out/hello_duck.wav --carrier 95 --bits 6
```

No recording handy? A short vowel-ish tone works as a test source. The tool was
verified by synthesizing a 150 Hz test tone and converting it.

---

## Tuning the "speech" feel

- **More/less talkative** → edit a mood's `syl` (syllable count) and `syl_dur` /
  `gap` in the `MOODS` table near the top of `duck_voice.py`.
- **Higher/lower voice** → change the mood `pitch` range (Hz).
- **More "asking a question"** → increase `contour` (positive = rising); negative
  = a falling statement.
- **More robotic / metallic** → lower `ring_hz` (or `--carrier` in convert) and
  raise `ring_depth`; add grit with fewer `--bits` and a larger `--downsample`.
- **More vibrato/wobble** → raise `vib_depth` and `vib_rate`.
- **Brighter / buzzier** → raise `harmonics`.
- **Cleaner vs. crunchier output** → lower/raise `drive` (soft-clip amount).
- **Different vowels** → edit the `VOWELS` formant table.

Change is heard immediately — re-run with the same `--seed` to A/B a tweak, or
sweep `--seed` to audition variations of the same mood.

---

## Dropping results into the robot

The runtime plays **any `*.wav`** it finds in
`mini_bdx_runtime/assets/` — the `Sounds` class loads that directory, and the
gamepad **`B` button** triggers a random one during the walk loop (`Sounds` is
gated by the `speaker` expression-feature flag). So to add new droid voices:

```bash
# from this tools/duck_voice/ directory
cp out/happy_gen1.wav out/curious_gen1.wav \
   ../../mini_bdx_runtime/assets/
```

They're already the correct format (mono / 16-bit / 44100 Hz), so no conversion
is needed. Then run the walk and press `B`.

> This tool only ever **creates** files under `tools/duck_voice/`. Copying into
> `assets/` is a manual step you do when you're happy with a clip.

---

## What's in this folder

```
duck_voice.py   the CLI (synth + convert)
README.md       this file
out/            pre-generated examples — one per mood + a converted test tone
```

The `out/` clips were generated with `--seed 1` so you can listen immediately
without running anything.
