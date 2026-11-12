#!/usr/bin/env python3
import os
import select
import termios
import time

def at_command(fd, cmd, timeout=3.0):
    while select.select([fd], [], [], 0)[0]:
        os.read(fd, 4096)
    os.write(fd, (cmd + "\r").encode("ascii"))
    deadline = time.monotonic() + timeout
    data = b""
    while time.monotonic() < deadline:
        r, _, _ = select.select([fd], [], [], 0.3)
        if r:
            data += os.read(fd, 4096)
            if b"OK\r\n" in data or b"ERROR\r\n" in data:
                break
    return data.decode("latin1", "replace").strip()

def check_port(port):
    if not os.path.exists(port):
        return None
    try:
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        attrs = termios.tcgetattr(fd)
        attrs[0] = attrs[1] = attrs[3] = 0
        attrs[2] |= termios.CLOCAL | termios.CREAD
        attrs[4] = attrs[5] = termios.B115200
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        res = at_command(fd, "AT", 1.0)
        if "OK" in res:
            return fd
        os.close(fd)
    except Exception as e:
        pass
    return None

def main():
    ports = ["/dev/ttyS1", "/dev/ttyO1", "/dev/ttyUSB2", "/dev/ttyUSB3", "/dev/ttyUSB1", "/dev/ttyUSB0", "/dev/ttyUSB6"]
    active_fd = None
    active_port = None
    for p in ports:
        fd = check_port(p)
        if fd is not None:
            active_fd = fd
            active_port = p
            print(f"Modem responded on: {p}")
            break
    
    if active_fd is None:
        print("ERROR: No port responded to AT commands!")
        return 1
    
    commands = [
        "ATI",
        "AT+CPIN?",
        "AT+CSQ",
        "AT+CREG?",
        "AT+CGREG?",
        "AT+CEREG?",
        "AT+COPS?",
        "AT+QNWINFO",
        "AT+CMEE=2",
        "AT+QCFG=\"usbnet\"",
        "AT+CGPADDR=1",
        "AT+CGCONTRDP=1",
        "AT+CGDCONT?",
        "AT+QNETDEVCTL?",
        "AT+QNETDEVCTL=?",
        "ATD*99#",
    ]
    for cmd in commands:
        res = at_command(active_fd, cmd, 3.0)
        print(f"--- {cmd} ---")
        print(res)
    
    os.close(active_fd)
    return 0

if __name__ == "__main__":
    main()

