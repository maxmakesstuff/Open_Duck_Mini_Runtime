"""
Flight recorder: a black box for the walk loop.

Every control tick is copied into a preallocated ring (the last ~20 s). When
something worth studying happens - a fall, a reflex trigger, a get-up, a policy
switch, or the operator pressing MARK - the seconds before and a few seconds after
are written to ~/duck_logs/<time>_<reason>.npz on a background thread. A "take"
records continuously between start and stop (up to a cap) for longer comparisons.

Why: thresholds for the fall reflex, the question whether a new policy really walks
better, and "why did it fall just now" can only be answered from what the duck
actually measured. Nothing here changes what the robot does.

Per tick it costs a handful of array row copies (tens of microseconds). The file
write happens off the control thread.

numpy only, no hardware: unit-tests off-robot; scripts/analyze_falls.py reads the
files.
"""
import json
import os
import threading
import time

import numpy as np

HOME_DIR = os.path.expanduser("~")
LOG_DIR = os.path.join(HOME_DIR, "duck_logs")

# name -> width. Order is the column order inside the ring.
FIELDS = (
    ("t", 1), ("dt", 1), ("gyro", 3), ("acc", 3), ("commands", 7),
    ("pos", 14), ("vel", 14), ("action", 14), ("targets", 14), ("feet", 2),
    ("tilt", 1), ("tilt_rate", 1), ("predicted", 1), ("gov_scale", 1),
    ("mode", 1), ("reflex", 1), ("took", 1),
)
WIDTH = sum(w for _, w in FIELDS)
OFFSET = {}
_o = 0
for _name, _w in FIELDS:
    OFFSET[_name] = (_o, _o + _w)
    _o += _w

# What the loop was doing on a tick ("mode" column).
MODE_CODES = {"walk": 0, "paused": 1, "stance": 2, "reflex": 3, "getup": 4,
              "balance": 5, "recover": 6, "switch": 7}
REFLEX_CODES = {"armed": 0, "tuck": 1, "soft": 2, "down": 3}

DEFAULTS = {"enabled": True, "pre_s": 8.0, "post_s": 3.0, "max_files": 150,
            "max_take_s": 180.0}
MIN_FREE_BYTES = 200 * 1024 * 1024      # never fill the SD card


def clean_config(cfg):
    out = dict(DEFAULTS)
    cfg = cfg if isinstance(cfg, dict) else {}
    if "enabled" in cfg:
        out["enabled"] = bool(cfg["enabled"])
    for key, lo, hi in (("pre_s", 1.0, 18.0), ("post_s", 0.0, 10.0),
                        ("max_files", 5, 2000), ("max_take_s", 10.0, 600.0)):
        try:
            v = float(cfg.get(key, out[key]))
        except (TypeError, ValueError, OverflowError):
            continue
        if v == v:
            out[key] = min(hi, max(lo, v))
    out["max_files"] = int(out["max_files"])
    return out


def safe_name(text):
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(text))[:40]


