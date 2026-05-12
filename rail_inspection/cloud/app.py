import csv
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Mapping

from flask import Flask, Response, abort, jsonify, redirect, render_template_string, request, url_for


app = Flask(__name__)

STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", str(Path(tempfile.gettempdir()) / "rail_surveys")))
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

CSV_FIELDS = [
    "Sample No",
    "Date & Time",
    "Reference Type",
    "Reference Point",
    "Station Code",
    "Chainage",
    "Loop/Line Siding",
    "Turn-out No",
    "Curve No",
    "Level Crossing No",
    "Hectometer Post",
    "Name",
    "Designation",
    "Lattitude",
    "Longitude",
    "Distance",
    "Gauge",
    "Crossover",
    "Absolute Tilt",
    "Cumulative Tilt",
]


BASE_CSS = """
<style>
  :root {
    --bg: #090b18;
    --panel: #171a2d;
    --panel-2: #1f2544;
    --line: #323854;
    --text: #eef4ff;
    --muted: #92a1c6;
    --accent: #52a3ff;
    --good: #27e79a;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: Consolas, "Courier New", monospace;
    font-size: 16px;
  }
  main { padding: 24px; }
  h1 {
    margin: 0 0 22px;
    color: var(--accent);
    font-size: 34px;
    letter-spacing: 1px;
  }
  h2 { color: #8dff9a; font-size: 18px; margin-top: 26px; }
  .status, .card {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 14px 16px;
    margin-bottom: 22px;
  }
  .status b { color: var(--good); }
  a { color: var(--accent); text-decoration: none; margin-right: 16px; }
  a:hover { text-decoration: underline; }
  .table-wrap {
    width: 100%;
    overflow: auto;
    border: 1px solid var(--line);
    border-radius: 8px;
    background: #0d1020;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    white-space: nowrap;
  }
  th {
    position: sticky;
    top: 0;
    background: var(--panel-2);
    color: #8fc7ff;
    text-align: left;
    padding: 10px;
    border-bottom: 1px solid var(--line);
  }
  td {
    padding: 8px 10px;
    border-bottom: 1px solid #242941;
    color: var(--text);
  }
  tr:nth-child(even) td { background: #0f1326; }
  .muted { color: var(--muted); font-style: italic; }
  .filename { color: #ffffff; }
  .num { text-align: right; }
  .toolbar {
    display: flex;
    gap: 12px;
    align-items: center;
    flex-wrap: wrap;
    margin-bottom: 16px;
  }
  .pill {
    color: var(--good);
    background: #10251e;
    border: 1px solid #1d6b4c;
    padding: 4px 8px;
    border-radius: 999px;
  }
</style>
"""


def _safe_filename(name: str) -> str:
    clean = Path(name or "").name.strip()
    if not clean:
        clean = f"survey_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.csv"
    if not clean.lower().endswith(".csv"):
        clean += ".csv"
    return clean


def _csv_path(filename: str) -> Path:
    path = STORAGE_DIR / _safe_filename(filename)
    if path.parent != STORAGE_DIR:
        abort(400, "invalid filename")
    return path


def _ordered_fields(rows: List[Mapping[str, object]]) -> List[str]:
    fields = [field for field in CSV_FIELDS if any(field in row for row in rows)]
    extras = []
    for row in rows:
        for key in row.keys():
            if key not in fields and key not in extras:
                extras.append(key)
    return fields + extras


def _write_csv(path: Path, rows: List[Mapping[str, object]]) -> None:
    fields = _ordered_fields(rows)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> List[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _csv_files() -> Iterable[Path]:
    return sorted(STORAGE_DIR.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)


@app.get("/")
def index():
    files = []
    for path in _csv_files():
        rows = _read_csv(path)
        files.append(
            {
                "name": path.name,
                "received": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "rows": len(rows),
                "size_kb": f"{path.stat().st_size / 1024:.1f}",
            }
        )
    return render_template_string(
        BASE_CSS
        + """
        <main>
          <h1>Rail Inspection Cloud Dashboard</h1>
          <div class="status">
            Status: <b>ACTIVE</b> | Storage: <b>{{ storage }}</b> | Files: <b>{{ files|length }}</b>
          </div>
          <h2>Stored Surveys</h2>
          {% if files %}
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Filename</th>
                  <th>Received</th>
                  <th class="num">Rows</th>
                  <th class="num">Size</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {% for file in files %}
                <tr>
                  <td class="filename">{{ file.name }}</td>
                  <td>{{ file.received }}</td>
                  <td class="num">{{ file.rows }}</td>
                  <td class="num">{{ file.size_kb }} kB</td>
                  <td>
                    <a href="{{ url_for('view_survey', filename=file.name) }}">View</a>
                    <a href="{{ url_for('download_csv', filename=file.name) }}">CSV</a>
                  </td>
                </tr>
                {% endfor %}
              </tbody>
            </table>
          </div>
          {% else %}
          <p class="muted">No surveys yet. Run the BeagleBone and complete a survey.</p>
          {% endif %}
        </main>
        """,
        files=files,
        storage=str(STORAGE_DIR),
    )


@app.get("/survey/<path:filename>")
def view_survey(filename: str):
    path = _csv_path(filename)
    if not path.exists():
        abort(404)
    rows = _read_csv(path)
    fields = _ordered_fields(rows) if rows else CSV_FIELDS
    return render_template_string(
        BASE_CSS
        + """
        <main>
          <h1>Survey Table</h1>
          <div class="toolbar">
            <a href="{{ url_for('index') }}">Back</a>
            <a href="{{ url_for('download_csv', filename=path.name) }}">Download CSV</a>
            <span class="pill">{{ rows|length }} rows</span>
            <span class="filename">{{ path.name }}</span>
          </div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  {% for field in fields %}
                  <th>{{ field }}</th>
                  {% endfor %}
                </tr>
              </thead>
              <tbody>
                {% for row in rows %}
                <tr>
                  {% for field in fields %}
                  <td>{{ row.get(field, "") }}</td>
                  {% endfor %}
                </tr>
                {% endfor %}
              </tbody>
            </table>
          </div>
        </main>
        """,
        path=path,
        rows=rows,
        fields=fields,
    )


@app.get("/csv/<path:filename>")
def download_csv(filename: str):
    path = _csv_path(filename)
    if not path.exists():
        abort(404)
    return Response(
        path.read_text(encoding="utf-8"),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={path.name}"},
    )


@app.post("/api/survey")
def api_survey():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        abort(400, "expected JSON object")
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows:
        abort(400, "expected non-empty data list")
    if not all(isinstance(row, dict) for row in rows):
        abort(400, "each data item must be an object")

    filename = _safe_filename(str(payload.get("filename", "")))
    path = _csv_path(filename)
    _write_csv(path, rows)
    return jsonify({"ok": True, "filename": path.name, "rows": len(rows), "view": url_for("view_survey", filename=path.name)})


@app.get("/health")
def health():
    return jsonify({"ok": True, "storage": str(STORAGE_DIR)})
