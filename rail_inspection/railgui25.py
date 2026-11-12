#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rail Track Geometry Inspection System
Target  : BeagleBone Black Industrial | Ubuntu | 1024×768 HDMI/Touch
Stack   : PyQt5 | Python 3.8+
Version : 5.1.0 — fixed layout: 3-panel DataEntry, popup keyboard, buttons up

Sensor map (PCB connections):
  Rotary Encoder   → GPIO → P8_15 (A / GPIO1_15), P8_12 (B / GPIO1_12) (/dev/gpiochip1)
  Track Gauge (ADC)→ ADC  → /sys/bus/iio/devices/iio:device0/in_voltage0_raw (P9_39 / AIN0)
  Inclinometer     → SPI1 → /dev/spidev1.0  (Murata SCL3300: P9_28 CSB, P9_29 MISO, P9_30 MOSI, P9_31 SCK)
  GNSS             → UART4→ /dev/ttyS4      (u-blox NEO-M8P-2: P9_11 RXD, P9_13 TXD)
  LTE              → UART1/USB → /dev/ttyS1 / usb2 (Quectel EC200U: P9_24 TXD, P9_26 RXD)
  Display          → HDMI → omapdrm / xrandr
"""

import sys, os, re, json, csv, time, random, subprocess, hmac, math
from datetime import datetime
from pathlib import Path

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFrame, QStackedWidget, QScrollArea,
    QFileDialog, QTextEdit, QSizePolicy, QDialog, QTableWidget,
    QTableWidgetItem, QHeaderView, QComboBox, QLineEdit, QMessageBox, QDateEdit, QCheckBox, QScroller,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QProcess, QPoint, QRect, QSize, QEvent, QDate
from PyQt5.QtGui import (
    QPainter, QColor, QPen, QFont, QBrush, QLinearGradient, QPainterPath,
)

# ─────────────────────────────────────────────────────────────────────────────
#  PALETTE
# ─────────────────────────────────────────────────────────────────────────────
BG    = "#ECEFF4"
CARD  = "#FFFFFF"
NEON  = "#1B8A4C"
CYAN  = "#1565C0"
AMBER = "#C06000"
RED   = "#C62828"
MAGI  = "#5E35B1"
HEADER_BG     = "#1E2430"
HEADER_ACCENT = "#1B8A4C"

NEON_LT  = "#E6F4EE"
CYAN_LT  = "#E3EEFA"
AMBER_LT = "#FFF3E0"
MAGI_LT  = "#EDE7F6"
RED_LT   = "#FFEBEE"
WARN     = "#E65100"
WARN_LT  = "#FFF3E0"

W, H = 1024, 768

# ─────────────────────────────────────────────────────────────────────────────
#  GLOBAL STYLESHEET
# ─────────────────────────────────────────────────────────────────────────────
SS = """
QWidget {
    background: #ECEFF4;
    color: #1A2332;
    font-family: 'Inter', 'DM Sans', 'Liberation Sans', sans-serif;
}
QDialog { background: #FFFFFF; }
QFrame#Card {
    background: #FFFFFF;
    border: 1px solid #DDE3EA;
    border-left: 4px solid #1B8A4C;
    border-radius: 10px;
}
QFrame#Panel {
    background: #FFFFFF;
    border: 1px solid #DDE3EA;
    border-radius: 8px;
}
QTextEdit {
    background: #FFFFFF;
    border: 1px solid #DDE3EA;
    color: #1A2332;
    font-size: 8pt;
    font-family: 'Roboto Mono', 'Courier New';
}
QScrollBar:vertical          { background: #ECEFF4; width: 32px; }
QScrollBar::handle:vertical  { background: #C8D0DA; border-radius: 16px; min-height: 40px; }
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical { height: 0; }

QPushButton#BG {
    background: #E6F4EE; border: 1.5px solid #1B8A4C;
    border-radius: 6px; color: #1B8A4C;
    font-size: 10pt; font-weight: bold;
}
QPushButton#BG:pressed   { background: #D2EDE0; }
QPushButton#BG:disabled  { background: #F0F0F0; border-color: #C8D0DA; color: #A0AAB8; }

QPushButton#BC {
    background: #FFFFFF; border: 2px solid #1565C0;
    border-radius: 12px; color: #1565C0;
    font-size: 10pt; font-weight: 700;
    padding: 4px 18px;
    min-height: 34px;
}
QPushButton#BC:hover     { background: #F5FAFF; border-color: #0D4A8A; color: #0D4A8A; }
QPushButton#BC:pressed   { background: #E3EEFA; border-color: #0D4A8A; color: #0D4A8A; }
QPushButton#BC:disabled  { background: #F8FAFB; border-color: #C8D0DA; color: #A0AAB8; }
QPushButton#BC:focus     { border: 2px solid #1565C0; }

QPushButton#BA {
    background: #FFF3E0; border: 1.5px solid #C06000;
    border-radius: 6px; color: #C06000;
    font-size: 10pt; font-weight: bold;
}
QPushButton#BA:pressed { background: #FFE4C0; }

QPushButton#BR {
    background: #FFEBEE; border: 1.5px solid #C62828;
    border-radius: 6px; color: #C62828;
    font-size: 10pt; font-weight: bold;
}
QPushButton#BR:pressed { background: #FFD6D6; }

QPushButton#BM {
    background: #EDE7F6; border: 1.5px solid #5E35B1;
    border-radius: 6px; color: #5E35B1;
    font-size: 10pt; font-weight: bold;
}
QPushButton#BM:pressed { background: #DDD5F0; }

QPushButton#BX {
    background: #F8FAFB; border: 1px solid #C8D0DA;
    border-radius: 6px; color: #4A5568;
    font-size: 10pt; font-weight: bold;
}
QPushButton#BX:pressed { background: #ECEFF4; }

QPushButton#NK {
    background: #111; border: 1px solid #2a2a2a;
    border-radius: 8px; color: #DDD;
    font-size: 18pt; font-weight: bold;
}
QPushButton#NK:pressed   { background: #222; border-color: #1565C0; }

QPushButton#NO {
    background: #001520; border: 1px solid #1565C0;
    border-radius: 8px; color: #1565C0;
    font-size: 14pt; font-weight: bold;
}
QPushButton#NO:pressed { background: #002030; }

QPushButton#NOK {
    background: #002800; border: 2px solid #1B8A4C;
    border-radius: 8px; color: #1B8A4C;
    font-size: 14pt; font-weight: bold;
}
QPushButton#NOK:pressed { background: #003d00; }

QPushButton#ND {
    background: #1a0a00; border: 1px solid #C06000;
    border-radius: 8px; color: #C06000;
    font-size: 14pt; font-weight: bold;
}
QPushButton#ND:pressed { background: #260e00; }

QPushButton#CK {
    background: #111; border: 1px solid #2a2a2a;
    border-radius: 5px; color: #aaa;
    font-size: 11pt; font-weight: bold;
}
QPushButton#CK:pressed { background: #222; border-color: #C06000; }

QPushButton#EF {
    background: #F8FAFB; border: 1px solid #C8D0DA;
    border-radius: 6px; color: #1565C0;
    font-size: 12pt; font-family: 'Roboto Mono', 'Courier New';
    text-align: left; padding-left: 12px;
}
QPushButton#EF:pressed { background: #E3EEFA; border-color: #1565C0; }

QPushButton#SB {
    background: #F8FAFB; border: 1px solid #DDE3EA;
    border-radius: 8px; color: #8A94A6;
    font-size: 8pt; font-weight: bold;
    padding: 6px 8px; text-align: left;
}
QPushButton#SB:checked { border-color: #1B8A4C88; color: #1B8A4C; }

