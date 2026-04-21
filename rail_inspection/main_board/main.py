#!/usr/bin/env python3
"""
main.py -- Rail Inspection Display & Logging Application
=========================================================
Runs on BeagleBone Black with PyQt5.

ARCHITECTURE:
  SensorThread  (QThread, prio=80)
    - Connects to Unix socket /tmp/rail_sensor.sock
    - Reads JSON frames from sensor_service C daemon
    - Emits sensor_update signal at ≤50 Hz

  CSVWriterThread  (QThread, prio=60)
    - Receives frames via a queue
    - Writes to /home/debian/surveys/<timestamp>.csv
    - Flushes every 100 rows (not every row -- flushing every row is
      catastrophically slow on an eMMC-backed filesystem)

  CloudPushThread  (QThread, prio=40)
    - On program exit, reads the saved CSV
    - POSTs JSON to Render cloud server
    - Retries up to 5 times with exponential backoff

  MainWindow  (QMainWindow)
    - Single redraw timer at 10 Hz (not 50 Hz!)
    - Reads LATEST frame only; displays gauges and status
    - WHY 10 Hz display? Human eye cannot track motion faster than
      ~24 fps. At 10 Hz we spend <5 ms/frame on the GUI and leave
      the remaining 95% of CPU budget for sensor acquisition.

PIN SUMMARY (for your reference):
  SCL3300:  P9_17(CS), P9_18(MOSI), P9_21(MISO), P9_22(SCK)
  Encoder:  P9_27(A), P9_42(B)
"""

import sys
import os
import json
import time
import socket
import csv
import queue
import threading
import datetime
import signal
import urllib.request
import urllib.error
import mmap
import struct

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QFrame, QGridLayout, QSizePolicy, QPushButton
)
from PyQt5.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QObject
)
from PyQt5.QtGui import (
    QPainter, QColor, QPen, QFont, QBrush, QLinearGradient
)

# ── Configuration ──────────────────────────────────────────────────────
SHM_PATH         = "/dev/shm/rail_sensor_shm"
SHM_MAGIC        = 0x5241494c
SHM_VERSION      = 1
SHM_STRUCT       = struct.Struct("<IIIIqddddiBBBB")
SURVEY_DIR       = os.path.expanduser("~/surveys")
CLOUD_URL        = os.environ.get("RAIL_CLOUD_URL",
                                   "https://thread-qm2o.onrender.com/api/survey")
CLOUD_RETRIES    = 5
DISPLAY_HZ       = 10
SENSOR_RETRY_S   = 3.0
CSV_FLUSH_ROWS   = 100
GAUGE_MM_CONST   = 1676.0
CSV_QUEUE_MAX    = 10000

# ── Shared state (latest frame, protected by a simple lock) ───────────
_latest_lock  = threading.Lock()
_latest_frame = None   # dict parsed from JSON

def set_latest(frame: dict):
    global _latest_frame
    with _latest_lock:
        _latest_frame = frame

def get_latest():
    with _latest_lock:
        return _latest_frame


