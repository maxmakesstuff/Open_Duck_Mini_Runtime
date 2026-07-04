"""
Interactively measure per-joint soft offsets (the servo horns are always mounted
with some offset) AND, when you're done, optionally write them straight into
~/duck_config.json for you -- no more copy/paste.

Measurement is unchanged: the robot moves to its zero position, then for each joint
you disable its torque, hand-move it to the true mechanical zero, and the script
records offset = new_pos - current_pos (measured from a zero-offset baseline).

At the end it shows a review table of your CURRENT config offsets vs the freshly
measured ones and, on confirmation, updates only the `joints_offsets` key of
~/duck_config.json -- backing the file up first and round-tripping every other
field untouched. Joints you SKIP (press "s") or don't confirm keep whatever value
is already in the config; only joints you confirm this run are overwritten.
Ctrl+C at any point turns the motors off and writes nothing.
"""

import json
import os
import shutil
import sys
import time

HOME_DIR = os.path.expanduser("~")
CONFIG_PATH = os.path.join(HOME_DIR, "duck_config.json")
EXAMPLE_CONFIG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "example_config.json"
)


# --------------------------- pure helpers (import-safe, unit-testable) ---------------------------
def merge_offsets(existing, found, joint_order):
    """The offsets dict to persist. Start from `existing` (what's already in
    duck_config.json), overwrite ONLY the joints confirmed this run (`found`),
    preserve every skipped joint's existing value, keep `joint_order`. Joints
    absent from both default to 0.0. Any extra keys in `existing` are kept."""
    merged = {}
    for j in joint_order:
        if j in found:
            merged[j] = found[j]
        elif j in existing:
            merged[j] = existing[j]
        else:
            merged[j] = 0.0
    for k, v in existing.items():          # defensive: don't drop unknown extras
        if k not in merged:
            merged[k] = v
    return merged


def format_review(existing, found, joint_order):
    """A printable current-vs-new offset table so you see exactly what changes
    before anything is written."""
    header = f"{'joint':<16}{'current':>11}{'new':>11}   status"
    lines = [header, "-" * len(header)]
    for j in joint_order:
        cur = existing.get(j)
        cur_s = f"{cur:.4f}" if isinstance(cur, (int, float)) else "--"
        if j in found:
            new = found[j]
            changed = cur is None or abs(new - cur) > 1e-9
            lines.append(f"{j:<16}{cur_s:>11}{new:>11.4f}   {'change' if changed else 'same'}")
        else:
            lines.append(f"{j:<16}{cur_s:>11}{cur_s:>11}   skip -> keep")
    return "\n".join(lines)


def load_config(path):
    """Load a duck_config.json into a dict, or None if the file doesn't exist."""
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        return json.load(f)


def write_offsets(path, config, merged, backup=True):
    """Set config['joints_offsets']=merged and write `path` back. Backs the file up
    first (timestamped) if it exists. `config` is the full existing dict so every
    other field is preserved. Returns the backup path (or None)."""
    backup_path = None
    if backup and os.path.exists(path):
        backup_path = f"{path}.{time.strftime('%Y%m%d-%H%M%S')}.bak"
        shutil.copy2(path, backup_path)
    config = dict(config)
    config["joints_offsets"] = merged
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    return backup_path


def _seed_config_from_example():
    """~/duck_config.json is missing: seed a new dict from example_config.json so
    the OTHER required fields exist, else fall back to an offsets-only minimal one."""
    if os.path.exists(EXAMPLE_CONFIG):
        with open(EXAMPLE_CONFIG, "r") as f:
            cfg = json.load(f)
        print(f"~/duck_config.json missing -- seeding a new one from {EXAMPLE_CONFIG}.")
        print("  !! Review the OTHER fields (feature flags, IMU, web) afterward.")
        return cfg
    print("~/duck_config.json missing and no example_config.json found --")
    print("  writing a MINIMAL config with only offsets. Fill in the rest!")
    return {}


