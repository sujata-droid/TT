#!/usr/bin/env python3
"""Console test for HG-C1200 laser gauge through BBB ADC.

This reads the BBB ADC sysfs value and applies the same laser gauge formula
used by sensor_service when RAIL_GAUGE_SOURCE=laser_adc.

Calibration (resistor-tolerance compensation)
---------------------------------------------
The analog output of the laser goes through a resistor divider into the BBB
ADC. Resistor tolerance changes the divider gain, so the same laser position
gives a different raw count on every board. A single gain factor fixes this:

    raw_cal = raw * cal_factor

All the downstream maths (ratio, laser_mm, ui_gauge_mm) uses raw_cal, so the
result stays the same even when the divider voltage drifts by 5-10 %.

    cal_factor = expected_raw / measured_raw
               = expected_voltage / measured_voltage      (same thing, ADC is linear)

The factor is stored in a small JSON file (--cal-file). The UI / sensor_service
only has to call save_cal_factor() (or write the same JSON); this script
re-reads the file whenever it changes, so no restart is needed.
"""

import argparse
import json
import os
import time
from collections import deque

ADC_PATH = "/sys/bus/iio/devices/iio:device0/in_voltage0_raw"
NOMINAL_GAUGE_MM = 1676.0

# --- BBB ADC constants -------------------------------------------------------
ADC_REF_V = 1.8                  # BBB ADC full-scale input voltage
ADC_MAX_RAW = 4095.0             # 12-bit
# Highest voltage the divider is allowed to deliver (design limit) -> raw count
DIVIDER_MAX_V = 1.35
RAW_AT_1V35 = round(DIVIDER_MAX_V / ADC_REF_V * ADC_MAX_RAW)   # 3071 (3071.25)

# --- Tolerance ---------------------------------------------------------------
# Divider voltage tolerance (resistor tolerance). 15 % -> the calibration factor
# must lie between 1/(1+0.15)=0.870 and 1/(1-0.15)=1.176. Anything outside
# means a wiring/resistor fault, not tolerance, so it is rejected.
RESISTOR_TOL_PCT = 15.0
# Ideal (exact) raw counts of the Panasonic window, defined at DIVIDER_MAX_V = 1.35 V:
#   +80 -> 1.35 V -> 3071.25 counts,  -80 -> 0 V -> 0 counts,  0 -> mid-point.
RAW_PLUS80 = DIVIDER_MAX_V / ADC_REF_V * ADC_MAX_RAW      # 3071.25
RAW_MINUS80 = 0.0
RAW_ZERO = (RAW_PLUS80 + RAW_MINUS80) / 2.0               # 1535.6 (assumed symmetric)
# Small ADC-noise allowance at the two window endpoints; larger excursions read 0 mm.
ENDPOINT_TOLERANCE_PCT = 1.0
RAW_END_TOL = RAW_PLUS80 * ENDPOINT_TOLERANCE_PCT / 100.0

DEFAULT_CAL_FILE = "laser_cal.json"


# ------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------
def read_raw(path: str) -> int:
    with open(path, "r", encoding="ascii") as f:
        return int(f.read().strip())


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def factor_limits(tol_pct: float):
    t = tol_pct / 100.0
    return 1.0 / (1.0 + t), 1.0 / (1.0 - t)


def factor_is_valid(factor: float, tol_pct: float) -> bool:
    lo, hi = factor_limits(tol_pct)
    return lo <= factor <= hi


