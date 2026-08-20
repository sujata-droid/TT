#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT_DIR/bbb_runtime/logs"
SENSOR_LOG="$LOG_DIR/sensor_service.log"
DIAG_JSON="$LOG_DIR/backend_diag.json"
MODE="${1:-all}"
export RAIL_CLOUD_URL="${RAIL_CLOUD_URL:-https://lwtmt-cloud-backend.onrender.com/api/survey}"
export RAIL_SCL_AXIS="${RAIL_SCL_AXIS:-X}"
export RAIL_ENCODER_PPR="${RAIL_ENCODER_PPR:-400}"
export RAIL_WHEEL_DIAMETER_MM="${RAIL_WHEEL_DIAMETER_MM:-250}"
export RAIL_ENCODER_INVERT="${RAIL_ENCODER_INVERT:-0}"
export RAIL_SAMPLING_DISTANCE_M="${RAIL_SAMPLING_DISTANCE_M:-0.25}"
export RAIL_TWIST_BASE_M="${RAIL_TWIST_BASE_M:-3.0}"
export RAIL_GPS_DEVICE="${RAIL_GPS_DEVICE:-/dev/ttyO4}"
export RAIL_CSV_DIR="${RAIL_CSV_DIR:-/home/debian/surveys}"
export RAIL_GAUGE_SOURCE="${RAIL_GAUGE_SOURCE:-laser_adc}"
export RAIL_GAUGE_OUTPUT_MODE="${RAIL_GAUGE_OUTPUT_MODE:-deviation}"
export RAIL_ADC_PATH="${RAIL_ADC_PATH:-/sys/bus/iio/devices/iio:device0/in_voltage0_raw}"
export RAIL_ADC_MAX_RAW="${RAIL_ADC_MAX_RAW:-3072}"
export RAIL_LASER_MIN_MM="${RAIL_LASER_MIN_MM:-160.0}"
export RAIL_LASER_MAX_MM="${RAIL_LASER_MAX_MM:-0.0}"
export RAIL_LASER_ZERO_MM="${RAIL_LASER_ZERO_MM:-80.0}"
export RAIL_LASER_ZERO_RAW="${RAIL_LASER_ZERO_RAW:--1}"
export RAIL_LASER_MPC="${RAIL_LASER_MPC:--0.039072}"
export RAIL_LASER_AUTO_ZERO="${RAIL_LASER_AUTO_ZERO:-1}"
export RAIL_LASER_SIGN="${RAIL_LASER_SIGN:--1}"
export RAIL_GAUGE_FACTOR="${RAIL_GAUGE_FACTOR:-1.0}"
export PYTHONDONTWRITEBYTECODE=1

mkdir -p "$LOG_DIR"

if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
else
    SUDO="sudo"
fi

cleanup() {
    if [ -n "${SENSOR_PID:-}" ]; then
        $SUDO kill "$SENSOR_PID" 2>/dev/null || true
        wait "$SENSOR_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo "== RailGUI25 backend runtime =="
echo "root=$ROOT_DIR"
echo "mode=$MODE"
echo "cloud=$RAIL_CLOUD_URL"
echo

echo "[1/7] Verifying files"
python3 -m py_compile "$ROOT_DIR/railgui25.py"
python3 -m py_compile "$ROOT_DIR/bbb_runtime/backend_bridge.py"
python3 -m py_compile "$ROOT_DIR/bbb_runtime/launch_railgui25_backend.py"
python3 -m py_compile "$ROOT_DIR/tools/encoder_console_test.py"
make -C "$ROOT_DIR/sensor_board" >/dev/null
echo "ok"
echo

echo "[2/7] Preparing PRU encoder"
$SUDO bash "$ROOT_DIR/setup_encoder_pru.sh"
echo

echo "[3/7] Starting sensor backend"
$SUDO pkill -x sensor_service 2>/dev/null || true
    $SUDO bash -c "cd '$ROOT_DIR' && env RAIL_SCL_AXIS='$RAIL_SCL_AXIS' RAIL_ENCODER_PPR='$RAIL_ENCODER_PPR' RAIL_WHEEL_DIAMETER_MM='$RAIL_WHEEL_DIAMETER_MM' RAIL_ENCODER_INVERT='$RAIL_ENCODER_INVERT' RAIL_SAMPLING_DISTANCE_M='$RAIL_SAMPLING_DISTANCE_M' RAIL_TWIST_BASE_M='$RAIL_TWIST_BASE_M' RAIL_GAUGE_SOURCE='$RAIL_GAUGE_SOURCE' RAIL_GAUGE_OUTPUT_MODE='$RAIL_GAUGE_OUTPUT_MODE' RAIL_ADC_PATH='$RAIL_ADC_PATH' RAIL_ADC_MAX_RAW='$RAIL_ADC_MAX_RAW' RAIL_LASER_MIN_MM='$RAIL_LASER_MIN_MM' RAIL_LASER_MAX_MM='$RAIL_LASER_MAX_MM' RAIL_LASER_ZERO_MM='$RAIL_LASER_ZERO_MM' RAIL_LASER_ZERO_RAW='$RAIL_LASER_ZERO_RAW' RAIL_LASER_MPC='$RAIL_LASER_MPC' RAIL_LASER_AUTO_ZERO='$RAIL_LASER_AUTO_ZERO' RAIL_LASER_SIGN='$RAIL_LASER_SIGN' RAIL_GAUGE_FACTOR='$RAIL_GAUGE_FACTOR' ./sensor_board/sensor_service >'$SENSOR_LOG' 2>&1" &
SENSOR_PID=$!
sleep 2
if ! $SUDO kill -0 "$SENSOR_PID" 2>/dev/null; then
    echo "sensor_service failed to stay up"
    cat "$SENSOR_LOG"
    exit 1
fi
echo "sensor_service pid=$SENSOR_PID"
echo

echo "[4/7] Starting GPSD"
if command -v gpsd >/dev/null 2>&1 && [ -e "$RAIL_GPS_DEVICE" ]; then
    if command -v config-pin >/dev/null 2>&1; then
        $SUDO config-pin P9_11 uart >/dev/null || true
        $SUDO config-pin P9_13 uart >/dev/null || true
    fi
    $SUDO systemctl stop gpsd.socket 2>/dev/null || true
    $SUDO systemctl stop gpsd 2>/dev/null || true
    $SUDO gpsd -n "$RAIL_GPS_DEVICE" 2>/dev/null || true
    echo "gpsd device=$RAIL_GPS_DEVICE"
else
    echo "gpsd not available or $RAIL_GPS_DEVICE missing; GPS fields will stay 0 until gpsd is running"
fi
echo

if [ "$MODE" = "diag-only" ]; then
    echo "[5/7] Running stationary diagnostics"
    python3 "$ROOT_DIR/bbb_runtime/launch_railgui25_backend.py" --diag-only --diag-json "$DIAG_JSON"
    echo

    echo "[6/7] Recent backend log"
    tail -n 20 "$SENSOR_LOG" || true
    echo

    echo "[7/7] diag-only mode complete"
    exit 0
fi

echo "[5/7] Backend ready"
echo

echo "[6/7] Recent backend log"
tail -n 20 "$SENSOR_LOG" || true
echo

echo "[7/7] Launching railgui25 via shared-memory wrapper"
exec python3 "$ROOT_DIR/bbb_runtime/launch_railgui25_backend.py"
