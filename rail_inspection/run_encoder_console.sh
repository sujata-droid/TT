#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

export RAIL_ENCODER_PPR="${RAIL_ENCODER_PPR:-400}"
export RAIL_WHEEL_DIAMETER_MM="${RAIL_WHEEL_DIAMETER_MM:-250}"
export PYTHONDONTWRITEBYTECODE=1

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

echo "== Rotary encoder console test =="
echo "root=$ROOT_DIR"
echo "ppr=$RAIL_ENCODER_PPR"
echo "wheel_diameter_mm=$RAIL_WHEEL_DIAMETER_MM"
echo

echo "[1/2] Setting up PRU quadrature input"
$SUDO bash "$ROOT_DIR/setup_encoder_pru.sh"
echo

echo "[2/2] Launching console reader"
exec $SUDO python3 "$ROOT_DIR/tools/encoder_console_test.py" \
    --ppr "$RAIL_ENCODER_PPR" \
    --wheel-diameter-mm "$RAIL_WHEEL_DIAMETER_MM" \
    "$@"
