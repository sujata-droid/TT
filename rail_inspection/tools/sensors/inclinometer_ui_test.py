#!/usr/bin/env python3
"""Murata SCL3300 Inclinometer Multi-Angle UI Test & Report Tool.

Reads directly from /dev/shm/rail_sensor_shm using active rail_config.json
calibration so that console readings and logs match the RailGUI25 touchscreen UI.

Verifies angles:
  0.0°  -> Theoretical Cross-Level:    0.0 mm
  5.0°  -> Theoretical Cross-Level:  146.1 mm (1676 * sin 5°)
 20.0°  -> Theoretical Cross-Level:  573.2 mm (1676 * sin 20°)
 45.0°  -> Theoretical Cross-Level: 1185.1 mm (1676 * sin 45°)
"""

import argparse
from datetime import datetime
import json
import math
import mmap
import os
from pathlib import Path
import struct
import sys
import time

SHM_PATH = "/dev/shm/rail_sensor_shm"
SHM_STRUCT = struct.Struct("<IIIIqddddiBBBB")
SHM_MAGIC = 0x5241494C
SHM_VERSION = 1
GAUGE_MM = 1676.0

PROJECT_ROOT = Path("/home/debian/trolley")
CONFIG_FILE = PROJECT_ROOT / "rail_config.json"
CAL_FILE = PROJECT_ROOT / "inclinometer_cal.json"
REPORT_FILE = PROJECT_ROOT / "inclinometer_test_results.txt"
ANGLE_LOG_FILE = PROJECT_ROOT / "inclinometer_angle.log"


def load_calibration():
    """Load offset and factor from config or cal file."""
    offset = 0.0
    factor = 1.0
    if CAL_FILE.exists():
        try:
            with open(CAL_FILE, "r") as f:
                c = json.load(f)
                offset = float(c.get("zero_offset_mm", 0.0))
                factor = float(c.get("scale_factor", 1.0))
        except Exception:
            pass
    elif CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                c = json.load(f)
                incl = c.get("incl", {})
                offset = float(incl.get("offset", 0.0))
                factor = float(incl.get("factor", 1.0))
        except Exception:
            pass
    return offset, factor


def open_shm():
    """Open shared memory frame."""
    if not os.path.exists(SHM_PATH):
        raise FileNotFoundError(f"Shared memory {SHM_PATH} not found. Is sensor_service running?")
    fd = os.open(SHM_PATH, os.O_RDONLY)
    return mmap.mmap(fd, SHM_STRUCT.size, access=mmap.ACCESS_READ)


def read_shm_sample(mm):
    """Read a single consistent sample from shared memory."""
    for _ in range(25):
        mm.seek(0)
        raw1 = mm.read(SHM_STRUCT.size)
        vals1 = SHM_STRUCT.unpack(raw1)
        seq1 = vals1[2]
        if seq1 & 1:
            time.sleep(0.002)
            continue

        mm.seek(0)
        raw2 = mm.read(SHM_STRUCT.size)
        vals2 = SHM_STRUCT.unpack(raw2)
        if vals2[2] == seq1 and not (vals2[2] & 1):
            magic, version, _seq, update_count, ts_us, raw_cl, tw, ch, gauge, enc, scl_ok, enc_ok, srv_ok, _ = vals2
            if magic != SHM_MAGIC or version != SHM_VERSION:
                raise RuntimeError("Shared memory version mismatch")
            return {
                "update_count": update_count,
                "raw_cl_mm": float(raw_cl),
                "scl_ok": bool(scl_ok),
                "srv_ok": bool(srv_ok),
            }
        time.sleep(0.002)
    raise TimeoutError("Failed to read stable shared memory frame")


