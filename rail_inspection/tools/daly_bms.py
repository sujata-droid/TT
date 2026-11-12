#!/usr/bin/env python3
"""Daly Smart BMS UART/RS485 Communication Driver and Service.

Implements Dongguan Daly Electronics Co., Ltd Communications Protocol V1.2:
- Physical Layer: UART / RS485 (9600 bps, 8 data bits, no parity, 1 stop bit)
- Host Address: 0x40 (Upper computer)
- Slave Address: 0x01 (BMS Master)
- Frame Format: Fixed 13 bytes
    [0xA5, Addr, CmdID, Length=0x08, Data0..Data7, Checksum]
    Checksum = sum(all previous 12 bytes) & 0xFF

Key Commands:
- 0x90: SOC of total voltage and current:
    Byte 0-1: Cumulative total voltage (0.1 V)
    Byte 2-3: Gather total voltage (0.1 V)
    Byte 4-5: Current (30000 offset, 0.1 A)
    Byte 6-7: SOC (0.1 %)
- 0x91: Max & Min cell voltage (mV)
- 0x92: Max & Min cell temperature (40 offset, deg C)
- 0x93: Charge/Discharge MOS status, BMS life, Remaining capacity (mAh)
- 0x98: Fault & alarm bit flags
"""

import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

try:
    import serial
except ImportError:
    serial = None

# Paths for battery status shared with railgui25.py
BATTERY_PCT_PATHS = [
    "/tmp/battery_pct",
    "/home/debian/trolley/battery_pct.txt",
]
BATTERY_JSON_PATH = "/tmp/battery_status.json"

START_FLAG = 0xA5
HOST_ADDR = 0x40
BMS_ADDR = 0x01

# Common serial candidates on Linux / BeagleBone / USB dongles
DEFAULT_CANDIDATE_PORTS = [
    "/dev/ttyUSB0",
    "/dev/ttyUSB1",
    "/dev/ttyUSB2",
    "/dev/ttyS5",     # UART5 on BBB (P8_37 TX, P8_38 RX)
    "/dev/ttyO5",
]


def calc_checksum(frame: bytes) -> int:
    """Calculate Daly 1-byte checksum: sum of all preceding bytes modulo 256."""
    return sum(frame) & 0xFF


def build_request(cmd_id: int, data: Optional[bytes] = None) -> bytes:
    """Construct a 13-byte Daly request frame."""
    if data is None:
        data = b"\x00" * 8
    elif len(data) < 8:
        data = data.ljust(8, b"\x00")
    elif len(data) > 8:
        data = data[:8]

    header = bytes([START_FLAG, HOST_ADDR, cmd_id, 0x08])
    payload = header + data
    checksum = calc_checksum(payload)
    return payload + bytes([checksum])


def validate_response(resp: bytes, expected_cmd: int) -> bool:
    """Validate 13-byte response frame, header, command, length, and checksum."""
    if not resp or len(resp) != 13:
        return False
    if resp[0] != START_FLAG:
        return False
    if resp[1] != BMS_ADDR:
        return False
    if resp[2] != expected_cmd:
        return False
    if resp[3] != 0x08:
        return False
    expected_csum = calc_checksum(resp[:12])
    if resp[12] != expected_csum:
        return False
    return True


