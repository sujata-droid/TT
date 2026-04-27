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

    def write(self, d):
        if not self._w:
            return
        cross = d.get("cross", 0)
        twist = d.get("twist", 0)
        row = {
            "Sample No": self.count + 1,
            "Date & Time": gui_app.datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
            "Reference Type": self._ref_type or "",
            "Reference Point": self._ref_value or "",
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


def _apply_station_reference(track_app):
    if not hasattr(track_app, "entry"):
        return
    ref_type, ref_value, values = _extract_station_reference(track_app.entry)
    track_app.logger.set_reference(ref_type, ref_value)
    station_code = values.get("Station Code", "").strip()
    track_app.logger.set_station(station_code or "BLE")


def _runtime_save_entry(self):
    app = self.window()
    if not hasattr(app, "logger"):
        return
    _apply_station_reference(app)
    if hasattr(app, "topbar"):
        app.topbar.push_error("")


def patched_data_entry_init(original_init):
    def wrapper(self):
        original_init(self)
        root = self.layout()
        if root is None or root.count() < 3:
            return
        hdr_w = root.itemAt(0).widget()
        if hdr_w is not None and hdr_w.layout() is not None:
            hdr_l = hdr_w.layout()
            close_btn = gui_app._btn("X", "BX", 38, 56)
            close_btn.clicked.connect(lambda: self.window().close())
            hdr_l.insertWidget(0, close_btn, 0, gui_app.Qt.AlignLeft | gui_app.Qt.AlignVCenter)
            self._runtime_entry_close_btn = close_btn
        bottom_w = root.itemAt(2).widget()
        if bottom_w is None or bottom_w.layout() is None:
            return
        bottom_l = bottom_w.layout()
        save_btn = gui_app._btn("SAVE DATA ENTRY", "BG", 46, 260)
        save_btn.clicked.connect(lambda: _runtime_save_entry(self))
        insert_at = max(0, bottom_l.count() - 1)
        bottom_l.insertWidget(insert_at, save_btn)
        self._runtime_save_btn = save_btn
    return wrapper


def patch_touch_keyboard_scaling() -> None:
    scale = float(os.environ.get("RAIL_TOUCH_SCALE", "1.6"))
    if scale <= 1.0:
        return

    popup_init = gui_app.PopupKeyboardDialog.__init__
    numpad_init = gui_app.NumpadDialog.__init__

    def popup_wrapper(self, *args, **kwargs):
        popup_init(self, *args, **kwargs)
        screen = gui_app.QApplication.primaryScreen()
        if screen is not None:
            geom = screen.availableGeometry()
            target_w = int(geom.width() * 0.96)
            target_h = int(geom.height() * 0.84)
            self.resize(max(self.width(), target_w), max(self.height(), target_h))
            self.move(
                geom.x() + (geom.width() - self.width()) // 2,
                geom.y() + (geom.height() - self.height()) // 2,
            )
        else:
            self.resize(int(self.width() * scale), int(self.height() * scale))
        self.setStyleSheet(
            self.styleSheet()
            + " QLabel{font-size:16pt;} QPushButton{font-size:16pt; min-height:68px; min-width:92px;}"
        )
        for btn in self.findChildren(gui_app.QPushButton):
            btn.setMinimumHeight(max(btn.minimumHeight(), 68))
            btn.setMinimumWidth(max(btn.minimumWidth(), 92))

    def numpad_wrapper(self, *args, **kwargs):
        numpad_init(self, *args, **kwargs)
        screen = gui_app.QApplication.primaryScreen()
        if screen is not None:
            geom = screen.availableGeometry()
            target_w = int(geom.width() * 0.65)
            target_h = int(geom.height() * 0.72)
            self.resize(max(self.width(), target_w), max(self.height(), target_h))
            self.move(
                geom.x() + (geom.width() - self.width()) // 2,
                geom.y() + (geom.height() - self.height()) // 2,
            )
        else:
            self.resize(int(self.width() * scale), int(self.height() * scale))
        self.setStyleSheet(
            self.styleSheet()
            + " QLabel{font-size:16pt;} QPushButton{font-size:16pt; min-height:66px; min-width:88px;}"
        )
        for btn in self.findChildren(gui_app.QPushButton):
            btn.setMinimumHeight(max(btn.minimumHeight(), 66))
            btn.setMinimumWidth(max(btn.minimumWidth(), 88))

    gui_app.PopupKeyboardDialog.__init__ = popup_wrapper
    gui_app.NumpadDialog.__init__ = numpad_wrapper


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
        _apply_station_reference(self)
        self.sensor.reset()
        self.history = {k: [] for k in self.history}
        self.logger.start(self.cfg["csv_dir"], self.cfg.get("hl_sec", 30))
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


def patched_trackapp_init(original_init):
    def wrapper(self):
        original_init(self)
        self._latest_sensor_data = None
        self._rendered_sensor_data = None
        self._rendered_session_state = None
        self.logger = BufferedCSVLogger()
        self.net.stop()
        self.net.wait(1000)
        self.net = RuntimeNetThread(self.cfg)
        self.net.status.connect(self._on_net)
        self.net.start()
        self._ui_refresh_timer = gui_app.QTimer(self)
        self._ui_refresh_timer.setInterval(UI_REFRESH_MS)
        self._ui_refresh_timer.timeout.connect(lambda: _refresh_latest_data(self))
        self._ui_refresh_timer.start()
        close_btn = gui_app.QPushButton("X", self)
        close_btn.setObjectName("BX")
        close_btn.setGeometry(8, 8, 42, 30)
        close_btn.clicked.connect(self.close)
        close_btn.raise_()
        self._runtime_close_btn = close_btn
    return wrapper


def apply_runtime_patches() -> None:
    sanitize_stylesheet()
    patch_qt_stylesheet_calls()
    patch_touch_keyboard_scaling()
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
    gui_app.TrackApp._apply_screen_geometry = safe_apply_screen_geometry
    gui_app.TrackApp._on_data = optimized_on_data
    gui_app.TrackApp._on_toggle = optimized_on_toggle
    gui_app.TrackApp.keyPressEvent = runtime_key_press
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
