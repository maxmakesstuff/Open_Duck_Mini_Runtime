"""Walk policy in MuJoCo with the runtime's head animations on top (head command + overlay as on the robot)."""
import sys, os, types, numpy
jax = types.ModuleType("jax"); jax.numpy = numpy; jax.Array = numpy.ndarray
sys.modules["jax"] = jax; sys.modules["jax.numpy"] = numpy
PG = os.environ["PG"]; REPO = os.environ["REPO"]
os.chdir(PG); sys.path.insert(0, PG); sys.path.insert(0, os.path.join(PG, "..", "curation", "tools"))
# The simulator only needs the model files from the training package; stand in for the
# training-only modules (jax, mjx, mujoco_playground, etils) it would otherwise import.
import glob, pathlib
_XM = os.path.join(PG, "playground/open_duck_mini_v2/xmls")
def _assets():
    out = {}
    for f in glob.glob(_XM + "/*.xml") + glob.glob(_XM + "/assets/*"):
        if os.path.isfile(f): out[os.path.basename(f)] = open(f, "rb").read()
    return out
_base = types.ModuleType("playground.open_duck_mini_v2.base"); _base.get_assets = _assets
sys.modules["playground.open_duck_mini_v2.base"] = _base
_et = types.ModuleType("etils"); _ep = types.ModuleType("etils.epath"); _ep.Path = pathlib.Path; _et.epath = _ep
sys.modules.setdefault("etils", _et); sys.modules.setdefault("etils.epath", _ep)
sys.path.insert(0, os.path.join(REPO, "mini_bdx_runtime", "mini_bdx_runtime"))
import numpy as np, mujoco, random, math
from duck_eval import Runner
import head_life as hl

SCENE = "playground/open_duck_mini_v2/xmls/scene_flat_terrain_backlash.xml"
REF = "playground/open_duck_mini_v2/data/polynomial_coefficients.pkl"
HEAD = ["neck_pitch", "head_pitch", "head_yaw", "head_roll"]
LO = np.array([-0.34, -0.78, -0.8, -0.5]); HI = np.array([1.1, 0.3, 0.8, 0.5])


class HeadRunner(Runner):
    """Runner + what the robot does with the head: offsets go into the command the policy sees,
    and the command is added to the policy's own head targets. Head servos softer (kp 8 vs 30)."""
    def __init__(self, onnx, soft_head=True):
        super().__init__(SCENE, onnx, REF)
        if soft_head:
            for n in HEAD:
                i = self.model.actuator(n).id
                self.model.actuator_gainprm[i, 0] *= 8 / 30.; self.model.actuator_biasprm[i, 1] *= 8 / 30.
        self.engine = None; self.bob_deg = 0.0; self.t = 0.0; self.base_cmd = [0.0] * 3
        self.accel_fix = True

    def get_obs(self, data, command):
        obs = super().get_obs(data, command)
        obs[3] -= 1.3          # upstream's viewer adds +1.3 to accel x; training and the robot do not
        return obs

    def control_step(self):
        self.t += 0.02
        cmd = list(self.base_cmd) + [0.0] * 4
        off = [0.0] * 4
        if self.engine is not None:
            driving = any(abs(c) > 0.01 for c in self.base_cmd)
            off = self.engine.update(self.t, 0.02, turn=cmd[2], side=cmd[1], walking=driving)
            s = self.engine.body_scale
            cmd[1] *= s; cmd[2] *= s
        if self.bob_deg:
            ph = self.imitation_i / self.PRM.nb_steps_in_period * 2 * np.pi
            off = list(off); off[0] += math.radians(self.bob_deg) * math.sin(2 * ph)
        cmd[3:7] = np.clip(np.array(off), LO, HI)
        self.commands = cmd
        # --- Runner.control_step with the robot's head overlay added
        self.imitation_i = (self.imitation_i + self.phase_frequency_factor) % self.PRM.nb_steps_in_period
        a = self.imitation_i / self.PRM.nb_steps_in_period * 2 * np.pi
        self.imitation_phase = np.array([np.cos(a), np.sin(a)])
        obs = self.get_obs(self.data, self.commands)
        action = self.policy.infer(obs)
        self.last_last_last_action = self.last_last_action.copy()
        self.last_last_action = self.last_action.copy()
        self.last_action = action.copy()
        self.motor_targets = self.default_actuator + action * self.action_scale
        lim = self.max_motor_velocity * (self.sim_dt * self.decimation)
        self.motor_targets = np.clip(self.motor_targets, self.prev_motor_targets - lim, self.prev_motor_targets + lim)
        self.prev_motor_targets = self.motor_targets.copy()
        ctrl = self.motor_targets.copy()
        ctrl[5:9] += np.array(self.commands[3:7])
        self.data.ctrl = ctrl
        for _ in range(self.decimation):
            mujoco.mj_step(self.model, self.data)

    def run(self, schedule, seed, engine_cfg=None, bob_deg=0.0):
        """schedule [(steps, [vx, vy, wz])]. Returns dict(fell_s, tilt_rms, tilt_p95, vx, wz, n)."""
        rng = np.random.default_rng(seed)
        self.reset(rng, 0.02); self.t = 0.0; self.bob_deg = bob_deg
        self.engine = hl.HeadLife(engine_cfg, rng=random.Random(seed)) if engine_cfg else None
        tilt = []; vx = []; wz = []; cmds = []; n = 0
        for steps, cmd in schedule:
            self.base_cmd = list(cmd)
            for _ in range(steps):
                self.control_step(); n += 1
                if self.fallen():
                    return dict(fell_s=n * 0.02, tilt_rms=float(np.sqrt(np.mean(np.square(tilt)))) if tilt else 0, tilt_p95=float(np.percentile(tilt, 95)) if tilt else 0, vx=float(np.mean(vx)) if vx else 0, wz=float(np.mean(wz)) if wz else 0, n=n)
                up = self.data.site_xmat[self.imu_site_id].reshape(3, 3)[2, 2]
                tilt.append(math.degrees(math.acos(max(-1.0, min(1.0, up)))))
                q = self.data.qpos; yaw = np.arctan2(2 * (q[3] * q[6] + q[4] * q[5]), 1 - 2 * (q[5] ** 2 + q[6] ** 2))
                c, s = np.cos(yaw), np.sin(yaw)
                if any(abs(x) > 0.01 for x in cmd):
                    vx.append(c * self.data.qvel[0] + s * self.data.qvel[1]); wz.append(self.data.qvel[5])
        return dict(fell_s=None, tilt_rms=float(np.sqrt(np.mean(np.square(tilt)))), tilt_p95=float(np.percentile(tilt, 95)), vx=float(np.mean(vx)) if vx else 0.0, wz=float(np.mean(wz)) if wz else 0.0, n=n)
