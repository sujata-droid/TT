#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

export RAIL_ENCODER_PPR="${RAIL_ENCODER_PPR:-400}"
export RAIL_WHEEL_DIAMETER_MM="${RAIL_WHEEL_DIAMETER_MM:-50}"
export RAIL_ENCODER_FACTOR="${RAIL_ENCODER_FACTOR:-1.123596}"
export PYTHONDONTWRITEBYTECODE=1

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

echo "== Rotary encoder console test =="
echo "root=$ROOT_DIR"
echo "ppr_per_channel=$RAIL_ENCODER_PPR (2X decode: $((RAIL_ENCODER_PPR * 2)) counts/rev)"
echo "wheel_diameter_mm=$RAIL_WHEEL_DIAMETER_MM"
echo "factor=$RAIL_ENCODER_FACTOR"
echo

echo "[1/2] Setting up PCB GPIO encoder input (P8_15=A, P8_12=B)"
$SUDO bash "$ROOT_DIR/setup_encoder_gpio.sh"
echo

echo "[2/2] Launching console reader"
exec $SUDO python3 "$ROOT_DIR/tools/encoder_gpio_console_test.py" \
    --ppr "$RAIL_ENCODER_PPR" \
    --wheel-diameter-mm "$RAIL_WHEEL_DIAMETER_MM" \
    --factor "$RAIL_ENCODER_FACTOR" \
    "$@"
