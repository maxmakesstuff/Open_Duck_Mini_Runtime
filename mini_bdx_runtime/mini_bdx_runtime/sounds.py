"""
Everything the duck plays through its speaker.

  * Sound effects: the WAV files in assets/ (beeps, the "happy" jingles, whatever
    else an owner drops in). Played by file name: `play("beep1.wav")`.
  * The voice: categorised clips in assets/voice (see duck_voice.py). Asked for by
    category: `say("curious", now)`. A clip can also be played by its file name, so
    a recorded performance replays exactly what was heard.

Everything is loaded once at start, so playing costs the control loop nothing. No
call here raises: a duck without a working sound card walks on in silence
(`ok` is False then).

`play("say:<category>")` is the same as `say(category)`; it lets code that only
knows file names (recordings, the face-tracking greeting) use the voice.
"""
import os
import random
import time

try:
    from mini_bdx_runtime.duck_voice import DuckVoice, load_manifest
except ImportError:  # flat import off the package (tests, diagnostic scripts)
    from duck_voice import DuckVoice, load_manifest

VOICE_DIR = "voice"


def _clamp01(v, default=1.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    if not v == v:
        return default
    return min(1.0, max(0.0, v))


class Sounds:
    def __init__(self, volume=1.0, sound_directory="./", voice_directory=None, mixer=None,
                 rng=None):
        self.sounds = {}                   # effect file name -> loaded sound
        self._voice_clips = {}             # voice file name -> loaded sound
        self.ok = False
        self.volume = _clamp01(volume)
        self._mixer = mixer
        if self._mixer is None:
            try:
                import pygame
                self._mixer = pygame.mixer
            except Exception as e:  # noqa: BLE001
                print(f"[sound] no sound library: {e}")
        if self._mixer is not None:
            try:
                self._mixer.init()
            except Exception as e:  # noqa: BLE001
                print(f"[sound] could not open the speaker: {e}")
                self._mixer = None

        if self._mixer is not None:
            self._load_dir(sound_directory, self.sounds)
        if voice_directory is None:
            voice_directory = os.path.join(sound_directory, VOICE_DIR)
        categories = load_manifest(voice_directory) if self._mixer is not None else {}
        usable = {}
        for cat, takes in categories.items():
            kept = [(n, s) for n, s in takes if self._load(voice_directory, n, self._voice_clips)]
            if kept:
                usable[cat] = kept
        self.voice = DuckVoice(usable, self._play_voice_clip, rng=rng)
        self.ok = bool(self.sounds or self._voice_clips)
        if self._mixer is not None:
            print(f"[sound] {len(self.sounds)} effects, {len(self._voice_clips)} voice clips "
                  f"in {len(usable)} categories")
        if not self.ok:
            print("[sound] nothing to play.")

    # --------------------------------------------------------------- loading ----
    def _load(self, directory, name, into):
        try:
            snd = self._mixer.Sound(os.path.join(directory, name))
            snd.set_volume(self.volume)
            into[name] = snd
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[sound] could not load {name}: {e}")
            return False

    def _load_dir(self, directory, into):
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            print(f"[sound] folder {directory} not found.")
            return
        for name in names:
            if name.endswith(".wav"):
                self._load(directory, name, into)

    # --------------------------------------------------------------- playing ----
    def _start(self, snd):
        try:
            snd.set_volume(self.volume)
            snd.play()
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[sound] playback failed: {e}")
            return False

    def _play_voice_clip(self, name):
        snd = self._voice_clips.get(name)
        return self._start(snd) if snd is not None else False

    def play(self, sound_name):
        """Play an effect or a voice clip by file name, or "say:<category>".
        Returns True if something started. Unknown names are ignored."""
        if not isinstance(sound_name, str):
            return False
        if sound_name.startswith("say:"):
            return self.say(sound_name[4:], urgent=True) is not None
        snd = self.sounds.get(sound_name)
        if snd is None:
            snd = self._voice_clips.get(sound_name)
        if snd is None:
            return False
        return self._start(snd)

    def say(self, category, now=None, urgent=False):
        """Say something of that category ("curious", "happy", ... or "*" for
        anything). Returns the clip's file name, or None if the duck stayed quiet."""
        return self.voice.say(category, time.time() if now is None else now, urgent=urgent)

    def play_random(self, now=None):
        """What the B button does: say anything; a duck without a voice library
        plays a random effect instead. Returns the name played, or None."""
        name = self.say("*", now, urgent=True)
        if name is None and self.sounds:
            name = random.choice(list(self.sounds.keys()))
            if not self.play(name):
                return None
        return name

    # kept for older callers
    def play_random_sound(self):
        self.play_random()

    def play_happy(self):
        if self.say("happy", urgent=True) is None:
            self.play("happy1.wav")

    def set_volume(self, volume):
        """Master volume 0..1, for everything that plays from now on and whatever is
        sounding right now."""
        self.volume = _clamp01(volume, self.volume)
        for snd in list(self.sounds.values()) + list(self._voice_clips.values()):
            try:
                snd.set_volume(self.volume)
            except Exception:  # noqa: BLE001
                pass

    def stop(self):
        try:
            if self._mixer is not None:
                self._mixer.stop()
        except Exception:  # noqa: BLE001
            pass

    def public(self):
        return {"ok": bool(self.ok), "volume": round(self.volume, 2),
                "voice": bool(self.voice.ok), "talk": bool(self.voice.enabled),
                "categories": sorted(self.voice.categories), "last": self.voice.last}


# Example usage
if __name__ == "__main__":
    player = Sounds(1.0, "../assets/")
    for cat in ("hello", "curious", "happy", "notice"):
        print(cat, "->", player.say(cat, urgent=True))
        time.sleep(3)
