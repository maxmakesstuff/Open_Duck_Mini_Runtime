import pygame
from threading import Thread
from queue import Queue
import time
import numpy as np
from mini_bdx_runtime.buttons import Buttons


X_RANGE = [-0.15, 0.15]
Y_RANGE = [-0.2, 0.2]
YAW_RANGE = [-1.0, 1.0]

# rads
NECK_PITCH_RANGE = [-0.34, 1.1]
HEAD_PITCH_RANGE = [-0.78, 0.3]
HEAD_YAW_RANGE = [-0.5, 0.5]
HEAD_ROLL_RANGE = [-0.5, 0.5]

# D-pad axis indices used by the axes-fallback (Linux often exposes the d-pad
# as ABS_HAT0X / ABS_HAT0Y, which pygame may present as axes rather than a hat).
DPAD_AXIS_X = 6
DPAD_AXIS_Y = 7
DPAD_AXIS_THRESHOLD = 0.5

# If the pad is absent, retry opening it at most this often (s) so a controller
# switched on mid-run re-attaches WITHOUT restarting the robot. Pairs with the
# OS-level auto-reconnect in ops/bluetooth/.
REOPEN_INTERVAL_S = 2.0


def decode_dpad(hat, axes):
    """Return the d-pad state as (dx, dy) in {-1, 0, 1}, hat convention
    (right = +1, up = +1).

    Many Xbox controllers report the d-pad as a hat, but several recent ones
    (especially over Bluetooth on Linux) report a *phantom* hat and put the
    d-pad on axes 6/7 instead. We read the hat first and, only if it carries
    nothing, fall back to those axes. `hat` may be None; `axes` may be None or
    a list of axis values.
    """
    dx = dy = 0
    if hat is not None and len(hat) >= 2:
        dx, dy = int(hat[0]), int(hat[1])
    if dx == 0 and dy == 0 and axes is not None and len(axes) > DPAD_AXIS_Y:
        ax = axes[DPAD_AXIS_X]
        ay = axes[DPAD_AXIS_Y]
        if ax <= -DPAD_AXIS_THRESHOLD:
            dx = -1
        elif ax >= DPAD_AXIS_THRESHOLD:
            dx = 1
        if ay <= -DPAD_AXIS_THRESHOLD:
            dy = 1   # axis "up" is negative -> d-pad up is +1
        elif ay >= DPAD_AXIS_THRESHOLD:
            dy = -1
    return dx, dy


def merge_axes(pad_axes, pad_trig, web_active, web_axes, web_trig):
    """Merge gamepad + web stick sources (pure, so it unit-tests off-robot).

    While the web is `active` its axes OVERRIDE the pad's (so you can drive from
    the phone); otherwise the pad wins. Triggers take the max of both (either
    source can raise an antenna). Both sources use the same normalized
    convention (up/right = +). Returns (l_x, l_y, r_x, r_y, left_trig, right_trig).
    """
    if web_active:
        l_x, l_y, r_x, r_y = web_axes
    else:
        l_x, l_y, r_x, r_y = pad_axes
    lt = max(pad_trig[0], web_trig[0])
    rt = max(pad_trig[1], web_trig[1])
    return l_x, l_y, r_x, r_y, lt, rt


