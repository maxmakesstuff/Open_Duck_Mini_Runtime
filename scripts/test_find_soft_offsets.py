"""
Off-robot tests for find_soft_offsets.py's pure config-write logic.

The hardware imports live inside main() (under __main__), so this module imports
cleanly on a dev machine and we can exercise the merge/write helpers directly.

Run from this directory:  python3 test_find_soft_offsets.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import find_soft_offsets as fso  # noqa: E402

ORDER = ["a", "b", "c"]


def test_merge_confirmed_overwrites_skipped_preserved():
    existing = {"a": 1.0, "b": 2.0, "c": 3.0}
    found = {"b": 9.9}                       # only b re-measured
    merged = fso.merge_offsets(existing, found, ORDER)
    assert merged == {"a": 1.0, "b": 9.9, "c": 3.0}


def test_merge_missing_from_both_defaults_zero():
    merged = fso.merge_offsets({"a": 1.0}, {}, ORDER)
    assert merged == {"a": 1.0, "b": 0.0, "c": 0.0}


def test_merge_keeps_extra_existing_keys():
    existing = {"a": 1.0, "b": 2.0, "c": 3.0, "weird_extra": 7.0}
    merged = fso.merge_offsets(existing, {"a": 0.5}, ORDER)
    assert merged["weird_extra"] == 7.0
    assert merged["a"] == 0.5


def test_merge_empty_found_is_identity():
    existing = {"a": 1.0, "b": 2.0, "c": 3.0}
    assert fso.merge_offsets(existing, {}, ORDER) == existing


def test_format_review_marks_change_and_skip():
    out = fso.format_review({"a": 1.0, "b": 2.0}, {"a": 5.0}, ["a", "b"])
    assert "change" in out
    assert "skip -> keep" in out


def test_write_preserves_other_fields_and_backs_up():
    cfg = {
        "web_ui": True,
        "imu_upside_down": True,
        "expression_features": {"eyes": True, "camera": False},
        "joints_offsets": {"a": 1.0, "b": 2.0, "c": 3.0},
    }
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "duck_config.json")
        with open(path, "w") as f:
            json.dump(cfg, f, indent=2)

        merged = fso.merge_offsets(cfg["joints_offsets"], {"b": 8.8}, ORDER)
        loaded = fso.load_config(path)
        backup = fso.write_offsets(path, loaded, merged, backup=True)

        after = json.load(open(path))
        # only b changed
        assert after["joints_offsets"] == {"a": 1.0, "b": 8.8, "c": 3.0}
        # every other field identical
        assert after["web_ui"] is True
        assert after["imu_upside_down"] is True
        assert after["expression_features"] == {"eyes": True, "camera": False}
        assert set(after) == set(cfg)
        # backup exists and equals the pre-write file
        assert backup and os.path.exists(backup)
        assert json.load(open(backup))["joints_offsets"] == {"a": 1.0, "b": 2.0, "c": 3.0}


def test_write_without_existing_file_makes_no_backup():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "new_config.json")
        backup = fso.write_offsets(path, {}, {"a": 1.0}, backup=True)
        assert backup is None                       # nothing to back up
        assert json.load(open(path))["joints_offsets"] == {"a": 1.0}


def test_load_config_missing_returns_none():
    assert fso.load_config("/no/such/duck_config.json") is None


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - failed} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
