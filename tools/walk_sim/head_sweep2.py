import sys, math, numpy as np, mujoco, walksim as W
N = 8
class Fixed:
    """A stand-in engine: a constant head offset, or yaw following the turn command with a sign and size."""
    def __init__(self, pitch=0.0, yaw=0.0, roll=0.0, follow=0.0):
        self.o = [0.0, pitch, yaw, roll]; self.follow = follow; self.body_scale = 1.0; self.k = 0.0
    def update(self, t, dt, turn=0.0, side=0.0, walking=False):
        self.k = min(1.0, self.k + dt / 0.5)                 # ease in over 0.5 s
        o = [v * self.k for v in self.o]; o[2] += self.follow * turn * self.k
        return o
def run(r, sched, seed, eng=None, bob=0.0):
    rng = np.random.default_rng(seed); r.reset(rng, 0.02); r.t = 0.0; r.bob_deg = bob; r.engine = eng
    tilt = []; vx = []; wz = []; n = 0
    for steps, cmd in sched:
        r.base_cmd = list(cmd)
        for _ in range(steps):
            r.control_step(); n += 1
            if r.fallen(): return dict(fell=True, tilt=np.nan, vx=np.nan, wz=np.nan)
            up = r.data.site_xmat[r.imu_site_id].reshape(3, 3)[2, 2]; tilt.append(math.degrees(math.acos(max(-1, min(1, up)))))
            q = r.data.qpos; yaw = np.arctan2(2 * (q[3] * q[6] + q[4] * q[5]), 1 - 2 * (q[5] ** 2 + q[6] ** 2))
            if any(abs(x) > 0.01 for x in cmd) and n > 150:
                vx.append(np.cos(yaw) * r.data.qvel[0] + np.sin(yaw) * r.data.qvel[1]); wz.append(r.data.qvel[5])
    return dict(fell=False, tilt=float(np.sqrt(np.mean(np.square(tilt)))), vx=float(np.mean(vx)), wz=float(np.mean(wz)))
def show(label, res):
    ok = [x for x in res if not x["fell"]]
    print(f"  {label:44s} falls {len(res)-len(ok)}/{len(res)}  lean {np.mean([x['tilt'] for x in ok]):4.1f}°  fwd {np.mean([x['vx'] for x in ok]):.3f} m/s  turn {np.mean([x['wz'] for x in ok]):5.2f} rad/s")
FWD = [(100, [0, 0, 0]), (1000, [0.1, 0, 0])]; TURN = [(100, [0, 0, 0]), (500, [0.05, 0, 0.8])]
r = W.HeadRunner(W.REPO + "/policies/dk5426_walker_258M.onnx")
print("dk5426 walker. Forward walk with the head held in one pose:")
for label, kw in (("straight", {}), ("yaw 0.2 (11°)", {"yaw": 0.2}), ("yaw 0.4 (23°)", {"yaw": 0.4}), ("yaw 0.6 (34°)", {"yaw": 0.6}),
                  ("pitch up 0.2", {"pitch": 0.2}), ("pitch down 0.3", {"pitch": -0.3}), ("lean 0.15 (9°)", {"roll": 0.15}), ("lean 0.28 (16°)", {"roll": 0.28})):
    show(label, [run(r, FWD, s, Fixed(**kw)) for s in range(N)])
print("Turning left (command 0.8 rad/s) with the head yaw following the turn:")
for label, f in (("head straight", 0.0), ("into the turn, 0.45 rad at full stick", 0.45), ("into the turn, 0.20 rad", 0.20), ("into the turn, 0.10 rad", 0.10), ("AWAY from the turn, 0.20 rad", -0.20), ("AWAY from the turn, 0.45 rad", -0.45)):
    show(label, [run(r, TURN, s, Fixed(follow=f)) for s in range(N)])
print("Does a heavier or stiffer head make the neck bob (10°) or the animations dangerous?")
import head_life as hl, random
for label, mass, stiff in (("head as modelled, soft (kp 8)", 1.0, False), ("head mass x1.6, soft", 1.6, False), ("head mass x2.2, soft", 2.2, False), ("head mass x1.6, stiff (kp 30)", 1.6, True)):
    rr = W.HeadRunner(W.REPO + "/policies/dk5426_walker_258M.onnx", soft_head=not stiff)
    b = rr.model.body("head_assembly").id; rr.model.body_mass[b] *= mass; rr.model.body_inertia[b] *= mass
    for cond, eng, bob in (("no head motion", None, 0.0), ("neck bob 10°", None, 10.0), ("animations default", "d", 0.0), ("animations max", "m", 0.0)):
        res = []
        for s in range(N):
            e = None if eng is None else hl.HeadLife({"look": True} if eng == "d" else {"look": True, "speed": 1.6, "look_scale": 1.5, "busy": 2.0}, rng=random.Random(s))
            res.append(run(rr, [(100, [0, 0, 0]), (1500, [0.1, 0, 0])], s, e, bob))
        show(f"{label} | {cond}", res)
