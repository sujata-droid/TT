#!/bin/bash
# ============================================================
# setup.sh  --  Rail Inspection System: Full Setup Script
# ============================================================
# Run this ONCE on a fresh BeagleBone Black after boot.
# Must be run as root: sudo bash setup.sh
#
# What this script does (and WHY each step matters):
#
#  1. Install packages  -- PyQt5, build tools, PRU toolchain
#  2. Pin-mux config    -- Tell the BBB which physical function
#                         each pin serves. Without this, SPI pins
#                         behave as GPIO and the SCL3300 gets zero
#                         SPI transactions.
#  3. Build C service   -- Compile sensor_service (acquisition daemon)
#  4. Skip PRU firmware -- Minimal encoder test uses eQEP2 instead
#  5. Skip PRU load     -- PRU path is not required for bare-minimum test
#  6. Create survey dir  -- /home/debian/surveys with correct permissions
#  7. Verify             -- Print status of all components
#
# ============================================================

set -e  # Exit on any error

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GRN}[OK]${NC} $*"; }
warn() { echo -e "${YLW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

echo "============================================================"
echo " Rail Inspection System Setup"
echo " BeagleBone Black | SCL3300 Inclinometer | railgui25 wrapper runtime"
echo "============================================================"

# ── Root check ───────────────────────────────────────────────────────
[ "$(id -u)" -eq 0 ] || fail "Run as root: sudo bash setup.sh"

# ── 1. Install packages ──────────────────────────────────────────────
echo ""
echo "[1/7] Installing packages..."
apt-get update -qq
apt-get install -y -qq \
    build-essential \
    gcc \
    git \
    python3-pyqt5 \
    python3-pip \
    pru-software-support-package \
    ti-pru-cgt-v2 \
    beaglebone-universal-io \
    linux-headers-$(uname -r) \
    spi-tools \
    2>/dev/null || warn "Some packages may not have installed (non-fatal)"
ok "Packages installed"

# Locate config-pin
CONFIG_PIN=$(command -v config-pin 2>/dev/null || echo "")
[ -z "$CONFIG_PIN" ] && CONFIG_PIN="/usr/bin/config-pin"
[ -x "$CONFIG_PIN" ] || fail "config-pin not found. Install beaglebone-universal-io"

# ── 2. Pin-mux configuration ─────────────────────────────────────────
echo ""
echo "[2/7] Configuring pins..."

# SCL3300 SPI0 pins
# P9_17 = SPI0_CS0   -> CSB on SCL3300
# P9_18 = SPI0_D1    -> MOSI on SCL3300
# P9_21 = SPI0_D0    -> MISO on SCL3300
# P9_22 = SPI0_CLK   -> SCK on SCL3300
$CONFIG_PIN P9_17 spi_cs   && ok "P9_17 -> spi_cs  (SCL3300 CSB)"   || warn "P9_17 config failed"
$CONFIG_PIN P9_18 spi       && ok "P9_18 -> spi     (SCL3300 MOSI)"  || warn "P9_18 config failed"
$CONFIG_PIN P9_21 spi       && ok "P9_21 -> spi     (SCL3300 MISO)"  || warn "P9_21 config failed"
$CONFIG_PIN P9_22 spi_sclk  && ok "P9_22 -> spi_sclk (SCL3300 SCK)" || warn "P9_22 config failed"

$CONFIG_PIN P8_11 qep      && ok "P8_11 -> qep     (Encoder B / eQEP2B)" || warn "P8_11 config failed"
$CONFIG_PIN P8_12 qep      && ok "P8_12 -> qep     (Encoder A / eQEP2A)" || warn "P8_12 config failed"

# Verify spidev appeared
if [ -e /dev/spidev0.0 ]; then
    ok "/dev/spidev0.0 present"
else
    warn "/dev/spidev0.0 not found. Try: modprobe spidev"
    modprobe spidev 2>/dev/null || true
    sleep 1
    [ -e /dev/spidev0.0 ] && ok "/dev/spidev0.0 appeared after modprobe" \
                           || warn "/dev/spidev0.0 still missing. Check DTS overlay."
fi

# ── 3. Build C sensor service ─────────────────────────────────────────
echo ""
echo "[3/7] Building sensor service..."
cd "$(dirname "$0")/sensor_board"
make clean -s 2>/dev/null || true
make 2>&1 | tail -5
[ -x sensor_service ] && ok "sensor_service built" || fail "sensor_service build failed"
cd ..

# ── 4. Build PRU firmware ─────────────────────────────────────────────
echo ""
echo "[4/7] Skipping PRU firmware build..."
warn "Minimal encoder testing is using eQEP2, not the PRU path."

# ── 5. Load PRU firmware ──────────────────────────────────────────────
echo ""
echo "[5/7] Skipping PRU firmware load..."
warn "Minimal encoder testing is using eQEP2, not the PRU path."

# ── 6. Survey directory ───────────────────────────────────────────────
echo ""
echo "[6/7] Creating survey directory..."
SURVEY_DIR="/home/debian/surveys"
mkdir -p "$SURVEY_DIR"
chown debian:debian "$SURVEY_DIR" 2>/dev/null || chown 1000:1000 "$SURVEY_DIR" || true
chmod 755 "$SURVEY_DIR"
ok "Survey dir: $SURVEY_DIR"

# ── 7. Verify ─────────────────────────────────────────────────────────
echo ""
echo "[7/7] System verification..."

echo ""
echo "  SPI device:    $(ls -l /dev/spidev0.0 2>/dev/null || echo 'NOT FOUND')"
echo "  PRU firmware:  NOT REQUIRED for basic eQEP2 encoder test"
echo "  sensor_service:$(ls -lh sensor_board/sensor_service 2>/dev/null || echo 'NOT BUILT')"
echo "  Survey dir:    $(ls -ld $SURVEY_DIR 2>/dev/null || echo 'NOT FOUND')"

# Cloud URL check
CLOUD_URL="${RAIL_CLOUD_URL:-NOT SET}"
echo "  Cloud URL:     $CLOUD_URL"

echo ""
echo "============================================================"
echo " SETUP COMPLETE"
echo "============================================================"
echo ""
echo " TO START (two terminals or use & to background):"
echo ""
echo "   Terminal 1:  sudo ./sensor_board/sensor_service"
echo "   Terminal 2:  bash run_railgui25.sh"
echo ""
echo " OR run both with screen/tmux:"
echo "   sudo ./sensor_board/sensor_service &"
echo "   bash run_railgui25.sh"
echo ""
echo " SENSOR STATUS CHECK:"
echo "   python3 sensor_status.py"
echo ""
echo " CLOUD URL (default root is already baked into the wrapper):"
echo "   export RAIL_CLOUD_URL=https://thread-qm2o.onrender.com"
echo ""
echo "============================================================"