# ═══════════════════════════════════════════════════════════════════════
# SensorThread
# ═══════════════════════════════════════════════════════════════════════
class SensorThread(QThread):
    """
    Connects to the C sensor_service via POSIX shared memory.
    Copies coherent frames and puts them on the CSV queue.

    WHY Unix socket and not SPI/GPIO directly in Python?
    Because Python cannot run SCHED_FIFO reliably -- the GIL and GC
    can pause any thread for milliseconds.  The C service runs
    SCHED_FIFO and handles all time-critical acquisition.  Python
    only handles display and logging, both of which tolerate latency.
    """
    sensor_update = pyqtSignal(dict)   # emitted for every parsed frame
    status_change = pyqtSignal(str)    # emitted on connect/disconnect

    def __init__(self, csv_queue: queue.Queue, parent=None):
        super().__init__(parent)
        self._csv_q = csv_queue
        self._stop  = False

    def stop(self):
        self._stop = True

    def _queue_csv_frame(self, frame: dict):
        try:
            self._csv_q.put_nowait(frame)
        except queue.Full:
            # Keep live acquisition running if the disk writer falls behind.
            try:
                self._csv_q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._csv_q.put_nowait(frame)
            except queue.Full:
                pass

    def run(self):
        shm_file = None
        shm = None
        last_update_count = None

        while not self._stop:
            # Connect
            if shm is None:
                try:
                    shm_file = open(SHM_PATH, "rb")
                    shm = mmap.mmap(shm_file.fileno(), SHM_STRUCT.size, access=mmap.ACCESS_READ)
                    self.status_change.emit("SENSOR: CONNECTED")
                    print("[SensorThread] Connected to sensor_service shared memory")
                except Exception as e:
                    self.status_change.emit("SENSOR: WAITING FOR SERVICE...")
                    print("[SensorThread] Shared memory not ready: {}".format(e))
                    try:
                        if shm:
                            shm.close()
                    except Exception:
                        pass
                    try:
                        if shm_file:
                            shm_file.close()
                    except Exception:
                        pass
                    shm = None
                    shm_file = None
                    time.sleep(SENSOR_RETRY_S)
                    continue

            try:
                vals1 = SHM_STRUCT.unpack(shm[:SHM_STRUCT.size])
                seq1 = vals1[2]
                if seq1 & 1:
                    time.sleep(0.01)
                    continue
                vals2 = SHM_STRUCT.unpack(shm[:SHM_STRUCT.size])
                if vals2[2] != seq1 or (vals2[2] & 1):
                    time.sleep(0.01)
                    continue
            except Exception as e:
                print("[SensorThread] Shared memory read error: {}".format(e))
                try:
                    shm.close()
                except Exception:
                    pass
                try:
                    shm_file.close()
                except Exception:
                    pass
                shm = None
                shm_file = None
                self.status_change.emit("SENSOR: RECONNECTING...")
                time.sleep(1.0)
                continue

            (magic, version, _seq, update_count, ts_us, cl_mm, tw_mm_m,
             ch_m, gauge_mm, _enc_count, scl_ok, enc_ok, service_ok,
             _reserved) = vals2

            if magic != SHM_MAGIC or version != SHM_VERSION or not service_ok:
                self.status_change.emit("SENSOR: WAITING FOR VALID FRAME...")
                time.sleep(0.2)
                continue

            if update_count != last_update_count:
                last_update_count = update_count
                frame = {
                    "ts": ts_us,
                    "cl": cl_mm,
                    "tw": tw_mm_m,
                    "ch": ch_m,
                    "ga": gauge_mm,
                    "s0": scl_ok,
                    "s1": enc_ok,
                }
                set_latest(frame)
                self._queue_csv_frame(frame)
                self.sensor_update.emit(frame)

            time.sleep(0.01)

        if shm:
            try: shm.close()
            except: pass
        if shm_file:
            try: shm_file.close()
            except: pass
        print("[SensorThread] Stopped.")


