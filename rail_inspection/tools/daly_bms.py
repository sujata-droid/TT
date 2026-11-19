#!/usr/bin/env python3
"""Daly Smart BMS monitor supporting Raspberry Pi Pico USB stream and direct UART/RS485.

Supports two hardware paths automatically:
1. Raspberry Pi Pico WH bridge over USB CDC (/dev/ttyACM*, 115200 baud) running
   the DALY BMS COMPLETE MONITOR firmware emitting ``DATA,<40 CSV fields>`` lines.
2. Direct Dongguan Daly Electronics Protocol V1.2 over UART/RS485 (/dev/ttyUSB*,
   /dev/ttyS5, /dev/ttyS2, 9600 8N1) querying Data IDs 0x90 through 0x98.

Publishes atomic status files for railgui25.py (/tmp/battery_status.json and
/tmp/battery_pct) and optionally appends 41-column Daly BMS telemetry logs under
DALY_BMS_Logs/ matching daly_bms_logger.py.
"""

import argparse
import csv
import glob
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import serial
except ImportError:
    serial = None

try:
    from serial.tools import list_ports as serial_list_ports
except ImportError:
    serial_list_ports = None


BATTERY_PCT_PATHS = [
    "/tmp/battery_pct",
    "/home/debian/trolley/battery_pct.txt",
]
BATTERY_JSON_PATH = os.environ.get("RAIL_BATTERY_JSON", "/tmp/battery_status.json")

START_FLAG = 0xA5
HOST_ADDR = 0x40
BMS_ADDR = 0x01
FRAME_SIZE = 13

PICO_BAUD = int(os.environ.get("RAIL_BMS_PICO_BAUD", "115200"))
DALY_BAUD = int(os.environ.get("RAIL_BMS_DALY_BAUD", "9600"))
DEFAULT_STALE_SECONDS = float(os.environ.get("RAIL_BMS_STALE_SECONDS", "150"))
DEFAULT_CSV_INTERVAL = float(os.environ.get("RAIL_BMS_CSV_INTERVAL", "60"))

# Never probe BBB serial console (ttyS0), LTE modem (ttyS1), GNSS (ttyS4), or USB gadget (ttyGS0).
PROTECTED_PORTS = {
    "/dev/ttyS0",
    "/dev/ttyO0",
    "/dev/ttyS1",
    "/dev/ttyO1",
    "/dev/ttyS4",
    "/dev/ttyO4",
    "/dev/ttyGS0",
}

DEFAULT_UART_CANDIDATES = [
    "/dev/ttyS5",
    "/dev/ttyO5",
    "/dev/ttyS2",
    "/dev/ttyO2",
]

HEADERS = [
    "Timestamp",
    "Pack_Voltage_V",
    "Current_A",
    "SOC_Percent",
    "Remaining_Capacity_mAh",
    "Max_Cell_mV",
    "Max_Cell_Number",
    "Min_Cell_mV",
    "Min_Cell_Number",
    "Cell1_V",
    "Cell2_V",
    "Cell3_V",
    "Cell4_V",
    "Cell5_V",
    "Cell6_V",
    "Cell7_V",
    "Cell8_V",
    "Extra_Cell_Slot_V",
    "Temp1_C",
    "Temp2_C",
    "Temp3_C",
    "Temp4_C",
    "Operating_State",
    "Charge_MOS",
    "Discharge_MOS",
    "BMS_Life",
    "Cell_Count",
    "Temperature_Sensor_Count",
    "Charger_Status",
    "Load_Status",
    "Digital_IO",
    "Balancing_Low_32",
    "Balancing_High_32",
    "Fault_Byte0",
    "Fault_Byte1",
    "Fault_Byte2",
    "Fault_Byte3",
    "Fault_Byte4",
    "Fault_Byte5",
    "Fault_Byte6",
    "Fault_Code",
]

STATE_LABELS = {
    0: "Stationary",
    1: "Charging",
    2: "Discharging",
}

# Dongguan Daly UART/485 Communications Protocol V1.2 Section 3 (0x98, pages 6-7)
FAULT_BIT_DEFINITIONS: Tuple[Tuple[int, int, str], ...] = (
    # Byte 0
    (0, 0, "Cell volt high level 1"),
    (0, 1, "Cell volt high level 2"),
    (0, 2, "Cell volt low level 1"),
    (0, 3, "Cell volt low level 2"),
    (0, 4, "Sum volt high level 1"),
    (0, 5, "Sum volt high level 2"),
    (0, 6, "Sum volt low level 1"),
    (0, 7, "Sum volt low level 2"),
    # Byte 1
    (1, 0, "Chg temp high level 1"),
    (1, 1, "Chg temp high level 2"),
    (1, 2, "Chg temp low level 1"),
    (1, 3, "Chg temp low level 2"),
    (1, 4, "Dischg temp high level 1"),
    (1, 5, "Dischg temp high level 2"),
    (1, 6, "Dischg temp low level 1"),
    (1, 7, "Dischg temp low level 2"),
    # Byte 2
    (2, 0, "Chg overcurrent level 1"),
    (2, 1, "Chg overcurrent level 2"),
    (2, 2, "Dischg overcurrent level 1"),
    (2, 3, "Dischg overcurrent level 2"),
    (2, 4, "SOC high level 1"),
    (2, 5, "SOC high level 2"),
    (2, 6, "SOC low level 1"),
    (2, 7, "SOC low level 2"),
    # Byte 3
    (3, 0, "Diff volt level 1"),
    (3, 1, "Diff volt level 2"),
    (3, 2, "Diff temp level 1"),
    (3, 3, "Diff temp level 2"),
    # Byte 4
    (4, 0, "Chg MOS temp high alarm"),
    (4, 1, "Dischg MOS temp high alarm"),
    (4, 2, "Chg MOS temp sensor err"),
    (4, 3, "Dischg MOS temp sensor err"),
    (4, 4, "Chg MOS adhesion err"),
    (4, 5, "Dischg MOS adhesion err"),
    (4, 6, "Chg MOS open circuit err"),
    (4, 7, "Dischg MOS open circuit err"),
    # Byte 5
    (5, 0, "AFE collect chip err"),
    (5, 1, "Voltage collect dropped"),
    (5, 2, "Cell temp sensor err"),
    (5, 3, "EEPROM err"),
    (5, 4, "RTC err"),
    (5, 5, "Precharge failure"),
    (5, 6, "Communication failure"),
    (5, 7, "Internal communication failure"),
    # Byte 6
    (6, 0, "Current module fault"),
    (6, 1, "Sum voltage detect fault"),
    (6, 2, "Short circuit protect fault"),
    (6, 3, "Low volt forbidden chg fault"),
)


