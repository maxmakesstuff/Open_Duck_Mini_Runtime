#!/usr/bin/env python3
"""Measure each servo's supply voltage WHILE THE LEGS WORK.

Why: on a stock Open Duck Mini the servo power enters at one point and runs through
the whole daisy chain. Under load the servos at the far end of a leg (knee, ankle)
can see much less voltage than the rest (atlantis235 measured 3.6 V against 7 V on
theirs). That shows up as bus errors and as falls that look like a bad policy. The
walk loop cannot read voltages while walking, so this script does it on its own:
the duck stands, bobs gently on its knees, and every servo's voltage is logged.

Run it ON THE DUCK with the walk stopped, the duck standing on the floor:

    python scripts/turn_on.py              # stands the duck in its stance, torque on
    python scripts/voltage_sag_test.py     # keep a hand near the duck
    python scripts/turn_off.py

What it does to the robot: both knees move a few degrees around where they stand,
slowly, a handful of times. Nothing else moves. It never sends an absolute pose: it
reads where the knees are and only adds a small, speed-limited offset to that.

Reading the result: "sag" is rest minus the lowest reading. All servos sagging
together is the battery. One leg's far end sagging more than the rest is wiring:
feed that leg from both ends, and check the knee and ankle cables.
"""
import argparse
import math
import sys
import time

JOINTS = {
    "left_hip_yaw": 20, "left_hip_roll": 21, "left_hip_pitch": 22, "left_knee": 23,
    "left_ankle": 24, "neck_pitch": 30, "head_pitch": 31, "head_yaw": 32, "head_roll": 33,
    "right_hip_yaw": 10, "right_hip_roll": 11, "right_hip_pitch": 12, "right_knee": 13,
    "right_ankle": 14,
}
KNEES = ("left_knee", "right_knee")
VOLT_SCALE = 0.1                 # register is in 0.1 V
MAX_AMPLITUDE_DEG = 10.0
MAX_STEP_DEG = 0.6               # per sample: at ~40 samples/s that is ~24 deg/s


def detect_units(knee_readings):
    """Is this library reporting degrees or radians? A knee in the stance sits near
    78 deg / 1.37 rad. Returns degrees-per-unit, or None if neither fits (then the
    duck is not in its stance and the test must not move anything)."""
    if all(0.6 <= abs(v) <= 2.2 for v in knee_readings):
        return math.degrees(1.0)             # radians
    if all(35.0 <= abs(v) <= 125.0 for v in knee_readings):
        return 1.0                           # degrees
    return None


def bob_offset(t, period_s, amplitude_deg, ramp_s=1.0):
    """Knee offset (deg) at time t: a sine that fades in, so it starts at zero speed."""
    fade = min(1.0, t / ramp_s)
    return amplitude_deg * fade * math.sin(2.0 * math.pi * t / period_s)


def limit_step(previous, wanted, max_step):
    return previous + max(-max_step, min(max_step, wanted - previous))


def summarise(rest, lowest):
    """rest / lowest: {joint: volts}. Returns (rows, verdict lines)."""
    rows = []
    for name in JOINTS:
        r, lo = rest.get(name), lowest.get(name)
        if r is None or lo is None:
            continue
        rows.append((name, r, lo, r - lo))
    if not rows:
        return rows, ["No voltages read."]
    sags = sorted(s for _, _, _, s in rows)
    median = sags[len(sags) // 2]
    floor = min(lo for _, _, lo, _ in rows)
    verdict = []
    outliers = [(n, s) for n, _, _, s in rows if s > median + 0.5]
    if outliers:
        verdict.append("Sagging more than the others (check the cable to it, feed that leg "
                       "from both ends): "
                       + ", ".join(f"{n} -{s:.1f} V" for n, s in outliers))
    if median > 0.8:
        verdict.append(f"All servos drop about {median:.1f} V under this light load: "
                       "the battery or its main lead is weak.")
    if floor < 6.0:
        verdict.append(f"Lowest reading {floor:.1f} V. Below about 6 V the servos lose "
                       "torque and start to miss messages.")
    if not verdict:
        verdict.append(f"Even supply: every servo within 0.5 V of the median sag "
                       f"({median:.1f} V), lowest reading {floor:.1f} V.")
    return rows, verdict


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--amplitude", type=float, default=6.0, help="knee bob in degrees (max 10)")
    ap.add_argument("--period", type=float, default=1.2, help="seconds per bob")
    ap.add_argument("--cycles", type=int, default=8)
    ap.add_argument("--yes", action="store_true", help="do not ask before moving")
    args = ap.parse_args(argv)
    amplitude = max(0.0, min(MAX_AMPLITUDE_DEG, args.amplitude))
    period = max(0.6, args.period)

    from pypot.feetech import FeetechSTS3215IO
    io = FeetechSTS3215IO(args.port, baudrate=1000000, use_sync_read=True)
    ids = list(JOINTS.values())
    names = list(JOINTS.keys())

    def volts():
        return {n: round(v * VOLT_SCALE, 2) for n, v in zip(names, io.get_present_voltage(ids))}

    try:
        rest = volts()
        print("At rest:")
        for n in names:
            print(f"  {n:<16} {rest[n]:.1f} V")

        knee_ids = [JOINTS[k] for k in KNEES]
        start = list(io.get_present_position(knee_ids))
        per_unit = detect_units(start)
        if per_unit is None:
            print(f"\nThe knees read {start}: that is not the stance pose. Nothing moved.\n"
                  "Run scripts/turn_on.py first so the duck stands, then try again.")
            return 1
        if not args.yes:
            print(f"\nBoth knees will bob ±{amplitude:.0f}° for about "
                  f"{args.cycles * period:.0f} s. The duck must STAND ON THE FLOOR with "
                  "torque on. Keep a hand near it.")
            if input("Start? (y/N) ").strip().lower() != "y":
                print("Nothing moved.")
                return 0

        lowest = dict(rest)
        sent = [0.0, 0.0]                    # offset (deg) currently commanded per knee
        t0 = time.time()
        total = args.cycles * period
        samples = 0
        while True:
            t = time.time() - t0
            want = bob_offset(t, period, amplitude) if t < total else 0.0
            sent = [limit_step(s, want, MAX_STEP_DEG) for s in sent]
            io.set_goal_position({kid: s0 + off / per_unit
                                  for kid, s0, off in zip(knee_ids, start, sent)})
            for n, v in volts().items():
                if v < lowest[n]:
                    lowest[n] = v
            samples += 1
            if t >= total and all(abs(s) < 1e-6 for s in sent):
                break
            if t > total + 5.0:              # never loop forever
                break
        rate = samples / max(1e-6, time.time() - t0)
        io.set_goal_position(dict(zip(knee_ids, start)))

        rows, verdict = summarise(rest, lowest)
        print(f"\nUnder load ({samples} readings, {rate:.0f} per second):")
        print(f"  {'servo':<16}{'rest':>7}{'lowest':>9}{'sag':>7}")
        for name, r, lo, sag in rows:
            print(f"  {name:<16}{r:>6.1f}V{lo:>8.1f}V{sag:>6.1f}V")
        print()
        for line in verdict:
            print(line)
        print("\nThis is a light load. A get-up pulls far more current; if a leg sags "
              "here, it will sag much more there.")
        return 0
    finally:
        try:
            io.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    sys.exit(main())
