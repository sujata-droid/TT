#!/bin/sh
# One-time installer for the Rail Inspection USB export feature.
# Run on the BeagleBone: sudo /home/debian/trolley/tools/install_usb_export.sh
set -eu

[ "$(id -u)" -eq 0 ] || {
    echo "Run as root: sudo $0" >&2
    exit 1
}

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

# Debian 10 uses exfat-fuse/exfat-utils.  Debian 11+ uses exfatprogs.
if ! command -v mount.exfat-fuse >/dev/null 2>&1 && \
   ! command -v mount.exfat >/dev/null 2>&1; then
    apt-get update -qq
    apt-get install -y -qq exfat-fuse exfat-utils 2>/dev/null || \
        apt-get install -y -qq exfatprogs
fi

install -d -m 755 /media/rail-usb
install -m 755 "$project_dir/tools/rail_usb_mount.sh" /usr/local/sbin/rail-usb-mount
install -m 644 "$project_dir/tools/rail-usb-mount@.service" /etc/systemd/system/rail-usb-mount@.service
install -m 644 "$project_dir/tools/99-rail-usb-mount.rules" /etc/udev/rules.d/99-rail-usb-mount.rules

systemctl daemon-reload
udevadm control --reload-rules
udevadm trigger --subsystem-match=block --action=add

echo "USB export setup complete. Insert a FAT32/exFAT drive and wait 3 seconds."
