"""Off-robot tests for policy_slots.py.  Run:  python3 test_policy_slots.py"""
import json
import os
import shutil
import tempfile
import threading

import policy_slots as ps

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


class FakeSession:
    def __init__(self, n_in, n_out):
        self._i, self._o = n_in, n_out

    def get_inputs(self):
        return [type("T", (), {"shape": [1, self._i]})()]

    def get_outputs(self):
        return [type("T", (), {"shape": [1, self._o]})()]


class FakePolicy:
    def __init__(self, path, n_in=101, n_out=14):
        self.path = path
        self.ort_session = FakeSession(n_in, n_out)


def _folder(manifests, files=("a.onnx",)):
    d = tempfile.mkdtemp(prefix="slots-")
    for name in files:
        with open(os.path.join(d, name), "wb") as f:
            f.write(b"model:" + name.encode())
    for name, m in manifests.items():
        with open(os.path.join(d, name), "w") as f:
            f.write(m if isinstance(m, str) else json.dumps(m))
    return d


def _walk(**over):
    m = {"id": "w1", "name": "Walk one", "kind": "walk", "file": "a.onnx"}
    m.update(over)
    return m


def test_stock_slot_always_exists_and_comes_first():
    slots = ps.discover([], "/x/BEST_WALK_ONNX_2.onnx")
    assert list(slots) == ["stock"]
    assert slots["stock"].usable and slots["stock"].kind == "walk"
    assert "BEST_WALK_ONNX_2.onnx" in slots["stock"].name


def test_discovers_manifests_in_name_order():
    d = _folder({"b.json": _walk(id="b"), "a.json": _walk(id="a")})
    try:
        assert list(ps.discover([d], "s.onnx")) == ["stock", "a", "b"]
    finally:
        shutil.rmtree(d)


def test_broken_manifests_become_unusable_slots_with_a_reason():
    d = _folder({
        "nofile.json": _walk(id="nofile", file="missing.onnx"),
        "kind.json": _walk(id="kind", kind="dance"),
        "size.json": _walk(id="size", obs_size=205),
        "outs.json": _walk(id="outs", action_size=10),
        "junk.json": "{not json",
        "stock.json": _walk(id="stock"),
    })
    try:
        slots = ps.discover([d], "s.onnx")
        assert "missing" in slots["nofile"].error
        assert "unknown kind" in slots["kind"].error
        assert "205" in slots["size"].error
        assert "10 outputs" in slots["outs"].error
        assert "cannot read" in slots["junk"].error
        assert slots["stock"].usable and slots["stock"].path == "s.onnx", \
            "a manifest must not replace the start-up policy"
        assert not any(s.usable for k, s in slots.items() if k != "stock")
    finally:
        shutil.rmtree(d)


def test_missing_folder_is_ignored():
    assert list(ps.discover(["/nonexistent/folder"], "s.onnx")) == ["stock"]


def test_checksum_mismatch_is_refused():
    d = _folder({"w.json": _walk(sha256="00" * 32)})
    try:
        slot = ps.discover([d], "s.onnx")["w1"]
        policy, problem = ps.load_slot(slot, FakePolicy)
        assert policy is None and "checksum" in problem
    finally:
        shutil.rmtree(d)


def test_wrong_real_input_size_is_refused():
    d = _folder({"w.json": _walk()})
    try:
        slot = ps.discover([d], "s.onnx")["w1"]
        policy, problem = ps.load_slot(slot, lambda p: FakePolicy(p, n_in=92))
        assert policy is None and "92 inputs" in problem
        policy, problem = ps.load_slot(slot, FakePolicy)
        assert policy is not None and problem == ""
    finally:
        shutil.rmtree(d)


def test_loader_exception_is_reported_not_raised():
    d = _folder({"w.json": _walk()})
    try:
        slot = ps.discover([d], "s.onnx")["w1"]

        def boom(path):
            raise RuntimeError("corrupt")
        policy, problem = ps.load_slot(slot, boom)
        assert policy is None and "corrupt" in problem
    finally:
        shutil.rmtree(d)


def _manager(**kw):
    d = _folder({"w.json": _walk(defaults={"action_scale": 0.25}),
                 "g.json": {"id": "g1", "kind": "getup", "file": "a.onnx"}})
    slots = ps.discover([d], "s.onnx")
    return ps.PolicyManager(slots, FakePolicy, run_in_thread=False, **kw), d


