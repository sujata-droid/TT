#!/bin/sh
# Configure a USB LTE modem/SIM connection for Airtel India on Debian/BBB.
# Run on the BeagleBone: sudo sh tools/configure_airtel_lte.sh

set -eu

CON_NAME="${CON_NAME:-airtel-lte}"
APN="${APN:-airtelgprs.com}"

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "Missing $1. Install modemmanager and network-manager first." >&2
        exit 1
    }
}

[ "$(id -u)" -eq 0 ] || {
    echo "Run as root: sudo sh tools/configure_airtel_lte.sh" >&2
    exit 1
}

need_cmd nmcli
need_cmd mmcli

systemctl enable --now ModemManager >/dev/null 2>&1 || true
systemctl enable --now NetworkManager >/dev/null 2>&1 || true
sleep 3

if lsusb 2>/dev/null | grep -qi "2c7c:0901"; then
    modprobe option 2>/dev/null || true
    modprobe qmi_wwan 2>/dev/null || true
    modprobe cdc_mbim 2>/dev/null || true
    echo 2c7c 0901 >/sys/bus/usb-serial/drivers/option1/new_id 2>/dev/null || true
    sleep 3
fi

MODEM_ID="$(mmcli -L 2>/dev/null | sed -n 's#.*Modem/\([0-9][0-9]*\).*#\1#p' | head -n 1)"
start_ppp_fallback() {
    if [ ! -e /dev/ttyUSB5 ]; then
        echo "PPP fallback unavailable: /dev/ttyUSB5 is missing." >&2
        return 1
    fi

    echo "Configuring Quectel EC200U PPP on /dev/ttyUSB5."
    command -v pppd >/dev/null 2>&1 || {
        echo "Missing pppd. Install ppp before running PPP fallback." >&2
        return 1
    }
    command -v chat >/dev/null 2>&1 || {
        echo "Missing chat. Install ppp before running PPP fallback." >&2
        return 1
    }

    mkdir -p /etc/chatscripts /etc/ppp/peers
    cat >/etc/chatscripts/airtel-ec200u <<EOF
ABORT "BUSY"
ABORT "NO CARRIER"
ABORT "NO DIALTONE"
ABORT "ERROR"
ABORT "NO ANSWER"
TIMEOUT 10
"" AT
OK ATE0
OK AT+CPIN?
OK AT+CGDCONT=1,"IP","$APN"
OK ATD*99#
CONNECT ""
EOF

    cat >/etc/ppp/peers/airtel-ec200u <<EOF
/dev/ttyUSB5
115200
connect "/usr/sbin/chat -v -f /etc/chatscripts/airtel-ec200u"
noauth
defaultroute
replacedefaultroute
usepeerdns
persist
holdoff 10
maxfail 0
crtscts
modem
lock
hide-password
lcp-echo-interval 20
lcp-echo-failure 3
ipcp-accept-local
ipcp-accept-remote
EOF

    chmod 600 /etc/chatscripts/airtel-ec200u /etc/ppp/peers/airtel-ec200u
    pkill -f "pppd call airtel-ec200u" 2>/dev/null || true
    poff airtel-ec200u 2>/dev/null || true
    pon airtel-ec200u || pppd call airtel-ec200u
    sleep 15

    if ip addr show ppp0 >/dev/null 2>&1; then
        [ -f /etc/ppp/resolv.conf ] && cp /etc/ppp/resolv.conf /etc/resolv.conf
        grep -q "8.8.8.8" /etc/resolv.conf 2>/dev/null || echo "nameserver 8.8.8.8" >>/etc/resolv.conf
        echo "PPP LTE is up:"
        ip addr show ppp0
        ip route
        return 0
    fi

    echo "PPP did not create ppp0. Check /var/log/syslog for pppd/chat output." >&2
    return 1
}

if [ -n "$MODEM_ID" ]; then

    echo "Detected modem $MODEM_ID"
    mmcli -m "$MODEM_ID" --enable >/dev/null 2>&1 || true

    if nmcli -t -f NAME connection show | grep -qx "$CON_NAME"; then
        nmcli connection modify "$CON_NAME" \
            gsm.apn "$APN" \
            connection.autoconnect yes \
            ipv4.method auto \
            ipv6.method ignore
    else
        nmcli connection add type gsm ifname "*" con-name "$CON_NAME" apn "$APN" \
            connection.autoconnect yes \
            ipv4.method auto \
            ipv6.method ignore
    fi

    nmcli radio wwan on || true
    if ! nmcli connection up "$CON_NAME"; then
        echo "NetworkManager GSM activation failed; falling back to PPP." >&2
        start_ppp_fallback
        exit $?
    fi

    echo "LTE status:"
    nmcli -f GENERAL.STATE,GENERAL.DEVICE,IP4.ADDRESS connection show "$CON_NAME" || true
    mmcli -m "$MODEM_ID" --signal-get >/dev/null 2>&1 && mmcli -m "$MODEM_ID" --signal-get || true
    exit 0
fi

echo "No ModemManager modem found; falling back to PPP."
start_ppp_fallback
