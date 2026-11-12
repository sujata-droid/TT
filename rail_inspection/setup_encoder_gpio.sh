#!/bin/bash
set -euo pipefail

# Configure BeagleBone Black pins for the PCB Rotary Encoder wiring:
# Header J3:
#   Pin 1 -> +5V
#   Pin 2 -> GND
#   Pin 3 -> P8_15 (Channel A / GPIO1_15)
#   Pin 4 -> P8_12 (Channel B / GPIO1_12)

CONFIG_PIN="$(command -v config-pin 2>/dev/null || echo /usr/bin/config-pin)"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root: sudo bash setup_encoder_gpio.sh" >&2
    exit 1
fi

if [ ! -x "$CONFIG_PIN" ]; then
    echo "config-pin not found" >&2
    exit 1
fi

echo "[ENC] Configuring BBB pins for PCB rotary encoder"
("$CONFIG_PIN" P8_15 gpio 2>/dev/null || "$CONFIG_PIN" P8_15 default 2>/dev/null || true)
("$CONFIG_PIN" P8_12 gpio 2>/dev/null || "$CONFIG_PIN" P8_12 default 2>/dev/null || true)
"$CONFIG_PIN" -q P8_15 || true
"$CONFIG_PIN" -q P8_12 || true

echo "[ENC] PCB Encoder GPIO configured: P8_15=A (GPIO1_15), P8_12=B (GPIO1_12)"
