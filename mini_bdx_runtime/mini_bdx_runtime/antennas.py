"""
Ear-antenna servo driver.

The ears are analog servos on GPIO13 (left) / GPIO12 (right). On stock Raspberry
Pi OS the CircuitPython `pwmio` output is *software*-timed, so its pulse edges
jitter whenever the CPU is busy — the ears twitch constantly. This driver prefers
**pigpio**, whose hardware-timed PWM holds the pulse width exactly, so the ears sit
dead still unless commanded (fix credited to Brad3D; I2S coexistence to
Elpidiovaldez5 — see ops/pigpio/). If pigpiod isn't reachable it falls back to the
old pwmio path so the robot still runs (just with the jitter), and prints how to
enable the fix.

I2S NOTE: this duck drives a max98357A speaker over I2S, which uses the PCM clock.
pigpio must therefore run on the *PWM* clock (`pigpiod -t 0`) so the two don't
fight — the ops/pigpio installer sets that up. pigpio only ever uses
set_servo_pulsewidth here (no waveforms), which is served by the selected clock.

Public API (unchanged): Antennas().set_position_left(v) / .set_position_right(v)
with v in [-1, 1], and .stop(). The mechanical throw is identical to before:
neutral 1500 us, +/-500 us at the extremes (1000..2000 us).
"""
import sys
import time

# pigpio's python client ships as an apt package (python3-pigpio) under the system
# dist-packages, which a venv built WITHOUT --system-site-packages can't import on
# its own. Append it (like face_tracker does for picamera2) so the venv resolves
# pigpio; append => the venv's own packages keep priority.
SYSTEM_DIST_PACKAGES = "/usr/lib/python3/dist-packages"

LEFT_ANTENNA_GPIO = 13     # BCM (== board.D13)
RIGHT_ANTENNA_GPIO = 12    # BCM (== board.D12)
LEFT_SIGN = 1
RIGHT_SIGN = -1
MIN_UPDATE_INTERVAL = 1 / 50  # 20 ms

NEUTRAL_US = 1500
THROW_US = 500             # +/- at v = +/-1  -> 1000..2000 us (unchanged from pwmio)


def value_to_pulsewidth(v):
    """Map v in [-1, 1] to a servo pulse width in microseconds (1000..2000)."""
    pw = int(NEUTRAL_US + v * THROW_US)
    return min(max(pw, 1000), 2000)


def value_to_duty_cycle(v):
    """pwmio 16-bit duty for a 50 Hz frame (kept for the fallback backend)."""
    pulse_width_ms = 1.5 + (v * 0.5)  # 1 ms to 2 ms
    duty_cycle = int((pulse_width_ms / 20) * 65535)
    return min(max(duty_cycle, 3277), 6553)


class _PigpioBackend:
    """Hardware-timed PWM via pigpiod (jitter-free). Raises if the daemon isn't up."""

    name = "pigpio"

    def __init__(self):
        if SYSTEM_DIST_PACKAGES and SYSTEM_DIST_PACKAGES not in sys.path:
            sys.path.append(SYSTEM_DIST_PACKAGES)
        import pigpio  # pure-python client; talks to the pigpiod daemon over a socket
        self._pi = pigpio.pi()
        if not self._pi.connected:
            raise RuntimeError("pigpiod not running (start it: sudo systemctl start pigpiod)")
        self.set(LEFT_ANTENNA_GPIO, 0, LEFT_SIGN)
        self.set(RIGHT_ANTENNA_GPIO, 0, RIGHT_SIGN)

    def set(self, gpio, value, sign):
        self._pi.set_servo_pulsewidth(gpio, value_to_pulsewidth(value * sign))

    def stop(self):
        # centre, settle, then release (pulsewidth 0 = servo off) and disconnect.
        time.sleep(MIN_UPDATE_INTERVAL)
        self.set(LEFT_ANTENNA_GPIO, 0, LEFT_SIGN)
        self.set(RIGHT_ANTENNA_GPIO, 0, RIGHT_SIGN)
        time.sleep(MIN_UPDATE_INTERVAL)
        self._pi.set_servo_pulsewidth(LEFT_ANTENNA_GPIO, 0)
        self._pi.set_servo_pulsewidth(RIGHT_ANTENNA_GPIO, 0)
        self._pi.stop()


class _PwmioBackend:
    """Original CircuitPython pwmio path — software-timed, so it jitters. Fallback."""

    name = "pwmio"

    def __init__(self):
        import board
        import pwmio
        self._pwmio = pwmio
        neutral = value_to_duty_cycle(0)
        self._left = pwmio.PWMOut(board.D13, frequency=50, duty_cycle=neutral)
        self._right = pwmio.PWMOut(board.D12, frequency=50, duty_cycle=neutral)

    def set(self, which, value, sign):
        pwm = self._left if which == LEFT_ANTENNA_GPIO else self._right
        pwm.duty_cycle = value_to_duty_cycle(value * sign)

    def stop(self):
        time.sleep(MIN_UPDATE_INTERVAL)
        self.set(LEFT_ANTENNA_GPIO, 0, LEFT_SIGN)
        self.set(RIGHT_ANTENNA_GPIO, 0, RIGHT_SIGN)
        time.sleep(MIN_UPDATE_INTERVAL)
        self._left.deinit()
        self._right.deinit()


class Antennas:
    def __init__(self, prefer_pigpio=True):
        self._backend = None
        if prefer_pigpio:
            try:
                self._backend = _PigpioBackend()
                print("[antennas] pigpio hardware PWM (jitter-free)")
            except Exception as e:  # noqa: BLE001 - fall back rather than refuse to run
                print(f"[antennas] pigpio unavailable ({e}); falling back to pwmio "
                      "(ears will jitter — see ops/pigpio/ to enable the fix)")
        if self._backend is None:
            self._backend = _PwmioBackend()

    @property
    def backend(self):
        return self._backend.name

    def set_position_left(self, position):
        if -1 <= position <= 1:
            self._backend.set(LEFT_ANTENNA_GPIO, position, LEFT_SIGN)
        else:
            print("Invalid input! Enter a value between -1 and 1.")

    def set_position_right(self, position):
        if -1 <= position <= 1:
            self._backend.set(RIGHT_ANTENNA_GPIO, position, RIGHT_SIGN)
        else:
            print("Invalid input! Enter a value between -1 and 1.")

    def stop(self):
        try:
            self._backend.stop()
        except Exception as e:  # noqa: BLE001
            print(f"[antennas] stop error: {e}")


if __name__ == "__main__":
    import math

    antennas = Antennas()
    print(f"backend = {antennas.backend}")
    try:
        start_time = time.monotonic()
        current_time = start_time
        while current_time - start_time < 5:
            value = math.sin(2 * math.pi * 1 * current_time)
            antennas.set_position_left(value)
            antennas.set_position_right(value)
            time.sleep(MIN_UPDATE_INTERVAL)
            current_time = time.monotonic()
    finally:
        antennas.stop()
