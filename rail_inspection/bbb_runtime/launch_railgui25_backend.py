#!/usr/bin/env python3
"""Runtime wrapper that keeps railgui25.py untouched and feeds it from shared memory."""

import argparse
import functools
import json
import math
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


RUNTIME_DIR = Path(__file__).resolve().parent
PROJECT_DIR = RUNTIME_DIR.parent
if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import railgui25 as gui_app
from backend_bridge import SharedMemoryBridge, compute_inclination_factor, format_diag

_BASE_LOAD_CFG = gui_app.load_cfg

CSV_FLUSH_ROWS = 50
UI_REFRESH_MS = 100
DEFAULT_CLOUD_ROOT = "https://lwtmt-cloud-backend.onrender.com/api/survey"
CLOUD_RETRIES = 3
CLOUD_UPLOAD_TIMEOUT_SECS = 300
GAUGE_NOMINAL_MM = 1676.0
LASER_CAL_FILE = Path(os.environ.get("RAIL_LASER_CAL_FILE", PROJECT_DIR / "laser_cal.json"))
LASER_ADC_MAX_RAW = 4095.0
LASER_CAL_TOLERANCE_PCT = 15.0
LASER_CAL_MAX_SPREAD = 200
LASER_CAL_SAMPLES = 50
LASER_CAL_SAMPLE_INTERVAL_MS = 200
LASER_RAW_PLUS80 = 1.35 / 1.8 * LASER_ADC_MAX_RAW
LASER_RAW_ZERO = LASER_RAW_PLUS80 / 2.0
LASER_RAW_MINUS80 = 0.0
LASER_ENDPOINT_TOLERANCE_PCT = 1.0
LASER_DISPLAY_MIN_MM = 120.0
LASER_DISPLAY_MAX_MM = 280.0
INCL_CAL_FILE = Path(os.environ.get(
    "RAIL_INCLINOMETER_CAL_FILE", PROJECT_DIR / "inclinometer_cal.json"
))
INCL_CAL_MAX_PKPK_MM = 1.0
INCL_CAL_SAMPLES = 100
INCL_CAL_SAMPLE_INTERVAL_S = 0.05
LOG_DIR = RUNTIME_DIR / "logs"
STATUS_FILE = LOG_DIR / "cloud_status.json"
QUEUE_FILE = LOG_DIR / "cloud_queue.json"
CSV_FIELDS = [
    "Sample No",
    "Date & Time",
    "Name",
    "Designation",
    "Station No",
    "Station Code",
    "Chainage",
    "Loop/Line Siding",
    "Turn-out No",
    "Curve No",
    "Level Crossing No",
    "Hectometer Post",
    "Bridge (Start)",
    "Bridge (End)",
    "Level Crossing (LC) In",
    "Level Crossing (LC) Out",
    "Kilometer Post (KM)",
    "Points & Crossing (P&C)",
    "Curve-In",
    "Curve Out",
    "OHE Mast (OHEM) Location",
    "Switch Expansion Joint (SEJ)",
    "Latitude",
    "Longitude",
    "Distance",
    "Gauge",
    "Crosslevel",
    "Twist",
]
STATION_REF_PRIORITY = [
    ("Station No", "Station"),
    ("Station Code", "Station"),
    ("Curve No", "Curve"),
    ("Level Crossing No", "Level crossing"),
    ("Hectometer Post", "Hectometer Post"),
]


def _normalize_cloud_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        value = DEFAULT_CLOUD_ROOT
    value = value.rstrip("/")
    if value.endswith("/api/survey"):
        return value
    return value + "/api/survey"


def _cloud_root(url: str) -> str:
    api_url = _normalize_cloud_url(url)
    if api_url.endswith("/api/survey"):
        return api_url[:-11] or "/"
    return api_url


