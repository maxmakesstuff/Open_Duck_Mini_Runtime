"""
Gamepad input inspector - figure out how YOUR controller reports the D-pad.

Run this ON THE ROBOT (the controller is paired to the Pi):
    cd scripts && python gamepad_debug.py

It prints the controller's capabilities, then every input change live. Press
DPAD-LEFT and DPAD-RIGHT and note what shows up:
  * "hat 0 -> (-1, 0)"   d-pad is on the hat   (left/right work via the hat)
  * "axis 6 -> -1.0"     d-pad is on axes 6/7  (handled by the axes fallback)
  * "button 13 -> 1"     d-pad is on buttons   (tell me the indices and I'll map them)

Ctrl-C to quit.
"""
import time
import pygame


def main():
    pygame.init()
    pygame.joystick.init()
    if pygame.joystick.get_count() == 0:
        print("No joystick found. Is the controller paired/connected?")
        return

    j = pygame.joystick.Joystick(0)
    j.init()
    nb, na, nh = j.get_numbuttons(), j.get_numaxes(), j.get_numhats()
    print(f"Controller: '{j.get_name()}'")
    print(f"  buttons={nb}  axes={na}  hats={nh}")
    print("\nPress DPAD-LEFT then DPAD-RIGHT and watch what changes:\n")

    prev_b = [0] * nb
    prev_h = [(0, 0)] * nh
    prev_a = [0.0] * na

    try:
        while True:
            pygame.event.pump()
            for i in range(nb):
                v = j.get_button(i)
                if v != prev_b[i]:
                    print(f"button {i} -> {v}")
                    prev_b[i] = v
            for i in range(nh):
                v = j.get_hat(i)
                if v != prev_h[i]:
                    print(f"hat {i} -> {v}")
                    prev_h[i] = v
            for i in range(na):
                v = round(j.get_axis(i), 2)
                if abs(v - prev_a[i]) > 0.3:   # ignore stick jitter
                    print(f"axis {i} -> {v}")
                    prev_a[i] = v
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
