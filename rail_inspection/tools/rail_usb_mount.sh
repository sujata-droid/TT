#!/bin/sh
# Mount one approved USB data partition for RailGUI.  Invoked by systemd from
# the udev rule; do not run it against an arbitrary block device.
set -eu

action=${1:?mount or unmount is required}
device_name=${2:?block-device name is required}

case "$device_name" in
    sd[a-z][0-9]|sd[a-z][0-9][0-9]) ;;
    *) echo "Refusing unexpected block device: $device_name" >&2; exit 2 ;;
esac

device="/dev/$device_name"
mount_root=/media/rail-usb
mount_point="$mount_root/$device_name"

case "$action" in
    unmount)
        if mountpoint -q "$mount_point"; then
            sync
            umount "$mount_point" || umount -l "$mount_point"
        fi
        rmdir "$mount_point" 2>/dev/null || true
        exit 0
        ;;
    mount) ;;
    *) echo "Unknown action: $action" >&2; exit 2 ;;
esac

[ -b "$device" ] || { echo "Block device is not present: $device" >&2; exit 1; }

# Only a partition below a USB device may be mounted by this service.
sys_path=$(readlink -f "/sys/class/block/$device_name")
case "$sys_path" in
    *"/usb"*|*"/usb/"*) ;;
    *) echo "Refusing non-USB device: $device" >&2; exit 2 ;;
esac

fs_type=$(/sbin/blkid -o value -s TYPE "$device" 2>/dev/null || true)
case "$fs_type" in
    vfat|exfat) ;;
    *)
        echo "Unsupported USB filesystem '${fs_type:-unknown}' on $device. Format the drive as FAT32 or exFAT." >&2
        exit 3
        ;;
esac

install -d -m 755 "$mount_root"
install -d -m 755 "$mount_point"
if ! mountpoint -q "$mount_point"; then
    mount -t "$fs_type" -o rw,uid=debian,gid=debian,umask=022 "$device" "$mount_point"
fi

echo "Mounted $device at $mount_point"