CLOUD_URL = _normalize_cloud_url(os.environ.get("RAIL_CLOUD_URL", DEFAULT_CLOUD_ROOT))
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _read_queue() -> list:
    if not QUEUE_FILE.exists():
        return []
    try:
        data = json.loads(QUEUE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [str(x) for x in data if isinstance(x, str)]
    except Exception:
        pass
    return []


def _write_queue(items: list) -> None:
    QUEUE_FILE.write_text(json.dumps(items, indent=2), encoding="utf-8")


def _queue_csv(path: str) -> None:
    if not path:
        return
    items = _read_queue()
    if path not in items:
        items.append(path)
        _write_queue(items)


def _write_cloud_status(
    ok: bool,
    message: str,
    csv_path: str = "",
    queued: bool = False,
    state: str = "",
) -> None:
    payload = {
        "timestamp": int(time.time()),
        "ok": bool(ok),
        "state": state or ("success" if ok else "failed"),
        "queued": bool(queued),
        "message": message,
        "csv_path": csv_path or "",
        "cloud_url": CLOUD_URL,
    }
    STATUS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[Cloud] {message}")


def _build_payload(csv_path: str):
    with open(csv_path, newline="") as handle:
        rows = list(gui_app.csv.DictReader(handle))
    station_no = ""
    for row in rows:
        station_no = (
            row.get("Station No")
            or row.get("Station Code")
            or row.get("station_no")
            or row.get("stationCode")
            or row.get("station")
            or ""
        ).strip()
        if station_no:
            break
    if station_no:
        for row in rows:
            row.setdefault("Station No", station_no)
            row.setdefault("station_no", station_no)
            row.setdefault("stationCode", station_no)
    body = json.dumps({
        "filename": os.path.basename(csv_path),
        "station_no": station_no,
        "stationCode": station_no,
        "station_code": station_no,
        "station": station_no,
        "data": rows,
    }).encode("utf-8")
    return body, len(rows)


def _post_payload(body: bytes, timeout: int = CLOUD_UPLOAD_TIMEOUT_SECS):
    with tempfile.NamedTemporaryFile("wb", delete=False) as handle:
        handle.write(body)
        payload_path = handle.name
    try:
        result = subprocess.run(
            [
                "curl",
                "--fail",
                "--silent",
                "--show-error",
                "--max-time",
                str(int(timeout)),
                "-H",
                "Content-Type:application/json",
                "--data-binary",
                f"@{payload_path}",
                CLOUD_URL,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"curl upload failed ({result.returncode}): {detail}")
    finally:
        try:
            os.unlink(payload_path)
        except OSError:
            pass


def _format_cloud_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        return f"HTTP {exc.code}: {detail}" if detail else f"HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, socket.gaierror) and getattr(reason, "errno", None) == -3:
            return "Cloud DNS error (errno -3): host name resolution failed"
    return str(exc)


def _flush_cloud_queue(max_files: int = 1) -> int:
    queue = _read_queue()
    if not queue:
        return 0
    remaining = []
    uploaded = 0
    for index, path in enumerate(queue):
        if not os.path.exists(path):
            continue
        if uploaded >= max_files:
            remaining.append(path)
            continue
        try:
            body, rows = _build_payload(path)
            _post_payload(body)
            uploaded += 1
            print(
                f"[Cloud] Uploaded queued file ({rows} rows): "
                f"{os.path.basename(path)}"
            )
        except Exception:
            remaining.append(path)
            remaining.extend(
                queued_path for queued_path in queue[index + 1:]
                if os.path.exists(queued_path)
            )
            break
    _write_queue(remaining)
    return len(remaining)


@functools.lru_cache(maxsize=512)
def _sanitize_css(text: str) -> str:
    if not any(token in text for token in ("letter-spacing", "line-spacing", "letter spacing")):
        return text
    text = re.sub(r"letter-spacing\s*:[^;]+;?", "", text)
    text = re.sub(r"line-spacing\s*:[^;]+;?", "", text)
    text = re.sub(r"letter\s+spacing\s*:[^;]+;?", "", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text


def _laser_calibration_state():
    if not LASER_CAL_FILE.exists():
        return {"factor": 1.0, "calibrated": False, "references": None}
    try:
        data = json.loads(LASER_CAL_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("calibration data must be a JSON object")
        reference_keys = ("raw_at_plus80", "raw_at_zero", "raw_at_minus80")
        has_any_reference = any(key in data for key in reference_keys)
        if has_any_reference and not all(key in data for key in reference_keys):
            raise ValueError("three-point calibration is incomplete")
        if all(key in data for key in reference_keys):
            references = {key: float(data[key]) for key in reference_keys}
            if not all(math.isfinite(value) and 0.0 <= value <= LASER_ADC_MAX_RAW
                       for value in references.values()):
                raise ValueError("three-point raw references must be finite ADC values")
            if not references["raw_at_plus80"] > references["raw_at_zero"] > references["raw_at_minus80"]:
                raise ValueError("three-point raw references must decrease from +80 through -80")
            return {"factor": 1.0, "calibrated": True, "references": references}
        factor = float(data.get("cal_factor", data.get("factor")))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot read laser calibration file {LASER_CAL_FILE}: {exc}") from exc
    min_factor = 1.0 / (1.0 + LASER_CAL_TOLERANCE_PCT / 100.0)
    max_factor = 1.0 / (1.0 - LASER_CAL_TOLERANCE_PCT / 100.0)
    if not math.isfinite(factor) or not min_factor <= factor <= max_factor:
        raise RuntimeError(f"Invalid laser calibration factor in {LASER_CAL_FILE}: {factor}")
    return {"factor": factor, "calibrated": True, "references": None}


def _laser_gauge_from_raw(raw, raw_plus80, raw_zero, raw_minus80):
    if raw < raw_minus80 or raw > raw_plus80:
        return 0.0
    if raw >= raw_zero:
        return LASER_DISPLAY_MIN_MM + (raw_plus80 - raw) * (
            200.0 - LASER_DISPLAY_MIN_MM
        ) / (raw_plus80 - raw_zero)
    return 200.0 + (raw_zero - raw) * (
        LASER_DISPLAY_MAX_MM - 200.0
    ) / (raw_zero - raw_minus80)


class SharedMemorySensorThread(gui_app.SensorThread):
    def __init__(self, cfg):
        super().__init__(cfg)
        self._stop = False
        self._bridge = SharedMemoryBridge(cfg)
        self._last_fault = ""
        self._needs_zero = False
        self._laser_cal_samples = []
        self._laser_cal_point = None

    def laser_read_raw(self):
        adc_path = os.environ.get("RAIL_ADC_PATH", "/sys/bus/iio/devices/iio:device0/in_voltage0_raw")
        try:
            raw = int(Path(adc_path).read_text(encoding="ascii").strip())
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Cannot read laser ADC value from {adc_path}: {exc}") from exc
        if not 0 <= raw <= int(LASER_ADC_MAX_RAW):
            raise RuntimeError(f"Laser ADC value {raw} is outside 0..{int(LASER_ADC_MAX_RAW)}")
        return raw

    def laser_gauge_readout(self):
        raw = self.laser_read_raw()
        calibration = _laser_calibration_state()
        if calibration["references"] is not None:
            raw_plus80 = calibration["references"]["raw_at_plus80"]
            raw_zero = calibration["references"]["raw_at_zero"]
            raw_minus80 = calibration["references"]["raw_at_minus80"]
            corrected = raw
            tolerance = raw_plus80 * LASER_ENDPOINT_TOLERANCE_PCT / 100.0
            if raw < raw_minus80 - tolerance or raw > raw_plus80 + tolerance:
                gauge_mm = 0.0
            else:
                corrected = max(raw_minus80, min(raw_plus80, raw))
                gauge_mm = _laser_gauge_from_raw(
                    corrected, raw_plus80, raw_zero, raw_minus80
                )
        else:
            corrected = raw * calibration["factor"]
            if calibration["calibrated"]:
                raw_plus80, raw_zero, raw_minus80 = (
                    LASER_RAW_PLUS80, LASER_RAW_ZERO, LASER_RAW_MINUS80
                )
            else:
                raw_plus80 = float(os.environ.get("RAIL_LASER_RAW_AT_PLUS80", "2957.5"))
                raw_zero = float(os.environ.get("RAIL_LASER_RAW_AT_ZERO", "1365.0"))
                raw_minus80 = float(os.environ.get("RAIL_LASER_RAW_AT_MINUS80", "0.0"))
            endpoint_tolerance = raw_plus80 * LASER_ENDPOINT_TOLERANCE_PCT / 100.0
            if (corrected < raw_minus80 - endpoint_tolerance or
                    corrected > raw_plus80 + endpoint_tolerance):
                gauge_mm = 0.0
            else:
                corrected = max(raw_minus80, min(raw_plus80, corrected))
                gauge_mm = _laser_gauge_from_raw(
                    corrected, raw_plus80, raw_zero, raw_minus80
                )
        return {
            "raw": raw,
            "corrected_raw": corrected,
            "gauge_mm": gauge_mm,
            "calibrated": calibration["calibrated"],
            "factor": calibration["factor"],
            "calibration_type": (
                "three_point" if calibration["references"] is not None else "single_factor"
            ),
        }

    def begin_laser_calibration(self, point):
        if point not in ("plus80", "zero", "minus80"):
            raise ValueError(f"Unsupported laser calibration point: {point}")
        self._laser_cal_point = point
        self._laser_cal_samples = []

    def cancel_laser_calibration(self):
        self._laser_cal_point = None
        self._laser_cal_samples = []

    def sample_laser_calibration(self):
        if self._laser_cal_point is None:
            raise RuntimeError("No laser calibration capture is active")
        self._laser_cal_samples.append(self.laser_read_raw())
        return len(self._laser_cal_samples)

    def finish_laser_calibration(self):
        if self._laser_cal_point is None:
            raise RuntimeError("No laser calibration capture is active")
        point = self._laser_cal_point
        samples = self._laser_cal_samples
        self._laser_cal_point = None
        self._laser_cal_samples = []
        if len(samples) != LASER_CAL_SAMPLES:
            raise RuntimeError(f"Expected {LASER_CAL_SAMPLES} samples, received {len(samples)}")
        spread = max(samples) - min(samples)
        if spread > LASER_CAL_MAX_SPREAD:
            raise RuntimeError(
                f"ADC readings were unstable (spread {spread}; maximum {LASER_CAL_MAX_SPREAD})"
            )
        measured_raw = sum(samples) / len(samples)
        if not math.isfinite(measured_raw):
            raise RuntimeError("Calibration mean is not a finite ADC reading")
        return {
            "point": point,
            "measured_mean_raw": measured_raw,
            "sample_count": len(samples),
            "sample_min_raw": min(samples),
            "sample_max_raw": max(samples),
        }

    def save_laser_calibration(self, captures):
        point_to_key = {
            "plus80": "raw_at_plus80",
            "zero": "raw_at_zero",
            "minus80": "raw_at_minus80",
        }
        if set(captures) != set(point_to_key):
            raise RuntimeError("Capture +80, 0, and -80 before saving the three-point calibration")
        references = {}
        capture_data = {}
        for point, key in point_to_key.items():
            capture = captures[point]
            mean = float(capture["measured_mean_raw"])
            if not math.isfinite(mean) or not 0.0 <= mean <= LASER_ADC_MAX_RAW:
                raise RuntimeError(f"{point} capture mean is outside the ADC range")
            references[key] = mean
            capture_data[point] = {
                "measured_mean_raw": mean,
                "sample_count": int(capture["sample_count"]),
                "sample_min_raw": int(capture["sample_min_raw"]),
                "sample_max_raw": int(capture["sample_max_raw"]),
            }
        if not references["raw_at_plus80"] > references["raw_at_zero"] > references["raw_at_minus80"]:
            raise RuntimeError(
                "Raw readings must decrease in order from +80 to 0 to -80. "
                "Check point placement, sensor direction, and ADC signal."
            )
        LASER_CAL_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = LASER_CAL_FILE.with_name(LASER_CAL_FILE.name + ".tmp")
        payload = {
            "cal_factor": 1.0,
            "calibration_type": "three_point",
            **references,
            "captures": capture_data,
        }
        try:
            temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, LASER_CAL_FILE)
        except OSError as exc:
            raise RuntimeError(f"Cannot save laser calibration to {LASER_CAL_FILE}: {exc}") from exc
        return payload

    def stop(self):
        self._stop = True
        self._bridge.close()

    def reset(self):
        raw_total = self._raw_total
        super().reset()
        self._raw_total = raw_total
        self._bridge.reset_display_reference()
        self._needs_zero = False

    def begin_encoder_calibration(self):
        return self._bridge.begin_encoder_calibration()

    def encoder_calibration_readout(self, start_count):
        return self._bridge.encoder_calibration_readout(start_count)

    def finish_encoder_calibration(self, start_count, true_m):
        result = self._bridge.finish_encoder_calibration(start_count, true_m)
        gui_app.save_cfg(self.cfg)
        return result

    def set_encoder_factor(self, factor, calibrated=True):
        self._bridge.set_encoder_factor(factor, calibrated)
        gui_app.save_cfg(self.cfg)

    def _emit_fault(self, message: str) -> None:
        if message != self._last_fault:
            self._last_fault = message
            self.fault.emit(message)

    def run(self):
        poll_ms = max(1, int(1000 / 10))
        while not self._stop:
            try:
                if self._needs_zero:
                    self._emit_fault("Calibrating inclinometer zero...")
                    self._bridge.calibrate_zero()
                    self._needs_zero = False
                    self._emit_fault("")
            except Exception as exc:
                self._emit_fault(str(exc))
                time.sleep(0.5)
                continue

            try:
                if self.active:
                    sample = self._bridge.next_display_sample()
                    self._raw_total = int(sample["encoder_count"])
                    self.motion.emit(self._bridge.is_moving(sample))
                    self._emit_fault("" if sample["scl_ok"] else "SCL3300 backend not ready")
                    self.data_ready.emit(sample)
                else:
                    # Keep the hardware path warm while STOP is selected, but
                    # do not update dashboard values or CSV samples.
                    frame = self._bridge.read_next_frame(require_new=True)
                    self._raw_total = frame.encoder_count
                    self._emit_fault("" if frame.scl_ok else "SCL3300 backend not ready")
            except Exception as exc:
                self._emit_fault(str(exc))
                time.sleep(0.5)
                continue
            self.msleep(poll_ms)


def sanitize_stylesheet() -> None:
    gui_app.SS = _sanitize_css(gui_app.SS)


def patch_qt_stylesheet_calls() -> None:
    original = gui_app.QWidget.setStyleSheet

    def patched(self, style):
        if not isinstance(style, str):
            return original(self, style)
        return original(self, _sanitize_css(style))

    gui_app.QWidget.setStyleSheet = patched


def safe_apply_screen_geometry(self) -> None:
    app = gui_app.QApplication.instance()
    screen = app.primaryScreen() if app is not None else None
    if screen is None:
        return
    geom = screen.availableGeometry()
    self.setGeometry(geom)
    self.setMinimumSize(geom.size())
    gui_app.W = geom.width()
    gui_app.H = geom.height()
    gui_app.SCREEN_W = gui_app.W
    gui_app.SCREEN_H = gui_app.H


class BufferedCSVLogger(gui_app.CSVLogger):
    def start(self, directory, hl_sec=30):
        os.makedirs(directory, exist_ok=True)
        safe_ts = gui_app.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"BLE_{safe_ts}.csv"
        station_values = dict(getattr(self, "_station_values", {}) or {})
        ref_type = getattr(self, "_ref_type", "")
        ref_value = getattr(self, "_ref_value", "")
        self.path = os.path.join(directory, filename)
        self._f = open(self.path, "w", newline="", encoding="utf-8")
        self._w = gui_app.csv.DictWriter(self._f, fieldnames=CSV_FIELDS)
        self._w.writeheader()
        self._rows = []
        self._hl_s = hl_sec
        self._next_hl = time.time() + hl_sec
        self._mark = []
        self.count = 0
        self._unflushed = 0
        self._station_values = station_values
        self._ref_type = ref_type
        self._ref_value = ref_value

    def write(self, d):
        if not self._w:
            return
        track_app = getattr(self, "_track_app", None)
        if track_app is not None:
            try:
                _apply_station_reference(track_app)
            except Exception:
                pass
        cross = d.get("cross", 0)
        twist = d.get("twist", 0)
        row = {
            "Sample No": self.count + 1,
            "Date & Time": gui_app.datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
            "Name": str(self._station_values.get("Name", "")),
            "Designation": str(self._station_values.get("Designation", "")),
            "Station No": str(self._station_values.get("Station No", "")),
            "Station Code": str(self._station_values.get("Station Code", "")),
            "Chainage": str(self._station_values.get("Chainage", "")),
            "Loop/Line Siding": str(self._station_values.get("Loop/Line Siding", "")),
            "Turn-out No": str(self._station_values.get("Turn-out No", "")),
            "Curve No": str(self._station_values.get("Curve No", "")),
            "Level Crossing No": str(self._station_values.get("Level Crossing No", "")),
            "Hectometer Post": str(self._station_values.get("Hectometer Post", "")),
            "Bridge (Start)": str(self._station_values.get("Bridge (Start)", "")),
            "Bridge (End)": str(self._station_values.get("Bridge (End)", "")),
            "Level Crossing (LC) In": str(self._station_values.get("Level Crossing (LC) In", "")),
            "Level Crossing (LC) Out": str(self._station_values.get("Level Crossing (LC) Out", "")),
            "Kilometer Post (KM)": str(self._station_values.get("Kilometer Post (KM)", "")),
            "Points & Crossing (P&C)": str(self._station_values.get("Points & Crossing (P&C)", "")),
            "Curve-In": str(self._station_values.get("Curve-In", "")),
            "Curve Out": str(self._station_values.get("Curve Out", "")),
            "OHE Mast (OHEM) Location": str(self._station_values.get("OHE Mast (OHEM) Location", "")),
            "Switch Expansion Joint (SEJ)": str(self._station_values.get("Switch Expansion Joint (SEJ)", "")),
            "Latitude": f"{float(d.get('lat', 0.0)):.5f}",
            "Longitude": f"{float(d.get('lon', 0.0)):.5f}",
            "Distance": f"{float(d.get('dist', 0.0)):.2f}",
            "Gauge": f"{float(d.get('gauge', 0.0)):.0f}",
            "Crosslevel": f"{float(cross):.0f}",
            "Twist": f"{float(twist):.0f}",
        }
        self._rows.append((time.time(), row))
        self._w.writerow(row)
        self._unflushed += 1
        if self._unflushed >= CSV_FLUSH_ROWS:
            self._f.flush()
            self._unflushed = 0
        self.count += 1

    def stop(self):
        if self._f:
            self._f.flush()
        saved_path = self.path
        super().stop()
        return saved_path


class CloudPushThread(gui_app.QThread):
    done = gui_app.pyqtSignal(bool, str)

    def __init__(self, csv_path, parent=None):
        super().__init__(parent)
        self.csv_path = csv_path

    def run(self):
        if not CLOUD_URL:
            self.done.emit(False, "Cloud URL not configured")
            return
        if not self.csv_path or not os.path.exists(self.csv_path):
            self.done.emit(False, "No CSV file to upload")
            return
        try:
            body, rows_count = _build_payload(self.csv_path)
        except Exception as exc:
            msg = f"CSV read failed: {exc}"
            _write_cloud_status(False, msg, self.csv_path)
            self.done.emit(False, msg)
            return

        filename = os.path.basename(self.csv_path)
        _write_cloud_status(
            False, f"Uploading current survey: {filename}", self.csv_path,
            state="uploading",
        )
        for attempt in range(CLOUD_RETRIES):
            try:
                _post_payload(body)
                msg = f"Cloud upload successful: {filename} ({rows_count} rows)."
                _write_cloud_status(True, msg, self.csv_path)
                self.done.emit(True, msg)
                try:
                    remaining = _flush_cloud_queue(max_files=1)
                    if remaining:
                        print(
                            f"[Cloud] Current survey uploaded; "
                            f"{remaining} older file(s) remain queued."
                        )
                except Exception as exc:
                    print(f"[Cloud] Current survey uploaded; queue retry failed: {exc}")
                return
            except Exception as exc:
                if attempt == CLOUD_RETRIES - 1:
                    reason = _format_cloud_error(exc)
                    _queue_csv(self.csv_path)
                    queued_msg = (
                        f"Cloud upload failed for {filename}: {reason}. "
                        "Saved locally and queued for retry."
                    )
                    _write_cloud_status(False, queued_msg, self.csv_path, queued=True)
                    self.done.emit(False, queued_msg)
                    return
                time.sleep(2 ** attempt)


class RuntimeNetThread(gui_app.NetThread):
    def __init__(self, cfg):
        super().__init__(cfg)
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        while not self._stop:
            self.status.emit(self._lte(), self._ping())
            self.sleep(15)

    def _ping(self):
        if gui_app.HW_SIM or not CLOUD_URL:
            return True
        try:
            health_url = _cloud_root(CLOUD_URL).rstrip("/") + "/health"
            request = urllib.request.Request(
                health_url,
                method="GET",
                headers={"User-Agent": "RailInspection-BBB/1.0"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status == 200
        except Exception:
            # The BBB's older OpenSSL build cannot always negotiate TLS with
            # the cloud host.  The top bar is an LTE indicator, so retain an
            # online state when the modem itself can reach the internet.
            try:
                result = subprocess.run(
                    [
                        "ping", "-c", "1", "-W", "2", "-I",
                        self.cfg.get("lte_iface", "usb2"), "1.1.1.1",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                return result.returncode == 0
            except Exception:
                return False


def save_inclinometer_calibration(calibration):
    INCL_CAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = INCL_CAL_FILE.with_name(INCL_CAL_FILE.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as output:
            json.dump(calibration, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(str(temporary), str(INCL_CAL_FILE))
    finally:
        if temporary.exists():
            temporary.unlink()


def load_runtime_cfg():
    cfg = _BASE_LOAD_CFG()
    if not INCL_CAL_FILE.exists():
        return cfg
    try:
        calibration = json.loads(INCL_CAL_FILE.read_text(encoding="utf-8"))
        offset = float(calibration["zero_offset_mm"])
        factor = float(calibration.get("scale_factor", 1.0))
        if not math.isfinite(offset) or not math.isfinite(factor):
            raise ValueError("offset and scale factor must be finite")
        if not 0.1 <= factor <= 5.0:
            raise ValueError("scale factor must be between 0.1 and 5.0")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Invalid inclinometer calibration file {INCL_CAL_FILE}: {exc}"
        ) from exc
    cfg["incl"].update({
        "offset": offset,
        "factor": factor,
        "calibrated": True,
        "reference_mm": float(calibration.get("target_ref_mm", 0.0)),
        "calibration_method": calibration.get("method", "external"),
    })
    return cfg


def configure_permissible_limits(cfg):
    """Apply rail_config.json limits to the live status colours."""
    def bands(value):
        return (None, None) if value is None else (float(value) * 0.8, float(value))
    limits = gui_app.permissible_limits(cfg)
    gui_app._THRESH.update({
        "gauge": bands(limits["Gauge"]),
        "cross": bands(limits["Crosslevel"]),
        "twist": bands(limits["Twist"]),
    })


class RuntimeInclinCal(gui_app.QWidget):
    saved = gui_app.pyqtSignal(str, dict)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self._bridge = None
        self._zero_captured = False
        self._reference_captured = False
        self._zero_stats = None
        self._reference_stats = None

        lay = gui_app.QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        lay.addWidget(gui_app._lbl(
            "Static calibration: hold the trolley still on a verified 0 mm fixture, "
            "capture ZERO, then capture a known signed reference (20-200 mm). "
            "Each capture uses 100 samples at 20 Hz and rejects spread above 1 mm. "
            "For same-spot 180-degree reversal, use the inclinometer monitor CLI.",
            "#555", 9))

        method = cfg["incl"].get("calibration_method", "")
        method_text = f" ({method})" if method else ""
        self._status = gui_app._lbl(
            f"Status: {'calibrated' if cfg['incl'].get('calibrated') else 'not calibrated'}"
            f"{method_text}", gui_app.AMBER, 9, True)
        lay.addWidget(self._status)

        self._live = gui_app._lbl("Live backend stats: --", "#4A5568", 9)
        lay.addWidget(self._live)

        row = gui_app.QHBoxLayout()
        row.addWidget(gui_app._lbl("Persistent zero offset:", "#4A5568", 10, True))
        self._offset_s = gui_app.Stepper(
            cfg["incl"].get("offset", 0.0), step=0.01, dec=3,
            lo=-500.0, hi=500.0, unit="mm", title="INCLINATION ZERO OFFSET")
        self._offset_s.changed.connect(self._offset_changed)
        row.addWidget(self._offset_s, 1)
        lay.addLayout(row)

        row = gui_app.QHBoxLayout()
        row.addWidget(gui_app._lbl("Calibration factor:", "#4A5568", 10, True))
        self._factor_s = gui_app.Stepper(
            cfg["incl"].get("factor", 1.0), step=0.001, dec=3,
            lo=0.1, hi=5.0, unit="x", title="INCLINATION SCALE FACTOR")
        self._factor_s.changed.connect(self._reference_invalidated)
        row.addWidget(self._factor_s, 1)
        lay.addLayout(row)

        row = gui_app.QHBoxLayout()
        row.addWidget(gui_app._lbl("Reference cross-level:", "#4A5568", 10, True))
        self._reference_s = gui_app.Stepper(
            cfg["incl"].get("reference_mm", 0.0), step=0.1, dec=1,
            lo=-200.0, hi=200.0, unit="mm", title="REFERENCE CROSS LEVEL")
        self._reference_s.changed.connect(self._reference_invalidated)
        row.addWidget(self._reference_s, 1)
        lay.addLayout(row)

        buttons = gui_app.QGridLayout()
        buttons.setSpacing(8)

        capture_zero = gui_app._btn("CAPTURE ZERO", "BA", 48)
        capture_zero.clicked.connect(self._capture_zero)
        buttons.addWidget(capture_zero, 0, 0)

        capture_ref = gui_app._btn("CAPTURE REFERENCE", "BA", 48)
        capture_ref.clicked.connect(self._capture_reference)
        buttons.addWidget(capture_ref, 0, 1)

        save = gui_app._btn("SAVE CALIBRATION", "BA", 48)
        save.clicked.connect(self._save)
        buttons.addWidget(save, 1, 0, 1, 2)

        lay.addLayout(buttons)
        lay.addStretch()

    def _bridge_for_cal(self):
        if self._bridge is None:
            self._bridge = SharedMemoryBridge({})
        return self._bridge

    def _capture_stats(self):
        self._status.setText("Status: capturing stationary inclinometer samples...")
        bridge = self._bridge_for_cal()
        stats = bridge.capture_raw_cross_stats(
            sample_count=INCL_CAL_SAMPLES,
            interval_s=INCL_CAL_SAMPLE_INTERVAL_S,
        )
        if stats["pkpk_mm"] > INCL_CAL_MAX_PKPK_MM:
            raise RuntimeError(
                f"Reading was not stable (peak-to-peak {stats['pkpk_mm']:.2f} mm; "
                f"limit {INCL_CAL_MAX_PKPK_MM:.1f} mm). Keep the trolley still and retry."
            )
        calibrated = (
            stats["mean_mm"] - self._offset_s.value()
        ) * self._factor_s.value()
        angle = math.degrees(math.asin(calibrated / GAUGE_NOMINAL_MM))
        self._live.setText(
            f"Capture: raw={stats['mean_mm']:.3f} mm  "
            f"corrected={calibrated:.3f} mm  angle={angle:.4f} deg  "
            f"spread={stats['pkpk_mm']:.3f} mm  n={stats['samples']}")
        return stats

    def _capture_zero(self):
        self._zero_captured = False
        self._reference_captured = False
        try:
            stats = self._capture_stats()
            self._offset_s.set_value(stats["mean_mm"])
            self._zero_stats = stats
            self._zero_captured = True
            self._status.setText(f"Status: zero captured at {stats['mean_mm']:.3f} mm")
        except (OSError, RuntimeError, ValueError) as exc:
            self._status.setText(f"Status: zero capture failed: {exc}")

    def _capture_reference(self):
        self._reference_captured = False
        if not self._zero_captured:
            self._status.setText("Status: capture zero on a verified level fixture first.")
            return
        try:
            stats = self._capture_stats()
            offset = self._offset_s.value()
            target = self._reference_s.value()
            factor = compute_inclination_factor(stats["mean_mm"], offset, target)
            self._factor_s.set_value(factor)
            self._reference_stats = stats
            self._reference_captured = True
            self._status.setText(
                f"Status: reference captured, factor set to {factor:.3f} for target {target:.1f} mm")
        except (OSError, RuntimeError, ValueError) as exc:
            self._status.setText(f"Status: reference capture failed: {exc}")

    def _save(self):
        if not self._zero_captured or not self._reference_captured:
            self._status.setText(
                "Status: capture a stable zero and a stable known nonzero reference before saving.")
            return
        calibration = {
            "zero_offset_mm": self._offset_s.value(),
            "zero_offset_deg": math.degrees(math.asin(
                self._offset_s.value() / GAUGE_NOMINAL_MM
            )),
            "scale_factor": self._factor_s.value(),
            "gauge_mm": GAUGE_NOMINAL_MM,
            "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
            "method": "ui_zero_reference",
            "samples": min(
                self._zero_stats["samples"], self._reference_stats["samples"]
            ),
            "zero_spread_mm": self._zero_stats["pkpk_mm"],
            "reference_spread_mm": self._reference_stats["pkpk_mm"],
            "target_ref_mm": self._reference_s.value(),
            "reference_mean_mm": self._reference_stats["mean_mm"],
        }
        try:
            save_inclinometer_calibration(calibration)
        except OSError as exc:
            self._status.setText(f"Status: could not save calibration file: {exc}")
            return
        self.cfg["incl"].update({
            "offset": self._offset_s.value(),
            "factor": self._factor_s.value(),
            "reference_mm": self._reference_s.value(),
            "calibrated": True,
            "calibration_method": "ui_zero_reference",
        })
        gui_app.save_cfg(self.cfg)
        self._status.setText("Status: inclination calibration saved")
        self.saved.emit("incl", self.cfg["incl"])

    def _offset_changed(self, _value):
        self._zero_captured = False
        self._reference_captured = False
        self._status.setText("Status: offset changed; capture zero and reference again.")

    def _reference_invalidated(self, _value):
        if self._zero_captured:
            self._reference_captured = False
            self._status.setText("Status: calibration value changed; capture reference again.")


def _refresh_latest_data(self):
    latest = getattr(self, "_latest_sensor_data", None)
    if latest is None:
        return
    if latest != getattr(self, "_rendered_sensor_data", None):
        self.dash.update_data(latest)
        self._rendered_sensor_data = dict(latest)
    if self.sensor.active and self.stack.currentWidget() is self.entry:
        self.entry.push_sensor_data(latest)
    session_state = (self.logger.count, self.sensor.active, self.logger.path or "")
    if session_state != getattr(self, "_rendered_session_state", None):
        self.dash.set_session(*session_state)
        self._rendered_session_state = session_state


def optimized_on_data(self, d):
    self._latest_sensor_data = d
    for key in self.history:
        if key in d:
            self.history[key].append(d[key])
            if len(self.history[key]) > 5000:
                self.history[key].pop(0)

    if self.sensor.active:
        _apply_station_reference(self)
        self.logger.write(d)


def optimized_metric_refresh(self, val):
    display = str(val)
    if getattr(self, "_last_display", None) == display:
        return

    warn, alarm = gui_app._THRESH.get(self.key, (None, None))
    dev = abs(float(val))
    if alarm is not None and dev >= alarm:
        state = ("alarm", gui_app.RED, "ALARM", "QFrame#Card{background:#FFEBEE; border:1px solid #DDE3EA; border-left:4px solid " + gui_app.RED + "; border-radius:10px;}", "⚠  ALARM")
    elif warn is not None and dev >= warn:
        state = ("warn", gui_app.WARN, "MONITOR", "QFrame#Card{background:#FFF3E0; border:1px solid #DDE3EA; border-left:4px solid " + gui_app.WARN + "; border-radius:10px;}", "△  WARN")
    else:
        state = ("nominal", self.color, "NOMINAL", "QFrame#Card{background:#FFFFFF; border:1px solid #DDE3EA; border-left:4px solid " + self.color + "; border-radius:10px;}", "")

    if getattr(self, "_last_visual_state", None) != state:
        _, color, badge, bg, alert = state
        self._apply_badge(badge, color)
        self._apply_val_style(color)
        self._apply_unit_style()
        self._alert.setText(alert)
        self.setStyleSheet(bg)
        self._last_visual_state = state

    self._val.setText(display)
    self._last_display = display


def optimized_dash_session(self, n, running, path=""):
    state = (n, running)
    if getattr(self, "_last_session_state", None) == state:
        return
    col = gui_app.NEON if running else "#8A94A6"
    icon = "●  REC" if running else "○  IDLE"
    self._stat.setText(f"{icon}  {n} pts")
    self._stat.setStyleSheet(
        f"color:{col}; font-family:'Roboto Mono','Courier New',monospace;"
        f" font-size:10pt; font-weight:500;")
    self._last_session_state = state


def optimized_entry_push(self, d):
    if d == getattr(self, "_last_sensor_data", None):
        return
    self._last_sensor_data = dict(d)
    for key, _, sensor_key, _, _, _ in gui_app._PARAM_TABLES:
        if sensor_key in d and key in self._tables:
            self._tables[key].push_value(d[sensor_key])


def _extract_station_reference(entry_page):
    try:
        values = entry_page._station_params.get_values()
        values.update(getattr(entry_page, "_feature_panel", {}).get_values() if hasattr(entry_page, "_feature_panel") else {})
    except Exception:
        return "", "", {}
    values = {k: str(v).strip() for k, v in values.items()}
    ref_type = ""
    ref_value = ""
    for key, label in STATION_REF_PRIORITY:
        if values.get(key):
            ref_type = label
            ref_value = values.get(key, "")
            break
    if not ref_type and values.get("Chainage"):
        ref_type = "Chainage"
        ref_value = values.get("Chainage", "")
    return ref_type, ref_value, values


def _entry_values_from_track_app(track_app):
    saved_values = dict(getattr(track_app, "_runtime_saved_entry_values", {}) or {})
    if saved_values:
        values = {k: str(v).strip() for k, v in saved_values.items()}
        ref_type = ""
        ref_value = ""
        for key, label in STATION_REF_PRIORITY:
            if values.get(key):
                ref_type = label
                ref_value = values.get(key, "")
                break
        if not ref_type and values.get("Chainage"):
            ref_type = "Chainage"
            ref_value = values.get("Chainage", "")
        return ref_type, ref_value, values
    if hasattr(track_app, "entry"):
        return _extract_station_reference(track_app.entry)
    return "", "", {}


def _apply_station_reference(track_app):
    ref_type, ref_value, values = _entry_values_from_track_app(track_app)
    track_app.logger.set_reference(ref_type, ref_value)
    track_app.logger._station_values = values
    station_no = values.get("Station No", "").strip()
    station_code = values.get("Station Code", "").strip()
    track_app.logger.set_station(station_no or station_code or "BLE")
    if hasattr(track_app, "dash"):
        track_app.dash.set_station_no(station_no)


def _runtime_save_entry(self):
    app = self.window()
    if not hasattr(app, "logger"):
        return
    if hasattr(app, "entry"):
        _, _, values = _extract_station_reference(app.entry)
        app._runtime_saved_entry_values = values
    _apply_station_reference(app)
    if hasattr(self, "_runtime_save_btn"):
        self._runtime_save_btn.setText("DATA ENTRY SAVED")
        gui_app.QTimer.singleShot(1800, lambda: self._runtime_save_btn.setText("SAVE DATA ENTRY"))
    if hasattr(app, "topbar"):
        app.topbar.push_error("")


def _runtime_tune_station_params(data_entry_page):
    station = getattr(data_entry_page, "_station_params", None)
    if station is None:
        return

    label_style = (
        "color:#5B6575; font-size:10.5pt; font-weight:700;"
        " background:transparent; border:none;"
    )
    field_empty = (
        "QPushButton { background:#F8FAFB; border:1px solid #C8D0DA; border-radius:8px;"
        " padding:0 10px; color:#94A3B8; font-size:10.5pt; text-align:left; }"
        "QPushButton:hover { background:#FFFFFF; border-color:#1565C0; }"
        "QPushButton:pressed { background:#EAF3FF; }"
    )
    field_filled = (
        "QPushButton { background:#F8FAFB; border:1px solid #C8D0DA; border-radius:8px;"
        " padding:0 10px; color:#1A2332; font-size:10.5pt; text-align:left; }"
        "QPushButton:hover { background:#FFFFFF; border-color:#1565C0; }"
        "QPushButton:pressed { background:#EAF3FF; }"
    )

    for row_layout in station.findChildren(gui_app.QHBoxLayout):
        if row_layout.count() < 2:
            continue
        label = row_layout.itemAt(0).widget()
        field = row_layout.itemAt(1).widget()
        if not isinstance(label, gui_app.QLabel):
            continue
        if not isinstance(field, gui_app.QPushButton):
            continue

        row_layout.setSpacing(10)
        label.setFixedWidth(108)
        label.setMinimumHeight(36)
        label.setWordWrap(True)
        label.setStyleSheet(label_style)
        field.setFixedHeight(32)
        current_text = field.text().strip()
        is_empty = current_text in {"", "Tap to enter", "Official name", "Designation"}
        field.setStyleSheet(field_empty if is_empty else field_filled)


def patched_data_entry_init(original_init):
    def wrapper(self):
        original_init(self)
        _runtime_tune_station_params(self)
        feature_panel = getattr(self, "_feature_panel", None)
        if feature_panel is not None:
            for field in getattr(feature_panel, "_fields", {}).values():
                field.setFixedHeight(32)
        root = self.layout()
        if root is None or root.count() < 3:
            return
        bottom_w = root.itemAt(2).widget()
        if bottom_w is None or bottom_w.layout() is None:
            return
        bottom_w.setFixedHeight(88)
        bottom_l = bottom_w.layout()
        back_btn = None
        for btn in self.findChildren(gui_app.QPushButton):
            if "BACK" in btn.text():
                back_btn = btn
                break
        btn_style = (
            f"QPushButton{{"
            f" background:{gui_app.CYAN_LT}; border:2px solid {gui_app.CYAN};"
            f" border-radius:8px; color:{gui_app.CYAN};"
            f" font-family:'Inter','DM Sans','Liberation Sans',sans-serif;"
            f" font-size:15pt; font-weight:bold; padding:0px 26px;}}"
            f"QPushButton:pressed{{"
            f" background:{gui_app.CYAN}; color:#FFFFFF; border:2px solid {gui_app.CYAN};}}"
        )
        if back_btn is not None:
            back_btn.setFixedHeight(72)
            back_btn.setMinimumWidth(220)
            back_btn.setStyleSheet(btn_style)
        save_btn = gui_app.QPushButton("SAVE DATA ENTRY")
        save_btn.setFixedHeight(72)
        save_btn.setMinimumWidth(320)
        save_btn.setStyleSheet(
            f"QPushButton{{"
            f" background:{gui_app.NEON_LT}; border:2px solid {gui_app.NEON};"
            f" border-radius:8px; color:{gui_app.NEON};"
            f" font-family:'Inter','DM Sans','Liberation Sans',sans-serif;"
            f" font-size:15pt; font-weight:bold; padding:0px 26px;}}"
            f"QPushButton:pressed{{"
            f" background:{gui_app.NEON}; color:#FFFFFF; border:2px solid {gui_app.NEON};}}"
        )
        save_btn.clicked.connect(lambda: _runtime_save_entry(self))
        insert_at = max(0, bottom_l.count() - 1)
        bottom_l.insertWidget(insert_at, save_btn)
        self._runtime_save_btn = save_btn
    return wrapper


class RuntimeLaserCalibrationPanel(gui_app.QWidget):
    saved = gui_app.pyqtSignal(str, dict)

    def __init__(self, cfg, sensor):
        super().__init__()
        self._cfg = cfg
        self._sensor = sensor
        self._capture_active = False
        self._capture_point = None
        self._captured_point = None
        root = gui_app.QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 22)
        root.setSpacing(14)

        title = gui_app.QLabel("LASER ADC CALIBRATION")
        title.setStyleSheet("color:#1565C0; font-size:17pt; font-weight:700;")
        root.addWidget(title)
        instructions = gui_app.QLabel(
            "Place the laser at each known Panasonic point and capture 50 ADC samples. "
            "Capture +80, 0, and -80, then save the three-point mapping."
        )
        instructions.setWordWrap(True)
        instructions.setStyleSheet("color:#5B6575; font-size:11pt;")
        root.addWidget(instructions)

        refs = gui_app.QLabel(
            "Reference mapping: +80 → 120 mm  |  0 → 200 mm  |  -80 → 280 mm"
        )
        refs.setStyleSheet("color:#1A2332; font-size:11pt; font-weight:600;")
        root.addWidget(refs)

        self._live_label = gui_app.QLabel("Reading laser ADC…")
        self._live_label.setWordWrap(True)
        self._live_label.setStyleSheet(
            "background:#F8FAFB; border:1px solid #DDE3EA; border-radius:8px;"
            " padding:12px; color:#1A2332; font-size:12pt;"
        )
        root.addWidget(self._live_label)

        buttons = gui_app.QGridLayout()
        self._plus80_button = gui_app.QPushButton("CAPTURE +80")
        self._zero_button = gui_app.QPushButton("CAPTURE 0")
        self._minus80_button = gui_app.QPushButton("CAPTURE -80")
        self._cancel_button = gui_app.QPushButton("CANCEL")
        self._save_button = gui_app.QPushButton("SAVE 3-POINT CALIBRATION")
        for button in (
            self._plus80_button, self._zero_button, self._minus80_button,
            self._cancel_button, self._save_button,
        ):
            button.setMinimumHeight(48)
        buttons.addWidget(self._plus80_button, 0, 0)
        buttons.addWidget(self._zero_button, 0, 1)
        buttons.addWidget(self._minus80_button, 0, 2)
        buttons.addWidget(self._save_button, 1, 0, 1, 2)
        buttons.addWidget(self._cancel_button, 1, 2)
        self._set_capture_button_style(self._plus80_button, "idle")
        self._set_capture_button_style(self._zero_button, "idle")
        self._set_capture_button_style(self._minus80_button, "idle")
        self._set_capture_button_style(self._cancel_button, "cancel")
        self._set_capture_button_style(self._save_button, "disabled")
        root.addLayout(buttons)

        self._status_label = gui_app.QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("color:#5B6575; font-size:10.5pt;")
        root.addWidget(self._status_label)
        root.addStretch(1)

        self._plus80_button.clicked.connect(lambda: self._start_capture("plus80"))
        self._zero_button.clicked.connect(lambda: self._start_capture("zero"))
        self._minus80_button.clicked.connect(lambda: self._start_capture("minus80"))
        self._cancel_button.clicked.connect(self._cancel_capture)
        self._save_button.clicked.connect(self._save_calibration)
        self._capture_timer = gui_app.QTimer(self)
        self._capture_timer.setInterval(LASER_CAL_SAMPLE_INTERVAL_MS)
        self._capture_timer.timeout.connect(self._capture_sample)
        self._refresh_timer = gui_app.QTimer(self)
        self._refresh_timer.setInterval(500)
        self._refresh_timer.timeout.connect(self._refresh_readout)
        self._refresh_timer.start()

        state = _laser_calibration_state()
        cfg.setdefault("adc", {})["calibrated"] = state["calibrated"]
        self._captured_refs = {}
        if state["references"] is not None:
            for point, key in (
                ("plus80", "raw_at_plus80"),
                ("zero", "raw_at_zero"),
                ("minus80", "raw_at_minus80"),
            ):
                value = state["references"][key]
                self._captured_refs[point] = {
                    "point": point,
                    "measured_mean_raw": value,
                    "sample_count": LASER_CAL_SAMPLES,
                    "sample_min_raw": round(value),
                    "sample_max_raw": round(value),
                }
        if state["calibrated"]:
            cfg["adc"]["factor"] = state["factor"]
            self._status_label.setText(
                "Three-point calibration loaded from " + str(LASER_CAL_FILE)
                if state["references"] is not None else
                f"Legacy single-factor calibration loaded: {state['factor']:.5f}. "
                "Capture all three references to replace it."
            )
        else:
            self._status_label.setText(
                "Capture +80, 0, and -80 before saving the calibration."
            )
        self._update_capture_buttons()
        self._refresh_readout()

    @staticmethod
    def _set_capture_button_style(button, state):
        styles = {
            "idle": (
                "QPushButton{background:#E3EEFA; border:1px solid #1565C0;"
                "border-radius:8px; color:#1565C0; font-size:11pt; font-weight:700;}"
                "QPushButton:pressed{background:#1565C0; color:#FFFFFF;}"
            ),
            "active": (
                "QPushButton{background:#1B8A4C; border:2px solid #126B39;"
                "border-radius:8px; color:#FFFFFF; font-size:11pt; font-weight:700;}"
            ),
            "saved": (
                "QPushButton{background:#E6F4EE; border:2px solid #1B8A4C;"
                "border-radius:8px; color:#126B39; font-size:11pt; font-weight:700;}"
            ),
            "disabled": (
                "QPushButton{background:#EEF1F5; border:1px solid #C8D0DA;"
                "border-radius:8px; color:#7B8492; font-size:11pt; font-weight:700;}"
            ),
            "cancel": (
                "QPushButton{background:#FFF3E0; border:1px solid #C06000;"
                "border-radius:8px; color:#9A4D00; font-size:11pt; font-weight:700;}"
                "QPushButton:pressed{background:#C06000; color:#FFFFFF;}"
            ),
        }
        button.setStyleSheet(styles[state])

    def _update_capture_buttons(self, progress=0):
        names = {"plus80": "+80", "zero": "0", "minus80": "-80"}
        buttons = {
            "plus80": self._plus80_button,
            "zero": self._zero_button,
            "minus80": self._minus80_button,
        }
        for point, button in buttons.items():
            if self._capture_active and point == self._capture_point:
                button.setText(f"CAPTURING {names[point]}  {progress}/{LASER_CAL_SAMPLES}")
                button.setEnabled(False)
                self._set_capture_button_style(button, "active")
            elif self._capture_active:
                button.setText(f"CAPTURE {names[point]}")
                button.setEnabled(False)
                self._set_capture_button_style(button, "disabled")
            elif point in self._captured_refs:
                button.setText(f"✓  {names[point]} CAPTURED")
                button.setEnabled(True)
                self._set_capture_button_style(button, "saved")
            else:
                button.setText(f"CAPTURE {names[point]}")
                button.setEnabled(True)
                self._set_capture_button_style(button, "idle")
        ready = len(self._captured_refs) == 3 and not self._capture_active
        self._save_button.setEnabled(ready)
        self._set_capture_button_style(self._save_button, "saved" if ready else "disabled")

    def _start_capture(self, point):
        try:
            self._sensor.begin_laser_calibration(point)
        except Exception as exc:
            self._status_label.setText(f"Cannot start calibration: {exc}")
            return
        self._captured_refs.pop(point, None)
        self._capture_active = True
        self._capture_point = point
        self._update_capture_buttons()
        self._status_label.setText(
            f"Capturing {LASER_CAL_SAMPLES} samples at the {point} reference point…"
        )
        self._capture_timer.start()

    def _cancel_capture(self):
        self._capture_timer.stop()
        self._capture_active = False
        self._sensor.cancel_laser_calibration()
        self._capture_point = None
        self._update_capture_buttons()
        self._status_label.setText("Capture cancelled; staged references were kept.")

    def _capture_sample(self):
        if not self._capture_active:
            return
        try:
            count = self._sensor.sample_laser_calibration()
            self._update_capture_buttons(count)
            self._status_label.setText(
                f"Capturing {self._capture_point} reference: {count}/{LASER_CAL_SAMPLES} samples"
            )
            if count == LASER_CAL_SAMPLES:
                self._capture_timer.stop()
                point = self._capture_point
                result = self._sensor.finish_laser_calibration()
                self._captured_refs[point] = result
                self._capture_active = False
                self._capture_point = None
                self._update_capture_buttons()
                self._status_label.setText(
                    f"Captured {point}: mean raw "
                    f"{result['measured_mean_raw']:.1f}, spread "
                    f"{result['sample_max_raw'] - result['sample_min_raw']}. "
                    f"{len(self._captured_refs)}/3 reference points captured."
                )
        except Exception as exc:
            self._capture_timer.stop()
            self._capture_active = False
            self._sensor.cancel_laser_calibration()
            self._capture_point = None
            self._update_capture_buttons()
            self._status_label.setText(f"Calibration failed: {exc}")
        finally:
            self._refresh_readout()

    def _save_calibration(self):
        try:
            result = self._sensor.save_laser_calibration(self._captured_refs)
            self._cfg.setdefault("adc", {}).update({
                "factor": result["cal_factor"],
                "calibrated": True,
            })
            gui_app.save_cfg(self._cfg)
        except Exception as exc:
            self._status_label.setText(f"Calibration save failed: {exc}")
            return
        self._status_label.setText(
            "Saved three-point calibration. The sensor service will reload it automatically."
        )
        self.saved.emit("adc", self._cfg["adc"])

    def _refresh_readout(self):
        try:
            result = self._sensor.laser_gauge_readout()
        except Exception as exc:
            self._live_label.setText(f"Laser readout unavailable: {exc}")
            return
        state = "calibrated" if result["calibrated"] else "uncalibrated"
        calibration_label = (
            "3-point mapping"
            if result["calibration_type"] == "three_point"
            else f"Factor: {result['factor']:.5f}"
        )
        self._live_label.setText(
            f"Live ADC raw: {result['raw']}    Corrected raw: "
            f"{result['corrected_raw']:.1f}    {calibration_label}\n"
            f"Predicted gauge readout: {result['gauge_mm']:.1f} mm ({state})"
        )


def patched_calibration_page_init(original_init):
    def wrapper(self, cfg, sensor=None):
        original_init(self, cfg, sensor)
        if sensor is not None and self._stack.count() > 0:
            scroll_area = self._stack.widget(0)
            if isinstance(scroll_area, gui_app.QScrollArea):
                panel = RuntimeLaserCalibrationPanel(cfg, sensor)
                panel.saved.connect(self._on_saved)
                scroll_area.setWidget(panel)
                if cfg.get("adc", {}).get("calibrated", False):
                    self._btns[0][0].setText("✓  " + self._btns[0][1])
        for sc in self.findChildren(gui_app.QScrollArea):
            sc.setStyleSheet(
                "QScrollArea{ border:none; background:#FFFFFF; }"
                "QScrollBar:vertical{ background:#ECEFF4; width:42px; }"
                "QScrollBar::handle:vertical{ background:#C8D0DA; border-radius:21px; min-height:56px; }"
                "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical{ height:0; }"
            )
        for btn in self.findChildren(gui_app.QPushButton):
            txt = btn.text().encode("ascii", "replace").decode("ascii")
            if "BACK" in txt:
                btn.setFixedHeight(72)
                btn.setMinimumWidth(220)
                btn.setStyleSheet(
                    f"QPushButton{{"
                    f" background:{gui_app.CYAN_LT}; border:2px solid {gui_app.CYAN};"
                    f" border-radius:8px; color:{gui_app.CYAN};"
                    f" font-family:'Inter','DM Sans','Liberation Sans',sans-serif;"
                    f" font-size:15pt; font-weight:bold; padding:0px 26px;}}"
                    f"QPushButton:pressed{{"
                    f" background:{gui_app.CYAN}; color:#FFFFFF; border:2px solid {gui_app.CYAN};}}"
                )
                break
    return wrapper


class RuntimePopupKeyboardDialog(gui_app.QDialog):
    def __init__(self, field_title="Enter Value", current="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(field_title)
        self.setModal(True)
        self.setWindowFlags(gui_app.Qt.Dialog | gui_app.Qt.FramelessWindowHint)
        self.setStyleSheet("QDialog { background:#FFFFFF; border:2px solid #1565C0; border-radius:18px; }")

        screen = gui_app.QApplication.primaryScreen()
        if screen is not None:
            geom = screen.availableGeometry()
            target_w = int(geom.width() * 0.84)
            target_h = int(geom.height() * 0.72)
            self.setGeometry(
                geom.x() + (geom.width() - target_w) // 2,
                geom.y() + (geom.height() - target_h) // 2,
                target_w,
                target_h,
            )
        else:
            self.resize(960, 520)

        self._buf = current or ""
        self._result = None

        root = gui_app.QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(8)
        inner_w = max(480, self.width() - 36)
        key_gap = 6
        alpha_key_w = max(48, min(76, (inner_w - (9 * key_gap) - 80) // 10))
        alpha_key_h = max(42, min(58, int((self.height() - 180) / 6)))
        special_key_w = alpha_key_w
        space_key_w = max(160, alpha_key_w * 3)
        action_h = max(46, alpha_key_h)

        hdr = gui_app.QHBoxLayout()
        title = gui_app.QLabel(field_title.upper())
        title.setStyleSheet(
            f"color:{gui_app.CYAN}; font-size:14pt; font-weight:bold; background:transparent;"
        )
        self._disp = gui_app.QLabel(self._buf or "-")
        self._disp.setAlignment(gui_app.Qt.AlignRight | gui_app.Qt.AlignVCenter)
        self._disp.setMinimumHeight(54)
        self._disp.setStyleSheet(
            f"background:#F8FAFB; border:2px solid {gui_app.CYAN}; border-radius:12px;"
            f" color:{gui_app.CYAN}; font-size:16pt; font-family:'Courier New';"
            f" padding-right:10px; font-weight:bold;"
        )
        hdr.addWidget(title, 0)
        hdr.addSpacing(20)
        hdr.addWidget(self._disp, 1)
        root.addLayout(hdr)

        key_rows = ["1234567890", "QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]
        key_style = (
            "QPushButton { background:#F8FAFB; border:2px solid #D8E1EB;"
            " border-radius:8px; color:#334155; font-size:14pt; font-weight:700; }"
            "QPushButton:hover { background:#FFFFFF; border-color:#1565C0; }"
            "QPushButton:pressed { background:#EAF3FF; }"
        )
        for row_idx, row_str in enumerate(key_rows):
            row = gui_app.QHBoxLayout()
            row.setSpacing(key_gap)
            if row_idx == 2:
                row.addSpacing(alpha_key_w // 3)
            elif row_idx == 3:
                row.addSpacing((alpha_key_w * 2) // 3)
            for ch in row_str:
                btn = gui_app.QPushButton(ch)
                btn.setFixedSize(alpha_key_w, alpha_key_h)
                btn.setStyleSheet(key_style)
                btn.clicked.connect(lambda _, v=ch: self._char(v))
                row.addWidget(btn)
            row.addStretch()
            root.addLayout(row)

        special = gui_app.QHBoxLayout()
        special.setSpacing(key_gap)
        special.addSpacing(alpha_key_w)
        for ch, lbl, width in [
            (" ", "SPACE", space_key_w),
            ("-", "-", special_key_w),
            (".", ".", special_key_w),
            ("/", "/", special_key_w),
            ("@", "@", special_key_w),
            ("_", "_", special_key_w),
        ]:
            btn = gui_app.QPushButton(lbl)
            btn.setFixedSize(width, alpha_key_h)
            btn.setStyleSheet(key_style)
            btn.clicked.connect(lambda _, v=ch: self._char(v))
            special.addWidget(btn)
        special.addStretch()
        root.addLayout(special)

        actions = gui_app.QHBoxLayout()
        actions.setSpacing(12)
        for txt, fn, style, flex in [
            ("BACK", self._backspace, "QPushButton { background:#F8FAFB; border:2px solid #D8E1EB; border-radius:8px; color:#5B6575; font-size:13pt; font-weight:700; }", 1),
            ("CLEAR", self._clear, "QPushButton { background:#F8FAFB; border:2px solid #D8E1EB; border-radius:8px; color:#5B6575; font-size:13pt; font-weight:700; }", 1),
            ("CANCEL", self.reject, f"QPushButton {{ background:#FFEBEE; border:2px solid {gui_app.RED}; border-radius:8px; color:{gui_app.RED}; font-size:13pt; font-weight:700; }}", 1),
            ("DONE", self._confirm, f"QPushButton {{ background:{gui_app.CYAN}; border:2px solid {gui_app.CYAN}; border-radius:8px; color:#FFFFFF; font-size:13pt; font-weight:700; }}", 2),
        ]:
            btn = gui_app.QPushButton(txt)
            btn.setFixedHeight(action_h)
            btn.setStyleSheet(style)
            btn.clicked.connect(fn)
            actions.addWidget(btn, flex)
        root.addLayout(actions)

    def _char(self, ch):
        self._buf += ch
        self._disp.setText(self._buf or "-")

    def _backspace(self):
        self._buf = self._buf[:-1]
        self._disp.setText(self._buf or "-")

    def _clear(self):
        self._buf = ""
        self._disp.setText("-")

    def _confirm(self):
        self._result = self._buf
        self.accept()

    def get_value(self):
        return self._result


class RuntimeNumpadDialog(gui_app.QDialog):
    def __init__(self, title, current_val="0", decimals=1, min_val=None, max_val=None, unit="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setStyleSheet(gui_app.SS + "QDialog{background:#0e0e0e;}")

        screen = gui_app.QApplication.primaryScreen()
        if screen is not None:
            geom = screen.availableGeometry()
            self.resize(int(geom.width() * 0.94), int(geom.height() * 0.88))
            self.move(
                geom.x() + (geom.width() - self.width()) // 2,
                geom.y() + (geom.height() - self.height()) // 2,
            )
        else:
            self.resize(900, 760)

        self._dec = decimals
        self._min = min_val
        self._max = max_val
        self._unit = unit
        self._buf = str(current_val).strip()
        self._result = None

        root = gui_app.QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 20)
        root.setSpacing(14)

        t = gui_app.QLabel(title.upper())
        t.setAlignment(gui_app.Qt.AlignCenter)
        t.setStyleSheet(f"color:{gui_app.CYAN}; font-size:18pt; font-weight:bold;")
        root.addWidget(t)

        self._disp = gui_app.QLabel()
        self._disp.setAlignment(gui_app.Qt.AlignRight | gui_app.Qt.AlignVCenter)
        self._disp.setFixedHeight(96)
        self._disp.setStyleSheet(
            f"background:#060606; border:2px solid {gui_app.CYAN}88; border-radius:12px;"
            f" color:{gui_app.CYAN}; font-size:30pt; font-family:'Courier New';"
            f" padding-right:18px; font-weight:bold;"
        )
        root.addWidget(self._disp)

        grid = gui_app.QGridLayout()
        grid.setSpacing(14)
        rows = [("7", "8", "9"), ("4", "5", "6"), ("1", "2", "3"), (".", "0", "DEL")]
        for r, trio in enumerate(rows):
            for c, lbl in enumerate(trio):
                name = "NO" if lbl == "." else "ND" if lbl == "DEL" else "NK"
                btn = gui_app._btn(lbl, name, 96, 160)
                if lbl == "DEL":
                    btn.clicked.connect(self._del)
                elif lbl == ".":
                    btn.clicked.connect(lambda _, ch=".": self._press(ch))
                    btn.setEnabled(decimals > 0)
                else:
                    btn.clicked.connect(lambda _, ch=lbl: self._press(ch))
                grid.addWidget(btn, r, c)

        pm = gui_app._btn("+/-", "NO", 96, 160)
        pm.clicked.connect(self._sign)
        clr = gui_app._btn("CLR", "NO", 96, 160)
        clr.clicked.connect(self._clear)
        ok = gui_app._btn("OK", "NOK", 96, 160)
        ok.clicked.connect(self._confirm)
        grid.addWidget(pm, 4, 0)
        grid.addWidget(clr, 4, 1)
        grid.addWidget(ok, 4, 2)
        root.addLayout(grid)

        cnc = gui_app._btn("CANCEL", "BR", 72)
        cnc.clicked.connect(self.reject)
        root.addWidget(cnc)
        self._refresh()

    def _press(self, ch):
        if ch == "." and "." in self._buf:
            return
        if "." in self._buf and ch != ".":
            after_dot = self._buf.split(".")[1]
            if len(after_dot) >= self._dec:
                return
        stripped = self._buf.lstrip("-")
        if stripped in ("0", "") and ch != ".":
            self._buf = ("-" if self._buf.startswith("-") else "") + ch
        else:
            self._buf += ch
        self._refresh()

    def _del(self):
        self._buf = self._buf[:-1] if len(self._buf) > 1 else "0"
        if self._buf == "-":
            self._buf = "0"
        self._refresh()

    def _clear(self):
        self._buf = "0"
        self._refresh()

    def _sign(self):
        if self._buf.startswith("-"):
            self._buf = self._buf[1:]
        elif self._buf not in ("0", ""):
            self._buf = "-" + self._buf
        self._refresh()

    def _refresh(self):
        suf = f"  {self._unit}" if self._unit else ""
        self._disp.setText((self._buf or "0") + suf)

    def _confirm(self):
        try:
            v = float(self._buf)
        except ValueError:
            v = 0.0
        if self._min is not None:
            v = max(float(self._min), v)
        if self._max is not None:
            v = min(float(self._max), v)
        self._result = v
        self.accept()

    def get_value(self):
        return self._result


def _position_topbar_close_button(track_app):
    if not hasattr(track_app, "_runtime_topbar_close_btn") or not hasattr(track_app, "topbar"):
        return
    topbar_h = track_app.topbar.height()
    btn = track_app._runtime_topbar_close_btn
    btn.move(150, max(8, (topbar_h - btn.height()) // 2))


def _cloud_done(self, ok, message, notify=True):
    if not notify:
        return
    if ok:
        self.topbar.push_error("")
        gui_app.QMessageBox.information(self, "Cloud upload", message)
    else:
        self.topbar.push_error(message)
        gui_app.QMessageBox.warning(self, "Cloud upload failed", message)


def _push_csv_to_cloud(self, csv_path, wait=False, notify=True):
    if not csv_path or not CLOUD_URL:
        return
    self._cloud_thread = CloudPushThread(csv_path, self)
    self._cloud_thread.done.connect(
        lambda ok, msg: _cloud_done(self, ok, msg, notify=notify)
    )
    self._cloud_thread.start()
    if wait:
        self._cloud_thread.wait(45000)


def _offer_usb_export(self, csv_path):
    """Offer a non-destructive copy of the survey that has just finished."""
    if not csv_path or not os.path.exists(csv_path):
        return
    answer = gui_app.QMessageBox.question(
        self, "USB pen drive export",
        "Survey saved locally:\n\n%s\n\nCopy this survey to the inserted USB pen drive?"
        "\n\nChoose No to skip USB export. Cloud upload will continue normally."
        % os.path.basename(csv_path),
        gui_app.QMessageBox.Yes | gui_app.QMessageBox.No,
        gui_app.QMessageBox.No,
    )
    if answer != gui_app.QMessageBox.Yes:
        return
    helper = PROJECT_DIR / "tools" / "export_surveys_to_usb.py"
    try:
        result = subprocess.run(
            [sys.executable, str(helper), "--file", csv_path],
            capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        gui_app.QMessageBox.warning(
            self, "USB export", "Export timed out. Do not remove the USB drive; try again.")
        return
    detail = (result.stdout or result.stderr or "USB export failed.").strip()
    if result.returncode == 0:
        gui_app.QMessageBox.information(self, "USB export complete", detail)
    else:
        gui_app.QMessageBox.warning(self, "USB export", detail)


def optimized_on_toggle(self, running):
    self.sensor.active = running
    if running:
        # Do not create a survey whose identifying/operator fields are all
        # empty.  USB export is a byte-for-byte copy of this local CSV, so
        # blocking this case keeps USB and cloud survey records consistent.
        _apply_station_reference(self)
        entry_values = dict(getattr(self.logger, "_station_values", {}) or {})
        if not any(str(value).strip() for value in entry_values.values()):
            self.sensor.active = False
            gui_app.QMessageBox.warning(
                self,
                "Survey data entry required",
                "Open Survey Data Entry, fill the required survey details, and press SAVE DATA ENTRY before starting the survey.",
            )
            return
        self.logger.set_reference("", "")
        self.logger.set_station("BLE")
        self.sensor.reset()
        self.history = {k: [] for k in self.history}
        self.logger.start(self.cfg["csv_dir"], self.cfg.get("hl_sec", 30))
        _apply_station_reference(self)
    else:
        self.sensor.reset()
        latest = getattr(self, "_latest_sensor_data", None) or {}
        zero_sample = {
            "gauge": latest.get("gauge", 0.0),
            "cross": latest.get("cross", 0.0),
            "twist": 0.0,
            "dist": 0.0,
            "lat": 0.0,
            "lon": 0.0,
            "speed": 0.0,
            "scl_ok": latest.get("scl_ok", False),
            "encoder_ok": latest.get("encoder_ok", False),
            "raw_cross_mm": latest.get("raw_cross_mm", latest.get("cross", 0.0)),
            "raw_twist_mm_m": 0.0,
            "raw_gauge_mm": latest.get("raw_gauge_mm", latest.get("gauge", 0.0)),
            "raw_dist_m": latest.get("raw_dist_m", 0.0),
            "ts": latest.get("ts", 0),
        }
        self._latest_sensor_data = zero_sample
        self._rendered_sensor_data = None
        self.dash.update_data(zero_sample)
        saved_path = self.logger.stop()
        self.dash.set_session(self.logger.count, False, saved_path or "")
        _offer_usb_export(self, saved_path)
        _push_csv_to_cloud(self, saved_path, wait=False)


def runtime_close_event(self, event):
    try:
        self.sensor.active = False
        if hasattr(self, "logger"):
            saved_path = self.logger.stop()
            _push_csv_to_cloud(self, saved_path, wait=True, notify=False)
        if hasattr(self, "sensor") and self.sensor.isRunning():
            self.sensor.stop()
            self.sensor.wait(3000)
        if hasattr(self, "net") and self.net.isRunning():
            self.net.stop()
            self.net.wait(3000)
    finally:
        event.accept()


def runtime_key_press(self, event):
    if event.key() == gui_app.Qt.Key_Q and (event.modifiers() & gui_app.Qt.ControlModifier):
        self.close()
        return
    if event.key() == gui_app.Qt.Key_Escape:
        if self.isFullScreen():
            self.showNormal()
            self.resize(gui_app.W, gui_app.H)
    gui_app.QWidget.keyPressEvent(self, event)


def runtime_resize_event(self, event):
    _position_topbar_close_button(self)
    gui_app.QWidget.resizeEvent(self, event)


def runtime_show_event(self, event):
    _position_topbar_close_button(self)
    gui_app.QWidget.showEvent(self, event)


def patched_trackapp_init(original_init):
    def wrapper(self):
        original_init(self)
        configure_permissible_limits(self.cfg)
        self._latest_sensor_data = None
        self._rendered_sensor_data = None
        self._rendered_session_state = None
        self._runtime_saved_entry_values = {}
        self.logger = BufferedCSVLogger()
        self.logger._track_app = self
        self.net.stop()
        self.net.wait(1000)
        self.net = RuntimeNetThread(self.cfg)
        self.net.status.connect(self._on_net)
        self.net.start()
        self._ui_refresh_timer = gui_app.QTimer(self)
        self._ui_refresh_timer.setInterval(UI_REFRESH_MS)
        self._ui_refresh_timer.timeout.connect(lambda: _refresh_latest_data(self))
        self._ui_refresh_timer.start()
        # railgui25 now provides its own left-aligned close control.  Retain
        # this fallback only for older GUI versions that do not have one.
        if hasattr(self, "topbar") and not hasattr(self.topbar, "sig_close_requested"):
            close_tb = gui_app.QPushButton("X", self.topbar)
            close_tb.setObjectName("BX")
            close_tb.setFixedSize(56, 42)
            close_tb.setStyleSheet(
                f"QPushButton{{background:#FFEBEE; border:2px solid {gui_app.RED}; border-radius:8px;"
                f" color:{gui_app.RED}; font-size:16pt; font-weight:bold;}}"
                f"QPushButton:pressed{{background:{gui_app.RED}; color:#FFFFFF;}}"
            )
            close_tb.clicked.connect(self.close)
            close_tb.raise_()
            close_tb.show()
            self._runtime_topbar_close_btn = close_tb
            _position_topbar_close_button(self)
    return wrapper


def apply_runtime_patches() -> None:
    sanitize_stylesheet()
    patch_qt_stylesheet_calls()
    gui_app.load_cfg = load_runtime_cfg
    gui_app.PopupKeyboardDialog = RuntimePopupKeyboardDialog
    gui_app.NumpadDialog = RuntimeNumpadDialog
    gui_app.InclinCal = RuntimeInclinCal
    gui_app._SENSORS = [
        ("adc", "Track Gauge", gui_app.CYAN, gui_app.ADCCal),
        ("incl", "Inclination", gui_app.AMBER, RuntimeInclinCal),
        ("encoder", "Track Distance", gui_app.NEON, gui_app.EncoderCal),
    ]
    gui_app.SensorThread = SharedMemorySensorThread
    gui_app.CSVLogger = BufferedCSVLogger
    gui_app.NetThread = RuntimeNetThread
    gui_app.HW_SIM = False
    gui_app.MetricCard.refresh = optimized_metric_refresh
    gui_app.DashboardPage.set_session = optimized_dash_session
    gui_app.DataEntryPage.push_sensor_data = optimized_entry_push
    gui_app.DataEntryPage.__init__ = patched_data_entry_init(gui_app.DataEntryPage.__init__)
    gui_app.CalibrationPage.__init__ = patched_calibration_page_init(gui_app.CalibrationPage.__init__)
    gui_app.TrackApp._apply_screen_geometry = safe_apply_screen_geometry
    gui_app.TrackApp._on_data = optimized_on_data
    gui_app.TrackApp._on_toggle = optimized_on_toggle
    gui_app.TrackApp.keyPressEvent = runtime_key_press
    gui_app.TrackApp.resizeEvent = runtime_resize_event
    gui_app.TrackApp.showEvent = runtime_show_event
    gui_app.TrackApp.closeEvent = runtime_close_event
    gui_app.TrackApp.__init__ = patched_trackapp_init(gui_app.TrackApp.__init__)


def run_gui() -> int:
    apply_runtime_patches()
    os.environ.setdefault("QT_XCB_GL_INTEGRATION", "none")
    os.environ.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
    if not os.environ.get("XDG_RUNTIME_DIR"):
        runtime_dir = Path("/tmp/runtime-root")
        runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(runtime_dir, 0o700)
        os.environ["XDG_RUNTIME_DIR"] = str(runtime_dir)
    return gui_app.main()


def run_diag(json_path: str = "") -> int:
    bridge = SharedMemoryBridge(gui_app.load_cfg())
    try:
        diag = bridge.diagnose()
        text = format_diag(diag)
        print(text)
        if json_path:
            Path(json_path).write_text(json.dumps(diag, indent=2))
        return 0
    finally:
        bridge.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--diag-only", action="store_true")
    parser.add_argument("--diag-json", default="")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.diag_only:
        raise SystemExit(run_diag(args.diag_json))
    raise SystemExit(run_gui())
