#!/usr/bin/env python3
"""Live Track Gauge Console Monitor and Calibration Tool for BeagleBone Black.

Displays the EXACT Track Gauge value shown on the GUI display in real time,
along with raw ADC counts, sensor health, and calibration status.

Also supports direct command-line calibration:
  --cal             Capture 50 ADC samples at MAX reference (237.4 mm)
  --set-raw MAX     Directly set raw ADC count for MAX reference
  --show-cal        Display current calibration file contents
"""

import argparse
import json
import os
import struct
import sys
import time
from pathlib import Path

# Shared memory layout matching sensor_board/src/shared_frame.h:
# struct RailSharedFrame {
#   uint32_t magic;         // [0]
#   uint32_t version;       // [1]
#   uint32_t seq;           // [2]
#   uint32_t update_count;  // [3]
#   int64_t  timestamp_us;  // [4]
#   double   cross_level_mm;// [5]
#   double   twist_mm_per_m;// [6]
#   double   chainage_m;    // [7]
#   double   gauge_mm;      // [8]
#   int32_t  encoder_count; // [9]
#   uint8_t  scl3300_ok;    // [10]
#   uint8_t  encoder_ok;    // [11]
#   uint8_t  service_ok;    // [12]
#   uint8_t  reserved0;     // [13]
# };
SHM_PATH = "/dev/shm/rail_sensor_shm"
ADC_PATH = os.environ.get("RAIL_ADC_PATH", "/sys/bus/iio/devices/iio:device0/in_voltage0_raw")
CAL_FILE = Path(os.environ.get("RAIL_LASER_CAL_FILE", "/home/debian/trolley/laser_cal.json"))
FRAME_FORMAT = "<IIIIqddddiBBBB"
FRAME_SIZE = struct.calcsize(FRAME_FORMAT)

# Exact smoothing settings matching bbb_runtime/backend_bridge.py
GAUGE_AVG_TAPS = 20
GAUGE_DEADBAND_MM = 0.20
DISPLAY_DECIMALS = 2

MAX_MM = 237.4
# Physical 1:1 sensor sensitivity: 160 mm optical range across 4095 ADC counts
DEFAULT_MPC = 160.0 / 4095.0  # 0.03907204 mm/count (25.59375 counts/mm)
DEFAULT_OFFSET_MM = 1523.5    # Track Gauge = Laser reading + 1523.5 mm (249 mm -> 1772.5 mm)


def read_adc_raw() -> int:
    try:
        if os.path.exists(ADC_PATH):
            with open(ADC_PATH, "r", encoding="ascii") as f:
                return int(f.read().strip())
    except Exception:
        pass
    return None


def sample_adc_mean(samples=50, interval_s=0.05):
    print(f"Sampling {samples} readings from {ADC_PATH} ...", flush=True)
    vals = []
    for i in range(samples):
        v = read_adc_raw()
        if v is not None:
            vals.append(v)
            sys.stdout.write(f"\r  Sample {i + 1}/{samples}: raw={v}   ")
            sys.stdout.flush()
        time.sleep(interval_s)
    print()
    if not vals:
        raise RuntimeError("Failed to read ADC")
    mean_val = sum(vals) / len(vals)
    spread = max(vals) - min(vals)
    print(f"  Result: mean={mean_val:.1f}, min={min(vals)}, max={max(vals)}, spread={spread}")
    return mean_val, min(vals), max(vals)


def load_calibration():
    if CAL_FILE.exists():
        try:
            data = json.loads(CAL_FILE.read_text(encoding="utf-8"))
            if "calibration_type" in data or "raw_at_max" in data:
                return data
        except Exception:
            pass
    return {
        "calibration_type": "default_120_to_280",
        "min_mm": 120.0,
        "max_mm": 280.0,
        "span_mm": 160.0,
        "track_gauge_offset_mm": float(os.environ.get("RAIL_LASER_DISPLAY_OFFSET_MM", str(DEFAULT_OFFSET_MM))),
        "raw_at_max": float(os.environ.get("RAIL_LASER_RAW_AT_MAX", "782.0")),
        "cal_max_mm": float(os.environ.get("RAIL_LASER_CAL_MAX_MM", "237.4")),
        "mm_per_count": float(os.environ.get("RAIL_LASER_MPC", str(DEFAULT_MPC))),
    }