class FlightRecorder:
    def __init__(self, control_freq=50.0, config=None, log_dir=LOG_DIR, meta=None,
                 ring_s=20.0, clock=time.time, threaded=True):
        self.cfg = clean_config(config)
        self.freq = float(control_freq)
        self.log_dir = log_dir
        self.meta = dict(meta or {})
        self._clock = clock
        self._threaded = threaded
        self.n = max(50, int(ring_s * self.freq))
        self.ring = np.zeros((self.n, WIDTH), dtype=np.float32)
        self.count = 0                  # ticks ever written
        self._row = np.zeros(WIDTH, dtype=np.float32)
        self._pending = []              # [{"reason","at","due","note"}]
        self._take = None               # list of row chunks while a take runs
        self._take_rows = 0
        self._take_label = ""
        self.saved = 0
        self.last_file = ""
        self.last_error = ""
        self._lock = threading.Lock()

    # ------------------------------------------------------------- per tick ----
    def add(self, **values):
        """Record one tick. Unknown names are ignored; missing ones stay 0."""
        if not self.cfg["enabled"]:
            return
        row = self._row
        row[:] = 0.0
        for name, value in values.items():
            span = OFFSET.get(name)
            if span is None or value is None:
                continue
            a, b = span
            try:
                row[a:b] = value
            except (ValueError, TypeError):
                pass                    # wrong width: leave zeros, never break the loop
        self.ring[self.count % self.n] = row
        self.count += 1
        if self._take is not None:
            self._take.append(row.copy())
            self._take_rows += 1
            if self._take_rows >= self.cfg["max_take_s"] * self.freq:
                self.stop_take()
        if self._pending and self.count >= self._pending[0]["due"]:
            ev = self._pending.pop(0)
            self._write(self._window(ev["at"]), ev["reason"], ev["note"])

    # --------------------------------------------------------------- events ----
    def mark(self, reason, note=""):
        """Ask for the ticks around NOW to be saved (after `post_s` more seconds)."""
        if not self.cfg["enabled"]:
            return False
        if len(self._pending) >= 4:     # a burst of events: the first ones cover it
            return False
        post = int(self.cfg["post_s"] * self.freq)
        self._pending.append({"reason": safe_name(reason), "at": self.count,
                              "due": self.count + post, "note": str(note)[:200]})
        return True

    def flush(self):
        """Write pending events now with whatever has been recorded (on shutdown)."""
        while self._pending:
            ev = self._pending.pop(0)
            self._write(self._window(ev["at"]), ev["reason"], ev["note"])

    def _window(self, at):
        """Rows from pre_s before `at` up to the newest tick, oldest first."""
        pre = int(self.cfg["pre_s"] * self.freq)
        first = max(0, at - pre, self.count - self.n)
        idx = np.arange(first, self.count) % self.n
        return self.ring[idx].copy()

    # ---------------------------------------------------------------- takes ----
    @property
    def taking(self):
        return self._take is not None

    def start_take(self, label="take"):
        if not self.cfg["enabled"] or self._take is not None:
            return False
        self._take, self._take_rows = [], 0
        self._take_label = safe_name(label) or "take"
        return True

    def stop_take(self):
        if self._take is None:
            return False
        rows = np.array(self._take, dtype=np.float32) if self._take else None
        self._take = None
        if rows is not None and len(rows):
            self._write(rows, self._take_label, "take")
        return True

    @property
    def take_seconds(self):
        return self._take_rows / self.freq if self._take is not None else 0.0

    # ---------------------------------------------------------------- files ----
    def _write(self, rows, reason, note):
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(self._clock()))
        meta = dict(self.meta, reason=reason, note=note, control_hz=self.freq,
                    fields=[[n, w] for n, w in FIELDS], rows=int(len(rows)))
        job = (rows, stamp, reason, json.dumps(meta))
        if self._threaded:
            threading.Thread(target=self._save, args=job, daemon=True).start()
        else:
            self._save(*job)

    def _save(self, rows, stamp, reason, meta_json):
        with self._lock:                # one writer at a time
            try:
                os.makedirs(self.log_dir, exist_ok=True)
                st = os.statvfs(self.log_dir)
                if st.f_bavail * st.f_frsize < MIN_FREE_BYTES:
                    self.last_error = "not saved: storage almost full"
                    return
                name = f"{stamp}_{reason}.npz"
                path = os.path.join(self.log_dir, name)
                k = 2
                while os.path.exists(path):           # same second: do not overwrite
                    name = f"{stamp}_{reason}_{k}.npz"
                    path = os.path.join(self.log_dir, name)
                    k += 1
                tmp = path + ".tmp"
                with open(tmp, "wb") as f:
                    np.savez_compressed(f, data=rows, meta=np.array(meta_json))
                os.replace(tmp, path)
                self.saved += 1
                self.last_file = name
                self.last_error = ""
                self._prune()
            except Exception as exc:  # noqa: BLE001 - recording must never hurt the walk
                self.last_error = f"not saved: {exc}"

    def _prune(self):
        files = sorted(f for f in os.listdir(self.log_dir) if f.endswith(".npz"))
        for old in files[:max(0, len(files) - self.cfg["max_files"])]:
            try:
                os.unlink(os.path.join(self.log_dir, old))
            except OSError:
                pass

    def public(self):
        return {"enabled": bool(self.cfg["enabled"]), "taking": self.taking,
                "take_s": round(self.take_seconds, 1), "saved": self.saved,
                "last_file": self.last_file, "error": self.last_error,
                "pending": len(self._pending)}


# ------------------------------------------------------------------ reading ----
def load(path):
    """Read one recording. Returns (columns, meta): columns maps each field name to
    an array of shape (rows, width) (width-1 fields are flattened to (rows,))."""
    with np.load(path, allow_pickle=False) as z:
        data = z["data"]
        meta = json.loads(str(z["meta"]))
    cols, o = {}, 0
    for name, w in meta.get("fields", [list(f) for f in FIELDS]):
        block = data[:, o:o + w]
        cols[name] = block[:, 0] if w == 1 else block
        o += w
    return cols, meta


def list_logs(log_dir=LOG_DIR, limit=200):
    """Newest first: [{"name","bytes","mtime"}]."""
    try:
        names = [n for n in os.listdir(log_dir) if n.endswith(".npz")]
    except OSError:
        return []
    out = []
    for n in names:
        try:
            st = os.stat(os.path.join(log_dir, n))
        except OSError:
            continue
        out.append({"name": n, "bytes": st.st_size, "mtime": round(st.st_mtime, 1)})
    out.sort(key=lambda e: e["name"], reverse=True)
    return out[:limit]


def log_path(name, log_dir=LOG_DIR):
    """Absolute path of a recording by bare file name, or None. Refuses anything
    that is not a plain .npz name inside the log folder (no path tricks)."""
    if (not isinstance(name, str) or not name.endswith(".npz")
            or os.path.basename(name) != name or name.startswith(".")):
        return None
    path = os.path.join(log_dir, name)
    return path if os.path.isfile(path) else None