def default_log_dir() -> str:
    env_dir = os.environ.get("RAIL_BMS_LOG_DIR", "").strip()
    if env_dir:
        return env_dir
    if os.path.isdir("/home/debian"):
        return "/home/debian/DALY_BMS_Logs"
    return os.path.join(os.getcwd(), "DALY_BMS_Logs")


def state_name(value: Any) -> str:
    """Return Daly operating state label from numeric code or string."""
    try:
        code = int(str(value).strip())
    except (TypeError, ValueError):
        return "Unknown"
    return STATE_LABELS.get(code, "Unknown")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def decode_fault_bytes(faults: Sequence[int]) -> List[str]:
    """Decode Daly 0x98 fault bytes (Byte0..Byte6 + Byte7 Fault_Code) into labels."""
    padded = list(faults[:8]) + [0] * max(0, 8 - len(faults))
    active: List[str] = []
    for byte_idx, bit_idx, label in FAULT_BIT_DEFINITIONS:
        if padded[byte_idx] & (1 << bit_idx):
            active.append(label)
    if padded[7] != 0:
        active.append(f"Fault code {padded[7]}")
    return active


def decode_balancing_cells(bal_low_32: int, bal_high_32: int = 0) -> List[int]:
    """Return 1-based cell indices currently marked as balancing."""
    bitmap = ((int(bal_high_32) & 0xFFFFFFFF) << 32) | (int(bal_low_32) & 0xFFFFFFFF)
    return [idx + 1 for idx in range(48) if (bitmap >> idx) & 1]


