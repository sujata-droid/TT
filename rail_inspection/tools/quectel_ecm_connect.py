#!/usr/bin/env python3
"""Bring up a Quectel EC200U CDC-ECM data call without third-party modules."""

import argparse
import os
import select
import sys
import termios
import time
from typing import Tuple


def at_command(fd: int, command: str, timeout: float = 8.0) -> str:
    """Send one AT command and return its complete response."""
    while select.select([fd], [], [], 0)[0]:
        os.read(fd, 4096)
    os.write(fd, (command + "\r").encode("ascii"))
    deadline = time.monotonic() + timeout
    quiet_until = time.monotonic() + 0.5
    data = b""
    while time.monotonic() < deadline:
        readable, _, _ = select.select([fd], [], [], 0.2)
        if readable:
            data += os.read(fd, 4096)
            quiet_until = time.monotonic() + 0.5
        if data and time.monotonic() >= quiet_until:
            break
    return data.decode("utf-8", "replace").strip()


def open_at_port() -> Tuple[int, str]:
    # EC200U on this BBB exposes its AT interface as ttyUSB6.  Probe the
    # remaining interfaces as a fallback because firmware builds can differ.
    for port in ("/dev/ttyUSB6", "/dev/ttyUSB3", "/dev/ttyUSB2", "/dev/ttyUSB1",
                 "/dev/ttyUSB4", "/dev/ttyUSB5"):
        if not os.path.exists(port):
            continue
        try:
            fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            attrs = termios.tcgetattr(fd)
            attrs[0] = attrs[1] = attrs[3] = 0
            attrs[2] |= termios.CLOCAL | termios.CREAD
            attrs[4] = attrs[5] = termios.B115200
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            if "OK" in at_command(fd, "AT", 2.0):
                return fd, port
            os.close(fd)
        except OSError:
            continue
    raise RuntimeError("No responding Quectel AT port found")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apn", required=True)
    args = parser.parse_args()

    fd, port = open_at_port()
    try:
        print(f"Quectel AT port: {port}")
        for command in ("AT+CPIN?", "AT+QNWINFO"):
            response = at_command(fd, command)
            print(f"{command}: {response}")
            if "ERROR" in response:
                raise RuntimeError(f"Modem rejected {command}: {response}")

        # The APN belongs to PDP context 1 used by the ECM adapter.  A live
        # PDP context cannot be rewritten (the EC200U returns CME ERROR: 3),
        # so leave it alone when it already has the requested APN.
        contexts = at_command(fd, "AT+CGDCONT?")
        wanted_context = f'+CGDCONT: 1,"IP","{args.apn}"'
        if wanted_context not in contexts:
            response = at_command(fd, f'AT+CGDCONT=1,"IP","{args.apn}"')
            if "OK" not in response:
                raise RuntimeError(f"Could not set APN: {response}")
        else:
            print(f"PDP context 1 already uses APN: {args.apn}")

        status = at_command(fd, "AT+QNETDEVCTL?")
        if ",1,1" not in status:
            response = at_command(fd, "AT+QNETDEVCTL=1,1,1", 15.0)
            if "OK" not in response:
                raise RuntimeError(f"Could not start ECM data call: {response}")
            status = at_command(fd, "AT+QNETDEVCTL?")
        print(f"ECM data call: {status}")
        if ",1,1" not in status:
            raise RuntimeError("ECM data call did not become active")
        return 0
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"LTE modem setup failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
