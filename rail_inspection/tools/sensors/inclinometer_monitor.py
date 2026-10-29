#!/usr/bin/env python3
"""Monitor and calibrate the SCL3300 cross-level shared-memory readings."""
import argparse
from collections import deque
import json
import math
import mmap
import os
import statistics
import struct
import sys
import time
from datetime import datetime
from pathlib import Path

FRAME_PATH = "/dev/shm/rail_sensor_shm"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CAL_FILE = PROJECT_ROOT / "inclinometer_cal.json"
DEFAULT_CONFIG_FILE = PROJECT_ROOT / "bbb_runtime" / "rail_config.json"
FRAME = struct.Struct("<IIIIqddddiBBBB")
GAUGE_MM = 1676.0
DISPLAY_AVERAGE_SAMPLES = 10


def read_frame(mm):
    values = None
    for _ in range(20):
        first = mm[:FRAME.size]
        if len(first) != FRAME.size:
            raise RuntimeError("Shared-memory frame is incomplete")
        before = FRAME.unpack(first)
        if before[2] & 1:
            time.sleep(0.005)
            continue
        second = mm[:FRAME.size]
        after = FRAME.unpack(second)
        if before[2] == after[2] and not (after[2] & 1):
            values = after
            break
        time.sleep(0.005)
    if values is None:
        raise RuntimeError("Could not read a stable shared-memory frame")
    if values[0] != 0x5241494C or values[1] != 1:
        raise RuntimeError("Shared-memory magic or version mismatch")
    return {
        "update_count": values[3],
        "cross_mm": values[5],
        "scl_ok": bool(values[10]),
        "service_ok": bool(values[12]),
    }


