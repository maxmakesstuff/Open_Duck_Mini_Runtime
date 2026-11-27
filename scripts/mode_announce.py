"""
Say which mode is starting, right after the foot press.

The launcher starts walk or head puppet depending on which foot switch is held.
Loading everything takes a Pi Zero several seconds, so this plays a short clip
first thing: imported at the very top of both scripts, before the heavy imports.

It lives in scripts/ (not in the package) on purpose: importing anything from
mini_bdx_runtime pulls in onnxruntime, which is exactly the wait to bridge.
Standard library only.

The speaker is opened exclusively (no dmix on the duck), so the clip must be
done before pygame opens it: call finish() before anything touches the mixer.

Nothing here may stop a mode from starting: every failure ends silent.
"""
import json
import os
import subprocess

CLIPS = {"walk": "walk_mode.wav", "head_puppet": "headpuppet_mode.wav"}
ASSETS = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "mini_bdx_runtime", "assets", "modes"
)
CONFIG = os.path.join(os.path.expanduser("~"), "duck_config.json")
WAIT_S = 3.0  # the clips are about 1 s; never hold the start longer than this


def speaker_enabled(config_path=CONFIG):
    """True only if the duck's config says it has a speaker."""
    try:
        with open(config_path) as f:
            return bool(json.load(f).get("expression_features", {}).get("speaker", False))
    except (OSError, ValueError, AttributeError):
        return False


def start(mode, assets_dir=ASSETS, config_path=CONFIG, popen=subprocess.Popen):
    """Start playing the clip for `mode` in the background.
    Returns the player process, or None if nothing is playing."""
    try:
        clip = CLIPS.get(mode)
        if clip is None or not speaker_enabled(config_path):
            return None
        path = os.path.join(assets_dir, clip)
        if not os.path.isfile(path):
            return None
        return popen(
            ["aplay", "-q", path],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001 - a missing player must never stop the duck
        return None


def finish(proc, timeout=WAIT_S):
    """Wait until the clip is done so the speaker is free again; cut it off if it
    overruns. Safe to call with None."""
    if proc is None:
        return
    try:
        proc.wait(timeout=timeout)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
            proc.wait(timeout=1.0)
        except Exception:  # noqa: BLE001
            pass
