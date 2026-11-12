#!/bin/bash
# Convenient launcher for the live Track Gauge console monitor and calibration
cd "$(dirname "$0")" || exit 1
python3 tools/track_gauge_console.py "$@"
