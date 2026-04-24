#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== SCL3300 SPI Debug ==="
echo "Project root: $ROOT_DIR"
echo

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo: sudo tools/scl3300_debug.sh"
    exit 1
fi

echo "[1/5] Forcing SPI pinmux"
config-pin P9_17 spi_cs
config-pin P9_18 spi
config-pin P9_21 spi
config-pin P9_22 spi_sclk
config-pin -q P9_17
config-pin -q P9_18
config-pin -q P9_21
config-pin -q P9_22
echo

echo "[2/5] Checking spidev"
ls -l /dev/spidev0.0
echo

echo "[3/5] Building sensor_service"
make -C "$ROOT_DIR/sensor_board"
echo

echo "[4/5] Running live sensor probe"
LOG_FILE="/tmp/scl3300_debug.log"
rm -f "$LOG_FILE"
(
    cd "$ROOT_DIR"
    export SCL3300_DEBUG=1
    timeout 4s ./sensor_board/sensor_service
) >"$LOG_FILE" 2>&1 || true
cat "$LOG_FILE"
echo

echo "[5/5] Quick diagnosis"
if grep -q "Healthy=YES" "$LOG_FILE"; then
    echo "Result: SPI link is working."
elif grep -q "Cannot open /dev/spidev0.0" "$LOG_FILE"; then
    echo "Result: SPI device is missing or permissions are wrong."
elif grep -q "RS = 0x00" "$LOG_FILE"; then
    echo "Result: sensor stayed in startup. Check 3.3V, GND, CSB, and pinmux."
elif grep -q "consecutive CRC errors" "$LOG_FILE"; then
    echo "Result: noisy or incorrect SPI wiring. Check MISO/MOSI/SCK/CS and grounding."
else
    echo "Result: inconclusive. Review the log above."
fi
