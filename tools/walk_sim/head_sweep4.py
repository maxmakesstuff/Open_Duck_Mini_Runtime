import os
import sys, math, random, numpy as np, mujoco, walksim as W, head_life as hl
from head_sweep2 import Fixed, run, show
N = 10
class Robot(W.HeadRunner):
    """The duck's loop, faithfully: the head offset is added to the head targets AFTER the policy, and those
    targets (with the offset) are what the next observation carries as 'motor targets'.
    mode 'told'   : as shipped until now - the offset is also in the command the policy sees
    mode 'seen'   : not in the command, but the policy sees the head joints and targets where the offset put them
    mode 'hidden' : the offset is taken out of everything the policy observes (command, joint positions, targets)
    """
    mode = "told"
    def control_step(self):
        self.t += 0.02
        cmd = list(self.base_cmd) + [0.0] * 4; off = [0.0] * 4
        if self.engine is not None:
            off = self.engine.update(self.t, 0.02, turn=cmd[2], side=cmd[1], walking=any(abs(c) > 0.01 for c in self.base_cmd))
            cmd[1] *= self.engine.body_scale; cmd[2] *= self.engine.body_scale
        if self.bob_deg:
            ph = self.imitation_i / self.PRM.nb_steps_in_period * 2 * np.pi
            off = list(off); off[0] += math.radians(self.bob_deg) * math.sin(2 * ph)
        off = np.clip(np.array(off, dtype=float), W.LO, W.HI)
        if self.mode == "told": cmd[3:7] = off
        self.commands = cmd
        self.imitation_i = (self.imitation_i + self.phase_frequency_factor) % self.PRM.nb_steps_in_period
        a = self.imitation_i / self.PRM.nb_steps_in_period * 2 * np.pi
        self.imitation_phase = np.array([np.cos(a), np.sin(a)])
        prev_off = getattr(self, "_off", np.zeros(4))
        self.motor_targets = self.motor_targets.copy(); self.motor_targets[5:9] += prev_off     # what the robot's obs carries
        obs = self.get_obs(self.data, self.commands)
        if self.mode == "hidden":
            lag = getattr(self, "_lag", np.zeros(4))
            obs[13 + 5:13 + 9] -= lag                      # joint positions: where the offset has moved the head by now
            obs[-4 - 14 + 5:-4 - 14 + 9] -= prev_off       # motor targets block (14 values before contacts(2)+phase(2))
        action = self.policy.infer(obs)
        self.last_last_last_action = self.last_last_action.copy(); self.last_last_action = self.last_action.copy(); self.last_action = action.copy()
        self.motor_targets = self.default_actuator + action * self.action_scale
        lim = self.max_motor_velocity * (self.sim_dt * self.decimation)
        self.motor_targets = np.clip(self.motor_targets, self.prev_motor_targets - lim, self.prev_motor_targets + lim)
        self.prev_motor_targets = self.motor_targets.copy()
        ctrl = self.motor_targets.copy(); ctrl[5:9] += off
        self._off = off
        # the head servo follows its target with a lag: track it the same way for the observation
        lag = getattr(self, "_lag", np.zeros(4)); self._lag = lag + (off - lag) * self.lag_a
        self.data.ctrl = ctrl
        for _ in range(self.decimation): mujoco.mj_step(self.model, self.data)
    def reset(self, rng, noise):
        super().reset(rng, noise); self._off = np.zeros(4); self._lag = np.zeros(4)
import os
LAG = float(os.environ.get("LAG_TAU", "0.08")); 
def table(pname, path, soft=True, mass=1.0, modes=("told", "seen", "hidden")):
    for mode in modes:
        r = Robot(path, soft_head=soft); r.mode = mode; r.lag_a = 1 - math.exp(-0.02 / LAG) if LAG > 0 else 1.0
        if mass != 1.0:
            b = r.model.body("head_assembly").id; r.model.body_mass[b] *= mass; r.model.body_inertia[b] *= mass
        print(f" [{mode}]")
        F = [(100, [0, 0, 0]), (1000, [0.1, 0, 0])]
        for label, kw in (("straight", {}), ("yaw 0.4", {"yaw": 0.4}), ("pitch up 0.25", {"pitch": 0.25}), ("lean 0.28", {"roll": 0.28})):
            show(label, [run(r, F, s, Fixed(**kw)) for s in range(N)])
        for label, f in (("turn 0.8, head straight", 0.0), ("turn 0.8, head 0.45 into the turn", 0.45)):
            show(label, [run(r, [(100, [0, 0, 0]), (500, [0.05, 0, 0.8])], s, Fixed(follow=f)) for s in range(N)])
        for label, cfg in (("animations default", {"look": True}), ("animations all max", {"look": True, "speed": 1.6, "look_scale": 1.5, "busy": 2.0})):
            show(label, [run(r, [(100, [0, 0, 0]), (1500, [0.1, 0, 0])], s, hl.HeadLife(cfg, rng=random.Random(s))) for s in range(N)])
DK = W.REPO + "/policies/dk5426_walker_258M.onnx"; ST = os.environ.get("STOCK_ONNX", "BEST_WALK_ONNX_2.onnx")
which = sys.argv[1] if len(sys.argv) > 1 else "all"
if which in ("all", "dk"): print("=== dk5426 walker, head as modelled (soft)"); table("dk", DK)
if which in ("all", "stock"): print("=== stock walk, head as modelled (soft)"); table("stock", ST)
if which in ("all", "heavy"): print("=== dk5426 walker, pessimistic head (mass x1.6, stiff)"); table("dk", DK, soft=False, mass=1.6, modes=("told", "hidden"))
