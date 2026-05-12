#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
CSV_DIR="${1:-$HOME/surveys}"
URL="${RAIL_CLOUD_URL:-https://render-cloud-api.onrender.com/api/survey}"

if [[ "$URL" != */api/survey ]]; then
  URL="${URL%/}/api/survey"
fi

LATEST="$(ls -1t "$CSV_DIR"/*.csv 2>/dev/null | head -n 1 || true)"
if [ -z "$LATEST" ]; then
  echo "No CSV found in $CSV_DIR"
  exit 1
fi

echo "Uploading: $LATEST"
echo "Endpoint : $URL"

python3 - "$LATEST" "$URL" <<'PY'
import csv
import json
import os
import sys
import urllib.request

csv_path = sys.argv[1]
url = sys.argv[2]

with open(csv_path, newline="") as handle:
    rows = list(csv.DictReader(handle))

if not rows:
    raise SystemExit("CSV has no rows")

payload = json.dumps({
    "filename": os.path.basename(csv_path),
    "data": rows,
}).encode("utf-8")

req = urllib.request.Request(
    url,
    data=payload,
    method="POST",
    headers={"Content-Type": "application/json", "User-Agent": "RailInspection-BBB/1.0"},
)

with urllib.request.urlopen(req, timeout=30) as resp:
    body = resp.read().decode("utf-8", "replace")
    print(f"HTTP {resp.status}")
    print(body)
PY
