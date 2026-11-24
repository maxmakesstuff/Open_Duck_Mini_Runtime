#!/usr/bin/env python3
"""Read the duck's black-box recordings and answer three questions:

  1. Falls: how early would the soft-landing reflex have fired, with which settings?
  2. Walking: does it fire when the duck does NOT fall (false alarms)?
  3. Takes: how steady was the walk (to compare two policies on the same route)?

It replays every recording through the SAME fall_reflex code the duck runs, so what
it reports is what the duck would do.

Runs on a laptop (numpy only) or on the duck. Get the files from the Web UI
(TUNE -> Health -> Recordings) or from ~/duck_logs on the duck.

    python scripts/analyze_falls.py ~/Downloads/*.npz
    python scripts/analyze_falls.py ~/duck_logs                 # a whole folder
    python scripts/analyze_falls.py logs/ --set min_tilt_deg=15 --set predict_deg=40
    python scripts/analyze_falls.py logs/ --suggest             # propose thresholds
"""
import argparse
import glob
import math
import os
import sys

import numpy as np

try:
    from mini_bdx_runtime import fall_reflex, flight_recorder
except ImportError:   # run straight from a checkout, without installing the package
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "mini_bdx_runtime", "mini_bdx_runtime"))
    import fall_reflex
    import flight_recorder

# A recording counts as a real fall once the lean passes this.
FALLEN_DEG = 60.0
# "On the floor" for the lead time: the lean at which a duck this size has hit.
IMPACT_DEG = 70.0
WALK_MODES = (flight_recorder.MODE_CODES["walk"], flight_recorder.MODE_CODES["balance"])


