# Alternate policies for Open Duck Mini v2

Status: 2026-11-24. A survey of published ONNX policies that could replace or extend the
stock walk. Nothing listed here has run on our ducks yet. The dk5426 walker and get-up
are built into the runtime as selectable policies: see [STABILITY_SUITE.md](STABILITY_SUITE.md).

Scope: about 480 GitHub repos (forks of apirrone's Open_Duck_Mini, Runtime and Playground,
plus keyword searches) and Hugging Face. Mostly default branches only; rimim's policy sits
on a side branch and was added by hand, so other side branches may hide more. Discord is
not covered systematically.

## How to read this

- **In → out** is the policy's input and output size. Our runtime builds 101 inputs and
  expects 14 outputs. A matching size means the file loads; it does not mean it balances.
  Home pose, action scale, gains, IMU handling, weight and foot-switch wiring can differ.
- **Evidence** is what the author shows: real duck, simulation, or nothing.
- **Licence**: without a licence file we may test locally but must ask the author before
  shipping the file in the release image.

## Stock (what our ducks run)

| File | In → out | Notes |
|---|---|---|
| `BEST_WALK_ONNX_2.onnx` | 101 → 14 | apirrone's upstream policy. Ours is byte-identical. Found in 75 repos. |
| `BEST_WALK_ONNX.onnx` | 101 → 14 | Older upstream policy. Found in 59 repos. |

## Built into the next release

### dk5426/open-duck-curation — walk and get-up
https://github.com/dk5426/open-duck-curation · Apache-2.0 · active (last push 2026-11-24)

| File (`policies/`) | In → out | What it does | Evidence |
|---|---|---|---|
| `ours_walker_full300M_258M.onnx` | 101 → 14 | Walk. 26% better command tracking than stock in their sim test. Ignores very small forward commands from standstill. | Ran on their real duck. Gene Turnbow (Discord): higher foot lift, six steps before a fall, on a duck that did not walk before. |
| `getup_real2_80M.onnx` | 92 → 14 | Get-up from face-down. | Repo logs (to 2026-11-02): 0 of 14 held standing; 9 of 14 upright briefly. atlantis235 (Discord, 2026-11-23): works after runtime and wiring fixes, video posted. |
| `getup_real_41M.onnx` | 92 → 14 | Earlier get-up. | Repo logs: 2 of 8. |
| `getup_real_29M`, `getup_back2_101M`, `getup_173M` | 92 → 14 | Earlier get-ups, incl. from the back. | Repo logs: 0 of 12 and 0 of 3 for the last two. |
| `jump_101M.onnx` | 94 → 14 | Jump. | Does not leave the ground. |

What the get-up needs beyond the file:
- Input: the walk input minus the 7 commands and the 2 gait-clock values; action scale 1.0;
  joint limits; joint speed capped at 5.24 rad/s; fresh action history at start.
- Runtime (from atlantis235): filtered fall detection (about 0.6 s), 50% blend with the
  previous target, first 0.5 s at a third of the speed, "standing" check held 0.4 s, then
  the walk policy takes over for 3 s at zero command. The last step was the key fix.
- An older version of the get-up runtime is in the repo as patches (`robot_patches/`, dated
  2026-10-30). The fixes above are not published as of this date.
- Power: knee and ankle servos sagged to about 3.6 V. Fixed by feeding each leg chain from
  both ends (servos 14 and 24) and replacing strained cables.
- Their runtime ignores the low-voltage cutoff during get-up. We keep ours.
- Works from face-down only.

## Candidates for later

### EAOZONE/Open_Duck_Playground — emotes in one policy
https://github.com/EAOZONE/Open_Duck_Playground · no licence · no real-duck results · no videos

| File | In → out | What it does |
|---|---|---|
| `unified_motion_v1.onnx` | 205 → 14 | Stand, walk and play motions. Needs its motion data file and its own input builder. |
| `Get_Up.onnx` | 104 → 14 | Get-up from face-down. |
| `playful_high_step_walk.onnx` | 101 → 14 | Walk with high steps. |

Motions in its data: bow, head nod, double nod, head tilt, look around, crouch, sway,
diagonal sway, playful bob, high step left/right. Also a scripted "expressive walk" overlay
and an excited hop (training code only, no file). Parked for now: most work to integrate.