def test_request_load_take_makes_the_slot_active():
    m, d = _manager()
    try:
        assert m.request("w1") and m.ready()
        assert m.status()["pending"] == "w1" and m.active_id == "stock"
        slot, policy = m.take()
        assert slot.id == "w1" and policy.path.endswith("a.onnx")
        assert m.active_id == "w1" and not m.ready() and m.take() is None
    finally:
        shutil.rmtree(d)


def test_request_refuses_unknown_getup_and_unusable_slots():
    m, d = _manager()
    try:
        assert not m.request("nope") and m.state == "error"
        assert not m.request("g1") and "not a walk" in m.message
        assert m.active_id == "stock" and m.take() is None
    finally:
        shutil.rmtree(d)


def test_requesting_the_active_slot_is_a_no_op():
    m, d = _manager()
    try:
        assert m.request("stock") and m.state == "idle" and not m.ready()
    finally:
        shutil.rmtree(d)


def test_a_newer_request_replaces_a_slow_older_load():
    d = _folder({"a.json": _walk(id="a"), "b.json": _walk(id="b")})
    gate = threading.Event()
    try:
        def slow(path):
            if not gate.is_set():
                gate.wait(2)            # the first load ("a") hangs here
            return FakePolicy(path)
        m = ps.PolicyManager(ps.discover([d], "s.onnx"), slow)
        m.request("a")
        gate.set()                      # release it only once "b" has been asked for
        m.request("b")
        for _ in range(200):
            if m.ready():
                break
            threading.Event().wait(0.01)
        slot, _ = m.take()
        assert slot.id == "b", "the stale load must not win"
    finally:
        shutil.rmtree(d)


def test_cancel_drops_a_pending_policy():
    m, d = _manager()
    try:
        m.request("w1")
        m.cancel()
        assert m.take() is None and m.active_id == "stock"
    finally:
        shutil.rmtree(d)


def test_tuning_precedence_saved_over_defaults_over_fallback():
    m, d = _manager(tuning={"w1": {"phase_frequency_factor_offset": -0.1}})
    try:
        t = m.tuning_for("w1", fallback={"action_scale": 0.2,
                                         "phase_frequency_factor_offset": 0.3})
        assert t == {"action_scale": 0.25, "phase_frequency_factor_offset": -0.1}
        assert m.tuning_for("stock", fallback={"action_scale": 0.2}) == {"action_scale": 0.2}
    finally:
        shutil.rmtree(d)


def test_tuning_is_clamped_and_junk_is_dropped():
    assert ps.clean_tuning({"action_scale": 9, "phase_frequency_factor_offset": "x",
                            "evil": 1}) == {"action_scale": 0.6}
    assert ps.clean_tuning({"action_scale": float("nan")}) == {}
    assert ps.clean_tuning(None) == {}


def test_config_fields_round_trip():
    m, d = _manager()
    try:
        m.remember_tuning("w1", {"action_scale": 0.3})
        m.request("w1")
        m.take()
        f = m.config_fields()
        assert f == {"active": "w1", "tuning": {"w1": {"action_scale": 0.3}}}
        m2 = ps.PolicyManager(m.slots, FakePolicy, active_id=f["active"],
                              tuning=f["tuning"])
        assert m2.active_id == "w1" and m2.tuning == f["tuning"]
    finally:
        shutil.rmtree(d)


def test_shipped_manifests_are_valid_and_match_their_files():
    """The policies/ folder in the repo: every slot usable, every checksum right."""
    slots = ps.discover([os.path.join(REPO, "policies")], "s.onnx")
    assert {"dk5426-walk", "dk5426-getup-real2", "dk5426-getup-real"} <= set(slots)
    for sid, slot in slots.items():
        if sid == "stock":
            continue
        assert slot.usable, f"{sid}: {slot.error}"
        assert slot.sha256 and ps._file_sha256(slot.path) == slot.sha256, sid
        assert slot.license, f"{sid} has no licence"
    assert slots["dk5426-walk"].kind == "walk" and slots["dk5426-walk"].obs_size == 101
    assert slots["dk5426-getup-real2"].obs_size == 92


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nAll {len(tests)} policy_slots tests passed.")
