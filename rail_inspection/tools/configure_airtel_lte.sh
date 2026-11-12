#!/bin/sh
# Configure Airtel LTE connection on Quectel EC200U modem and OpenVPN tunnel.
#
# Hardware layout:
# - Quectel EC200U modem AT/PPP serial interface is connected to UART1 (P9_24=TXD, P9_26=RXD, /dev/ttyS1).
# - Cellular PPP dial uses /etc/ppp/peers/airtel-ec200u and /etc/chatscripts/airtel-ec200u.
# - OpenVPN connects via ppp0 to 13.232.116.88 and creates tap0 (20.8.1.38).
# - Cloud server is at http://20.8.1.15:5173/api/survey.
set -eu

APN="${APN:-airtelgprs.com}"

echo "Configuring UART1 pins for Quectel EC200U..."
if command -v config-pin >/dev/null 2>&1; then
    config-pin P9_24 uart 2>/dev/null || true
    config-pin P9_26 uart 2>/dev/null || true
fi

# Check if ppp0 is already up with an IPv4 address
if ! ip -4 addr show dev ppp0 2>/dev/null | grep -q 'inet '; then
    echo "Starting Airtel PPP link on /dev/ttyS1..."
    poff airtel-ec200u 2>/dev/null || true
    sleep 1
    pon airtel-ec200u
    # Wait up to 15 seconds for ppp0 to obtain an IP address
    for i in $(seq 1 15); do
        sleep 1
        if ip -4 addr show dev ppp0 2>/dev/null | grep -q 'inet '; then
            echo "PPP link established on ppp0."
            break
        fi
    done
fi

if ip -4 addr show dev ppp0 2>/dev/null | grep -q 'inet '; then
    echo "Cellular IP on ppp0: $(ip -4 addr show dev ppp0 | grep -oP 'inet \K[\d.]+')"
else
    echo "Warning: ppp0 has not obtained an IP yet."
fi

# Ensure OpenVPN client is active
if ! ip -4 addr show dev tap0 2>/dev/null | grep -q 'inet '; then
    echo "Starting OpenVPN tunnel to cloud..."
    systemctl restart openvpn-client@lwtmt.service 2>/dev/null || true
    for i in $(seq 1 12); do
        sleep 1
        if ip -4 addr show dev tap0 2>/dev/null | grep -q 'inet '; then
            echo "OpenVPN tap0 interface is up."
            break
        fi
    done
fi

if ip -4 addr show dev tap0 2>/dev/null | grep -q 'inet '; then
    echo "OpenVPN connected: tap0 IP $(ip -4 addr show dev tap0 | grep -oP 'inet \K[\d.]+')"
    echo "Route to cloud server 20.8.1.15 is active."
    exit 0
else
    echo "Warning: OpenVPN tap0 not established yet, continuing."
    exit 0
fi
