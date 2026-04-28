#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_PIN="$(command -v config-pin 2>/dev/null || echo /usr/bin/config-pin)"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash setup_encoder_pru.sh" >&2
    exit 1
fi

if [ ! -x "$CONFIG_PIN" ]; then
    echo "config-pin not found" >&2
    exit 1
fi

echo "[ENC] Configuring BBB pins for PRU quadrature input"
"$CONFIG_PIN" P9_27 pruin
"$CONFIG_PIN" P9_30 pruin

echo "[ENC] Building PRU firmware"
make -C "$ROOT_DIR/sensor_board" pru

echo "[ENC] Installing PRU firmware"
cp "$ROOT_DIR/pru/encoder_pru0.out" /lib/firmware/am335x-pru0-fw

started=0
for d in /sys/class/remoteproc/remoteproc*; do
    [ -e "$d/name" ] || continue
    if grep -q "4a334000.pru" "$d/name" 2>/dev/null; then
        state="$(cat "$d/state" 2>/dev/null || echo unknown)"
        if [ "$state" = "running" ]; then
            echo stop > "$d/state" 2>/dev/null || true
            sleep 1
        fi
        echo am335x-pru0-fw > "$d/firmware"
        sleep 1
        echo start > "$d/state"
        echo "[ENC] PRU0 started via $d"
        started=1
        break
    fi
done

if [ "$started" -ne 1 ]; then
    echo "Could not find PRU0 remoteproc node" >&2
    exit 1
fi

echo "[ENC] Launch test with:"
echo "sudo python3 $ROOT_DIR/tools/encoder_console_test.py --ppr 400 --wheel-diameter-mm 250"
