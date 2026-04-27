#!/bin/bash
set -euo pipefail

ROOT_DIR="/home/debian/trolley"
cd "$ROOT_DIR"

export PYTHONDONTWRITEBYTECODE=1
exec bash "$ROOT_DIR/run_railgui25.sh"
