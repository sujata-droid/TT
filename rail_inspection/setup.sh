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
#  4. Build PRU firmware -- Compile encoder_pru0.c with clpru
#  5. Load PRU firmware  -- Deploy to /lib/firmware and start PRU0
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
echo " BeagleBone Black | SCL3300 Inclinometer | PRU Encoder"
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

# Rotary encoder PRU input pins
# P9_27 = pr1_pru0_pru_r31_5 -> Encoder Channel A
# P9_42 = pr1_pru0_pru_r31_0 -> Encoder Channel B
$CONFIG_PIN P9_27 pruin     && ok "P9_27 -> pruin   (Encoder A)"     || warn "P9_27 config failed"
$CONFIG_PIN P9_42 pruin     && ok "P9_42 -> pruin   (Encoder B)"     || warn "P9_42 config failed"

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
echo "[4/7] Building PRU firmware..."
CLPRU=$(command -v clpru 2>/dev/null || echo "")
if [ -z "$CLPRU" ]; then
    warn "clpru not found. Trying /usr/bin/clpru..."
    CLPRU="/usr/bin/clpru"
fi

if [ -x "$CLPRU" ]; then
    PRU_INC_PATHS=""
    for d in /usr/share/ti/cgt-pru/include \
              /usr/lib/ti/pru-software-support-package/include \
              /usr/lib/ti/pru-software-support-package/include/am335x; do
        [ -d "$d" ] && PRU_INC_PATHS="$PRU_INC_PATHS -I$d"
    done

    cd pru
    $CLPRU $PRU_INC_PATHS -v3 -O2 --c99 -c encoder_pru0.c \
        --obj_directory=. && ok "PRU compile OK" || fail "PRU compile failed"

    $CLPRU -z encoder_pru0.obj am335x_pru0.cmd \
        -o encoder_pru0.out \
        --entry_point=main --warn_sections --diag_warning=225 \
        && ok "PRU link OK" || fail "PRU link failed"
    cd ..
else
    warn "clpru not available. PRU firmware NOT built."
    warn "Install: sudo apt-get install ti-pru-cgt-v2"
    warn "Encoder will show 0 until PRU firmware is loaded."
fi

# ── 5. Load PRU firmware ──────────────────────────────────────────────
echo ""
echo "[5/7] Loading PRU firmware..."
FW_SRC="pru/encoder_pru0.out"
FW_DST="/lib/firmware/am335x-pru0-fw"

if [ -f "$FW_SRC" ]; then
    cp "$FW_SRC" "$FW_DST"
    ok "Copied firmware to $FW_DST"

    # Find PRU0 remoteproc node
    PRU0_NODE=""
    for d in /sys/class/remoteproc/remoteproc*; do
        if [ -f "$d/name" ] && grep -q "4a334000.pru" "$d/name" 2>/dev/null; then
            PRU0_NODE=$(basename "$d")
            break
        fi
    done

    if [ -z "$PRU0_NODE" ]; then
        warn "PRU0 remoteproc node not found. Trying remoteproc0..."
        PRU0_NODE="remoteproc0"
    fi

    NODE="/sys/class/remoteproc/$PRU0_NODE"
    echo "stop"            | tee "$NODE/state" >/dev/null 2>&1 || true; sleep 1
    echo "am335x-pru0-fw"  | tee "$NODE/firmware" >/dev/null; sleep 1
    echo "start"           | tee "$NODE/state" >/dev/null
    sleep 1

    PRU_STATE=$(cat "$NODE/state" 2>/dev/null || echo "unknown")
    if [ "$PRU_STATE" = "running" ]; then
        ok "PRU0 is RUNNING (state=$PRU_STATE)"
    else
        warn "PRU0 state=$PRU_STATE (expected 'running')"
    fi
else
    warn "PRU firmware not found at $FW_SRC -- skipping load."
fi

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
echo "  PRU firmware:  $(ls -lh $FW_DST 2>/dev/null || echo 'NOT FOUND')"
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
echo "   Terminal 2:  python3 main_board/main.py"
echo ""
echo " OR run both with screen/tmux:"
echo "   sudo ./sensor_board/sensor_service &"
echo "   python3 main_board/main.py"
echo ""
echo " CLOUD URL (set before running main.py):"
echo "   export RAIL_CLOUD_URL=https://YOUR-APP.onrender.com/api/survey"
echo ""
echo "============================================================"