/* CSV export page: a small, consistent action bar for the touchscreen. */
QPushButton#CSVBack, QPushButton#CSVRefresh, QPushButton#CSVExport {
    border-radius: 8px;
    font-size: 10pt;
    font-weight: bold;
    padding: 0 14px;
}
QPushButton#CSVBack {
    background: #FFFFFF; border: 2px solid #1565C0; color: #1565C0;
}
QPushButton#CSVBack:pressed { background: #E3EEFA; }
QPushButton#CSVRefresh {
    background: #F8FAFB; border: 1px solid #B9C4D2; color: #4A5568;
}
QPushButton#CSVRefresh:pressed { background: #E8EDF3; }
QPushButton#CSVExport {
    background: #1B8A4C; border: 2px solid #1B8A4C; color: #FFFFFF;
}
QPushButton#CSVExport:pressed { background: #126B39; border-color: #126B39; }
QPushButton#CSVExport:disabled { background: #AFCDBB; border-color: #AFCDBB; }
QWidget#CSVHeader {
    background: #FFFFFF;
    border-bottom: 1px solid #DDE3EA;
}
QPushButton#TopClose {
    background: #FFFFFF;
    border: 2px solid #C62828;
    border-radius: 8px;
    color: #C62828;
    font-size: 20pt;
    font-weight: bold;
}
QPushButton#TopClose:pressed { background: #FFEBEE; }
"""


# ─────────────────────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────────────────────
CFG_PATH  = Path(__file__).parent / "rail_config.json"
DEFAULT_CSV_DIR = os.environ.get("RAIL_CSV_DIR", "/home/debian/surveys")
_ENCODER_PPR = float(os.environ.get("RAIL_ENCODER_PPR", "400"))
_WHEEL_DIAMETER_MM = float(os.environ.get("RAIL_WHEEL_DIAMETER_MM", "50"))
if _ENCODER_PPR <= 0 or _WHEEL_DIAMETER_MM <= 0:
    raise ValueError("Encoder PPR and wheel diameter must both be greater than zero.")
_ENCODER_DECODE = float(os.environ.get("RAIL_ENCODER_DECODE", "2.0"))
_ENCODER_SCALE_MM_PER_COUNT = math.pi * _WHEEL_DIAMETER_MM / (_ENCODER_PPR * _ENCODER_DECODE)
_DEF = {
    "csv_dir":   DEFAULT_CSV_DIR,
    "hl_sec":    30,
    "server":    "8.8.8.8",
    "lte_iface": "usb2",
    "calibration_password": "Patil@123",
    "encoder":   {"scale": _ENCODER_SCALE_MM_PER_COUNT, "factor": 1.123596, "calibrated": True},
    "adc":       {"zero": 2048,  "mpc": 0.0684, "factor": 1.0, "calibrated": False},
    "incl":      {"offset": 0.0, "factor": 1.0, "calibrated": False},
    "gnss":      {"ref_ch": 0.0, "calibrated": False},
    "permissible_limits": {
        "track_mode": "straight",
        "nominal_gauge_mm": 1676.0,
        "gauge_min_mm": 1670.0,
        "gauge_max_mm": 1682.0,
        "gauge_mm": 6.0,
        "crosslevel_mm": 200.0,
        "twist_mm_per_m": None,
        "distance_m": None,
    },
}
def load_cfg():
    if CFG_PATH.exists():
        try:
            d = json.loads(CFG_PATH.read_text())
            for k, v in _DEF.items():
                d.setdefault(k, v)
                if isinstance(v, dict):
                    for kk, vv in v.items():
                        d[k].setdefault(kk, vv)
            d["encoder"]["scale"] = _ENCODER_SCALE_MM_PER_COUNT
            if os.environ.get("RAIL_CSV_DIR"):
                d["csv_dir"] = os.environ["RAIL_CSV_DIR"]
            if os.environ.get("RAIL_CALIBRATION_PASSWORD"):
                d["calibration_password"] = os.environ["RAIL_CALIBRATION_PASSWORD"]
            # Early installations used eth1 in the sample configuration.  The
            # Quectel EC200U exposed by this BBB is a CDC-ECM device named usb2.
            # Migrate that stale default automatically when the real interface
            # is present, rather than reporting a permanently empty signal bar.
            if d.get("lte_iface") == "eth1" and os.path.exists("/sys/class/net/usb2"):
                d["lte_iface"] = "usb2"
            return d
        except Exception:
            pass
        pass
        pass
    return {k: (dict(v) if isinstance(v, dict) else v) for k, v in _DEF.items()}


def save_cfg(cfg):
    try:
        CFG_PATH.write_text(json.dumps(cfg, indent=2))
    except Exception as e:
        print(f"[CFG] {e}")


NOMINAL_GAUGE_MM = 1676.0
TRACK_GAUGE_MODES = {
    "straight": {
        "label": "Straight (-6 to +6 mm)",
        "short": "STRAIGHT (-6/+6)",
        "min_tol": -6.0,
        "max_tol": 6.0,
    },
    "curve_ge_440": {
        "label": "Curve R>=440m (-6 to +15 mm)",
        "short": "CURVE >=440m (-6/+15)",
        "min_tol": -6.0,
        "max_tol": 15.0,
    },
    "curve_lt_440": {
        "label": "Curve R<440m (up to +20 mm)",
        "short": "CURVE <440m (+20)",
        "min_tol": -6.0,
        "max_tol": 20.0,
    },
}


def normalize_track_mode(mode):
    key = str(mode or "straight").strip().lower()
    aliases = {
        "straight": "straight",
        "str": "straight",
        "curve_ge_440": "curve_ge_440",
        "curve>=440": "curve_ge_440",
        "curve_lt_440": "curve_lt_440",
        "curve<440": "curve_lt_440",
    }
    return aliases.get(key, key if key in TRACK_GAUGE_MODES else "straight")


def gauge_limits_for_mode(cfg=None, mode=None):
    """Return (mode_key, nominal_mm, min_tol, max_tol, min_abs_mm, max_abs_mm)."""
    limits = (cfg or load_cfg()).get("permissible_limits", {})
    mode_key = normalize_track_mode(mode if mode is not None else limits.get("track_mode", "straight"))
    nominal = float(limits.get("nominal_gauge_mm") or NOMINAL_GAUGE_MM)
    spec = TRACK_GAUGE_MODES[mode_key]
    min_tol = float(spec["min_tol"])
    max_tol = float(spec["max_tol"])
    return mode_key, nominal, min_tol, max_tol, nominal + min_tol, nominal + max_tol


def set_track_mode(cfg, mode):
    """Persist selected track geometry mode into cfg['permissible_limits']."""
    mode_key, nominal, _min_tol, max_tol, min_abs, max_abs = gauge_limits_for_mode(cfg, mode=mode)
    limits = cfg.setdefault("permissible_limits", {})
    limits["track_mode"] = mode_key
    limits["nominal_gauge_mm"] = nominal
    limits["gauge_min_mm"] = min_abs
    limits["gauge_max_mm"] = max_abs
    limits["gauge_mm"] = max_tol
    if limits.get("crosslevel_mm") is None:
        limits["crosslevel_mm"] = 200.0
    limits["twist_mm_per_m"] = None
    limits["distance_m"] = None
    if "sync_runtime_thresholds" in globals():
        sync_runtime_thresholds(cfg)
    return mode_key


def permissible_limits(cfg=None):
    """Editable saved-data and live-display alarm limits."""
    active_cfg = cfg or load_cfg()
    limits = active_cfg.get("permissible_limits", {})
    mode_key, nominal, min_tol, max_tol, min_abs, max_abs = gauge_limits_for_mode(active_cfg)
    cross = limits.get("crosslevel_mm")
    cross_val = 200.0 if cross is None else float(cross)
    return {
        "Gauge": max_tol,
        "GaugeMinMM": min_abs,
        "GaugeMaxMM": max_abs,
        "GaugeMinTol": min_tol,
        "GaugeMaxTol": max_tol,
        "NominalGaugeMM": nominal,
        "TrackMode": mode_key,
        "Crosslevel": cross_val,
        "Twist": None,
    }


def exceeds_permissible(field, value, cfg=None):
    norm = re.sub(r"[^a-z]", "", str(field).lower())
    try:
        num = float(value)
    except (TypeError, ValueError):
        return False
    active_cfg = cfg if cfg is not None else load_cfg()
    if norm in ("gauge", "trackgauge", "trackgaugemm", "gaugemm"):
        _m, _nom, min_tol, max_tol, min_abs, max_abs = gauge_limits_for_mode(active_cfg)
        if abs(num) > 1000.0:
            return num < min_abs or num > max_abs
        return num < min_tol or num > max_tol
    if norm in ("twist", "twistmmm", "twistmmperm", "cumulativetilt",
                "distance", "dist", "distm", "chainage"):
        return False
    if norm in ("crosslevel", "crosslevelmm", "cross", "crossover", "crosslvl", "absolutetilt"):
        limit = permissible_limits(active_cfg).get("Crosslevel")
        return limit is not None and abs(num) > float(limit)
    return False


# ─────────────────────────────────────────────────────────────────────────────
#  HARDWARE
# ─────────────────────────────────────────────────────────────────────────────
EQEP_PATH = ("/sys/devices/platform/ocp/48304000.epwmss"
             "/48304180.eqep/counter/count0/count")
ADC_PATH  = os.environ.get("RAIL_ADC_PATH", "/sys/bus/iio/devices/iio:device0/in_voltage0_raw")
SPI_DEV   = "/dev/spidev1.0"
HW_SIM    = not (os.path.exists(EQEP_PATH) or os.path.exists("/dev/gpiochip1") or os.path.exists("/dev/shm/rail_sensor_shm"))


def _sysfs(path, default="0"):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return default


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _lbl(text, color="#888", pt=9, bold=False):
    l = QLabel(text)
    w = "bold" if bold else "normal"
    l.setStyleSheet(f"color:{color}; font-size:{pt}pt; font-weight:{w};")
    l.setWordWrap(True)
    return l


def _logbox(h=90):
    t = QTextEdit()
    t.setReadOnly(True)
    t.setFixedHeight(h)
    return t


def _vline():
    f = QFrame()
    f.setFrameShape(QFrame.VLine)
    f.setStyleSheet("color:#1a1a1a; max-width:1px;")
    return f


def _btn(label, name, h=48, w=None):
    b = QPushButton(label)
    b.setObjectName(name)
    b.setFixedHeight(h)
    if w:
        b.setFixedWidth(w)
    return b


def _shorten(path, n=34):
    return ("…" + path[-(n - 1):]) if len(path) > n else path


def _run_cmd(cmd, callback, parent):
    proc = QProcess(parent)
    proc.setProcessChannelMode(QProcess.MergedChannels)

    def _done():
        out = proc.readAllStandardOutput().data().decode(errors="replace")
        callback(out)

    proc.finished.connect(_done)
    proc.start("sh", ["-c", cmd])
    return proc


# ═════════════════════════════════════════════════════════════════════════════
#  NUMPAD DIALOG
# ═════════════════════════════════════════════════════════════════════════════
class NumpadDialog(QDialog):
    def __init__(self, title, current_val="0", decimals=1,
                 min_val=None, max_val=None, unit="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setStyleSheet(SS + "QDialog{background:#0e0e0e;}")
        self.setFixedSize(380, 480)

        self._dec  = decimals
        self._min  = min_val
        self._max  = max_val
        self._unit = unit
        self._buf  = str(current_val).strip()
        self._result = None

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)

        t = QLabel(title.upper())
        t.setAlignment(Qt.AlignCenter)
        t.setStyleSheet(
            f"color:{CYAN}; font-size:11pt; font-weight:bold; letter-spacing:2px;"
        )
        root.addWidget(t)

        self._disp = QLabel()
        self._disp.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._disp.setFixedHeight(58)
        self._disp.setStyleSheet(
            f"background:#060606; border:1px solid {CYAN}55; border-radius:6px;"
            f" color:{CYAN}; font-size:24pt; font-family:'Courier New';"
            f" padding-right:10px; font-weight:bold;"
        )
        root.addWidget(self._disp)

        g = QGridLayout()
        g.setSpacing(6)
        rows = [("7","8","9"), ("4","5","6"), ("1","2","3"), (".","0","⌫")]
        for r, trio in enumerate(rows):
            for c, lbl in enumerate(trio):
                if lbl == "⌫":
                    b = _btn(lbl, "ND", 64, 86)
                    b.clicked.connect(self._del)
                elif lbl == ".":
                    b = _btn(lbl, "NO", 64, 86)
                    b.clicked.connect(lambda _, ch=lbl: self._press(ch))
                    b.setEnabled(decimals > 0)
                else:
                    b = _btn(lbl, "NK", 64, 86)
                    b.clicked.connect(lambda _, ch=lbl: self._press(ch))
                g.addWidget(b, r, c)

        pm  = _btn("±",   "NO",  64, 86); pm.clicked.connect(self._sign);  g.addWidget(pm,  4, 0)
        clr = _btn("CLR", "NO",  64, 86); clr.clicked.connect(self._clear); g.addWidget(clr, 4, 1)
        ok  = _btn("✓ OK","NOK", 64, 86); ok.clicked.connect(self._confirm); g.addWidget(ok,  4, 2)
        root.addLayout(g)

        cnc = _btn("✕  CANCEL", "BR", 46)
        cnc.clicked.connect(self.reject)
        root.addWidget(cnc)
        self._refresh()

        if parent:
            pg = parent.geometry()
            self.move(pg.x() + (pg.width()  - self.width())  // 2,
                      pg.y() + (pg.height() - self.height()) // 2)

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


# ═════════════════════════════════════════════════════════════════════════════
#  POPUP KEYBOARD DIALOG  (replaces InlineTextPad)
#  — A proper floating QDialog with QWERTY + numpad, shown on demand
# ═════════════════════════════════════════════════════════════════════════════
class PopupKeyboardDialog(QDialog):
    """
    Floating keyboard dialog.
    Usage:
        dlg = PopupKeyboardDialog(field_name, current_value, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            value = dlg.get_value()
    """
    def __init__(self, field_title="Enter Value", current="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(field_title)
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setFixedSize(720, 300)
        self.setStyleSheet(
            "QDialog { background:#FFFFFF; border:2px solid #1565C0;"
            " border-radius:16px; }")

        self._buf    = current or ""
        self._result = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # ── Title + display ───────────────────────────────────────────────────
        hdr = QHBoxLayout()
        t = QLabel(field_title.upper())
        t.setStyleSheet(
            f"color:{CYAN}; font-size:11pt; font-weight:bold; letter-spacing:2px;"
            " background:transparent;")
        self._disp = QLabel(self._buf or "—")
        self._disp.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._disp.setFixedHeight(40)
        self._disp.setMinimumWidth(300)
        self._disp.setStyleSheet(
            f"background:#F8FAFB; border:1.5px solid {CYAN}; border-radius:8px;"
            f" color:{CYAN}; font-size:15pt; font-family:'Courier New';"
            f" padding-right:10px; font-weight:bold;")
        hdr.addWidget(t, 0)
        hdr.addStretch()
        hdr.addWidget(self._disp, 1)
        root.addLayout(hdr)

        # ── Key area: Single Laptop Layout ──────────────────────────────
        key_area = QVBoxLayout()
        key_area.setSpacing(5)

        _KEY_SS = (
            "QPushButton { background:#F8FAFB; border:1px solid #D8E1EB;"
            " border-radius:9px; color:#334155; font-size:10pt; font-weight:700; }"
            "QPushButton:hover { background:#FFFFFF; border-color:#1565C0; }"
            "QPushButton:pressed { background:#EAF3FF; }")
        
        for row_idx, row_str in enumerate(["1234567890", "QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"]):
            rl = QHBoxLayout()
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(4)
            if row_idx == 2:
                rl.addSpacing(24)
            elif row_idx == 3:
                rl.addSpacing(48)
            for ch in row_str:
                b = QPushButton(ch)
                b.setFixedSize(52, 36)
                b.setStyleSheet(_KEY_SS)
                b.clicked.connect(lambda _, v=ch: self._char(v))
                rl.addWidget(b)
            rl.addStretch()
            key_area.addLayout(rl)

        # Space + special row
        sp_row = QHBoxLayout()
        sp_row.setSpacing(4)
        for ch, lbl, w in [
            (" ", "SPACE", 200),
            ("-", "-", 50),
            (".", ".", 50),
            ("/", "/", 50),
            ("@", "@", 50),
            ("_", "_", 50),
            ("#", "#", 50),
            ("$", "$", 50),
            ("*", "*", 50),
            ("!", "!", 50),
        ]:
            b = QPushButton(lbl)
            b.setFixedSize(w, 36)
            b.setStyleSheet(_KEY_SS)
            b.clicked.connect(lambda _, v=ch: self._char(v))
            sp_row.addWidget(b)
        sp_row.addStretch()
        key_area.addLayout(sp_row)

        root.addLayout(key_area)

        # ── Bottom action row ─────────────────────────────────────────────────
        bot = QHBoxLayout()
        bot.setSpacing(8)
        _ACT_SS_BACK = (
            "QPushButton { background:#F8FAFB; border:1px solid #D8E1EB;"
            " border-radius:10px; color:#5B6575; font-size:10pt; font-weight:700; }"
            "QPushButton:pressed { background:#EEF3F8; }")
        _ACT_SS_CLR = _ACT_SS_BACK
        _ACT_SS_DONE = (
            f"QPushButton {{ background:{CYAN}; border:1px solid {CYAN};"
            f" border-radius:10px; color:#FFFFFF; font-size:10pt; font-weight:700; }}"
            f"QPushButton:pressed {{ background:#0D4A8A; }}")
        _ACT_SS_CANCEL = (
            f"QPushButton {{ background:#FFEBEE; border:1px solid {RED};"
            f" border-radius:10px; color:{RED}; font-size:10pt; font-weight:700; }}"
            f"QPushButton:pressed {{ background:#FFD6D6; }}")

        for txt, fn, ss, flex in [
            ("⌫  BACK",   self._backspace, _ACT_SS_BACK,   1),
            ("CLEAR",      self._clear,     _ACT_SS_CLR,    1),
            ("✕ CANCEL",   self.reject,     _ACT_SS_CANCEL, 1),
            ("✓  DONE",    self._confirm,   _ACT_SS_DONE,   2),
        ]:
            b = QPushButton(txt)
            b.setFixedHeight(36)
            b.setStyleSheet(ss)
            b.clicked.connect(fn)
            bot.addWidget(b, flex)
        root.addLayout(bot)

        # Centre over parent window
        if parent:
            pg = parent.window().geometry()
            self.move(pg.x() + (pg.width()  - self.width())  // 2,
                      pg.y() + (pg.height() - self.height()) // 2)

    def _char(self, ch):
        self._buf += ch
        self._disp.setText(self._buf or "—")

    def _backspace(self):
        self._buf = self._buf[:-1]
        self._disp.setText(self._buf or "—")

    def _clear(self):
        self._buf = ""
        self._disp.setText("—")

    def _confirm(self):
        self._result = self._buf
        self.accept()

    def get_value(self):
        return self._result


# ═════════════════════════════════════════════════════════════════════════════
#  TEXT PICKER DIALOG
# ═════════════════════════════════════════════════════════════════════════════
class TextPickerDialog(QDialog):
    def __init__(self, title, presets=None, current="", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setStyleSheet(SS + "QDialog{background:#0e0e0e;}")
        self.setFixedSize(680, 560)

        self._buf    = current or ""
        self._result = None

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(6)

        hdr = QHBoxLayout()
        t = QLabel(title.upper())
        t.setStyleSheet(
            f"color:{AMBER}; font-size:11pt; font-weight:bold; letter-spacing:2px;"
        )
        self._disp = QLabel(self._buf or "—")
        self._disp.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._disp.setFixedHeight(44)
        self._disp.setMinimumWidth(260)
        self._disp.setStyleSheet(
            f"background:#060606; border:1px solid {AMBER}55; border-radius:5px;"
            f" color:{AMBER}; font-size:16pt; font-family:'Courier New';"
            f" padding-right:8px; font-weight:bold;"
        )
        hdr.addWidget(t, 0)
        hdr.addStretch()
        hdr.addWidget(self._disp, 1)
        root.addLayout(hdr)

        if presets:
            pg = QGridLayout()
            pg.setSpacing(5)
            per_row = 4
            for i, p in enumerate(presets):
                b = QPushButton(p)
                b.setObjectName("BA")
                b.setFixedHeight(44)
                b.clicked.connect(lambda _, v=p: self._pick(v))
                pg.addWidget(b, i // per_row, i % per_row)
            root.addLayout(pg)

        kb_rows = ["1234567890", "QWERTYUIOP", "ASDFGHJKL-", "ZXCVBNM /."]
        for row_str in kb_rows:
            rl = QHBoxLayout()
            rl.setSpacing(3)
            for ch in row_str:
                label = "SPC" if ch == " " else ch
                b = QPushButton(label)
                b.setObjectName("CK")
                b.setFixedSize(58, 42)
                b.clicked.connect(lambda _, c=ch: self._char(c))
                rl.addWidget(b)
            root.addLayout(rl)

        br = QHBoxLayout()
        br.setSpacing(6)
        bs  = _btn("⌫  BACK", "ND",  42); bs.clicked.connect(self._bksp)
        clr = _btn("CLR",     "BX",  42); clr.clicked.connect(self._clr)
        br.addWidget(bs, 1)
        br.addWidget(clr, 1)
        root.addLayout(br)

        bot = QHBoxLayout()
        bot.setSpacing(8)
        ok  = _btn("✓  CONFIRM", "BG", 46); ok.clicked.connect(self._confirm)
        cnc = _btn("✕  CANCEL",  "BR", 46); cnc.clicked.connect(self.reject)
        bot.addWidget(cnc, 1)
        bot.addWidget(ok,  1)
        root.addLayout(bot)

        if parent:
            pg = parent.geometry()
            self.move(pg.x() + (pg.width()  - self.width())  // 2,
                      pg.y() + (pg.height() - self.height()) // 2)

    def _pick(self, v):
        self._buf = v
        self._disp.setText(v or "—")

    def _char(self, ch):
        self._buf += ch
        self._disp.setText(self._buf or "—")

    def _bksp(self):
        self._buf = self._buf[:-1]
        self._disp.setText(self._buf or "—")

    def _clr(self):
        self._buf = ""
        self._disp.setText("—")

    def _confirm(self):
        self._result = self._buf
        self.accept()

    def get_value(self):
        return self._result


# ═════════════════════════════════════════════════════════════════════════════
#  TOUCH TEXT FIELD  — tappable label that opens PopupKeyboardDialog
# ═════════════════════════════════════════════════════════════════════════════
class TouchTextField(QPushButton):
    """A button that looks like a text input; tapping opens the popup keyboard."""
    value_changed = pyqtSignal(str)

    def __init__(self, placeholder="", field_title="", parent=None):
        super().__init__(parent)
        self._placeholder   = placeholder
        self._field_title   = field_title or placeholder
        self._value         = ""
        self.setFixedHeight(38)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._open_keyboard)
        self._refresh()

    def _refresh(self):
        if self._value:
            self.setText(self._value)
            self.setStyleSheet(
                "QPushButton { background:#F8FAFB; border:1px solid #C8D0DA; border-radius:8px;"
                " padding:0 10px; color:#1A2332; font-size:10pt; text-align:left; }"
                "QPushButton:hover { background:#FFFFFF; border-color:#1565C0; }"
                "QPushButton:pressed { background:#EAF3FF; }")
        else:
            self.setText(self._placeholder)
            self.setStyleSheet(
                "QPushButton { background:#F8FAFB; border:1px solid #C8D0DA; border-radius:8px;"
                " padding:0 10px; color:#94A3B8; font-size:10pt; text-align:left; }"
                "QPushButton:hover { background:#FFFFFF; border-color:#1565C0; }"
                "QPushButton:pressed { background:#EAF3FF; }")

    def _open_keyboard(self):
        # Find the top-level window to centre dialog over
        top = self.window()
        dlg = PopupKeyboardDialog(self._field_title, self._value, parent=top)
        if dlg.exec_() == QDialog.Accepted:
            v = dlg.get_value()
            if v is not None:
                self._value = v
                self._refresh()
                self.value_changed.emit(self._value)

    def value(self):
        return self._value

    def set_value(self, value):
        self._value = value
        self._refresh()


# ═════════════════════════════════════════════════════════════════════════════
#  STEPPER
# ═════════════════════════════════════════════════════════════════════════════
class Stepper(QWidget):
    changed = pyqtSignal(float)

    def __init__(self, val=0, step=1, dec=0,
                 lo=0, hi=9999, unit="", title="VALUE", parent=None):
        super().__init__(parent)
        self._step = step
        self._dec  = dec
        self._lo   = lo
        self._hi   = hi
        self._unit = unit
        self._title = title
        self._val  = float(val)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self._btn = QPushButton()
        self._btn.setFixedHeight(50)
        self._btn.setStyleSheet(
            "QPushButton {"
            " background:#FFFFFF; border:1px solid #C8D0DA; border-radius:12px;"
            " color:#1565C0; font-size:12pt; font-weight:700;"
            " padding:0 16px; text-align:center; }"
            "QPushButton:hover { background:#F8FAFB; border-color:#1565C0; }"
            "QPushButton:pressed { background:#EAF3FF; }")
        self._btn.clicked.connect(self._open_pad)
        lay.addWidget(self._btn)

        self._pad = QFrame()
        self._pad.setObjectName("Panel")
        self._pad.hide()
        pad_l = QVBoxLayout(self._pad)
        pad_l.setContentsMargins(12, 12, 12, 12)
        pad_l.setSpacing(8)

        self._pad_disp = QLabel()
        self._pad_disp.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._pad_disp.setFixedHeight(46)
        self._pad_disp.setStyleSheet(
            "background:#F8FAFB; border:1px solid #DDE3EA; border-radius:10px;"
            " color:#1565C0; font-size:16pt; font-weight:700; padding:0 14px;")
        pad_l.addWidget(self._pad_disp)

        g = QGridLayout()
        g.setSpacing(8)
        rows = [("7", "8", "9"), ("4", "5", "6"), ("1", "2", "3"), (".", "0", "⌫")]
        for r, row in enumerate(rows):
            for c, ch in enumerate(row):
                b = QPushButton(ch)
                b.setFixedSize(64, 52)
                b.setStyleSheet(
                    "QPushButton { background:#FFFFFF; border:1px solid #DDE3EA;"
                    " border-radius:10px; color:#334155; font-size:14pt; font-weight:700; }"
                    "QPushButton:hover { background:#F8FAFB; border-color:#1565C0; }"
                    "QPushButton:pressed { background:#EAF3FF; }")
                if ch == "⌫":
                    b.clicked.connect(self._pad_backspace)
                else:
                    b.clicked.connect(lambda _, v=ch: self._pad_char(v))
                    if ch == "." and self._dec == 0:
                        b.setEnabled(False)
                g.addWidget(b, r, c)
        pad_l.addLayout(g)

        action_l = QHBoxLayout()
        action_l.setSpacing(8)
        clr = QPushButton("CLEAR")
        clr.setFixedHeight(44)
        clr.setStyleSheet(
            "QPushButton { background:#FFFFFF; border:1px solid #DDE3EA; border-radius:10px;"
            " color:#5B6575; font-size:10pt; font-weight:700; }"
            "QPushButton:hover { background:#F8FAFB; border-color:#BAC8D8; }")
        clr.clicked.connect(self._pad_clear)
        if self._lo < 0:
            sign = QPushButton("+/-")
            sign.setFixedHeight(44)
            sign.setStyleSheet(
                "QPushButton { background:#FFFFFF; border:1px solid #DDE3EA;"
                " border-radius:10px; color:#5B6575; font-size:10pt;"
                " font-weight:700; }"
                "QPushButton:hover { background:#F8FAFB; border-color:#BAC8D8; }"
            )
            sign.clicked.connect(self._pad_toggle_sign)
            action_l.addWidget(sign)
        done = QPushButton("DONE")
        done.setFixedHeight(44)
        done.setStyleSheet(
            "QPushButton { background:#E3EEFA; border:1px solid #1565C0; border-radius:10px;"
            " color:#1565C0; font-size:10pt; font-weight:700; }"
            "QPushButton:hover { background:#D8E9FD; }")
        done.clicked.connect(self._pad_commit)
        action_l.addWidget(clr)
        action_l.addWidget(done)
        # Inline pad is retained for backward compatibility if ever shown,
        # but tapping the stepper opens the clean floating PopupKeyboardDialog.
        self._buf = ""
        self._refresh()

    def _refresh(self):
        suf = f"  {self._unit}" if self._unit else ""
        self._btn.setText(f"{self._val:.{self._dec}f}{suf}")

    def _open_pad(self):
        top = self.window()
        cur_str = f"{self._val:.{self._dec}f}" if self._dec > 0 else f"{int(round(self._val))}"
        dlg = PopupKeyboardDialog(self._title, cur_str, parent=top)
        if dlg.exec_() == QDialog.Accepted:
            v_str = dlg.get_value()
            if v_str is not None:
                try:
                    v = float(v_str.strip())
                except ValueError:
                    v = self._val
                v = max(self._lo, min(self._hi, v))
                self._val = round(v, self._dec)
                self._refresh()
                self.changed.emit(self._val)

    def _pad_char(self, ch):
        if ch == "." and "." in self._buf:
            return
        if "." in self._buf and ch != ".":
            after_dot = self._buf.split(".")[1]
            if len(after_dot) >= self._dec:
                return
        if self._buf in ("0", "0" * max(1, self._dec + 2)) and ch != ".":
            self._buf = ch
        else:
            self._buf += ch
        self._pad_refresh()

    def _pad_backspace(self):
        self._buf = self._buf[:-1] if self._buf else ""
        self._pad_refresh()

    def _pad_clear(self):
        self._buf = ""
        self._pad_refresh()

    def _pad_toggle_sign(self):
        if self._buf.startswith("-"):
            self._buf = self._buf[1:]
        else:
            self._buf = "-" + (self._buf or "0")
        self._pad_refresh()

    def _pad_refresh(self):
        txt = self._buf or "0"
        suf = f" {self._unit}" if self._unit else ""
        self._pad_disp.setText(txt + suf)

    def _pad_commit(self):
        try:
            v = float(self._buf or 0)
        except ValueError:
            v = self._val
        v = max(self._lo, min(self._hi, v))
        self._val = round(v, self._dec)
        self._refresh()
        self._pad.hide()
        self.changed.emit(self._val)

    def value(self):
        return self._val

    def set_value(self, v):
        self._val = float(v)
        self._refresh()


# ═════════════════════════════════════════════════════════════════════════════
#  PRESET TILES
# ═════════════════════════════════════════════════════════════════════════════
class PresetTiles(QWidget):
    changed = pyqtSignal(str)

    def __init__(self, options, selected="", color=CYAN, parent=None):
        super().__init__(parent)
        self._color = color
        self._btns  = {}
        self._sel   = ""

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)

        for opt in options:
            b = QPushButton(opt)
            b.setFixedHeight(44)
            b.setMinimumWidth(60)
            self._style(b, False)
            b.clicked.connect(lambda _, v=opt: self._pick(v))
            lay.addWidget(b)
            self._btns[opt] = b

        lay.addStretch()
        target = selected if selected in self._btns else (options[0] if options else "")
        if target:
            self._pick(target)

    def _style(self, btn, active):
        c = self._color
        if active:
            btn.setStyleSheet(
                f"QPushButton{{background:{c}22; border:2px solid {c};"
                f" border-radius:5px; color:{c}; font-size:9pt;"
                f" font-weight:bold; padding:0 10px;}}"
                f"QPushButton:pressed{{background:{c}33;}}"
            )
        else:
            btn.setStyleSheet(
                "QPushButton{background:#111; border:1px solid #2a2a2a;"
                " border-radius:5px; color:#444; font-size:9pt;"
                " font-weight:bold; padding:0 10px;}"
                "QPushButton:pressed{background:#1a1a1a;}"
            )

    def _pick(self, v):
        for opt, btn in self._btns.items():
            self._style(btn, opt == v)
        self._sel = v
        self.changed.emit(v)

    def value(self):
        return self._sel


# ═════════════════════════════════════════════════════════════════════════════
#  SENSOR THREAD
# ═════════════════════════════════════════════════════════════════════════════
class SensorThread(QThread):
    data_ready = pyqtSignal(dict)
    fault      = pyqtSignal(str)
    motion     = pyqtSignal(bool)

    def __init__(self, cfg):
        super().__init__()
        self.cfg    = cfg
        self.active = False
        self._dist  = 0.0
        self._lastc = 0
        self._raw_total = 0

    def begin_encoder_calibration(self):
        raise RuntimeError("Encoder calibration is unavailable on this sensor backend.")

    def encoder_calibration_readout(self, start_count):
        raise RuntimeError("Encoder calibration is unavailable on this sensor backend.")

    def finish_encoder_calibration(self, start_count, true_m):
        raise RuntimeError("Encoder calibration is unavailable on this sensor backend.")

    def set_encoder_factor(self, factor, calibrated=True):
        raise RuntimeError("Encoder calibration is unavailable on this sensor backend.")

    def run(self):
        while True:
            if self.active:
                try:
                    d = self._sim() if HW_SIM else self._hw()
                    self.data_ready.emit(d)
                except Exception as e:
                    self.fault.emit(str(e))
            self.msleep(500)

    def _hw(self):
        cnt         = int(_sysfs(EQEP_PATH, "0"))
        delta       = cnt - self._lastc
        self._lastc = cnt
        self._raw_total += delta
        enc_factor = self.cfg["encoder"].get("factor", 1.0)
        adc_factor = self.cfg["adc"].get("factor", 1.0)
        self._dist = self._raw_total * self.cfg["encoder"]["scale"] * enc_factor / 1000.0
        self.motion.emit(delta != 0)
        raw   = int(_sysfs(ADC_PATH, str(self.cfg["adc"]["zero"])))
        
        gauge_source = os.environ.get("RAIL_GAUGE_SOURCE", "laser_adc")
        if gauge_source in ("laser", "laser_adc", "hg-c1200", "hgc1200"):
            laser_min_mm = float(os.environ.get("RAIL_LASER_MIN_MM", 160.0))
            laser_max_mm = float(os.environ.get("RAIL_LASER_MAX_MM", 0.0))
            laser_zero_mm = float(os.environ.get("RAIL_LASER_ZERO_MM", 80.0))
            laser_zero_raw = float(os.environ.get("RAIL_LASER_ZERO_RAW", -1.0))
            adc_max_raw = float(os.environ.get("RAIL_ADC_MAX_RAW", 4095.0))
            laser_auto_zero = int(os.environ.get("RAIL_LASER_AUTO_ZERO", 1)) != 0
            laser_display_min_mm = float(os.environ.get("RAIL_LASER_DISPLAY_MIN_MM", 0.0))
            laser_display_max_mm = float(os.environ.get("RAIL_LASER_DISPLAY_MAX_MM", 237.4))
            laser_display_offset_mm = float(os.environ.get("RAIL_LASER_DISPLAY_OFFSET_MM", 1523.5))
            laser_raw_at_plus = float(os.environ.get("RAIL_LASER_RAW_AT_MIN", os.environ.get("RAIL_LASER_RAW_AT_PLUS40", 3936.0)))
            laser_raw_at_zero = float(os.environ.get("RAIL_LASER_RAW_AT_ZERO", 1970.0))
            laser_raw_at_minus = float(os.environ.get("RAIL_LASER_RAW_AT_MAX", os.environ.get("RAIL_LASER_RAW_AT_MINUS40", 15.0)))
            if laser_display_min_mm > laser_display_max_mm:
                laser_display_min_mm, laser_display_max_mm = laser_display_max_mm, laser_display_min_mm
            
            if laser_auto_zero and (not hasattr(self, "_laser_zero_raw") or self._laser_zero_raw < 15.0):
                if raw >= 15:
                    self._laser_zero_raw = float(raw)
                
            output_mode = os.environ.get("RAIL_GAUGE_OUTPUT_MODE", "absolute")
            # laser_window is an absolute distance display and does not use a
            # startup reference. The legacy deviation modes still require one.
            has_ref = output_mode == "laser_window" or \
                (hasattr(self, "_laser_zero_raw") and self._laser_zero_raw >= 0.0) or \
                laser_zero_raw >= 0.0
            # The calibrated laser window deliberately includes raw=0: it is
            # the measured Panasonic endpoint, not an invalid ADC value.
            if not has_ref or (output_mode != "laser_window" and (raw < 15 or raw > 3060)):
                gauge = 0.0
            else:
                if hasattr(self, "_laser_zero_raw") and self._laser_zero_raw >= 0.0:
                    ref_laser = laser_min_mm + (self._laser_zero_raw / adc_max_raw) * (laser_max_mm - laser_min_mm)
                else:
                    ref_laser = laser_min_mm + (laser_zero_raw / adc_max_raw) * (laser_max_mm - laser_min_mm)
                
                ratio = float(raw) / adc_max_raw
                ratio = max(0.0, min(1.0, ratio))
                curr_laser = laser_min_mm + ratio * (laser_max_mm - laser_min_mm)
                curr_laser = max(0.0, min(160.0, curr_laser))
                
                cal_file_path = os.environ.get("RAIL_LASER_CAL_FILE", "laser_cal.json")
                cal_min = laser_raw_at_plus
                cal_max = laser_raw_at_minus
                cal_mid = None
                cal_points = None
                laser_display_mid_mm = float(os.environ.get("RAIL_LASER_DISPLAY_MID_MM", 162.4))
                try:
                    if os.path.exists(cal_file_path):
                        with open(cal_file_path, "r", encoding="utf-8") as f:
                            cal_data = json.load(f)
                        if "points" in cal_data and isinstance(cal_data["points"], list) and len(cal_data["points"]) >= 2:
                            cal_points = sorted(cal_data["points"], key=lambda p: float(p["mm"]))
                        cal_type = cal_data.get("calibration_type", "default_120_to_280")
                        cal_mpc = float(cal_data.get("mm_per_count", 0.03907204))
                        offset_mm = float(cal_data.get("track_gauge_offset_mm", cal_data.get("offset_mm", laser_display_offset_mm)))
                        if cal_type in ("default", "default_120_to_280"):
                            gauge = max(120.0, min(280.0, 280.0 - float(raw) * cal_mpc)) + offset_mm
                        elif "raw_at_max" in cal_data:
                            cal_max = float(cal_data["raw_at_max"])
                            laser_display_max_mm = float(cal_data.get("cal_max_mm", cal_data.get("max_mm", 237.4)))
                            gauge = max(0.0, laser_display_max_mm - (float(raw) - cal_max) * cal_mpc) + offset_mm
                        else:
                            gauge = max(120.0, min(280.0, 280.0 - float(raw) * cal_mpc)) + offset_mm
                        cross = self._spi()
                        twist = round(abs(cross) * 0.65 + random.gauss(0, 0.015), 2)
                        return {"gauge": round(gauge, 1), "cross": round(cross, 2),
                                "twist": twist, "dist": round(self._dist, 1),
                                "lat": 0.0, "lon": 0.0, "speed": 0.0}
                except Exception:
                    pass

                if cal_points and len(cal_points) >= 2:
                    raw_val = float(raw)
                    n = len(cal_points)
                    r_max = float(cal_points[0]["raw"])
                    r_min = float(cal_points[-1]["raw"])
                    if r_max > r_min:
                        if raw_val >= r_max - 0.5:
                            gauge = float(cal_points[0]["mm"])
                        elif raw_val <= r_min + 0.5:
                            gauge = float(cal_points[-1]["mm"])
                        else:
                            gauge = float(cal_points[0]["mm"])
                            for i in range(n - 1):
                                r0 = float(cal_points[i]["raw"])
                                r1 = float(cal_points[i + 1]["raw"])
                                if r0 >= raw_val >= r1:
                                    dr = r1 - r0
                                    if abs(dr) > 1e-6:
                                        frac = (raw_val - r0) / dr
                                        gauge = float(cal_points[i]["mm"]) + frac * (float(cal_points[i + 1]["mm"]) - float(cal_points[i]["mm"]))
                                    break
                    else:
                        if raw_val <= r_max + 5.0:
                            gauge = float(cal_points[0]["mm"])
                        elif raw_val >= r_min - 5.0:
                            gauge = float(cal_points[-1]["mm"])
                        else:
                            gauge = float(cal_points[0]["mm"])
                            for i in range(n - 1):
                                r0 = float(cal_points[i]["raw"])
                                r1 = float(cal_points[i + 1]["raw"])
                                if r0 <= raw_val <= r1:
                                    dr = r1 - r0
                                    if abs(dr) > 1e-6:
                                        frac = (raw_val - r0) / dr
                                        gauge = float(cal_points[i]["mm"]) + frac * (float(cal_points[i + 1]["mm"]) - float(cal_points[i]["mm"]))
                                    break
                else:
                    d_raw = cal_max - cal_min
                    if abs(d_raw) < 1.0:
                        gauge = laser_display_min_mm
                    else:
                        r_lo = min(cal_min, cal_max)
                        r_hi = max(cal_min, cal_max)
                        clamped_raw = max(r_lo, min(r_hi, float(raw)))
                        if cal_mid is None and abs(cal_max - cal_min) >= 1.0:
                            cal_mid = cal_max + 0.775409 * (cal_min - cal_max)
                            laser_display_mid_mm = 139.5
                        if cal_mid is not None and (
                            (cal_min > cal_max and cal_min > cal_mid > cal_max) or
                            (cal_min < cal_max and cal_min < cal_mid < cal_max)
                        ):
                            if (cal_min >= cal_max and clamped_raw >= cal_mid) or (cal_min < cal_max and clamped_raw <= cal_mid):
                                d1 = cal_mid - cal_min
                                gauge = laser_display_min_mm + (clamped_raw - cal_min) * (laser_display_mid_mm - laser_display_min_mm) / d1
                            else:
                                d2 = cal_max - cal_mid
                                gauge = laser_display_mid_mm + (clamped_raw - cal_mid) * (laser_display_max_mm - laser_display_mid_mm) / d2
                        else:
                            gauge = laser_display_min_mm + (clamped_raw - cal_min) * (laser_display_max_mm - laser_display_min_mm) / d_raw
        else:
            gauge = 1435.0 + (raw - self.cfg["adc"]["zero"]) * self.cfg["adc"]["mpc"] * adc_factor

        cross = self._spi()
        twist = round(abs(cross) * 0.65 + random.gauss(0, 0.015), 2)
        return {"gauge": round(gauge, 1), "cross": round(cross, 2),
                "twist": twist, "dist": round(self._dist, 1),
                "lat": 0.0, "lon": 0.0, "speed": 0.0}

    def _spi(self):
        try:
            import spidev
            spi = spidev.SpiDev()
            spi.open(1, 0); spi.max_speed_hz = 1_000_000; spi.mode = 0
            spi.xfer2([0xB4, 0x00, 0x00, 0x1F])
            self.msleep(5)
            r = spi.xfer2([0x04, 0x00, 0x00, 0x00]); spi.close()
            raw = (r[1] << 8) | r[2]
            if raw > 32767: raw -= 65536
            factor = self.cfg["incl"].get("factor", 1.0)
            return round((raw / 16384.0 * 90.0 - self.cfg["incl"]["offset"]) * factor, 2)
        except Exception:
            pass
        pass
        return round(random.gauss(0, 0.15) * self.cfg["incl"].get("factor", 1.0), 2)

    def _sim(self):
        enc_factor = self.cfg["encoder"].get("factor", 1.0)
        self._raw_total += round(200.0 / self.cfg["encoder"]["scale"])
        self._dist = self._raw_total * self.cfg["encoder"]["scale"] * enc_factor / 1000.0
        adc_factor = self.cfg["adc"].get("factor", 1.0)
        incl_factor = self.cfg["incl"].get("factor", 1.0)
        self.motion.emit(True)
        cross = round(random.gauss(0, 0.25) * incl_factor, 2)
        
        gauge_source = os.environ.get("RAIL_GAUGE_SOURCE", "laser_adc")
        if gauge_source in ("laser", "laser_adc", "hg-c1200", "hgc1200"):
            ref_laser = 81.6
            curr_laser = 81.6 + random.gauss(0, 0.12) * adc_factor
            gauge = ref_laser - curr_laser
        else:
            gauge = 1435.0 + random.gauss(0, 0.12) * adc_factor
            
        return {"gauge": round(gauge, 1),
                "cross": cross,
                "twist": round(abs(cross) * 0.65 + random.gauss(0, 0.015), 2),
                "dist":  round(self._dist, 1),
                "lat":   12.9716 + self._dist * 1e-6,
                "lon":   77.5946 + self._dist * 1e-6,
                "speed": round(random.uniform(2.0, 8.0), 1)}

    def reset(self):
        self._dist = 0.0
        self._lastc = 0
        self._raw_total = 0


# ═════════════════════════════════════════════════════════════════════════════
#  NETWORK THREAD
# ═════════════════════════════════════════════════════════════════════════════
class NetThread(QThread):
    status = pyqtSignal(int, bool)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg

    def run(self):
        while True:
            self.status.emit(self._lte(), self._ping())
            self.sleep(15)

    def _lte(self):
        preferred = self.cfg.get("lte_iface", "tap0")
        candidates = [preferred, "tap0", "ppp0", "usb2", "wwan0", "eth0", "eth1"]
        seen = set()
        for iface in candidates:
            if iface in seen:
                continue
            seen.add(iface)
            base = f"/sys/class/net/{iface}"
            if not os.path.exists(base):
                continue
            carrier = _sysfs(f"{base}/carrier", "0")
            state = _sysfs(f"{base}/operstate", "down")
            if carrier == "1" or iface in {"ppp0", "tap0"} or state in {"up", "unknown"}:
                try:
                    result = subprocess.run(
                        ["ip", "-4", "addr", "show", "dev", iface],
                        capture_output=True, text=True, timeout=2)
                    lines = [ln for ln in result.stdout.splitlines() if "inet " in ln and "169.254." not in ln]
                    if lines:
                        return 4 if iface == "tap0" else 3
                except Exception:
                    pass
                return 1
        return 0 if not HW_SIM else 3

    def _ping(self):
        if HW_SIM: return True
        server = self.cfg.get("server", "20.8.1.15")
        for target in [server, "20.8.0.1", "1.1.1.1"]:
            try:
                r = subprocess.run(
                    ["ping", "-c", "1", "-W", "2", target],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                if r.returncode == 0:
                    return True
            except Exception:
                pass
        return False


# ═════════════════════════════════════════════════════════════════════════════
#  CSV LOGGER
# ═════════════════════════════════════════════════════════════════════════════
STATION_NAME = "BLR"

_FIELDS = [
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


class CSVLogger:
    def __init__(self):
        self._f              = self._w = None
        self._rows           = []
        self.path            = ""
        self.count           = 0
        self._ref_type       = ""
        self._ref_value      = ""
        self._station        = "BLE"
        self._station_values = {}

    def set_reference(self, ref_type, ref_value):
        self._ref_type  = ref_type
        self._ref_value = ref_value

    def set_station(self, station_name):
        self._station = station_name.strip() if station_name else "UNKNOWN"

    def start(self, directory, hl_sec=30):
        os.makedirs(directory, exist_ok=True)
        safe_ts  = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"BLE_{safe_ts}.csv"
        self.path = os.path.join(directory, filename)
        self._f   = open(self.path, "w", newline="")
        self._w   = csv.DictWriter(self._f, fieldnames=_FIELDS)
        self._w.writeheader()
        self._rows  = []
        self._hl_s  = hl_sec
        self.count  = 0

    def write(self, d):
        if not self._w: return
        cross = d.get("cross", 0)
        station_no = self._station_values.get("Station No", self._station)
        row = {
            "Sample No": self.count + 1,
            "Date & Time": datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
            "Name": "",
            "Designation": "",
            "Station No": station_no,
            "Station Code": self._station_values.get("Station Code", station_no),
            "Chainage": d.get("dist", 0),
            "Loop/Line Siding": "",
            "Turn-out No": "",
            "Curve No": "",
            "Level Crossing No": "",
            "Hectometer Post": "",
            "Bridge (Start)": "",
            "Bridge (End)": "",
            "Level Crossing (LC) In": "",
            "Level Crossing (LC) Out": "",
            "Kilometer Post (KM)": "",
            "Points & Crossing (P&C)": "",
            "Curve-In": "",
            "Curve Out": "",
            "OHE Mast (OHEM) Location": "",
            "Switch Expansion Joint (SEJ)": "",
            "Latitude": d.get("lat", 0),
            "Longitude": d.get("lon", 0),
            "Distance": d.get("dist", 0),
            "Gauge": "",
            "Crosslevel": cross,
            "Twist": d.get("twist", 0),
        }
        self._rows.append((time.time(), row))
        self._w.writerow(row)
        self._f.flush()
        self.count += 1

    def mark(self, hl_sec=30):
        if not self._w or not self._rows: return
        self._hl_s = hl_sec
        self._f.seek(0); self._f.truncate()
        self._w.writeheader()
        for ts, row in self._rows:
            self._w.writerow(row)
        self._f.flush()

    def stop(self):
        saved_path = self.path
        if self._f:
            self._f.close()
        # The filename records the start time.  Set the completed file's
        # modification time at STOP so the CSV/USB page can show the exact
        # recording end time without changing the survey CSV data format.
        if saved_path and os.path.exists(saved_path):
            try:
                stopped_at = time.time()
                os.utime(saved_path, (stopped_at, stopped_at))
            except OSError:
                pass
        self._f = self._w = None
        return saved_path


# ═════════════════════════════════════════════════════════════════════════════
#  SPARKLINE
# ═════════════════════════════════════════════════════════════════════════════
class SparkLine(QWidget):
    def __init__(self, color=NEON, parent=None):
        super().__init__(parent)
        self._d   = []
        self._col = QColor(color)
        self.setFixedHeight(24)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def push(self, v):
        self._d.append(float(v))
        if len(self._d) > 200: self._d.pop(0)
        self.update()

    def paintEvent(self, _):
        if len(self._d) < 2: return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        mn, mx = min(self._d), max(self._d)
        rng = mx - mn or 1
        pts = [QPoint(int(W * i / (len(self._d) - 1)),
                      int(H * (mx - v) / rng))
               for i, v in enumerate(self._d)]
        p.setPen(QPen(self._col, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for i in range(len(pts) - 1):
            p.drawLine(pts[i], pts[i + 1])


# ═════════════════════════════════════════════════════════════════════════════
#  GRAPH CANVAS
# ═════════════════════════════════════════════════════════════════════════════
class GraphCanvas(QWidget):
    def __init__(self, color=NEON, parent=None):
        super().__init__(parent)
        self._d   = []
        self._col = QColor(color)
        self.title = ""
        self.unit  = ""
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def load(self, data, title="", unit=""):
        self._d = list(data); self.title = title; self.unit = unit
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        p.fillRect(0, 0, W, H, QColor(BG))
        if len(self._d) < 2:
            p.setPen(QColor("#333"))
            p.setFont(QFont("Courier New", 10))
            p.drawText(QRect(0, 0, W, H), Qt.AlignCenter,
                       "NO DATA — START SESSION FIRST")
            p.end(); return

        PAD = 52; gW = W - PAD - 10; gH = H - 46
        mn, mx = min(self._d), max(self._d); rng = mx - mn or 1

        for i in range(5):
            y   = 26 + gH * i // 4
            val = mx - rng * i / 4
            p.setPen(QPen(QColor("#181818"), 1, Qt.DashLine))
            p.drawLine(PAD, y, PAD + gW, y)
            p.setPen(QColor("#444"))
            p.setFont(QFont("Courier New", 7))
            p.drawText(QRect(0, y - 8, PAD - 4, 16),
                       Qt.AlignRight | Qt.AlignVCenter, f"{val:.2f}")

        n    = len(self._d)
        path = QPainterPath()
        path.moveTo(PAD, 26 + gH)
        for i, v in enumerate(self._d):
            x = PAD + int(gW * i / (n - 1))
            y = 26  + int(gH * (mx - v) / rng)
            path.lineTo(x, y)
        path.lineTo(PAD + gW, 26 + gH)
        path.closeSubpath()
        grad = QLinearGradient(0, 26, 0, 26 + gH)
        c1 = QColor(self._col); c1.setAlpha(55)
        c2 = QColor(self._col); c2.setAlpha(0)
        grad.setColorAt(0, c1); grad.setColorAt(1, c2)
        p.fillPath(path, QBrush(grad))

        p.setPen(QPen(self._col, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        prev = None
        for i, v in enumerate(self._d):
            pt = QPoint(PAD + int(gW * i / (n - 1)),
                        26 + int(gH * (mx - v) / rng))
            if prev: p.drawLine(prev, pt)
            prev = pt

        p.setPen(self._col)
        p.setFont(QFont("Courier New", 9, QFont.Bold))
        p.drawText(PAD, 18, f"{self.title.upper()}  [{self.unit}]  ·  {n} pts")
        p.setPen(QColor("#333"))
        p.setFont(QFont("Courier New", 7))
        p.drawText(PAD, H - 4, "SESSION START")
        p.drawText(PAD + gW - 30, H - 4, "NOW")
        p.end()


# ═════════════════════════════════════════════════════════════════════════════
#  TOP BAR
# ═════════════════════════════════════════════════════════════════════════════
class TopBar(QWidget):
    sig_close_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(70)
        self._close_btn = QPushButton("X", self)
        self._close_btn.setObjectName("TopClose")
        self._close_btn.setFixedSize(62, 46)
        self._close_btn.setToolTip("Close application")
        self._close_btn.clicked.connect(self.sig_close_requested)

        self._bar_count   = 4
        self._bar_color   = QColor("#4CAF50")
        self._net_txt     = "LTE"
        self._net_col     = QColor("#4CAF50")
        self._sensor_txt  = "SENSOR OK"
        self._sensor_ok   = True
        self._bat_txt     = "87%"
        self._time_txt    = "--:--:--"
        self._title_txt   = "LWTMT"

        tmr = QTimer(self)
        tmr.timeout.connect(self._tick)
        tmr.start(1000)
        self._tick()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()

        p.fillRect(0, 0, W, H, QColor("#1C2333"))
        p.fillRect(0, H - 2, W, 2, QColor("#2A3A50"))

        cy = H // 2
        f_mono_sm = QFont("Courier New", 10, QFont.Normal)
        f_mono_md = QFont("Courier New", 12, QFont.Bold)
        f_clock   = QFont("Courier New", 14, QFont.Bold)
        f_title   = QFont("Segoe UI", 16, QFont.Bold)
        f_battery = QFont("Courier New", 12, QFont.Bold)

        bw, bg_gap = 6, 4
        bar_hs = (7, 12, 17, 22)
        # The close control occupies the far-left edge.  Keep the connection
        # and sensor state grouped immediately to its right.
        x = 92
        for i, bh in enumerate(bar_hs):
            by = cy + 9 - bh
            col = self._bar_color if i < self._bar_count else QColor("#3A4555")
            path = QPainterPath()
            path.addRoundedRect(x, by, bw, bh, 1, 1)
            p.fillPath(path, col)
            x += bw + bg_gap

        x += 4
        p.setFont(f_mono_md)
        p.setPen(self._net_col)
        p.drawText(x, 0, 55, H, Qt.AlignVCenter | Qt.AlignLeft, self._net_txt)
        x += 55

        x += 6
        p.fillRect(x, cy - 8, 1, 16, QColor("#3A4555"))
        x += 10

        dot_col = QColor("#4CAF50") if self._sensor_ok else QColor("#EF5350")
        p.setBrush(dot_col)
        p.setPen(Qt.NoPen)
        p.drawEllipse(x, cy - 6, 12, 12)
        x += 20

        p.setFont(f_mono_sm)
        p.setPen(QColor("#A8C8A8") if self._sensor_ok else QColor("#FFAA44"))
        p.drawText(x, 0, 120, H, Qt.AlignVCenter | Qt.AlignLeft, self._sensor_txt)

        right_pad = 78
        clock_w = 138
        battery_w = 128
        gap = 12
        sep_gap = 10

        p.setFont(f_clock)
        p.setPen(QColor("#FFFFFF"))
        clock_x = W - right_pad - clock_w
        p.drawText(clock_x, 0, clock_w, H,
                   Qt.AlignVCenter | Qt.AlignRight, self._time_txt)

        sep_x = clock_x - sep_gap
        p.fillRect(sep_x, cy - 8, 1, 16, QColor("#3A4555"))

        # Parse battery percentage
        try:
            bat_pct = int("".join(c for c in str(self._bat_txt) if c.isdigit()))
        except Exception:
            bat_pct = 87
        bat_pct = max(0, min(100, bat_pct))

        # Battery icon colors: <= 20% Red, > 20% Green
        if bat_pct <= 20:
            bat_col = QColor("#FF3B30")      # Bright Red
            bat_text_col = QColor("#FF3B30")
        else:
            bat_col = QColor("#00E676")      # Bright Green
            bat_text_col = QColor("#00E676")

        # Horizontal Battery Icon Dimensions (Horizontal wide aspect ratio)
        bat_w = 30
        bat_h = 14
        term_w = 4
        term_h = 7
        inner_pad = 2

        p.setFont(f_battery)
        txt_str = f"{bat_pct}%"
        metrics = p.fontMetrics()
        txt_w = metrics.horizontalAdvance(txt_str) if hasattr(metrics, "horizontalAdvance") else metrics.width(txt_str)

        total_bat_w = (bat_w + term_w) + 7 + txt_w
        battery_block_x = sep_x - gap - total_bat_w

        icon_x = battery_block_x
        icon_y = cy - (bat_h // 2)

        # Draw Horizontal Battery Body
        p.setPen(QPen(bat_col, 1.8))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(icon_x, icon_y, bat_w, bat_h, 3, 3)

        # Draw Horizontal Battery Terminal (Right side)
        term_x = icon_x + bat_w
        term_y = cy - (term_h // 2)
        p.setPen(Qt.NoPen)
        p.setBrush(bat_col)
        p.drawRoundedRect(term_x, term_y, term_w, term_h, 1.5, 1.5)

        # Draw Inner Charge Fill
        inner_max_w = bat_w - (inner_pad * 2) - 1
        inner_fill_w = max(1, int(round(inner_max_w * (bat_pct / 100.0))))
        inner_fill_h = bat_h - (inner_pad * 2)
        p.fillRect(icon_x + inner_pad + 1, icon_y + inner_pad, inner_fill_w, inner_fill_h, bat_col)

        # Draw Battery Percentage Text
        text_x = term_x + term_w + 7
        p.setPen(bat_text_col)
        p.drawText(text_x, 0, txt_w + 10, H, Qt.AlignVCenter | Qt.AlignLeft, txt_str)

        p.setFont(f_title)
        p.setPen(QColor("#F3F6FB"))
        title_w = 280
        title_x = max(80, (W - title_w) // 2)
        p.drawText(QRect(title_x, 0, title_w, H),
                   Qt.AlignCenter, self._title_txt)

        p.end()

    def resizeEvent(self, event):
        self._close_btn.move(14, (self.height() - self._close_btn.height()) // 2)
        super().resizeEvent(event)

    def _tick(self):
        self._time_txt = datetime.now().strftime("%H:%M:%S")
        for b_path in ("/tmp/battery_pct", "/home/debian/trolley/battery_pct.txt"):
            try:
                if os.path.exists(b_path):
                    with open(b_path, "r") as bf:
                        val = bf.read().strip()
                        if val:
                            self._bat_txt = val if "%" in val else f"{val}%"
                    break
            except Exception:
                pass
        self.update()

    def set_battery(self, pct):
        try:
            val = int("".join(c for c in str(pct) if c.isdigit()))
            self._bat_txt = f"{val}%"
        except Exception:
            self._bat_txt = str(pct)
        self.update()

    def update_net(self, bars, cloud):
        self._bar_count = max(0, min(4, bars))
        if bars >= 3:
            self._bar_color = QColor("#4CAF50");  self._net_col = QColor("#4CAF50")
        elif bars >= 1:
            self._bar_color = QColor("#FF9800");  self._net_col = QColor("#FF9800")
        else:
            self._bar_color = QColor("#EF5350");  self._net_col = QColor("#EF5350")
        if bars == 4:
            self._net_txt = "VPN"
        elif bars > 0:
            self._net_txt = "LTE"
        else:
            self._net_txt = "NO LTE"
        self.setToolTip(
            ("VPN & LTE connected" if bars == 4 else ("LTE link connected" if bars > 0 else "LTE link unavailable"))
            + ("; cloud reachable" if cloud else "; cloud unavailable")
        )
        self.update()

    def push_error(self, msg):
        if msg:
            self._sensor_txt = (msg[:22] + "…") if len(msg) > 22 else msg
            self._sensor_ok  = False
        else:
            self._sensor_txt = "SENSOR OK"
            self._sensor_ok  = True
        self.update()


# ═════════════════════════════════════════════════════════════════════════════
#  CONTROL BAR
# ═════════════════════════════════════════════════════════════════════════════
class ControlBar(QWidget):
    sig_cal  = pyqtSignal()
    sig_mark = pyqtSignal(int)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setFixedHeight(44)
        self.setStyleSheet(
            "background:#060606; border-bottom:1px solid #141414;")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(8)

        self._csv_lbl = QLabel("📁  " + _shorten(cfg["csv_dir"]))
        self._csv_lbl.setStyleSheet(
            "background:#111; border:1px solid #333; border-radius:5px;"
            " color:#666; font-size:8pt; padding:3px 10px;"
            " font-family:'Courier New';")
        self._csv_lbl.setFixedHeight(32)

        cal = QPushButton("⚙  CALIBRATE")
        cal.setStyleSheet(
            f"QPushButton{{background:#1a1500; border:2px solid {AMBER};"
            f" border-radius:5px; color:{AMBER}; font-size:9pt;"
            f" font-weight:bold; padding:3px 14px; min-height:32px;}}"
            f"QPushButton:pressed{{background:#251d00;}}")
        cal.clicked.connect(self.sig_cal)

        hl_lbl = QLabel("MARK LAST")
        hl_lbl.setStyleSheet("color:#333; font-size:8pt;")

        self._stepper = Stepper(
            cfg.get("hl_sec", 30), step=5, dec=0,
            lo=5, hi=600, unit="s", title="HIGHLIGHT SECONDS",
        )
        self._stepper.setFixedWidth(200)

        mark = QPushButton("MARK")
        mark.setStyleSheet(
            f"QPushButton{{background:#001520; border:2px solid {CYAN};"
            f" border-radius:5px; color:{CYAN}; font-size:9pt;"
            f" font-weight:bold; padding:3px 12px; min-height:32px;}}"
            f"QPushButton:pressed{{background:#002030;}}")
        mark.clicked.connect(lambda: self.sig_mark.emit(int(self._stepper.value())))

        lay.addWidget(self._csv_lbl)
        lay.addWidget(cal)
        lay.addStretch()
        lay.addWidget(hl_lbl)
        lay.addWidget(self._stepper)
        lay.addWidget(mark)

    def set_csv_path(self, path):
        self._csv_lbl.setText("📁  " + _shorten(path))


# ═════════════════════════════════════════════════════════════════════════════
#  METRIC CARD
# ═════════════════════════════════════════════════════════════════════════════
_THRESH = {
    "gauge": (None, 6.0),
    "cross": (None, 200.0),
    "twist": (None, None),
    "dist":  (None, None),
}
_GAUGE_BAND = {
    "mode": "straight",
    "nominal": NOMINAL_GAUGE_MM,
    "min_tol": -6.0,
    "max_tol": 6.0,
    "min_abs": 1670.0,
    "max_abs": 1682.0,
}


def sync_runtime_thresholds(cfg=None):
    """Synchronize live card alarm thresholds with cfg['permissible_limits']."""
    active_cfg = cfg if cfg is not None else load_cfg()
    mode_key, nominal, min_tol, max_tol, min_abs, max_abs = gauge_limits_for_mode(active_cfg)
    limits = active_cfg.get("permissible_limits", {})
    cross_val = limits.get("crosslevel_mm")
    cross_limit = 200.0 if cross_val is None else float(cross_val)
    _GAUGE_BAND.update({
        "mode": mode_key,
        "nominal": nominal,
        "min_tol": min_tol,
        "max_tol": max_tol,
        "min_abs": min_abs,
        "max_abs": max_abs,
    })
    _THRESH.update({
        "gauge": (None, max_tol),
        "cross": (None, cross_limit),
        "twist": (None, None),
        "dist":  (None, None),
    })


def metric_alarm_state(key, fval):
    """Return ('alarm'|'warn'|'nominal') for live MetricCard values."""
    if key == "gauge":
        if abs(fval) > 1000.0:
            alarmed = (fval < _GAUGE_BAND["min_abs"] or fval > _GAUGE_BAND["max_abs"])
        else:
            alarmed = (fval < _GAUGE_BAND["min_tol"] or fval > _GAUGE_BAND["max_tol"])
        return "alarm" if alarmed else "nominal"
    if key in ("twist", "dist"):
        return "nominal"
    if key == "cross":
        _warn, alarm = _THRESH.get("cross", (None, 200.0))
        if alarm is not None and abs(fval) > float(alarm):
            return "alarm"
        return "nominal"
    warn, alarm = _THRESH.get(key, (None, None))
    dev = abs(fval)
    if alarm is not None and dev >= float(alarm):
        return "alarm"
    if warn is not None and dev >= float(warn):
        return "warn"
    return "nominal"


class MetricCard(QFrame):
    def __init__(self, key, title, unit, color, parent=None):
        super().__init__(parent)
        self.key   = key
        self.color = color
        self.setObjectName("Card")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        from PyQt5.QtWidgets import QGraphicsDropShadowEffect
        fx = QGraphicsDropShadowEffect(self)
        fx.setBlurRadius(16)
        fx.setOffset(0, 4)
        fx.setColor(QColor(0, 0, 0, 20))
        self.setGraphicsEffect(fx)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 6)
        lay.setSpacing(0)

        hdr = QWidget()
        hdr_l = QHBoxLayout(hdr)
        hdr_l.setContentsMargins(18, 10, 14, 8)
        hdr_l.setSpacing(8)

        self._title = QLabel(title.upper())
        self._title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._title.setStyleSheet(
            "background: transparent; border: none;"
            " color: #4A5568;"
            " font-family: 'Inter','DM Sans','Liberation Sans',sans-serif;"
            " font-size: 11pt; font-weight: 600; letter-spacing: 1.2px;")

        self._badge = QLabel("NOMINAL")
        self._badge.setAlignment(Qt.AlignCenter)

        hdr_l.addWidget(self._title, 1)
        hdr_l.addWidget(self._badge)

        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet("background:#DDE3EA; border:none;")

        val_row_w = QWidget()
        val_row_w.setStyleSheet("background:transparent; border:none;")
        val_row_w.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        outer_l = QHBoxLayout(val_row_w)
        outer_l.setContentsMargins(0, 0, 0, 0)
        outer_l.setSpacing(0)
        outer_l.addStretch(1)

        pair_w = QWidget()
        pair_w.setStyleSheet("background:transparent; border:none;")
        pair_l = QHBoxLayout(pair_w)
        pair_l.setContentsMargins(0, 0, 0, 0)
        pair_l.setSpacing(6)

        self._val = QLabel("---")
        self._val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._val.setMinimumWidth(320)
        self._val.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        self._unit = QLabel(unit)
        self._unit.setObjectName("UnitLbl")
        self._unit.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._unit.setFixedWidth(68)
        self._unit.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._apply_unit_style()

        pair_l.addWidget(self._val)
        pair_l.addWidget(self._unit)

        outer_l.addWidget(pair_w)
        outer_l.addStretch(1)

        self._alert = QLabel("")
        self._alert.setAlignment(Qt.AlignCenter)
        self._alert.setFixedHeight(22)
        self._alert.setStyleSheet(
            "color: #C62828; background: transparent; border: none;"
            " font-family: 'Inter','DM Sans',sans-serif;"
            " font-size: 9pt; font-weight: 600; letter-spacing: 0.5px;")

        lay.addWidget(hdr)
        lay.addWidget(rule)
        lay.addWidget(val_row_w, 1)
        lay.addWidget(self._alert)

        self._apply_badge("NOMINAL", color)
        self._apply_val_style(color)
        self._apply_unit_style()
        self.setStyleSheet(
            f"QFrame#Card{{"
            f" background:#FFFFFF; border:1px solid #DDE3EA;"
            f" border-left:4px solid {color}; border-radius:10px;}}")

    def _apply_badge(self, text, color):
        _BG_MAP = {
            "#1B8A4C": ("#D4EDDA", "#1B8A4C"),
            "#1565C0": ("#D0E4F7", "#1565C0"),
            "#C06000": ("#FFE8C0", "#944800"),
            "#5E35B1": ("#E0D4F5", "#5E35B1"),
            "#C62828": ("#FDDEDE", "#C62828"),
            "#E65100": ("#FFE0CC", "#C04000"),
        }
        bg, fg = _BG_MAP.get(color, ("#E8E8E8", "#333333"))
        self._badge.setText(text)
        self._badge.setStyleSheet(
            f"color:{fg}; background:{bg};"
            f" border:1.5px solid {fg};"
            f" border-radius:4px;"
            f" padding:3px 10px;"
            f" font-family:'Courier New',monospace;"
            f" font-size:9pt; font-weight:bold; letter-spacing:1px;")

    def _apply_unit_style(self):
        self._unit.setStyleSheet(
            "QLabel#UnitLbl {"
            " color: #8A94A6;"
            " background: transparent;"
            " border: none;"
            " font-family: 'Courier New', monospace;"
            " font-size: 26pt;"
            " font-weight: 500;"
            " letter-spacing: 1px;"
            " padding-bottom: 14px;"
            "}")

    def _apply_val_style(self, color, font_size=84):
        self._val.setStyleSheet(
            f"color:{color}; background:transparent; border:none;"
            f" font-family:'Inter','DM Sans','Liberation Sans',sans-serif;"
            f" font-size:{font_size}pt; font-weight:700; letter-spacing:-3px;")

    def refresh(self, val):
        self._last_raw_val = val
        try:
            fval = float(val)
            txt = f"{fval:.2f}"
            state = metric_alarm_state(self.key, fval)
        except (ValueError, TypeError):
            txt = str(val)
            state = "nominal"
        self._val.setText(txt)
        if state == "alarm":
            vc  = RED
            bg  = (f"QFrame#Card{{background:#FFEBEE; border:1px solid #DDE3EA;"
                   f" border-left:4px solid {RED}; border-radius:10px;}}")
            self._apply_badge("ALARM", RED)
            alert_txt = "⚠  ALARM"
        elif state == "warn":
            vc  = WARN
            bg  = (f"QFrame#Card{{background:#FFF3E0; border:1px solid #DDE3EA;"
                   f" border-left:4px solid {WARN}; border-radius:10px;}}")
            self._apply_badge("MONITOR", WARN)
            alert_txt = "△  WARN"
        else:
            vc  = self.color
            bg  = (f"QFrame#Card{{background:#FFFFFF; border:1px solid #DDE3EA;"
                   f" border-left:4px solid {self.color}; border-radius:10px;}}")
            self._apply_badge("NOMINAL", self.color)
            alert_txt = ""
        font_size = 72 if len(txt) >= 7 else (78 if len(txt) >= 6 else 84)
        self._apply_val_style(vc, font_size=font_size)
        self._apply_unit_style()
        self._alert.setText(alert_txt)
        self.setStyleSheet(bg)


# ═════════════════════════════════════════════════════════════════════════════
#  GRAPH PAGE
# ═════════════════════════════════════════════════════════════════════════════
class GraphPage(QWidget):
    sig_back = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 8)
        lay.setSpacing(6)

        hdr = QHBoxLayout()
        back = _btn("← BACK", "BC", 46, 140)
        back.clicked.connect(self.sig_back)

        self._lbl = QLabel("—")
        self._lbl.setStyleSheet(
            f"color:{CYAN}; font-size:11pt; font-weight:bold; letter-spacing:2px;")
        hdr.addWidget(back)
        hdr.addSpacing(12)
        hdr.addWidget(self._lbl)
        hdr.addStretch()
        lay.addLayout(hdr)

        self._canvas = GraphCanvas()
        lay.addWidget(self._canvas, 1)

    def load(self, title, unit, data, color):
        self._lbl.setText(f"▸  {title.upper()} — SESSION HISTORY")
        self._canvas._col = QColor(color)
        self._canvas.load(data, title, unit)


# ═════════════════════════════════════════════════════════════════════════════
#  LIVE TERMINAL WIDGET
# ═════════════════════════════════════════════════════════════════════════════
class TerminalWidget(QWidget):
    finished = pyqtSignal(int, str)

    def __init__(self, height=200, parent=None):
        super().__init__(parent)
        self._full_out = ""
        self._procs    = []

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        hdr = QHBoxLayout()
        self._cmd_lbl = QLabel("$  —")
        self._cmd_lbl.setStyleSheet(
            "color:#516074; font-size:8pt; font-family:'Courier New';"
            " background:#F8FAFB; border:1px solid #DDE3EA;"
            " padding:4px 8px; border-radius:8px 8px 0 0;")
        self._stat = QLabel("IDLE")
        self._stat.setStyleSheet(
            "color:#8A94A6; font-size:8pt; font-weight:bold;"
            " font-family:'Courier New'; padding:3px 8px;")
        hdr.addWidget(self._cmd_lbl, 1)
        hdr.addWidget(self._stat)
        lay.addLayout(hdr)

        self._out = QTextEdit()
        self._out.setReadOnly(True)
        self._out.setFixedHeight(height)
        self._out.setStyleSheet(
            "QTextEdit { background:#FFFFFF; border:1px solid #DDE3EA;"
            " border-top:none; border-radius:0 0 8px 8px; color:#334155;"
            " font-size:8pt; font-family:'Courier New'; }")
        lay.addWidget(self._out)

    def run(self, cmd):
        self._full_out = ""
        self._out.clear()
        self._cmd_lbl.setText("$  " + cmd[:140])
        self._set_status("RUNNING", AMBER)

        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(
            lambda p=proc: self._read(p))
        proc.finished.connect(
            lambda code, status, p=proc: self._done(code, p))
        proc.start("sh", ["-c", cmd])
        self._procs.append(proc)
        return proc

    def append(self, text):
        self._out.append(text)
        self._scroll()

    def clear_output(self):
        self._out.clear()
        self._full_out = ""
        self._cmd_lbl.setText("$  —")
        self._set_status("IDLE", "#8A94A6")

    def _read(self, proc):
        raw = proc.readAllStandardOutput().data().decode(errors="replace")
        self._full_out += raw
        for line in raw.splitlines():
            if line.strip():
                self._out.append(line)
        self._scroll()

    def _done(self, code, proc):
        raw = proc.readAllStandardOutput().data().decode(errors="replace")
        if raw.strip():
            self._full_out += raw
            for line in raw.splitlines():
                if line.strip():
                    self._out.append(line)
        ok = (code == 0)
        self._set_status(f"EXIT {code}", NEON if ok else RED)
        self._out.append(f"\n{'─'*40}\n{'OK' if ok else 'FAIL'}  exit={code}")
        self._scroll()
        self.finished.emit(code, self._full_out)

    def _scroll(self):
        sb = self._out.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _set_status(self, txt, color):
        self._stat.setText(txt)
        self._stat.setStyleSheet(
            f"color:{color}; font-size:8pt; font-weight:bold;"
            f" font-family:'Courier New'; padding:3px 8px;")


# ═════════════════════════════════════════════════════════════════════════════
#  ENCODER CAL
# ═════════════════════════════════════════════════════════════════════════════
class EncoderCal(QWidget):
    saved = pyqtSignal(str, dict)

    def __init__(self, cfg, sensor=None):
        super().__init__()
        self.cfg = cfg
        self.sensor = sensor
        self._start_raw = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)
        lay.addWidget(_lbl("ENCODER CALIBRATION", CYAN, 11, True))
        self._info = _lbl("", "#1A2332", 10)
        self._run = _lbl("", "#4A5568", 10)
        self._msg = _lbl("", "#4A5568", 10)
        for widget in (self._info, self._run, self._msg):
            lay.addWidget(widget)

        self._b_start = _btn("1. START RUN", "BC", 48)
        self._b_end = _btn("2. END RUN  +  ENTER TRUE DISTANCE", "BG", 48)
        self._b_manual = _btn("ENTER FACTOR MANUALLY", "BX", 44)
        self._b_reset = _btn("RESET FACTOR TO 1.0000", "BX", 44)
        for button in (self._b_start, self._b_end, self._b_manual, self._b_reset):
            lay.addWidget(button)
        self._b_end.setEnabled(False)
        self._b_start.clicked.connect(self._start_run)
        self._b_end.clicked.connect(self._end_run)
        self._b_manual.clicked.connect(self._manual)
        self._b_reset.clicked.connect(self._reset)

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._tick)
        self._tmr.start(500)
        self._refresh()
        lay.addStretch()

    def _enc(self):
        return self.cfg["encoder"]

    def _save_factor(self, factor, calibrated=True):
        self.sensor.set_encoder_factor(factor, calibrated)
        self.saved.emit("encoder", self._enc())

    def _refresh(self):
        enc = self._enc()
        state = "CALIBRATED" if enc.get("calibrated") else "NOT CALIBRATED"
        self._info.setText(
            f"Factor {enc.get('factor', 1.0):.4f}   |   "
            f"Scale {enc['scale']:.6f} mm/count   |   {state}"
        )

    def _tick(self):
        if self._start_raw is None or self.sensor is None:
            self._run.setText("")
            return
        try:
            readout = self.sensor.encoder_calibration_readout(self._start_raw)
        except RuntimeError as exc:
            self._msg.setText(str(exc))
            return
        self._run.setText(
            f"Run: {readout['raw_counts']} counts  =  "
            f"{readout['distance_m']:.3f} m (as currently displayed)"
        )

    def _start_run(self):
        if self.sensor is None:
            self._msg.setText("Encoder sensor is unavailable.")
            return
        if not self.sensor.active:
            self._msg.setText("Start the sensor/session first; encoder counts are read while it is running.")
            return
        try:
            self._start_raw = self.sensor.begin_encoder_calibration()
        except RuntimeError as exc:
            self._msg.setText(str(exc))
            return
        self._b_end.setEnabled(True)
        self._msg.setText("Move the trolley over a measured distance, then press END RUN.")

    def _end_run(self):
        if self._start_raw is None or self.sensor is None:
            return
        dialog = NumpadDialog(
            "True distance", "0", decimals=3, min_val=0.0, max_val=1000.0,
            unit="m", parent=self.window()
        )
        if dialog.exec_() != QDialog.Accepted:
            return
        true_m = dialog.get_value()
        try:
            result = self.sensor.finish_encoder_calibration(self._start_raw, true_m)
            old_factor = self._enc().get("factor", 1.0)
        except ValueError as exc:
            self._msg.setText(str(exc))
            return
        except RuntimeError as exc:
            self._msg.setText(str(exc))
            return
        self._start_raw = None
        self._b_end.setEnabled(False)
        self.saved.emit("encoder", self._enc())
        self._msg.setText(
            f"Factor {old_factor:.4f} -> {result['factor']:.4f}   "
            f"({result['raw_counts']} counts = {result['true_m']:.3f} m)"
        )
        self._refresh()

    def _manual(self):
        current = self._enc().get("factor", 1.0)
        dialog = NumpadDialog(
            "Calibration factor", f"{current:.4f}", decimals=4,
            min_val=0.5, max_val=2.5, unit="x",
            parent=self.window()
        )
        if dialog.exec_() == QDialog.Accepted and dialog.get_value() is not None:
            factor = dialog.get_value()
            try:
                self._save_factor(factor)
            except (ValueError, RuntimeError) as exc:
                self._msg.setText(str(exc))
                return
            self._msg.setText(f"Factor set to {factor:.4f}")
            self._refresh()

    def _reset(self):
        try:
            self._save_factor(1.0, calibrated=False)
        except (ValueError, RuntimeError) as exc:
            self._msg.setText(str(exc))
            return
        self._start_raw = None
        self._b_end.setEnabled(False)
        self._msg.setText("Factor reset to 1.0000")
        self._refresh()


# ═════════════════════════════════════════════════════════════════════════════
#  TRACK MODE SELECTOR (DISPLAY + CALIBRATION)
# ═════════════════════════════════════════════════════════════════════════════
class TrackModeSelector(QWidget):
    """Segmented track geometry selector controlling Track Gauge red limits."""
    mode_changed = pyqtSignal(str)

    def __init__(self, initial_mode="straight", compact=False, parent=None):
        super().__init__(parent)
        self._compact = compact
        self._mode = normalize_track_mode(initial_mode)
        self._buttons = {}

        if compact:
            lay = QHBoxLayout(self)
            lay.setContentsMargins(8, 2, 8, 2)
            lay.setSpacing(6)
            lay.addWidget(_lbl("TRACK:", "#4A5568", 9, True))
            btn_h = 34
            font_pt = 9
        else:
            lay = QVBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(6)
            lay.addWidget(_lbl(
                "Track Geometry Mode (Nominal Gauge 1676 mm | Crosslevel ±200 mm):",
                "#4A5568", 10, True))
            row = QHBoxLayout()
            row.setSpacing(8)
            btn_h = 44
            font_pt = 10

        self._btn_font_pt = font_pt
        container = lay if compact else row
        for key in ("straight", "curve_ge_440", "curve_lt_440"):
            spec = TRACK_GAUGE_MODES[key]
            btn = QPushButton(spec["short"])
            btn.setCheckable(True)
            btn.setFixedHeight(btn_h)
            btn.clicked.connect(lambda _checked=False, k=key: self.set_mode(k, emit_signal=True))
            container.addWidget(btn, 1 if not compact else 0)
            self._buttons[key] = btn

        self._summary = QLabel("")
        self._summary.setStyleSheet(
            f"background:{CYAN_LT}; border:1px solid {CYAN}; border-radius:6px;"
            f" color:{CYAN}; font-family:'Roboto Mono','Courier New',monospace;"
            f" font-size:{'8.5' if compact else '9.5'}pt; font-weight:bold;"
            f" padding:{'3px 8px' if compact else '6px 10px'};"
        )
        if compact:
            lay.addStretch(1)
            lay.addWidget(self._summary)
        else:
            lay.addLayout(row)
            lay.addWidget(self._summary)

        self.set_mode(self._mode, emit_signal=False)

    def mode(self):
        return self._mode

    def set_mode(self, mode, emit_signal=False):
        norm = normalize_track_mode(mode)
        changed = (norm != self._mode)
        self._mode = norm
        pt = self._btn_font_pt
        for key, btn in self._buttons.items():
            active = (key == norm)
            btn.blockSignals(True)
            btn.setChecked(active)
            btn.blockSignals(False)
            if active:
                btn.setStyleSheet(
                    f"QPushButton{{background:#E3EEFA; border:2px solid {CYAN};"
                    f" border-radius:7px; color:{CYAN}; font-size:{pt}pt;"
                    f" font-weight:bold; padding:0px 10px;}}"
                )
            else:
                btn.setStyleSheet(
                    f"QPushButton{{background:#FFFFFF; border:1px solid #C8D0DA;"
                    f" border-radius:7px; color:#4A5568; font-size:{pt}pt;"
                    f" font-weight:600; padding:0px 10px;}}"
                    f"QPushButton:pressed{{background:#EEF3F8;}}"
                )
        spec = TRACK_GAUGE_MODES[norm]
        lo_mm = int(round(NOMINAL_GAUGE_MM + spec["min_tol"]))
        hi_mm = int(round(NOMINAL_GAUGE_MM + spec["max_tol"]))
        if self._compact:
            self._summary.setText(f"GAUGE {lo_mm}-{hi_mm} mm | CL ±200")
        else:
            self._summary.setText(
                f"Active Limit: Gauge {lo_mm}-{hi_mm} mm ({spec['min_tol']:+.0f}/{spec['max_tol']:+.0f} mm)"
                f"  |  Crosslevel: ±200 mm  |  Twist & Distance: No limit"
            )
        if emit_signal and changed:
            self.mode_changed.emit(norm)


# ═════════════════════════════════════════════════════════════════════════════
#  ADC / GAUGE CAL
# ═════════════════════════════════════════════════════════════════════════════
class ADCCal(QWidget):
    saved = pyqtSignal(str, dict)
    sig_track_mode = pyqtSignal(str)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(14)

        self._track_selector = TrackModeSelector(
            gauge_limits_for_mode(cfg)[0], compact=False
        )
        self._track_selector.mode_changed.connect(self._on_mode_changed)
        lay.addWidget(self._track_selector)

        fr = QHBoxLayout()
        fr.addWidget(_lbl("Calibration factor:", "#4A5568", 10, True))
        self._factor_s = Stepper(cfg["adc"].get("factor", 1.0), step=0.01, dec=3,
                                 lo=0.001, hi=100.0, unit="x",
                                 title="TRACK GAUGE CALIBRATION FACTOR")
        fr.addWidget(self._factor_s, 1)
        lay.addLayout(fr)

        sv = _btn("SAVE CHANGES", "BA", 48)
        sv.clicked.connect(self._do_save)
        lay.addWidget(sv)
        lay.addStretch()

    def set_track_mode(self, mode):
        if hasattr(self, "_track_selector"):
            self._track_selector.set_mode(mode, emit_signal=False)

    def _on_mode_changed(self, mode):
        norm = set_track_mode(self.cfg, mode)
        save_cfg(self.cfg)
        self.sig_track_mode.emit(norm)

    def _do_save(self):
        factor = self._factor_s.value()
        set_track_mode(self.cfg, self._track_selector.mode())
        self.cfg["adc"].update({"factor": factor, "calibrated": True})
        save_cfg(self.cfg)
        self.saved.emit("adc", self.cfg["adc"])


# ═════════════════════════════════════════════════════════════════════════════
#  INCLINOMETER CAL
# ═════════════════════════════════════════════════════════════════════════════
class InclinCal(QWidget):
    saved = pyqtSignal(str, dict)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(14)

        fr = QHBoxLayout()
        fr.addWidget(_lbl("Calibration factor:", "#4A5568", 10, True))
        self._factor_s = Stepper(cfg["incl"].get("factor", 1.0), step=0.01, dec=3,
                                 lo=0.001, hi=100.0, unit="x",
                                 title="INCLINATION CALIBRATION FACTOR")
        fr.addWidget(self._factor_s, 1)
        lay.addLayout(fr)

        sv = _btn("SAVE CHANGES", "BA", 48)
        sv.clicked.connect(self._do_save)
        lay.addWidget(sv)
        lay.addStretch()

    def _do_save(self):
        factor = self._factor_s.value()
        self.cfg["incl"].update({"factor": factor, "calibrated": True})
        save_cfg(self.cfg)
        self.saved.emit("incl", self.cfg["incl"])


# ═════════════════════════════════════════════════════════════════════════════
#  GNSS CAL
# ═════════════════════════════════════════════════════════════════════════════
class GNSSCal(QWidget):
    saved = pyqtSignal(str, dict)

    def __init__(self, cfg):
        super().__init__()
        self.cfg     = cfg
        self._action = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(8)

        lay.addWidget(_lbl("GNSS  u-blox NEO-M8P-2  (/dev/ttyS4) — FIX & CHAINAGE",
                           MAGI, 10, True))
        lay.addWidget(_lbl(
            "Start gpsd → check fix (≥4 sats for survey) → "
            "optionally enable RTK → set reference chainage → SAVE", "#555", 8))

        g = QGridLayout(); g.setSpacing(8)
        for i, (lbl, fn, nm) in enumerate([
            ("▶ START gpsd",  self._start_gpsd,  "BM"),
            ("■ STOP gpsd",   self._stop_gpsd,   "BM"),
            ("⊛ CHECK FIX",  self._check_fix,   "BM"),
            ("⚡ RTK MODE",   self._rtk,         "BM"),
        ]):
            b = _btn(lbl, nm, 50)
            b.clicked.connect(fn)
            g.addWidget(b, i // 2, i % 2)
        lay.addLayout(g)

        self._term = TerminalWidget(height=170)
        self._term.finished.connect(self._on_done)
        lay.addWidget(self._term)

        rc = QHBoxLayout()
        rc.addWidget(_lbl("Reference chainage:", "#888"))
        self._ch_s = Stepper(cfg["gnss"]["ref_ch"], step=100, dec=1,
                             lo=0, hi=9_999_999, unit="m", title="REF CHAINAGE")
        rc.addWidget(self._ch_s, 1)
        lay.addLayout(rc)

        sv = _btn("SAVE CONFIGURATION ✓", "BA", 48)
        sv.clicked.connect(self._do_save)
        lay.addWidget(sv)

        ok = cfg["gnss"].get("calibrated", False)
        self._info = _lbl(
            ("✓ CONFIGURED" if ok else "✗ NOT CONFIGURED")
            + f"  |  ref={cfg['gnss']['ref_ch']:.1f} m",
            NEON if ok else RED)
        lay.addWidget(self._info)
        lay.addStretch()

    def _start_gpsd(self):
        self._action = "start"
        cmd = (
            "echo '# Starting gpsd service on Ubuntu...' && "
            "sudo systemctl start gpsd 2>&1 && "
            "sleep 1 && echo '# Service status:' && "
            "systemctl is-active gpsd && "
            "echo '# Socket status:' && "
            "systemctl is-active gpsd.socket 2>/dev/null || true"
        ) if not HW_SIM else (
            "echo '# [SIM] sudo systemctl start gpsd' && "
            "sleep 0.5 && echo 'gpsd.service: active (running)'"
        )
        self._term.run(cmd)

    def _stop_gpsd(self):
        self._action = "stop"
        cmd = (
            "echo '# Stopping gpsd...' && "
            "sudo systemctl stop gpsd 2>&1 && echo 'gpsd stopped.'"
        ) if not HW_SIM else (
            "echo '# [SIM] sudo systemctl stop gpsd' && "
            "sleep 0.3 && echo 'gpsd stopped.'"
        )
        self._term.run(cmd)

    def _check_fix(self):
        self._action = "fix"
        cmd = (
            "echo '# Polling GNSS (10 s timeout)...' && "
            "timeout 10 gpspipe -r -n 25 2>&1 | grep -m1 'GGA' || "
            "echo 'No GGA sentence — is gpsd running and antenna connected?'"
        ) if not HW_SIM else (
            "echo '# [SIM] gpspipe -r -n 25 | grep GGA' && "
            "sleep 0.8 && "
            "echo '$GPGGA,123519,1259.04,N,07730.18,E,1,08,0.9,920.4,M,46.9,M,,*47'"
        )
        self._term.append("# Checking GNSS fix quality (wait up to 10 s)...")
        self._term.run(cmd)

    def _rtk(self):
        self._action = "rtk"
        cmd = (
            "echo '# Enabling RTK via ubxtool...' && "
            "ubxtool -p RTCM 2>&1 | head -30"
        ) if not HW_SIM else (
            "echo '# [SIM] ubxtool -p RTCM' && "
            "sleep 0.5 && echo 'RTK RTCM3 output enabled on NEO-M8P-2'"
        )
        self._term.run(cmd)

    def _on_done(self, code, out):
        if self._action == "fix":
            for line in out.splitlines():
                if "GGA" in line:
                    p = line.split(",")
                    try:
                        q    = int(p[6]) if len(p) > 6 else 0
                        sats = int(p[7]) if len(p) > 7 else 0
                        alt  = p[9]      if len(p) > 9 else "?"
                        qual = {0:"No fix", 1:"GPS fix", 2:"DGPS",
                                4:"RTK Fixed", 5:"RTK Float"}.get(q, str(q))
                        col  = NEON if q >= 1 else RED
                        self._term.append(
                            f"\n→ Quality: {qual}  Satellites: {sats}  Alt: {alt} m")
                        self._info.setStyleSheet(f"color:{col}; font-size:9pt;")
                    except Exception:
                        pass
                    return

    def _do_save(self):
        self.cfg["gnss"].update({"ref_ch": self._ch_s.value(), "calibrated": True})
        save_cfg(self.cfg)
        self._info.setText(f"✓ CONFIGURED  |  ref={self._ch_s.value():.1f} m")
        self._info.setStyleSheet(f"color:{NEON}; font-size:9pt;")
        self._term.append("✓ Saved to rail_config.json")
        self.saved.emit("gnss", self.cfg["gnss"])


# ═════════════════════════════════════════════════════════════════════════════
#  LTE STATUS
# ═════════════════════════════════════════════════════════════════════════════
class LTECal(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(8)

        lay.addWidget(_lbl("LTE MODEM  (cdc_ether) — NETWORK DIAGNOSTICS",
                           CYAN, 10, True))
        lay.addWidget(_lbl(
            "Modem appears as Ethernet via cdc_ether kernel driver.\n"
            "Select interface then run diagnostics to verify connectivity.", "#555", 8))

        ir = QHBoxLayout()
        ir.addWidget(_lbl("Interface:", "#888"))
        self._iface = PresetTiles(
            ["usb2", "wwan0", "ppp0", "eth1", "eth0"],
            selected=cfg.get("lte_iface", "usb2"), color=CYAN)
        ir.addWidget(self._iface, 1)
        lay.addLayout(ir)

        g = QGridLayout(); g.setSpacing(8)
        for i, (lbl, fn, nm) in enumerate([
            ("IP ADDRESSES",  self._ip,     "BC"),
            ("PING TEST",     self._ping,   "BC"),
            ("SHOW ROUTES",   self._routes, "BC"),
            ("MODEM STATUS",  self._nmcli,  "BC"),
        ]):
            b = _btn(lbl, nm, 50)
            b.clicked.connect(fn)
            g.addWidget(b, i // 2, i % 2)
        lay.addLayout(g)

        self._term = TerminalWidget(height=210)
        lay.addWidget(self._term)

        sv = _btn("SAVE INTERFACE SELECTION ✓", "BA", 48)
        sv.clicked.connect(self._save)
        lay.addWidget(sv)
        lay.addStretch()

    def _ip(self):
        i = self._iface.value()
        self._term.run(
            f"echo '# ip addr show {i}' && ip addr show {i} 2>&1 && "
            f"echo && echo '# ip link show {i}' && ip link show {i} 2>&1")

    def _ping(self):
        srv = self.cfg.get("server", "8.8.8.8")
        iface = self._iface.value()
        self._term.run(
            f"echo '# ping -I {iface} -c 4 -W 2 {srv}' && "
            f"ping -I {iface} -c 4 -W 2 {srv} 2>&1")

    def _routes(self):
        self._term.run("echo '# ip route show' && ip route show 2>&1")

    def _nmcli(self):
        iface = self._iface.value()
        cmd = (
            f"echo '# {iface} modem state' && "
            f"ip -br link show {iface} 2>&1 && "
            f"echo '# carrier (1=registered link)' && "
            f"cat /sys/class/net/{iface}/carrier 2>&1 && "
            f"echo '# IPv4 address' && ip -4 addr show dev {iface} 2>&1 && "
            f"echo '# route through modem' && ip route show dev {iface} 2>&1 && "
            "echo '# NetworkManager' && nmcli device status 2>&1"
        ) if not HW_SIM else (
            "echo '# [SIM] nmcli device status' && "
            "printf 'DEVICE  TYPE      STATE      CONNECTION\\n"
            "eth1    ethernet  connected  LTE-modem\\n"
            "eth0    ethernet  connected  local-net\\n'"
        )
        self._term.run(cmd)

    def _save(self):
        self.cfg["lte_iface"] = self._iface.value()
        save_cfg(self.cfg)
        self._term.append(f"✓ Interface saved: {self._iface.value()}")


# ═════════════════════════════════════════════════════════════════════════════
#  DISPLAY CAL
# ═════════════════════════════════════════════════════════════════════════════
class DisplayCal(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(8)

        lay.addWidget(_lbl("LCD DISPLAY  (HDMI / omapdrm) — XRANDR CONFIGURATION",
                           AMBER, 10, True))

        for label, options, color, attr in [
            ("Output:",     ["HDMI-0","HDMI-1","HDMI-A-1","DVI-0"],        AMBER, "_out"),
            ("Resolution:", ["1024x768","800x600","640x480","1280x720"],     AMBER, "_res"),
            ("Rotation:",   ["normal","left","right","inverted"],           AMBER, "_rot"),
        ]:
            row = QHBoxLayout()
            row.addWidget(_lbl(label, "#888"))
            w = PresetTiles(options, options[0], color)
            setattr(self, attr, w)
            row.addWidget(w, 1)
            lay.addLayout(row)

        g = QGridLayout(); g.setSpacing(8)
        for i, (lbl, fn, nm) in enumerate([
            ("APPLY MODE",   self._apply,  "BA"),
            ("AUTO DETECT",  self._auto,   "BA"),
            ("SET ROTATION", self._rotate, "BA"),
            ("LIST MODES",   self._modes,  "BA"),
        ]):
            b = _btn(lbl, nm, 50)
            b.clicked.connect(fn)
            g.addWidget(b, i // 2, i % 2)
        lay.addLayout(g)

        self._term = TerminalWidget(height=190)
        lay.addWidget(self._term)
        lay.addStretch()

    def _apply(self):
        cmd = (f"echo '# xrandr --output {self._out.value()} --mode {self._res.value()}' && "
               f"xrandr --output {self._out.value()} --mode {self._res.value()} 2>&1 && "
               f"echo 'Mode applied.' || echo 'xrandr error — check output name with LIST MODES'")
        self._term.run(cmd)

    def _auto(self):
        cmd = (f"echo '# xrandr --output {self._out.value()} --auto' && "
               f"xrandr --output {self._out.value()} --auto 2>&1 && echo 'Done.'")
        self._term.run(cmd)

    def _rotate(self):
        cmd = (f"echo '# xrandr --output {self._out.value()} --rotate {self._rot.value()}' && "
               f"xrandr --output {self._out.value()} --rotate {self._rot.value()} 2>&1 && "
               f"echo 'Rotation set.'")
        self._term.run(cmd)

    def _modes(self):
        self._term.run("echo '# xrandr --query' && xrandr 2>&1")


# ═════════════════════════════════════════════════════════════════════════════
#  CALIBRATION PAGE
# ═════════════════════════════════════════════════════════════════════════════
_SENSORS = [
    ("adc", "Track Gauge", CYAN, ADCCal),
    ("incl", "Inclination", AMBER, InclinCal),
    ("encoder", "Track Distance", NEON, EncoderCal),
]


class CalibrationPasswordDialog(QDialog):
    """Password-only gateway for sensor-calibration controls."""
    def __init__(self, expected_password, parent=None):
        super().__init__(parent)
        self._expected_password = expected_password
        self.setWindowTitle("Calibration Access")
        self.setModal(True)
        self.setWindowState(self.windowState() | Qt.WindowFullScreen)
        self.setStyleSheet(
            "QDialog { background:#FFFFFF; }")

        screen = QApplication.primaryScreen()
        screen_width = screen.availableGeometry().width() if screen else 1024
        screen_height = screen.availableGeometry().height() if screen else 768
        key_width = max(48, min(68, (screen_width - 200) // 10))
        key_height = max(42, min(58, screen_height // 13))

        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.addStretch(1)
        content = QWidget(self)
        content.setMaximumWidth(900)
        root = QVBoxLayout(content)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)
        root.addStretch(1)
        title = QLabel("SENSOR CALIBRATION")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"color:{AMBER}; font-size:22pt; font-weight:bold; letter-spacing:2px;")
        root.addWidget(title)
        prompt = _lbl("Enter the calibration password to continue.", "#5B6575", 14)
        prompt.setAlignment(Qt.AlignCenter)
        root.addWidget(prompt)

        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.Password)
        self._password.setPlaceholderText("Password")
        self._password.setMinimumHeight(max(54, key_height))
        self._password.setStyleSheet(
            "QLineEdit { background:#F8FAFB; border:1.5px solid #C8D0DA; border-radius:8px;"
            " padding:0 12px; color:#1A2332; font-size:18pt; }")
        self._password.returnPressed.connect(self._unlock)
        root.addWidget(self._password)

        # Touchscreen keyboard: the QLineEdit remains masked at all times.
        self._uppercase = False
        self._letter_keys = []
        self._shift_btn = None
        keyboard = QVBoxLayout()
        keyboard.setSpacing(6)
        self._key_style = (
            "QPushButton { background:#F8FAFB; border:1px solid #D8E1EB;"
            " border-radius:7px; color:#334155; font-size:15pt; font-weight:700; }"
            "QPushButton:pressed { background:#FFF3E0; border-color:#C06000; }")
        key_style = self._key_style

        # Numbers and QWERTY rows
        for row_index, row_text in enumerate(("1234567890", "QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM")):
            row = QHBoxLayout()
            row.setSpacing(6)
            if row_index == 2:
                row.addSpacing(max(12, key_width // 4))
            elif row_index == 3:
                row.addSpacing(max(24, key_width // 2))
            for char in row_text:
                display_char = char if row_index == 0 else char.lower()
                key = QPushButton(display_char)
                key.setFixedSize(key_width, key_height)
                key.setStyleSheet(key_style)
                if row_index == 0:
                    key.clicked.connect(lambda _, value=char: self._append_key(value))
                else:
                    key.clicked.connect(lambda _, button=key: self._append_key(button.text()))
                    self._letter_keys.append(key)
                row.addWidget(key)
            row.addStretch()
            keyboard.addLayout(row)

        # Special characters row
        sp_row = QHBoxLayout()
        sp_row.setSpacing(6)
        for char in ("@", "#", "$", "%", "-", "_", ".", "/", "*", "!", "?"):
            key = QPushButton(char)
            key.setFixedSize(key_width, key_height)
            key.setStyleSheet(key_style)
            key.clicked.connect(lambda _, value=char: self._append_key(value))
            sp_row.addWidget(key)
        sp_row.addStretch()
        keyboard.addLayout(sp_row)

        # Action row
        actions = QHBoxLayout()
        actions.setSpacing(6)
        action_width = max(90, min(140, key_width * 2))
        for text, width, action in (
            ("SHIFT", action_width, self._toggle_case),
            ("SPACE", action_width * 2, lambda: self._append_key(" ")),
            ("CLEAR", action_width, self._password.clear),
            ("⌫", action_width, self._delete_key),
        ):
            key = QPushButton(text)
            key.setFixedSize(width, key_height)
            key.setStyleSheet(key_style)
            key.clicked.connect(action)
            if text == "SHIFT":
                self._shift_btn = key
            actions.addWidget(key)
        actions.addStretch()
        keyboard.addLayout(actions)
        root.addLayout(keyboard)

        self._error = QLabel("")
        self._error.setAlignment(Qt.AlignCenter)
        self._error.setStyleSheet(f"color:{RED}; font-size:9pt; font-weight:bold;")
        root.addWidget(self._error)

        buttons = QHBoxLayout()
        back = _btn("← BACK", "BC", key_height, 220)
        back.clicked.connect(self.reject)
        unlock = _btn("UNLOCK", "BA", key_height, 220)
        unlock.clicked.connect(self._unlock)
        buttons.addWidget(back)
        buttons.addStretch()
        buttons.addWidget(unlock)
        root.addLayout(buttons)
        root.addStretch(1)
        outer.addWidget(content)
        outer.addStretch(1)
        self._password.setFocus()

    def _append_key(self, value):
        self._password.setText(self._password.text() + value)
        self._password.setFocus()

    def _delete_key(self):
        self._password.setText(self._password.text()[:-1])
        self._password.setFocus()

    def _toggle_case(self):
        self._uppercase = not self._uppercase
        if self._shift_btn:
            self._shift_btn.setText("SHIFT ON" if self._uppercase else "SHIFT")
            if self._uppercase:
                self._shift_btn.setStyleSheet(
                    "QPushButton { background:#FFF3E0; border:2px solid #C06000;"
                    " border-radius:7px; color:#C06000; font-size:13pt; font-weight:700; }"
                )
            else:
                self._shift_btn.setStyleSheet(self._key_style)
        for key in self._letter_keys:
            key.setText(key.text().upper() if self._uppercase else key.text().lower())
        self._password.setFocus()

    def _unlock(self):
        if not self._expected_password:
            self._error.setText("Calibration password is not configured.")
        elif hmac.compare_digest(self._password.text(), self._expected_password):
            self.accept()
        else:
            self._password.clear()
            self._error.setText("Incorrect password. Try again or tap Back.")


class CalibrationPage(QWidget):
    sig_back = pyqtSignal()
    sig_track_mode = pyqtSignal(str)

    def __init__(self, cfg, sensor=None):
        super().__init__()
        self.cfg   = cfg
        self.sensor = sensor
        self._btns = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        hdr_w = QWidget()
        hdr_w.setFixedHeight(50)
        hdr_w.setStyleSheet("background:#FFFFFF; border-bottom:1px solid #DDE3EA;")
        hdr = QHBoxLayout(hdr_w)
        hdr.setContentsMargins(12, 0, 12, 0)
        title = QLabel("SENSOR CALIBRATION")
        title.setStyleSheet(
            f"color:{AMBER}; font-size:13pt; font-weight:bold; letter-spacing:3px;")
        hdr.addStretch()
        hdr.addWidget(title)
        hdr.addStretch()
        root.addWidget(hdr_w)

        body_w = QWidget()
        body = QVBoxLayout(body_w)
        body.setContentsMargins(18, 18, 18, 18)
        body.setSpacing(14)

        nav = QWidget()
        nav_l = QHBoxLayout(nav)
        nav_l.setContentsMargins(0, 0, 0, 0)
        nav_l.setSpacing(10)

        self._stack = QStackedWidget()

        for i, (key, label, color, Cls) in enumerate(_SENSORS):
            sub = cfg.get(key, {})
            ok  = sub.get("calibrated", False) if isinstance(sub, dict) else False

            btn = QPushButton(("✓  " if ok else "○  ") + label)
            btn.setFixedHeight(52)
            btn.setStyleSheet(
                "QPushButton{ background:#FFFFFF; border:1px solid #D7E0EA;"
                " border-radius:14px; color:#5B6575; font-size:10pt;"
                " font-weight:700; padding:8px 16px; text-align:center;}"
                "QPushButton:hover{ background:#F8FAFB; border-color:#BAC8D8; }"
                "QPushButton:pressed{ background:#EEF3F8; }")
            btn.clicked.connect(lambda _, idx=i: self._sel(idx))
            nav_l.addWidget(btn, 1)
            self._btns.append((btn, label, color))

            w = Cls(cfg, sensor) if key == "encoder" else Cls(cfg)
            if hasattr(w, "saved"):
                w.saved.connect(self._on_saved)
            if hasattr(w, "sig_track_mode"):
                w.sig_track_mode.connect(self.sig_track_mode.emit)

            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setStyleSheet("QScrollArea{ border:none; background:#FFFFFF; }")
            sc.setWidget(w)
            self._stack.addWidget(sc)

        body.addWidget(nav)

        rf = QFrame()
        rf.setObjectName("Panel")
        rl = QVBoxLayout(rf)
        rl.setContentsMargins(12, 12, 12, 12)
        rl.addWidget(self._stack)
        body.addWidget(rf, 1)

        root.addWidget(body_w, 1)

        bottom_w = QWidget()
        bottom_w.setStyleSheet("background:#FFFFFF; border-top:1px solid #DDE3EA;")
        bottom_l = QHBoxLayout(bottom_w)
        bottom_l.setContentsMargins(16, 10, 16, 10)
        back = _btn("← BACK", "BC", 42, 150)
        back.clicked.connect(self.sig_back.emit)
        bottom_l.addStretch()
        bottom_l.addWidget(back)
        bottom_l.addStretch()
        root.addWidget(bottom_w)

        self._sel(0)

    def _sel(self, idx):
        for i, (btn, _, color) in enumerate(self._btns):
            if i == idx:
                btn.setStyleSheet(
                    f"QPushButton{{ background:{color}14; border:2px solid {color};"
                    f" border-radius:14px; color:{color}; font-size:10pt;"
                    f" font-weight:700; padding:8px 16px; text-align:center;}}"
                    f"QPushButton:hover{{ background:{color}18; }}"
                    f"QPushButton:pressed{{ background:{color}24; }}")
            else:
                btn.setStyleSheet(
                    "QPushButton{ background:#FFFFFF; border:1px solid #D7E0EA;"
                    " border-radius:14px; color:#5B6575; font-size:10pt;"
                    " font-weight:700; padding:8px 16px; text-align:center;}"
                    "QPushButton:hover{ background:#F8FAFB; border-color:#BAC8D8; }"
                    "QPushButton:pressed{ background:#EEF3F8; }")
        self._stack.setCurrentIndex(idx)

    def set_track_mode(self, mode):
        for sel in self.findChildren(TrackModeSelector):
            sel.set_mode(mode, emit_signal=False)

    def _on_saved(self, key, values):
        for i, (k, label, color, *_) in enumerate(_SENSORS):
            if k == key:
                calibrated = values.get("calibrated", False) if isinstance(values, dict) else True
                self._btns[i][0].setText(("✓  " if calibrated else "○  ") + label)
                break


# ═════════════════════════════════════════════════════════════════════════════
#  DATA ENTRY PAGE
#  FIX: 3 equal panels always visible; popup keyboard; no horizontal scroll
# ═════════════════════════════════════════════════════════════════════════════

_PARAM_TABLES = [
    ("gauge", "GAUGE", "gauge", "mm", NEON, [0.25, 0.5, 0.75, 1.0]),
    ("twist", "TWIST", "twist", "mm/m", AMBER, [2.0, 3.0, 4.0]),
]


class ParamTableWidget(QFrame):
    """
    Frequency selector panel for a survey parameter.
    Compact vertical list of frequency buttons; always visible.
    """
    def __init__(self, label, color, unit, freq_options=None, parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setStyleSheet(
            "QFrame#Panel { background:#FFFFFF; border:1px solid #DDE3EA;"
            " border-radius:10px; }")
        self._color = color
        self._unit = unit
        self._label = label
        self._freq_options = freq_options or []
        self._freq_interval = None

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # Panel title
        title_lbl = QLabel(label)
        title_lbl.setAlignment(Qt.AlignCenter)
        title_lbl.setStyleSheet(
            f"color:{color}; font-size:12pt; font-weight:bold;"
            f" letter-spacing:1px; background:transparent; border:none;"
            f" padding:4px 0;")
        root.addWidget(title_lbl)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{color}44; border:none;")
        root.addWidget(sep)

        freq_lbl = QLabel("Select recording frequency")
        freq_lbl.setAlignment(Qt.AlignCenter)
        freq_lbl.setStyleSheet(
            "color:#4A5568; font-size:10.5pt; font-weight:600; background:transparent; border:none;")
        root.addWidget(freq_lbl)

        # Frequency buttons — vertical list, touch-friendly
        self._freq_buttons = {}
        for val in self._freq_options:
            lbl_str = f"{val:g} m"
            btn = QPushButton(lbl_str)
            btn.setFixedHeight(32)
            btn.setStyleSheet(self._btn_ss(False, color))
            btn.clicked.connect(lambda _, v=val: self._on_freq_selected(v))
            root.addWidget(btn)
            self._freq_buttons[val] = btn

        root.addStretch()

    def _btn_ss(self, active, color):
        if active:
            return (
                f"QPushButton {{ background:{color}18; border:2px solid {color};"
                f" border-radius:8px; color:{color}; font-size:10.5pt; font-weight:700; }}"
                f"QPushButton:pressed {{ background:{color}30; }}")
        return (
            "QPushButton { background:#F8FAFB; border:1.5px solid #C8D0DA;"
            " border-radius:8px; color:#334155; font-size:10.5pt; font-weight:700; }"
            "QPushButton:hover { background:#FFFFFF; border-color:#1565C0; }"
            "QPushButton:pressed { background:#EAF3FF; }")

    def _on_freq_selected(self, value):
        self._freq_interval = value
        for v, btn in self._freq_buttons.items():
            btn.setStyleSheet(self._btn_ss(v == value, self._color))

    def push_value(self, val):
        pass

    def clear_rows(self):
        pass

    def get_rows(self):
        return []


class StationParamsWidget(QFrame):
    """
    Compact station parameter panel — fits in 1/3 of 1024px screen.
    Uses popup keyboard when tapping any field.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setStyleSheet(
            "QFrame#Panel { background:#FFFFFF; border:1px solid #DDE3EA;"
            " border-radius:10px; }")

        self._field_names = [
            "Station No",
            "Station Code",
            "Chainage",
            "Loop/Line Siding",
            "Turn-out No",
            "Curve No",
            "Level Crossing No",
            "Hectometer Post",
        ]
        self._active_field = None
        self._fields = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        # Panel title
        title_lbl = QLabel("STATION PARAMETERS")
        title_lbl.setAlignment(Qt.AlignCenter)
        title_lbl.setStyleSheet(
            f"color:{HEADER_ACCENT}; font-size:12pt; font-weight:bold;"
            f" letter-spacing:1px; background:transparent; border:none; padding:4px 0;")
        root.addWidget(title_lbl)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{HEADER_ACCENT}44; border:none;")
        root.addWidget(sep)

        # Compact field list — each row is label + tappable value button
        _FIELD_SS_LABEL = (
            "color:#5B6575; font-size:10.5pt; font-weight:600;"
            " background:transparent; border:none;")
        _FIELD_BTN_EMPTY = (
            "QPushButton { background:#F8FAFB; border:1px solid #C8D0DA; border-radius:6px;"
            " padding:0 10px; color:#94A3B8; font-size:10pt; text-align:left; }"
            "QPushButton:hover { background:#FFFFFF; border-color:#1565C0; }"
            "QPushButton:pressed { background:#EAF3FF; }")
        _FIELD_BTN_FILLED = (
            "QPushButton { background:#F8FAFB; border:1px solid #C8D0DA; border-radius:6px;"
            " padding:0 10px; color:#1A2332; font-size:10pt; text-align:left; }"
            "QPushButton:hover { background:#FFFFFF; border-color:#1565C0; }"
            "QPushButton:pressed { background:#EAF3FF; }")

        for name in self._field_names:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)

            lbl = QLabel(name)
            lbl.setStyleSheet(_FIELD_SS_LABEL)
            lbl.setFixedWidth(132)
            lbl.setMinimumHeight(36)
            lbl.setWordWrap(True)

            field = TouchTextField(f"Tap to enter", field_title=name)
            field.setFixedHeight(32)
            self._fields[name] = field

            row.addWidget(lbl)
            row.addWidget(field, 1)
            root.addLayout(row)

        # Inspecting official section
        sep2 = QFrame()
        sep2.setFixedHeight(1)
        sep2.setStyleSheet("background:#DDE3EA; border:none;")
        root.addWidget(sep2)

        official_hdr = QLabel("Inspecting Official")
        official_hdr.setStyleSheet(
            f"color:{HEADER_ACCENT}; font-size:9pt; font-weight:bold;"
            f" background:transparent; border:none;")
        root.addWidget(official_hdr)

        for name, placeholder in [("Name", "Official name"), ("Designation", "Designation")]:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            lbl = QLabel(name)
            lbl.setStyleSheet(_FIELD_SS_LABEL)
            lbl.setFixedWidth(132)
            lbl.setMinimumHeight(36)
            lbl.setWordWrap(True)
            field = TouchTextField(placeholder, field_title=name)
            field.setFixedHeight(32)
            self._fields[name] = field
            row.addWidget(lbl)
            row.addWidget(field, 1)
            root.addLayout(row)

        root.addStretch()

    def get_values(self):
        return {k: f.value() for k, f in self._fields.items()}


