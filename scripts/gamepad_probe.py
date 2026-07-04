#!/usr/bin/env python3
"""Raw gamepad probe (bring-up / debug).

Shows exactly what THIS controller reports so we can see which axis / hat / button
each D-pad direction maps to. Useful when the D-pad seems to move the robot or
record/playback (D-pad Left/Right) won't fire — usually the pad is reporting the
D-pad on a stick axis instead of a hat.

Run on the robot (stop the walk first so nothing moves):
    python scripts/gamepad_probe.py

Then press D-pad Left, Right, Up, Down one at a time and watch which field reacts.
For reference, the walk loop (xbox_controller.py) reads:
    L-stick = axis 0/1   R-stick = axis 2/3   triggers = axis 4/5
    D-pad   = hat 0, with a fallback to axis 6/7
"""
import os
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")   # headless-safe (no display)
import pygame  # noqa: E402


def main():
    pygame.init()
    pygame.joystick.init()
    print("Waiting for a gamepad… (turn it on / press a button)")
    j = None
    while j is None:
        pygame.event.pump()
        if pygame.joystick.get_count() > 0:
            j = pygame.joystick.Joystick(0)
            j.init()
        else:
            pygame.joystick.quit()
            pygame.joystick.init()
            time.sleep(0.5)

    print(f"\nCONNECTED: name={j.get_name()!r}")
    print(f"  axes={j.get_numaxes()}  buttons={j.get_numbuttons()}  hats={j.get_numhats()}")
    print("\n>>> Press D-pad LEFT, then RIGHT, then UP, then DOWN (one at a time).")
    print(">>> Note which axis / hat / button number reacts. Ctrl-C to stop.\n")

    prev = None
    while True:
        pygame.event.pump()
        axes = [round(j.get_axis(i), 2) for i in range(j.get_numaxes())]
        hats = [j.get_hat(i) for i in range(j.get_numhats())]
        btns = [i for i in range(j.get_numbuttons()) if j.get_button(i)]
        # print only on a meaningful change (ignore tiny stick drift)
        key = (tuple(1 if abs(a) > 0.4 else 0 for a in axes), tuple(hats), tuple(btns))
        if key != prev:
            active = [f"ax{i}={v}" for i, v in enumerate(axes) if abs(v) > 0.4]
            print(f"hats={hats}  buttons_down={btns}  active_axes={active or '-'}")
            prev = key
        time.sleep(0.05)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nbye")
