#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Rail Track Geometry Inspection System
Target  : BeagleBone Black Industrial | Ubuntu | 1024×600 HDMI/Touch
Stack   : PyQt5 | Python 3.8+
Version : 5.1.0 — fixed layout: 3-panel DataEntry, popup keyboard, buttons up

Sensor map:
  Rotary Encoder   → eQEP → /sys/…/eqep/counter/count0/count
  Gauge Pot(TRS100)→ ADC  → /sys/bus/iio/devices/iio:device0/in_voltage0_raw
  Inclinometer     → SPI  → /dev/spidev1.0  (Murata SCL3300)
  GNSS             → UART → /dev/ttyS4      (u-blox NEO-M8P-2)
  LTE              → ETH  → eth1            (cdc_ether)
  Display          → HDMI → omapdrm / xrandr
"""

import sys, os, json, csv, time, random, subprocess, mmap, struct, queue, threading, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFrame, QStackedWidget, QScrollArea,
    QFileDialog, QTextEdit, QSizePolicy, QDialog, QTableWidget,
    QTableWidgetItem, QHeaderView, QComboBox, QLineEdit,
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QProcess, QPoint, QRect, QSize, QEvent
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

W, H = 1024, 600

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
"""


# ─────────────────────────────────────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────────────────────────────────────
CFG_PATH  = Path(__file__).parent / "rail_config.json"
_DEF = {
    "csv_dir":   str(Path.home() / "surveys"),
    "hl_sec":    30,
    "server":    "8.8.8.8",
    "lte_iface": "eth1",
    "encoder":   {"scale": 1.0,  "factor": 1.0, "calibrated": False},
    "adc":       {"zero": 2048,  "mpc": 0.0684, "factor": 1.0, "calibrated": False},
    "incl":      {"offset": 0.0, "factor": 1.0, "calibrated": False},
    "gnss":      {"ref_ch": 0.0, "calibrated": False},
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
            return d
        except Exception:
            pass
    return {k: (dict(v) if isinstance(v, dict) else v) for k, v in _DEF.items()}


def save_cfg(cfg):
    try:
        CFG_PATH.write_text(json.dumps(cfg, indent=2))
    except Exception as e:
        print(f"[CFG] {e}")


# ─────────────────────────────────────────────────────────────────────────────
#  HARDWARE
# ─────────────────────────────────────────────────────────────────────────────
EQEP_PATH = ("/sys/devices/platform/ocp/48304000.epwmss"
             "/48304180.eqep/counter/count0/count")
ADC_PATH  = "/sys/bus/iio/devices/iio:device0/in_voltage0_raw"
SPI_DEV   = "/dev/spidev1.0"
HW_SIM    = not os.path.exists(EQEP_PATH)
SHM_PATH = os.environ.get("RAIL_SENSOR_SHM", "/dev/shm/rail_sensor_shm")
CLOUD_URL = os.environ.get(
    "RAIL_CLOUD_URL", "https://thread-qm2o.onrender.com/api/survey")
CLOUD_RETRIES = 5
GAUGE_MM_CONST = 1676.0
SHM_RETRY_S = 2.0
SHM_MAGIC = 0x5241494C
SHM_VERSION = 1
SHM_STRUCT = struct.Struct("<IIIIqddddiBBBB")
DISPLAY_HZ = 10
DISPLAY_INTERVAL_MS = max(1, int(1000 / DISPLAY_HZ))
CSV_FLUSH_ROWS = 100


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
            f"color:{CYAN}; font-size:11pt; font-weight:bold; "
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
        self.setFixedSize(820, 340)
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
            f"color:{CYAN}; font-size:11pt; font-weight:bold; "
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
                b.setFixedSize(60, 44)
                b.setStyleSheet(_KEY_SS)
                b.clicked.connect(lambda _, v=ch: self._char(v))
                rl.addWidget(b)
            rl.addStretch()
            key_area.addLayout(rl)

        # Space + special row
        sp_row = QHBoxLayout()
        sp_row.setSpacing(4)
        sp_row.addSpacing(70)
        for ch, lbl, w in [(" ", "SPACE", 280), ("-", "-", 60), (".", ".", 60), ("/", "/", 60), ("@", "@", 60), ("_", "_", 60)]:
            b = QPushButton(lbl)
            b.setFixedSize(w, 44)
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
            b.setFixedHeight(44)
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
            f"color:{AMBER}; font-size:11pt; font-weight:bold; "
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
        done = QPushButton("DONE")
        done.setFixedHeight(44)
        done.setStyleSheet(
            "QPushButton { background:#E3EEFA; border:1px solid #1565C0; border-radius:10px;"
            " color:#1565C0; font-size:10pt; font-weight:700; }"
            "QPushButton:hover { background:#D8E9FD; }")
        done.clicked.connect(self._pad_commit)
        action_l.addWidget(clr)
        action_l.addWidget(done)
        pad_l.addLayout(action_l)
        lay.addWidget(self._pad)

        self._buf = ""
        self._refresh()

    def _refresh(self):
        suf = f"  {self._unit}" if self._unit else ""
        self._btn.setText(f"{self._val:.{self._dec}f}{suf}")

    def _open_pad(self):
        self._buf = f"{self._val:.{self._dec}f}"
        self._pad_disp.setText(self._btn.text())
        self._pad.setVisible(not self._pad.isVisible())

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
        self._stop  = False
        self._last_fault = ""
        self._last_chainage = None
        self._raw_chainage = 0.0
        self._chainage_offset = 0.0

    def run(self):
        shm = None
        while not self._stop:
            if shm is None:
                try:
                    fd = os.open(SHM_PATH, os.O_RDONLY)
                    shm = mmap.mmap(fd, SHM_STRUCT.size, access=mmap.ACCESS_READ)
                    os.close(fd)
                    self._emit_fault("")
                except Exception as e:
                    self._emit_fault("WAITING SHM SERVICE")
                    shm = None
                    time.sleep(SHM_RETRY_S)
                    continue

            try:
                frame = self._read_shared_frame(shm)
                d = self._from_backend_frame(frame)
                self.data_ready.emit(d)
            except Exception as e:
                self._emit_fault("SHM READ ERROR")
                try: shm.close()
                except Exception: pass
                shm = None
                time.sleep(1.0)
                continue
            self.msleep(50)

        if shm:
            try: shm.close()
            except Exception: pass

    def stop(self):
        self._stop = True

    def _emit_fault(self, msg):
        if msg != self._last_fault:
            self._last_fault = msg
            self.fault.emit(msg)

    def _read_shared_frame(self, shm):
        for _ in range(5):
            shm.seek(0)
            raw1 = shm.read(SHM_STRUCT.size)
            vals1 = SHM_STRUCT.unpack(raw1)
            seq1 = vals1[2]
            if seq1 & 1:
                self.msleep(2)
                continue
            shm.seek(0)
            raw2 = shm.read(SHM_STRUCT.size)
            vals2 = SHM_STRUCT.unpack(raw2)
            if vals2[2] != seq1 or (vals2[2] & 1):
                self.msleep(2)
                continue
            magic, version, _, _, ts, cl, tw, ch, ga, enc_count, s0, s1, service_ok, _ = vals2
            if magic != SHM_MAGIC or version != SHM_VERSION:
                raise RuntimeError("shared memory version mismatch")
            if not service_ok:
                raise RuntimeError("sensor service not publishing")
            return {
                "ts": ts,
                "cl": cl,
                "tw": tw,
                "ch": ch,
                "ga": ga,
                "enc_count": enc_count,
                "s0": s0,
                "s1": s1,
            }
        raise RuntimeError("shared memory update unstable")

    def _from_backend_frame(self, frame):
        enc_factor = self.cfg["encoder"].get("factor", 1.0)
        incl_factor = self.cfg["incl"].get("factor", 1.0)
        raw_chainage = float(frame.get("ch", 0.0)) * enc_factor
        self._raw_chainage = raw_chainage
        chainage = max(0.0, raw_chainage - self._chainage_offset)
        cross = (float(frame.get("cl", 0.0)) -
                 self.cfg["incl"].get("offset", 0.0)) * incl_factor
        twist = float(frame.get("tw", 0.0))
        gauge = float(frame.get("ga", GAUGE_MM_CONST))
        scl_ok = bool(frame.get("s0", 0))
        enc_ok = bool(frame.get("s1", 0))
        if self._last_chainage is None:
            moving = False
        else:
            moving = abs(chainage - self._last_chainage) >= 0.001
        self._last_chainage = chainage
        self._dist = chainage
        self.motion.emit(moving)
        if not scl_ok:
            self._emit_fault("SCL3300 NOT READY")
        elif not enc_ok:
            self._emit_fault("ENCODER PRU NOT RUNNING")
        else:
            self._emit_fault("")
        return {
            "gauge": round(gauge, 1),
            "cross": round(cross, 2),
            "twist": round(twist, 2),
            "dist": round(chainage, 3),
            "lat": 0.0,
            "lon": 0.0,
            "speed": 0.0,
            "scl_ok": scl_ok,
            "encoder_ok": enc_ok,
            "ts": frame.get("ts", 0),
        }

    def _sim(self):
        enc_factor = self.cfg["encoder"].get("factor", 1.0)
        adc_factor = self.cfg["adc"].get("factor", 1.0)
        incl_factor = self.cfg["incl"].get("factor", 1.0)
        self._dist += 0.2 * enc_factor
        self.motion.emit(True)
        cross = round(random.gauss(0, 0.25) * incl_factor, 2)
        return {"gauge": round(1435.0 + random.gauss(0, 0.12) * adc_factor, 1),
                "cross": cross,
                "twist": round(abs(cross) * 0.65 + random.gauss(0, 0.015), 2),
                "dist":  round(self._dist, 1),
                "lat":   12.9716 + self._dist * 1e-6,
                "lon":   77.5946 + self._dist * 1e-6,
                "speed": round(random.uniform(2.0, 8.0), 1)}

    def reset(self):
        self._dist = 0.0
        self._lastc = 0
        self._last_chainage = None
        self._chainage_offset = self._raw_chainage


# ═════════════════════════════════════════════════════════════════════════════
#  NETWORK THREAD
# ═════════════════════════════════════════════════════════════════════════════
class NetThread(QThread):
    status = pyqtSignal(int, bool)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self._stop = False

    def run(self):
        while not self._stop:
            self.status.emit(self._lte(), self._cloud_ready())
            self.sleep(15)

    def stop(self):
        self._stop = True

    def _lte(self):
        iface = self.cfg.get("lte_iface", "eth1")
        if _sysfs(f"/sys/class/net/{iface}/operstate", "down") == "up": return 3
        if _sysfs("/sys/class/net/eth0/operstate",     "down") == "up": return 2
        return 0 if not HW_SIM else 3

    def _cloud_ready(self):
        try:
            req = urllib.request.Request(
                CLOUD_URL.replace("/api/survey", "/"),
                method="GET",
                headers={"User-Agent": "RailInspection-BBB/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                return 200 <= resp.status < 500
        except Exception:
            return False


# ═════════════════════════════════════════════════════════════════════════════
#  CSV LOGGER
# ═════════════════════════════════════════════════════════════════════════════
STATION_NAME = "BLR"

_FIELDS = [
    "epoch_time",
    "reference_value",
    "reference_type",
    "latitude",
    "longitude",
    "cross_level",
    "chainage",
    "twist",
    "tilt",
    "tilt_cord_length",
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
        self._unflushed      = 0

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
        self._unflushed = 0

    def write(self, d):
        if not self._w: return
        cross = d.get("cross", 0)
        row = {
            "epoch_time":       int(time.time()),
            "reference_type":   d.get("reference_type", self._ref_type),
            "reference_value":  d.get("reference_value", self._ref_value),
            "latitude":         d.get("lat",   0),
            "longitude":        d.get("lon",   0),
            "cross_level":      cross,
            "chainage":         d.get("dist",  0),
            "twist":            d.get("twist", 0),
            "tilt":             cross,
            "tilt_cord_length": d.get("dist",  0),
        }
        self._rows.append((time.time(), row))
        self._w.writerow(row)
        self._unflushed += 1
        if self._unflushed >= CSV_FLUSH_ROWS:
            self._f.flush()
            os.fsync(self._f.fileno())
            self._unflushed = 0
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
        if not self._f:
            return ""
        saved_path = self.path
        self._f.flush()
        os.fsync(self._f.fileno())
        self._f.close()
        self._f = self._w = None
        self._unflushed = 0
        return saved_path if saved_path and os.path.exists(saved_path) else ""


class CSVWriterThread(QThread):
    session_started = pyqtSignal(str)
    session_stopped = pyqtSignal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cmd_q = queue.Queue()
        self._data_q = queue.Queue(maxsize=2000)
        self._stop = False
        self._logger = CSVLogger()
        self.active = False
        self.path = ""
        self.count = 0

    def start_session(self, directory, hl_sec=30, ref_type="", ref_value="", station="BLE"):
        self._cmd_q.put(("start", directory, hl_sec, ref_type, ref_value, station))

    def enqueue(self, d):
        if not self.active:
            return
        try:
            self._data_q.put_nowait(dict(d))
        except queue.Full:
            try:
                self._data_q.get_nowait()
            except queue.Empty:
                pass
            try:
                self._data_q.put_nowait(dict(d))
            except queue.Full:
                pass

    def stop_session(self, wait=False, timeout_ms=60000):
        waiter = None
        if wait:
            waiter = {"event": threading.Event(), "path": "", "count": self.count}
        self._cmd_q.put(("stop", waiter))
        if not waiter:
            return "", self.count
        waiter["event"].wait(timeout_ms / 1000.0)
        return waiter["path"], waiter["count"]

    def mark(self, hl_sec=30):
        self._cmd_q.put(("mark", hl_sec))

    def shutdown(self):
        self._cmd_q.put(("shutdown",))

    def run(self):
        while not self._stop:
            self._process_commands()
            if self.active:
                try:
                    d = self._data_q.get(timeout=0.1)
                except queue.Empty:
                    continue
                self._logger.write(d)
                self.count = self._logger.count
            else:
                self.msleep(50)
        if self.active:
            path = self._logger.stop()
            self.count = self._logger.count
            self.path = path
            self.active = False
            self.session_stopped.emit(path, self.count)

    def _process_commands(self):
        while True:
            try:
                cmd = self._cmd_q.get_nowait()
            except queue.Empty:
                return
            op = cmd[0]
            if op == "start":
                if self.active:
                    path = self._logger.stop()
                    self.count = self._logger.count
                    self.path = path
                    self.session_stopped.emit(path, self.count)
                _, directory, hl_sec, ref_type, ref_value, station = cmd
                self._drain_data()
                self._logger.set_reference(ref_type, ref_value)
                self._logger.set_station(station)
                self._logger.start(directory, hl_sec)
                self.active = True
                self.count = 0
                self.path = self._logger.path
                self.session_started.emit(self.path)
            elif op == "stop":
                waiter = cmd[1] if len(cmd) > 1 else None
                path = ""
                count = self.count
                if self.active:
                    path = self._logger.stop()
                    count = self._logger.count
                    self.count = count
                    self.path = path
                    self.active = False
                    self.session_stopped.emit(path, count)
                    self._drain_data()
                if waiter:
                    waiter["path"] = path
                    waiter["count"] = count
                    waiter["event"].set()
            elif op == "mark":
                if self.active:
                    self._logger.mark(cmd[1])
            elif op == "shutdown":
                self._stop = True
                return

    def _drain_data(self):
        while True:
            try:
                self._data_q.get_nowait()
            except queue.Empty:
                return


class CloudPushThread(QThread):
    done = pyqtSignal(bool, str)

    def __init__(self, csv_path, parent=None):
        super().__init__(parent)
        self.csv_path = csv_path

    def run(self):
        if not self.csv_path or not os.path.exists(self.csv_path):
            self.done.emit(False, "No CSV file to push")
            return
        rows = []
        try:
            with open(self.csv_path, newline="") as f:
                rows = list(csv.DictReader(f))
        except Exception as e:
            self.done.emit(False, f"CSV read error: {e}")
            return

        payload = {
            "filename": os.path.basename(self.csv_path),
            "data": rows,
        }
        body = json.dumps(payload).encode("utf-8")
        for attempt in range(CLOUD_RETRIES):
            try:
                req = urllib.request.Request(
                    CLOUD_URL,
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "Content-Length": str(len(body)),
                        "User-Agent": "RailInspection-BBB/1.0",
                    },
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    resp.read()
                self.done.emit(True, f"Pushed {len(rows)} rows")
                return
            except Exception as e:
                if attempt == CLOUD_RETRIES - 1:
                    self.done.emit(False, f"Cloud push failed: {e}")
                    return
                time.sleep(2 ** attempt)


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
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(70)
        from PyQt5.QtGui import QPixmap
        self._logo = QPixmap("C:/Users/Pilabs/Downloads/Patil_logo.png")

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
        if not self._logo.isNull():
            logo_size = 60
            x_logo = 10
            y_logo = (H - logo_size) // 2
            path = QPainterPath()
            path.addEllipse(x_logo, y_logo, logo_size, logo_size)
            p.setClipPath(path)
            p.drawPixmap(x_logo, y_logo, logo_size, logo_size, self._logo)
            p.setClipping(False)

        p.fillRect(0, H - 2, W, 2, QColor("#2A3A50"))

        cy = H // 2
        f_mono_sm = QFont("Courier New", 10, QFont.Normal)
        f_mono_md = QFont("Courier New", 12, QFont.Bold)
        f_clock   = QFont("Courier New", 14, QFont.Bold)
        f_title   = QFont("Segoe UI", 16, QFont.Bold)
        f_battery = QFont("Courier New", 12, QFont.Bold)

        bw, bg_gap = 6, 4
        bar_hs = (7, 12, 17, 22)
        x = 220
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
        p.drawText(x, 0, 180, H, Qt.AlignVCenter | Qt.AlignLeft, self._sensor_txt)

        p.setFont(f_title)
        p.setPen(QColor("#F3F6FB"))
        p.drawText(QRect(80, 0, W-80, H), Qt.AlignCenter, self._title_txt)

        p.setFont(f_clock)
        p.setPen(QColor("#FFFFFF"))
        clk_w = 140
        right_offset = 220
        p.drawText(W - clk_w - right_offset, 0, clk_w, H,
           Qt.AlignVCenter | Qt.AlignRight, self._time_txt)

        sep_x = W - clk_w - right_offset - 10
        p.fillRect(sep_x, cy - 8, 1, 16, QColor("#3A4555"))

        bat_w = 120
        p.setFont(f_battery)
        p.setPen(QColor("#A8C8A8"))
        bix = sep_x - bat_w - 8
        p.drawText(bix, 0, bat_w, H,
                   Qt.AlignVCenter | Qt.AlignRight, "▮ " + self._bat_txt)

        p.end()

    def _tick(self):
        self._time_txt = datetime.now().strftime("%H:%M:%S")
        self.update()

    def update_net(self, bars, cloud):
        self._bar_count = max(0, min(4, bars))
        if bars >= 3:
            self._bar_color = QColor("#4CAF50")
        elif bars >= 1:
            self._bar_color = QColor("#FF9800")
        else:
            self._bar_color = QColor("#EF5350")
        self._net_col = QColor("#4CAF50") if cloud else QColor("#EF5350")
        self._net_txt = "CLOUD" if cloud else "NO SYNC"
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
    "gauge": (0.5, 1.5),
    "cross": (0.8, 1.5),
    "twist": (0.40, 0.80),
    "dist":  (None, None),
}


class MetricCard(QFrame):
    def __init__(self, key, title, unit, color, parent=None):
        super().__init__(parent)
        self.key   = key
        self.color = color
        self.setObjectName("Card")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

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
            " font-size: 11pt; font-weight: 600; ")

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
            " font-size: 9pt; font-weight: 600; ")

        lay.addWidget(hdr)
        lay.addWidget(rule)
        lay.addWidget(val_row_w, 1)
        lay.addWidget(self._alert)

        self._last_display = None
        self._last_state = None
        self._apply_badge("NOMINAL", color)
        self._apply_val_style(color)
        self._apply_unit_style()
        self._apply_card_style(color, "#FFFFFF")

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
            f" font-size:9pt; font-weight:bold; ")

    def _apply_unit_style(self):
        self._unit.setStyleSheet(
            "QLabel#UnitLbl {"
            " color: #8A94A6;"
            " background: transparent;"
            " border: none;"
            " font-family: 'Courier New', monospace;"
            " font-size: 26pt;"
            " font-weight: 500;"
            " "
            " padding-bottom: 14px;"
            "}")

    def _apply_val_style(self, color):
        self._val.setStyleSheet(
            f"color:{color}; background:transparent; border:none;"
            f" font-family:'Inter','DM Sans','Liberation Sans',sans-serif;"
            f" font-size:84pt; font-weight:700; ")

    def _apply_card_style(self, accent, bg):
        self.setStyleSheet(
            f"QFrame#Card{{"
            f" background:{bg}; border:1px solid #DDE3EA;"
            f" border-left:4px solid {accent}; border-radius:10px;}}")

    def refresh(self, val):
        display = str(val)
        if display != self._last_display:
            self._val.setText(display)
            self._last_display = display
        warn, alarm = _THRESH.get(self.key, (None, None))
        dev = (abs(float(val) - 1435.0) if self.key == "gauge"
               else abs(float(val)))
        if alarm is not None and dev >= alarm:
            state = ("alarm", RED, "#FFEBEE", "ALARM", "⚠  ALARM")
        elif warn is not None and dev >= warn:
            state = ("warn", WARN, "#FFF3E0", "MONITOR", "△  WARN")
        else:
            state = ("nominal", self.color, "#FFFFFF", "NOMINAL", "")
        if state != self._last_state:
            _, accent, bg, badge, alert = state
            self._apply_badge(badge, accent)
            self._apply_val_style(accent)
            self._alert.setText(alert)
            self._apply_card_style(accent, bg)
            self._last_state = state


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
            f"color:{CYAN}; font-size:11pt; font-weight:bold; ")
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

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(14)

        fr = QHBoxLayout()
        fr.addWidget(_lbl("Calibration factor:", "#4A5568", 10, True))
        self._factor_s = Stepper(cfg["encoder"].get("factor", 1.0), step=0.01, dec=3,
                                 lo=0.001, hi=100.0, unit="x",
                                 title="ENCODER CALIBRATION FACTOR")
        fr.addWidget(self._factor_s, 1)
        lay.addLayout(fr)

        sv = _btn("SAVE CHANGES", "BA", 48)
        sv.clicked.connect(self._do_save)
        lay.addWidget(sv)
        lay.addStretch()

    def _do_save(self):
        factor = self._factor_s.value()
        self.cfg["encoder"].update({"factor": factor, "calibrated": True})
        save_cfg(self.cfg)
        self.saved.emit("encoder", self.cfg["encoder"])


