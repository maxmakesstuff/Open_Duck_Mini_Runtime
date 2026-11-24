"""
Loop health: how regularly the 50 Hz control loop really runs.

The walk policy assumes one tick every 20 ms. The web server, camera and telemetry
share the process (and, on a Pi Zero 2 W, very little CPU), so ticks can run late;
a late tick means the legs act on stale balance data, and from the outside that
looks exactly like a policy that "just falls sometimes". This keeps score so that
question can be answered with numbers instead of a guess.

Pure stdlib -> unit-tests off-robot. One update() per tick costs a few comparisons.
"""
from collections import deque


class LoopHealth:
    def __init__(self, control_freq=50.0, window_s=60.0, late_factor=1.25):
        self.period = 1.0 / float(control_freq)
        self.late_after = self.period * float(late_factor)
        self.window = deque(maxlen=max(10, int(window_s * control_freq)))
        self.ticks = 0
        self.late_total = 0
        self.read_failures = 0
        self.worst_ms = 0.0           # since the last reset
        self._late_in_window = 0
        # The IMU is read by its own thread; a tick that gets the very same sample
        # again balanced on old data. A few repeats are normal (two 50 Hz clocks
        # drifting past each other); a long run means the sensor stopped answering.
        self._last_imu = None
        self.imu_samples = 0
        self.imu_repeats = 0
        self._repeat_run = 0
        self.imu_longest_repeat = 0

    def update(self, dt):
        """Feed the measured time since the previous tick (s). Returns True if this
        tick was late."""
        if dt is None or dt <= 0.0:
            return False
        late = dt > self.late_after
        if len(self.window) == self.window.maxlen and self.window[0]:
            self._late_in_window -= 1
        self.window.append(late)
        self.ticks += 1
        if late:
            self.late_total += 1
            self._late_in_window += 1
        ms = dt * 1000.0
        if ms > self.worst_ms:
            self.worst_ms = ms
        return late

    def note_read_failure(self):
        self.read_failures += 1

    def note_imu(self, gyro, accel):
        """Feed the IMU sample a controlling tick used. Returns the current run of
        identical samples (0 = fresh)."""
        try:
            sample = (float(gyro[0]), float(gyro[1]), float(gyro[2]),
                      float(accel[0]), float(accel[1]), float(accel[2]))
        except (TypeError, ValueError, IndexError):
            return self._repeat_run
        self.imu_samples += 1
        if sample == self._last_imu:
            self.imu_repeats += 1
            self._repeat_run += 1
            if self._repeat_run > self.imu_longest_repeat:
                self.imu_longest_repeat = self._repeat_run
        else:
            self._repeat_run = 0
        self._last_imu = sample
        return self._repeat_run

    def reset(self):
        self.window.clear()
        self.ticks = self.late_total = self.read_failures = 0
        self._late_in_window = 0
        self.worst_ms = 0.0
        self.imu_samples = self.imu_repeats = 0
        self._repeat_run = self.imu_longest_repeat = 0

    @property
    def late_pct(self):
        n = len(self.window)
        return 100.0 * self._late_in_window / n if n else 0.0

    def public(self):
        return {"late_pct": round(self.late_pct, 1),
                "late_total": self.late_total,
                "worst_ms": round(self.worst_ms, 1),
                "read_failures": self.read_failures,
                "ticks": self.ticks,
                "imu_repeat_pct": round(100.0 * self.imu_repeats / self.imu_samples, 1)
                if self.imu_samples else 0.0,
                "imu_longest_repeat": self.imu_longest_repeat}