def collect_samples(mm, count, period, max_spread_mm):
    samples = []
    last_update = None
    deadline = time.monotonic() + max(10.0, count * period * 4)
    progress_step = max(1, count // 10)
    while len(samples) < count:
        frame = read_frame(mm)
        if not frame["service_ok"] or not frame["scl_ok"]:
            raise RuntimeError(
                "Inclinometer is unhealthy during capture "
                "(service_ok={}, scl_ok={})".format(
                    int(frame["service_ok"]), int(frame["scl_ok"])
                )
            )
        if frame["update_count"] != last_update:
            if not math.isfinite(frame["cross_mm"]):
                raise RuntimeError("Inclinometer produced a non-finite cross-level")
            samples.append(frame["cross_mm"])
            last_update = frame["update_count"]
            if len(samples) % progress_step == 0 or len(samples) == count:
                print(
                    "  capture progress: {:3d}% ({}/{})".format(
                        int(len(samples) * 100 / count), len(samples), count
                    ),
                    flush=True,
                )
        if time.monotonic() >= deadline:
            raise RuntimeError(
                "Timed out waiting for {} new sensor samples; received {}".format(
                    count, len(samples)
                )
            )
        time.sleep(period)
    spread = max(samples) - min(samples)
    if spread > max_spread_mm:
        raise RuntimeError(
            "Capture rejected: spread {:.3f} mm exceeds {:.3f} mm".format(
                spread, max_spread_mm
            )
        )
    return {
        "mean_mm": statistics.mean(samples),
        "min_mm": min(samples),
        "max_mm": max(samples),
        "spread_mm": spread,
        "stdev_mm": statistics.pstdev(samples),
        "samples": len(samples),
    }

def atomic_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(value, output, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(str(temporary), str(path))


def load_factor(config_path):
    path = Path(config_path)
    if not path.exists():
        return 1.0
    with path.open(encoding="utf-8") as source:
        config = json.load(source)
    factor = float(config.get("incl", {}).get("factor", 1.0))
    if not math.isfinite(factor) or not 0.1 <= factor <= 5.0:
        raise ValueError("Existing inclination factor must be between 0.1 and 5.0")
    return factor


def persist_calibration(calibration, cal_path, config_path):
    calibration = dict(calibration)
    calibration["updated"] = datetime.now().astimezone().isoformat(timespec="seconds")
    atomic_write_json(cal_path, calibration)

    config_path = Path(config_path)
    config = {}
    if config_path.exists():
        with config_path.open(encoding="utf-8") as source:
            config = json.load(source)
    incl = config.setdefault("incl", {})
    incl.update({
        "offset": calibration["zero_offset_mm"],
        "factor": calibration.get("scale_factor", 1.0),
        "calibrated": True,
        "reference_mm": calibration.get("target_ref_mm", 0.0),
        "calibration_method": calibration["method"],
    })
    atomic_write_json(config_path, config)


def angle_degrees(height_mm, gauge_mm):
    ratio = height_mm / gauge_mm
    if not -1.0 <= ratio <= 1.0:
        raise ValueError("Cross-level magnitude cannot exceed the gauge")
    return math.degrees(math.asin(ratio))


def calibration_from_static(stats, target_mm, factor, gauge_mm=GAUGE_MM):
    offset = stats["mean_mm"] - target_mm / factor
    return {
        "zero_offset_mm": offset,
        "zero_offset_deg": angle_degrees(offset, gauge_mm),
        "scale_factor": factor,
        "gauge_mm": gauge_mm,
        "method": "static_reference",
        "samples": stats["samples"],
        "spread_mm": stats["spread_mm"],
        "target_ref_mm": target_mm,
        "reference_mean_mm": stats["mean_mm"],
    }


def load_calibration(path):
    with Path(path).open(encoding="utf-8") as source:
        calibration = json.load(source)
    if not isinstance(calibration, dict) or "zero_offset_mm" not in calibration:
        raise ValueError("Calibration file must contain zero_offset_mm")
    try:
        offset = float(calibration["zero_offset_mm"])
        factor = float(calibration.get("scale_factor", 1.0))
        gauge = float(calibration.get("gauge_mm", GAUGE_MM))
    except (TypeError, ValueError) as error:
        raise ValueError("Calibration offset, scale factor, and gauge must be numeric") from error
    if not math.isfinite(offset) or not math.isfinite(factor) or not math.isfinite(gauge):
        raise ValueError("Calibration offset, scale factor, and gauge must be finite")
    if not 0.1 <= factor <= 5.0 or gauge <= 0:
        raise ValueError("Calibration scale factor or gauge is out of range")
    return offset, factor, gauge


def calibration_from_reversal(pos_a, pos_b, factor, gauge_mm=GAUGE_MM):
    offset = (pos_a["mean_mm"] + pos_b["mean_mm"]) / 2.0
    raw_cant = (pos_a["mean_mm"] - pos_b["mean_mm"]) / 2.0
    cant = raw_cant * factor
    return {
        "zero_offset_mm": offset,
        "zero_offset_deg": angle_degrees(offset, gauge_mm),
        "scale_factor": factor,
        "gauge_mm": gauge_mm,
        "method": "reversal_180",
        "samples": min(pos_a["samples"], pos_b["samples"]),
        "pos_a_mean_mm": pos_a["mean_mm"],
        "pos_b_mean_mm": pos_b["mean_mm"],
        "pos_a_spread_mm": pos_a["spread_mm"],
        "pos_b_spread_mm": pos_b["spread_mm"],
        "track_cant_mm": cant,
        "track_cant_raw_mm": raw_cant,
        "track_cant_deg": angle_degrees(cant, gauge_mm),
    }


def print_capture(label, stats):
    print(
        "{}: mean={:.3f} mm  spread={:.3f} mm  "
        "min={:.3f} mm  max={:.3f} mm  n={}".format(
            label, stats["mean_mm"], stats["spread_mm"],
            stats["min_mm"], stats["max_mm"], stats["samples"]
        )
    )


def run_monitor(mm, args):
    offset = 0.0
    factor = 1.0
    gauge = args.gauge_mm or GAUGE_MM
    if not args.no_cal and args.manual_offset_mm is None:
        path = Path(args.cal_file)
        if path.exists():
            offset, factor, stored_gauge = load_calibration(path)
            if args.gauge_mm is None:
                gauge = stored_gauge
    if args.manual_offset_mm is not None:
        offset = args.manual_offset_mm
    print(
        "elapsed_s    raw_mm avg_cal_mm  angle_deg scl_ok srv_ok  "
        "update_count  notes"
    )
    start = time.monotonic()
    last_update = None
    last_update_time = start
    calibrated_samples = deque(maxlen=DISPLAY_AVERAGE_SAMPLES)
    try:
        while True:
            frame = read_frame(mm)
            now = time.monotonic()
            raw = frame["cross_mm"]
            calculated = (raw - offset) * factor
            if frame["update_count"] != last_update:
                last_update = frame["update_count"]
                last_update_time = now
                # Add only fresh sensor values so repeated monitor reads do
                # not give extra weight to the same shared-memory frame.
                calibrated_samples.append(calculated)
            calibrated = statistics.mean(calibrated_samples)
            angle = angle_degrees(calibrated, gauge)
            stale_s = now - last_update_time
            notes = (
                "FRAME_STALE_{:.1f}s".format(stale_s)
                if stale_s >= args.warn_freeze else ""
            )
            print(
                "{:9.3f} {:9.3f} {:9.3f} {:10.4f} {:6d} {:6d} {:13d}  {}".format(
                    now - start, raw, calibrated, angle,
                    int(frame["scl_ok"]), int(frame["service_ok"]),
                    frame["update_count"], notes,
                ),
                flush=True,
            )
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--calibrate", action="store_true",
                      help="capture a static known reference")
    mode.add_argument("--calibrate-reversal", action="store_true",
                      help="capture a same-spot 180-degree trolley reversal")
    parser.add_argument("--cal-file", default=str(DEFAULT_CAL_FILE))
    parser.add_argument("--config-file", default=str(DEFAULT_CONFIG_FILE))
    parser.add_argument("--shm-path", default=FRAME_PATH)
    parser.add_argument("--samples", "--cal-samples", dest="samples",
                        type=int, default=100)
    parser.add_argument("--rate", type=float, default=20.0,
                        help="capture sample rate in Hz (default: 20)")
    parser.add_argument("--cal-period", type=float,
                        help="capture interval in seconds (overrides --rate)")
    parser.add_argument("--max-spread-mm", type=float, default=1.0)
    parser.add_argument("--target-ref-mm", type=float, default=0.0)
    parser.add_argument("--gauge-mm", type=float)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--warn-freeze", type=float, default=2.0,
                        help="seconds without new sensor frames before a monitor warning")
    parser.add_argument("--manual-offset-mm", type=float)
    parser.add_argument("--no-cal", action="store_true")
    args = parser.parse_args()
    if args.samples < 2:
        parser.error("--samples must be at least 2")
    for name in ("rate", "max_spread_mm", "interval", "warn_freeze"):
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            parser.error("--{} must be finite and greater than zero".format(
                name.replace("_", "-")
            ))
    if args.cal_period is not None and (
            not math.isfinite(args.cal_period) or args.cal_period <= 0):
        parser.error("--cal-period must be finite and greater than zero")
    args.sample_period = (
        args.cal_period if args.cal_period is not None else 1.0 / args.rate
    )
    if args.gauge_mm is not None and (
            not math.isfinite(args.gauge_mm) or args.gauge_mm <= 0):
        parser.error("--gauge-mm must be finite and greater than zero")
    if not math.isfinite(args.target_ref_mm) or abs(args.target_ref_mm) > 200:
        parser.error("--target-ref-mm must be between -200 and 200 mm")
    if (args.manual_offset_mm is not None and
            not math.isfinite(args.manual_offset_mm)):
        parser.error("--manual-offset-mm must be finite")
    if args.manual_offset_mm is not None and args.no_cal:
        parser.error("--manual-offset-mm and --no-cal cannot be combined")
    return args


