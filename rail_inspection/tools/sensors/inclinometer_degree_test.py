#!/usr/bin/env python3
"""Murata SCL3300 Inclinometer Degree Test & Video Demonstration Tool.

Tests and records inclination in Degrees (0°, 5°, 20°, 45°) and Cross-Level (mm)
over Indian Broad Gauge (1676 mm).
"""

import argparse
import math
import os
import struct
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    import spidev
except ImportError:
    spidev = None

# SCL3300 Pre-computed commands
CMD_READ_WHOAMI   = 0x40000091
CMD_SW_RESET      = 0xB4002098
CMD_READ_STATUS   = 0x180000E5
CMD_DUMMY         = 0x000000FF

# Modes:
# Mode 1: +/-3g, full +/-90° range (essential for 45° test!)
CMD_CHANGE_MODE1  = 0xB400001F
# Mode 4: +/-0.5g, +/-10° to +/-30° range (high sensitivity for track survey)
CMD_CHANGE_MODE4  = 0xB4000338

# Angle readout commands (Datasheet Section 4.3):
CMD_READ_ANG_X    = 0x240000C7
CMD_READ_ANG_Y    = 0x280000CD
CMD_READ_ANG_Z    = 0x2C0000CB

# Acceleration readout commands:
CMD_READ_ACC_X    = 0x040000F7
CMD_READ_ACC_Y    = 0x080000FD
CMD_READ_ACC_Z    = 0x0C0000FB

GAUGE_MM = 1676.0


def crc8(b0: int, b1: int, b2: int) -> int:
    crc = 0xFF
    for b in (b0, b1, b2):
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x1D) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc ^ 0xFF


def frame_bytes(cmd: int):
    return [(cmd >> 24) & 0xFF, (cmd >> 16) & 0xFF, (cmd >> 8) & 0xFF, cmd & 0xFF]


def xfer(spi, cmd: int) -> int:
    res = spi.xfer2(frame_bytes(cmd))
    return int.from_bytes(bytes(res), "big")


def check_crc(val: int) -> bool:
    b0 = (val >> 24) & 0xFF
    b1 = (val >> 16) & 0xFF
    b2 = (val >> 8) & 0xFF
    b3 = val & 0xFF
    return b3 == crc8(b0, b1, b2)


def frame_data(val: int) -> int:
    raw = (val >> 8) & 0xFFFF
    return struct.unpack(">h", raw.to_bytes(2, "big"))[0]


def frame_rs(val: int) -> int:
    return (val >> 24) & 0x03


class Inclinometer:
    def __init__(self, mode: int = 1, axis: str = "X"):
        if spidev is None:
            raise RuntimeError("spidev module not installed. Run on BeagleBone.")
        self.mode = mode
        self.axis = axis.upper()
        self.spi = spidev.SpiDev()
        self.spi.open(1, 0)
        self.spi.max_speed_hz = 500000
        self.spi.mode = 0
        self.zero_deg = 0.262
        self.scale_factor = 1.0359
        self._init_sensor()

    def _init_sensor(self):
        # Software reset
        xfer(self.spi, CMD_SW_RESET)
        time.sleep(0.15)

        # Set Mode (Mode 1 for +/-90°, Mode 4 for +/-10°)
        mode_cmd = CMD_CHANGE_MODE1 if self.mode == 1 else CMD_CHANGE_MODE4
        xfer(self.spi, mode_cmd)
        time.sleep(0.15)

        # Pipeline flush (3 transfers)
        xfer(self.spi, CMD_READ_STATUS)
        time.sleep(0.005)
        xfer(self.spi, CMD_READ_STATUS)
        time.sleep(0.005)
        rx = xfer(self.spi, CMD_DUMMY)

        rs = frame_rs(rx)
        if rs != 0x01:
            time.sleep(0.05)
            xfer(self.spi, CMD_READ_STATUS)
            rx = xfer(self.spi, CMD_DUMMY)
            rs = frame_rs(rx)

        print(f"[SCL3300] RS = 0x{rs:02X} ({'NORMAL' if rs == 0x01 else 'NOT READY'})")

        # Read WHOAMI
        xfer(self.spi, CMD_READ_WHOAMI)
        time.sleep(0.002)
        who_rx = xfer(self.spi, CMD_DUMMY)
        whoami = frame_data(who_rx) & 0xFF
        print(f"[SCL3300] WHOAMI = 0x{whoami:02X} (expected 0xC1)")
        if whoami != 0xC1:
            print(f"[WARN] WHOAMI mismatch!")

        # Prime the ACC pipeline with 2 dummy reads
        acc_cmd = CMD_READ_ACC_X
        if self.axis == "Y":
            acc_cmd = CMD_READ_ACC_Y
        elif self.axis == "Z":
            acc_cmd = CMD_READ_ACC_Z
        xfer(self.spi, acc_cmd)
        time.sleep(0.002)
        xfer(self.spi, acc_cmd)
        time.sleep(0.002)
        print(f"[SCL3300] Mode {'1 (+/-90 deg)' if self.mode == 1 else '4 (+/-10 deg)'} | Axis: ACC_{self.axis} | Ready")

    def read_angle(self) -> float:
        """Read inclination angle in degrees (-90.0 to +90.0).

        Uses ACC register + asin() for reliable readings across all
        SCL3300 firmware revisions (ANG registers return 0 on some chips).
        Mode 1 sensitivity: 6000 LSB/g.  Mode 4: 12000 LSB/g.
        """
        if self.axis == "Y":
            cmd = CMD_READ_ACC_Y
        elif self.axis == "Z":
            cmd = CMD_READ_ACC_Z
        else:
            cmd = CMD_READ_ACC_X

        xfer(self.spi, cmd)
        time.sleep(0.002)
        rx = xfer(self.spi, CMD_DUMMY)

        if not check_crc(rx):
            return float("nan")
        if frame_rs(rx) != 0x01:
            return float("nan")

        raw_int = frame_data(rx)
        # Mode 1: 6000 LSB/g, Mode 4: 12000 LSB/g
        sens = 6000.0 if self.mode == 1 else 12000.0
        acc_g = raw_int / sens
        acc_g = max(-1.0, min(1.0, acc_g))
        deg = math.degrees(math.asin(acc_g))
        scale = getattr(self, "scale_factor", 1.0)
        return (deg - self.zero_deg) * scale

    def close(self):
        self.spi.close()