def save_calibration(data):
    CAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CAL_FILE.with_name(CAL_FILE.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, CAL_FILE)
    print(f"Saved calibration to {CAL_FILE}")


def compute_laser_default(raw, mm_per_count=DEFAULT_MPC):
    if raw is None:
        return 0.0
    val = 280.0 - float(raw) * float(mm_per_count)
    return max(120.0, min(280.0, val))


def compute_gauge(raw, raw_max=None, max_mm=MAX_MM, mm_per_count=DEFAULT_MPC, mode="default", offset_mm=DEFAULT_OFFSET_MM, **kwargs):
    if raw is None:
        return 0.0
    offset = float(offset_mm) if offset_mm is not None else DEFAULT_OFFSET_MM
    if mode in ("default", "default_120_to_280") or raw_max is None:
        return compute_laser_default(raw, mm_per_count) + offset
    raw_val = float(raw)
    raw_max_val = float(raw_max)
    mpc = float(mm_per_count) if mm_per_count else DEFAULT_MPC
    # 1:1 physical distance change from MAX reference + track gauge offset
    gauge_val = float(max_mm) - (raw_val - raw_max_val) * mpc + offset
    return max(0.0, gauge_val)


compute_two_point_gauge = compute_gauge


def compute_gauge_from_cal(raw, cal):
    if raw is None or cal is None:
        return 0.0
    cal_type = cal.get("calibration_type", "default_120_to_280")
    mpc = cal.get("mm_per_count", DEFAULT_MPC)
    offset_mm = cal.get("track_gauge_offset_mm", cal.get("offset_mm", cal.get("display_offset_mm", DEFAULT_OFFSET_MM)))
    if cal_type in ("default", "default_120_to_280", "laser_default_120_to_280"):
        return compute_laser_default(raw, mpc) + float(offset_mm)
    raw_max = cal.get("raw_at_max", 782.0)
    max_mm = cal.get("cal_max_mm", cal.get("max_mm", MAX_MM))
    return compute_gauge(raw, raw_max=raw_max, max_mm=max_mm, mm_per_count=mpc, mode="single_point", offset_mm=offset_mm)


def do_set_default():
    cal = {
        "cal_factor": 1.0,
        "calibration_type": "default_120_to_280",
        "min_mm": 120.0,
        "max_mm": 280.0,
        "span_mm": 160.0,
        "mm_per_count": DEFAULT_MPC,
        "track_gauge_offset_mm": DEFAULT_OFFSET_MM,
        "cal_reference_point": "max",
        "cal_max_mm": 237.4,
        "raw_at_max": 782.0,
    }
    save_calibration(cal)
    print("Switched calibration mode to DEFAULT (120 to 280 mm + 1523.5 mm offset).")
    print_calibration_summary(cal)


def do_cal_point(point: str = "max"):
    target_mm = MAX_MM
    print(f"=== CALIBRATING SINGLE-POINT MAX REFERENCE ({target_mm} mm) ===")
    print(f"Ensure the laser target is positioned at MAX distance ({target_mm} mm).")
    mean_val, s_min, s_max = sample_adc_mean(50)
    cal = {
        "cal_factor": 1.0,
        "calibration_type": "single_point",
        "cal_reference_point": "max",
        "max_mm": MAX_MM,
        "cal_max_mm": MAX_MM,
        "raw_at_max": round(mean_val, 1),
        "mm_per_count": DEFAULT_MPC,
        "captures": {
            "max": {
                "measured_mean_raw": round(mean_val, 1),
                "sample_count": 50,
                "sample_min_raw": s_min,
                "sample_max_raw": s_max,
            }
        }
    }
    save_calibration(cal)
    print(f"Updated MAX = {mean_val:.1f} ADC raw.")
    print_calibration_summary(cal)


def do_set_raw(raw_max: float):
    cal = {
        "cal_factor": 1.0,
        "calibration_type": "single_point",
        "cal_reference_point": "max",
        "max_mm": MAX_MM,
        "cal_max_mm": MAX_MM,
        "raw_at_max": round(raw_max, 1),
        "mm_per_count": DEFAULT_MPC,
    }
    save_calibration(cal)
    print_calibration_summary(cal)


