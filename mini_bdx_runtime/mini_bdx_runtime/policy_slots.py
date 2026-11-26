"""
Policy slots: every policy the duck can run, described by a small JSON file next to
its ONNX file (see policies/README.md), and a manager that swaps the running walk
policy without a restart.

Pure stdlib and hardware-free: the ONNX loader is passed in, so this unit-tests
off-robot. The control loop owns the actual swap (it must happen at a standstill);
this module only finds slots, loads the next policy on a background thread and
reports when it is ready.

Why a description file: a policy that merely has the right input size is not
necessarily compatible. The slot says what layout it expects ("kind") and which
settings it was trained with, and anything the runtime cannot feed is refused
before it reaches the motors.
"""
import hashlib
import json
import os
import threading

STOCK_ID = "stock"

# Input sizes the runtime can build. "walk" is the stock 101-value layout; "getup"
# is the same without the 7 commands and the 2 gait-clock values.
KIND_OBS_SIZE = {"walk": 101, "getup": 92}
ACTION_SIZE = 14

# Keys a slot may carry its own value for. Everything else in the walk tuning is a
# property of the duck, not of the policy.
TUNING_KEYS = ("action_scale", "phase_frequency_factor_offset", "steer_trim")
TUNING_LIMITS = {"action_scale": (0.0, 0.6),
                 "phase_frequency_factor_offset": (-0.5, 0.5),
                 "steer_trim": (-0.5, 0.5)}


class Slot:
    def __init__(self, id, name, kind, path, obs_size=None, action_size=ACTION_SIZE,
                 sha256=None, source="", license="", summary="", defaults=None,
                 error=""):
        self.id = str(id)
        self.name = str(name or id)
        self.kind = str(kind)
        self.path = path
        self.obs_size = int(obs_size if obs_size is not None
                            else KIND_OBS_SIZE.get(self.kind, 0))
        self.action_size = int(action_size)
        self.sha256 = sha256
        self.source = str(source or "")
        self.license = str(license or "")
        self.summary = str(summary or "")
        self.defaults = clean_tuning(defaults or {})
        self.error = error            # why this slot cannot be used ("" = usable)

    @property
    def usable(self):
        return not self.error

    def public(self):
        """What the Web UI needs to list this slot."""
        return {"id": self.id, "name": self.name, "kind": self.kind,
                "summary": self.summary, "license": self.license,
                "source": self.source, "usable": self.usable, "error": self.error,
                "defaults": dict(self.defaults)}


def clean_tuning(d):
    """Keep only known tuning keys, as finite floats inside their limits."""
    out = {}
    for key in TUNING_KEYS:
        if key not in (d or {}):
            continue
        try:
            v = float(d[key])
        except (TypeError, ValueError, OverflowError):
            continue
        if v != v or v in (float("inf"), float("-inf")):
            continue
        lo, hi = TUNING_LIMITS[key]
        out[key] = min(hi, max(lo, v))
    return out


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def _slot_from_manifest(manifest_path):
    """Build a Slot from one JSON file. Never raises: a broken manifest becomes an
    unusable slot with the reason in .error, so the UI can show what is wrong."""
    folder = os.path.dirname(manifest_path)
    fallback_id = os.path.splitext(os.path.basename(manifest_path))[0]
    try:
        with open(manifest_path, "r") as f:
            m = json.load(f)
        if not isinstance(m, dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError) as exc:
        return Slot(fallback_id, fallback_id, "walk", None,
                    error=f"cannot read description: {exc}")

    sid = str(m.get("id") or fallback_id)
    kind = str(m.get("kind", ""))
    path = os.path.join(folder, str(m.get("file", "")))
    slot = Slot(sid, m.get("name"), kind, path, obs_size=m.get("obs_size"),
                action_size=m.get("action_size", ACTION_SIZE), sha256=m.get("sha256"),
                source=m.get("source"), license=m.get("license"),
                summary=m.get("summary"), defaults=m.get("defaults"))
    if sid == STOCK_ID:
        slot.error = f'the id "{STOCK_ID}" is reserved for the start-up policy'
    elif kind not in KIND_OBS_SIZE:
        slot.error = f'unknown kind "{kind}" (the runtime can feed: walk, getup)'
    elif slot.obs_size != KIND_OBS_SIZE[kind]:
        slot.error = (f"needs {slot.obs_size} inputs; a {kind} policy gets "
                      f"{KIND_OBS_SIZE[kind]}")
    elif slot.action_size != ACTION_SIZE:
        slot.error = f"gives {slot.action_size} outputs; the duck has {ACTION_SIZE} joints"
    elif not m.get("file") or not os.path.isfile(path):
        slot.error = f"file missing: {m.get('file')}"
    return slot


def discover(folders, stock_path, stock_name=None):
    """Return {id: Slot}. The stock slot (the --onnx_model_path file) comes first;
    then every *.json in `folders`, in name order. A duplicate id keeps the first."""
    slots = {}
    slots[STOCK_ID] = Slot(
        STOCK_ID, stock_name or "Stock walk", "walk", stock_path,
        summary="The policy the walk script was started with (%s)."
                % os.path.basename(stock_path or "none"),
        error="" if stock_path else "no start-up policy")
    for folder in folders:
        try:
            names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
        except OSError:
            continue
        for n in names:
            slot = _slot_from_manifest(os.path.join(folder, n))
            if slot.id not in slots:
                slots[slot.id] = slot
    return slots


def default_folders(repo_root, home):
    return [os.path.join(repo_root, "policies"), os.path.join(home, "duck_policies")]


