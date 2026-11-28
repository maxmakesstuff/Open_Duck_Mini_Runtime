# Walk simulation with head animations

How the head animations were tested without a duck (2026-11-28): the walk policies in
MuJoCo, in a loop that copies what the robot does with the head.

Needs, outside this repository:
- a checkout of `apirrone/Open_Duck_Playground` with the patches of
  `dk5426/open-duck-curation` applied, and that curation checkout next to it
  (`<somewhere>/playground`, `<somewhere>/curation`);
- Python with `mujoco`, `onnxruntime`, `numpy` (no jax needed, the scripts stand in for it).

```bash
export PG=<somewhere>/playground REPO=<this repository> STOCK_ONNX=<path>/BEST_WALK_ONNX_2.onnx
python tools/walk_sim/head_sweep4.py all     # told / seen / hidden, both policies, heavy head
python tools/walk_sim/head_sweep5.py         # how wrong the assumed head lag may be
```

- `walksim.py`: `HeadRunner`, the walk loop with the runtime's `head_life` on top.
- `head_sweep2.py`: single head poses, turning with a head lead, heavier and stiffer heads.
- `head_sweep4.py`: the three ways an offset can reach the head (see `head_life.py`).
- `head_sweep5.py`: sensitivity of "hidden" to the assumed head lag.

Results are in the repository's `CLAUDE.md`. A simulation is not the duck: it did not
reproduce the fall the removed neck bob caused on the real robot until the head was
made heavier and stiffer than modelled.