class XBoxController:
    def __init__(self, command_freq, only_head_control=False, web_bus=None):
        self.command_freq = command_freq
        self.head_control_mode = only_head_control
        self.only_head_control = only_head_control
        self.web_bus = web_bus

        self.last_commands = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self.last_left_trigger = 0.0
        self.last_right_trigger = 0.0
        pygame.init()
        pygame.joystick.init()

        self.p1 = None
        self._last_open_attempt = 0.0
        self._prev_Y = False
        self._open_joystick(verbose=True)
        if self.p1 is None:
            print(
                "[xbox] no gamepad found — running WITHOUT a pad "
                "(web UI still works; the pad auto-attaches when switched on)."
            )

        self.cmd_queue = Queue(maxsize=1)

        self.A_pressed = False
        self.B_pressed = False
        self.X_pressed = False
        self.Y_pressed = False
        self.LB_pressed = False
        self.RB_pressed = False

        self.buttons = Buttons()

        Thread(target=self.commands_worker, daemon=True).start()

    # ------------------------------------------------------ joystick lifecycle
    def _open_joystick(self, verbose=False):
        """(Re)open gamepad 0 if present. Never raises — a missing pad just leaves
        self.p1 = None so the reader keeps running (web-only / awaiting reconnect)."""
        self._last_open_attempt = time.time()
        try:
            if pygame.joystick.get_count() > 0:
                self.p1 = pygame.joystick.Joystick(0)
                self.p1.init()
                if verbose:
                    print(
                        f"Loaded joystick '{self.p1.get_name()}' | "
                        f"axes={self.p1.get_numaxes()} buttons={self.p1.get_numbuttons()} "
                        f"hats={self.p1.get_numhats()}"
                    )
                return True
        except pygame.error as e:
            if verbose:
                print(f"[xbox] joystick open failed: {e}")
            self.p1 = None
        return False

    def _pad_present(self):
        return self.p1 is not None

    def commands_worker(self):
        while True:
            self.cmd_queue.put(self.get_commands())
            time.sleep(1 / self.command_freq)

    # ------------------------------------------------------------ pad reading
    def _read_pad(self):
        """Return the pad's axes/triggers/buttons/dpad, or all-neutral if the pad
        is absent. Guarded so a mid-read disconnect can't crash the loop; on
        failure the pad is dropped and re-probed later."""
        neutral = {
            "l_x": 0.0, "l_y": 0.0, "r_x": 0.0, "r_y": 0.0, "lt": 0.0, "rt": 0.0,
            "A": False, "B": False, "X": False, "Y": False, "LB": False, "RB": False,
            "du": False, "dd": False, "dl": False, "dr": False,
        }
        # --- process the SDL event queue: button edges + hotplug ---
        try:
            for event in pygame.event.get():
                if event.type == pygame.JOYDEVICEADDED and self.p1 is None:
                    self._open_joystick(verbose=True)
                elif event.type == pygame.JOYDEVICEREMOVED:
                    self.p1 = None
                    self._reset_pad_buttons()
                    print("[xbox] gamepad disconnected — waiting for reconnect…")
                elif event.type == pygame.JOYBUTTONDOWN and self.p1 is not None:
                    self._on_button_down()
                elif event.type == pygame.JOYBUTTONUP:
                    self._reset_pad_buttons()
            pygame.event.pump()
        except pygame.error:
            self.p1 = None

        # --- periodic re-probe if we have no pad (belt-and-suspenders vs the
        #     ADDED event, which some SDL builds drop on headless setups) ---
        if self.p1 is None:
            if time.time() - self._last_open_attempt >= REOPEN_INTERVAL_S:
                self._open_joystick()
            return neutral

        try:
            l_x = -1 * self.p1.get_axis(0)
            l_y = -1 * self.p1.get_axis(1)
            r_x = -1 * self.p1.get_axis(2)
            r_y = -1 * self.p1.get_axis(3)
            right_trigger = float(np.around((self.p1.get_axis(4) + 1) / 2, 3))
            left_trigger = float(np.around((self.p1.get_axis(5) + 1) / 2, 3))
            if left_trigger < 0.1:
                left_trigger = 0.0
            if right_trigger < 0.1:
                right_trigger = 0.0
            hat = self.p1.get_hat(0) if self.p1.get_numhats() > 0 else None
            axes = [self.p1.get_axis(i) for i in range(self.p1.get_numaxes())]
        except pygame.error:
            self.p1 = None
            self._reset_pad_buttons()
            return neutral

        left_right, up_down = decode_dpad(hat, axes)
        return {
            "l_x": l_x, "l_y": l_y, "r_x": r_x, "r_y": r_y,
            "lt": left_trigger, "rt": right_trigger,
            "A": self.A_pressed, "B": self.B_pressed, "X": self.X_pressed,
            "Y": self.Y_pressed, "LB": self.LB_pressed, "RB": self.RB_pressed,
            "du": up_down == 1, "dd": up_down == -1,
            "dl": left_right == -1, "dr": left_right == 1,
        }

    def _on_button_down(self):
        if self.p1 is None:
            return
        if self.p1.get_button(0):
            self.A_pressed = True
        if self.p1.get_button(1):
            self.B_pressed = True
        if self.p1.get_button(3):
            self.X_pressed = True
        if self.p1.get_button(4):
            self.Y_pressed = True
        if self.p1.get_button(6):
            self.LB_pressed = True
        if self.p1.get_button(7):
            self.RB_pressed = True

    def _reset_pad_buttons(self):
        self.A_pressed = self.B_pressed = self.X_pressed = False
        self.Y_pressed = self.LB_pressed = self.RB_pressed = False

    # ---------------------------------------------------------- command build
    def get_commands(self):
        last_commands = self.last_commands

        pad = self._read_pad()

        # web overrides (optional)
        w_active = False
        w_axes = (0.0, 0.0, 0.0, 0.0)
        w_trig = (0.0, 0.0)
        wbtn = {k: False for k in
                ("A", "B", "X", "Y", "LB", "RB",
                 "dpad_up", "dpad_down", "dpad_left", "dpad_right")}
        if self.web_bus is not None:
            active, wl_x, wl_y, wr_x, wr_y, wlt, wrt = self.web_bus.stick_override(time.time())
            w_active = active
            w_axes = (wl_x, wl_y, wr_x, wr_y)
            w_trig = (wlt, wrt)
            wbtn = self.web_bus.consume_buttons()

        l_x, l_y, r_x, r_y, left_trigger, right_trigger = merge_axes(
            (pad["l_x"], pad["l_y"], pad["r_x"], pad["r_y"]),
            (pad["lt"], pad["rt"]),
            w_active, w_axes, w_trig,
        )

        # final button state = pad OR web (fed to the same edge detector)
        A = pad["A"] or wbtn["A"]
        B = pad["B"] or wbtn["B"]
        X = pad["X"] or wbtn["X"]
        Y = pad["Y"] or wbtn["Y"]
        LB = pad["LB"] or wbtn["LB"]
        RB = pad["RB"] or wbtn["RB"]
        du = pad["du"] or wbtn["dpad_up"]
        dd = pad["dd"] or wbtn["dpad_down"]
        dl = pad["dl"] or wbtn["dpad_left"]
        dr = pad["dr"] or wbtn["dpad_right"]

        # Y toggles head-control mode on a rising edge (works for pad AND web),
        # unless this controller is pinned to head-control only.
        if Y and not self._prev_Y and not self.only_head_control:
            self.head_control_mode = not self.head_control_mode
        self._prev_Y = Y

        if not self.head_control_mode:
            lin_vel_y = l_x
            lin_vel_x = l_y
            ang_vel = r_x
            if lin_vel_x >= 0:
                lin_vel_x *= np.abs(X_RANGE[1])
            else:
                lin_vel_x *= np.abs(X_RANGE[0])

            if lin_vel_y >= 0:
                lin_vel_y *= np.abs(Y_RANGE[1])
            else:
                lin_vel_y *= np.abs(Y_RANGE[0])

            if ang_vel >= 0:
                ang_vel *= np.abs(YAW_RANGE[1])
            else:
                ang_vel *= np.abs(YAW_RANGE[0])

            last_commands[0] = lin_vel_x
            last_commands[1] = lin_vel_y
            last_commands[2] = ang_vel
        else:
            last_commands[0] = 0.0
            last_commands[1] = 0.0
            last_commands[2] = 0.0
            last_commands[3] = 0.0  # neck pitch 0 for now

            head_yaw = l_x
            head_pitch = l_y
            head_roll = r_x

            if head_yaw >= 0:
                head_yaw *= np.abs(HEAD_YAW_RANGE[0])
            else:
                head_yaw *= np.abs(HEAD_YAW_RANGE[1])

            if head_pitch >= 0:
                head_pitch *= np.abs(HEAD_PITCH_RANGE[0])
            else:
                head_pitch *= np.abs(HEAD_PITCH_RANGE[1])

            if head_roll >= 0:
                head_roll *= np.abs(HEAD_ROLL_RANGE[0])
            else:
                head_roll *= np.abs(HEAD_ROLL_RANGE[1])

            last_commands[4] = head_pitch
            last_commands[5] = head_yaw
            last_commands[6] = head_roll

        return (
            np.around(last_commands, 3),
            A, B, X, Y, LB, RB,
            left_trigger, right_trigger,
            du, dd, dl, dr,
        )

    def get_last_command(self):
        A = B = X = Y = LB = RB = False
        du = dd = dl = dr = False
        try:
            (
                self.last_commands,
                A, B, X, Y, LB, RB,
                self.last_left_trigger,
                self.last_right_trigger,
                du, dd, dl, dr,
            ) = self.cmd_queue.get(False)  # non blocking
        except Exception:
            pass

        self.buttons.update(A, B, X, Y, LB, RB, du, dd, dl, dr)

        return (
            self.last_commands,
            self.buttons,
            self.last_left_trigger,
            self.last_right_trigger,
        )


if __name__ == "__main__":
    controller = XBoxController(20)

    while True:
        print(controller.get_last_command())
        time.sleep(0.05)
