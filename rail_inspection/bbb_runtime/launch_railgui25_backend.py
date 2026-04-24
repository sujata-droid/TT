#!/usr/bin/env python3
"""Runtime wrapper that keeps railgui25.py untouched and feeds it from shared memory."""

import argparse
import functools
import json
import os
import re
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
CLOUD_URL = os.environ.get("RAIL_CLOUD_URL", "")
CLOUD_RETRIES = 3


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
        super().start(directory, hl_sec)
        self._unflushed = 0

    def write(self, d):
        if not self._w:
            return
        cross = d.get("cross", 0)
        row = {
            "epoch_time": int(time.time()),
            "reference_type": self._ref_type,
            "reference_value": self._ref_value,
            "latitude": d.get("lat", 0),
            "longitude": d.get("lon", 0),
            "cross_level": cross,
            "chainage": d.get("dist", 0),
            "twist": d.get("twist", 0),
            "tilt": cross,
            "tilt_cord_length": d.get("dist", 0),
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
            with open(self.csv_path, newline="") as handle:
                rows = list(gui_app.csv.DictReader(handle))
        except Exception as exc:
            self.done.emit(False, f"CSV read failed: {exc}")
            return

        payload = json.dumps({
            "filename": os.path.basename(self.csv_path),
            "data": rows,
        }).encode("utf-8")

        for attempt in range(CLOUD_RETRIES):
            try:
                request = urllib.request.Request(
                    CLOUD_URL,
                    data=payload,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "RailInspection-BBB/1.0",
                    },
                )
                with urllib.request.urlopen(request, timeout=20) as response:
                    response.read()
                self.done.emit(True, f"Uploaded {len(rows)} rows")
                return
            except Exception as exc:
                if attempt == CLOUD_RETRIES - 1:
                    self.done.emit(False, f"Cloud upload failed: {exc}")
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
                CLOUD_URL.replace("/api/survey", "/"),
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
        dist = d.get("dist", 0)
        self.logger._ref_type = "KM"
        self.logger._ref_value = f"{round(dist, 1)} km"
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


def _cloud_done(self, ok, message):
    if ok:
        self.topbar.push_error("")
    else:
        self.topbar.push_error(message)


def _push_csv_to_cloud(self, csv_path):
    if not csv_path or not CLOUD_URL:
        return
    self._cloud_thread = CloudPushThread(csv_path, self)
    self._cloud_thread.done.connect(lambda ok, msg: _cloud_done(self, ok, msg))
    self._cloud_thread.start()


def optimized_on_toggle(self, running):
    self.sensor.active = running
    if running:
        self.logger.set_reference("", "")
        self.logger.set_station("BLE")
        self.sensor.reset()
        self.history = {k: [] for k in self.history}
        self.logger.start(self.cfg["csv_dir"], self.cfg.get("hl_sec", 30))
    else:
        saved_path = self.logger.stop()
        self.dash.set_session(self.logger.count, False, saved_path or "")
        _push_csv_to_cloud(self, saved_path)


def runtime_close_event(self, event):
    try:
        self.sensor.active = False
        if hasattr(self, "logger"):
            saved_path = self.logger.stop()
            _push_csv_to_cloud(self, saved_path)
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
    return wrapper


def apply_runtime_patches() -> None:
    sanitize_stylesheet()
    patch_qt_stylesheet_calls()
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
