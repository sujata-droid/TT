#!/usr/bin/env python3
"""PCB GPIO rotary encoder console test for BeagleBone Black (P8_15=A, P8_12=B)."""

import argparse
import mmap
import os
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path

# Shared memory frame layout matching shared_frame.h
SHM_PATH = "/dev/shm/rail_sensor_shm"
SHM_STRUCT = struct.Struct("<IIIIqddddiBBBB")


def read_shm_frame():
    """Attempt reading frame from sensor_service shared memory."""
    if not os.path.exists(SHM_PATH):
        return None
    try:
        with open(SHM_PATH, "r+b") as f:
            mm = mmap.mmap(f.fileno(), SHM_STRUCT.size, mmap.MAP_SHARED, mmap.PROT_READ)
            try:
                for _ in range(10):
                    b1 = mm[:SHM_STRUCT.size]
                    v1 = SHM_STRUCT.unpack(b1)
                    if v1[2] & 1:  # seq is odd -> write in progress
                        time.sleep(0.002)
                        continue
                    b2 = mm[:SHM_STRUCT.size]
                    v2 = SHM_STRUCT.unpack(b2)
                    if v1[2] == v2[2] and not (v2[2] & 1):
                        if v2[0] == 0x5241494C:  # 'RAIL' magic
                            return {
                                "count": v2[9],
                                "chainage_m": v2[7],
                                "encoder_ok": bool(v2[11]),
                                "update_count": v2[3],
                            }
                    time.sleep(0.002)
            finally:
                mm.close()
    except Exception:
        return None
    return None


def main():
    parser = argparse.ArgumentParser(description="PCB Rotary Encoder Console Monitor (P8_15=A, P8_12=B)")
    parser.add_argument("--wheel-diameter-mm", type=float, default=50.0)
    parser.add_argument("--ppr", type=int, default=400)
    parser.add_argument("--factor", type=float, default=None, help="distance calibration multiplier")
    parser.add_argument("--sample-hz", type=float, default=10.0)
    parser.add_argument("--duration", type=float, default=0.0)
    args = parser.parse_args()

    factor = args.factor
    if factor is None:
        cfg_path = Path(__file__).resolve().parents[1] / "rail_config.json"
        if cfg_path.exists():
            try:
                import json
                cfg = json.loads(cfg_path.read_text())
                factor = float(cfg.get("encoder", {}).get("factor", 1.0))
            except Exception:
                factor = 1.0
        else:
            factor = 1.0

    if os.geteuid() != 0:
        raise SystemExit("Run with sudo: sudo python3 tools/encoder_gpio_console_test.py")

    spawned_proc = None
    shm_test = read_shm_frame()
    if shm_test is None:
        # Start sensor_service in the background
        root_dir = Path(__file__).resolve().parents[1]
        svc_bin = root_dir / "sensor_board" / "sensor_service"
        if not svc_bin.exists():
            raise SystemExit(f"sensor_service binary not found at {svc_bin}. Run make in sensor_board first.")

        env = os.environ.copy()
        env["RAIL_ENCODER_PPR"] = str(args.ppr)
        env["RAIL_WHEEL_DIAMETER_MM"] = str(args.wheel_diameter_mm)
        env["RAIL_ENCODER_FACTOR"] = str(factor)
        print(f"[ENC] Starting sensor backend ({svc_bin})...")
        spawned_proc = subprocess.Popen(
            [str(svc_bin)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(root_dir),
            env=env,
        )
        for _ in range(25):
            time.sleep(0.2)
            if read_shm_frame() is not None:
                break

    try:
        print("=== PCB Rotary Encoder Console Monitor (P8_15=A, P8_12=B) ===")
        print(f"Geometry: ppr={args.ppr} decode=2X wheel_diameter_mm={args.wheel_diameter_mm:.1f} factor={factor:.6f}")
        print("Fields: elapsed_s count delta dir chainage_m")
        prev_count = None
        start = time.time()
        sample_period = 1.0 / args.sample_hz
        while True:
            frame = read_shm_frame()
            if frame is not None:
                count = frame["count"]
                now = time.time()
                elapsed = now - start
                delta = 0 if prev_count is None else (count - prev_count)
                prev_count = count
                direction = "FWD" if delta > 0 else ("REV" if delta < 0 else "STOP")
                print(f"{elapsed:8.3f}s  count={count:10d}  delta={delta:6d}  dir={direction:4s}  chainage_m={frame['chainage_m']:10.5f}")
                sys.stdout.flush()
                if args.duration > 0.0 and elapsed >= args.duration:
                    break
            time.sleep(sample_period)
    except KeyboardInterrupt:
        pass
    finally:
        if spawned_proc is not None:
            spawned_proc.terminate()
            try:
                spawned_proc.wait(timeout=2.0)
            except Exception:
                spawned_proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
