#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT_DIR/bbb_runtime/logs"
SENSOR_LOG="$LOG_DIR/sensor_service.log"
DIAG_JSON="$LOG_DIR/backend_diag.json"
MODE="${1:-all}"
export RAIL_CLOUD_URL="${RAIL_CLOUD_URL:-https://thread-qm2o.onrender.com}"
export RAIL_ENCODER_PPR="${RAIL_ENCODER_PPR:-400}"
export RAIL_WHEEL_DIAMETER_MM="${RAIL_WHEEL_DIAMETER_MM:-250}"
export RAIL_ENCODER_INVERT="${RAIL_ENCODER_INVERT:-0}"
export PYTHONDONTWRITEBYTECODE=1

mkdir -p "$LOG_DIR"

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

cleanup() {
    if [ -n "${SENSOR_PID:-}" ]; then
        kill "$SENSOR_PID" 2>/dev/null || true
        wait "$SENSOR_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo "== RailGUI25 backend runtime =="
echo "root=$ROOT_DIR"
echo "mode=$MODE"
echo "cloud=$RAIL_CLOUD_URL"
echo

echo "[1/6] Verifying files"
python3 -m py_compile "$ROOT_DIR/railgui25.py"
python3 -m py_compile "$ROOT_DIR/bbb_runtime/backend_bridge.py"
python3 -m py_compile "$ROOT_DIR/bbb_runtime/launch_railgui25_backend.py"
python3 -m py_compile "$ROOT_DIR/tools/encoder_console_test.py"
make -C "$ROOT_DIR/sensor_board" >/dev/null
echo "ok"
echo

echo "[2/6] Preparing PRU encoder"
$SUDO bash "$ROOT_DIR/setup_encoder_pru.sh"
echo

echo "[3/6] Starting sensor backend"
$SUDO pkill -x sensor_service 2>/dev/null || true
$SUDO bash -c "cd '$ROOT_DIR' && env RAIL_ENCODER_PPR='$RAIL_ENCODER_PPR' RAIL_WHEEL_DIAMETER_MM='$RAIL_WHEEL_DIAMETER_MM' RAIL_ENCODER_INVERT='$RAIL_ENCODER_INVERT' ./sensor_board/sensor_service >'$SENSOR_LOG' 2>&1" &
SENSOR_PID=$!
sleep 2
if ! kill -0 "$SENSOR_PID" 2>/dev/null; then
    echo "sensor_service failed to stay up"
    cat "$SENSOR_LOG"
    exit 1
fi
echo "sensor_service pid=$SENSOR_PID"
echo

if [ "$MODE" = "diag-only" ]; then
    echo "[4/6] Running stationary diagnostics"
    python3 "$ROOT_DIR/bbb_runtime/launch_railgui25_backend.py" --diag-only --diag-json "$DIAG_JSON"
    echo

    echo "[5/6] Recent backend log"
    tail -n 20 "$SENSOR_LOG" || true
    echo

    echo "[6/6] diag-only mode complete"
    exit 0
fi

echo "[4/6] Backend ready"
echo

echo "[5/6] Recent backend log"
tail -n 20 "$SENSOR_LOG" || true
echo

echo "[6/6] Launching railgui25 via shared-memory wrapper"
exec python3 "$ROOT_DIR/bbb_runtime/launch_railgui25_backend.py"