def render_bar(deg: float, max_deg: float = 50.0, width: int = 30) -> str:
    """ASCII bar visualization of tilt."""
    clamped = max(-max_deg, min(max_deg, deg))
    norm = clamped / max_deg
    mid = width // 2
    pos = int(mid + norm * mid)
    pos = max(0, min(width, pos))
    bar = [" "] * (width + 1)
    bar[mid] = "|"
    if pos < mid:
        for i in range(pos, mid):
            bar[i] = "="
    elif pos > mid:
        for i in range(mid + 1, pos + 1):
            bar[i] = "="
    return "[" + "".join(bar) + "]"


def read_live_gauge_mm() -> float:
    """Read live gauge from shared memory (rail_sensor_shm).
    Falls back to GAUGE_MM constant if sensor_service is not running.
    struct layout: <IIIIqddddiBBBB
      index 0=magic, 1=ver, 2=seq, 3=update_count, 4=ts_us,
      5=cross_level_mm, 6=twist_mm_per_m, 7=chainage_m, 8=gauge_mm, ...
    """
    try:
        SHM_STRUCT = struct.Struct('<IIIIqddddiBBBB')
        with open('/dev/shm/rail_sensor_shm', 'rb') as f:
            data = f.read(SHM_STRUCT.size)
        if len(data) >= SHM_STRUCT.size:
            unpacked = SHM_STRUCT.unpack_from(data)
            gauge = unpacked[8]  # gauge_mm field
            if 1500.0 < gauge < 1800.0:  # sanity range for Indian BG/MG
                return gauge
    except Exception:
        pass
    return GAUGE_MM  # fallback to constant


def _trimmed_mean_and_stdev(samples):
    """Compute 10-sample trimmed mean (drop min & max) and standard deviation."""
    vals = sorted(samples)
    trimmed = vals[1:-1] if len(vals) >= 5 else vals
    mean_val = sum(trimmed) / len(trimmed)
    var = sum((x - mean_val) ** 2 for x in trimmed) / len(trimmed)
    return mean_val, math.sqrt(var)


