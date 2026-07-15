#!/usr/bin/env python3
"""Console test for HG-C1200 laser gauge through BBB ADC.

This reads the BBB ADC sysfs value and applies the same laser gauge formula
used by sensor_service when RAIL_GAUGE_SOURCE=laser_adc.
"""

import argparse
import time
from collections import deque

ADC_PATH = "/sys/bus/iio/devices/iio:device0/in_voltage0_raw"
NOMINAL_GAUGE_MM = 1676.0


def read_raw(path: str) -> int:
    with open(path, "r", encoding="ascii") as f:
        return int(f.read().strip())


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def main() -> None:
    parser = argparse.ArgumentParser(description="HG-C1200 laser gauge ADC test")
    parser.add_argument("--adc-path", default=ADC_PATH)
    parser.add_argument("--adc-max-raw", type=float, default=3072.0)
    parser.add_argument("--laser-min-mm", type=float, default=160.0)
    parser.add_argument("--laser-max-mm", type=float, default=0.0)
    parser.add_argument("--laser-zero-mm", type=float, default=80.0)
    parser.add_argument("--laser-zero-raw", type=float, default=-1.0, help="ADC count that represents nominal gauge; -1 auto-zeroes from first sample")
    parser.add_argument("--laser-mpc", type=float, default=(0.0 - 160.0) / 3072.0, help="Laser mm per ADC count in offset mode")
    parser.add_argument("--nominal-gauge-mm", type=float, default=NOMINAL_GAUGE_MM)
    parser.add_argument("--output-mode", choices=("deviation", "absolute"), default="deviation", help="deviation prints 0 at the auto-zero/reference point")
    parser.add_argument("--gauge-min-mm", type=float, default=NOMINAL_GAUGE_MM - 25.0)
    parser.add_argument("--gauge-max-mm", type=float, default=NOMINAL_GAUGE_MM + 50.0)
    parser.add_argument("--factor", type=float, default=1.0)
    parser.add_argument("--sign", type=int, choices=(-1, 1), default=-1)
    parser.add_argument("--sample-period", type=float, default=0.2)
    parser.add_argument("--samples", type=int, default=0, help="Number of samples to print; 0 means run forever")
    parser.add_argument("--warn-jump-raw", type=int, default=200, help="Warn when consecutive ADC samples jump more than this many raw counts")
    args = parser.parse_args()

    print("HG-C1200 laser gauge ADC test", flush=True)
    print(f"adc_path={args.adc_path}", flush=True)
    print(
        f"laser_range=[{args.laser_min_mm:.1f}, {args.laser_max_mm:.1f}] "
        f"zero={args.laser_zero_mm:.1f} sign={args.sign} factor={args.factor:.3f}",
        flush=True,
    )
    print("Rule: reference laser value -> UI 0.00; lower laser value -> positive UI deviation", flush=True)
    print("Fields: raw ratio laser_mm gauge_deviation_mm ui_gauge_mm", flush=True)

    zero_raw = args.laser_zero_raw
    sample_count = 0
    last_raw = None
    raw_window = deque(maxlen=10)
    while True:
        raw = read_raw(args.adc_path)
        if args.laser_zero_raw < 0.0 and zero_raw < 15.0:
            if raw >= 15:
                zero_raw = float(raw)
                print(f"auto_zero_raw={zero_raw:.0f} -> nominal_gauge={args.nominal_gauge_mm:.1f} mm", flush=True)
        raw_window.append(raw)
        jump_note = ""
        if last_raw is not None and abs(raw - last_raw) > args.warn_jump_raw:
            jump_note = f"  WARN_ADC_JUMP={raw - last_raw:+d}"
        last_raw = raw
        ratio = clamp(raw / args.adc_max_raw, 0.0, 1.0)
        laser_mm = args.laser_min_mm + ratio * (args.laser_max_mm - args.laser_min_mm)
        laser_mm = clamp(laser_mm, 0.0, 160.0)
        
        has_ref = (args.laser_zero_raw >= 0.0) or (zero_raw >= 15.0)
        if not has_ref or raw < 15 or raw > 3060:
            gauge_deviation = 0.0
            display_mm = 0.0
        else:
            ref_laser = args.laser_min_mm + (zero_raw / args.adc_max_raw) * (args.laser_max_mm - args.laser_min_mm)
            gauge_deviation = (ref_laser - laser_mm) * args.factor
            if args.output_mode == "absolute":
                display_mm = laser_mm * args.factor
            else:
                display_mm = gauge_deviation
        print(f"raw={raw:5d} ratio={ratio:0.4f} laser_mm={laser_mm:8.2f} gauge_deviation_mm={gauge_deviation:8.2f} ui_gauge_mm={display_mm:8.2f}{jump_note}", flush=True)
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