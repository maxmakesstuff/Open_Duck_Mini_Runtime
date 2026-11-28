"""
The duck's voice: which clip to play, and when to stay quiet.

The clips are ready-made WAV files (assets/voice, rendered from the droid-voice
synthesizer by tools/render_voice_library.py), sorted into categories such as
"curious", "notice", "happy" or "worried". Callers ask for a category; this picks a
take that was not just heard, and keeps the duck from talking over itself or
chattering without a pause.

Why files and not live synthesis: the synthesizer renders about 140 times faster
than real time on a laptop, which is only a few times real time on the duck's Pi
Zero 2 W, in bursts of a few hundred milliseconds. The walk loop needs about half of
every 20 ms tick, so those bursts would cost late ticks. The files cost nothing while
walking, and choosing between categories and takes at the moment of playing is all
the "reacting to what happens" the duck needs.

Pure logic, no audio library: the caller passes a `play(name)` function. Unit-tests
off-robot.
"""
import json
import os

MANIFEST = "voice.json"
# Seconds of silence after an utterance before an ordinary one may follow.
GAP_S = 3.0


def load_manifest(directory):
    """{category: [(file name, seconds)]} from <directory>/voice.json, keeping only
    files that exist. Without a manifest, WAVs named <category>_<n>.wav are used
    (2 s assumed). Never raises; an empty dict means: no voice."""
    out = {}
    try:
        names = set(os.listdir(directory))
    except OSError:
        return out
    try:
        with open(os.path.join(directory, MANIFEST)) as f:
            cats = json.load(f).get("categories", {})
        for cat, entries in cats.items():
            takes = []
            for e in entries:
                name = e.get("file")
                if isinstance(name, str) and name in names and name.endswith(".wav"):
                    try:
                        seconds = max(0.1, float(e.get("s", 2.0)))
                    except (TypeError, ValueError):
                        seconds = 2.0
                    takes.append((name, seconds))
            if takes:
                out[str(cat)] = takes
        if out:
            return out
    except (OSError, ValueError, AttributeError, TypeError):
        out = {}
    for name in sorted(names):
        stem, ext = os.path.splitext(name)
        if ext == ".wav" and "_" in stem:
            out.setdefault(stem.rsplit("_", 1)[0], []).append((name, 2.0))
    return out


class DuckVoice:
    def __init__(self, categories, play, rng=None, gap_s=GAP_S):
        import random
        self.categories = {c: list(t) for c, t in (categories or {}).items() if t}
        self._play = play
        self.rng = rng if rng is not None else random.Random()
        self.gap_s = float(gap_s)
        self.enabled = True
        self._recent = {}                  # category -> names heard last
        self._busy_until = 0.0
        self._quiet_until = 0.0
        self.last = ""                     # name of the clip played last

    @property
    def ok(self):
        return bool(self.categories)

    def busy(self, now):
        """True while an utterance is (by its length) still sounding."""
        return now < self._busy_until

    def names(self, categories=None):
        """All clip names, or those of the given categories."""
        cats = self.categories if categories is None else categories
        return [n for c in cats for n, _ in self.categories.get(c, [])]

    def _pick(self, category):
        takes = self.categories[category]
        recent = self._recent.setdefault(category, [])
        fresh = [t for t in takes if t[0] not in recent] or takes
        take = fresh[int(self.rng.random() * len(fresh)) % len(fresh)]
        recent.append(take[0])
        del recent[:-min(2, max(0, len(takes) - 1)) or None]
        return take

    def say(self, category, now, urgent=False):
        """Play a take of `category`. Returns the clip name, or None if the duck
        stays quiet: unknown category, switched off, still talking, or it only just
        spoke. `urgent` (a fall, a button the operator pressed) skips the pause
        between utterances and may talk over a running clip."""
        if category == "*":
            cats = sorted(self.categories)
            category = cats[int(self.rng.random() * len(cats)) % len(cats)] if cats else ""
        if category not in self.categories:
            return None
        if not urgent and (not self.enabled or now < self._quiet_until):
            return None
        name, seconds = self._pick(category)
        try:
            if self._play(name) is False:
                return None
        except Exception:  # noqa: BLE001 - a sound must never stop the duck
            return None
        self._busy_until = now + seconds
        self._quiet_until = self._busy_until + self.gap_s
        self.last = name
        return name
