# droid_voice

A synthesizer for the duck's own droid language: chatter, glides, beatbox
boops, chirps, bell calls and sweeps, strung together into utterances by mood.
Every run can produce a sound nobody has heard before, and it is still
recognisably the same creature.

It runs on a laptop and needs only Python 3 and numpy.

## Try it

```bash
cd tools/droid_voice
python3 droid_voice.py --all                           # a starter set of everything, into out/
python3 droid_voice.py --mood happy --count 5 --play   # five new happy utterances, played as they are made
python3 droid_voice.py --mood excited --energy 0.9 --play
python3 droid_voice.py --block chatter --play          # one building block on its own
python3 droid_voice.py --list
```

Files land in `tools/droid_voice/out/` (git-ignored) unless you pass `--out`.
The name says how to make the file again: `happy_e050_s3.wav` is
`--mood happy --energy 0.5 --seed 3`. Without `--seed` you get random seeds.

## Moods

| Mood | What you hear |
|---|---|
| `happy` | chatter with bright accents, often a chirp at the end |
| `hello` | a bell call, then a little chatter |
| `curious` | short chatter or a word, ending in a rising question |
| `excited` | sweep, fast chatter, chirps, a rising run of beeps |
| `sad` | slow falling notes with a slight wobble |
| `beatbox` | boop-bip rhythm with chirps on top |
| `alert` | one or two sharp rising sounds |
| `yes` / `no` | two quick rising notes / two falling notes |

`--energy` runs from 0 (calm) to 1 (lively); 0.5 is the neutral voice. It
changes the phrasing: tempo, number of syllables, how often accents and chirps
happen, and a small overall pitch shift (at most 1.5 semitones either way).

## Building blocks

`chatter`, `glide`, `word`, `question`, `boopbip`, `chirp`, `bell`, `sweep`.
Their recipes are in `mini_bdx_runtime/mini_bdx_runtime/droid_voice/blocks.py`.
The numbers there were tuned by ear, so change them by listening, not by
reasoning.

## Putting a sound on a duck

Copy the WAVs you like into `mini_bdx_runtime/assets/` and deploy as usual. Note
that the `B` button plays a random WAV from that folder, so everything you put
there joins the rotation.

## Using it from Python

```python
import sys; sys.path.insert(0, "mini_bdx_runtime/mini_bdx_runtime")
import droid_voice

audio = droid_voice.say("curious", seed=4, energy=0.7)   # numpy float array at droid_voice.SR
droid_voice.write_wav("curious.wav", audio)
pcm = droid_voice.to_int16(audio)                        # e.g. for pygame.mixer.Sound(buffer=pcm)
```

The synthesizer has no hardware imports and renders one utterance at a time, so
the same code can run on the robot later. `python3 droid_voice.py --bench`
prints the render speed; run it on a Pi to see whether live rendering is
feasible there.

## Tests

```bash
cd mini_bdx_runtime/mini_bdx_runtime && python3 test_droid_voice.py
cd tools/droid_voice && python3 test_cli.py
```

## One rule

Everything here is synthesized from scratch. Do not add recordings of Disney's
droids (or slices, or traced copies of their phrases) to this repo. Reference
recordings are for listening and tuning by ear only.