def find_files(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            out += sorted(glob.glob(os.path.join(p, "*.npz")))
        else:
            out += sorted(glob.glob(p)) or [p]
    return out


def replay(cols, config):
    """Run one recording through a fresh estimator + reflex in ACTIVE mode.
    Returns per-tick tilt (deg), predicted lean (deg) and the trigger row or None.
    Only ticks where the walk policy was driving can trigger, as on the duck."""
    est = fall_reflex.TiltEstimator()
    reflex = fall_reflex.FallReflex(dict(config, mode="active"))
    n = len(cols["t"])
    tilt = np.zeros(n)
    pred = np.zeros(n)
    trigger = None
    for i in range(n):
        dt = float(cols["dt"][i]) if 0.0 < cols["dt"][i] < 0.2 else 0.02
        gyro = cols["gyro"][i]
        tilt[i] = est.update(gyro, cols["acc"][i], dt)
        walking = int(round(float(cols["mode"][i]))) in WALK_MODES
        if trigger is None:
            ev = reflex.update(tilt[i], est.rate_deg_s, float(np.linalg.norm(gyro)),
                               float(cols["t"][i]), enabled=walking)
            if ev in ("trigger", "trigger_soft"):
                trigger = i
        pred[i] = reflex.predicted_deg if trigger is None or trigger == i else pred[i - 1]
    return tilt, pred, trigger


def analyse(cols, meta, config):
    """One recording -> a dict of findings."""
    t = cols["t"]
    tilt, pred, trig = replay(cols, config)
    walking = np.isin(np.round(cols["mode"]).astype(int), WALK_MODES)
    fell = bool(np.any(tilt >= FALLEN_DEG))
    out = {"reason": meta.get("reason", "?"), "policy": meta.get("policy", "?"),
           "seconds": float(t[-1] - t[0]) if len(t) > 1 else 0.0, "fell": fell,
           "trigger_s": None, "trigger_tilt": None, "lead_s": None, "false_alarm": False}
    if fell:
        impact = int(np.argmax(tilt >= IMPACT_DEG)) if np.any(tilt >= IMPACT_DEG) \
            else int(np.argmax(tilt >= FALLEN_DEG))
        out["impact_s"] = float(t[impact])
        if trig is not None and trig <= impact:
            out.update(trigger_s=float(t[trig]), trigger_tilt=float(tilt[trig]),
                       lead_s=float(t[impact] - t[trig]))
        # what the duck's own slow check did: first paused tick after the impact
        later = np.nonzero(~walking & (np.arange(len(t)) > impact))[0]
        out["old_detect_s"] = float(t[later[0]] - t[impact]) if len(later) else None
    else:
        out["false_alarm"] = trig is not None
        if trig is not None:
            out.update(trigger_s=float(t[trig]), trigger_tilt=float(tilt[trig]))

    # steadiness while walking and upright (for comparing takes)
    steady = walking & (tilt < 35.0)
    if np.count_nonzero(steady) > 25:
        gyro = cols["gyro"][steady]
        dts = cols["dt"][steady]
        dts = dts[(dts > 0) & (dts < 1.0)]
        out.update(
            walk_s=float(np.count_nonzero(steady)) * 0.02,
            tilt_mean=float(np.mean(tilt[steady])),
            tilt_peak=float(np.max(tilt[steady])),
            pred_peak=float(np.max(pred[steady])),
            wobble=float(np.sqrt(np.mean(gyro[:, 0] ** 2 + gyro[:, 1] ** 2))),
            late_pct=float(100.0 * np.mean(dts > 0.025)) if len(dts) else 0.0,
            worst_ms=float(1000.0 * np.max(dts)) if len(dts) else 0.0,
        )
        cmd_yaw, yaw = cols["commands"][steady][:, 2], gyro[:, 2]
        if np.std(cmd_yaw) > 0.05:      # only meaningful if the take included turns
            out["yaw_follow"] = float(np.corrcoef(cmd_yaw, yaw)[0, 1])
    return out


def suggest(results, config):
    """Propose thresholds from what was recorded: above the worst lean seen while
    walking without falling, by a margin."""
    walks = [r for r in results if "tilt_peak" in r and not r["fell"]]
    if not walks:
        return None
    peak = max(r["tilt_peak"] for r in walks)
    pred = max(r["pred_peak"] for r in walks)
    return {"min_tilt_deg": float(min(60.0, max(12.0, math.ceil(peak * 1.25)))),
            "predict_deg": float(min(90.0, max(30.0, math.ceil(pred * 1.2)))),
            "hard_deg": float(min(90.0, max(config["hard_deg"], math.ceil(peak * 1.6))))}


def _fmt(v, unit="", digits=2):
    return "–" if v is None else f"{v:.{digits}f}{unit}"


def report(files, config, want_suggest=False, out=print):
    results = []
    for path in files:
        try:
            cols, meta = flight_recorder.load(path)
        except Exception as e:  # noqa: BLE001
            out(f"{os.path.basename(path)}: cannot read ({e})")
            continue
        if len(cols["t"]) < 5:
            continue
        r = analyse(cols, meta, config)
        r["file"] = os.path.basename(path)
        results.append(r)

    falls = [r for r in results if r["fell"]]
    walks = [r for r in results if not r["fell"]]
    out(f"{len(results)} recordings: {len(falls)} with a fall, {len(walks)} without.\n")

    if falls:
        out("FALLS  (lead = how long before the floor the reflex fires)")
        out(f"  {'file':<44}{'fires at':>10}{'lead':>9}{'old check':>12}")
        for r in falls:
            fires = "never" if r["trigger_tilt"] is None else f"{r['trigger_tilt']:.0f}° lean"
            old = "–" if r.get("old_detect_s") is None else f"+{r['old_detect_s']:.1f} s"
            out(f"  {r['file']:<44}{fires:>10}{_fmt(r['lead_s'], ' s'):>9}{old:>12}")
        leads = [r["lead_s"] for r in falls if r["lead_s"] is not None]
        missed = len(falls) - len(leads)
        if leads:
            out(f"  caught {len(leads)} of {len(falls)}; lead {min(leads):.2f}–{max(leads):.2f} s, "
                f"median {float(np.median(leads)):.2f} s" + (f"; MISSED {missed}" if missed else ""))
        else:
            out(f"  caught none of {len(falls)}: the thresholds are too high for these falls")
        out("")

    if walks:
        out("WALKING  (no fall in the recording)")
        out(f"  {'file':<44}{'policy':<16}{'peak lean':>10}{'predicted':>10}"
            f"{'wobble':>8}{'late':>7}{'alarm':>7}")
        for r in walks:
            if "tilt_peak" not in r:
                continue
            out(f"  {r['file']:<44}{str(r['policy'])[:15]:<16}{r['tilt_peak']:>9.1f}°"
                f"{r['pred_peak']:>9.1f}°{r['wobble']:>8.2f}{r['late_pct']:>6.1f}%"
                f"{'YES' if r['false_alarm'] else 'no':>7}")
        alarms = sum(1 for r in walks if r["false_alarm"])
        out(f"  false alarms: {alarms} of {len(walks)}"
            + ("  <- raise the thresholds before using Active" if alarms else ""))
        out("")

        by_policy = {}
        for r in walks:
            if "wobble" in r:
                by_policy.setdefault(str(r["policy"]), []).append(r)
        if len(by_policy) > 1:
            out("PER POLICY  (lower wobble and lean = steadier; compare the same route)")
            for pol, rs in sorted(by_policy.items()):
                secs = sum(x["walk_s"] for x in rs)
                out(f"  {pol:<20} {secs:6.0f} s walked   wobble {np.mean([x['wobble'] for x in rs]):.2f} rad/s"
                    f"   mean lean {np.mean([x['tilt_mean'] for x in rs]):.1f}°"
                    f"   peak lean {max(x['tilt_peak'] for x in rs):.1f}°")
            out("")

    out("Settings used: " + ", ".join(f"{k}={config[k]:g}" for k in
                                       ("min_tilt_deg", "predict_deg", "horizon_s",
                                        "hard_deg", "confirm_ticks")))
    if want_suggest:
        s = suggest(results, config)
        if s is None:
            out("No suggestion: record some walking without a fall first (a take).")
        else:
            out("Suggested from this data: " + ", ".join(f"{k}={v:g}" for k, v in s.items()))
            out("Re-run with:  " + " ".join(f"--set {k}={v:g}" for k, v in s.items()))
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="+", help="recordings (.npz) or folders of them")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="override a reflex setting, e.g. --set min_tilt_deg=15")
    ap.add_argument("--suggest", action="store_true",
                    help="propose thresholds from the walking recordings")
    args = ap.parse_args(argv)
    overrides = {}
    for item in args.set:
        key, _, value = item.partition("=")
        if key not in fall_reflex.LIMITS:
            ap.error(f"unknown setting '{key}' (known: {', '.join(fall_reflex.LIMITS)})")
        overrides[key] = value
    files = find_files(args.paths)
    if not files:
        print("No recordings found.")
        return 1
    report(files, fall_reflex.clean_config(overrides), want_suggest=args.suggest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
