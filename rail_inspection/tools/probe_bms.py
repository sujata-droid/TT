#!/usr/bin/env python3
"""Diagnostic script to probe BeagleBone Black serial ports for Daly Smart BMS."""

import glob
import os
import sys
import time

try:
    import serial
except ImportError:
    print("ERROR: pyserial is not installed!")
    sys.exit(1)

print("--- System serial ports ---")
os.system("ls -l /dev/ttyS* /dev/ttyUSB* 2>/dev/null")

print("\n--- Testing /dev/ttyS1 for AT command (115200) ---")
try:
    s = serial.Serial('/dev/ttyS1', 115200, timeout=0.5)
    s.reset_input_buffer()
    s.write(b"AT\r\n")
    time.sleep(0.1)
    resp = s.read(100)
    print(f"ttyS1 AT response: {resp}")
    s.close()
except Exception as e:
    print(f"ttyS1 error: {e}")

print("\n--- Testing all ports for Daly BMS (9600 baud) ---")
REQ_90 = bytes([0xA5, 0x40, 0x90, 0x08, 0, 0, 0, 0, 0, 0, 0, 0, 0x7D])

candidate_ports = ["/dev/ttyS1", "/dev/ttyS2", "/dev/ttyS3", "/dev/ttyS4", "/dev/ttyS5"]
candidate_ports.extend(glob.glob("/dev/ttyUSB*"))

for port in candidate_ports:
    if not os.path.exists(port):
        continue
    print(f"\nChecking {port}...")
    for baud in [9600, 19200, 115200]:
        try:
            s = serial.Serial(port, baudrate=baud, timeout=0.4)
            s.reset_input_buffer()
            s.write(REQ_90)
            time.sleep(0.1)
            resp = s.read(50)
            if resp:
                hex_str = " ".join(f"{b:02X}" for b in resp)
                print(f"  [{baud}] Recv {len(resp)} bytes: {hex_str}")
                if len(resp) >= 13 and resp[0] == 0xA5 and resp[1] == 0x01:
                    print(f"  ===> SUCCESS! Valid Daly BMS response on {port}!")
                    break
            else:
                print(f"  [{baud}] 0 bytes")
            s.close()
        except Exception as e:
            print(f"  [{baud}] Error: {e}")


