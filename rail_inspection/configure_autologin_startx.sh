#!/bin/bash
set -euo pipefail

TARGET_USER="${1:-debian}"
MODE="${2:-console}"
TARGET_HOME="$(eval echo "~$TARGET_USER")"
GETTY_DIR="/etc/systemd/system/getty@tty1.service.d"
GETTY_OVERRIDE="$GETTY_DIR/override.conf"
SUDOERS_FILE="/etc/sudoers.d/rail-startx"
PROFILE_FILE="$TARGET_HOME/.bash_profile"
PROFILE_BLOCK_START="# >>> rail autostartx >>>"
PROFILE_BLOCK_END="# <<< rail autostartx <<<"
APP_SCRIPT="$TARGET_HOME/trolley/start_gui_session.sh"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash configure_autologin_startx.sh [user]" >&2
    exit 1
fi

if ! id "$TARGET_USER" >/dev/null 2>&1; then
    echo "User '$TARGET_USER' does not exist" >&2
    exit 1
fi

if [ "$MODE" = "app" ] && [ ! -x "$APP_SCRIPT" ]; then
    echo "App launcher not found: $APP_SCRIPT" >&2
    exit 1
fi

mkdir -p "$GETTY_DIR"
cat >"$GETTY_OVERRIDE" <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $TARGET_USER --noclear %I \$TERM
Type=idle
EOF

cat >"$SUDOERS_FILE" <<EOF
$TARGET_USER ALL=(root) NOPASSWD: /usr/bin/startx
Defaults:$TARGET_USER !requiretty
EOF
chmod 0440 "$SUDOERS_FILE"

touch "$PROFILE_FILE"
STARTX_CMD="/usr/bin/startx"
if [ "$MODE" = "app" ]; then
    STARTX_CMD="/usr/bin/startx $APP_SCRIPT -- -nocursor"
fi

python3 - "$PROFILE_FILE" "$PROFILE_BLOCK_START" "$PROFILE_BLOCK_END" "$STARTX_CMD" <<'PY'
from pathlib import Path
import sys

profile = Path(sys.argv[1])
start = sys.argv[2]
end = sys.argv[3]
startx_cmd = sys.argv[4]
block = f"""{start}
if [ -z "${{DISPLAY:-}}" ] && [ "${{XDG_VTNR:-0}}" = "1" ] && [ -z "${{SSH_CONNECTION:-}}" ]; then
    exec sudo {startx_cmd}
fi
{end}
"""

text = profile.read_text() if profile.exists() else ""
if start in text and end in text:
    before, rest = text.split(start, 1)
    _, after = rest.split(end, 1)
    text = before.rstrip() + "\n\n" + block + after.lstrip("\n")
else:
    if text and not text.endswith("\n"):
        text += "\n"
    text += ("\n" if text else "") + block
profile.write_text(text)
PY

chown "$TARGET_USER:$TARGET_USER" "$PROFILE_FILE"

systemctl daemon-reload
systemctl enable getty@tty1.service >/dev/null

echo "Configured tty1 autologin for $TARGET_USER"
echo "Configured passwordless sudo startx for $TARGET_USER"
echo "Configured $PROFILE_FILE to exec startx on local tty1 login"
if [ "$MODE" = "app" ]; then
    echo "Configured app autorun via $APP_SCRIPT"
fi
echo "Reboot or run: sudo systemctl restart getty@tty1"
