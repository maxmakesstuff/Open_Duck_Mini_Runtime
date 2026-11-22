# Droid Voice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A numpy-only synthesizer that generates new BDX-style droid utterances as WAV files on the Mac, built so the same core can later run on the duck.

**Architecture:** A small package `droid_voice` with three layers: `dsp` helpers, `blocks` (six approved sound recipes), `phrases` (mood grammars) plus `render` (normalise, WAV). A thin CLI in `tools/droid_voice/` wraps it. All randomness flows from one explicitly passed `numpy.random.Generator`.

**Tech Stack:** Python 3, numpy (1.26.4-compatible), stdlib `wave`/`argparse`/`subprocess`. Tests are flat scripts, no pytest.

**Spec:** `docs/superpowers/specs/2026-11-23-droid-voice-design.md` (recipes and starting values: `docs/superpowers/specs/2026-11-23-droid-voice-reference/approved_spike.py`)

## Global Constraints

- Synthesis core imports numpy and stdlib only. No scipy, pygame, board.
- Must import without `mini_bdx_runtime/__init__.py` (which pulls onnxruntime): tests and CLI put `mini_bdx_runtime/mini_bdx_runtime/` on `sys.path` and `import droid_voice`. Inside the package use relative imports only.
- Output: mono, 16-bit PCM, 44100 Hz; `SR` is one constant in `dsp.py`.
- No per-sample Python loops. Render speed at least 50x real time on the Mac.
- No module-level random state: every function that varies takes `rng`.
- `energy=0.5` and no transposition reproduces the approved block recipes.
- No Disney audio, spectrograms or resynthesis tools in the repo. Nothing added to `mini_bdx_runtime/assets/`.
- Commit locally only. Never push. Branch `feature/droid-voice`.

## Review Focus

- Unknown mood or block name on the CLI: a clear error listing valid names, exit code 2, no traceback.
- `energy` outside 0-1 (e.g. `--energy 3`): clamped, never a crash or a 20 s utterance.
- `--play` on a machine without `afplay` (the Pi): the WAV is still written, a one-line note is printed, exit code 0.
- `--count 50` without `--seed`: 50 different files, none overwritten, names carry the seed actually used.
- Extreme energy (0 and 1) on every mood: no NaN, no clipping, duration stays within 0.2-6 s.

---

### Task 1: Core helpers and the six blocks

**Files:**
- Create: `mini_bdx_runtime/mini_bdx_runtime/droid_voice/__init__.py`, `dsp.py`, `blocks.py`
- Test: `mini_bdx_runtime/mini_bdx_runtime/test_droid_voice.py`

**Interfaces (produces):**
- `dsp.SR = 44100`; `n(ms) -> int`; `osc(f: ndarray) -> ndarray` (phase); `env(N, a_ms, r_ms, shape=None)`; `contour(points, N)` (log-frequency interpolation); `sil(ms)`; `cat(*arrays)`; `tone(points, ms, amp=1.0, h=(1,.12,.05), a=8, r=20, vib=0.0)`.
- `blocks.chat_syl(rng, ms, lo, hi, fm=150, idx_hi=2.2, idx_lo=0.18, mix=0.6, amp=1.0, accent=False)`, `chatter(rng, n_syl=9, tempo=1.0, accent_p=0.16)`, `glide_run(rng, n_syl=6, tempo=1.0)`, `glide_word(rng, low=610, peak=1500, end=700, ms=640)`, `glide_question(rng)`, `boop(rng, amp=1.0)`, `bip(rng, amp=.42)`, `boopbip(rng, reps=4, period=332)`, `chirp(rng)`, `thump(rng, f=390)`, `chirps(rng, reps=4, gap=200)`, `bell(rng, f=2075, ms=350, amp=1.0, bend=0.0)`, `bell_call(rng, calls=2)`, `sweep(rng)`. All return float64 mono arrays, peak roughly 1.
- `blocks.BLOCKS: dict[str, Callable[[Generator], ndarray]]` with keys `chatter, glide, word, question, boopbip, chirp, bell, sweep`.

