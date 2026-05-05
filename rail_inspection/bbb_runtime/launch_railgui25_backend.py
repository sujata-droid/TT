#!/usr/bin/env python3
"""Runtime wrapper that keeps railgui25.py untouched and feeds it from shared memory."""

import argparse
import functools
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


RUNTIME_DIR = Path(__file__).resolve().parent
PROJECT_DIR = RUNTIME_DIR.parent
if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

import railgui25 as gui_app
from backend_bridge import SharedMemoryBridge, format_diag

CSV_FLUSH_ROWS = 50
UI_REFRESH_MS = 100
DEFAULT_CLOUD_ROOT = "https://thread-qm2o.onrender.com"
CLOUD_RETRIES = 3
LOG_DIR = RUNTIME_DIR / "logs"
STATUS_FILE = LOG_DIR / "cloud_status.json"
QUEUE_FILE = LOG_DIR / "cloud_queue.json"
CSV_FIELDS = [
    "Sample No",
    "Date & Time",
    "Reference Type",
    "Reference Point",
    "Station Code",
    "Chainage",
    "Loop/Line Siding",
    "Turn-out No",
    "Curve No",
    "Level Crossing No",
    "Hectometer Post",
    "Name",
    "Designation",
    "Lattitude",
    "Longitude",
    "Distance",
    "Gauge",
    "Crossover",
    "Absolute Tilt",
    "Cumulative Tilt",
]
STATION_REF_PRIORITY = [
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


def _write_cloud_status(ok: bool, message: str, csv_path: str = "", queued: bool = False) -> None:
    payload = {
        "timestamp": int(time.time()),
        "ok": bool(ok),
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
    body = json.dumps({
        "filename": os.path.basename(csv_path),
        "data": rows,
    }).encode("utf-8")
    return body, len(rows)


def _post_payload(body: bytes, timeout: int = 20):
    req = urllib.request.Request(
        CLOUD_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "RailInspection-BBB/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        response.read()


def _format_cloud_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, socket.gaierror) and getattr(reason, "errno", None) == -3:
            return "Cloud DNS error (errno -3): host name resolution failed"
    return str(exc)


def _flush_cloud_queue() -> None:
    queue = _read_queue()
    if not queue:
        return
    remaining = []
    for path in queue:
        if not os.path.exists(path):
            continue
        try:
            body, rows = _build_payload(path)
            _post_payload(body, timeout=20)
            _write_cloud_status(True, f"Uploaded queued file ({rows} rows): {os.path.basename(path)}", path)
        except Exception:
            remaining.append(path)
    _write_queue(remaining)


@functools.lru_cache(maxsize=512)
def _sanitize_css(text: str) -> str:
    if not any(token in text for token in ("letter-spacing", "line-spacing", "letter spacing")):
        return text
    text = re.sub(r"letter-spacing\s*:[^;]+;?", "", text)
    text = re.sub(r"line-spacing\s*:[^;]+;?", "", text)
    text = re.sub(r"letter\s+spacing\s*:[^;]+;?", "", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text


class SharedMemorySensorThread(gui_app.SensorThread):
    def __init__(self, cfg):
        super().__init__(cfg)
        self._stop = False
        self._bridge = SharedMemoryBridge(cfg)
        self._last_fault = ""
        self._needs_zero = True

    def stop(self):
        self._stop = True
        self._bridge.close()

    def reset(self):
        super().reset()
        self._bridge.reset_display_reference()
        self._needs_zero = True

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

            if self.active:
                try:
                    sample = self._bridge.next_display_sample()
                    self.motion.emit(self._bridge.is_moving(sample))
                    self._emit_fault("" if sample["scl_ok"] else "SCL3300 backend not ready")
                    self.data_ready.emit(sample)
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
            "Reference Type": self._ref_type or "",
            "Reference Point": self._ref_value or "",
            "Station Code": str(self._station_values.get("Station Code", "")),
            "Chainage": str(self._station_values.get("Chainage", "")),
            "Loop/Line Siding": str(self._station_values.get("Loop/Line Siding", "")),
            "Turn-out No": str(self._station_values.get("Turn-out No", "")),
            "Curve No": str(self._station_values.get("Curve No", "")),
            "Level Crossing No": str(self._station_values.get("Level Crossing No", "")),
            "Hectometer Post": str(self._station_values.get("Hectometer Post", "")),
            "Name": str(self._station_values.get("Name", "")),
            "Designation": str(self._station_values.get("Designation", "")),
            "Lattitude": f"{float(d.get('lat', 0.0)):.5f}",
            "Longitude": f"{float(d.get('lon', 0.0)):.5f}",
            "Distance": f"{float(d.get('dist', 0.0)):.2f}",
            "Gauge": f"{float(d.get('gauge', 0.0)):.0f}",
            "Crossover": f"{float(cross):.0f}",
            "Absolute Tilt": f"{float(cross):.0f}",
            "Cumulative Tilt": f"{float(twist):.0f}",
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
            _flush_cloud_queue()
        except Exception as exc:
            self.done.emit(False, f"CSV read failed: {exc}")
            return

        for attempt in range(CLOUD_RETRIES):
            try:
                _post_payload(body, timeout=20)
                msg = f"Uploaded {rows_count} rows"
                _write_cloud_status(True, msg, self.csv_path)
                self.done.emit(True, msg)
                return
            except Exception as exc:
                if attempt == CLOUD_RETRIES - 1:
                    reason = _format_cloud_error(exc)
                    _queue_csv(self.csv_path)
                    queued_msg = f"Cloud upload failed: {reason}. Queued for retry."
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
            request = urllib.request.Request(
                _cloud_root(CLOUD_URL) + "/",
                method="GET",
                headers={"User-Agent": "RailInspection-BBB/1.0"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                return 200 <= response.status < 500
        except Exception:
            return False


class RuntimeInclinCal(gui_app.QWidget):
    saved = gui_app.pyqtSignal(str, dict)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self._last_raw_mean = 0.0
        self._bridge = None

        lay = gui_app.QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)

        lay.addWidget(gui_app._lbl(
            "Shared-memory SCL3300 calibration for railway cross-level. Keep the trolley stationary on a known level before capturing zero.",
            "#555", 9))

        self._status = gui_app._lbl("Status: ready", gui_app.AMBER, 9, True)
        lay.addWidget(self._status)

        self._live = gui_app._lbl("Live backend stats: --", "#4A5568", 9)
        lay.addWidget(self._live)

        row = gui_app.QHBoxLayout()
        row.addWidget(gui_app._lbl("Persistent zero offset:", "#4A5568", 10, True))
        self._offset_s = gui_app.Stepper(
            cfg["incl"].get("offset", 0.0), step=0.01, dec=3,
            lo=-500.0, hi=500.0, unit="mm", title="INCLINOMETER ZERO OFFSET")
        row.addWidget(self._offset_s, 1)
        lay.addLayout(row)

        row = gui_app.QHBoxLayout()
        row.addWidget(gui_app._lbl("Calibration factor:", "#4A5568", 10, True))
        self._factor_s = gui_app.Stepper(
            cfg["incl"].get("factor", 1.0), step=0.001, dec=3,
            lo=0.1, hi=5.0, unit="x", title="INCLINOMETER SCALE FACTOR")
        row.addWidget(self._factor_s, 1)
        lay.addLayout(row)

        row = gui_app.QHBoxLayout()
        row.addWidget(gui_app._lbl("Reference cross-level:", "#4A5568", 10, True))
        self._reference_s = gui_app.Stepper(
            cfg["incl"].get("reference_mm", 0.0), step=0.1, dec=1,
            lo=-100.0, hi=100.0, unit="mm", title="REFERENCE CROSS LEVEL")
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

    def _capture_stats(self, seconds=2.0):
        self._status.setText("Status: capturing stationary inclinometer samples...")
        bridge = self._bridge_for_cal()
        stats = bridge.capture_raw_cross_stats(seconds=seconds)
        self._last_raw_mean = stats["mean_mm"]
        self._live.setText(
            f"Live backend stats: mean={stats['mean_mm']:.3f} mm  pkpk={stats['pkpk_mm']:.3f} mm  stdev={stats['stdev_mm']:.3f} mm")
        return stats

    def _capture_zero(self):
        try:
            stats = self._capture_stats(2.5)
            self._offset_s.set_value(stats["mean_mm"])
            self._status.setText(f"Status: zero captured at {stats['mean_mm']:.3f} mm")
        except Exception as exc:
            self._status.setText(f"Status: zero capture failed: {exc}")

    def _capture_reference(self):
        try:
            stats = self._capture_stats(2.5)
            offset = self._offset_s.value()
            target = self._reference_s.value()
            denom = stats["mean_mm"] - offset
            if abs(denom) < 0.01:
                raise RuntimeError("reference capture is too close to zero; tilt the trolley to a known cross-level first")
            factor = target / denom
            self._factor_s.set_value(factor)
            self._status.setText(
                f"Status: reference captured, factor set to {factor:.3f} for target {target:.1f} mm")
        except Exception as exc:
            self._status.setText(f"Status: reference capture failed: {exc}")

    def _save(self):
        self.cfg["incl"].update({
            "offset": self._offset_s.value(),
            "factor": self._factor_s.value(),
            "reference_mm": self._reference_s.value(),
            "calibrated": True,
        })
        gui_app.save_cfg(self.cfg)
        self._status.setText("Status: inclinometer calibration saved")
        self.saved.emit("incl", self.cfg["incl"])


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
    dev = (abs(float(val) - 1435.0) if self.key == "gauge" else abs(float(val)))
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
    parts = [f"{k}: {v}" for k, v in values.items() if v]
    if parts:
        ref_value = " / ".join(parts)
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
        parts = [f"{k}: {v}" for k, v in values.items() if v]
        if parts:
            ref_value = " / ".join(parts)
        return ref_type, ref_value, values
    if hasattr(track_app, "entry"):
        return _extract_station_reference(track_app.entry)
    return "", "", {}


def _apply_station_reference(track_app):
    ref_type, ref_value, values = _entry_values_from_track_app(track_app)
    track_app.logger.set_reference(ref_type, ref_value)
    track_app.logger._station_values = values
    station_code = values.get("Station Code", "").strip()
    track_app.logger.set_station(station_code or "BLE")


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
        "color:#5B6575; font-size:9.5pt; font-weight:700;"
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
        label.setFixedWidth(150)
        label.setMinimumHeight(42)
        label.setWordWrap(True)
        label.setStyleSheet(label_style)
        field.setFixedHeight(42)
        current_text = field.text().strip()
        is_empty = current_text in {"", "Tap to enter", "Official name", "Designation"}
        field.setStyleSheet(field_empty if is_empty else field_filled)


def patched_data_entry_init(original_init):
    def wrapper(self):
        original_init(self)
        _runtime_tune_station_params(self)
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


def patched_calibration_page_init(original_init):
    def wrapper(self, cfg):
        original_init(self, cfg)
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
            target_w = int(geom.width() * 0.96)
            target_h = int(geom.height() * 0.88)
            self.setGeometry(
                geom.x() + (geom.width() - target_w) // 2,
                geom.y() + (geom.height() - target_h) // 2,
                target_w,
                target_h,
            )
        else:
            self.resize(1180, 680)

        self._buf = current or ""
        self._result = None

        root = gui_app.QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(16)
        inner_w = max(780, self.width() - 56)
        key_gap = 10
        alpha_key_w = max(68, min(96, (inner_w - (9 * key_gap) - 120) // 10))
        alpha_key_h = max(64, min(82, int((self.height() - 260) / 5.2)))
        special_key_w = alpha_key_w
        space_key_w = max(220, alpha_key_w * 3)
        action_h = max(70, alpha_key_h)

        hdr = gui_app.QHBoxLayout()
        title = gui_app.QLabel(field_title.upper())
        title.setStyleSheet(
            f"color:{gui_app.CYAN}; font-size:20pt; font-weight:bold; background:transparent;"
        )
        self._disp = gui_app.QLabel(self._buf or "-")
        self._disp.setAlignment(gui_app.Qt.AlignRight | gui_app.Qt.AlignVCenter)
        self._disp.setMinimumHeight(84)
        self._disp.setStyleSheet(
            f"background:#F8FAFB; border:2px solid {gui_app.CYAN}; border-radius:12px;"
            f" color:{gui_app.CYAN}; font-size:22pt; font-family:'Courier New';"
            f" padding-right:16px; font-weight:bold;"
        )
        hdr.addWidget(title, 0)
        hdr.addSpacing(20)
        hdr.addWidget(self._disp, 1)
        root.addLayout(hdr)

        key_rows = ["1234567890", "QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]
        key_style = (
            "QPushButton { background:#F8FAFB; border:2px solid #D8E1EB;"
            " border-radius:14px; color:#334155; font-size:20pt; font-weight:700; }"
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
            ("BACK", self._backspace, "QPushButton { background:#F8FAFB; border:2px solid #D8E1EB; border-radius:14px; color:#5B6575; font-size:18pt; font-weight:700; }", 1),
            ("CLEAR", self._clear, "QPushButton { background:#F8FAFB; border:2px solid #D8E1EB; border-radius:14px; color:#5B6575; font-size:18pt; font-weight:700; }", 1),
            ("CANCEL", self.reject, f"QPushButton {{ background:#FFEBEE; border:2px solid {gui_app.RED}; border-radius:14px; color:{gui_app.RED}; font-size:18pt; font-weight:700; }}", 1),
            ("DONE", self._confirm, f"QPushButton {{ background:{gui_app.CYAN}; border:2px solid {gui_app.CYAN}; border-radius:14px; color:#FFFFFF; font-size:18pt; font-weight:700; }}", 2),
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


def _cloud_done(self, ok, message):
    if ok:
        self.topbar.push_error("")
    else:
        self.topbar.push_error(message)


def _push_csv_to_cloud(self, csv_path, wait=False):
    if not csv_path or not CLOUD_URL:
        return
    self._cloud_thread = CloudPushThread(csv_path, self)
    self._cloud_thread.done.connect(lambda ok, msg: _cloud_done(self, ok, msg))
    self._cloud_thread.start()
    if wait:
        self._cloud_thread.wait(45000)


def optimized_on_toggle(self, running):
    self.sensor.active = running
    if running:
        self.logger.set_reference("", "")
        self.logger.set_station("BLE")
        self.sensor.reset()
        self.history = {k: [] for k in self.history}
        self.logger.start(self.cfg["csv_dir"], self.cfg.get("hl_sec", 30))
        _apply_station_reference(self)
    else:
        saved_path = self.logger.stop()
        self.dash.set_session(self.logger.count, False, saved_path or "")
        _push_csv_to_cloud(self, saved_path, wait=False)


def runtime_close_event(self, event):
    try:
        self.sensor.active = False
        if hasattr(self, "logger"):
            saved_path = self.logger.stop()
            _push_csv_to_cloud(self, saved_path, wait=True)
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
        if hasattr(self, "topbar"):
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
    gui_app.PopupKeyboardDialog = RuntimePopupKeyboardDialog
    gui_app.NumpadDialog = RuntimeNumpadDialog
    gui_app.InclinCal = RuntimeInclinCal
    gui_app._SENSORS = [
        ("adc", "Potentiometer", gui_app.CYAN, gui_app.ADCCal),
        ("incl", "Inclinometer", gui_app.AMBER, RuntimeInclinCal),
        ("encoder", "Rotary Encoder", gui_app.NEON, gui_app.EncoderCal),
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
