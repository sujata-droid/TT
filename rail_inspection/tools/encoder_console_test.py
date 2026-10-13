#!/usr/bin/env python3
"""Console-side PRU encoder monitor for the BeagleBone."""

import argparse
import math
import mmap
import os
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bbb_runtime.backend_bridge import compute_encoder_factor, encoder_distance_m


# PRUSS shared RAM.  PRU0 data RAM starts with the remoteproc resource table,
# so the firmware publishes the encoder control block here instead.
PRU_DMEM_PHYS = 0x4A310000
PRU_MAP_SIZE = mmap.PAGESIZE
PRU_STRUCT = struct.Struct("<iIII")
COUNT_COOKIE = 0xA5A5A5A5


def read_block(mem, base_offset):
    for _ in range(8):
        mem.seek(base_offset)
        raw = mem.read(PRU_STRUCT.size)
        count, status, sample_us, reserved = PRU_STRUCT.unpack(raw)
        if ((count & 0xFFFFFFFF) ^ COUNT_COOKIE) == reserved:
            return count, status, sample_us, reserved
    return count, status, sample_us, reserved


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel-diameter-mm", type=float, default=50.0)
    parser.add_argument("--ppr", type=int, default=400,
                        help="encoder cycles per channel per revolution (400 gives 1600 counts/rev with 4X decoding)")
    parser.add_argument("--sample-hz", type=float, default=10.0)
    parser.add_argument("--max-delta", type=int, default=0,
                        help="ignore count jumps larger than this between displayed samples")
    parser.add_argument("--duration", type=float, default=0.0,
                        help="0 means run until Ctrl+C")
    parser.add_argument("--calibrate-counts", type=int,
                        help="calculate a calibration factor from this raw count delta")
    parser.add_argument("--true-distance-m", type=float,
                        help="measured travel distance; requires --calibrate-counts")
    args = parser.parse_args()

    if args.ppr <= 0:
        raise SystemExit("--ppr must be > 0")
    if args.sample_hz <= 0:
        raise SystemExit("--sample-hz must be > 0")
    if args.max_delta < 0:
        raise SystemExit("--max-delta must be >= 0")
    if args.wheel_diameter_mm <= 0:
        raise SystemExit("--wheel-diameter-mm must be > 0")
    if (args.calibrate_counts is None) != (args.true_distance_m is None):
        raise SystemExit("--calibrate-counts and --true-distance-m must be used together")

    circumference_mm = 3.141592653589793 * args.wheel_diameter_mm
    counts_per_rev = args.ppr * 4.0
    # One quadrature count represents this many millimetres of wheel travel.
    mm_per_count = circumference_mm / counts_per_rev
    if args.calibrate_counts is not None:
        factor = compute_encoder_factor(
            args.calibrate_counts,
            args.true_distance_m,
            mm_per_count,
        )
        print(f"raw_counts={abs(args.calibrate_counts)}")
        print(f"true_distance_m={args.true_distance_m:.3f}")
        print(f"calibration_factor={factor:.6f}")
        return
    if os.geteuid() != 0:
        raise SystemExit("Run with sudo so /dev/mem can be mapped.")
    sample_period = 1.0 / args.sample_hz

    with open("/dev/mem", "r+b", buffering=0) as handle:
        base = PRU_DMEM_PHYS & ~(mmap.PAGESIZE - 1)
        offset = PRU_DMEM_PHYS - base
        mem = mmap.mmap(handle.fileno(), PRU_MAP_SIZE, offset=base)

        try:
            prev_count = None
            zero_count = None
            start = time.time()
            print("PRU encoder console test")
            print(f"ppr_per_channel={args.ppr} decode=4X counts_per_rev={counts_per_rev:.0f} wheel_diameter_mm={args.wheel_diameter_mm:.2f}")
            print(f"mm_per_count={mm_per_count:.6f}")
            print("Fields: elapsed_s count delta dir chainage_m pru_status sample_us")
            while True:
                count, status, sample_us, _reserved = read_block(mem, offset)
                if zero_count is None:
                    zero_count = count
                count -= zero_count
                now = time.time()
                elapsed = now - start
                if prev_count is None:
                    delta = 0
                else:
                    delta = count - prev_count
                    if args.max_delta and math.fabs(delta) > args.max_delta:
                        count = prev_count
                        delta = 0
                prev_count = count

                if delta > 0:
                    direction = "FWD"
                elif delta < 0:
                    direction = "REV"
                else:
                    direction = "STOP"

                chainage_m = encoder_distance_m(count, mm_per_count)
                print(
                    f"{elapsed:8.3f}s  count={count:10d}  delta={delta:6d}  "
                    f"dir={direction:4s}  chainage_m={chainage_m:10.5f}  "
                    f"pru_status={status}  sample_us={sample_us}"
                )
                sys.stdout.flush()

                if args.duration > 0.0 and elapsed >= args.duration:
                    break
                time.sleep(sample_period)
        finally:
            mem.close()


if __name__ == "__main__":
    main()