- [ ] Write tests: every `BLOCKS` entry returns finite 1-D float array, 0.05-4 s; same seed identical, different seed differs; spectral checks via `numpy.fft.rfft`: chatter has its strongest peak in 800-1300 Hz, energy in 3.3-4.6 kHz at least 5 % of total, and less than 2 % of total in 1.7-2.9 kHz; boopbip strongest peak 430-760 Hz; bell strongest peak within 2 % of 2075 Hz; sweep has no energy peak above 2.3 kHz stronger than -20 dB relative to max.
- [ ] Run: `cd mini_bdx_runtime/mini_bdx_runtime && python3 test_droid_voice.py` - expect import failure.
- [ ] Implement from `approved_spike.py`, replacing the global `rng` with the parameter. `glide_run` generalises the spike's fixed `glide_up`: syllable durations drawn around 25-115 ms, gaps 16-48 ms, pitches stepping upward from about 920 Hz to about 2.8 kHz with the last syllable drooping about 20 %; per-syllable pitch jitter within 4 %.
- [ ] Run tests - expect pass. Commit.

### Task 2: Moods, energy and rendering

**Files:**
- Create: `droid_voice/phrases.py`, `droid_voice/render.py`; extend `__init__.py`, the test file.

**Interfaces (produces):**
- `phrases.MOODS: dict[str, Callable[[Generator, float], ndarray]]` with keys `happy, hello, curious, excited, sad, beatbox, alert, yes, no`.
- `render.finish(y) -> ndarray` (40 ms lead-in, 80 ms tail, 3 ms fades, peak 0.708); `render.to_int16(y)`; `render.write_wav(path, y)`.
- `droid_voice.say(mood: str, seed: int | None = None, energy: float = 0.5) -> ndarray` (finished audio; raises `ValueError` naming valid moods; clamps energy to 0-1; transposes by resampling with factor `2 ** ((energy - 0.5) * 3 / 12)`, skipped exactly at 0.5).
- `droid_voice.block(name: str, seed=None) -> ndarray` (finished audio).

- [ ] Write tests: each mood at energy 0, 0.5, 1 with seeds 0-4: finite, peak <= 0.72, duration 0.2-6 s; determinism per (mood, seed, energy); `say("nope")` raises `ValueError` whose message contains `happy`; `say("happy", 1, 99)` equals `say("happy", 1, 1.0)`; mean duration of `excited` at energy 1 is shorter per syllable... (tempo check: `beatbox` at energy 1 is shorter than at energy 0 for the same seed); `alert` is under 0.8 s; `sad` has its spectral centroid below `excited`'s; render speed: total audio seconds / wall seconds >= 50 across all moods; `write_wav` output read back with `wave` is mono, 2 bytes, 44100.
- [ ] Run - expect failure. Implement. Energy maps to: `tempo = 0.8 + 0.4 * energy` (divides durations and gaps), syllable counts, accent probability, block choice. Run - expect pass. Commit.

### Task 3: Command-line tool, docs, removal of the old tool

**Files:**
- Create: `tools/droid_voice/droid_voice.py`, `tools/droid_voice/README.md`
- Delete: `tools/duck_voice/` (whole directory)
- Modify: `.gitignore` (add `tools/droid_voice/out/`)
- Test: `tools/droid_voice/test_cli.py` (flat script, runs the CLI via `subprocess` into a temp dir)

**Interface:** `--mood NAME`, `--block NAME`, `--all`, `--count N` (default 1), `--seed S` (default: random, printed), `--energy E` (default 0.5), `--out DIR` (default `tools/droid_voice/out`), `--play`, `--bench`, `--list`. File names: `{mood}_e{energy*100:03.0f}_s{seed}.wav`, `block_{name}_s{seed}.wav`. With `--count N --seed S` seeds are `S, S+1, ...`. `--all` writes 3 of each mood plus one of each block.

- [ ] Write tests for each Review Focus line plus: `--all` writes 9*3 + 8 files; `--list` prints every mood and block; `--bench` prints one line per mood containing `x real time`.
- [ ] Run - expect failure. Implement. Run - expect pass.
- [ ] README: what it is, the commands, the moods table, how to copy a WAV to a duck, the no-Disney-audio rule. Commit.

### Task 4: Morning review pack

- [ ] Run `--all` into `tools/droid_voice/out/`; render a spectrogram check of two moods against the approved spike files (scratch only, not committed); run every test; run `--bench`.
- [ ] Fresh-reviewer pass over the branch diff; fix findings; commit.