def load_cal_factor(path: str):
    """Return the stored factor, or None if the file is missing/invalid."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return float(json.load(f)["cal_factor"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_cal_factor(path: str, factor: float, **meta) -> None:
    """Atomically store the factor. Call this from the UI backend."""
    data = {"cal_factor": factor, "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
    data.update(meta)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def collect_mean_raw(path: str, n: int, period: float):
    vals = []
    for _ in range(n):
        vals.append(read_raw(path))
        time.sleep(period)
    return sum(vals) / len(vals), max(vals) - min(vals)


def run_calibration(args) -> None:
    targets = {"plus80": args.raw_at_plus80, "zero": args.raw_at_zero}
    expected_raw = targets[args.cal_point]
    expected_v = expected_raw * ADC_REF_V / ADC_MAX_RAW

    if args.cal_voltage > 0.0:
        measured_v = args.cal_voltage
        measured_raw = measured_v / ADC_REF_V * ADC_MAX_RAW
        spread = 0
        print(f"Using multimeter voltage {measured_v:.4f} V at the BBB AIN pin")
    else:
        print(f"Set the laser to the Panasonic {'+80' if args.cal_point == 'plus80' else '0'} "
              f"point, then sampling {args.cal_samples} readings ...", flush=True)
        measured_raw, spread = collect_mean_raw(args.adc_path, args.cal_samples, args.sample_period)
        measured_v = measured_raw * ADC_REF_V / ADC_MAX_RAW

    if measured_raw <= 0.0:
        print("ERROR: measured value is zero, cannot calibrate.")
        return
    if spread > args.warn_jump_raw:
        print(f"ERROR: ADC unstable (spread={spread}). Fix wiring/noise before calibrating.")
        return

    factor = expected_raw / measured_raw
    print(f"expected: raw={expected_raw:.1f}  ({expected_v:.4f} V)")
    print(f"measured: raw={measured_raw:.1f}  ({measured_v:.4f} V)")
    print(f"cal_factor = {expected_raw:.1f} / {measured_raw:.1f} = {factor:.5f}")

    if not factor_is_valid(factor, args.tol_pct):
        lo, hi = factor_limits(args.tol_pct)
        print(f"ERROR: factor outside +/-{args.tol_pct:.0f}% tolerance window "
              f"[{lo:.4f}, {hi:.4f}]. Not saved - check resistors/wiring.")
        return

    save_cal_factor(args.cal_file, factor, point=args.cal_point,
                    measured_raw=round(measured_raw, 2), expected_raw=expected_raw)
    print(f"saved to {args.cal_file}")


# ------------------------------------------------------------------------------
# main
# ------------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="HG-C1200 laser gauge ADC test")
    parser.add_argument("--adc-path", default=ADC_PATH)
    parser.add_argument("--adc-max-raw", type=float, default=ADC_MAX_RAW)
    parser.add_argument("--laser-min-mm", type=float, default=160.0)
    parser.add_argument("--laser-max-mm", type=float, default=0.0)
    parser.add_argument("--laser-zero-mm", type=float, default=80.0)
    parser.add_argument("--laser-zero-raw", type=float, default=-1.0, help="ADC count that represents nominal gauge; -1 auto-zeroes from first sample")
    parser.add_argument("--laser-mpc", type=float, default=(0.0 - 160.0) / 3072.0, help="Laser mm per ADC count in offset mode")
    parser.add_argument("--nominal-gauge-mm", type=float, default=NOMINAL_GAUGE_MM)
    parser.add_argument("--output-mode", choices=("laser_window", "deviation", "absolute"), default="laser_window",
                        help="laser_window maps Panasonic +80/0/-80 to physical 120/200/280 mm")
    parser.add_argument("--display-min-mm", type=float, default=120.0,
                        help="physical distance corresponding to Panasonic +80")
    parser.add_argument("--display-max-mm", type=float, default=280.0,
                        help="physical distance corresponding to Panasonic -80")
    # Reference raw values measured ONCE with nominal (reference) resistors.
    parser.add_argument("--raw-at-plus80", type=float, default=RAW_PLUS80,
                        help="ideal ADC raw at Panasonic +80 (1.35 V -> 3071.25)")
    parser.add_argument("--raw-at-zero", type=float, default=RAW_ZERO,
                        help="ideal ADC raw at Panasonic 0 (mid-point, 0.675 V -> 1535.6); override if your measured zero differs")
    parser.add_argument("--raw-at-minus80", type=float, default=RAW_MINUS80,
                        help="ideal ADC raw at Panasonic -80 (0 V -> 0)")
    parser.add_argument("--raw-end-tol", type=float, default=RAW_END_TOL,
                        help="Endpoint noise tolerance in corrected raw counts (default 1%% of +80 reference)")
    parser.add_argument("--gauge-min-mm", type=float, default=NOMINAL_GAUGE_MM - 25.0)
    parser.add_argument("--gauge-max-mm", type=float, default=NOMINAL_GAUGE_MM + 50.0)
    parser.add_argument("--factor", type=float, default=1.0)
    parser.add_argument("--sign", type=int, choices=(-1, 1), default=-1)
    parser.add_argument("--sample-period", type=float, default=0.2)
    parser.add_argument("--samples", type=int, default=0, help="Number of samples to print; 0 means run forever")
    parser.add_argument("--warn-jump-raw", type=int, default=200, help="Warn when consecutive ADC samples jump more than this many raw counts")

    # calibration options
    parser.add_argument("--cal-file", default=DEFAULT_CAL_FILE, help="JSON file holding cal_factor (written by UI or --calibrate)")
    parser.add_argument("--cal-factor", type=float, default=0.0,
                        help="Override the calibration factor directly (0 = use --cal-file, else 1.0)")
    parser.add_argument("--tol-pct", type=float, default=RESISTOR_TOL_PCT, help="Divider voltage tolerance in percent")
    parser.add_argument("--calibrate", action="store_true", help="Compute and save cal_factor, then exit")
    parser.add_argument("--cal-point", choices=("plus80", "zero"), default="plus80",
                        help="Known laser position used for calibration")
    parser.add_argument("--cal-samples", type=int, default=50)
    parser.add_argument("--cal-voltage", type=float, default=0.0,
                        help="Calibrate from a multimeter reading (V) at the ADC pin instead of sampling")
    args = parser.parse_args()

    if args.calibrate:
        run_calibration(args)
        return

    print("HG-C1200 laser gauge ADC test", flush=True)
    print(f"adc_path={args.adc_path}", flush=True)
    print(
        f"laser_range=[{args.laser_min_mm:.1f}, {args.laser_max_mm:.1f}] "
        f"zero={args.laser_zero_mm:.1f} sign={args.sign} factor={args.factor:.3f}",
        flush=True,
    )
    print(f"Rule: raw={args.raw_at_plus80:.1f} (+80) -> {args.display_min_mm:.0f} mm, "
          f"raw={args.raw_at_zero:.1f} (0) -> {(args.display_min_mm + args.display_max_mm) / 2.0:.0f} mm, "
          f"raw={args.raw_at_minus80:.1f} (-80) -> {args.display_max_mm:.0f} mm", flush=True)
    lo_f, hi_f = factor_limits(args.tol_pct)
    print(f"Ref: {DIVIDER_MAX_V:.2f} V -> raw {RAW_AT_1V35}; tolerance +/-{args.tol_pct:.0f}% "
          f"-> cal_factor window [{lo_f:.4f}, {hi_f:.4f}]", flush=True)
    print("Fields: raw cal_raw ratio laser_span_mm gauge_deviation_mm track_gauge_mm", flush=True)

    cal_factor = 1.0
    cal_mtime = None

    zero_raw = args.laser_zero_raw
    sample_count = 0
    last_raw = None
    raw_window = deque(maxlen=10)
    while True:
        # ---- (re)load calibration factor (picks up UI changes live) ----
        if args.cal_factor > 0.0:
            new_factor = args.cal_factor
        else:
            new_factor = None
            try:
                mt = os.path.getmtime(args.cal_file)
                if mt != cal_mtime:
                    cal_mtime = mt
                    new_factor = load_cal_factor(args.cal_file)
                    if new_factor is None:
                        print(f"WARN: {args.cal_file} unreadable, keeping factor {cal_factor:.5f}", flush=True)
            except OSError:
                pass
        if new_factor is not None and new_factor != cal_factor:
            if factor_is_valid(new_factor, args.tol_pct):
                cal_factor = new_factor
                print(f"cal_factor={cal_factor:.5f}", flush=True)
            else:
                print(f"WARN: cal_factor {new_factor:.5f} outside window [{lo_f:.4f}, {hi_f:.4f}] - ignored", flush=True)

        raw_hw = read_raw(args.adc_path)           # raw count straight from the ADC
        raw = raw_hw * cal_factor                  # tolerance-corrected count (float)

        if args.laser_zero_raw < 0.0 and zero_raw < 15.0:
            if raw >= 15:
                zero_raw = float(raw)
                print(f"auto_zero_raw={zero_raw:.0f} -> nominal_gauge={args.nominal_gauge_mm:.1f} mm", flush=True)
        raw_window.append(raw_hw)
        jump_note = ""
        if last_raw is not None and abs(raw_hw - last_raw) > args.warn_jump_raw:
            jump_note = f"  WARN_ADC_JUMP={raw_hw - last_raw:+d}"
        last_raw = raw_hw
        ratio = clamp(raw / args.adc_max_raw, 0.0, 1.0)
        laser_mm = args.laser_min_mm + ratio * (args.laser_max_mm - args.laser_min_mm)
        laser_mm = clamp(laser_mm, 0.0, 160.0)

        has_ref = args.output_mode == "laser_window" or (args.laser_zero_raw >= 0.0) or (zero_raw >= 15.0)
        if not has_ref or (args.output_mode != "laser_window" and (raw < 15 or raw > RAW_AT_1V35)):
            gauge_deviation = 0.0
            display_mm = 0.0
        else:
            ref_laser = args.laser_min_mm + (zero_raw / args.adc_max_raw) * (args.laser_max_mm - args.laser_min_mm)
            gauge_deviation = (ref_laser - laser_mm) * args.factor
            if args.output_mode == "laser_window":
                span_plus_to_zero = args.raw_at_plus80 - args.raw_at_zero
                span_zero_to_minus = args.raw_at_zero - args.raw_at_minus80
                display_center_mm = (args.display_min_mm + args.display_max_mm) / 2.0
                tolerance = args.raw_end_tol
                if span_plus_to_zero <= 0.0 or span_zero_to_minus <= 0.0 or \
                        raw < args.raw_at_minus80 - tolerance or \
                        raw > args.raw_at_plus80 + tolerance:
                    display_mm = 0.0
                else:
                    mapped_raw = clamp(raw, args.raw_at_minus80, args.raw_at_plus80)
                    if mapped_raw >= args.raw_at_zero:
                        display_mm = display_center_mm + (args.raw_at_zero - mapped_raw) * \
                            (display_center_mm - args.display_min_mm) / span_plus_to_zero
                    else:
                        display_mm = display_center_mm + (args.raw_at_zero - mapped_raw) * \
                            (args.display_max_mm - display_center_mm) / span_zero_to_minus
            elif args.output_mode == "absolute":
                display_mm = laser_mm * args.factor
            else:
                display_mm = gauge_deviation
        print(f"raw={raw_hw:5d} cal_raw={raw:7.1f} ratio={ratio:0.4f} laser_mm={laser_mm:8.2f} "
              f"gauge_deviation_mm={gauge_deviation:8.2f} ui_gauge_mm={display_mm:8.2f}{jump_note}", flush=True)
        sample_count += 1
        if args.samples and sample_count >= args.samples:
            if raw_window:
                spread = max(raw_window) - min(raw_window)
                print(f"summary_last_{len(raw_window)}_samples: raw_min={min(raw_window)} raw_max={max(raw_window)} raw_spread={spread}", flush=True)
                if spread > args.warn_jump_raw:
                    print("diagnosis: ADC is unstable/noisy. Check laser analog output type, common ground, voltage divider, and BBB AIN wiring before tuning calibration.", flush=True)
            break
        time.sleep(args.sample_period)


if __name__ == "__main__":
    main()