def print_calibration_summary(cal=None):
    if cal is None:
        cal = load_calibration()
    cal_type = cal.get("calibration_type", "default_120_to_280")
    raw_max = cal.get("raw_at_max", "N/A")
    cal_max = cal.get("cal_max_mm", cal.get("max_mm", MAX_MM))
    mpc = cal.get("mm_per_count", DEFAULT_MPC)
    offset_mm = float(cal.get("track_gauge_offset_mm", cal.get("offset_mm", DEFAULT_OFFSET_MM)))
    print("---------------------------------------------------------------")
    if cal_type in ("default", "default_120_to_280"):
        print("  Mode:             Default Laser Range (120.0 mm to 280.0 mm)")
        print("  Optical Span:     120.0 mm (Near Limit) to 280.0 mm (Far Limit)")
        print(f"  Scale Factor:     {mpc:.8f} mm/count ({1.0/mpc:.2f} counts/mm)")
        print(f"  Track Gauge Offset:+{offset_mm:.1f} mm  (249 mm laser -> {249.0 + offset_mm:.1f} mm gauge)")
        print(f"  Track Gauge Span: {120.0 + offset_mm:.1f} mm to {280.0 + offset_mm:.1f} mm")
    else:
        print("  Calibration Type: Single-Point (MAX = 237.4 mm Reference)")
        print(f"  MAX Reference:    {cal_max:.1f} mm  -->  ADC Raw: {raw_max}")
        print(f"  Scale Factor:     {mpc:.8f} mm/count ({1.0/mpc:.2f} counts/mm)")
        print(f"  Track Gauge Offset:+{offset_mm:.1f} mm")
    print("---------------------------------------------------------------")