def check_policy_shape(policy, slot):
    """Compare the loaded model's real input/output size with what the slot claims.
    Returns "" if fine or unknown (a test double has no session), else the reason."""
    session = getattr(policy, "ort_session", None)
    if session is None:
        return ""
    try:
        n_in = session.get_inputs()[0].shape[-1]
        n_out = session.get_outputs()[0].shape[-1]
    except Exception as exc:  # noqa: BLE001 - a model we cannot inspect is not trusted
        return f"cannot inspect the model: {exc}"
    if isinstance(n_in, int) and n_in != slot.obs_size:
        return f"the file takes {n_in} inputs, not {slot.obs_size}"
    if isinstance(n_out, int) and n_out != slot.action_size:
        return f"the file gives {n_out} outputs, not {slot.action_size}"
    return ""


def load_slot(slot, loader):
    """Load one slot's policy now. Returns (policy, "") or (None, reason)."""
    if not slot.usable:
        return None, slot.error
    try:
        if slot.sha256 and _file_sha256(slot.path) != str(slot.sha256).lower():
            return None, "file does not match its checksum (incomplete copy?)"
        policy = loader(slot.path)
    except Exception as exc:  # noqa: BLE001 - any load failure keeps the old policy
        return None, f"could not load: {exc}"
    problem = check_policy_shape(policy, slot)
    if problem:
        return None, problem
    return policy, ""


class PolicyManager:
    """Tracks the active walk policy and prepares the next one.

    States: "idle" (nothing pending), "loading" (background thread reading the file),
    "ready" (loaded, waiting for the loop to swap at a standstill), "error".
    Thread-safe; the loop calls request()/take()/note_*() and status()."""

    def __init__(self, slots, loader, active_id=STOCK_ID, tuning=None,
                 run_in_thread=True):
        self.slots = slots
        self._loader = loader
        self._lock = threading.Lock()
        self._run_in_thread = run_in_thread
        self.active_id = active_id if active_id in slots else STOCK_ID
        self.state = "idle"
        self.message = ""
        self._pending_id = None
        self._pending_policy = None
        self._job = 0
        # {id: {key: value}} - values the user tuned and saved for that slot
        tuning = tuning if isinstance(tuning, dict) else {}
        self.tuning = {str(sid): clean_tuning(t) for sid, t in tuning.items()
                       if isinstance(t, dict)}

    # ------------------------------------------------------------ queries ----
    def walk_slots(self):
        return [s for s in self.slots.values() if s.kind == "walk"]

    def getup_slots(self):
        return [s for s in self.slots.values() if s.kind == "getup"]

    def tuning_for(self, slot_id, fallback=None):
        """Tuning to apply when `slot_id` becomes active: what the user saved for it,
        else the slot's own defaults, else `fallback` (the values in use now)."""
        out = dict(fallback or {})
        slot = self.slots.get(slot_id)
        if slot is not None:
            out.update(slot.defaults)
        out.update(self.tuning.get(slot_id, {}))
        return clean_tuning(out)

    def remember_tuning(self, slot_id, values):
        self.tuning[slot_id] = clean_tuning(values)

    def status(self):
        with self._lock:
            return {
                "active": self.active_id,
                "pending": self._pending_id,
                "state": self.state,
                "message": self.message,
                "slots": [s.public() for s in self.slots.values()],
            }

    def config_fields(self, extra=None):
        """The dict stored under "policies" in duck_config."""
        with self._lock:
            out = {"active": self.active_id,
                   "tuning": {k: dict(v) for k, v in self.tuning.items() if v}}
        out.update(extra or {})
        return out

    # ------------------------------------------------------------ switching ----
    def request(self, slot_id):
        """Ask for `slot_id` to become the walk policy. Returns True if a load was
        started (or nothing needs doing), False if refused; .message says why."""
        slot = self.slots.get(str(slot_id))
        with self._lock:
            if slot is None:
                self.state, self.message = "error", f"unknown policy: {slot_id}"
                return False
            if slot.kind != "walk":
                self.state, self.message = "error", f"{slot.name} is not a walk policy"
                return False
            if not slot.usable:
                self.state, self.message = "error", f"{slot.name}: {slot.error}"
                return False
            if slot.id == self.active_id and self._pending_id is None:
                self.state, self.message = "idle", ""
                return True
            if self._pending_id == slot.id and self.state in ("loading", "ready"):
                return True
            self._job += 1
            job = self._job
            self._pending_id, self._pending_policy = slot.id, None
            self.state, self.message = "loading", f"loading {slot.name}"
        if self._run_in_thread:
            threading.Thread(target=self._load, args=(slot, job), daemon=True).start()
        else:
            self._load(slot, job)
        return True

    def _load(self, slot, job):
        policy, problem = load_slot(slot, self._loader)
        with self._lock:
            if job != self._job:          # a newer request replaced this one
                return
            if policy is None:
                self._pending_id = None
                self.state, self.message = "error", f"{slot.name}: {problem}"
            else:
                self._pending_policy = policy
                self.state, self.message = "ready", f"{slot.name} ready"

    def cancel(self):
        with self._lock:
            self._job += 1
            self._pending_id = self._pending_policy = None
            self.state, self.message = "idle", ""

    def ready(self):
        with self._lock:
            return self.state == "ready"

    def take(self):
        """Hand the loaded policy to the loop and make it the active one. Returns
        (slot, policy) or None if nothing is ready. Call only at a standstill."""
        with self._lock:
            if self.state != "ready" or self._pending_policy is None:
                return None
            slot = self.slots[self._pending_id]
            policy = self._pending_policy
            self.active_id = slot.id
            self._pending_id = self._pending_policy = None
            self.state, self.message = "idle", f"now running {slot.name}"
            return slot, policy
