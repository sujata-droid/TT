#!/usr/bin/env python3
"""Minimal eQEP console test for a rotary encoder on BeagleBone Black."""

import argparse
import math
import os
import sys
import time
from pathlib import Path


EQEP2_PATH = Path("/sys/devices/platform/ocp/48304000.epwmss/48304180.eqep")


def read_text(path: Path) -> str:
    return path.read_text().strip()


def write_text(path: Path, value: str) -> None:
    path.write_text(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--eqep-path", default=str(EQEP2_PATH))
    parser.add_argument("--wheel-diameter-mm", type=float, default=250.0)
    parser.add_argument("--ppr", type=int, default=400)
    parser.add_argument("--sample-hz", type=float, default=10.0)
    parser.add_argument("--duration", type=float, default=0.0, help="0 means run until Ctrl+C")
    args = parser.parse_args()

    eqep = Path(args.eqep_path)
    if os.geteuid() != 0:
        raise SystemExit("Run with sudo.")
    if args.ppr <= 0:
        raise SystemExit("--ppr must be > 0")
    if args.sample_hz <= 0:
        raise SystemExit("--sample-hz must be > 0")
    if not eqep.exists():
        raise SystemExit(f"eQEP path not found: {eqep}")

    enabled = eqep / "enabled"
    position = eqep / "position"
    period = eqep / "period"

    counts_per_rev = args.ppr * 4.0
    mm_per_count = (math.pi * args.wheel_diameter_mm) / counts_per_rev
    sample_period = 1.0 / args.sample_hz

    try:
        write_text(enabled, "0")
    except Exception:
        pass

    try:
        write_text(position, "0")
    except Exception:
        pass
    write_text(enabled, "1")

    prev_count = None
    start = time.time()

    print("eQEP encoder console test")
    print(f"path={eqep}")
    print(f"ppr={args.ppr} counts_per_rev={counts_per_rev:.0f} wheel_diameter_mm={args.wheel_diameter_mm:.2f}")
    print(f"mm_per_count={mm_per_count:.6f}")
    try:
        print(f"eqep_period={read_text(period)}")
    except Exception:
        print("eqep_period=unavailable")
    print("Fields: elapsed_s count delta dir chainage_m")

    try:
        while True:
            count = int(read_text(position))
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
                f"dir={direction:4s}  chainage_m={chainage_m:10.5f}"
            )
            sys.stdout.flush()

            if args.duration > 0.0 and elapsed >= args.duration:
                break
            time.sleep(sample_period)
    finally:
        try:
            write_text(enabled, "0")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
