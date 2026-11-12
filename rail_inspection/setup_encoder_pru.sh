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

echo "[ENC] Configuring PCB encoder pins for PRU1 quadrature via GPIO1_DATAIN (P8_15=A, P8_12=B)"
"$CONFIG_PIN" P8_15 gpio 2>/dev/null || "$CONFIG_PIN" P8_15 default 2>/dev/null || true
"$CONFIG_PIN" P8_12 gpio 2>/dev/null || "$CONFIG_PIN" P8_12 default 2>/dev/null || true
"$CONFIG_PIN" -q P8_15 || true
"$CONFIG_PIN" -q P8_12 || true

echo "[ENC] Building PRU1 firmware (P8_15/P8_12)"
make -C "$ROOT_DIR/sensor_board" pru1

echo "[ENC] Installing PRU1 firmware"
cp "$ROOT_DIR/pru/encoder_pru1.out" /lib/firmware/am335x-pru1-fw

wait_for_pru_stop() {
    local node="$1"
    local state=""
    local i=0

    for i in 1 2 3 4 5; do
        state="$(cat "$node/state" 2>/dev/null || echo unknown)"
        [ "$state" != "running" ] && return 0
        sleep 0.2
    done

    return 1
}

started=0
for d in /sys/class/remoteproc/remoteproc*; do
    [ -e "$d/name" ] || continue
    # PRU1 is at 4a338000.pru
    if grep -q "4a338000.pru" "$d/name" 2>/dev/null; then
        state="$(cat "$d/state" 2>/dev/null || echo unknown)"
        if [ "$state" = "running" ]; then
            echo stop > "$d/state" 2>/dev/null || true
            wait_for_pru_stop "$d" || true
        fi
        echo am335x-pru1-fw > "$d/firmware"
        sleep 0.5
        if ! echo start > "$d/state" 2>/dev/null; then
            state="$(cat "$d/state" 2>/dev/null || echo unknown)"
            if [ "$state" != "running" ]; then
                echo "Could not start PRU1 via $d (state=$state)" >&2
                exit 1
            fi
        fi
        echo "[ENC] PRU1 started via $d"
        started=1
        break
    fi
done

if [ "$started" -ne 1 ]; then
    echo "Could not find PRU1 remoteproc node (4a338000.pru)" >&2
    exit 1
fi

echo "[ENC] PRU1 hardware quadrature active! P9_30 (SCL3300 SPI1 MOSI) is safe."
echo "[ENC] Launch test with:"
echo "sudo python3 $ROOT_DIR/tools/encoder_console_test.py --ppr 400 --wheel-diameter-mm 50"
