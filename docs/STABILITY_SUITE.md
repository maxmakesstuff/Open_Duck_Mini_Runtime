# Stability suite

New in the next release: selectable walk policies, a soft landing when the duck falls,
a get-up, and a black-box recorder to tune all of it with real data.

**Status: first test on a real duck done (2026-11-26), on one of our two ducks.** Policy
switching, the soft landing, the black box and the Health tab ran on hardware; the
results are in [First test results](#first-test-results-2026-11-26). The get-up and the
supply test under load have not run on hardware yet, and neither have the rest pose and
the steering trim, which were built from that evening's data. Off-robot, every part runs
on a simulated duck (the real walk loop on a fake servo bus) and the real policy files
load and run.

With no new settings, the walk is unchanged: the servo commands are identical to the
previous version. Soft landing starts in **Observe** (it only watches), the get-up and
the stick ramp start **off**.

## What is in it

| Part | What it does | Starts as |
|---|---|---|
| **Policy slots** | Pick the walk policy on the phone (TUNE → Policy). The duck stands still, fades the old policy out, swaps, fades the new one in, and remembers the choice. Each policy keeps its own action scale and gait offset. | Stock walk |
| **dk5426 walker** | A retrained walk from [dk5426/open-duck-curation](https://github.com/dk5426/open-duck-curation) (Apache-2.0). | Selectable |
| **Soft landing** | Notices a fall on the way down (gyro and accelerometer together), stops the legs, folds them briefly and lowers the servo stiffness before the impact. | Observe |
| **Get-up** | Runs dk5426's get-up policy from face-down, then lets the walk policy balance for a few seconds. | Off |
| **Black box** | Keeps the last 20 s of everything the duck measured. Falls, reflex triggers, get-ups and policy switches save themselves; MARK saves on demand; a take records until stopped. Download in TUNE → Health. | On |
| **Stick ramp** | Turns a slammed stick into a short ramp. | Off |
| **Loop health** | Counts control steps that ran late and failed servo reads. | On |
| **Rest pose** | Every policy stands in its own, often crooked pose at zero command. "Straight stance" (TUNE → Policy) fades the policy out after 1.5 s without stick input, so the duck stands in the plain stance, and fades it back in on a stick move or a push. | Policy's own pose |
| **Steering trim** | A constant turn command added while driving, to cancel a walk that curves by itself (TUNE → Walk). Kept per policy. | 0 |
| **Sensor check** | Drops impossible IMU samples before they reach the policy and counts them (Health: "bad dropped"). | On |
| **Servo supply** | Per-servo voltage while paused (TUNE → Health), and `voltage_sag_test.py` for the reading under load. | On |

## Deploy

1. Mac joined to the duck's Wi-Fi, then run `mac_transfer.command` (Windows:
   `windows_transfer.ps1`). Both now compare checksums and check that every Python file
   parses on the duck, and stop if anything arrived broken.
2. Restart the walk. The log line `[policy] running: Stock walk (2 walk / 2 get-up slots
   found)` confirms the policies arrived.

Nothing in `~/duck_config.json` is touched by the deploy. The suite adds these keys only
when you save from the phone: `policies`, `fall_reflex`, `getup`, `flight_recorder`,
`command_slew`. `joints_offsets` and `imu_trim` are never written by any of it.

## First test on a real duck

Have a soft surface (a folded blanket or a foam mat) and a second person if possible.
Do the steps in order; each one only needs the previous ones to have passed. Tap **MARK**
whenever something looks odd: it saves the last 20 seconds.

### 1. Nothing got worse (5 min)
- Walk the duck as usual on the stock policy.
- **Pass:** it walks as before. TUNE → Health shows late steps under about 1 % and no
  failed servo reads.
- If late steps are high, that alone explains some falls. Note the number.

### 2. Servo supply (5 min, walk stopped)
```bash
python scripts/turn_on.py
python scripts/voltage_sag_test.py      # both knees bob a few degrees; hand near the duck
python scripts/turn_off.py
```
- **Pass:** "Even supply". If one leg's knee or ankle sags more than the rest, that leg
  needs power from both ends and a cable check before any get-up test.

### 3. Record normal walking (5 min)
- TUNE → Health → **START A TAKE**, walk a fixed route for about a minute (forward, turn
  both ways, sidestep, stop), **STOP**.
- TUNE → Safety: read **Highest while walking** and **Highest predicted**. They should
  sit clearly below the two thresholds on the gauge.

### 4. Record falls in Observe (10 min, soft surface)
- Soft landing stays on **Observe**. While the duck walks in place, tip it over by hand:
  twice forward, twice backward, once to each side. Let it fall onto the blanket.
- Each fall saves a recording by itself. **Falls seen** counts up if the reflex would
  have fired.

### 5. Look at the data (10 min, laptop)
- TUNE → Health → Recordings: download the files. Then:
```bash
python scripts/analyze_falls.py ~/Downloads/*.npz --suggest
```
- **Pass:** every fall is caught with a lead time above about 0.1 s, and "false alarms:
  0" for the walking take. If not, re-run with the suggested `--set` values until both
  hold, then enter those values in TUNE → Safety → Thresholds and save.

### 6. Soft landing in Active (10 min, soft surface)
- TUNE → Safety → **Active**. Tip the duck over again.
- **Pass:** the legs stop kicking and go limp before or at the impact; the banner shows
  FALLEN. Stand the duck up, press **RESUME** (or A): it stiffens, glides to its stance
  and walks on. Pressing RESUME while it still lies down is refused, on purpose. A
  second A during the glide stops it; the next A continues the glide.
- Walk normally for a few minutes. It must never go limp while walking. If it does, tap
  MARK, go back to Observe and raise the thresholds.

### 7. The dk5426 walker (10 min, duck supported at first)
- Pause first (A), then TUNE → Policy → **dk5426 walker**, then resume: the duck glides
  to its stance and the new policy fades in. Start with a hand under it. Later switches
  can be done while walking (it stands still for about a second, then switches); watch
  "Slowest step" in Health the first time, since loading a policy may stall the loop.
- It runs at action scale 0.25 (what it was trained with). If it is too lively, lower it
  in TUNE → Walk and save; the stock policy keeps its own value.
- Record a take on the same route as in step 3 and compare:
```bash
python scripts/analyze_falls.py ~/Downloads/*take*.npz
```
  "PER POLICY" lists wobble and lean for each. Lower is steadier.

### 8. Get-up (15 min, hard smooth floor, only if step 2 passed)
- **Experimental.** Read [Get-up test results](#get-up-test-results-2026-11-27) first.
- Nothing may hold a joint back: no decoration, cable tie or tight cable on the neck
  or the legs. Check that head and legs move freely through their whole range.
- Charged battery. The push pulls about 7 A and drops the supply by about 2 V.
- TUNE → Safety → Get up → **Get-up available: ON**.
- Once per duck: put it on the floor, tap **HOLD STANCE**, wait until it stands still,
  tap **CAPTURE**. "Standing reference: captured" must appear.
- Pause, lay the duck face-down, head straight. Tap **GET UP** on the main screen
  (gamepad: RB + B). The button is there while the duck is paused or has fallen. It
  only starts while the duck is lying down.
  **A stops it at any time** and the joints go soft.
- **Pass:** it stands up, the banner shows BALANCING for 3 s, then you have control.
- If it does not stand within 10 s it stops by itself and goes soft. The message
  names any joint that stood still far from its target ("Blocked: left_hip_pitch
  1.3 s"): look there first. Download the `getup` recording either way; it shows how
  far it got.
- Leave "Start by itself after a fall" off until the manual get-up works several times
  in a row.

## First test results (2026-11-26)

One duck, stock policy and dk5426 walker, about 15 minutes of recorded walking and 17
recorded falls. Numbers come from the black-box recordings, read with
`scripts/analyze_falls.py`.

**Two faults found in the recordings and fixed the same evening**

- **Corrupted IMU samples.** About every 3 to 4 seconds the BNO055 returned one sample
  with a single axis at full scale (gyro about 35 rad/s, accelerometer about 320 m/s²).
  They went straight into the policy, whose output then jumped to about six times its
  normal step for two control steps. `imu_sanity.py` now drops such samples in the IMU
  reader. This removed the kicks; it did not change how often the duck fell.
- **The stability governor throttled the stick almost all the time.** It judged lean
  from the bare accelerometer, which while walking mostly shows foot impacts: more than
  10° on 71 % of control steps, when the real lean exceeded 10° on 1 %. It cut the drive
  commands on 87 % of steps, by 27 % on average, with the amount changing every step. It
  now uses the gyro-fused lean of the soft landing and stays out of normal walking.
  With it, the same duck was driven about twice as hard and leaned no more than before.
  **If you tuned your walk with the old governor, expect the duck to feel faster.**

**Soft landing**

- With the default thresholds, all 5 falls that happened during normal walking were
  detected 0.16 to 0.28 s before the floor, and there was no false alarm in about 8
  minutes of walking. The thresholds were left as they are.
- In Active, 6 hand-tipped falls (both ways about the pitch axis, both sides) all
  triggered at 30 to 37° lean; the joints were soft 0.06 s later, 0.14 to 0.20 s before
  the duck passed 70°. Resume was refused while lying and glided cleanly once upright.
- Not shown by the data: whether the landing is gentler for the servos. The jolt measured
  at the body is the same with and without; the IMU cannot see servo load.

**Policies**

| While moving | Stock | dk5426 walker |
|---|---|---|
| Falls | 1 in 53 s | 0 in 103 s |
| Typical lean | 3.5° | 4.5° |
| Lean, worst 1 % | 8.6° | 14.1° |
| Largest lean | 12.0° | 17.4° |

- The stock walk fell about once every one to two minutes of hard driving, always during
  turns. The dk5426 walker did not fall. It leans further and recovers.
- The dk5426 walker curved by about 16°/s with only the forward stick, and stood with the
  body rolled about 12° when idle. Both are properties of the policy on this duck:
  replaying the recording through the ONNX file with the IMU reading rotated by 12°
  moved its output by less than half a degree, and its standing pose is the same for any
  accelerometer reading. Hence the rest pose option and the steering trim, not a sensor
  correction.
- Switching policies was clean: no slow control step, no jump in the servo commands.

**Other measurements**

- Control loop: a step takes about 10 ms of its 20 ms; 0.1 to 0.5 % of steps ran late.
  No failed servo read in the whole session.
- IMU pitch trim: +0.6° and −3.5° gave the same posture and lean with the stock walk.

**Still open after 2026-11-27:** a get-up that works repeatedly (see below), and all of
it on the second duck. The supply test (step 2), the rest pose and the steering trim
are done on the first duck.

## Get-up test results (2026-11-27)

One duck, hardwood floor, about ten attempts. **Not reliable yet; ships off.**

**Supply first.** The left leg was wired behind the last head servo and sagged 1.1 to
1.9 V under a light load (5.5 V at the ankle). Rewired with a Y-cable at the board, one
branch per leg: every servo now sags 0.2 to 0.5 V, lowest 7.5 V. Wire your legs this way.

**What we measured**
- In simulation (the policy's own training model, driven by this runtime's get-up code)
  the duck stands up 12 times out of 12, upright after about 0.8 s. The inputs rebuilt
  from the duck's recordings reproduce the recorded actions, so the duck feeds the
  policy correctly.
- Still standing up in simulation: head gain as on the robot, a head up to 60 %
  heavier, floor friction from 0.3 to 1.5, uniformly weaker servos (down to 45 %
  torque), with and without the blend and the soft start. None of these is the cause.
- What does reproduce the failure in simulation: **one** hip pitch that cannot move.
- On the duck, the left hip pitch stood still for 0.3 to 1.3 s in the first swing while
  the right one followed, and neck and head took about 1.5 s to get under the body.
  **Cause: a decoration ("tie") on the neck that held the neck pitch servo back.**
  Without it, neck and head are in place after about 0.7 s and the hip no longer stalls.
- Ruled out along the way: servo settings (identical on all 14), the servos' overload
  protection tripping while the duck lies stiff on the floor (8 s, highest load 16 %),
  and a weak left hip servo (left and right follow equally over 450 s of walking).
- A replay of a get-up start with the servos' own telemetry: supply 7.5 V down to
  5.6 V at 6.8 A peak, no protection flag, left hip pitch briefly at 100 % load and 3.4 A.

**Two faults in the runtime, fixed**
- The reference for "what the accelerometer reads standing" was wrong. It came from the
  policy author's tool and is not the model at rest (measured: level). It tilted a
  level-mounted IMU by 15°.
- "Standing" was judged from the bare accelerometer. A duck that has just landed on its
  feet rocks, and single samples read far off. In one attempt the duck stood for 0.85 s
  (lean under 20°, both feet down) without counting as standing, and the get-up policy
  tipped it over backwards. The test now uses the fused lean (gyro and accelerometer).

**Where it stands.** After both fixes and without the tie, one recorded attempt went
from lying to standing in 3.0 s and then balanced for 3 s with both feet down. It is a
single recording, and whether a hand helped is not known. Testing stopped there: the
duck's neck was damaged and needs a repair first.

**Next, when the neck is repaired:** five unaided attempts on a charged battery, filmed
from the side, and the `getup` recording of each.

## Reviewed

An independent review of the motor-control code found six bugs before any hardware run,
two of which could have moved joints abruptly at full stiffness (a refused get-up retry
falling through to the walk policy, and a stopped recovery glide followed by a plain
resume). All six are fixed and pinned by tests; a gyro spike filter, a lock around every
config save and stricter input checks came out of the same review.

## Known limits

- The get-up only works from face-down, is not reliable yet, and is hard on the neck:
  the policy pushes the head into the floor to lever the body up.
- Anything that restricts a joint (decoration, cable tie, tight cable) makes the
  get-up fail; one held-back neck servo was enough.
- The soft landing lowers the stiffness of all servos in one bus write. A servo from a
  newer firmware batch can disturb such grouped writes (seen once on a replaced knee
  servo); step 6 shows at once whether the write gets through. After that servo was
  flashed to the same firmware as the others, the write got through every time.
- With "Straight stance" the policy is faded out while the duck stands. It wakes on a
  stick move, a quick tip or a lean that grows by 6°; a very slow push below that is
  only caught by the soft landing.
- Voltages cannot be read while walking with this servo library; the Health tab shows
  the last reading taken while paused.
- The duck's clock is not set, so recording names carry the wrong date. Their order is
  right.

## For developers

| File | Role |
|---|---|
| `mini_bdx_runtime/policy_slots.py` | slot discovery, background load, per-slot tuning |
| `mini_bdx_runtime/fall_reflex.py` | tilt estimator, fall predictor, reflex phases |
| `mini_bdx_runtime/getup.py` | get-up controller, IMU reference correction |
| `mini_bdx_runtime/flight_recorder.py` | ring buffer, event and take files |
| `mini_bdx_runtime/command_shaper.py`, `loop_health.py` | stick ramp, loop statistics |
| `scripts/v2_rl_walk_mujoco.py` | the "stability suite" block wires them to the servos |
| `scripts/analyze_falls.py`, `scripts/voltage_sag_test.py` | analysis and supply test |
| `policies/` | ONNX files and their descriptions; `policies/README.md` to add one |

Tests, all off-robot: `cd mini_bdx_runtime/mini_bdx_runtime && for t in test_*.py; do
python3 "$t"; done`. `test_walk_stability.py` runs the real walk loop on a simulated duck
through switching, falling, landing, recovering and getting up, and checks every servo
write for speed and joint limits.

Credits: walker and get-up policies and the get-up contract by Dhawal Kabra and team
(dk5426/open-duck-curation, Apache-2.0). Runtime behaviour around the get-up and the
supply-wiring finding follow atlantis235's report on the Open Duck Discord. The stick
ramp follows vquintin's runtime. Other published policies: [ALTERNATE_POLICIES.md](ALTERNATE_POLICIES.md).