# ═══════════════════════════════════════════════════════════════════════
# CSVWriterThread
# ═══════════════════════════════════════════════════════════════════════
class CSVWriterThread(QThread):
    """
    Drains the CSV queue and writes rows to disk.

    WHY buffer 100 rows before flush?
    On BBB, eMMC write latency is ~5-15 ms per fsync().
    At 50 Hz, fsyncing every row = 50 × 15 ms = 750 ms/second of IO wait.
    That would freeze the entire system.
    Buffering 100 rows = 2 seconds of data, then fsyncing once = fine.
    """
    def __init__(self, csv_queue: queue.Queue, parent=None):
        super().__init__(parent)
        self._q         = csv_queue
        self._stop      = False
        self.csv_path   = None   # set during run()

    def stop(self):
        self._stop = True

    def run(self):
        try:
            os.makedirs(SURVEY_DIR, exist_ok=True)
        except Exception as e:
            print("[CSVWriter] Cannot create survey directory {}: {}".format(
                  SURVEY_DIR, e))
            return
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.csv_path = os.path.join(SURVEY_DIR, "survey_{}.csv".format(ts))

        fieldnames = ["timestamp_us", "cross_level_mm", "twist_mm_per_m",
                      "chainage_m", "gauge_mm", "scl3300_ok", "encoder_ok"]

        try:
            with open(self.csv_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                row_count  = 0
                unflushed  = 0

                while not self._stop or not self._q.empty():
                    try:
                        frame = self._q.get(timeout=0.5)
                    except queue.Empty:
                        if unflushed > 0:
                            f.flush()
                            unflushed = 0
                        continue

                    writer.writerow({
                        "timestamp_us":   frame.get("ts",  0),
                        "cross_level_mm": "{:.4f}".format(frame.get("cl", 0.0)),
                        "twist_mm_per_m": "{:.4f}".format(frame.get("tw", 0.0)),
                        "chainage_m":     "{:.4f}".format(frame.get("ch", 0.0)),
                        "gauge_mm":       "{:.1f}".format(frame.get("ga", GAUGE_MM_CONST)),
                        "scl3300_ok":     frame.get("s0", 0),
                        "encoder_ok":     frame.get("s1", 0),
                    })
                    row_count  += 1
                    unflushed  += 1

                    if unflushed >= CSV_FLUSH_ROWS:
                        f.flush()
                        os.fsync(f.fileno())
                        unflushed = 0

                # Final flush
                if unflushed > 0:
                    f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            print("[CSVWriter] Write error: {}".format(e))
            return

        print("[CSVWriter] Saved {} rows to {}".format(row_count, self.csv_path))


# ═══════════════════════════════════════════════════════════════════════
# CloudPushThread
# ═══════════════════════════════════════════════════════════════════════
class CloudPushThread(QThread):
    """
    Reads the completed CSV and POSTs it to the Render cloud server.

    WHY send as JSON with CSV syntax inside?
    The Render server receives JSON (easy to parse in Flask).
    The JSON payload contains the CSV rows as an array of objects --
    this means the cloud can reconstruct the exact CSV file on its side,
    AND you can query individual fields without parsing CSV text.

    Payload format:
    {
      "filename": "survey_20250421_143022.csv",
      "data": [
        {"timestamp_us": 12345, "cross_level_mm": 1.23, ...},
        ...
      ]
    }
    """
    push_done   = pyqtSignal(bool, str)   # (success, message)

    def __init__(self, csv_path: str, parent=None):
        super().__init__(parent)
        self._csv_path = csv_path

    def run(self):
        if not self._csv_path or not os.path.exists(self._csv_path):
            self.push_done.emit(False, "No CSV file to push.")
            return

        # Read CSV into list of dicts
        rows = []
        try:
            with open(self._csv_path, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(row)
        except Exception as e:
            self.push_done.emit(False, "CSV read error: {}".format(e))
            return

        payload = {
            "filename": os.path.basename(self._csv_path),
            "data":     rows
        }
        body = json.dumps(payload).encode("utf-8")

        # Retry with exponential backoff
        for attempt in range(CLOUD_RETRIES):
            wait = 2 ** attempt
            try:
                req = urllib.request.Request(
                    CLOUD_URL,
                    data    = body,
                    method  = "POST",
                    headers = {
                        "Content-Type":   "application/json",
                        "Content-Length": str(len(body)),
                        "User-Agent":     "RailInspection-BBB/1.0",
                    }
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    resp_body = resp.read().decode("utf-8")
                    print("[Cloud] Attempt {}: {} rows pushed. Response: {}".format(
                          attempt + 1, len(rows), resp_body))
                    self.push_done.emit(True,
                        "Pushed {} rows to cloud.".format(len(rows)))
                    return
            except urllib.error.HTTPError as e:
                print("[Cloud] HTTP {} on attempt {}: {}".format(
                      e.code, attempt + 1, e.reason))
            except urllib.error.URLError as e:
                print("[Cloud] Network error attempt {}: {}".format(
                      attempt + 1, e.reason))
            except Exception as e:
                print("[Cloud] Error attempt {}: {}".format(attempt + 1, e))

            if attempt < CLOUD_RETRIES - 1:
                print("[Cloud] Retrying in {} s...".format(wait))
                time.sleep(wait)

        self.push_done.emit(False,
            "Cloud push failed after {} attempts. CSV saved locally: {}".format(
             CLOUD_RETRIES, self._csv_path))


# ═══════════════════════════════════════════════════════════════════════
# Gauge Widget -- draws a single analogue gauge
# ═══════════════════════════════════════════════════════════════════════
class GaugeWidget(QWidget):
    """
    Industrial-style analogue gauge.
    Redraws only when value changes by >= 0.05 mm (hysteresis) so the
    display does not repaint 50 times/second for noise.

    WHY custom paint instead of QDial?
    QDial has no dead-zone, no coloured arc, no unit label, and no
    centre value text.  A custom widget gives us all of these in ~80
    lines of QPainter code and runs fast enough at 10 Hz.
    """
    def __init__(self, title, unit, vmin, vmax, warn, alarm, parent=None):
        super().__init__(parent)
        self.title  = title
        self.unit   = unit
        self.vmin   = vmin
        self.vmax   = vmax
        self.warn   = warn    # yellow zone starts
        self.alarm  = alarm   # red zone starts
        self._val   = 0.0
        self._prev  = None
        self.setMinimumSize(180, 180)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_value(self, v: float):
        v = max(self.vmin, min(self.vmax, v))
        if self._prev is None or abs(v - self._prev) >= 0.05:
            self._val  = v
            self._prev = v
            self.update()   # triggers paintEvent

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        side = min(self.width(), self.height()) - 10
        cx   = self.width()  // 2
        cy   = self.height() // 2
        r    = side // 2

        # Background circle
        p.setBrush(QColor(30, 30, 40))
        p.setPen(QPen(QColor(80, 80, 100), 2))
        p.drawEllipse(cx - r, cy - r, 2*r, 2*r)

        # Arc  (-225 deg to +45 deg, total 270 degrees span)
        # Qt arc: 0 deg = 3 o'clock, positive = counter-clockwise, in 1/16 deg units
        START_DEG = 225    # 7 o'clock position
        SPAN_DEG  = 270

        def val_to_angle(v):
            frac = (v - self.vmin) / (self.vmax - self.vmin)
            return 180 + 45 - frac * SPAN_DEG  # degrees in Qt paint space

        import math
        arc_r = int(r * 0.75)
        pen = QPen()
        pen.setWidth(6)
        pen.setCapStyle(Qt.RoundCap)

        # Draw zones
        zones = [
            (self.vmin, self.warn,  QColor(0, 200, 80)),
            (self.warn, self.alarm, QColor(220, 180, 0)),
            (self.alarm, self.vmax, QColor(220, 50, 50)),
        ]
        for zmin, zmax, col in zones:
            a_start = int(val_to_angle(zmin) * 16)
            a_end   = int(val_to_angle(zmax)  * 16)
            pen.setColor(col)
            p.setPen(pen)
            p.drawArc(cx - arc_r, cy - arc_r, 2*arc_r, 2*arc_r,
                      a_end, a_start - a_end)

        # Needle
        angle_deg = val_to_angle(self._val)
        angle_rad = math.radians(angle_deg)
        needle_len = int(r * 0.68)
        nx = cx + int(needle_len * math.cos(angle_rad))
        ny = cy - int(needle_len * math.sin(angle_rad))
        needle_pen = QPen(QColor(255, 255, 255), 2)
        needle_pen.setCapStyle(Qt.RoundCap)
        p.setPen(needle_pen)
        p.drawLine(cx, cy, nx, ny)

        # Centre dot
        p.setBrush(QColor(180, 180, 200))
        p.setPen(Qt.NoPen)
        p.drawEllipse(cx - 5, cy - 5, 10, 10)

        # Value text
        font = QFont("Monospace", max(10, r // 5), QFont.Bold)
        p.setFont(font)
        p.setPen(QColor(255, 255, 255))
        val_str = "{:.2f}".format(self._val)
        p.drawText(cx - r, cy + int(r * 0.35), 2*r, 30,
                   Qt.AlignCenter, val_str)

        # Unit text
        font2 = QFont("Monospace", max(7, r // 8))
        p.setFont(font2)
        p.setPen(QColor(160, 160, 180))
        p.drawText(cx - r, cy + int(r * 0.55), 2*r, 20,
                   Qt.AlignCenter, self.unit)

        # Title
        font3 = QFont("Monospace", max(8, r // 7), QFont.Bold)
        p.setFont(font3)
        p.setPen(QColor(200, 200, 220))
        p.drawText(cx - r, cy - r + 8, 2*r, 20,
                   Qt.AlignCenter, self.title)


# ═══════════════════════════════════════════════════════════════════════
# Status LED widget
# ═══════════════════════════════════════════════════════════════════════
class StatusLED(QWidget):
    def __init__(self, label, parent=None):
        super().__init__(parent)
        self._ok    = False
        self._label = label
        self.setFixedSize(120, 28)

    def set_ok(self, ok: bool):
        if ok != self._ok:
            self._ok = ok
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = QColor(0, 220, 80) if self._ok else QColor(220, 50, 50)
        p.setBrush(QBrush(col))
        p.setPen(QPen(col.darker(150), 1))
        p.drawEllipse(2, 6, 16, 16)
        p.setPen(QColor(220, 220, 230))
        p.setFont(QFont("Monospace", 9))
        p.drawText(24, 0, 96, 28, Qt.AlignVCenter | Qt.AlignLeft, self._label)


# ═══════════════════════════════════════════════════════════════════════
# Digital readout widget (for chainage -- no gauge needed)
# ═══════════════════════════════════════════════════════════════════════
class DigitalReadout(QWidget):
    def __init__(self, title, unit, parent=None):
        super().__init__(parent)
        self._title = title
        self._unit  = unit
        self._val   = 0.0
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_value(self, v: float):
        if abs(v - self._val) >= 0.001:
            self._val = v
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(20, 20, 30))
        p.setPen(QColor(100, 200, 255))
        p.setFont(QFont("Monospace", 36, QFont.Bold))
        p.drawText(0, 0, self.width(), 65,
                   Qt.AlignCenter, "{:.3f}".format(self._val))
        p.setPen(QColor(140, 140, 160))
        p.setFont(QFont("Monospace", 11))
        p.drawText(0, 55, self.width(), 20,
                   Qt.AlignCenter, "{} ({})".format(self._title, self._unit))


# ═══════════════════════════════════════════════════════════════════════
# Main Window
# ═══════════════════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Rail Inspection System  |  Gauge: 1676 mm BG")
        self.setStyleSheet("background-color: #1a1a2e; color: #e0e0f0;")
        self.resize(900, 600)

        # ── Central widget ──────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ── Top status bar ───────────────────────────────────────────
        status_row = QHBoxLayout()
        self._lbl_status = QLabel("Initialising...")
        self._lbl_status.setStyleSheet(
            "color:#80c0ff; font-family:Monospace; font-size:11px;")
        self._led_scl    = StatusLED("SCL3300")
        self._led_enc    = StatusLED("ENCODER")
        self._led_cloud  = StatusLED("CLOUD")
        self._lbl_ts     = QLabel("--:--:--.---")
        self._lbl_ts.setStyleSheet(
            "color:#a0a0c0; font-family:Monospace; font-size:11px;")
        status_row.addWidget(self._lbl_status)
        status_row.addStretch()
        status_row.addWidget(self._led_scl)
        status_row.addWidget(self._led_enc)
        status_row.addWidget(self._led_cloud)
        status_row.addWidget(self._lbl_ts)
        root.addLayout(status_row)

        # ── Separator ────────────────────────────────────────────────
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#404060;")
        root.addWidget(line)

        # ── Gauges row ───────────────────────────────────────────────
        gauges_row = QHBoxLayout()
        self._g_cl  = GaugeWidget("CROSS-LEVEL", "mm",  -25,  25,  12,  20)
        self._g_tw  = GaugeWidget("TWIST",       "mm/m", -10,  10,   5,   8)
        gauges_row.addWidget(self._g_cl)
        gauges_row.addWidget(self._g_tw)
        root.addLayout(gauges_row)

        # ── Chainage digital readout ─────────────────────────────────
        self._d_ch = DigitalReadout("CHAINAGE", "m")
        root.addWidget(self._d_ch)

        # ── Gauge constant label ─────────────────────────────────────
        gauge_lbl = QLabel("GAUGE: 1676 mm (Indian Broad Gauge  --  constant)")
        gauge_lbl.setStyleSheet(
            "color:#60d060; font-family:Monospace; font-size:12px;"
            "font-weight:bold; padding:4px;")
        gauge_lbl.setAlignment(Qt.AlignCenter)
        root.addWidget(gauge_lbl)

        # ── Threads & queues ─────────────────────────────────────────
        self._csv_q     = queue.Queue(maxsize=CSV_QUEUE_MAX)
        self._sensor_th = SensorThread(self._csv_q)
        self._csv_th    = CSVWriterThread(self._csv_q)
        self._cloud_th  = None

        self._sensor_th.status_change.connect(self._on_status)
        # Do NOT connect sensor_update to display -- we poll at 10 Hz instead
        # to completely decouple acquisition rate from redraw rate.

        self._sensor_th.start()
        self._csv_th.start()

        # ── Display timer 10 Hz ──────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_display)
        self._timer.start(1000 // DISPLAY_HZ)

        self._frame_count = 0
        self._fps_ts      = time.monotonic()

    # ── Slots ────────────────────────────────────────────────────────

    def _on_status(self, msg: str):
        self._lbl_status.setText(msg)

    def _refresh_display(self):
        """Called at 10 Hz. Reads LATEST frame only."""
        f = get_latest()
        if f is None:
            return

        self._g_cl.set_value(f.get("cl", 0.0))
        self._g_tw.set_value(f.get("tw", 0.0))
        self._d_ch.set_value(f.get("ch", 0.0))

        self._led_scl.set_ok(bool(f.get("s0", 0)))
        self._led_enc.set_ok(bool(f.get("s1", 0)))

        # Sensor service timestamp is CLOCK_MONOTONIC in microseconds.
        ts_us = f.get("ts", 0)
        age_ms = max(0, int((time.monotonic() * 1000000.0 - ts_us) / 1000.0))
        self._lbl_ts.setText("age {:d} ms".format(age_ms))

        self._frame_count += 1

    # ── Shutdown ─────────────────────────────────────────────────────

    def closeEvent(self, event):
        print("[Main] Shutting down...")

        self._timer.stop()

        # Stop acquisition
        self._sensor_th.stop()
        if not self._sensor_th.wait(3000):
            print("[Main] Sensor thread did not stop within timeout.")

        # Stop CSV writer (it will flush remaining rows)
        self._csv_th.stop()
        if not self._csv_th.wait(10000):
            print("[Main] CSV writer did not stop within timeout.")

        csv_path = self._csv_th.csv_path

        # Launch cloud push in background
        if csv_path and os.path.exists(csv_path):
            print("[Main] Launching cloud push for:", csv_path)
            self._cloud_th = CloudPushThread(csv_path)
            self._cloud_th.push_done.connect(self._on_push_done)
            self._cloud_th.start()
            # Wait up to 60 s for push
            self._cloud_th.wait(60000)

        event.accept()
        print("[Main] Exit complete.")

    def _on_push_done(self, success: bool, msg: str):
        self._led_cloud.set_ok(success)
        print("[Cloud] Push result: {} -- {}".format(
              "OK" if success else "FAIL", msg))


# ═══════════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════════
def main():
    # Allow Ctrl+C in terminal to work even with Qt event loop
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = QApplication(sys.argv)
    app.setApplicationName("Rail Inspection")
    app.setStyle("Fusion")

    # Dark palette for Qt5 (Fusion allows palette overrides)
    from PyQt5.QtGui import QPalette
    pal = QPalette()
    pal.setColor(QPalette.Window,          QColor(26, 26, 46))
    pal.setColor(QPalette.WindowText,      QColor(224, 224, 240))
    pal.setColor(QPalette.Base,            QColor(18, 18, 35))
    pal.setColor(QPalette.AlternateBase,   QColor(35, 35, 60))
    pal.setColor(QPalette.Text,            QColor(224, 224, 240))
    pal.setColor(QPalette.Button,          QColor(50, 50, 80))
    pal.setColor(QPalette.ButtonText,      QColor(224, 224, 240))
    pal.setColor(QPalette.Highlight,       QColor(80, 130, 200))
    pal.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    app.setPalette(pal)

    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
