#!/usr/bin/env python3
"""Shared-memory bridge for railgui25.py without modifying the original file."""

import json
import math
import mmap
import os
import select
import socket
import statistics
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional


SHM_PATH = os.environ.get("RAIL_SENSOR_SHM", "/dev/shm/rail_sensor_shm")
SHM_MAGIC = 0x5241494C
SHM_VERSION = 1
SHM_STRUCT = __import__("struct").Struct("<IIIIqddddiBBBB")

DISPLAY_DECIMALS = 1
TWIST_DISPLAY_DECIMALS = 2
DISPLAY_HZ = 10
RAW_POLL_HZ = 50

WARMUP_SECONDS = 2.0
DIAG_CAPTURE_SECONDS = 6.0
ZERO_CAPTURE_SECONDS = 2.0

CROSS_AVG_TAPS = 1
TWIST_AVG_TAPS = 1
GAUGE_AVG_TAPS = 5
DIST_AVG_TAPS = 1

CROSS_DEADBAND_MM = 0.0
TWIST_DEADBAND_MM_M = 0.02
GAUGE_DEADBAND_MM = 0.05
DIST_DEADBAND_M = 0.0

ZERO_HOLD_MM = 0.0
TWIST_ZERO_HOLD_MM_M = 0.02

MOTION_CROSS_MM = 0.15
MOTION_TWIST_MM_M = 0.15
MOTION_GAUGE_MM = 0.10
MOTION_DIST_M = 0.05

GPSD_HOST = os.environ.get("RAIL_GPSD_HOST", "127.0.0.1")
GPSD_PORT = int(os.environ.get("RAIL_GPSD_PORT", "2947"))
GPS_STALE_SECONDS = float(os.environ.get("RAIL_GPS_STALE_SECONDS", "30"))


def _rounded(value: float) -> float:
    return round(float(value), DISPLAY_DECIMALS)


def _rounded_twist(value: float) -> float:
    return round(float(value), TWIST_DISPLAY_DECIMALS)


def _stable_update(previous: float, candidate: float, deadband: float) -> float:
    if abs(candidate - previous) < deadband:
        return previous
    return candidate


