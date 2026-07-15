#!/usr/bin/env python3
"""Continuously monitor SCL3300 cross-level from the production shared frame.

Run this on the BeagleBone while ``sensor_service`` is running. It reads the
same shared-memory frame consumed by the UI, so it is the best quick check for
whether a frozen value is coming from the backend or only from the UI.
"""

from __future__ import annotations

import argparse
import mmap
import struct
import time


SHM_PATH = "/dev/shm/rail_sensor_shm"
FRAME = struct.Struct("<IIIIqddddiBBBB")
MAGIC = 0x5241494C


def read_frame(mm: mmap.mmap):
    mm.seek(0)
    first = FRAME.unpack(mm.read(FRAME.size))
    seq = first[2]
    if seq & 1:
        return None
    mm.seek(0)
    second = FRAME.unpack(mm.read(FRAME.size))
    if second[2] != seq or (second[2] & 1):
        return None
    if second[0] != MAGIC:
        raise RuntimeError("shared-memory frame magic mismatch")
    return second


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor live inclinometer cross-level")
    parser.add_argument("--interval", type=float, default=0.2, help="print interval in seconds")
    parser.add_argument("--warn-freeze", type=float, default=2.0, help="seconds before warning that value is unchanged")
    args = parser.parse_args()

    last_cross = None
    last_change = time.monotonic()

    with open(SHM_PATH, "rb") as handle:
        mm = mmap.mmap(handle.fileno(), FRAME.size, access=mmap.ACCESS_READ)
        print("elapsed_s cross_mm scl_ok service_ok update_count note")
        start = time.monotonic()
        while True:
            frame = read_frame(mm)
            if frame is None:
                time.sleep(min(args.interval, 0.02))
                continue

            update_count = frame[3]
            cross_mm = frame[5]
            scl_ok = frame[10]
            service_ok = frame[12]

            now = time.monotonic()
            if last_cross is None or abs(cross_mm - last_cross) >= 0.05:
                last_change = now
                last_cross = cross_mm

            unchanged_s = now - last_change
            note = ""
            if not service_ok:
                note = "SERVICE_NOT_OK"
            elif not scl_ok:
                note = "SCL_NOT_OK"
            elif unchanged_s >= args.warn_freeze:
                note = f"UNCHANGED_{unchanged_s:.1f}s"

            print(
                f"{now - start:8.3f} {cross_mm:9.3f} {scl_ok:d} {service_ok:d} "
                f"{update_count:10d} {note}",
                flush=True,
            )
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
