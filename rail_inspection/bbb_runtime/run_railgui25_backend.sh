#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT_DIR/bbb_runtime/logs"
SENSOR_LOG="$LOG_DIR/sensor_service.log"
DIAG_JSON="$LOG_DIR/backend_diag.json"
MODE="${1:-all}"

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
echo

echo "[1/5] Verifying files"
python3 -m py_compile "$ROOT_DIR/railgui25.py"
python3 -m py_compile "$ROOT_DIR/bbb_runtime/backend_bridge.py"
python3 -m py_compile "$ROOT_DIR/bbb_runtime/launch_railgui25_backend.py"
make -C "$ROOT_DIR/sensor_board" >/dev/null
echo "ok"
echo

echo "[2/5] Starting sensor backend"
$SUDO pkill -x sensor_service 2>/dev/null || true
$SUDO bash -c "cd '$ROOT_DIR' && ./sensor_board/sensor_service >'$SENSOR_LOG' 2>&1" &
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
    echo "[3/5] Running stationary diagnostics"
    python3 "$ROOT_DIR/bbb_runtime/launch_railgui25_backend.py" --diag-only --diag-json "$DIAG_JSON"
    echo

    echo "[4/5] Recent backend log"
    tail -n 20 "$SENSOR_LOG" || true
    echo

    echo "[5/5] diag-only mode complete"
    exit 0
fi

echo "[3/5] Backend ready"
echo

echo "[4/5] Recent backend log"
tail -n 20 "$SENSOR_LOG" || true
echo

echo "[5/5] Launching railgui25 via shared-memory wrapper"
exec python3 "$ROOT_DIR/bbb_runtime/launch_railgui25_backend.py"
