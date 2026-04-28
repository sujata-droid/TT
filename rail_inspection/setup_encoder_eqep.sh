#!/bin/bash
set -euo pipefail

CONFIG_PIN="$(command -v config-pin 2>/dev/null || echo /usr/bin/config-pin)"
EQEP_PATH="/sys/devices/platform/ocp/48304000.epwmss/48304180.eqep"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash setup_encoder_eqep.sh" >&2
    exit 1
fi

if [ ! -x "$CONFIG_PIN" ]; then
    echo "config-pin not found" >&2
    exit 1
fi

echo "[ENC] Configuring BBB pins for eQEP2"
"$CONFIG_PIN" P8_11 qep
"$CONFIG_PIN" P8_12 qep
"$CONFIG_PIN" -q P8_11
"$CONFIG_PIN" -q P8_12

if [ -d "$EQEP_PATH" ]; then
    echo "[ENC] eQEP2 path: $EQEP_PATH"
    echo "[ENC] mode=$(cat "$EQEP_PATH/mode" 2>/dev/null || echo unknown)"
    echo "[ENC] enabled=$(cat "$EQEP_PATH/enabled" 2>/dev/null || echo unknown)"
else
    echo "[ENC] eQEP2 path not found: $EQEP_PATH" >&2
    exit 1
fi

echo "[ENC] Launch test with:"
echo "sudo python3 $(cd "$(dirname "$0")" && pwd)/tools/encoder_eqep_console_test.py --ppr 400 --wheel-diameter-mm 250"