def monitor_loop():
    print("=" * 70)
    print("      RAIL TROLLEY - LIVE TRACK GAUGE MONITOR (SINGLE-POINT MAX)")
    print("=" * 70)
    cal = load_calibration()
    print_calibration_summary(cal)
    print("Commands:")
    print("  python3 track_gauge_console.py --cal       (capture MAX 237.4 mm)")
    print("  python3 track_gauge_console.py --sample    (sample live ADC counts)")
    print("Press Ctrl+C to stop.\n")

    has_shm = os.path.exists(SHM_PATH)
    if not has_shm:
        print(f"NOTE: Shared memory {SHM_PATH} not found.")
        print("Displaying direct ADC raw counts and direct calculation.\n")

    samples = []
    last_stable_gauge = 0.0
    raw_max = float(cal.get("raw_at_max", 782.0))
    max_mm = float(cal.get("max_mm", MAX_MM))
    mpc = float(cal.get("mm_per_count", DEFAULT_MPC))
    cal_mtime = None

    try:
        shm_file = open(SHM_PATH, "rb") if has_shm else None
    except Exception:
        shm_file = None

    try:
        while True:
            # Hot reload calibration if file changes
            try:
                if CAL_FILE.exists():
                    mtime = CAL_FILE.stat().st_mtime
                    if mtime != cal_mtime:
                        cal_mtime = mtime
                        cal = load_calibration()
                        raw_max = float(cal.get("raw_at_max", raw_max))
                        max_mm = float(cal.get("max_mm", max_mm))
                        mpc = float(cal.get("mm_per_count", mpc))
            except Exception:
                pass

            adc_raw = read_adc_raw()
            direct_mm = compute_gauge_from_cal(adc_raw, cal) if adc_raw is not None else 0.0

            shm_gauge_str = "  N/A  "
            dist_str = " N/A "
            seq_str = " N/A "
            health_str = ""

            if shm_file is not None:
                try:
                    shm_file.seek(0)
                    raw_bytes = shm_file.read(FRAME_SIZE)
                    if len(raw_bytes) == FRAME_SIZE:
                        fields = struct.unpack(FRAME_FORMAT, raw_bytes)
                        magic = fields[0]
                        version = fields[1]
                        seq = fields[2]
                        chainage_m = fields[7]
                        shm_gauge = fields[8]
                        scl_ok = fields[10]
                        enc_ok = fields[11]

                        if abs(shm_gauge) > 1e-6:
                            samples.append(shm_gauge)
                            if len(samples) > GAUGE_AVG_TAPS:
                                samples.pop(0)
                            avg_gauge = sum(samples) / len(samples)
                            if abs(avg_gauge - last_stable_gauge) >= GAUGE_DEADBAND_MM:
                                last_stable_gauge = avg_gauge
                            display_gauge = round(last_stable_gauge, DISPLAY_DECIMALS)
                        else:
                            display_gauge = 0.0

                        shm_gauge_str = f"{display_gauge:6.2f} mm"
                        dist_str = f"{chainage_m:6.2f} m"
                        seq_str = f"{seq:<6d}"
                        health_str = f"SCL:{'OK' if scl_ok else 'NO'} ENC:{'OK' if enc_ok else 'NO'}"
                except Exception:
                    pass

            adc_str = f"{adc_raw:4d}" if adc_raw is not None else " N/A"

            sys.stdout.write(
                f"\r[DISPLAY] {shm_gauge_str}  |  "
                f"Calc: {direct_mm:6.2f} mm  |  "
                f"ADC: {adc_str}  |  "
                f"Dist: {dist_str}  |  "
                f"Seq: {seq_str}  |  "
                f"{health_str}"
            )
            sys.stdout.flush()
            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n\nMonitor stopped.")
    finally:
        if shm_file is not None:
            try:
                shm_file.close()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="Track Gauge Console Monitor and Calibration")
    parser.add_argument("--default", action="store_true", help="Set mode to default laser range (120 to 280 mm)")
    parser.add_argument("--cal", action="store_true", help=f"Capture 50 ADC samples at MAX distance ({MAX_MM} mm)")
    parser.add_argument("--cal-max", action="store_true", help=f"Capture 50 ADC samples at MAX distance ({MAX_MM} mm)")
    parser.add_argument("--set-raw", type=float, metavar="RAW_MAX",
                        help="Directly set raw ADC count for MAX (237.4 mm)")
    parser.add_argument("--show-cal", action="store_true", help="Display current calibration")
    parser.add_argument("--once", action="store_true", help="Read ADC once and print gauge mm, then exit")
    parser.add_argument("--sample", type=int, nargs="?", const=50, default=None, help="Sample ADC N times (default 50) and show stats")
    parser.add_argument("--watch-raw", action="store_true", help="Continuously print raw ADC counts and voltages")
    parser.add_argument("--test-raw", type=float, metavar="RAW", help="Test gauge calculation for a specific ADC raw value")
    args = parser.parse_args()

    if args.default:
        do_set_default()
        return

    if args.sample is not None:
        sample_adc_mean(args.sample)
        return

    if args.watch_raw:
        print("Watching live ADC counts (press Ctrl+C to stop)...")
        try:
            while True:
                raw = read_adc_raw()
                cal = load_calibration()
                laser_default = compute_laser_default(raw) if raw is not None else 0.0
                gauge_val = compute_gauge_from_cal(raw, cal) if raw is not None else 0.0
                v = (raw / 4095.0) * 1.8 if raw is not None else 0.0
                sys.stdout.write(f"\r  ADC Raw: {raw:4d}  |  ADC Volts: {v:.3f} V  |  Laser: {laser_default:6.2f} mm  |  Track Gauge: {gauge_val:7.2f} mm   ")
                sys.stdout.flush()
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\nStopped.")
        return

    if args.test_raw is not None:
        cal = load_calibration()
        laser = compute_laser_default(args.test_raw)
        mm = compute_gauge_from_cal(args.test_raw, cal)
        print(f"ADC Raw {args.test_raw:.1f} --> Laser {laser:.2f} mm --> Track Gauge {mm:.2f} mm")
        return
    if args.once:
        adc = read_adc_raw()
        cal = load_calibration()
        laser = compute_laser_default(adc) if adc is not None else 0.0
        mm = compute_gauge_from_cal(adc, cal) if adc is not None else 0.0
        shm_str = ""
        try:
            if os.path.exists(SHM_PATH):
                with open(SHM_PATH, "rb") as f:
                    raw_bytes = f.read(FRAME_SIZE)
                    if len(raw_bytes) == FRAME_SIZE:
                        fields = struct.unpack(FRAME_FORMAT, raw_bytes)
                        if fields[0] == 0x5241494C:
                            shm_str = f"  |  Service SHM Gauge: {fields[8]:.2f} mm (seq={fields[2]})"
        except Exception:
            pass
        print(f"Current ADC Raw: {adc} --> Laser: {laser:.2f} mm --> Track Gauge: {mm:.2f} mm{shm_str}")
        return

    if args.cal or args.cal_max:
        do_cal_point("max")
        return
    if args.set_raw is not None:
        do_set_raw(args.set_raw)
        return
    if args.show_cal:
        print_calibration_summary()
        return

    monitor_loop()


if __name__ == "__main__":
    main()