FEATURE_FIELDS = [
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
]


class FeatureLocationWidget(QFrame):
    """Operator-entered locations for the track features in a survey."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        self.setStyleSheet(
            "QFrame#Panel { background:#FFFFFF; border:1px solid #DDE3EA;"
            " border-radius:10px; }")
        self._fields = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        title = QLabel("TRACK FEATURES")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            f"color:{HEADER_ACCENT}; font-size:12pt; font-weight:bold;"
            " letter-spacing:1px; background:transparent; border:none; padding:4px 0;")
        root.addWidget(title)

        label_style = (
            "color:#5B6575; font-size:10.5pt; font-weight:600;"
            " background:transparent; border:none;")
        for feature in FEATURE_FIELDS:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            label = QLabel(feature)
            label.setFixedWidth(132)
            label.setMinimumHeight(36)
            label.setWordWrap(True)
            label.setStyleSheet(label_style)
            field = TouchTextField("Tap to enter", field_title=feature)
            field.setFixedHeight(32)
            self._fields[feature] = field
            row.addWidget(label)
            row.addWidget(field, 1)
            root.addLayout(row)
        root.addStretch()

    def get_values(self):
        return {feature: field.value() for feature, field in self._fields.items()}


class DataEntryPage(QWidget):
    sig_back = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._tables = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header bar ────────────────────────────────────────────────────────
        hdr_w = QWidget()
        hdr_w.setFixedHeight(46)
        hdr_w.setStyleSheet("background:#FFFFFF; border-bottom:1px solid #DDE3EA;")
        hdr = QHBoxLayout(hdr_w)
        hdr.setContentsMargins(10, 0, 10, 0)
        title = QLabel("SURVEY DATA ENTRY")
        title.setStyleSheet(
            f"color:{CYAN}; font-size:13pt; font-weight:bold; letter-spacing:3px;")
        hdr.addStretch()
        hdr.addWidget(title)
        hdr.addStretch()
        root.addWidget(hdr_w)

        # ── 3-panel body: fixed equal-width columns, NO scroll ────────────────
        # Total available width = 1024px
        # Margins: 8px each side = 16px; spacing between 3 panels: 2×8 = 16px
        # Available for panels: 1024 - 16 - 16 = 992px → each panel ~330px

        panels_w = QWidget()
        panels_w.setStyleSheet("background:#ECEFF4;")
        panels_l = QHBoxLayout(panels_w)
        panels_l.setContentsMargins(8, 8, 8, 8)
        panels_l.setSpacing(8)

        # Panel 1: Station Parameters
        self._station_params = StationParamsWidget()
        panels_l.addWidget(self._station_params, 1)

        self._feature_panel = FeatureLocationWidget()
        panels_l.addWidget(self._feature_panel, 1)

        self._measurements = QFrame()
        self._measurements.setObjectName("Panel")
        self._measurements.setStyleSheet(
            "QFrame#Panel { background:#FFFFFF; border:1px solid #DDE3EA;"
            " border-radius:10px; }")
        measurements_layout = QVBoxLayout(self._measurements)
        measurements_layout.setContentsMargins(6, 6, 6, 6)
        measurements_layout.setSpacing(8)

        # Gauge and Twist share the third column.
        for key, label, _, unit, color, freq_options in _PARAM_TABLES:
            tw = ParamTableWidget(label, color, unit, freq_options)
            self._tables[key] = tw
            measurements_layout.addWidget(tw, 1)
        panels_l.addWidget(self._measurements, 1)

        root.addWidget(panels_w, 1)

        # ── Bottom bar ────────────────────────────────────────────────────────
        bottom_w = QWidget()
        bottom_w.setFixedHeight(52)
        bottom_w.setStyleSheet("background:#FFFFFF; border-top:1px solid #DDE3EA;")
        bottom_l = QHBoxLayout(bottom_w)
        bottom_l.setContentsMargins(16, 6, 16, 6)

        back_btn = _btn("← BACK", "BC", 40, 150)
        back_btn.clicked.connect(self.sig_back.emit)
        bottom_l.addStretch()
        bottom_l.addWidget(back_btn)
        bottom_l.addStretch()
        root.addWidget(bottom_w)

    def push_sensor_data(self, d):
        for key, _, sensor_key, _, _, _ in _PARAM_TABLES:
            if sensor_key in d and key in self._tables:
                self._tables[key].push_value(d[sensor_key])

    def _clear_all(self):
        for tw in self._tables.values():
            tw.clear_rows()

    def get_data(self):
        return {
            "station": self._station_params.get_values(),
            "feature_fields": self._feature_panel.get_values(),
            "parameters": {key: tw.get_rows() for key, tw in self._tables.items()},
        }


# ═════════════════════════════════════════════════════════════════════════════
#  CSV VIEWER PAGE
# ═════════════════════════════════════════════════════════════════════════════
class USBExportRangeDialog(QDialog):
    """Operator-facing local-survey picker for a date-bounded USB export."""
    def __init__(self, csv_dir, parent=None):
        super().__init__(parent)
        self._csv_dir = Path(csv_dir)
        self.setWindowTitle("Select surveys for USB export")
        self.setMinimumSize(860, 520)
        self.setStyleSheet("QDialog{background:#FFFFFF;} QTableWidget{font-size:9pt;}")
        root = QVBoxLayout(self)
        title = QLabel("USB EXPORT — SELECT SURVEYS")
        title.setStyleSheet(f"color:{CYAN}; font-size:15pt; font-weight:bold;")
        root.addWidget(title)
        root.addWidget(QLabel("Choose the date range, select the required CSV files, then export only that selection."))
        filters = QHBoxLayout()
        filters.addWidget(QLabel("From:"))
        self._from = QDateEdit(QDate.currentDate().addDays(-7))
        self._from.setCalendarPopup(True)
        filters.addWidget(self._from)
        filters.addWidget(QLabel("To:"))
        self._to = QDateEdit(QDate.currentDate())
        self._to.setCalendarPopup(True)
        filters.addWidget(self._to)
        apply_btn = _btn("APPLY RANGE", "BC", 38, 145)
        apply_btn.clicked.connect(self._populate)
        all_btn = _btn("SELECT ALL", "BX", 38, 130)
        all_btn.clicked.connect(lambda: self._table.selectAll())
        filters.addWidget(apply_btn)
        filters.addWidget(all_btn)
        filters.addStretch()
        root.addLayout(filters)
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Saved date & time", "CSV file", "Size"])
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setSelectionMode(QTableWidget.ExtendedSelection)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self._table, 1)
        self._status = QLabel("")
        self._status.setStyleSheet("color:#4A5568; font-size:9pt;")
        root.addWidget(self._status)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = _btn("CANCEL", "BR", 42, 120)
        cancel.clicked.connect(self.reject)
        export = _btn("EXPORT SELECTED", "BG", 42, 190)
        export.clicked.connect(self.accept)
        actions.addWidget(cancel)
        actions.addWidget(export)
        root.addLayout(actions)
        self._populate()

    def _populate(self):
        start, end = self._from.date(), self._to.date()
        if start > end:
            self._status.setText("The From date must not be later than the To date.")
            self._table.setRowCount(0)
            return
        files = []
        for path in self._csv_dir.glob("*.csv"):
            modified = datetime.fromtimestamp(path.stat().st_mtime)
            date = QDate(modified.year, modified.month, modified.day)
            if start <= date <= end:
                files.append((modified, path))
        files.sort(reverse=True)
        self._table.setRowCount(len(files))
        for row, (modified, path) in enumerate(files):
            date_item = QTableWidgetItem(modified.strftime("%d-%m-%Y %H:%M:%S"))
            name_item = QTableWidgetItem(path.name)
            name_item.setData(Qt.UserRole, str(path))
            size_item = QTableWidgetItem(f"{path.stat().st_size / 1024:.1f} KB")
            self._table.setItem(row, 0, date_item)
            self._table.setItem(row, 1, name_item)
            self._table.setItem(row, 2, size_item)
        self._status.setText(f"{len(files)} file(s) in selected date range. Select one or more rows.")

    def selected_files(self):
        return [self._table.item(index.row(), 1).data(Qt.UserRole)
                for index in self._table.selectionModel().selectedRows()]


class CSVViewerPage(QWidget):
    sig_back = pyqtSignal()
    sig_before_export = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._csv_dir = DEFAULT_CSV_DIR

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        hdr_w = QWidget()
        hdr_w.setObjectName("CSVHeader")
        hdr_w.setFixedHeight(80)
        hdr = QHBoxLayout(hdr_w)
        hdr.setContentsMargins(14, 12, 14, 12)
        hdr.setSpacing(12)

        self._view_mode = "files"
        self._current_loaded_path = ""
        self._all_selected = False
        self._back_btn = _btn("<  BACK TO DASHBOARD", "CSVBack", 52, 220)
        self._back_btn.clicked.connect(self._on_back_clicked)
        back = self._back_btn


        self._export_btn = _btn("EXPORT TO USB (EXCEL)", "CSVExport", 52, 280)
        export_btn = self._export_btn
        export_btn.clicked.connect(self._export_selected_to_usb)
        self._select_all_btn = _btn("SELECT ALL", "CSVRefresh", 52, 130)
        self._select_all_btn.clicked.connect(self._toggle_select_all)
        self._top_refresh = _btn("REFRESH FILE LIST", "CSVRefresh", 52, 175)
        top_refresh = self._top_refresh
        top_refresh.clicked.connect(self._refresh_list)

        # Matching side areas keep the primary USB action exactly centered.
        right_actions = QWidget()
        right_actions.setFixedWidth(320)
        right_layout = QHBoxLayout(right_actions)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addStretch(1)
        right_layout.setSpacing(8)
        right_layout.addWidget(self._select_all_btn)
        right_layout.addWidget(top_refresh)

        hdr.addWidget(back)
        hdr.addStretch(1)
        hdr.addWidget(export_btn)
        hdr.addStretch(1)
        hdr.addWidget(right_actions)
        root.addWidget(hdr_w)

        body = QHBoxLayout()
        body.setContentsMargins(8, 8, 8, 8)
        body.setSpacing(8)

        left = QFrame(); left.setObjectName("Panel"); left.setFixedWidth(220)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(6, 6, 6, 6)
        ll.setSpacing(5)

        lbl = QLabel("SAVED FILES")
        lbl.setStyleSheet(
            f"color:{MAGI}; font-size:8pt; font-weight:bold; letter-spacing:2px;")
        ll.addWidget(lbl)

        self._file_scroll = QScrollArea()
        self._file_scroll.setWidgetResizable(True)
        self._file_scroll.setStyleSheet("QScrollArea{border:none; background:#050505;}")
        self._file_list_widget = QWidget()
        self._file_list_layout = QVBoxLayout(self._file_list_widget)
        self._file_list_layout.setContentsMargins(2, 2, 2, 2)
        self._file_list_layout.setSpacing(4)
        self._file_list_layout.addStretch()
        self._file_scroll.setWidget(self._file_list_widget)
        ll.addWidget(self._file_scroll, 1)

        refresh_btn = _btn("↺  REFRESH", "BX", 36)
        refresh_btn.clicked.connect(self._refresh_list)
        ll.addWidget(refresh_btn)

        # The old preview/sidebar is hidden. Reading a large CSV just to open
        # this page made the touchscreen interface slow.
        left.setVisible(False)
        body.addWidget(left)

        right = QFrame(); right.setObjectName("Panel")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 6, 6, 6)
        rl.setSpacing(4)

        self._row_lbl = QLabel("")
        self._row_lbl.setStyleSheet("color:#444; font-size:8pt; font-family:'Courier New';")
        rl.addWidget(self._row_lbl)

        self._table = QTableWidget()
        self._table.setStyleSheet(
            f"QTableWidget {{ background:#FFFFFF; color:#1A2332;"
            f" font-size:8pt; font-family:'Courier New';"
            f" gridline-color:#DDE3EA; border:none; }}"
            f"QHeaderView::section {{ background:#EAF2FF; color:{CYAN};"
            f" font-size:8pt; font-weight:bold; border:1px solid #C8DDF5;"
            f" padding:4px; }}"
            f"QTableWidget::item:selected {{ background:#DCEBFF; color:#1A2332; }}"
            f"QScrollBar:vertical {{ background:#F0F4FA; width:14px; }}"
            f"QScrollBar::handle:vertical {{ background:#AFC5E5; border-radius:6px; }}"
            f"QScrollBar:horizontal {{ background:#F0F4FA; height:14px; }}"
            f"QScrollBar::handle:horizontal {{ background:#AFC5E5; border-radius:6px; }}"
        )
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionMode(QTableWidget.ExtendedSelection)
        self._table.verticalHeader().setDefaultSectionSize(48)
        self._table.verticalHeader().setStyleSheet(
            "QHeaderView::section { background:#F4F7FB; color:#607080;"
            " font-size:7pt; border:1px solid #DDE3EA; }")
        # Let an operator swipe anywhere in the list, as on a phone.  The
        # scrollbar is intentionally hidden; a short tap still operates a
        # selection checkbox while a drag scrolls the list.
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        QScroller.grabGesture(self._table.viewport(), QScroller.TouchGesture)
        QScroller.grabGesture(self._table.viewport(), QScroller.LeftMouseButtonGesture)
        self._table.cellClicked.connect(self._on_cell_clicked)
        rl.addWidget(self._table, 1)

        body.addWidget(right, 1)

        root_body = QWidget()
        root_body.setLayout(body)
        root.addWidget(root_body, 1)

    def set_csv_dir(self, path):
        self._csv_dir = path

    def load_latest(self):
        self._show_file_list()

    def _on_back_clicked(self):
        if getattr(self, "_view_mode", "files") == "data":
            self._show_file_list()
        else:
            self.sig_back.emit()

    def _show_file_list(self):
        self._view_mode = "files"
        self._current_loaded_path = ""
        self._all_selected = False
        self._back_btn.setText("<  BACK TO DASHBOARD")
        self._export_btn.setText("EXPORT TO USB (EXCEL)")
        if hasattr(self, "_select_all_btn") and True:
            self._select_all_btn.setVisible(True)
            self._select_all_btn.setText("SELECT ALL")
        pass
        pass
        if hasattr(self, "_top_refresh"):
            self._top_refresh.setVisible(True)
        self._refresh_list()
        return
        pass
        pass
        if hasattr(self, "_top_refresh"):
            self._top_refresh.setVisible(True)
        pass

    def _toggle_select_all(self):
        if getattr(self, "_view_mode", "files") != "files":
            return
        self._all_selected = not getattr(self, "_all_selected", False)
        target_state = Qt.Checked if self._all_selected else Qt.Unchecked
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item:
                item.setCheckState(target_state)
        if hasattr(self, "_select_all_btn") and True:
            self._select_all_btn.setVisible(True)
            self._select_all_btn.setText("DESELECT ALL" if self._all_selected else "SELECT ALL")

    def _on_cell_clicked(self, row, col):
        if getattr(self, "_view_mode", "files") == "files":
            if col == 0:
                item = self._table.item(row, 0)
                if item:
                    new_state = Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked
                    item.setCheckState(new_state)
            else:
                file_item = self._table.item(row, 1)
                if file_item:
                    file_path = file_item.data(Qt.UserRole)
                    if file_path and os.path.exists(file_path):
                        self._load_file(file_path)

    def _refresh_list(self):
        self._view_mode = "files"
        self._current_loaded_path = ""
        self._back_btn.setText("<  BACK TO DASHBOARD")
        self._export_btn.setText("EXPORT TO USB (EXCEL)")
        if hasattr(self, "_select_all_btn") and True:
            self._select_all_btn.setVisible(True)
        pass
        pass
        if hasattr(self, "_top_refresh"):
            self._top_refresh.setVisible(True)

        csv_path = Path(self._csv_dir)
        files = []
        if csv_path.is_dir():
            files = sorted(csv_path.glob("*.csv"),
                           key=lambda p: p.stat().st_mtime, reverse=True)

        self._table.clear()
        self._table.setColumnCount(3)
        self._table.setRowCount(len(files))
        self._table.setHorizontalHeaderLabels(["SELECT", "CSV FILE (TAP TO VIEW DATA)", "RECORDED TIME & SIZE"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self._table.setColumnWidth(0, 95)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)

        for row, f in enumerate(files):
            check_item = QTableWidgetItem("  Select")
            check_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            check_item.setCheckState(Qt.Unchecked)
            check_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, 0, check_item)

            name_item = QTableWidgetItem(f"📄  {f.name}")
            name_item.setData(Qt.UserRole, str(f))
            name_item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            font = name_item.font()
            font.setPointSize(9)
            font.setBold(True)
            name_item.setFont(font)
            self._table.setItem(row, 1, name_item)

            stat = f.stat()
            ended_at = datetime.fromtimestamp(stat.st_mtime).strftime("%d-%m-%Y  %H:%M:%S")
            size_kb = stat.st_size / 1024.0
            info_item = QTableWidgetItem(f"{ended_at}  ({size_kb:.1f} KB)")
            info_item.setTextAlignment(Qt.AlignCenter)
            self._table.setItem(row, 2, info_item)

        now_str = datetime.now().strftime("%H:%M:%S")
        if files:
            self._row_lbl.setText(
                f"{len(files)} survey file(s) found. Refreshed at {now_str}. "
                f"Tap file name to VIEW records, or tick checkboxes to COPY TO USB."
            )
        else:
            self._row_lbl.setText(
                f"No CSV files found in {self._csv_dir}. Refreshed at {now_str}."
            )

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open CSV File", self._csv_dir, "CSV Files (*.csv)")
        if path:
            self._load_file(path)

    def _browse_usb(self):
        """Open a CSV stored on an automatically mounted USB drive."""
        usb_root = Path("/media/rail-usb")
        if not usb_root.is_dir() or not any(usb_root.iterdir()):
            QMessageBox.information(
                self, "USB files",
                "No mounted USB drive found. Insert a FAT32/exFAT pen drive and wait a few seconds.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open CSV from USB", str(usb_root), "CSV Files (*.csv)")
        if path:
            self._load_file(path)

    def _export_selected_to_usb(self):
        """Copy only rows selected in the saved-CSV list to the USB drive."""
        selected = []
        if getattr(self, "_view_mode", "files") == "data" and getattr(self, "_current_loaded_path", ""):
            selected = [self._current_loaded_path]
        else:
            for row in range(self._table.rowCount()):
                file_item = self._table.item(row, 1)
                check_item = self._table.item(row, 0)
                if check_item and check_item.checkState() == Qt.Checked and file_item:
                    path = file_item.data(Qt.UserRole)
                    if path:
                        selected.append(path)
        if not selected:
            QMessageBox.information(self, "USB export", "Tick at least one CSV file checkbox first (or tap a file name to view and export it).")
            return

        self.sig_before_export.emit()
        helper = Path(__file__).resolve().parent / "tools" / "export_surveys_to_usb.py"
        command = [sys.executable, str(helper)]
        for path in selected:
            command.extend(["--file", path])
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            QMessageBox.warning(self, "USB export", "Export timed out. Do not remove the USB drive; try again.")
            return
        detail = (result.stdout or result.stderr or "USB export failed.").strip()
        if result.returncode == 0:
            QMessageBox.information(
                self, "USB export complete",
                detail + "\n\nNote: Each survey is exported as one password-protected .zip archive containing both .csv and .xlsx."
            )
        else:
            QMessageBox.warning(self, "USB export", detail)

    def _export_to_usb(self):
        """Copy (never move) all local CSVs to an inserted, mounted USB drive."""
        self.sig_before_export.emit()
        helper = Path(__file__).resolve().parent / "tools" / "export_surveys_to_usb.py"
        try:
            result = subprocess.run(
                [sys.executable, str(helper), "--source", self._csv_dir],
                capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            QMessageBox.warning(
                self, "USB export",
                "Export timed out. Do not remove the USB drive; try again.")
            return
        detail = (result.stdout or result.stderr or "USB export failed.").strip()
        if result.returncode == 0:
            QMessageBox.information(
                self, "USB export complete",
                detail + "\n\nNote: Each survey is exported as one password-protected .zip archive containing both .csv and .xlsx."
            )
        else:
            QMessageBox.warning(self, "USB export", detail)

    def _export_range_to_usb(self):
        dialog = USBExportRangeDialog(self._csv_dir, self)
        if dialog.exec_() != QDialog.Accepted:
            return
        selected = dialog.selected_files()
        if not selected:
            QMessageBox.information(self, "USB export", "Select at least one CSV file to export.")
            return
        self.sig_before_export.emit()
        helper = Path(__file__).resolve().parent / "tools" / "export_surveys_to_usb.py"
        command = [sys.executable, str(helper)]
        for path in selected:
            command.extend(["--file", path])
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            QMessageBox.warning(self, "USB export", "Export timed out. Do not remove the USB drive; try again.")
            return
        detail = (result.stdout or result.stderr or "USB export failed.").strip()
        if result.returncode == 0:
            QMessageBox.information(
                self, "USB export complete",
                detail + "\n\nNote: Each survey is exported as one password-protected .zip archive containing both .csv and .xlsx."
            )
        else:
            QMessageBox.warning(self, "USB export", detail)

    def _load_file(self, path):
        self._view_mode = "data"
        self._current_loaded_path = str(path)
        self._back_btn.setText("<  BACK TO FILE LIST")
        self._export_btn.setText("COPY THIS SURVEY TO USB")
        if hasattr(self, "_select_all_btn"):
            self._select_all_btn.setVisible(False)
        if hasattr(self, "_top_refresh"):
            self._top_refresh.setVisible(False)
        try:
            with open(path, newline="") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                headers = reader.fieldnames or []

            cfg = load_cfg()
            self._table.clear()
            self._table.setRowCount(len(rows))
            self._table.setColumnCount(len(headers))
            self._table.setHorizontalHeaderLabels(headers)

            for r, row in enumerate(rows):
                for c, h in enumerate(headers):
                    item = QTableWidgetItem(str(row.get(h, "")))
                    item.setTextAlignment(Qt.AlignCenter)
                    if exceeds_permissible(h, row.get(h, ""), cfg=cfg):
                        item.setForeground(QBrush(QColor(RED)))
                        item.setBackground(QBrush(QColor(RED_LT)))
                    self._table.setItem(r, c, item)

            name = Path(path).name
            self._row_lbl.setText(
                f"Viewing {name}  ·  {len(rows)} records  ·  (Tap '< BACK TO FILE LIST' to return)")
        except Exception as e:
            self._row_lbl.setText(f"Error loading file: {e}")


# ═════════════════════════════════════════════════════════════════════════════
#  DASHBOARD PAGE
#  FIX: bottom control bar pulled up — less spacing, compact layout
# ═════════════════════════════════════════════════════════════════════════════
_METRICS = [
    ("gauge", "Track Gauge",  "mm",   NEON),
    ("cross", "Cross Level",  "mm",   CYAN),
    ("twist", "Twist",        "mm/m", AMBER),
    ("dist",  "Distance",     "m",    MAGI),
]


class DashboardPage(QWidget):
    sig_toggle     = pyqtSignal(bool)
    sig_pause      = pyqtSignal(bool)
    sig_entry      = pyqtSignal()
    sig_csv        = pyqtSignal()
    sig_cal        = pyqtSignal()
    sig_track_mode = pyqtSignal(str)

    def __init__(self, initial_mode="straight"):
        super().__init__()
        self._running = False
        self._paused  = False

        self.setStyleSheet("background:#ECEFF4;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 4)   # tighter margins
        lay.setSpacing(4)                      # less spacing between grid and controls

        # ── Metric cards grid ─────────────────────────────────────────────────
        grid = QGridLayout()
        grid.setSpacing(8)
        self._cards = {}
        for i, (key, title, unit, color) in enumerate(_METRICS):
            card = MetricCard(key, title, unit, color)
            grid.addWidget(card, i // 2, i % 2)
            self._cards[key] = card
        lay.addLayout(grid, 1)

        # ── Thin separator ────────────────────────────────────────────────────
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#DDE3EA; border:none;")
        lay.addWidget(sep)

        # ── Bottom control bar — compact, pulled up ───────────────────────────
        bot_w = QWidget()
        bot_w.setFixedHeight(76)
        bot_w.setStyleSheet("background:#ECEFF4;")
        bot = QHBoxLayout(bot_w)
        bot.setContentsMargins(8, 8, 8, 8)
        bot.setSpacing(6)

        self._toggle = QPushButton("▶  START")
        self._toggle.setFixedHeight(58)
        self._toggle.setMinimumWidth(112)
        self._toggle.setStyleSheet(self._ss_start())
        self._toggle.clicked.connect(self._do_toggle)

        self._pause_btn = QPushButton("⏸  PAUSE")
        self._pause_btn.setFixedHeight(58)
        self._pause_btn.setMinimumWidth(108)
        self._pause_btn.setStyleSheet(self._ss_pause())
        self._pause_btn.setEnabled(False)
        self._pause_btn.clicked.connect(self._do_pause)

        vsep = QFrame()
        vsep.setFixedSize(1, 42)
        vsep.setStyleSheet("background:#DDE3EA; border:none;")

        self._entry_btn = QPushButton("DATA ENTRY")
        self._entry_btn.setFixedHeight(58)
        self._entry_btn.setMinimumWidth(130)
        self._entry_btn.setStyleSheet(self._ss_action(CYAN))
        self._entry_btn.clicked.connect(self.sig_entry)

        self._csv_btn = QPushButton("CSV / USB")
        self._csv_btn.setFixedHeight(58)
        self._csv_btn.setMinimumWidth(125)
        self._csv_btn.setStyleSheet(self._ss_action(MAGI))
        self._csv_btn.clicked.connect(self.sig_csv)

        self._cal_btn = QPushButton("CALIBRATE")
        self._cal_btn.setFixedHeight(58)
        self._cal_btn.setMinimumWidth(105)
        self._cal_btn.setStyleSheet(self._ss_action(AMBER))
        self._cal_btn.clicked.connect(self.sig_cal)

        self._station_no = QLabel("STN NO: --")
        self._station_no.setFixedSize(100, 40)
        self._station_no.setAlignment(Qt.AlignCenter)
        self._station_no.setStyleSheet(
            f"background:{CYAN_LT}; border:1px solid {CYAN}; border-radius:6px;"
            f" color:{CYAN}; font-family:'Roboto Mono','Courier New',monospace;"
            " font-size:9pt; font-weight:bold;")

        self._stat = QLabel("○  IDLE  0 pts")
        self._stat.setStyleSheet(
            "color:#8A94A6; font-family:'Roboto Mono','Courier New',monospace;"
            " font-size:10pt; font-weight:500; letter-spacing:0.5px;")

        bot.addStretch()
        bot.addWidget(self._toggle)
        bot.addWidget(self._pause_btn)
        bot.addWidget(vsep)
        bot.addWidget(self._entry_btn)
        bot.addWidget(self._csv_btn)
        bot.addWidget(self._cal_btn)
        bot.addSpacing(8)
        bot.addWidget(self._stat)
        bot.addWidget(self._station_no)
        bot.addStretch()

        lay.addWidget(bot_w)

    def _ss_action(self, color):
        bg = CYAN_LT if color == CYAN else (MAGI_LT if color == MAGI else AMBER_LT)
        return (
            f"QPushButton{{"
            f" background:{bg}; border:2px solid {color};"
            f" border-radius:8px; color:{color};"
            f" font-family:'Inter','DM Sans','Liberation Sans',sans-serif;"
            f" font-size:11pt; font-weight:bold; padding:0px 14px;}}"
            f"QPushButton:pressed{{"
            f" background:{color}; color:#FFFFFF; border:2px solid {color};}}"
            f"QPushButton:disabled{{"
            f" background:#EEF0F3; border-color:#C8D0DA; color:#B0BAC8;}}")

    def _ss_start(self):
        return (
            f"QPushButton{{"
            f" background:{NEON}; border:2px solid {NEON};"
            f" border-radius:8px; color:#FFFFFF;"
            f" font-family:'Inter','DM Sans','Liberation Sans',sans-serif;"
            f" font-size:11pt; font-weight:bold; padding:0px 14px;}}"
            f"QPushButton:pressed{{"
            f" background:#145E35; border-color:#145E35; color:#FFFFFF;}}")

    def _ss_stop(self):
        return (
            f"QPushButton{{"
            f" background:{RED}; border:2px solid {RED};"
            f" border-radius:8px; color:#FFFFFF;"
            f" font-family:'Inter','DM Sans','Liberation Sans',sans-serif;"
            f" font-size:11pt; font-weight:bold; padding:0px 14px;}}"
            f"QPushButton:pressed{{"
            f" background:#8B1A1A; border-color:#8B1A1A; color:#FFFFFF;}}")

    def _ss_pause(self):
        return (
            f"QPushButton{{"
            f" background:{AMBER_LT}; border:2px solid {AMBER};"
            f" border-radius:8px; color:{AMBER};"
            f" font-family:'Inter','DM Sans','Liberation Sans',sans-serif;"
            f" font-size:11pt; font-weight:bold; padding:0px 14px;}}"
            f"QPushButton:pressed{{"
            f" background:{AMBER}; color:#FFFFFF; border:2px solid {AMBER};}}"
            f"QPushButton:disabled{{"
            f" background:#EEF0F3; border-color:#C8D0DA; color:#B0BAC8;}}")

    def _ss_resume(self):
        return (
            f"QPushButton{{"
            f" background:{CYAN}; border:2px solid {CYAN};"
            f" border-radius:8px; color:#FFFFFF;"
            f" font-family:'Inter','DM Sans','Liberation Sans',sans-serif;"
            f" font-size:11pt; font-weight:bold; padding:0px 14px;}}"
            f"QPushButton:pressed{{"
            f" background:#0D4A8A; border-color:#0D4A8A; color:#FFFFFF;}}")

    def _do_toggle(self):
        self._running = not self._running
        if self._running:
            self._toggle.setText("■  STOP")
            self._toggle.setStyleSheet(self._ss_stop())
            self._entry_btn.setEnabled(False)
            self._cal_btn.setEnabled(False)
            self._pause_btn.setEnabled(True)
            self._paused = False
            self._pause_btn.setText("⏸  PAUSE")
            self._pause_btn.setStyleSheet(self._ss_pause())
        else:
            self._toggle.setText("▶  START")
            self._toggle.setStyleSheet(self._ss_start())
            self._entry_btn.setEnabled(True)
            self._cal_btn.setEnabled(True)
            self._pause_btn.setEnabled(False)
            self._paused = False
            self._pause_btn.setText("⏸  PAUSE")
            self._pause_btn.setStyleSheet(self._ss_pause())
        self.sig_toggle.emit(self._running)

    def _do_pause(self):
        self._paused = not self._paused
        if self._paused:
            self._pause_btn.setText("▶  RESUME")
            self._pause_btn.setStyleSheet(self._ss_resume())
        else:
            self._pause_btn.setText("⏸  PAUSE")
            self._pause_btn.setStyleSheet(self._ss_pause())
        self.sig_pause.emit(self._paused)

    def update_data(self, d):
        for key, card in self._cards.items():
            if key in d:
                card.refresh(d[key])

    def set_session(self, n, running, path=""):
        col  = NEON if running else "#8A94A6"
        icon = "●  REC" if running else "○  IDLE"
        self._stat.setText(f"{icon}  {n} pts")
        self._stat.setStyleSheet(
            f"color:{col}; font-family:'Roboto Mono','Courier New',monospace;"
            f" font-size:10pt; font-weight:500; letter-spacing:0.5px;")

    def set_station_no(self, station_no):
        self._station_no.setText(f"STN NO: {station_no or '--'}")

    def set_track_mode(self, mode):
        if hasattr(self, "_track_selector"):
            self._track_selector.set_mode(mode, emit_signal=False)

    def refresh_cards_limits(self):
        for card in self._cards.values():
            if hasattr(card, "_last_display"):
                card._last_display = None
            if hasattr(card, "_last_visual_state"):
                card._last_visual_state = None
            if hasattr(card, "_last_band_sig"):
                card._last_band_sig = None
            val = getattr(card, "_last_raw_val", card._val.text())
            card.refresh(val)


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN APPLICATION
# ═════════════════════════════════════════════════════════════════════════════
SCREEN_W, SCREEN_H = 1024, 768


class TrackApp(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg     = load_cfg()
        sync_runtime_thresholds(self.cfg)
        self.logger  = CSVLogger()
        self.history = {k: [] for k, *_ in _METRICS}

        self.setWindowTitle("Rail Inspection Unit v5.1")
        self.setStyleSheet(SS + "QWidget#TrackApp{background:#ECEFF4;}")

        self.sensor = SensorThread(self.cfg)
        self.sensor.data_ready.connect(self._on_data)
        self.sensor.fault.connect(self._on_fault)
        self.sensor.motion.connect(self._on_motion)
        self.sensor.start()

        self._SCREEN_TIMEOUT_MS = 5 * 60 * 1000
        self._last_motion_time  = time.time()
        self._screen_off        = False

        self._screen_timer = QTimer(self)
        self._screen_timer.setInterval(10_000)
        self._screen_timer.timeout.connect(self._check_screen_timeout)
        self._screen_timer.start()

        self._blank = QWidget(self)
        self._blank.setStyleSheet("background:#000000;")
        self._blank.hide()
        self._blank.mousePressEvent = self._wake_screen

        self.net = NetThread(self.cfg)
        self.net.status.connect(self._on_net)
        self.net.start()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.topbar = TopBar(self)
        self.topbar.sig_close_requested.connect(self.close)
        self.stack = QStackedWidget()

        initial_mode = gauge_limits_for_mode(self.cfg)[0]
        self.dash = DashboardPage(initial_mode=initial_mode)
        self.dash.update_data({"gauge": 0.0, "cross": 0.0, "twist": 0.0, "dist": 0.0})
        self.dash.sig_toggle.connect(self._on_toggle)
        self.dash.sig_pause.connect(self._on_pause)
        self.dash.sig_entry.connect(lambda: self._goto(2))
        self.dash.sig_csv.connect(self._show_csv_viewer)
        self.dash.sig_cal.connect(self._request_calibration_access)
        self.dash.sig_track_mode.connect(self._on_track_mode)
        self.stack.addWidget(self.dash)       # 0

        self.cal = CalibrationPage(self.cfg, self.sensor)
        self.cal.sig_back.connect(lambda: self._goto(0))
        self.cal.sig_track_mode.connect(self._on_track_mode)
        self.stack.addWidget(self.cal)        # 1

        self.entry = DataEntryPage()
        self.entry.sig_back.connect(lambda: self._goto(0))
        self.stack.addWidget(self.entry)      # 2

        self.graph_pg = GraphPage()
        self.graph_pg.sig_back.connect(lambda: self._goto(0))
        self.stack.addWidget(self.graph_pg)   # 3

        self.csv_viewer = CSVViewerPage()
        self.csv_viewer.sig_back.connect(lambda: self._goto(0))
        self.csv_viewer.sig_before_export.connect(self._flush_csv_for_export)
        self.csv_viewer.set_csv_dir(self.cfg["csv_dir"])
        self.stack.addWidget(self.csv_viewer) # 4

        root.addWidget(self.topbar)
        root.addWidget(self.stack, 1)

        self._apply_screen_geometry()
        self.showFullScreen()

    def _apply_screen_geometry(self):
        screen = self.screen() or QApplication.primaryScreen()
        if not screen:
            return
        geom = screen.availableGeometry()
        self.setGeometry(geom)
        self.setMinimumSize(geom.size())
        global W, H, SCREEN_W, SCREEN_H
        W, H = geom.width(), geom.height()
        SCREEN_W, SCREEN_H = W, H

    def _on_track_mode(self, mode):
        norm = set_track_mode(self.cfg, mode)
        save_cfg(self.cfg)
        sync_runtime_thresholds(self.cfg)
        if hasattr(self, "dash"):
            self.dash.set_track_mode(norm)
            self.dash.refresh_cards_limits()
        if hasattr(self, "cal"):
            self.cal.set_track_mode(norm)
        if hasattr(self, "_rendered_sensor_data"):
            self._rendered_sensor_data = None
        if (
            hasattr(self, "csv_viewer")
            and getattr(self.csv_viewer, "_view_mode", "") == "data"
            and getattr(self.csv_viewer, "_current_loaded_path", "")
        ):
            self.csv_viewer._load_file(self.csv_viewer._current_loaded_path)

    def _goto(self, idx):
        self.stack.setCurrentIndex(idx)

    def _request_calibration_access(self):
        """Open calibration only after a password-only authorization."""
        expected_password = str(
            os.environ.get("RAIL_CALIBRATION_PASSWORD")
            or self.cfg.get("calibration_password")
            or "Patil@123"
        )
        dialog = CalibrationPasswordDialog(expected_password, self)
        if dialog.exec_() == QDialog.Accepted:
            self._goto(1)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._screen_off:
            self._blank.setGeometry(self.rect())

    def _on_data(self, d):
        if not getattr(self.sensor, "active", False):
            return
        for key in self.history:
            if key in d:
                self.history[key].append(d[key])
                if len(self.history[key]) > 10_000:
                    self.history[key].pop(0)
        self.dash.update_data(d)
        if self.sensor.active:
            self.entry.push_sensor_data(d)

        dist = d.get("dist", 0)
        self.logger._ref_type = "KM"
        self.logger._ref_value = f"{round(dist, 2)} km"
        self.logger.write(d)
        self.dash.set_session(
            self.logger.count, self.sensor.active, self.logger.path or "")

    def _on_fault(self, msg):
        self.topbar.push_error(f"Sensor: {msg}")

    def _on_net(self, bars, cloud):
        self.topbar.update_net(bars, cloud)

    def _on_toggle(self, running):
        self.sensor.active = running
        if running:
            self.logger.set_reference("", "")
            self.logger.set_station("BLE")
            self.sensor.reset()
            self.history = {k: [] for k in self.history}
            self.logger.start(self.cfg["csv_dir"], self.cfg.get("hl_sec", 30))
        else:
            self.sensor.reset()
            self.dash.update_data({"gauge": 0.0, "cross": 0.0, "twist": 0.0, "dist": 0.0})
            self.logger.stop()

    def _on_pause(self, paused):
        self.sensor.active = not paused

    def _on_motion(self, moving):
        if moving:
            self._last_motion_time = time.time()
            if self._screen_off:
                self._wake_screen()

    def _check_screen_timeout(self):
        if self._screen_off:
            return
        idle_ms = (time.time() - self._last_motion_time) * 1000
        if idle_ms >= self._SCREEN_TIMEOUT_MS:
            self._blank_screen()

    def _blank_screen(self):
        self._screen_off = True
        self._blank.setGeometry(self.rect())
        self._blank.raise_()
        self._blank.show()

    def _wake_screen(self, _event=None):
        self._screen_off       = False
        self._last_motion_time = time.time()
        self._blank.hide()

    def _on_mark(self, sec):
        self.cfg["hl_sec"] = sec
        save_cfg(self.cfg)
        self.logger.mark(sec)

    def _show_csv_viewer(self):
        self.csv_viewer.set_csv_dir(self.cfg["csv_dir"])
        self.csv_viewer.load_latest()
        self._goto(4)

    def _flush_csv_for_export(self):
        """Include the most recent in-progress survey samples in USB export."""
        file_handle = getattr(self.logger, "_f", None)
        if file_handle:
            file_handle.flush()
            try:
                os.fsync(file_handle.fileno())
            except OSError:
                pass

    def _pick_csv(self):
        d = QFileDialog.getExistingDirectory(
            self, "Select CSV Output Directory",
            self.cfg["csv_dir"], QFileDialog.ShowDirsOnly)
        if d:
            self.cfg["csv_dir"] = d
            save_cfg(self.cfg)
            self.csv_viewer.set_csv_dir(d)

    def _show_graph(self, key):
        meta = {k: (t, u, c) for k, t, u, c in _METRICS}
        if key not in meta:
            return
        title, unit, color = meta[key]
        self.graph_pg.load(title, unit, list(self.history.get(key, [])), color)
        self._goto(3)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
                self.resize(W, H)
        super().keyPressEvent(e)


# ─────────────────────────────────────────────────────────────────────────────
def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setApplicationName("Rail Inspection Unit")
    app.restoreOverrideCursor()
    w = TrackApp()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