def _queue_mean(values: Deque[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / float(len(values))


@dataclass
class RawFrame:
    ts_us: int
    cross_level_mm: float
    twist_mm_per_m: float
    chainage_m: float
    gauge_mm: float
    encoder_count: int
    scl_ok: bool
    encoder_ok: bool


class GPSDReader:
    """Non-blocking gpsd client.

    gpsd only provides latitude/longitude after satellite fix. Until TPV mode is
    2 or 3, this reader deliberately returns 0.0 so the CSV does not contain a
    fake location.
    """

    def __init__(self):
        self._sock = None
        self._buf = ""
        self._next_connect = 0.0
        self._lat = 0.0
        self._lon = 0.0
        self._speed = 0.0
        self._mode = 0
        self._last_fix = 0.0

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None

    def _connect(self) -> None:
        now = time.time()
        if self._sock is not None or now < self._next_connect:
            return
        self._next_connect = now + 5.0
        try:
            sock = socket.create_connection((GPSD_HOST, GPSD_PORT), timeout=0.25)
            sock.setblocking(False)
            sock.sendall(b'?WATCH={"enable":true,"json":true};\n')
            self._sock = sock
        except Exception:
            self.close()

    def poll(self) -> Dict[str, float]:
        self._connect()
        if self._sock is None:
            return self.latest()

        try:
            while True:
                readable, _, _ = select.select([self._sock], [], [], 0)
                if not readable:
                    break
                chunk = self._sock.recv(4096)
                if not chunk:
                    self.close()
                    break
                self._buf += chunk.decode("ascii", "ignore")
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    self._handle_line(line.strip())
        except Exception:
            self.close()
        return self.latest()

    def _handle_line(self, line: str) -> None:
        if not line:
            return
        try:
            msg = json.loads(line)
        except Exception:
            return
        if msg.get("class") != "TPV":
            return
        try:
            self._mode = int(msg.get("mode", self._mode or 0))
        except Exception:
            self._mode = 0
        if self._mode >= 2 and "lat" in msg and "lon" in msg:
            self._lat = float(msg["lat"])
            self._lon = float(msg["lon"])
            self._speed = float(msg.get("speed", 0.0) or 0.0)
            self._last_fix = time.time()

    def latest(self) -> Dict[str, float]:
        fresh = self._last_fix > 0.0 and (time.time() - self._last_fix) <= GPS_STALE_SECONDS
        return {
            "lat": self._lat if fresh else 0.0,
            "lon": self._lon if fresh else 0.0,
            "speed": self._speed if fresh else 0.0,
            "gps_mode": self._mode,
            "gps_ok": bool(fresh),
        }


class SharedMemoryBridge:
    def __init__(self, cfg: Optional[dict] = None):
        self.cfg = cfg or {}
        self._fd = None
        self._shm = None
        self._gps = GPSDReader()
        self._last_update_count = None
        self._cross_hist: Deque[float] = deque(maxlen=CROSS_AVG_TAPS)
        self._twist_hist: Deque[float] = deque(maxlen=TWIST_AVG_TAPS)
        self._gauge_hist: Deque[float] = deque(maxlen=GAUGE_AVG_TAPS)
        self._dist_hist: Deque[float] = deque(maxlen=DIST_AVG_TAPS)
        self._display = {
            "gauge": 0.0,
            "cross": 0.0,
            "twist": 0.0,
            "dist": 0.0,
            "lat": 0.0,
            "lon": 0.0,
            "speed": 0.0,
            "scl_ok": False,
            "encoder_ok": False,
            "raw_cross_mm": 0.0,
            "raw_twist_mm_m": 0.0,
            "raw_gauge_mm": 0.0,
            "ts": 0,
        }
        self._last_motion_sample = None
        self._chainage_offset = None
        self._session_zero_cross_mm = 0.0
        self._zero_calibrated = True
        self._twist_sample_step_m = 0.25
        self._twist_baseline_m = 3.0
        self._twist_baseline_steps = max(1, int(round(self._twist_baseline_m / self._twist_sample_step_m)))
        self._twist_step_samples: Dict[int, float] = {}
        self._twist_last_step_index: Optional[int] = None

    def close(self) -> None:
        self._gps.close()
        if self._shm is not None:
            try:
                self._shm.close()
            except Exception:
                pass
            self._shm = None
        if self._fd is not None:
            try:
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None

    def reset_display_reference(self) -> None:
        self._chainage_offset = self._display.get("raw_dist_m", 0.0)
        self._last_motion_sample = None
        self._cross_hist.clear()
        self._twist_hist.clear()
        self._gauge_hist.clear()
        self._dist_hist.clear()
        self._twist_step_samples.clear()
        self._twist_last_step_index = None
        self._display.update({
            "twist": 0.0,
            "dist": 0.0,
            "raw_twist_mm_m": 0.0,
        })

    def _ensure_open(self) -> None:
        if self._shm is not None:
            return
        self._fd = os.open(SHM_PATH, os.O_RDONLY)
        self._shm = mmap.mmap(self._fd, SHM_STRUCT.size, access=mmap.ACCESS_READ)

    def read_next_frame(self, require_new: bool = True, timeout_s: float = 2.0) -> RawFrame:
        self._ensure_open()
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            self._shm.seek(0)
            raw1 = self._shm.read(SHM_STRUCT.size)
            vals1 = SHM_STRUCT.unpack(raw1)
            seq1 = vals1[2]
            if seq1 & 1:
                time.sleep(0.005)
                continue

            self._shm.seek(0)
            raw2 = self._shm.read(SHM_STRUCT.size)
            vals2 = SHM_STRUCT.unpack(raw2)
            if vals2[2] != seq1 or (vals2[2] & 1):
                time.sleep(0.005)
                continue

            magic, version, _seq, update_count, ts_us, cl_mm, tw_mm_m, ch_m, gauge_mm, enc_count, scl_ok, enc_ok, service_ok, _ = vals2
            if magic != SHM_MAGIC or version != SHM_VERSION:
                raise RuntimeError("shared-memory version mismatch")
            if not service_ok:
                raise RuntimeError("sensor service not publishing valid data")
            if require_new and update_count == self._last_update_count:
                time.sleep(1.0 / RAW_POLL_HZ)
                continue

            self._last_update_count = update_count
            return RawFrame(
                ts_us=ts_us,
                cross_level_mm=float(cl_mm),
                twist_mm_per_m=float(tw_mm_m),
                chainage_m=float(ch_m),
                gauge_mm=float(gauge_mm),
                encoder_count=int(enc_count),
                scl_ok=bool(scl_ok),
                encoder_ok=bool(enc_ok),
            )
        raise TimeoutError("timed out waiting for sensor frame")

    def _apply_cfg(self, frame: RawFrame) -> Dict[str, float]:
        incl = self.cfg.get("incl", {})
        enc = self.cfg.get("encoder", {})

        cross = (frame.cross_level_mm - float(incl.get("offset", 0.0))) * float(incl.get("factor", 1.0))
        twist = frame.twist_mm_per_m
        gauge = frame.gauge_mm
        raw_dist = frame.chainage_m * float(enc.get("factor", 1.0))
        if self._chainage_offset is None:
            self._chainage_offset = raw_dist
        # Show travelled distance from the session start regardless of
        # encoder direction; previously reverse motion was clipped to 0.0.
        dist = abs(raw_dist - self._chainage_offset)
        return {
            "cross": cross,
            "twist": twist,
            "gauge": gauge,
            "dist": dist,
            "raw_dist_m": raw_dist,
        }

    def calibrate_zero(self, seconds: float = ZERO_CAPTURE_SECONDS) -> float:
        samples: List[float] = []
        start = time.time()
        while time.time() - start < seconds:
            frame = self.read_next_frame(require_new=True, timeout_s=2.0)
            values = self._apply_cfg(frame)
            samples.append(values["cross"])
        if not samples:
            raise RuntimeError("zero calibration did not collect any inclinometer samples")
        self._session_zero_cross_mm = statistics.mean(samples)
        self._zero_calibrated = True
        self._cross_hist.clear()
        self._display["cross"] = 0.0
        return self._session_zero_cross_mm

    def _smooth_display(self, values: Dict[str, float], frame: RawFrame) -> Dict[str, float]:
        cross = values["cross"] - self._session_zero_cross_mm

        self._cross_hist.append(cross)
        self._gauge_hist.append(values["gauge"])
        self._dist_hist.append(values["dist"])

        cross_avg = _queue_mean(self._cross_hist)
        gauge_avg = _queue_mean(self._gauge_hist)
        dist_avg = _queue_mean(self._dist_hist)
        session_dist = max(0.0, values["dist"])
        step_index = int(round(session_dist / self._twist_sample_step_m)) if self._twist_sample_step_m > 0 else 0
        if self._twist_last_step_index is None or step_index != self._twist_last_step_index:
            self._twist_step_samples[step_index] = cross
            self._twist_last_step_index = step_index
        twist_raw = self._display.get("raw_twist_mm_m", 0.0)
        on_twist_point = (
            step_index > 0
            and (step_index % self._twist_baseline_steps) == 0
            and abs(session_dist - (step_index * self._twist_sample_step_m)) <= (self._twist_sample_step_m * 0.51)
        )
        if on_twist_point:
            prev_step = step_index - self._twist_baseline_steps
            if prev_step in self._twist_step_samples and step_index in self._twist_step_samples:
                twist_raw = (self._twist_step_samples[step_index] - self._twist_step_samples[prev_step]) / self._twist_baseline_m
        self._twist_hist.clear()
        self._twist_hist.append(twist_raw)
        twist_avg = _queue_mean(self._twist_hist)

        if abs(cross_avg) < ZERO_HOLD_MM:
            cross_avg = 0.0
        if abs(twist_avg) < TWIST_ZERO_HOLD_MM_M and step_index == 0:
            twist_avg = 0.0

        self._display["cross"] = _rounded(_stable_update(self._display["cross"], cross_avg, CROSS_DEADBAND_MM))
        self._display["twist"] = _rounded_twist(_stable_update(self._display["twist"], twist_avg, TWIST_DEADBAND_MM_M))
        self._display["gauge"] = _rounded(_stable_update(self._display["gauge"], gauge_avg, GAUGE_DEADBAND_MM))
        self._display["dist"] = _rounded(_stable_update(self._display["dist"], dist_avg, DIST_DEADBAND_M))
        gps = self._gps.poll()
        self._display["lat"] = gps["lat"]
        self._display["lon"] = gps["lon"]
        self._display["speed"] = gps["speed"]
        self._display["gps_mode"] = gps["gps_mode"]
        self._display["gps_ok"] = gps["gps_ok"]
        self._display["scl_ok"] = frame.scl_ok
        self._display["encoder_ok"] = frame.encoder_ok
        self._display["raw_cross_mm"] = cross
        self._display["raw_twist_mm_m"] = twist_raw
        self._display["raw_gauge_mm"] = values["gauge"]
        self._display["raw_dist_m"] = values["raw_dist_m"]
        self._display["ts"] = frame.ts_us
        return dict(self._display)

    def next_display_sample(self) -> Dict[str, float]:
        if not self._zero_calibrated:
            self.calibrate_zero()
        frame = self.read_next_frame(require_new=True)
        values = self._apply_cfg(frame)
        return self._smooth_display(values, frame)

    def is_moving(self, display_sample: Dict[str, float]) -> bool:
        sample = (
            display_sample["cross"],
            display_sample["twist"],
            display_sample["gauge"],
            display_sample["dist"],
        )
        if self._last_motion_sample is None:
            self._last_motion_sample = sample
            return False
        moving = (
            abs(sample[0] - self._last_motion_sample[0]) >= MOTION_CROSS_MM or
            abs(sample[1] - self._last_motion_sample[1]) >= MOTION_TWIST_MM_M or
            abs(sample[2] - self._last_motion_sample[2]) >= MOTION_GAUGE_MM or
            abs(sample[3] - self._last_motion_sample[3]) >= MOTION_DIST_M
        )
        self._last_motion_sample = sample
        return moving

    def diagnose(self, warmup_s: float = WARMUP_SECONDS, capture_s: float = DIAG_CAPTURE_SECONDS) -> Dict[str, float]:
        raw_cross: List[float] = []
        display_cross: List[float] = []
        display_twist: List[float] = []
        start = time.time()
        while time.time() - start < warmup_s:
            self.next_display_sample()

        capture_start = time.time()
        while time.time() - capture_start < capture_s:
            sample = self.next_display_sample()
            raw_cross.append(sample["raw_cross_mm"])
            display_cross.append(sample["cross"])
            display_twist.append(sample["twist"])

        return {
            "samples": len(raw_cross),
            "raw_cross_min_mm": min(raw_cross) if raw_cross else 0.0,
            "raw_cross_max_mm": max(raw_cross) if raw_cross else 0.0,
            "raw_cross_pkpk_mm": (max(raw_cross) - min(raw_cross)) if raw_cross else 0.0,
            "raw_cross_stdev_mm": statistics.pstdev(raw_cross) if len(raw_cross) > 1 else 0.0,
            "display_cross_min_mm": min(display_cross) if display_cross else 0.0,
            "display_cross_max_mm": max(display_cross) if display_cross else 0.0,
            "display_cross_pkpk_mm": (max(display_cross) - min(display_cross)) if display_cross else 0.0,
            "display_cross_stdev_mm": statistics.pstdev(display_cross) if len(display_cross) > 1 else 0.0,
            "display_twist_min_mm_m": min(display_twist) if display_twist else 0.0,
            "display_twist_max_mm_m": max(display_twist) if display_twist else 0.0,
            "display_twist_pkpk_mm_m": (max(display_twist) - min(display_twist)) if display_twist else 0.0,
        }

    def capture_raw_cross_stats(self, seconds: float = 2.0) -> Dict[str, float]:
        samples: List[float] = []
        start = time.time()
        while time.time() - start < seconds:
            frame = self.read_next_frame(require_new=True, timeout_s=2.0)
            samples.append(frame.cross_level_mm)
        if not samples:
            raise RuntimeError("no inclinometer samples captured")
        return {
            "samples": len(samples),
            "mean_mm": statistics.mean(samples),
            "stdev_mm": statistics.pstdev(samples) if len(samples) > 1 else 0.0,
            "min_mm": min(samples),
            "max_mm": max(samples),
            "pkpk_mm": max(samples) - min(samples),
        }


def format_diag(diag: Dict[str, float]) -> str:
    lines = ["Shared-memory diagnostics"]
    for key in [
        "samples",
        "raw_cross_min_mm",
        "raw_cross_max_mm",
        "raw_cross_pkpk_mm",
        "raw_cross_stdev_mm",
        "display_cross_min_mm",
        "display_cross_max_mm",
        "display_cross_pkpk_mm",
        "display_cross_stdev_mm",
        "display_twist_min_mm_m",
        "display_twist_max_mm_m",
        "display_twist_pkpk_mm_m",
    ]:
        value = diag.get(key, 0.0)
        if isinstance(value, float):
            lines.append(f"{key}={value:.4f}")
        else:
            lines.append(f"{key}={value}")
    return "\n".join(lines)