class DalyBMS:
    """Interface to Daly Smart BMS over UART / RS485."""

    def __init__(self, port: Optional[str] = None, baudrate: int = 9600, timeout: float = 0.5):
        self.port = port or os.environ.get("RAIL_BMS_PORT")
        self.baudrate = baudrate
        self.timeout = timeout
        self._ser: Optional[Any] = None

    def find_port(self) -> Optional[str]:
        """Auto-detect working Daly BMS port among candidates."""
        if self.port and os.path.exists(self.port):
            return self.port

        candidates: List[str] = []
        # Dynamic USB serials
        candidates.extend(sorted(glob.glob("/dev/ttyUSB*")))
        candidates.extend(DEFAULT_CANDIDATE_PORTS)

        for p in candidates:
            if not os.path.exists(p):
                continue
            try:
                ser = serial.Serial(p, baudrate=self.baudrate, timeout=0.3)
                # Send 0x90 test query
                ser.reset_input_buffer()
                ser.write(build_request(0x90))
                resp = ser.read(13)
                ser.close()
                if validate_response(resp, 0x90):
                    self.port = p
                    return p
            except Exception:
                continue

        # Fallback to first existing USB port if no response yet
        for p in candidates:
            if os.path.exists(p):
                self.port = p
                return p

        return None

    def connect(self) -> bool:
        if serial is None:
            sys.stderr.write("[BMS] Error: pyserial not installed.\n")
            return False

        if not self.port or not os.path.exists(self.port):
            detected = self.find_port()
            if not detected:
                return False
            self.port = detected

        try:
            if self._ser and self._ser.is_open:
                return True
            self._ser = serial.Serial(
                self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout,
            )
            return True
        except Exception as exc:
            self._ser = None
            return False

    def close(self):
        if self._ser and self._ser.is_open:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None

    def query(self, cmd_id: int) -> Optional[bytes]:
        """Send command and read 13-byte response."""
        if not self.connect() or not self._ser:
            return None

        req = build_request(cmd_id)
        try:
            self._ser.reset_input_buffer()
            self._ser.write(req)
            resp = self._ser.read(13)
            if validate_response(resp, cmd_id):
                return resp
        except Exception:
            self.close()
        return None

    def read_soc_voltage_current(self) -> Optional[Dict[str, Any]]:
        """Query 0x90: Cumulative Voltage, Current, and SOC (State of Charge)."""
        resp = self.query(0x90)
        if not resp:
            return None

        # Byte 0..1 (index 4..5): Cumulative total voltage (0.1 V)
        total_volt_raw = (resp[4] << 8) | resp[5]
        total_voltage_v = total_volt_raw / 10.0

        # Byte 2..3 (index 6..7): Gather total voltage (0.1 V)
        gather_volt_raw = (resp[6] << 8) | resp[7]
        gather_voltage_v = gather_volt_raw / 10.0

        # Byte 4..5 (index 8..9): Current (offset 30000, 0.1 A)
        current_raw = (resp[8] << 8) | resp[9]
        current_a = (current_raw - 30000) / 10.0

        # Byte 6..7 (index 10..11): SOC (0.1 %)
        soc_raw = (resp[10] << 8) | resp[11]
        soc_pct = soc_raw / 10.0

        state = "idle"
        if current_a > 0.05:
            state = "charging"
        elif current_a < -0.05:
            state = "discharging"

        return {
            "soc_pct": round(soc_pct, 1),
            "total_voltage_v": round(total_voltage_v, 1),
            "gather_voltage_v": round(gather_voltage_v, 1),
            "current_a": round(current_a, 1),
            "state": state,
            "color": "red" if soc_pct <= 20.0 else "green",
        }

    def read_temperatures(self) -> Optional[Dict[str, Any]]:
        """Query 0x92: Max & Min temperatures (offset 40 deg C)."""
        resp = self.query(0x92)
        if not resp:
            return None

        max_temp = resp[4] - 40
        max_temp_cell = resp[5]
        min_temp = resp[6] - 40
        min_temp_cell = resp[7]

        return {
            "max_temp_c": max_temp,
            "max_temp_cell": max_temp_cell,
            "min_temp_c": min_temp,
            "min_temp_cell": min_temp_cell,
        }

    def read_status(self) -> Dict[str, Any]:
        """Aggregate readings into a complete status dictionary."""
        data = self.read_soc_voltage_current()
        if not data:
            return {"connected": False, "port": self.port}

        temps = self.read_temperatures()
        if temps:
            data.update(temps)

        data["connected"] = True
        data["port"] = self.port
        data["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return data


def write_battery_status(soc_pct: float, full_info: Optional[Dict[str, Any]] = None):
    """Write battery percentage and diagnostics to files monitored by railgui25."""
    int_pct = max(0, min(100, int(round(soc_pct))))

    # 1. Update text files polled by railgui25.py TopBarWidget
    for p in BATTERY_PCT_PATHS:
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(f"{int_pct}\n")
        except Exception:
            pass

    # 2. Update detailed JSON status file
    if full_info:
        try:
            with open(BATTERY_JSON_PATH, "w") as f:
                json.dump(full_info, f, indent=2)
        except Exception:
            pass


def run_daemon(poll_interval: float = 2.0, port: Optional[str] = None):
    """Continuous background loop updating battery status for railgui25."""
    bms = DalyBMS(port=port)
    print(f"[BMS] Starting Daly BMS monitor (poll every {poll_interval}s)...")

    while True:
        status = bms.read_status()
        if status.get("connected"):
            soc = status.get("soc_pct", 0.0)
            volt = status.get("total_voltage_v", 0.0)
            curr = status.get("current_a", 0.0)
            col = status.get("color", "green").upper()
            print(f"[BMS] Port={bms.port} | SOC={soc:.1f}% ({col}) | V={volt:.1f}V | I={curr:.1f}A | State={status.get('state')}")
            write_battery_status(soc, status)
        else:
            # If disconnected, retry port search periodically
            bms.close()
            time.sleep(2.0)
            bms.find_port()

        time.sleep(poll_interval)


def main():
    parser = argparse.ArgumentParser(description="Daly BMS UART Reader & Status Provider")
    parser.add_argument("--port", help="Serial port (e.g. /dev/ttyUSB0, /dev/ttyS5)")
    parser.add_argument("--daemon", action="store_true", help="Run background monitor daemon")
    parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds")
    parser.add_argument("--test", action="store_true", help="Read once and print status")
    parser.add_argument("--mock", type=float, help="Set mock battery percentage for GUI testing (e.g. 15 or 85)")

    args = parser.parse_args()

    if args.mock is not None:
        pct = max(0.0, min(100.0, float(args.mock)))
        mock_info = {
            "connected": True,
            "mock": True,
            "soc_pct": pct,
            "total_voltage_v": 52.4,
            "current_a": -1.8,
            "state": "discharging",
            "color": "red" if pct <= 20.0 else "green",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        write_battery_status(pct, mock_info)
        print(f"[BMS] Wrote mock battery: {int(round(pct))}% (Color: {mock_info['color'].upper()}) to {BATTERY_PCT_PATHS}")
        return

    if args.daemon:
        run_daemon(poll_interval=args.interval, port=args.port)
        return

    # Default to single test readout
    bms = DalyBMS(port=args.port)
    print(f"Connecting to Daly BMS (port: {bms.port or 'auto-detect'})...")
    status = bms.read_status()
    print(json.dumps(status, indent=2))
    if status.get("connected"):
        write_battery_status(status["soc_pct"], status)
        print(f"\nBattery percentage successfully updated: {int(round(status['soc_pct']))}%")


if __name__ == "__main__":
    main()

