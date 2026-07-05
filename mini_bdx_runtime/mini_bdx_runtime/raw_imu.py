import adafruit_bno055
import board
import busio
import numpy as np
import os
import pickle

from queue import Queue
from threading import Thread
import time

try:
    from mini_bdx_runtime.imu_trim import apply_trim
except ImportError:  # allow flat imports off the package (diagnostic scripts)
    from imu_trim import apply_trim

# Calibration offsets live in HOME so they load no matter what cwd the walk is
# launched from (a relative path silently ran the IMU uncalibrated otherwise). We
# still fall back to a cwd-local file so an existing calibration keeps working.
CALIB_FILENAME = "imu_calib_data.pkl"
CALIB_PATH = os.path.join(os.path.expanduser("~"), CALIB_FILENAME)


# TODO filter spikes
class Imu:
    def __init__(
        self, sampling_freq, user_pitch_bias=0, calibrate=False, upside_down=True,
        pitch_trim=0.0, roll_trim=0.0
    ):
        self.sampling_freq = sampling_freq
        self.calibrate = calibrate
        # Residual mounting-tilt trim (radians) applied to accel+gyro after the axis
        # remap. Default 0 -> identity. The legacy user_pitch_bias (degrees) folds in
        # as extra pitch so the old --pitch_bias flag is no longer a no-op.
        self.pitch_trim = float(pitch_trim) + float(np.radians(user_pitch_bias))
        self.roll_trim = float(roll_trim)

        i2c = busio.I2C(board.SCL, board.SDA)
        self.imu = adafruit_bno055.BNO055_I2C(i2c)

        # self.imu.mode = adafruit_bno055.IMUPLUS_MODE
        # self.imu.mode = adafruit_bno055.ACCGYRO_MODE
        # self.imu.mode = adafruit_bno055.GYRONLY_MODE
        self.imu.mode = adafruit_bno055.NDOF_MODE
        # self.imu.mode = adafruit_bno055.NDOF_FMC_OFF_MODE

        if upside_down:
            self.imu.axis_remap = (
                adafruit_bno055.AXIS_REMAP_Y,
                adafruit_bno055.AXIS_REMAP_X,
                adafruit_bno055.AXIS_REMAP_Z,
                adafruit_bno055.AXIS_REMAP_NEGATIVE,
                adafruit_bno055.AXIS_REMAP_NEGATIVE,
                adafruit_bno055.AXIS_REMAP_NEGATIVE,
            )

        else:
            self.imu.axis_remap = (
                adafruit_bno055.AXIS_REMAP_Y,
                adafruit_bno055.AXIS_REMAP_X,
                adafruit_bno055.AXIS_REMAP_Z,
                adafruit_bno055.AXIS_REMAP_NEGATIVE,
                adafruit_bno055.AXIS_REMAP_POSITIVE,
                adafruit_bno055.AXIS_REMAP_POSITIVE,
            )

        if self.calibrate:
            self.imu.mode = adafruit_bno055.NDOF_MODE
            print("=== IMU calibration ===")
            print("The walk policy only uses GYRO + ACCEL, so we calibrate those and")
            print("IGNORE the magnetometer (motors disturb it and it never converges).")
            print("  GYRO  : hold the duck perfectly still for a few seconds")
            print("  ACCEL : hold it in several stable poses (each side/face down),")
            print("          pausing a couple seconds on each, until accel reaches 3")
            print("Ctrl+C aborts. Completes automatically once gyro+accel are both 3.")
            print("")
            while True:
                sysc, gyroc, accelc, magc = self.imu.calibration_status
                print(
                    f"  status  gyro={gyroc}  accel={accelc}  (need both 3)   "
                    f"[sys={sysc} mag={magc} ignored]"
                )
                if gyroc >= 3 and accelc >= 3:
                    print("Gyro + accel calibrated.")
                    break
                time.sleep(0.5)

            imu_calib_data = {
                "offsets_accelerometer": self.imu.offsets_accelerometer,
                "offsets_gyroscope": self.imu.offsets_gyroscope,
                "offsets_magnetometer": self.imu.offsets_magnetometer,
            }
            for k, v in imu_calib_data.items():
                print(k, v)

            pickle.dump(imu_calib_data, open(CALIB_PATH, "wb"))
            print("Saved", CALIB_PATH)
            exit()

        calib_path = CALIB_PATH if os.path.exists(CALIB_PATH) else CALIB_FILENAME
        if os.path.exists(calib_path):
            imu_calib_data = pickle.load(open(calib_path, "rb"))
            self.imu.mode = adafruit_bno055.CONFIG_MODE
            time.sleep(0.1)
            self.imu.offsets_accelerometer = imu_calib_data["offsets_accelerometer"]
            self.imu.offsets_gyroscope = imu_calib_data["offsets_gyroscope"]
            self.imu.offsets_magnetometer = imu_calib_data["offsets_magnetometer"]
            self.imu.mode = adafruit_bno055.NDOF_MODE
            time.sleep(0.1)
            print(f"Loaded IMU calibration from {calib_path}")
        else:
            print(f"{CALIB_FILENAME} not found (looked in HOME and cwd)")
            print("Imu is running uncalibrated")

        self.x_offset = 0

        # self.tare_x()

        self.last_imu_data = [0, 0, 0, 0]
        self.last_imu_data = {
            "gyro": [0, 0, 0],
            "accelero": [0, 0, 0],
        }
        self.imu_queue = Queue(maxsize=1)
        Thread(target=self.imu_worker, daemon=True).start()

    def tare_x(self):
        print("Taring x ...")
        x_values = []
        num_values = 100
        ok = False
        while not ok:
            x_values.append(np.array(self.imu.acceleration)[0])

            x_values = x_values[-num_values:]

            if len(x_values) == num_values:
                mean = np.mean(x_values)
                std = np.std(x_values)
                if std < 0.05:
                    ok = True
                    self.x_offset = mean
                    print("Tare x done")
                else:
                    print(std)

            time.sleep(0.01)

    def imu_worker(self):
        while True:
            s = time.time()
            try:
                gyro = np.array(self.imu.gyro).copy()
                accelero = np.array(self.imu.acceleration).copy()
            except Exception as e:
                print("[IMU]:", e)
                continue

            if gyro is None or accelero is None:
                continue

            if gyro.any() is None or accelero.any() is None:
                continue

            accelero[0] -= self.x_offset

            # Correct residual IMU mounting tilt (same rigid rotation on both accel
            # and gyro). Default trim is 0 -> this is a no-op.
            if self.pitch_trim or self.roll_trim:
                accelero = apply_trim(accelero, self.pitch_trim, self.roll_trim)
                gyro = apply_trim(gyro, self.pitch_trim, self.roll_trim)

            data = {
                "gyro": gyro,
                "accelero": accelero,
            }

            self.imu_queue.put(data)
            took = time.time() - s
            time.sleep(max(0, 1 / self.sampling_freq - took))

    def get_data(self):
        try:
            self.last_imu_data = self.imu_queue.get(False)  # non blocking
        except Exception:
            pass

        return self.last_imu_data


if __name__ == "__main__":
    imu = Imu(50, upside_down=False)
    while True:
        data = imu.get_data()
        # print(data)
        print("gyro", np.around(data["gyro"], 3))
        print("accelero", np.around(data["accelero"], 3))
        print("---")
        time.sleep(1 / 25)