def live_display_mode(incl: Inclinometer, duration: float = 0.0, auto_tare: bool = False):
    """Clean live console display suitable for video recording.
    Shows Raw Angle, 10-Sample Trimmed Moving Average Angle, Cross-Level, and StdDev.
    Cross-Level = live_gauge_mm * sin(avg_angle_rad)
    """
    from collections import deque

    if auto_tare:
        print("\n[TARE] Capturing 20 samples at 0.0° for zero calibration...", end="", flush=True)
        tare_samples = []
        for _ in range(20):
            d = incl.read_angle()
            if math.isfinite(d):
                tare_samples.append(d + incl.zero_deg)
            time.sleep(0.05)
        if tare_samples:
            incl.zero_deg = sum(tare_samples) / len(tare_samples)
            print(f" Done! zero_deg = {incl.zero_deg:+.4f}°")
        else:
            print(" Failed (no valid samples)")

    # Read live gauge once at startup (updated each loop iteration)
    live_gauge = read_live_gauge_mm()

    mode_label = "Mode 4 (+/-10° to +/-30°, High-Res)" if incl.mode == 4 else "Mode 1 (+/-90°)"
    print("\n" + "=" * 74)
    print("      MURATA SCL3300 INCLINOMETER LIVE MONITOR (VIDEO RECORDING MODE)")
    print("=" * 74)
    print(f" Sensor Axis: ACC/ANG_{incl.axis}  |  {mode_label}  |  Gauge: {live_gauge:.0f} mm")
    print(" Filter: 10-sample Trimmed Mean (drop min/max, 1.0s @ 10 Hz)")
    print(" Controls: Tilt board | Press Ctrl+C to stop")
    print("-" * 74)
    print(f"{'Elapsed':>8s} | {'Raw Angle':>11s} | {'Avg Angle (1s)':>14s} | {'Cross-Level':>12s} | {'StdDev':>8s}")
    print("-" * 74)

    # 10 samples @ 10 Hz = 1.0 second moving average window
    filter_window = deque(maxlen=10)

    start = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            elapsed = now - start
            raw_deg = incl.read_angle()

            # Refresh live gauge each iteration (tracks gauge sensor updates)
            live_gauge = read_live_gauge_mm()

            if math.isfinite(raw_deg):
                # Moving average filtering (10 samples)
                filter_window.append(raw_deg)

                if len(filter_window) >= 10:
                    # 10 samples (1.0 second) filled: display trimmed mean, cross level, and stdev
                    avg_deg, stdev_deg = _trimmed_mean_and_stdev(filter_window)
                    avg_str = f"{avg_deg:+13.3f}°"
                    rad = math.radians(avg_deg)
                    # Cross-Level = live gauge (mm) * sin(avg_angle)
                    cl_mm = live_gauge * math.sin(rad)
                    cl_str = f"{cl_mm:+9.3f} mm"
                    std_str = f"±{stdev_deg:.3f}°"
                else:
                    # Initial 0.00s to 0.90s warmup: show '--' until 10 samples collected
                    avg_str = "       --     "
                    cl_str = "     --     "
                    std_str = "   --   "

                print(f"{elapsed:7.2f}s | {raw_deg:+10.3f}° | {avg_str} | {cl_str} | {std_str}", flush=True)
            else:
                print(f"{elapsed:7.2f}s |   CRC/ERR   |        ERR     |     ERR      |   ERR   ", flush=True)

            if duration > 0 and elapsed >= duration:
                break
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n[Stopped by user]")


