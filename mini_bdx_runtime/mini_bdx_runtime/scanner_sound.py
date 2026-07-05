"""
Scanner-light sound loop for the Open Duck Mini.

When the scanner LED (projector, toggled by the X button) is switched on, the
three "lamp" sounds are played on repeat in order (lamp -> lamp2 -> lamp3 ->
lamp ...), crossfading slightly between them. When the LED is switched off the
loop stops immediately.

This is non-blocking: ScannerSound.update(now) is driven once per control tick
(no sleeps), exactly like AnimationPlayer. The audio I/O sits behind a small
backend so the sequencing logic stays pure and unit-testable without an audio
device. The real backend (PygameScannerBackend) imports pygame lazily.
"""
import os

# Played in this exact order, then looped.
SCANNER_FILES = ["lamp.wav", "lamp2.wav", "lamp3.wav"]

# Overlap between consecutive sounds. Must be < the shortest sound (lamp*.wav
# are ~6 s, so 0.6 s is a gentle crossfade).
DEFAULT_CROSSFADE_S = 0.6


class ScannerSound:
    """Sequencer: loops the backend's sounds in order with a crossfade.

    The backend must provide:
        count() -> int
        length(i) -> float seconds
        play(i, fade_ms) -> handle      # start sound i with a fade-in
        fadeout(handle, ms)             # fade a previously returned handle out
        stop_all()                      # stop everything immediately
    """

    def __init__(self, backend, crossfade_s=DEFAULT_CROSSFADE_S):
        self.backend = backend
        self.crossfade_s = crossfade_s
        self._crossfade_ms = int(crossfade_s * 1000)
        self.active = False
        self._index = 0
        self._cur_start = 0.0
        self._cur_handle = None

    @property
    def is_active(self):
        return self.active

    def start(self, now):
        """Begin (or restart) the loop from the first sound."""
        self.active = True
        self._index = 0
        self._cur_start = now
        self._cur_handle = self.backend.play(0, self._crossfade_ms)

    def update(self, now):
        """Advance the loop. Cheap no-op when inactive; call every tick."""
        if not self.active:
            return
        cur_len = self.backend.length(self._index)
        elapsed = now - self._cur_start
        threshold = cur_len - self.crossfade_s
        if elapsed >= threshold:
            next_index = (self._index + 1) % self.backend.count()
            new_handle = self.backend.play(next_index, self._crossfade_ms)
            self.backend.fadeout(self._cur_handle, self._crossfade_ms)
            self._index = next_index
            self._cur_handle = new_handle
            self._cur_start = now

    def stop(self):
        """Stop immediately."""
        if not self.active:
            return
        self.active = False
        self._cur_handle = None
        self.backend.stop_all()


class PygameScannerBackend:
    """Real audio backend using two reserved pygame mixer channels so the
    scanner can crossfade without being interrupted by (or interrupting) the
    regular Sounds effects."""

    def __init__(self, sound_dir, filenames=SCANNER_FILES, channels=(0, 1)):
        import pygame

        self._pygame = pygame
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        # Keep the regular Sounds.play() auto-allocation away from our channels.
        try:
            pygame.mixer.set_reserved(max(channels) + 1)
        except Exception:  # noqa: BLE001 - older SDL/pygame may not support it
            pass
        self._sounds = [
            pygame.mixer.Sound(os.path.join(sound_dir, f)) for f in filenames
        ]
        self._channels = [pygame.mixer.Channel(c) for c in channels]
        self._next = 0

    def count(self):
        return len(self._sounds)

    def length(self, i):
        return self._sounds[i].get_length()

    def play(self, i, fade_ms):
        ch = self._channels[self._next]
        self._next = (self._next + 1) % len(self._channels)
        ch.play(self._sounds[i], fade_ms=fade_ms)
        return ch

    def fadeout(self, handle, ms):
        if handle is not None:
            handle.fadeout(ms)

    def stop_all(self):
        for ch in self._channels:
            ch.stop()