# ═════════════════════════════════════════════════════════════════════════════
#  ADC / GAUGE CAL
# ═════════════════════════════════════════════════════════════════════════════
class ADCCal(QWidget):
    saved = pyqtSignal(str, dict)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(14)

        fr = QHBoxLayout()
        fr.addWidget(_lbl("Calibration factor:", "#4A5568", 10, True))
        self._factor_s = Stepper(cfg["adc"].get("factor", 1.0), step=0.01, dec=3,
                                 lo=0.001, hi=100.0, unit="x",
                                 title="POTENTIOMETER CALIBRATION FACTOR")
        fr.addWidget(self._factor_s, 1)
        lay.addLayout(fr)

        sv = _btn("SAVE CHANGES", "BA", 48)
        sv.clicked.connect(self._do_save)
        lay.addWidget(sv)
        lay.addStretch()

    def _do_save(self):
        factor = self._factor_s.value()
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
                                 title="INCLINOMETER CALIBRATION FACTOR")
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
            ["eth0", "eth1", "usb0", "wwan0"],
            selected=cfg.get("lte_iface", "eth1"), color=CYAN)
        ir.addWidget(self._iface, 1)
        lay.addLayout(ir)

        g = QGridLayout(); g.setSpacing(8)
        for i, (lbl, fn, nm) in enumerate([
            ("IP ADDRESSES",  self._ip,     "BC"),
            ("PING TEST",     self._ping,   "BC"),
            ("SHOW ROUTES",   self._routes, "BC"),
            ("nmcli STATUS",  self._nmcli,  "BC"),
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
        self._term.run(
            f"echo '# ping -c 4 -W 2 {srv}' && ping -c 4 -W 2 {srv} 2>&1")

    def _routes(self):
        self._term.run("echo '# ip route show' && ip route show 2>&1")

    def _nmcli(self):
        cmd = (
            "echo '# nmcli device status' && nmcli device status 2>&1"
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
            ("Resolution:", ["1024x600","1280x720","800x480","1920x1080"],  AMBER, "_res"),
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
    ("adc", "Potentiometer", CYAN, ADCCal),
    ("incl", "Inclinometer", AMBER, InclinCal),
    ("encoder", "Rotary Encoder", NEON, EncoderCal),
]


class CalibrationPage(QWidget):
    sig_back = pyqtSignal()

    def __init__(self, cfg):
        super().__init__()
        self.cfg   = cfg
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
            f"color:{AMBER}; font-size:13pt; font-weight:bold; ")
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

            w = Cls(cfg)
            if hasattr(w, "saved"):
                w.saved.connect(self._on_saved)

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

    def _on_saved(self, key, _):
        for i, (k, label, color, *_) in enumerate(_SENSORS):
            if k == key:
                self._btns[i][0].setText("✓  " + label)
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
            f"color:{color}; font-size:13pt; font-weight:bold;"
            f" background:transparent; border:none;"
            f" padding:4px 0;")
        root.addWidget(title_lbl)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{color}44; border:none;")
        root.addWidget(sep)

        freq_lbl = QLabel("Select recording frequency")
        freq_lbl.setAlignment(Qt.AlignCenter)
        freq_lbl.setStyleSheet(
            "color:#4A5568; font-size:9pt; font-weight:600; background:transparent; border:none;")
        root.addWidget(freq_lbl)

        # Frequency buttons — vertical list, touch-friendly
        self._freq_buttons = {}
        for val in self._freq_options:
            lbl_str = f"{val:g} m"
            btn = QPushButton(lbl_str)
            btn.setFixedHeight(52)
            btn.setStyleSheet(self._btn_ss(False, color))
            btn.clicked.connect(lambda _, v=val: self._on_freq_selected(v))
            root.addWidget(btn)
            self._freq_buttons[val] = btn

        root.addStretch()

    def _btn_ss(self, active, color):
        if active:
            return (
                f"QPushButton {{ background:{color}18; border:2px solid {color};"
                f" border-radius:10px; color:{color}; font-size:14pt; font-weight:700; }}"
                f"QPushButton:pressed {{ background:{color}30; }}")
        return (
            "QPushButton { background:#F8FAFB; border:1.5px solid #C8D0DA;"
            " border-radius:10px; color:#334155; font-size:14pt; font-weight:700; }"
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
            f" background:transparent; border:none; padding:4px 0;")
        root.addWidget(title_lbl)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{HEADER_ACCENT}44; border:none;")
        root.addWidget(sep)

        # Compact field list — each row is label + tappable value button
        _FIELD_SS_LABEL = (
            "color:#5B6575; font-size:8pt; font-weight:600;"
            " background:transparent; border:none;")
        _FIELD_BTN_EMPTY = (
            "QPushButton { background:#F8FAFB; border:1px solid #C8D0DA; border-radius:6px;"
            " padding:0 8px; color:#94A3B8; font-size:9pt; text-align:left; }"
            "QPushButton:hover { background:#FFFFFF; border-color:#1565C0; }"
            "QPushButton:pressed { background:#EAF3FF; }")
        _FIELD_BTN_FILLED = (
            "QPushButton { background:#F8FAFB; border:1px solid #C8D0DA; border-radius:6px;"
            " padding:0 8px; color:#1A2332; font-size:9pt; text-align:left; }"
            "QPushButton:hover { background:#FFFFFF; border-color:#1565C0; }"
            "QPushButton:pressed { background:#EAF3FF; }")

        for name in self._field_names:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)

            lbl = QLabel(name)
            lbl.setStyleSheet(_FIELD_SS_LABEL)
            lbl.setFixedWidth(100)

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
            row.setSpacing(6)
            lbl = QLabel(name)
            lbl.setStyleSheet(_FIELD_SS_LABEL)
            lbl.setFixedWidth(100)
            field = TouchTextField(placeholder, field_title=name)
            field.setFixedHeight(32)
            self._fields[name] = field
            row.addWidget(lbl)
            row.addWidget(field, 1)
            root.addLayout(row)

        root.addStretch()

    def get_values(self):
        return {k: f.value() for k, f in self._fields.items()}


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
            f"color:{CYAN}; font-size:13pt; font-weight:bold; ")
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

        # Panels 2 & 3: Gauge + Twist
        for key, label, _, unit, color, freq_options in _PARAM_TABLES:
            tw = ParamTableWidget(label, color, unit, freq_options)
            self._tables[key] = tw
            panels_l.addWidget(tw, 1)

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
        return {key: tw.get_rows() for key, tw in self._tables.items()}