def calculate_ui_values(raw_cl_mm, offset, factor):
    """Calculate UI calibrated cross-level and physical tilt angle."""
    calibrated_cl_mm = (raw_cl_mm - offset) * factor
    # Angle from cross-level over 1676 mm gauge:
    ratio = max(-1.0, min(1.0, calibrated_cl_mm / GAUGE_MM))
    angle_deg = math.degrees(math.asin(ratio))
    return calibrated_cl_mm, angle_deg


def render_tilt_bar(deg, max_deg=50.0, width=30):
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


def append_angle_log(msg: str):
    """Append a timestamped message to the permanent angle log file."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    try:
        with open(ANGLE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def run_live_monitor():
    """Continuously stream live UI Cross-Level and Angle to console and log file."""
    offset, factor = load_calibration()
    mm = open_shm()

    header_msg = (
        f"SESSION START: Zero Offset={offset:+.2f} mm | Scale Factor={factor:.4f} | "
        f"Standard=Indian Broad Gauge ({GAUGE_MM:.0f} mm)"
    )
    append_angle_log("=" * 80)
    append_angle_log(header_msg)
    append_angle_log("=" * 80)

    print("\n" + "=" * 80)
    print("      MURATA SCL3300 INCLINOMETER LIVE MONITOR (MATCHING TOUCHSCREEN UI)")
    print("=" * 80)
    print(f" Calibration: Zero Offset = {offset:+.2f} mm | Scale Factor = {factor:.4f}")
    print(f" Track Standard: Indian Broad Gauge ({GAUGE_MM:.0f} mm) | Source: /dev/shm/rail_sensor_shm")
    print(f" Logging live angle readings to: {ANGLE_LOG_FILE}")
    print(" Press Ctrl+C to stop")
    print("-" * 80)
    print(f"{'Elapsed':>8s} | {'UI Angle':>10s} | {'UI Cross-Level':>15s} | {'Raw CL':>10s} | {'Tilt Bar':^22s}")
    print("-" * 80)

    start = time.monotonic()
    last_log_time = 0.0
    last_detected_target = None
    target_hold_count = 0

    try:
        while True:
            sample = read_shm_sample(mm)
            cal_cl, angle = calculate_ui_values(sample["raw_cl_mm"], offset, factor)
            now = time.monotonic()
            elapsed = now - start
            bar = render_tilt_bar(angle, 50.0, 20)

            # Match UI rounding
            ui_cl_str = f"{cal_cl:7.1f} mm"
            angle_str = f"{angle:6.2f}°"
            raw_str = f"{sample['raw_cl_mm']:6.1f} mm"

            # Check if near target angle (5°, 20°, 45°, 0°)
            tag = ""
            current_target = None
            for target in (0.0, 5.0, 20.0, 45.0):
                if abs(abs(angle) - target) <= 0.5:
                    tag = f" <-- Near {target:4.1f}°"
                    current_target = target
                    break

            # Print live row to console
            print(f"{elapsed:7.2f}s | {angle_str:>10s} | {ui_cl_str:>15s} | {raw_str:>10s} | {bar}{tag}", flush=True)

            # Log periodically (every 0.5s) to file
            if now - last_log_time >= 0.5:
                append_angle_log(f"Angle: {angle:7.2f}° | UI Cross-Level: {cal_cl:7.1f} mm | Raw: {sample['raw_cl_mm']:7.1f} mm")
                last_log_time = now

            # If user holds at 5°, 20°, or 45°, log a highlighted detection event
            if current_target is not None and current_target in (5.0, 20.0, 45.0, 0.0):
                if current_target == last_detected_target:
                    target_hold_count += 1
                else:
                    last_detected_target = current_target
                    target_hold_count = 1

                # If held steady for 5 samples (approx 0.5s), log the confirmed angle event
                if target_hold_count == 5:
                    expected_cl = GAUGE_MM * math.sin(math.radians(current_target))
                    err = abs(cal_cl - expected_cl)
                    event_msg = (
                        f"*** [INCLINOMETER ANGLE REACHED: {current_target:4.1f}°] *** -> "
                        f"Measured Angle: {angle:6.2f}° | UI Cross-Level: {cal_cl:6.1f} mm "
                        f"(Expected: {expected_cl:6.1f} mm, Error: {err:4.1f} mm)"
                    )
                    append_angle_log(event_msg)
                    print(f"\n >>> LOGGED: {event_msg}\n", flush=True)
            else:
                last_detected_target = None
                target_hold_count = 0

            time.sleep(0.1)
    except KeyboardInterrupt:
        append_angle_log("SESSION STOPPED by user\n")
        print(f"\n[Live monitor stopped. Complete angle log saved to {ANGLE_LOG_FILE}]")


def run_test_report():
    """Step through 0°, 5°, 20°, 45° test, verify accuracy, and log results."""
    offset, factor = load_calibration()
    mm = open_shm()

    targets = [0.0, 5.0, 20.0, 45.0]
    results = []

    append_angle_log("=" * 80)
    append_angle_log("MULTI-ANGLE VERIFICATION TEST (0°, 5°, 20°, 45°) STARTED")
    append_angle_log("=" * 80)

    print("\n" + "=" * 80)
    print("           MURATA SCL3300 INCLINOMETER MULTI-ANGLE VERIFICATION TEST")
    print("=" * 80)
    print("This automated test captures live readings that match the Touchscreen UI")
    print("at the 4 standard verification angles: 0.0°, 5.0°, 20.0°, and 45.0°.\n")
    print(f"Current Calibration: Zero Offset = {offset:+.2f} mm, Scale Factor = {factor:.4f}")
    print(f"Track Standard: Indian Broad Gauge ({GAUGE_MM:.0f} mm)")
    print(f"Logging all angle captures to: {ANGLE_LOG_FILE}\n")

    for target in targets:
        theoretical_cl = GAUGE_MM * math.sin(math.radians(target))
        print("-" * 80)
        print(f"TARGET POSITION: {target:.1f}° (Expected UI Cross-Level: {theoretical_cl:.1f} mm)")
        print(f"  Adjust trolley / inclinometer fixture to {target:.1f}°.")
        print("  (Tip: Look at the live touchscreen UI or press Enter when in position.)")
        input(f"  --> Press [Enter] when holding at {target:.1f}° to capture samples: ")

        print(f"  Recording 30 samples at {target:.1f}°...", end="", flush=True)
        samples_cl = []
        samples_ang = []
        for _ in range(30):
            sample = read_shm_sample(mm)
            cal_cl, angle = calculate_ui_values(sample["raw_cl_mm"], offset, factor)
            samples_cl.append(cal_cl)
            samples_ang.append(angle)
            time.sleep(0.04)
            print(".", end="", flush=True)
        print(" Done!")

        mean_cl = sum(samples_cl) / len(samples_cl)
        mean_ang = sum(samples_ang) / len(samples_ang)
        stdev_ang = math.sqrt(sum((x - mean_ang) ** 2 for x in samples_ang) / len(samples_ang))
        err_ang = abs(mean_ang - target)
        err_cl = abs(mean_cl - theoretical_cl)

        res = {
            "nominal_ang": target,
            "measured_ang": mean_ang,
            "stdev_ang": stdev_ang,
            "err_ang": err_ang,
            "expected_cl": theoretical_cl,
            "measured_cl": mean_cl,
            "err_cl": err_cl,
        }
        results.append(res)

        log_step = (
            f"[STEP {target:4.1f}° COMPLETED] Nominal: {target:4.1f}° | "
            f"Measured Angle: {mean_ang:6.2f}° | UI Cross-Level: {mean_cl:7.1f} mm | "
            f"Angle Error: {err_ang:5.2f}° | Cross-Level Error: {err_cl:5.2f} mm"
        )
        append_angle_log(log_step)
        print(f"  -> {log_step}\n")

    # Display Report Summary
    print("\n" + "=" * 86)
    print("                          INCLINOMETER ACCURACY TEST REPORT")
    print("=" * 86)
    header = (
        f"{'Nominal':>9s} | {'Measured':>10s} | {'StdDev':>8s} | {'Angle Err':>10s} | "
        f"{'Nominal CL':>12s} | {'UI Measured CL':>15s} | {'CL Error':>9s}"
    )
    units = (
        f"{'(deg)':>9s} | {'(deg)':>10s} | {'(deg)':>8s} | {'(deg)':>10s} | "
        f"{'(mm)':>12s} | {'(mm)':>15s} | {'(mm)':>9s}"
    )
    print(header)
    print(units)
    print("-" * 86)
    for r in results:
        line = (
            f"{r['nominal_ang']:8.1f}° | {r['measured_ang']:9.2f}° | {r['stdev_ang']:7.3f}° | {r['err_ang']:9.2f}° | "
            f"{r['expected_cl']:11.1f} mm | {r['measured_cl']:14.1f} mm | {r['err_cl']:8.2f} mm"
        )
        print(line)
    print("=" * 86)

    # Save to file
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write("=" * 86 + "\n")
        f.write("         MURATA SCL3300 INCLINOMETER TEST RESULTS REPORT (UI VERIFIED)\n")
        f.write("=" * 86 + "\n")
        f.write(f"Date & Time:    {timestamp}\n")
        f.write(f"Gauge Standard: Indian Broad Gauge ({GAUGE_MM:.0f} mm)\n")
        f.write(f"Zero Offset:    {offset:+.2f} mm\n")
        f.write(f"Scale Factor:   {factor:.4f}\n\n")
        f.write(header + "\n")
        f.write(units + "\n")
        f.write("-" * 86 + "\n")
        for r in results:
            f.write(
                f"{r['nominal_ang']:8.1f}° | {r['measured_ang']:9.2f}° | {r['stdev_ang']:7.3f}° | {r['err_ang']:9.2f}° | "
                f"{r['expected_cl']:11.1f} mm | {r['measured_cl']:14.1f} mm | {r['err_cl']:8.2f} mm\n"
            )
        f.write("=" * 86 + "\n\n")
        f.write("VERIFICATION CRITERIA:\n")
        f.write("  0.0 Deg:   0.0 mm  (Zero Datum)\n")
        f.write("  5.0 Deg: 146.1 mm  (Superelevation / Cant range)\n")
        f.write(" 20.0 Deg: 573.2 mm  (Mid-range tilt)\n")
        f.write(" 45.0 Deg: 1185.1 mm (Full operational span)\n")

    append_angle_log(f"FINAL REPORT SAVED to {REPORT_FILE}\n")
    print(f"\n[OK] Formal report successfully saved to:\n     {REPORT_FILE}")
    print(f"[OK] Full chronological angle log saved to:\n     {ANGLE_LOG_FILE}\n")


def view_angle_logs():
    """Print the last 50 lines of the angle log."""
    if not ANGLE_LOG_FILE.exists():
        print(f"No log file found at {ANGLE_LOG_FILE} yet.")
        return
    print(f"\n--- [LAST 50 LINES OF {ANGLE_LOG_FILE}] ---")
    with open(ANGLE_LOG_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
        for line in lines[-50:]:
            print(line, end="")
    print("--- [END OF LOG] ---\n")


def main():
    parser = argparse.ArgumentParser(description="Murata SCL3300 Inclinometer Multi-Angle UI Test & Report")
    parser.add_argument("--live", action="store_true", help="Live continuous monitor mode (matches touchscreen UI)")
    parser.add_argument("--report", action="store_true", help="Run 0°, 5°, 20°, 45° verification and save report")
    parser.add_argument("--view-log", action="store_true", help="View the inclinometer angle log")
    args = parser.parse_args()

    if args.view_log:
        view_angle_logs()
    elif args.live:
        run_live_monitor()
    else:
        run_test_report()


if __name__ == "__main__":
    main()
