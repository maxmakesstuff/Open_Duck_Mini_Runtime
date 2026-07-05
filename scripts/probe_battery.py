"""
One-shot battery/temperature probe — run on the robot to confirm what the pinned
rustypot build exposes and whether HWI.get_present_voltage's 0.1 V scale is right.

    cd scripts && python3 probe_battery.py

Prints, per servo: the raw register value returned by rustypot (if the method
exists) and the x0.1 interpretation (volts). A 2S LiPo reads ~6.6-8.4 V, so if the
x0.1 column looks like ~7-8 the scale is correct; if the RAW column already looks
like ~7-8 then rustypot returns volts and you should set HWI.VOLTAGE_SCALE = 1.0.

Safe: read-only, does not enable torque or move anything.
"""
import time
import rustypot

from mini_bdx_runtime.duck_config import DuckConfig
from mini_bdx_runtime.rustypot_position_hwi import HWI


def main():
    cfg = DuckConfig(ignore_default=True)
    hwi = HWI(cfg)  # opens the bus; does NOT turn on torque
    ids = list(hwi.joints.values())
    names = list(hwi.joints.keys())

    print(f"rustypot io type: {type(hwi.io).__name__}")
    for m in ("read_present_voltage", "read_present_temperature",
              "read_present_current", "read_present_load"):
        print(f"  io.{m}: {'yes' if hasattr(hwi.io, m) else 'MISSING'}")

    print("\n-- raw voltage register vs x0.1 interpretation --")
    reader = getattr(hwi.io, "read_present_voltage", None)
    if reader is None:
        print("  read_present_voltage NOT exposed by this rustypot build.")
        print("  -> telemetry voltage will be null. Options: upgrade rustypot, or")
        print("     read register 62 via a generic register read, or use pypot's")
        print("     FeetechSTS3215IO.get_present_voltage (needs the bus free).")
    else:
        try:
            raw = reader(ids)
            for name, r in zip(names, raw):
                print(f"  {name:15s} raw={float(r):7.2f}   x0.1={float(r) * 0.1:6.2f} V")
        except Exception as e:  # noqa: BLE001
            print(f"  read_present_voltage raised: {e}")

    print("\n-- HWI helpers (what telemetry will use) --")
    print(f"  get_present_voltage()     -> {hwi.get_present_voltage()} V")
    print(f"  get_present_temperature() -> {hwi.get_present_temperature()} C")

    treader = getattr(hwi.io, "read_present_temperature", None)
    if treader is not None:
        try:
            temps = treader(ids)
            print("\n-- per-servo temperature (C) --")
            for name, tv in zip(names, temps):
                print(f"  {name:15s} {float(tv):5.1f}")
        except Exception as e:  # noqa: BLE001
            print(f"  read_present_temperature raised: {e}")

    time.sleep(0.1)


if __name__ == "__main__":
    main()
