#!/usr/bin/env python3
"""Read all SCL3300 acceleration axes to identify board mounting direction.

Use this when cross-level is healthy but does not change as expected. Tilt the
board in the physical direction that should affect cross-level and watch which
axis changes most. Then run sensor_service with RAIL_SCL_AXIS=X/Y/Z.
"""

from __future__ import annotations

import argparse
import struct
import time

import spidev


CMD_READ_ACC_X = 0x040000F7
CMD_READ_ACC_Y = 0x080000FD
CMD_READ_ACC_Z = 0x0C0000FB
CMD_READ_STATUS = 0x180000E5
CMD_READ_WHOAMI = 0x40000091
CMD_CHANGE_MODE4 = 0xB4000319
CMD_SW_RESET = 0xB4002098
CMD_DUMMY = 0x000000FF
SENSITIVITY = 12000.0
GAUGE_MM = 1676.0


def word_to_bytes(value: int):
    return [(value >> 24) & 0xFF, (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF]


def xfer(spi, command: int) -> int:
    data = spi.xfer2(word_to_bytes(command))
    return int.from_bytes(bytes(data), "big")


def frame_data(frame: int) -> int:
    raw = (frame >> 8) & 0xFFFF
    return struct.unpack(">h", raw.to_bytes(2, "big"))[0]


def frame_rs(frame: int) -> int:
    return (frame >> 24) & 0x03


def read_response(spi, command: int) -> int:
    xfer(spi, command)
    time.sleep(0.002)
    return xfer(spi, CMD_DUMMY)


def read_axis(spi, command: int) -> float:
    response = read_response(spi, command)
    if frame_rs(response) != 0x01:
        return float("nan")
    acc_g = max(-1.0, min(1.0, frame_data(response) / SENSITIVITY))
    return acc_g * GAUGE_MM


def main() -> int:
    parser = argparse.ArgumentParser(description="SCL3300 all-axis motion test")
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--interval", type=float, default=0.25)
    args = parser.parse_args()

    spi = spidev.SpiDev()
    spi.open(0, 0)
    spi.max_speed_hz = 500000
    spi.mode = 0
    try:
        xfer(spi, CMD_SW_RESET)
        time.sleep(0.1)
        xfer(spi, CMD_CHANGE_MODE4)
        time.sleep(0.1)
        read_response(spi, CMD_READ_STATUS)
        whoami = read_response(spi, CMD_READ_WHOAMI)
        print(f"WHOAMI=0x{frame_data(whoami) & 0xFF:02X}")
        print("elapsed_s x_mm y_mm z_mm")
        start = time.monotonic()
        while time.monotonic() - start < args.seconds:
            x_mm = read_axis(spi, CMD_READ_ACC_X)
            y_mm = read_axis(spi, CMD_READ_ACC_Y)
            z_mm = read_axis(spi, CMD_READ_ACC_Z)
            print(f"{time.monotonic() - start:8.3f} {x_mm:9.2f} {y_mm:9.2f} {z_mm:9.2f}", flush=True)
            time.sleep(args.interval)
    finally:
        spi.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