# ═════════════════════════════════════════════════════════════════════════════
#  CSV VIEWER PAGE
# ═════════════════════════════════════════════════════════════════════════════
class CSVViewerPage(QWidget):
    sig_back = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._csv_dir = str(Path.home() / "surveys")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        hdr_w = QWidget()
        hdr_w.setFixedHeight(50)
        hdr_w.setStyleSheet("background:#070707; border-bottom:1px solid #1a1a1a;")
        hdr = QHBoxLayout(hdr_w)
        hdr.setContentsMargins(10, 0, 10, 0)
        hdr.setSpacing(8)

        back = _btn("← DASHBOARD", "BC", 40, 170)
        back.clicked.connect(self.sig_back)

        title = QLabel("📋   CSV FILE VIEWER")
        title.setStyleSheet(
            f"color:{MAGI}; font-size:13pt; font-weight:bold; ")

        self._file_lbl = QLabel("No file loaded")
        self._file_lbl.setStyleSheet("color:#444; font-size:8pt; font-family:'Courier New';")
        self._file_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        browse_btn = _btn("📂  BROWSE", "BM", 40, 130)
        browse_btn.clicked.connect(self._browse)

        hdr.addWidget(back)
        hdr.addSpacing(10)
        hdr.addWidget(title)
        hdr.addStretch()
        hdr.addWidget(self._file_lbl, 1)
        hdr.addSpacing(8)
        hdr.addWidget(browse_btn)
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
            f"color:{MAGI}; font-size:8pt; font-weight:bold; ")
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
            f"QTableWidget {{ background:#060606; color:#ccc;"
            f" font-size:8pt; font-family:'Courier New';"
            f" gridline-color:#1a1a1a; border:none; }}"
            f"QHeaderView::section {{ background:#0c0c0c; color:{MAGI};"
            f" font-size:8pt; font-weight:bold; border:1px solid #1a1a1a;"
            f" padding:4px; }}"
            f"QTableWidget::item:selected {{ background:{MAGI}33; color:#fff; }}"
            f"QScrollBar:vertical {{ background:#0a0a0a; width:8px; }}"
            f"QScrollBar::handle:vertical {{ background:#2a2a2a; border-radius:4px; }}"
            f"QScrollBar:horizontal {{ background:#0a0a0a; height:8px; }}"
            f"QScrollBar::handle:horizontal {{ background:#2a2a2a; border-radius:4px; }}"
        )
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setDefaultSectionSize(28)
        self._table.verticalHeader().setStyleSheet(
            "QHeaderView::section { background:#0c0c0c; color:#333;"
            " font-size:7pt; border:1px solid #1a1a1a; }")
        rl.addWidget(self._table, 1)

        body.addWidget(right, 1)

        root_body = QWidget()
        root_body.setLayout(body)
        root.addWidget(root_body, 1)

    def set_csv_dir(self, path):
        self._csv_dir = path
        self._refresh_list()

    def load_latest(self):
        self._refresh_list()
        files = sorted(Path(self._csv_dir).glob("*.csv"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if files:
            self._load_file(str(files[0]))

    def _refresh_list(self):
        while self._file_list_layout.count() > 1:
            item = self._file_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        files = sorted(Path(self._csv_dir).glob("*.csv"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files:
            btn = QPushButton(f.name)
            btn.setObjectName("EF")
            btn.setFixedHeight(44)
            btn.setStyleSheet(
                f"QPushButton{{background:#0a0a0a; border:1px solid #1e1e1e;"
                f" border-radius:5px; color:{MAGI}; font-size:7pt;"
                f" font-family:'Courier New'; text-align:left; padding-left:8px;}}"
                f"QPushButton:pressed{{background:#140020; border-color:{MAGI};}}")
            btn.clicked.connect(lambda _, p=str(f): self._load_file(p))
            self._file_list_layout.insertWidget(
                self._file_list_layout.count() - 1, btn)

        if not files:
            empty = QLabel("No CSV files found")
            empty.setStyleSheet("color:#333; font-size:8pt; padding:8px;")
            self._file_list_layout.insertWidget(0, empty)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open CSV File", self._csv_dir, "CSV Files (*.csv)")
        if path:
            self._load_file(path)

    def _load_file(self, path):
        try:
            with open(path, newline="") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                headers = reader.fieldnames or []

            self._table.clear()
            self._table.setRowCount(len(rows))
            self._table.setColumnCount(len(headers))
            self._table.setHorizontalHeaderLabels(headers)

            for r, row in enumerate(rows):
                for c, h in enumerate(headers):
                    item = QTableWidgetItem(str(row.get(h, "")))
                    item.setTextAlignment(Qt.AlignCenter)
                    self._table.setItem(r, c, item)

            name = Path(path).name
            self._file_lbl.setText(name)
            self._row_lbl.setText(
                f"{len(rows)} rows  ·  {len(headers)} columns  ·  {name}")
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
    sig_toggle = pyqtSignal(bool)
    sig_pause  = pyqtSignal(bool)
    sig_entry  = pyqtSignal()
    sig_cal    = pyqtSignal()

    def __init__(self):
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
        bot_w.setFixedHeight(62)           # fixed height keeps it compact
        bot_w.setStyleSheet("background:#ECEFF4;")
        bot = QHBoxLayout(bot_w)
        bot.setContentsMargins(4, 4, 4, 4)
        bot.setSpacing(10)

        self._toggle = QPushButton("▶  START")
        self._toggle.setFixedHeight(52)
        self._toggle.setMinimumWidth(120)
        self._toggle.setStyleSheet(self._ss_start())
        self._toggle.clicked.connect(self._do_toggle)

        self._pause_btn = QPushButton("⏸  PAUSE")
        self._pause_btn.setFixedHeight(52)
        self._pause_btn.setMinimumWidth(120)
        self._pause_btn.setStyleSheet(self._ss_pause())
        self._pause_btn.setEnabled(False)
        self._pause_btn.clicked.connect(self._do_pause)

        vsep = QFrame()
        vsep.setFixedSize(1, 36)
        vsep.setStyleSheet("background:#DDE3EA; border:none;")

        self._entry_btn = QPushButton("DATA ENTRY")
        self._entry_btn.setFixedHeight(52)
        self._entry_btn.setMinimumWidth(130)
        self._entry_btn.setStyleSheet(self._ss_action(CYAN))
        self._entry_btn.clicked.connect(self.sig_entry)

        self._cal_btn = QPushButton("CALIBRATE")
        self._cal_btn.setFixedHeight(52)
        self._cal_btn.setMinimumWidth(120)
        self._cal_btn.setStyleSheet(self._ss_action(AMBER))
        self._cal_btn.clicked.connect(self.sig_cal)

        self._stat = QLabel("○  IDLE  0 pts")
        self._stat.setStyleSheet(
            "color:#8A94A6; font-family:'Roboto Mono','Courier New',monospace;"
            " font-size:10pt; font-weight:500; ")

        bot.addStretch()
        bot.addWidget(self._toggle)
        bot.addWidget(self._pause_btn)
        bot.addWidget(vsep)
        bot.addWidget(self._entry_btn)
        bot.addWidget(self._cal_btn)
        bot.addSpacing(8)
        bot.addWidget(self._stat)
        bot.addStretch()

        lay.addWidget(bot_w)

    def _ss_action(self, color):
        bg = CYAN_LT if color == CYAN else AMBER_LT
        return (
            f"QPushButton{{"
            f" background:{bg}; border:2px solid {color};"
            f" border-radius:8px; color:{color};"
            f" font-family:'Inter','DM Sans','Liberation Sans',sans-serif;"
            f" font-size:12pt; font-weight:bold; padding:0px 16px;}}"
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
            f" font-size:12pt; font-weight:bold; padding:0px 16px;}}"
            f"QPushButton:pressed{{"
            f" background:#145E35; border-color:#145E35; color:#FFFFFF;}}")

    def _ss_stop(self):
        return (
            f"QPushButton{{"
            f" background:{RED}; border:2px solid {RED};"
            f" border-radius:8px; color:#FFFFFF;"
            f" font-family:'Inter','DM Sans','Liberation Sans',sans-serif;"
            f" font-size:12pt; font-weight:bold; padding:0px 16px;}}"
            f"QPushButton:pressed{{"
            f" background:#8B1A1A; border-color:#8B1A1A; color:#FFFFFF;}}")

    def _ss_pause(self):
        return (
            f"QPushButton{{"
            f" background:{AMBER_LT}; border:2px solid {AMBER};"
            f" border-radius:8px; color:{AMBER};"
            f" font-family:'Inter','DM Sans','Liberation Sans',sans-serif;"
            f" font-size:12pt; font-weight:bold; padding:0px 16px;}}"
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
            f" font-size:12pt; font-weight:bold; padding:0px 16px;}}"
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
            f" font-size:10pt; font-weight:500; ")


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN APPLICATION
# ═════════════════════════════════════════════════════════════════════════════
SCREEN_W, SCREEN_H = 1024, 600


class TrackApp(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg     = load_cfg()
        self.logger  = CSVWriterThread(self)
        self.history = {k: [] for k, *_ in _METRICS}
        self._cloud_th = None
        self._latest_data = None
        self._session_path = ""
        self._session_count = 0
        self._push_pending = False

        self.setWindowTitle("Rail Inspection Unit v5.1")
        self.setStyleSheet(SS + "QWidget#TrackApp{background:#ECEFF4;}")

        self.sensor = SensorThread(self.cfg)
        self.sensor.data_ready.connect(self._on_data)
        self.sensor.fault.connect(self._on_fault)
        self.sensor.motion.connect(self._on_motion)
        self.sensor.start()

        self.logger.session_started.connect(self._on_session_started)
        self.logger.session_stopped.connect(self._on_session_stopped)
        self.logger.start()

        self._SCREEN_TIMEOUT_MS = 5 * 60 * 1000
        self._last_motion_time  = time.time()
        self._screen_off        = False

        self._screen_timer = QTimer(self)
        self._screen_timer.setInterval(10_000)
        self._screen_timer.timeout.connect(self._check_screen_timeout)
        self._screen_timer.start()

        self._display_timer = QTimer(self)
        self._display_timer.setInterval(DISPLAY_INTERVAL_MS)
        self._display_timer.timeout.connect(self._refresh_ui)
        self._display_timer.start()

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
        self.stack = QStackedWidget()

        self.dash = DashboardPage()
        self.dash.sig_toggle.connect(self._on_toggle)
        self.dash.sig_pause.connect(self._on_pause)
        self.dash.sig_entry.connect(lambda: self._goto(2))
        self.dash.sig_cal.connect(lambda: self._goto(1))
        self.stack.addWidget(self.dash)       # 0

        self.cal = CalibrationPage(self.cfg)
        self.cal.sig_back.connect(lambda: self._goto(0))
        self.stack.addWidget(self.cal)        # 1

        self.entry = DataEntryPage()
        self.entry.sig_back.connect(lambda: self._goto(0))
        self.stack.addWidget(self.entry)      # 2

        self.graph_pg = GraphPage()
        self.graph_pg.sig_back.connect(lambda: self._goto(0))
        self.stack.addWidget(self.graph_pg)   # 3

        self.csv_viewer = CSVViewerPage()
        self.csv_viewer.sig_back.connect(lambda: self._goto(0))
        self.csv_viewer.set_csv_dir(self.cfg["csv_dir"])
        self.stack.addWidget(self.csv_viewer) # 4

        root.addWidget(self.topbar)
        root.addWidget(self.stack, 1)

        self.showFullScreen()

    def _goto(self, idx):
        self.stack.setCurrentIndex(idx)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._screen_off:
            self._blank.setGeometry(self.rect())

    def _on_data(self, d):
        for key in self.history:
            if key in d:
                self.history[key].append(d[key])
                if len(self.history[key]) > 10_000:
                    self.history[key].pop(0)
        self._latest_data = d
        if self.sensor.active and self.logger.active:
            payload = dict(d)
            dist = float(d.get("dist", 0.0))
            payload["reference_type"] = "KM"
            payload["reference_value"] = f"{dist:.2f} km"
            self.logger.enqueue(payload)
            self._session_count += 1

    def _refresh_ui(self):
        if self._latest_data:
            self.dash.update_data(self._latest_data)
            if self.sensor.active and self.stack.currentWidget() is self.entry:
                self.entry.push_sensor_data(self._latest_data)
        self.dash.set_session(
            self._session_count, self.sensor.active, self._session_path)

    def _on_fault(self, msg):
        self.topbar.push_error(f"Sensor: {msg}" if msg else "")

    def _on_net(self, bars, cloud):
        self.topbar.update_net(bars, cloud)

    def _on_toggle(self, running):
        self.sensor.active = running
        if running:
            self.sensor.reset()
            self.history = {k: [] for k in self.history}
            self._session_count = 0
            self._session_path = ""
            self._push_pending = False
            self.logger.start_session(
                self.cfg["csv_dir"], self.cfg.get("hl_sec", 30), station="BLE")
        else:
            self._stop_logging_and_push(wait=False)

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
        if e.key() == Qt.Key_Q and (e.modifiers() & Qt.ControlModifier):
            self.close()
            return
        if e.key() == Qt.Key_Escape:
            if self.isFullScreen():
                self.showNormal()
                self.resize(W, H)
        super().keyPressEvent(e)

    def _stop_logging_and_push(self, wait=False):
        self._push_pending = True
        if wait:
            csv_path, count = self.logger.stop_session(wait=True, timeout_ms=60000)
            self._session_count = count
            self._session_path = csv_path or self._session_path
            self._push_pending = False
            if csv_path:
                self._start_cloud_push(csv_path, wait=True)
        else:
            self.logger.stop_session(wait=False)

    def _start_cloud_push(self, csv_path, wait=False):
        self._cloud_th = CloudPushThread(csv_path, self)
        self._cloud_th.done.connect(self._on_cloud_done)
        self._cloud_th.start()
        if wait:
            self._cloud_th.wait(60000)

    def _on_session_started(self, path):
        self._session_path = path
        self._session_count = 0
        self.dash.set_session(0, True, path)

    def _on_session_stopped(self, path, count):
        self._session_count = count
        if path:
            self._session_path = path
        self.dash.set_session(count, False, self._session_path)
        if self._push_pending and path:
            self._push_pending = False
            self._start_cloud_push(path, wait=False)

    def _on_cloud_done(self, ok, msg):
        self.topbar.update_net(3 if ok else 0, ok)
        if not ok:
            self.topbar.push_error(msg)
        else:
            self.topbar.push_error("")
        print(f"[Cloud] {msg}")

    def closeEvent(self, event):
        self.sensor.active = False
        self._stop_logging_and_push(wait=True)
        if self.sensor.isRunning():
            self.sensor.stop()
            self.sensor.wait(3000)
        if self.net.isRunning():
            self.net.stop()
            self.net.wait(3000)
        if self.logger.isRunning():
            self.logger.shutdown()
            self.logger.wait(5000)
        event.accept()


# ─────────────────────────────────────────────────────────────────────────────
def main():
    if not os.environ.get("XDG_RUNTIME_DIR"):
        runtime_dir = Path("/tmp/runtime-root")
        runtime_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(runtime_dir, 0o700)
        os.environ["XDG_RUNTIME_DIR"] = str(runtime_dir)
    app = QApplication(sys.argv)
    app.setApplicationName("Rail Inspection Unit")
    app.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app.restoreOverrideCursor()
    w = TrackApp()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
