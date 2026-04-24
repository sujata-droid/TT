#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "$ROOT_DIR/bbb_runtime/run_railgui25_backend.sh"
