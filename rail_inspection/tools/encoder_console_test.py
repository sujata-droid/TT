#!/usr/bin/env python3
"""Console-side PRU encoder monitor for the BeagleBone."""

import argparse
import mmap
import os
import struct
import sys
import time


PRU_DMEM_PHYS = 0x4A300000
PRU_MAP_SIZE = mmap.PAGESIZE
PRU_STRUCT = struct.Struct("<iIII")


def read_block(mem, base_offset):
    mem.seek(base_offset)
    raw = mem.read(PRU_STRUCT.size)
    return PRU_STRUCT.unpack(raw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel-diameter-mm", type=float, default=250.0)
    parser.add_argument("--ppr", type=int, default=600)
    parser.add_argument("--sample-hz", type=float, default=10.0)
    parser.add_argument("--duration", type=float, default=0.0,
                        help="0 means run until Ctrl+C")
    args = parser.parse_args()

    if os.geteuid() != 0:
        raise SystemExit("Run with sudo so /dev/mem can be mapped.")
    if args.ppr <= 0:
        raise SystemExit("--ppr must be > 0")
    if args.sample_hz <= 0:
        raise SystemExit("--sample-hz must be > 0")

    counts_per_rev = args.ppr * 4.0
    mm_per_count = (3.141592653589793 * args.wheel_diameter_mm) / counts_per_rev
    sample_period = 1.0 / args.sample_hz

    with open("/dev/mem", "r+b", buffering=0) as handle:
        base = PRU_DMEM_PHYS & ~(mmap.PAGESIZE - 1)
        offset = PRU_DMEM_PHYS - base
        mem = mmap.mmap(handle.fileno(), PRU_MAP_SIZE, offset=base)

        try:
            prev_count = None
            start = time.time()
            print("PRU encoder console test")
            print(f"ppr={args.ppr} counts_per_rev={counts_per_rev:.0f} wheel_diameter_mm={args.wheel_diameter_mm:.2f}")
            print(f"mm_per_count={mm_per_count:.6f}")
            print("Fields: elapsed_s count delta dir chainage_m pru_status sample_us")
            while True:
                count, status, sample_us, _reserved = read_block(mem, offset)
                now = time.time()
                elapsed = now - start
                if prev_count is None:
                    delta = 0
                else:
                    delta = count - prev_count
                prev_count = count

                if delta > 0:
                    direction = "FWD"
                elif delta < 0:
                    direction = "REV"
                else:
                    direction = "STOP"

                chainage_m = (count * mm_per_count) / 1000.0
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