def step_test_mode(incl: Inclinometer):
    """Step-by-step verification of 0°, 5°, 20°, and 45° with automated report output."""
    targets = [0.0, 5.0, 20.0, 45.0]
    results = []

    print("\n" + "=" * 75)
    print("        MURATA SCL3300 MULTI-ANGLE ACCURACY VERIFICATION TEST")
    print("=" * 75)
    print("This procedure captures high-precision samples at 0°, 5°, 20°, and 45°.")
    print("Mount your PCB / sensor securely with a digital protractor / angle gauge.\n")

    input("Step 0: Place sensor on FLAT REFERENCE surface (0.0°). Press [Enter] to calibrate zero...")
    print("  Capturing reference zero...")
    samples = []
    for _ in range(30):
        d = incl.read_angle()
        if math.isfinite(d):
            samples.append(d)
        time.sleep(0.05)
    if samples:
        incl.zero_deg = sum(samples) / len(samples)
        print(f"  Zero tare offset: {incl.zero_deg:+.2f}° (calibrated)\n")

    for target in targets:
        print("-" * 75)
        print(f"STEP: Tilt sensor to Nominal Target = {target:+.1f}°")
        input(f"  Set angle to {target:+.1f}° on your reference level and press [Enter] to capture...")
        print(f"  Capturing 40 samples at {target:+.1f}°...", end="", flush=True)

        samples = []
        for _ in range(40):
            d = incl.read_angle()
            if math.isfinite(d):
                samples.append(d)
            time.sleep(0.05)
            print(".", end="", flush=True)
        print(" Done!")

        if not samples:
            print("  [ERROR] No valid samples captured!")
            continue

        mean_deg = sum(samples) / len(samples)
        min_deg = min(samples)
        max_deg = max(samples)
        variance = sum((x - mean_deg) ** 2 for x in samples) / len(samples)
        stdev = math.sqrt(variance)
        abs_err = abs(mean_deg - target)
        err_pct = (abs_err / target * 100.0) if target != 0.0 else (abs_err * 100.0)
        cl_mm = GAUGE_MM * math.sin(math.radians(mean_deg))
        theoretical_cl_mm = GAUGE_MM * math.sin(math.radians(target))
        cl_err_mm = abs(cl_mm - theoretical_cl_mm)

        res = {
            "target_deg": target,
            "measured_deg": mean_deg,
            "min_deg": min_deg,
            "max_deg": max_deg,
            "stdev_deg": stdev,
            "abs_error_deg": abs_err,
            "error_pct": err_pct,
            "cross_level_mm": cl_mm,
            "expected_cl_mm": theoretical_cl_mm,
            "cl_error_mm": cl_err_mm,
        }
        results.append(res)
        print(f"  -> Measured Mean: {mean_deg:+.2f}° (Error: {abs_err:.2f}°, Cross-Level: {cl_mm:.1f} mm)\n")

    # Generate Report Table
    print("\n" + "=" * 82)
    print("                        INCLINOMETER TEST REPORT SUMMARY")
    print("=" * 82)
    print(f"{'Nominal':>8s} | {'Measured':>9s} | {'Std Dev':>8s} | {'Angle Err':>10s} | {'Nominal CL':>11s} | {'Measured CL':>12s} | {'CL Err':>8s}")
    print(f"{'(Deg)':>8s} | {'(Deg)':>9s} | {'(Deg)':>8s} | {'(Deg)':>10s} | {'(mm)':>11s} | {'(mm)':>12s} | {'(mm)':>8s}")
    print("-" * 82)
    for r in results:
        print(f"{r['target_deg']:8.1f}° | {r['measured_deg']:8.2f}° | {r['stdev_deg']:7.3f}° | {r['abs_error_deg']:9.2f}° | {r['expected_cl_mm']:10.1f} mm | {r['cross_level_mm']:11.1f} mm | {r['cl_error_mm']:7.2f} mm")
    print("=" * 82)

    # Save to file
    out_file = Path("/home/debian/trolley/inclinometer_test_results.txt")
    with open(out_file, "w") as f:
        f.write("Murata SCL3300 Inclinometer Multi-Angle Test Report\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Track Gauge: {GAUGE_MM:.0f} mm\n\n")
        f.write(f"{'Nominal (deg)':>14s} | {'Measured (deg)':>14s} | {'Std Dev (deg)':>14s} | {'Angle Err (deg)':>15s} | {'Expected CL (mm)':>16s} | {'Measured CL (mm)':>16s} | {'CL Err (mm)':>12s}\n")
        f.write("-" * 115 + "\n")
        for r in results:
            f.write(f"{r['target_deg']:13.1f}° | {r['measured_deg']:13.2f}° | {r['stdev_deg']:13.3f}° | {r['abs_error_deg']:14.2f}° | {r['expected_cl_mm']:15.1f} mm | {r['cross_level_mm']:15.1f} mm | {r['cl_error_mm']:11.2f} mm\n")
    print(f"\nReport saved to: {out_file}\n")


def main():
    parser = argparse.ArgumentParser(description="Murata SCL3300 Multi-Angle Verification & Video Demo Tool")
    parser.add_argument("--live", action="store_true", help="Run live continuous display for video recording")
    parser.add_argument("--test", action="store_true", help="Run guided step-by-step 0°, 5°, 20°, 45° test report")
    parser.add_argument("--axis", default=os.environ.get("RAIL_SCL_AXIS", "X"), choices=["X", "Y", "Z"], help="Sensor tilt axis (default X)")
    parser.add_argument("--mode", type=int, default=4, choices=[1, 4], help="SCL3300 mode: 4=high-res +/-30° (default), 1=full +/-90°")
    parser.add_argument("--tare", action="store_true", help="Auto-calibrate 0.0° zero offset at startup")
    parser.add_argument("--duration", type=float, default=0.0, help="Live display duration in seconds (0 = infinite)")
    args = parser.parse_args()

    # Before opening spidev, temporarily stop sensor_service if running to avoid SPI bus conflict
    svc_was_running = False
    ret = os.system("pgrep -x sensor_service >/dev/null 2>&1")
    if ret == 0:
        print("[INFO] Temporarily pausing background sensor_service to reserve SPI bus...")
        os.system("sudo pkill -STOP -x sensor_service >/dev/null 2>&1")
        svc_was_running = True

    try:
        incl = Inclinometer(mode=args.mode, axis=args.axis)
        if args.test:
            step_test_mode(incl)
        else:
            live_display_mode(incl, duration=args.duration, auto_tare=args.tare)
        incl.close()
    finally:
        if svc_was_running:
            print("[INFO] Resuming background sensor_service...")
            os.system("sudo pkill -CONT -x sensor_service >/dev/null 2>&1")


if __name__ == "__main__":
    main()