def build_battery_status(
    *,
    pack_voltage_v: float,
    current_a: float,
    soc_pct: float,
    remaining_mah: int = 0,
    max_cell_mv: int = 0,
    max_cell_num: int = 0,
    min_cell_mv: int = 0,
    min_cell_num: int = 0,
    cells_v: Sequence[float] = (),
    extra_cell_v: float = -1.0,
    temperatures_c: Sequence[float] = (),
    state_code: int = 255,
    charge_mos: int = 255,
    discharge_mos: int = 255,
    bms_life: int = 0,
    cell_count: int = 0,
    temp_sensor_count: int = 0,
    charger_status: int = 255,
    load_status: int = 255,
    digital_io: int = 0,
    bal_low_32: int = 0,
    bal_high_32: int = 0,
    faults_raw: Sequence[int] = (),
    port: Optional[str] = None,
    source: str = "daly_uart",
    mock: bool = False,
    timestamp_str: Optional[str] = None,
    raw_csv_values: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Normalize Pico or direct Daly BMS telemetry into status JSON + CSV values."""
    ts = timestamp_str or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    soc_clamped = max(0.0, min(100.0, round(float(soc_pct), 1)))
    pack_v = round(float(pack_voltage_v), 2)
    curr_a = round(float(current_a), 2)

    # Explicitly detect charging when charger is connected, current is positive, or Daly reports charging
    try:
        raw_st_code = int(state_code)
    except (TypeError, ValueError):
        raw_st_code = 255

    try:
        chg_stat = int(charger_status)
    except (TypeError, ValueError):
        chg_stat = 255

    if chg_stat == 1 or curr_a > 0.05 or raw_st_code == 1:
        inferred_code = 1
    elif curr_a < -0.05 or raw_st_code == 2:
        inferred_code = 2
    elif raw_st_code in STATE_LABELS:
        inferred_code = raw_st_code
    else:
        inferred_code = 0

    s_label = STATE_LABELS.get(inferred_code, "Unknown")
    state_lower = s_label.lower()

    padded_cells = list(cells_v[:8]) + [-1.0] * max(0, 8 - len(cells_v))
    clean_cells: List[Optional[float]] = [
        round(float(v), 3) if v is not None and 0.0 < float(v) < 10.0 else None
        for v in padded_cells
    ]
    clean_extra = round(float(extra_cell_v), 3) if 0.0 < float(extra_cell_v) < 10.0 else None

    padded_temps = list(temperatures_c[:4]) + [-999.0] * max(0, 4 - len(temperatures_c))
    clean_temps: List[Optional[float]] = [
        round(float(t), 1) if t is not None and -50.0 <= float(t) <= 150.0 else None
        for t in padded_temps
    ]
    valid_temps = [t for t in clean_temps if t is not None]
    max_temp_c = max(valid_temps) if valid_temps else None
    min_temp_c = min(valid_temps) if valid_temps else None

    fault_list_raw = [int(f) & 0xFF for f in list(faults_raw[:8])] + [0] * max(0, 8 - len(faults_raw))
    active_faults = decode_fault_bytes(fault_list_raw)
    balancing_cells = decode_balancing_cells(bal_low_32, bal_high_32)
    delta_mv = (
        int(max_cell_mv) - int(min_cell_mv)
        if max_cell_mv > 0 and min_cell_mv > 0 and max_cell_mv >= min_cell_mv
        else 0
    )

    if raw_csv_values is not None and len(raw_csv_values) == len(HEADERS) - 1:
        csv_values = list(raw_csv_values)
    else:
        csv_values = [
            f"{pack_v:.2f}",
            f"{curr_a:.2f}",
            f"{soc_clamped:.1f}",
            str(int(remaining_mah)),
            str(int(max_cell_mv)),
            str(int(max_cell_num)),
            str(int(min_cell_mv)),
            str(int(min_cell_num)),
            *[f"{padded_cells[i]:.3f}" for i in range(8)],
            f"{float(extra_cell_v):.3f}",
            *[f"{padded_temps[i]:.1f}" for i in range(4)],
            str(int(state_code)),
            str(int(charge_mos)),
            str(int(discharge_mos)),
            str(int(bms_life)),
            str(int(cell_count)),
            str(int(temp_sensor_count)),
            str(int(charger_status)),
            str(int(load_status)),
            str(int(digital_io)),
            str(int(bal_low_32) & 0xFFFFFFFF),
            str(int(bal_high_32) & 0xFFFFFFFF),
            *[str(int(f)) for f in fault_list_raw],
        ]

    color = "red" if (soc_clamped <= 20.0 or bool(active_faults)) else "green"

    return {
        "connected": True,
        "stale": False,
        "mock": bool(mock),
        "source": source,
        "port": port,
        "timestamp": ts,
        "unix_ts": round(time.time(), 3),
        "soc_pct": soc_clamped,
        "total_voltage_v": pack_v,
        "current_a": curr_a,
        "remaining_capacity_mah": int(remaining_mah),
        "max_cell_mv": int(max_cell_mv),
        "max_cell_num": int(max_cell_num),
        "min_cell_mv": int(min_cell_mv),
        "min_cell_num": int(min_cell_num),
        "cell_delta_mv": delta_mv,
        "cells_v": clean_cells,
        "extra_cell_v": clean_extra,
        "temperatures_c": clean_temps,
        "max_temp_c": max_temp_c,
        "min_temp_c": min_temp_c,
        "state_code": int(state_code),
        "state_label": s_label,
        "state": state_lower,
        "charge_mos": int(charge_mos),
        "discharge_mos": int(discharge_mos),
        "bms_life": int(bms_life),
        "cell_count": int(cell_count),
        "temp_sensor_count": int(temp_sensor_count),
        "charger_status": int(charger_status),
        "load_status": int(load_status),
        "digital_io": int(digital_io),
        "balancing_low_32": int(bal_low_32) & 0xFFFFFFFF,
        "balancing_high_32": int(bal_high_32) & 0xFFFFFFFF,
        "balancing_cells": balancing_cells,
        "faults_raw": fault_list_raw,
        "faults": active_faults,
        "fault_count": len(active_faults),
        "color": color,
        "csv_values": csv_values,
    }


def parse_pico_data_line(line: str, port: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Parse one ``DATA,<40 values>`` line emitted by the Pico Daly BMS monitor."""
    if not line:
        return None
    text = line.strip()
    if not text.startswith("DATA,"):
        return None

    values = [item.strip() for item in text.split(",")[1:]]
    if len(values) != len(HEADERS) - 1:
        return None

    return build_battery_status(
        pack_voltage_v=_to_float(values[0]),
        current_a=_to_float(values[1]),
        soc_pct=_to_float(values[2]),
        remaining_mah=_to_int(values[3]),
        max_cell_mv=_to_int(values[4]),
        max_cell_num=_to_int(values[5]),
        min_cell_mv=_to_int(values[6]),
        min_cell_num=_to_int(values[7]),
        cells_v=[_to_float(values[8 + i], -1.0) for i in range(8)],
        extra_cell_v=_to_float(values[16], -1.0),
        temperatures_c=[_to_float(values[17 + i], -999.0) for i in range(4)],
        state_code=_to_int(values[21], 255),
        charge_mos=_to_int(values[22], 255),
        discharge_mos=_to_int(values[23], 255),
        bms_life=_to_int(values[24], 0),
        cell_count=_to_int(values[25], 0),
        temp_sensor_count=_to_int(values[26], 0),
        charger_status=_to_int(values[27], 255),
        load_status=_to_int(values[28], 255),
        digital_io=_to_int(values[29], 0),
        bal_low_32=_to_int(values[30], 0),
        bal_high_32=_to_int(values[31], 0),
        faults_raw=[_to_int(values[32 + i], 0) for i in range(8)],
        port=port,
        source="pico_usb",
        raw_csv_values=values,
    )


def calc_checksum(frame: bytes) -> int:
    """Calculate Daly 1-byte checksum: low 8 bits of sum of preceding bytes."""
    return sum(frame) & 0xFF


def build_request(cmd_id: int, data: Optional[bytes] = None) -> bytes:
    """Construct a 13-byte Daly UART/485 V1.2 request frame."""
    if data is None:
        payload_data = b"\x00" * 8
    elif len(data) < 8:
        payload_data = data.ljust(8, b"\x00")
    else:
        payload_data = data[:8]

    payload = bytes([START_FLAG, HOST_ADDR, cmd_id & 0xFF, 0x08]) + payload_data
    return payload + bytes([calc_checksum(payload)])


def validate_response(resp: bytes, expected_cmd: int) -> bool:
    """Validate 13-byte Daly BMS response frame header, command, length, and checksum."""
    if not resp or len(resp) != FRAME_SIZE:
        return False
    if resp[0] != START_FLAG or resp[1] != BMS_ADDR:
        return False
    if resp[2] != (expected_cmd & 0xFF) or resp[3] != 0x08:
        return False
    return resp[12] == calc_checksum(resp[:12])


def _real_port(path: str) -> str:
    try:
        return os.path.realpath(path)
    except Exception:
        return path


def _is_protected_port(path: str) -> bool:
    real = _real_port(path)
    if path in PROTECTED_PORTS or real in PROTECTED_PORTS:
        return True
    lower = os.path.basename(path).lower()
    return any(tok in lower for tok in ("quectel", "ec200", "android", "modem"))


def discover_candidate_ports(explicit_port: Optional[str] = None) -> List[Tuple[str, str]]:
    """Return prioritized ``[(port_path, hint)]`` where hint is ``'pico'`` or ``'daly'``."""
    candidates: List[Tuple[str, str]] = []
    seen = set()

    def _add(path: Optional[str], hint: str) -> None:
        if not path:
            return
        if not path.upper().startswith("COM") and not os.path.exists(path):
            return
        if _is_protected_port(path):
            return
        key = path if path.upper().startswith("COM") else _real_port(path)
        if key in seen:
            return
        seen.add(key)
        candidates.append((key if key.startswith("/dev/") else path, hint))

    env_port = explicit_port or os.environ.get("RAIL_BMS_PORT", "").strip()
    if env_port:
        hint = "pico" if ("ACM" in env_port.upper() or env_port.upper().startswith("COM")) else "daly"
        _add(env_port, hint)

    # 1. USB CDC Pico devices by Linux /dev/serial/by-id or /dev/ttyACM*
    for path in sorted(glob.glob("/dev/serial/by-id/*")):
        name_l = os.path.basename(path).lower()
        if any(k in name_l for k in ("pico", "rp2040", "raspberry", "2e8a", "cdc")):
            _add(path, "pico")

    for path in sorted(glob.glob("/dev/ttyACM*")):
        _add(path, "pico")

    # 2. Windows / cross-platform list_ports (useful on Windows dev/test)
    if serial_list_ports is not None:
        try:
            for info in serial_list_ports.comports():
                dev = getattr(info, "device", None)
                vid = getattr(info, "vid", None)
                desc = (getattr(info, "description", "") or "").lower()
                if vid == 0x2E8A or "pico" in desc or "rp2040" in desc:
                    _add(dev, "pico")
                elif dev and dev.upper().startswith("COM"):
                    _add(dev, "pico")
        except Exception:
            pass

    # 3. USB-UART / USB-RS485 adapters (typically Daly 9600, could also be Pico)
    for path in sorted(glob.glob("/dev/ttyUSB*")):
        _add(path, "daly")

    # 4. Safe BeagleBone hardware UARTs (ttyS5 / ttyS2; never ttyS0/1/4)
    for path in DEFAULT_UART_CANDIDATES:
        _add(path, "daly")

    return candidates


class DalyBMS:
    """Interface to Daly Smart BMS V1.2 over direct UART / RS485 (9600 8N1)."""

    def __init__(self, port: Optional[str] = None, baudrate: int = DALY_BAUD, timeout: float = 0.45):
        self.port = port or os.environ.get("RAIL_BMS_PORT")
        self.baudrate = int(baudrate)
        self.timeout = float(timeout)
        self._ser: Optional[Any] = None

    def find_port(self) -> Optional[str]:
        """Auto-detect a responding Daly BMS UART/RS485 port."""
        if self.port and not _is_protected_port(self.port) and (
            self.port.upper().startswith("COM") or os.path.exists(self.port)
        ):
            return self.port

        if serial is None:
            return None

        for path, hint in discover_candidate_ports():
            if hint == "pico":
                continue
            try:
                ser = serial.Serial(
                    path,
                    baudrate=self.baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=0.35,
                )
                try:
                    ser.reset_input_buffer()
                    ser.write(build_request(0x90))
                    ser.flush()
                    resp = self._read_frame_from(ser, 0x90, deadline_s=0.45)
                finally:
                    ser.close()
                if resp:
                    self.port = path
                    return path
            except Exception:
                continue
        return None

    def connect(self) -> bool:
        if serial is None:
            sys.stderr.write("[BMS] Error: pyserial not installed.\n")
            return False

        if not self.port or _is_protected_port(self.port) or (
            not self.port.upper().startswith("COM") and not os.path.exists(self.port)
        ):
            if not self.find_port():
                return False

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
        except Exception:
            self._ser = None
            return False

    def close(self) -> None:
        if self._ser:
            try:
                if self._ser.is_open:
                    self._ser.close()
            except Exception:
                pass
        self._ser = None

    def _read_frame_from(self, ser_obj: Any, expected_cmd: Optional[int], deadline_s: float) -> Optional[bytes]:
        end_at = time.monotonic() + deadline_s
        while time.monotonic() < end_at:
            first = ser_obj.read(1)
            if not first:
                continue
            if first[0] != START_FLAG:
                continue
            rest = ser_obj.read(FRAME_SIZE - 1)
            if len(rest) != FRAME_SIZE - 1:
                continue
            frame = first + rest
            cmd = expected_cmd if expected_cmd is not None else frame[2]
            if validate_response(frame, cmd):
                return frame
        return None

    def query(self, cmd_id: int) -> Optional[bytes]:
        """Send a single Daly V1.2 command and read one 13-byte response frame."""
        if not self.connect() or not self._ser:
            return None
        try:
            self._ser.reset_input_buffer()
            self._ser.write(build_request(cmd_id))
            self._ser.flush()
            return self._read_frame_from(self._ser, cmd_id, deadline_s=max(0.45, self.timeout))
        except Exception:
            self.close()
            return None

    def query_multi(
        self, cmd_id: int, max_frames: int = 8, quiet_timeout: float = 0.25, max_window: float = 1.2
    ) -> List[bytes]:
        """Send command (0x95 or 0x96) and collect multi-frame responses deduplicated by Byte0."""
        if not self.connect() or not self._ser:
            return []
        try:
            self._ser.reset_input_buffer()
            self._ser.write(build_request(cmd_id))
            self._ser.flush()
            frames: Dict[int, bytes] = {}
            ordered: List[bytes] = []
            start = time.monotonic()
            last_rx = start
            while time.monotonic() - start < max_window and len(ordered) < max_frames:
                if ordered and (time.monotonic() - last_rx) >= quiet_timeout:
                    break
                frame = self._read_frame_from(self._ser, cmd_id, deadline_s=0.15)
                if frame:
                    last_rx = time.monotonic()
                    seq = frame[4]
                    if seq not in frames:
                        frames[seq] = frame
                        ordered.append(frame)
            return ordered
        except Exception:
            self.close()
            return []

    def read_soc_voltage_current(self) -> Optional[Dict[str, Any]]:
        """Query 0x90: Cumulative Voltage, Gather Voltage, Current, and SOC."""
        resp = self.query(0x90)
        if not resp:
            return None
        total_volt_v = ((resp[4] << 8) | resp[5]) / 10.0
        gather_volt_v = ((resp[6] << 8) | resp[7]) / 10.0
        current_a = (((resp[8] << 8) | resp[9]) - 30000) / 10.0
        soc_pct = ((resp[10] << 8) | resp[11]) / 10.0
        state = "charging" if current_a > 0.05 else ("discharging" if current_a < -0.05 else "stationary")
        return {
            "soc_pct": round(soc_pct, 1),
            "total_voltage_v": round(total_volt_v, 1),
            "gather_voltage_v": round(gather_volt_v, 1),
            "current_a": round(current_a, 1),
            "state": state,
            "color": "red" if soc_pct <= 20.0 else "green",
        }

    def read_temperatures(self) -> Optional[Dict[str, Any]]:
        """Query 0x92: Max & Min temperature summary."""
        resp = self.query(0x92)
        if not resp:
            return None
        return {
            "max_temp_c": resp[4] - 40,
            "max_temp_cell": resp[5],
            "min_temp_c": resp[6] - 40,
            "min_temp_cell": resp[7],
        }

    def read_status(self) -> Dict[str, Any]:
        """Query full Daly 0x90..0x98 dataset when connected over direct UART."""
        r90 = self.query(0x90)
        if not r90:
            return {"connected": False, "port": self.port}

        pack_v = ((r90[4] << 8) | r90[5]) * 0.1
        current_a = (((r90[8] << 8) | r90[9]) - 30000) * 0.1
        soc_pct = ((r90[10] << 8) | r90[11]) * 0.1

        r91 = self.query(0x91)
        max_cell_mv = ((r91[4] << 8) | r91[5]) if r91 else 0
        max_cell_num = r91[6] if r91 else 0
        min_cell_mv = ((r91[7] << 8) | r91[8]) if r91 else 0
        min_cell_num = r91[9] if r91 else 0

        r92 = self.query(0x92)
        r93 = self.query(0x93)
        state_code = r93[4] if r93 else 255
        charge_mos = r93[5] if r93 else 255
        discharge_mos = r93[6] if r93 else 255
        bms_life = r93[7] if r93 else 0
        rem_mah = (
            ((r93[8] << 24) | (r93[9] << 16) | (r93[10] << 8) | r93[11])
            if r93
            else 0
        )

        r94 = self.query(0x94)
        cell_count = r94[4] if r94 else 0
        temp_count = r94[5] if r94 else 0
        charger_status = r94[6] if r94 else 255
        load_status = r94[7] if r94 else 255
        digital_io = r94[8] if r94 else 0

        cells_v = [-1.0] * 8
        extra_cell_v = -1.0
        for frame in self.query_multi(0x95, max_frames=6, quiet_timeout=0.35):
            frame_no = frame[4]
            if not (1 <= frame_no <= 3):
                continue
            for slot in range(3):
                idx = 5 + slot * 2
                mv = (frame[idx] << 8) | frame[idx + 1]
                volt = -1.0 if (mv == 0 or mv == 0xFFFF) else mv / 1000.0
                pos = (frame_no - 1) * 3 + slot
                if pos < 8:
                    cells_v[pos] = volt
                elif pos == 8:
                    extra_cell_v = volt

        temps_c = [-999.0] * 4
        for frame in self.query_multi(0x96, max_frames=3, quiet_timeout=0.25):
            frame_no = frame[4]
            if frame_no == 0 or frame_no == 0xFF:
                continue
            first_sensor = (frame_no - 1) * 7
            for slot in range(7):
                sensor_idx = first_sensor + slot
                if sensor_idx >= 4:
                    break
                raw_t = frame[5 + slot]
                temps_c[sensor_idx] = -999.0 if raw_t == 0xFF else float(raw_t - 40)

        # Fallback to 0x92 summary temps if 0x96 multi-frame was empty
        if all(t <= -900.0 for t in temps_c) and r92:
            if r92[4] != 0xFF:
                temps_c[0] = float(r92[4] - 40)
            if r92[6] != 0xFF:
                temps_c[1] = float(r92[6] - 40)

        r97 = self.query(0x97)
        bal_low_32 = 0
        bal_high_32 = 0
        if r97:
            bitmap = 0
            for i in range(8):
                bitmap |= (r97[4 + i] << (8 * i))
            bal_low_32 = bitmap & 0xFFFFFFFF
            bal_high_32 = (bitmap >> 32) & 0xFFFFFFFF

        r98 = self.query(0x98)
        faults_raw = list(r98[4:12]) if r98 else [0] * 8

        return build_battery_status(
            pack_voltage_v=pack_v,
            current_a=current_a,
            soc_pct=soc_pct,
            remaining_mah=rem_mah,
            max_cell_mv=max_cell_mv,
            max_cell_num=max_cell_num,
            min_cell_mv=min_cell_mv,
            min_cell_num=min_cell_num,
            cells_v=cells_v,
            extra_cell_v=extra_cell_v,
            temperatures_c=temps_c,
            state_code=state_code,
            charge_mos=charge_mos,
            discharge_mos=discharge_mos,
            bms_life=bms_life,
            cell_count=cell_count,
            temp_sensor_count=temp_count,
            charger_status=charger_status,
            load_status=load_status,
            digital_io=digital_io,
            bal_low_32=bal_low_32,
            bal_high_32=bal_high_32,
            faults_raw=faults_raw,
            port=self.port,
            source="daly_uart",
        )


class DalyCSVLogger:
    """Append normalized Daly BMS telemetry to DALY_BMS_YYYYMMDD_HHMMSS.csv."""

    def __init__(self, log_dir: Optional[str] = None):
        self.log_dir = log_dir or default_log_dir()
        self.path: Optional[str] = None
        self._file: Optional[Any] = None
        self._writer: Optional[Any] = None

    def _ensure_open(self) -> bool:
        if self._writer is not None:
            return True
        try:
            os.makedirs(self.log_dir, exist_ok=True)
            filename = f"DALY_BMS_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            self.path = os.path.join(self.log_dir, filename)
            self._file = open(self.path, "w", newline="", encoding="utf-8")
            self._writer = csv.writer(self._file)
            self._writer.writerow(HEADERS)
            self._file.flush()
            print(f"[BMS-CSV] Logging Daly BMS telemetry to {self.path}", flush=True)
            return True
        except Exception as exc:
            sys.stderr.write(f"[BMS-CSV] Unable to open CSV log: {exc}\n")
            self.close()
            return False

    def write_status(self, status: Dict[str, Any]) -> bool:
        csv_values = status.get("csv_values")
        if not csv_values or len(csv_values) != len(HEADERS) - 1:
            return False
        if not self._ensure_open():
            return False
        try:
            timestamp = status.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._writer.writerow([timestamp] + list(csv_values))
            self._file.flush()
            return True
        except Exception as exc:
            sys.stderr.write(f"[BMS-CSV] Write error: {exc}\n")
            return False

    def close(self) -> None:
        if self._file:
            try:
                self._file.close()
            except Exception:
                pass
        self._file = None
        self._writer = None


def _atomic_write_text(path: str, content: str) -> None:
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_bms_", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(tmp_path, 0o644)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def write_battery_status(soc_pct: float, full_info: Optional[Dict[str, Any]] = None) -> None:
    """Atomically write battery percentage and JSON diagnostics for railgui25.py."""
    int_pct = max(0, min(100, int(round(float(soc_pct)))))
    is_chg = False
    if full_info:
        try:
            is_chg = (
                str(full_info.get("state", "")).lower() == "charging"
                or str(full_info.get("state_label", "")).lower() == "charging"
                or int(full_info.get("charger_status", 0)) == 1
                or float(full_info.get("current_a", 0.0)) > 0.02
            )
        except Exception:
            is_chg = False
    pct_str = f"{int_pct}+\n" if is_chg else f"{int_pct}\n"
    for path in BATTERY_PCT_PATHS:
        try:
            _atomic_write_text(path, pct_str)
        except Exception:
            pass

    if full_info is not None:
        try:
            payload = {k: v for k, v in full_info.items() if k != "csv_values"}
            _atomic_write_text(BATTERY_JSON_PATH, json.dumps(payload, indent=2))
        except Exception:
            pass


def write_disconnected_status(
    reason: str = "No BMS / Pico detected",
    port: Optional[str] = None,
    stale: bool = False,
    last_good: Optional[Dict[str, Any]] = None,
) -> None:
    """Publish explicit disconnected/stale JSON and clear stale pct files."""
    if not stale:
        for path in BATTERY_PCT_PATHS:
            try:
                if os.path.exists(path):
                    os.unlink(path)
            except OSError:
                pass

    payload: Dict[str, Any] = {
        "connected": False,
        "stale": bool(stale),
        "port": port,
        "error": reason,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "unix_ts": round(time.time(), 3),
    }
    if last_good:
        payload["last_soc_pct"] = last_good.get("soc_pct")
        payload["last_total_voltage_v"] = last_good.get("total_voltage_v")
        payload["last_timestamp"] = last_good.get("timestamp")
    try:
        _atomic_write_text(BATTERY_JSON_PATH, json.dumps(payload, indent=2))
    except Exception:
        pass


def _log_status_line(status: Dict[str, Any]) -> None:
    soc = status.get("soc_pct", 0.0)
    volt = status.get("total_voltage_v", 0.0)
    curr = status.get("current_a", 0.0)
    state = status.get("state_label", "Unknown")
    faults = status.get("faults") or []
    fault_note = f" | FAULTS={','.join(faults)}" if faults else ""
    print(
        f"[BMS:{status.get('source')}] Port={status.get('port')} | "
        f"SOC={soc:.1f}% | V={volt:.2f}V | I={curr:.2f}A | "
        f"State={state}{fault_note}",
        flush=True,
    )


def _run_pico_session(
    port: str,
    baudrate: int,
    csv_logger: Optional[DalyCSVLogger],
    stale_seconds: float,
) -> None:
    """Keep Pico USB serial open and consume 115200-baud ``DATA,...`` records."""
    print(f"[BMS-Pico] Opening {port} at {baudrate} baud...", flush=True)
    ser = serial.Serial(port, baudrate=baudrate, timeout=1.0)
    last_packet_ts = time.monotonic()
    last_status: Optional[Dict[str, Any]] = None
    marked_stale = False
    try:
        time.sleep(0.25)
        while True:
            raw = ser.readline()
            now = time.monotonic()
            if raw:
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                if line.startswith("DATA,"):
                    status = parse_pico_data_line(line, port=port)
                    if status:
                        last_status = status
                        last_packet_ts = now
                        marked_stale = False
                        write_battery_status(status["soc_pct"], status)
                        if csv_logger:
                            csv_logger.write_status(status)
                        _log_status_line(status)
                    else:
                        print(f"[BMS-Pico] Invalid DATA record ignored: {line[:80]}", flush=True)
                else:
                    print(f"[BMS-Pico] {line}", flush=True)
            else:
                if now - last_packet_ts > stale_seconds and not marked_stale:
                    marked_stale = True
                    msg = f"No DATA packet from Pico in {int(stale_seconds)}s"
                    print(f"[BMS-Pico] {msg}", flush=True)
                    write_disconnected_status(
                        msg, port=port, stale=True, last_good=last_status
                    )
    finally:
        try:
            ser.close()
        except Exception:
            pass


def _probe_pico_text_on_uart(candidates: Sequence[Tuple[str, str]], baudrate: int, listen_s: float = 2.0) -> Optional[str]:
    """Listen briefly on non-Pico UART candidates in case a Pico is wired via UART TX/RX."""
    for cand_port, hint in candidates:
        if hint == "pico":
            continue
        try:
            ser = serial.Serial(cand_port, baudrate=baudrate, timeout=0.5)
            try:
                end_at = time.monotonic() + listen_s
                while time.monotonic() < end_at:
                    raw = ser.readline()
                    if raw:
                        line = raw.decode("utf-8", errors="ignore").strip()
                        if line.startswith("DATA,") and parse_pico_data_line(line, port=cand_port):
                            return cand_port
            finally:
                ser.close()
        except Exception:
            continue
    return None


def run_daemon(
    poll_interval: float = 5.0,
    port: Optional[str] = None,
    pico_baud: int = PICO_BAUD,
    daly_baud: int = DALY_BAUD,
    log_dir: Optional[str] = None,
    enable_csv: bool = True,
) -> None:
    """Continuous background daemon monitoring Pico USB stream or direct Daly UART."""
    print("[BMS] Starting Daly BMS monitor...", flush=True)
    write_disconnected_status("Scanning for Daly BMS / Raspberry Pi Pico...")
    csv_logger = DalyCSVLogger(log_dir) if enable_csv else None

    if serial is None:
        msg = "pyserial is not installed; battery telemetry unavailable."
        print(f"[BMS] {msg}", file=sys.stderr, flush=True)
        write_disconnected_status(msg)
        while True:
            time.sleep(30.0)

    last_csv_ts = 0.0
    last_scan_log_ts = 0.0

    try:
        while True:
            candidates = discover_candidate_ports(port)
            handled = False

            for cand_port, hint in candidates:
                if hint == "pico":
                    try:
                        handled = True
                        _run_pico_session(
                            cand_port,
                            baudrate=pico_baud,
                            csv_logger=csv_logger,
                            stale_seconds=DEFAULT_STALE_SECONDS,
                        )
                    except Exception as exc:
                        print(f"[BMS-Pico] Disconnected from {cand_port}: {exc}", flush=True)
                        write_disconnected_status(f"Pico disconnected ({cand_port})", port=cand_port)
                        time.sleep(2.0)
                    break

            if handled:
                continue

            # Try direct Daly V1.2 UART/RS485 candidates (or explicit port in direct mode)
            bms = DalyBMS(port=port, baudrate=daly_baud)
            detected_port = bms.find_port() if not port else port
            if detected_port:
                bms.port = detected_port
                status = bms.read_status()
                if status.get("connected"):
                    misses = 0
                    print(f"[BMS-UART] Connected direct Daly BMS on {detected_port} at {daly_baud} baud", flush=True)
                    while misses < 3:
                        if misses > 0:
                            status = bms.read_status()
                        if status.get("connected"):
                            misses = 0
                            write_battery_status(status["soc_pct"], status)
                            now = time.monotonic()
                            if csv_logger and (now - last_csv_ts >= DEFAULT_CSV_INTERVAL):
                                csv_logger.write_status(status)
                                last_csv_ts = now
                            _log_status_line(status)
                        else:
                            misses += 1
                        time.sleep(max(1.0, float(poll_interval)))
                        if misses == 0:
                            status = bms.read_status()

                    bms.close()
                    write_disconnected_status(f"Daly BMS lost on {detected_port}", port=detected_port)
                    print(f"[BMS-UART] Lost response on {detected_port}; re-scanning...", flush=True)
                    continue
                bms.close()

            # Check whether a Pico UART DATA,... stream is wired onto a hardware UART (e.g. /dev/ttyS5)
            uart_pico = _probe_pico_text_on_uart(candidates, baudrate=pico_baud, listen_s=2.0)
            if uart_pico:
                try:
                    _run_pico_session(
                        uart_pico,
                        baudrate=pico_baud,
                        csv_logger=csv_logger,
                        stale_seconds=DEFAULT_STALE_SECONDS,
                    )
                except Exception as exc:
                    print(f"[BMS-Pico] Disconnected from {uart_pico}: {exc}", flush=True)
                    write_disconnected_status(f"Pico disconnected ({uart_pico})", port=uart_pico)
                continue

            now = time.monotonic()
            if now - last_scan_log_ts >= 30.0:
                desc = ", ".join(f"{p}({h})" for p, h in candidates) or "none"
                print(f"[BMS] Waiting for Pico USB (/dev/ttyACM*) or Daly UART... scanned=[{desc}]", flush=True)
                last_scan_log_ts = now

            write_disconnected_status("Waiting for Pico USB or Daly BMS connection")
            time.sleep(3.0)
    except KeyboardInterrupt:
        print("\n[BMS] Monitor stopped by user.", flush=True)
    finally:
        if csv_logger:
            csv_logger.close()


def _read_once(port: Optional[str], pico_baud: int, daly_baud: int, timeout_s: float) -> Dict[str, Any]:
    if serial is None:
        return {"connected": False, "error": "pyserial not installed"}

    for cand_port, hint in discover_candidate_ports(port):
        if hint == "pico":
            try:
                ser = serial.Serial(cand_port, baudrate=pico_baud, timeout=1.0)
                deadline = time.monotonic() + timeout_s
                try:
                    while time.monotonic() < deadline:
                        line = ser.readline().decode("utf-8", errors="ignore").strip()
                        if line.startswith("DATA,"):
                            parsed = parse_pico_data_line(line, port=cand_port)
                            if parsed:
                                return parsed
                finally:
                    ser.close()
            except Exception:
                continue

    bms = DalyBMS(port=port, baudrate=daly_baud)
    return bms.read_status()


def main() -> None:
    parser = argparse.ArgumentParser(description="Daly BMS Pico USB / UART Reader & Logger")
    parser.add_argument("--port", help="Serial port (e.g. /dev/ttyACM0, COM4, /dev/ttyUSB0, /dev/ttyS5)")
    parser.add_argument("--daemon", action="store_true", help="Run background monitor daemon")
    parser.add_argument("--interval", type=float, default=5.0, help="Direct UART polling interval (s)")
    parser.add_argument("--pico-baud", type=int, default=PICO_BAUD, help="Pico USB serial baud (default 115200)")
    parser.add_argument("--baud", type=int, default=DALY_BAUD, help="Direct Daly UART baud (default 9600)")
    parser.add_argument("--log-dir", default=None, help="Directory for DALY_BMS_*.csv logs")
    parser.add_argument("--no-csv", action="store_true", help="Disable CSV logging in daemon mode")
    parser.add_argument("--test", action="store_true", help="Read once and print JSON status")
    parser.add_argument("--timeout", type=float, default=5.0, help="Wait timeout for single Pico DATA packet")
    parser.add_argument("--mock", type=float, help="Write mock battery percentage for GUI testing")

    args = parser.parse_args()

    if args.mock is not None:
        pct = max(0.0, min(100.0, float(args.mock)))
        mock_status = build_battery_status(
            pack_voltage_v=25.6,
            current_a=-1.8,
            soc_pct=pct,
            remaining_mah=int(pct * 200),
            max_cell_mv=3315,
            max_cell_num=3,
            min_cell_mv=3298,
            min_cell_num=6,
            cells_v=[3.305, 3.308, 3.315, 3.302, 3.304, 3.298, 3.306, 3.310],
            temperatures_c=[28.0, 29.0, 27.5, -999.0],
            state_code=2,
            charge_mos=1,
            discharge_mos=1,
            bms_life=42,
            cell_count=8,
            temp_sensor_count=3,
            charger_status=0,
            load_status=1,
            port="mock",
            source="mock",
            mock=True,
        )
        write_battery_status(pct, mock_status)
        print(f"[BMS] Wrote mock battery: {int(round(pct))}% (Color: {mock_status['color'].upper()})", flush=True)
        return

    if args.daemon:
        run_daemon(
            poll_interval=args.interval,
            port=args.port,
            pico_baud=args.pico_baud,
            daly_baud=args.baud,
            log_dir=args.log_dir,
            enable_csv=not args.no_csv,
        )
        return

    status = _read_once(args.port, args.pico_baud, args.baud, args.timeout)
    printable = {k: v for k, v in status.items() if k != "csv_values"}
    print(json.dumps(printable, indent=2), flush=True)
    if status.get("connected"):
        write_battery_status(status["soc_pct"], status)
        print(f"\nBattery percentage updated: {int(round(status['soc_pct']))}%", flush=True)


if __name__ == "__main__":
    main()
