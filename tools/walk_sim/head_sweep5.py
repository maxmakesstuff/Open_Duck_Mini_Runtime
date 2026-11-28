import os
import sys, math, random, numpy as np, walksim as W, head_life as hl
from head_sweep2 import run, show
import head_sweep4 as S
N = 10
DK = W.REPO + "/policies/dk5426_walker_258M.onnx"; ST = os.environ.get("STOCK_ONNX", "BEST_WALK_ONNX_2.onnx")
print("hidden mode: how wrong may the assumed head lag be? (animations ALL MAX, 30 s forward)")
for pname, path, soft, mass in (("dk5426, head as modelled", DK, True, 1.0), ("dk5426, pessimistic head", DK, False, 1.6), ("stock, head as modelled", ST, True, 1.0)):
    for tau in (0.0, 0.04, 0.08, 0.15, 0.3, 0.6):
        r = S.Robot(path, soft_head=soft); r.mode = "hidden"; r.lag_a = 1 - math.exp(-0.02 / tau) if tau > 0 else 1.0
        if mass != 1.0:
            b = r.model.body("head_assembly").id; r.model.body_mass[b] *= mass; r.model.body_inertia[b] *= mass
        cfg = {"look": True, "speed": 1.6, "look_scale": 1.5, "busy": 2.0}
        show(f"{pname}, assumed lag {tau:.2f} s", [run(r, [(100, [0, 0, 0]), (1500, [0.1, 0, 0])], s, hl.HeadLife(cfg, rng=random.Random(s))) for s in range(N)])