def main():
    args = parse_args()
    try:
        fd = os.open(args.shm_path, os.O_RDONLY | os.O_SYNC)
        with mmap.mmap(fd, FRAME.size, mmap.MAP_SHARED, mmap.PROT_READ) as mm:
            if args.calibrate:
                factor = load_factor(args.config_file)
                print(
                    "Static calibration: keep the trolley stationary on a "
                    "verified reference fixture."
                )
                input("Press Enter when ready to capture: ")
                print("Capturing {} samples at {:.1f} Hz.".format(
                    args.samples, 1.0 / args.sample_period
                ))
                gauge = args.gauge_mm or GAUGE_MM
                stats = collect_samples(
                    mm, args.samples, args.sample_period, args.max_spread_mm
                )
                print_capture("static reference", stats)
                calibration = calibration_from_static(
                    stats, args.target_ref_mm, factor, gauge
                )
                calibration["target_ref_mm"] = args.target_ref_mm
                persist_calibration(
                    calibration, args.cal_file, args.config_file
                )
                print("Saved calibration to {}".format(args.cal_file))
                print("Updated UI configuration at {}".format(args.config_file))
                print(
                    "offset={:.3f} mm; calibrated reference={:.3f} mm".format(
                        calibration["zero_offset_mm"],
                        (stats["mean_mm"] - calibration["zero_offset_mm"]) *
                        calibration["scale_factor"]
                    )
                )
                return 0
            if args.calibrate_reversal:
                factor = load_factor(args.config_file)
                gauge = args.gauge_mm or GAUGE_MM
                print(
                    "On a secured test fixture, place the stationary trolley "
                    "at the marked position and capture Position A."
                )
                input("Press Enter when Position A is stationary: ")
                pos_a = collect_samples(
                    mm, args.samples, args.sample_period, args.max_spread_mm
                )
                print_capture("Position A", pos_a)
                input(
                    "Rotate the trolley 180 degrees at the same marked "
                    "position, keep wheel contacts on the same reference "
                    "points, then press Enter: "
                )
                pos_b = collect_samples(
                    mm, args.samples, args.sample_period, args.max_spread_mm
                )
                print_capture("Position B", pos_b)
                calibration = calibration_from_reversal(
                    pos_a, pos_b, factor, gauge
                )
                persist_calibration(
                    calibration, args.cal_file, args.config_file
                )
                print("Saved calibration to {}".format(args.cal_file))
                print("Updated UI configuration at {}".format(args.config_file))
                print(
                    "offset={:.3f} mm; estimated track cant={:.3f} mm".format(
                        calibration["zero_offset_mm"],
                        calibration["track_cant_mm"]
                    )
                )
                return 0
            return run_monitor(mm, args)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as error:
        print("inclinometer_monitor: {}".format(error), file=sys.stderr)
        return 1
    finally:
        if "fd" in locals():
            os.close(fd)


if __name__ == "__main__":
    raise SystemExit(main())
