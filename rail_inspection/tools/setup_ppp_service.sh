#!/bin/bash
set -euo pipefail

cat <<'EOF' > /etc/systemd/system/ppp-airtel.service
[Unit]
Description=Airtel PPP Cellular Link on /dev/ttyS1
After=local-fs.target
Before=network.target openvpn.service openvpn-client@lwtmt.service
Wants=openvpn-client@lwtmt.service

[Service]
Type=forking
ExecStartPre=-/usr/bin/config-pin P9_24 uart
ExecStartPre=-/usr/bin/config-pin P9_26 uart
ExecStart=/usr/bin/pon airtel-ec200u
ExecStop=/usr/bin/poff airtel-ec200u
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ppp-airtel.service
systemctl enable openvpn-client@lwtmt.service
echo "ppp-airtel service installed and enabled."