def review_and_maybe_write(found, joint_order, config_path=CONFIG_PATH):
    """Show the review table and, on confirmation, persist the merged offsets."""
    config = load_config(config_path)
    existing = (config or {}).get("joints_offsets", {})

    print("")
    print("================ offset review ================")
    print(format_review(existing, found, joint_order))
    print("===============================================")

    if not found:
        print("No joints were confirmed this run -- nothing to write.")
        return

    merged = merge_offsets(existing, found, joint_order)
    res = input(f"\nWrite these offsets to {config_path}? (y/N) ").strip().lower()
    if res != "y":
        print("Not writing. Merged offsets for manual copy into duck_config.json:")
        print(json.dumps({"joints_offsets": merged}, indent=2))
        return

    if config is None:
        config = _seed_config_from_example()
    backup = write_offsets(config_path, config, merged, backup=True)
    if backup:
        print(f"Backed up previous config -> {backup}")
    print(
        f"Wrote {len(found)} updated offset(s) to {config_path}. "
        "Skipped joints kept their existing values."
    )


# --------------------------------- interactive measurement ---------------------------------
def main():
    from mini_bdx_runtime.rustypot_position_hwi import HWI
    from mini_bdx_runtime.duck_config import DuckConfig

    dummy_config = DuckConfig(config_json_path=None, ignore_default=True)

    print("======")
    print(
        "Warning : this script will move the robot to its zero position quiclky, make sure it is safe to do so"
    )
    print("======")
    print("")
    input(
        "Press any key to start. The robot will move to its zero position. Make sure it is safe to do so. At any time, press ctrl+c to stop, the motors will be turned off."
    )

    hwi = HWI(dummy_config)

    hwi.init_pos = hwi.zero_pos
    hwi.set_kds([0] * len(hwi.joints))
    hwi.turn_on()
    print("")
    print("")
    print("")
    print("")
    hwi.set_position_all(hwi.zero_pos)
    time.sleep(1)

    found_offsets = {}   # only joints CONFIRMED this run land here
    try:
        for i, joint_name in enumerate(hwi.joints.keys()):
            joint_id = hwi.joints[joint_name]
            ok = False
            while not ok:
                res = input(f" === Setting up {joint_name} === (Y/(s)kip : ").lower()
                if res == "s":
                    break
                hwi.set_position_all(hwi.zero_pos)
                time.sleep(0.5)
                current_pos = hwi.get_present_positions()[i]
                if current_pos is None:
                    continue
                hwi.io.disable_torque([joint_id])
                input(
                    f"{joint_name} is now turned off. Move it to the desired zero position and press any key to confirm the offset"
                )
                new_pos = hwi.get_present_positions()[i]
                offset = new_pos - current_pos
                print(f" ---> Offset is {offset}")
                hwi.joints_offsets[joint_name] = offset
                input(
                    "Press any key to move the motor to its zero position with offset taken into account"
                )
                hwi.set_position_all(hwi.zero_pos)
                time.sleep(0.5)
                hwi.io.enable_torque([joint_id])
                res = input("Is that ok ? (Y/n)").lower()
                if res == "y" or res == "":
                    print("Ok, setting offset")
                    hwi.joints_offsets[joint_name] = offset
                    found_offsets[joint_name] = offset
                    ok = True
                    print("------")
                    print("Current offsets : ")
                    for k, v in hwi.joints_offsets.items():
                        print(f"{k} : {v}")
                    print("------")
                    print("")
                else:
                    print("Ok, let's try again")
                    hwi.joints_offsets[joint_name] = 0

                print("===")

        print("Done ! ")
        # New: offer to write straight into ~/duck_config.json (skipped joints kept).
        review_and_maybe_write(found_offsets, list(hwi.joints.keys()))

    except KeyboardInterrupt:
        print("\nInterrupted -- turning motors off. duck_config.json was NOT modified.")
        hwi.turn_off()
        sys.exit(0)


if __name__ == "__main__":
    main()
