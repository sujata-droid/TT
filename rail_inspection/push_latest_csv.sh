#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
CSV_DIR="${1:-$HOME/surveys}"
URL="${RAIL_CLOUD_URL:-https://lwtmt-cloud-backend.onrender.com/api/survey}"

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

csv_path = sys.argv[1]
url = sys.argv[2]

with open(csv_path, newline="") as handle:
    rows = list(csv.DictReader(handle))

if not rows:
    raise SystemExit("CSV has no rows")

station_no = ""
for row in rows:
    station_no = (
        row.get("Station Code")
        or row.get("Station No")
        or row.get("station_no")
        or row.get("stationCode")
        or row.get("station")
        or ""
    ).strip()
    if station_no:
        break

if station_no:
    for row in rows:
        row.setdefault("Station No", station_no)
        row.setdefault("station_no", station_no)
        row.setdefault("stationCode", station_no)

payload = json.dumps({
    "filename": os.path.basename(csv_path),
    "station_no": station_no,
    "stationCode": station_no,
    "station_code": station_no,
    "station": station_no,
    "data": rows,
}).encode("utf-8")

payload_path = "/tmp/rail_latest_upload.json"
with open(payload_path, "wb") as handle:
    handle.write(payload)
print(payload_path)
PY

PAYLOAD_PATH="/tmp/rail_latest_upload.json"
curl --fail --show-error --max-time 300 \
  -H Content-Type:application/json \
  --data-binary "@$PAYLOAD_PATH" \
  "$URL"
echo
