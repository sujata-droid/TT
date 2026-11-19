#!/bin/bash
# ============================================================
# setup.sh  --  Rail Inspection System: Full Setup Script
# ============================================================
# Run this ONCE on a fresh BeagleBone Black after boot.
# Must be run as root: sudo bash setup.sh
#
# What this script does (and WHY each step matters):
#
#  1. Install packages  -- PyQt5, build tools, PRU toolchain, GPSD
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

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GRN}[OK]${NC} $*"; }
warn() { echo -e "${YLW}[WARN]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

echo "============================================================"
echo " Rail Inspection System Setup"
echo " BeagleBone Black | SCL3300 Inclinometer | railgui25 wrapper runtime"
echo "============================================================"

# â”€â”€ Root check â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
[ "$(id -u)" -eq 0 ] || fail "Run as root: sudo bash setup.sh"

# â”€â”€ 1. Install packages â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo ""
echo "[1/7] Installing packages..."
apt-get update -qq
apt-get install -y -qq \
    build-essential \
    gcc \
    git \
    python3-pyqt5 \
    python3-pip \
    gpsd \
    gpsd-clients \
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

# â”€â”€ 2. Pin-mux configuration â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo ""
echo "[2/7] Configuring pins..."

# SCL3300 SPI1 pins (PCB J5: CSB, SDO, SDI, SCK)
# P9_28 = SPI1_CS0   -> CSB on SCL3300
# P9_29 = SPI1_D0    -> MISO/SDO on SCL3300
# P9_30 = SPI1_D1    -> MOSI/SDI on SCL3300
# P9_31 = SPI1_CLK   -> SCK on SCL3300
$CONFIG_PIN P9_28 spi_cs   && ok "P9_28 -> spi_cs  (SCL3300 CSB)"       || warn "P9_28 config failed"
$CONFIG_PIN P9_29 spi       && ok "P9_29 -> spi     (SCL3300 MISO/SDO)" || warn "P9_29 config failed"
$CONFIG_PIN P9_30 spi       && ok "P9_30 -> spi     (SCL3300 MOSI/SDI)" || warn "P9_30 config failed"
$CONFIG_PIN P9_31 spi_sclk  && ok "P9_31 -> spi_sclk (SCL3300 SCK)"    || warn "P9_31 config failed"

# Rotary Encoder GPIO pins (PCB J3: Pin 3 -> P8_15, Pin 4 -> P8_12)
($CONFIG_PIN P8_15 gpio || $CONFIG_PIN P8_15 default) && ok "P8_15 -> gpio (Encoder A / GPIO1_15)" || warn "P8_15 config failed"
($CONFIG_PIN P8_12 gpio || $CONFIG_PIN P8_12 default) && ok "P8_12 -> gpio (Encoder B / GPIO1_12)" || warn "P8_12 config failed"

# GNSS UART4 pins (PCB J15: Pin 5 -> P9_11, Pin 6 -> P9_13)
$CONFIG_PIN P9_11 uart       && ok "P9_11 -> uart       (GNSS RX / UART4_RXD)"  || warn "P9_11 config failed"
$CONFIG_PIN P9_13 uart       && ok "P9_13 -> uart       (GNSS TX / UART4_TXD)"  || warn "P9_13 config failed"

# LTE Modem UART1 pins (PCB J1: Pin 5 -> P9_26, Pin 6 -> P9_24)
$CONFIG_PIN P9_24 uart       && ok "P9_24 -> uart       (LTE TX / UART1_TXD)"   || warn "P9_24 config failed"
$CONFIG_PIN P9_26 uart       && ok "P9_26 -> uart       (LTE RX / UART1_RXD)"   || warn "P9_26 config failed"

# Verify spidev appeared
if [ -e /dev/spidev1.0 ]; then
    ok "/dev/spidev1.0 present"
else
    warn "/dev/spidev1.0 not found. Try: modprobe spidev"
    modprobe spidev 2>/dev/null || true
    sleep 1
    if [ -e /dev/spidev1.0 ]; then
        ok "/dev/spidev1.0 appeared after modprobe"
    else
        warn "/dev/spidev1.0 still missing. Check DTS overlay."
    fi
fi

# â”€â”€ 3. Build C sensor service â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo ""
echo "[3/7] Building sensor service..."
cd "$(dirname "$0")/sensor_board"
make clean -s 2>/dev/null || true
make 2>&1 | tail -5
[ -x sensor_service ] && ok "sensor_service built" || fail "sensor_service build failed"
cd ..

# â”€â”€ 4. Build PRU firmware â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo ""
echo "[4/7] Configuring PRU1 early module load and firmware..."
cat >/etc/modules-load.d/rail-pru.conf <<'EOF'
pruss_soc_bus
pru_rproc
EOF
modprobe pruss_soc_bus 2>/dev/null || true
modprobe pru_rproc 2>/dev/null || true
if [ -f "$PROJECT_DIR/setup_encoder_pru.sh" ] && { [ -s "$PROJECT_DIR/pru/encoder_pru1.out" ] || command -v clpru >/dev/null 2>&1; }; then
    bash "$PROJECT_DIR/setup_encoder_pru.sh" && ok "PRU1 firmware built and started" || warn "PRU1 setup failed (GPIO fallback remains active)"
else
    warn "clpru and prebuilt encoder_pru1.out not found; skipping PRU1 setup"
fi

# â”€â”€ 5. Load PRU firmware â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo ""
echo "[5/7] Skipping PRU firmware load..."
warn "Minimal encoder testing is using eQEP2, not the PRU path."

# â”€â”€ 6. Survey directory â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo ""
echo "[6/7] Creating survey directory..."
SURVEY_DIR="/home/debian/surveys"
mkdir -p "$SURVEY_DIR"
chown debian:debian "$SURVEY_DIR" 2>/dev/null || chown 1000:1000 "$SURVEY_DIR" || true
chmod 755 "$SURVEY_DIR"
ok "Survey dir: $SURVEY_DIR"

# USB export mount service.  The GUI export helper only writes to a mounted
# removable drive; this udev/systemd pair mounts a newly inserted FAT32/exFAT
# USB partition at /media/rail-usb/<device>, owned by the debian user.
echo "Installing automatic USB mount service..."
bash "$PROJECT_DIR/tools/install_usb_export.sh"
ok "Automatic USB mount service installed (FAT32/exFAT only)"

# â”€â”€ 7. Verify â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
echo ""
echo "[7/7] System verification..."

echo ""
echo "  SPI device:    $(ls -l /dev/spidev1.0 2>/dev/null || echo 'NOT FOUND')"
echo "  PRU firmware:  NOT REQUIRED for basic eQEP2 encoder test"
echo "  sensor_service:$(ls -lh sensor_board/sensor_service 2>/dev/null || echo 'NOT BUILT')"
echo "  Survey dir:    $(ls -ld $SURVEY_DIR 2>/dev/null || echo 'NOT FOUND')"
echo "  USB mount dir: $(ls -ld /media/rail-usb 2>/dev/null || echo 'NOT FOUND')"
echo "  GPSD:          $(command -v gpsd 2>/dev/null || echo 'NOT INSTALLED')"

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
echo "   export RAIL_CLOUD_URL=https://lwtmt-cloud-backend.onrender.com/api/survey"
echo ""
echo "============================================================"
