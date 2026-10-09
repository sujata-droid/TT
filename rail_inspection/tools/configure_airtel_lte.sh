#!/bin/sh
# Configure the Quectel EC200U CDC-ECM data interface without touching the
# BBB's USB console network (usb0) or a wired maintenance connection.
#
# The installed SIM registers on Airtel and uses the "airtelgprs.com" APN.
# In ECM mode the modem firmware owns the APN configuration;
# this script requests an IP address only after the modem itself reports an
# active cellular data link.
set -eu

APN="${APN:-airtelgprs.com}"
IFACE="${RAIL_LTE_IFACE:-usb2}"
TEST_HOST="${RAIL_LTE_TEST_HOST:-1.1.1.1}"
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

if [ ! -d "/sys/class/net/$IFACE" ]; then
    echo "LTE interface $IFACE is not present. Check USB power/cable and modem boot."
    exit 1
fi

# EC200U exposes its AT ports through the generic option driver.  The modem
# can re-enumerate after power-up, so bind its USB ID before asking it to start
# the CDC-ECM data call.
if ! ls /dev/ttyUSB* >/dev/null 2>&1; then
    modprobe option 2>/dev/null || true
    # Do not use a shell writability test here: sysfs can report it false
    # immediately after boot even for root.  Writing the ID is idempotent.
    echo "2c7c 0901" > /sys/bus/usb-serial/drivers/option1/new_id 2>/dev/null || true
    sleep 3
fi

python3 "$ROOT_DIR/tools/quectel_ecm_connect.py" --apn "$APN"
ip link set "$IFACE" up
CARRIER="$(cat "/sys/class/net/$IFACE/carrier" 2>/dev/null || echo 0)"
if [ "$CARRIER" != "1" ]; then
    echo "Quectel ECM data call was accepted but the USB link is still down."
    echo "Check USB power/cable, antenna, SIM activation, and cellular coverage. APN requested: $APN"
    exit 2
fi

if command -v dhclient >/dev/null 2>&1; then
    dhclient -v "$IFACE"
elif command -v udhcpc >/dev/null 2>&1; then
    udhcpc -i "$IFACE" -n -q
else
    echo "No DHCP client installed; install dhclient or busybox/udhcpc."
    exit 3
fi

ip -4 addr show dev "$IFACE"
ip route show dev "$IFACE"

if ! ip -4 addr show dev "$IFACE" | grep -q 'inet '; then
    echo "LTE link is up, but DHCP did not assign an IPv4 address on $IFACE."
    exit 4
fi

if command -v ping >/dev/null 2>&1; then
    echo "Testing LTE internet access through $IFACE -> $TEST_HOST"
    if ! ping -I "$IFACE" -c 3 -W 3 "$TEST_HOST"; then
        echo "LTE received an address but cannot reach $TEST_HOST through $IFACE."
        exit 5
    fi
fi

echo "LTE ECM configuration and internet test completed on $IFACE."
echo "Requested APN: $APN (the ECM modem firmware manages the actual APN)."