### rimim/Open_Duck_Mini_Runtime (Skelmir) — multi-command walk
https://github.com/rimim/Open_Duck_Mini_Runtime/tree/multi_command_policy · no licence · last push 2025-11-12

| File | In → out | What it does |
|---|---|---|
| `ONNX-multi.onnx` | 105 → 14 | Walk plus body ("trunk") pitch, yaw and roll commands and head commands. Uses foot switches. |
| `ONNX-nocontact.onnx` | 103 → 14 | Same without foot switches. |

- Trained with pushes, sensor and action delay, and a side-step recovery reward.
- Needs its own runtime (`scripts/v3_rl_walk.py` on that branch), not drop-in.
- Our own test at the time: not stable enough.
- Worth copying: each file carries its settings inside (input size, joint order, home pose,
  action offset, gait period, foot-switch use). Good model for our policy slots.

### soyeon24/open-duck-mini-v2-build — emotional walk
https://github.com/soyeon24/open-duck-mini-v2-build · no licence · simulation only · 88 files

| File | In → out | What it does |
|---|---|---|
| `from_ubai/emolo/emolo_hp_300M.onnx` | 104 → 14 | Happy / neutral / sad walk via 3 style inputs (head up, level, down). Needs head-pitch action scale 0.5. |
| `fr186…` | 101 → 14 | Walk trained at the real servo torque limit (1.86 N·m). |
| obstacle and carry-ball sets | 101 → 14 | Experiments. |

Its stand-up attempts (11 versions) never stood up in sim. Five "dance moves" exist as
keys in its sim viewer, not as policies.

### vquintin/Open_Duck_Mini_Runtime — walkers iterated on a real duck
https://github.com/vquintin/Open_Duck_Mini_Runtime · no licence · active to 2026-11-20

25 walkers, 101 → 14 (`ducks/canard/canard_walk_1..22`, `ducks/bdxv2/bdxv2_walk_1..3`).
Tuned to that duck's weight, a symmetric home pose and a different IMU mode, so not
drop-in. Useful practices: command change limited to 0.3 m/s², training with IMU delay.

### Smaller sources (no licence unless noted)

| Repo | Files | In → out | Evidence |
|---|---|---|---|
| SeanEZhou/openduck | standing v2, walking v5 | 101 → 14 | Sim. Better head tracking, less push-robust than stock. |
| calebjakemossey/Open_Duck_Playground | `walk_best_E11`, `stand_contact_E20`, `stand_v2_T2b` | 101 / 209 → 14 | None shown. Good notes on upstream training bugs. |
| kristof/bdx-mini | 4 walkers incl. a "winning policy" | 101 → 14 | Not documented. |
| dfriishecht | `STANDING_JOG_BEST` | 101 → 14 | Not documented. |
| jmachuca77 | `HEAD_CONTROL_1/2` | 101 → 14 | Not documented. |
| RXLiao/OpenDuckMini-MicroDuck-Locomotion-Training | legs-only walker | 101 → 10 | Real-robot video. |
| isopink / happylee8282 | forward, backward, crouch (Isaac Lab) | own layout | Not drop-in. |
| feisuo | `ONNX_12V_test` | 101 → 14 | For 12 V servos. |

## Not usable on our duck

- **Microduck** (`pollen-robotics/microduck-policies` on Hugging Face): bow, moonwalk,
  dance and more, but a different, smaller robot with 61 inputs.
- **mgm136044/kobi-robot**: 14 gestures with sim videos, but a larger body with arms.
- **Hugging Face `kevincity2026/…-walk-ppo`**: relabelled copies of the stock files.
- **Hugging Face `DaOn-ai/open-duck-mini-v2-rl`**: the policy file is empty.

## Related tools

- **PaulTR/Open_Duck_Mini_Animator** (Apache-2.0): keyframe animation tool with sound sync
  and robot playback. Not a policy. Shows emotes on a real duck:
  https://youtube.com/shorts/AD9E_y8t4Yc
- **roboninecom/STS32XX-Firmware** (Apache-2.0): servo firmware for robot arms. It changes
  how the servo responds, which the walk policies were not trained for. Do not flash on a
  walking duck without retraining.
