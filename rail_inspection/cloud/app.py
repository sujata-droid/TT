import csv
import os
import tempfile
import threading
from io import BytesIO
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Mapping

from flask import Flask, Response, abort, jsonify, redirect, render_template_string, request, url_for
import psycopg2
from psycopg2.extras import RealDictCursor


app = Flask(__name__)

STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", str(Path(tempfile.gettempdir()) / "rail_surveys")))
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

CSV_FIELDS = [
    "Sample No",
    "Date & Time",
    "Name",
    "Designation",
    "Station No",
    "Station Code",
    "Chainage",
    "Loop/Line Siding",
    "Turn-out No",
    "Curve No",
    "Level Crossing No",
    "Hectometer Post",
    "Bridge (Start)",
    "Bridge (End)",
    "Level Crossing (LC) In",
    "Level Crossing (LC) Out",
    "Kilometer Post (KM)",
    "Points & Crossing (P&C)",
    "Curve-In",
    "Curve Out",
    "OHE Mast (OHEM) Location",
    "Switch Expansion Joint (SEJ)",
    "Latitude",
    "Longitude",
    "Distance",
    "Gauge",
    "Crosslevel",
    "Twist",
]

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
_schema_lock = threading.Lock()
_schema_ready = False
DB_RECORD_FIELDS = [
    ("sample_no", "Sample No"),
    ("recorded_at", "Date & Time"),
    ("name", "Name"),
    ("designation", "Designation"),
    ("station_no", "Station No"),
    ("station_code", "Station Code"),
    ("chainage", "Chainage"),
    ("loop_line_siding", "Loop/Line Siding"),
    ("turnout_no", "Turn-out No"),
    ("curve_no", "Curve No"),
    ("level_crossing_no", "Level Crossing No"),
    ("hectometer_post", "Hectometer Post"),
    ("bridge_start", "Bridge (Start)"),
    ("bridge_end", "Bridge (End)"),
    ("level_crossing_lc_in", "Level Crossing (LC) In"),
    ("level_crossing_lc_out", "Level Crossing (LC) Out"),
    ("kilometer_post", "Kilometer Post (KM)"),
    ("points_crossing", "Points & Crossing (P&C)"),
    ("curve_in", "Curve-In"),
    ("curve_out", "Curve Out"),
    ("ohe_mast_location", "OHE Mast (OHEM) Location"),
    ("switch_expansion_joint", "Switch Expansion Joint (SEJ)"),
    ("latitude", "Latitude"),
    ("longitude", "Longitude"),
    ("distance", "Distance"),
    ("gauge", "Gauge"),
    ("crosslevel", "Crosslevel"),
    ("twist", "Twist"),
]


def _db_connect():
    if not DATABASE_URL:
        return None
    return psycopg2.connect(DATABASE_URL, sslmode="require", connect_timeout=10)


def _ensure_db_schema():
    global _schema_ready
    if not DATABASE_URL or _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        with _db_connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS surveys (
                        id SERIAL PRIMARY KEY,
                        filename VARCHAR(255) NOT NULL,
                        station_code VARCHAR(64),
                        surveyor_name VARCHAR(128),
                        designation VARCHAR(128),
                        row_count INTEGER NOT NULL DEFAULT 0,
                        uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    );
                    CREATE TABLE IF NOT EXISTS survey_records (
                        id SERIAL PRIMARY KEY,
                        survey_id INTEGER NOT NULL REFERENCES surveys(id) ON DELETE CASCADE,
                        sample_no INTEGER,
                        recorded_at TIMESTAMPTZ,
                        name VARCHAR(128),
                        designation VARCHAR(128),
                        station_no VARCHAR(64),
                        station_code VARCHAR(64),
                        chainage DOUBLE PRECISION,
                        loop_line_siding VARCHAR(64),
                        turnout_no VARCHAR(64),
                        curve_no VARCHAR(64),
                        level_crossing_no VARCHAR(64),
                        hectometer_post VARCHAR(64),
                        bridge_start VARCHAR(128),
                        bridge_end VARCHAR(128),
                        level_crossing_lc_in VARCHAR(128),
                        level_crossing_lc_out VARCHAR(128),
                        kilometer_post VARCHAR(128),
                        points_crossing VARCHAR(128),
                        curve_in VARCHAR(128),
                        curve_out VARCHAR(128),
                        ohe_mast_location VARCHAR(128),
                        switch_expansion_joint VARCHAR(128),
                        latitude DOUBLE PRECISION,
                        longitude DOUBLE PRECISION,
                        distance DOUBLE PRECISION,
                        gauge DOUBLE PRECISION,
                        crosslevel DOUBLE PRECISION,
                        twist DOUBLE PRECISION
                    );
                    ALTER TABLE survey_records
                        ADD COLUMN IF NOT EXISTS sample_no INTEGER,
                        ADD COLUMN IF NOT EXISTS recorded_at TIMESTAMPTZ,
                        ADD COLUMN IF NOT EXISTS name VARCHAR(128),
                        ADD COLUMN IF NOT EXISTS designation VARCHAR(128),
                        ADD COLUMN IF NOT EXISTS station_no VARCHAR(64),
                        ADD COLUMN IF NOT EXISTS station_code VARCHAR(64),
                        ADD COLUMN IF NOT EXISTS chainage DOUBLE PRECISION,
                        ADD COLUMN IF NOT EXISTS loop_line_siding VARCHAR(64),
                        ADD COLUMN IF NOT EXISTS turnout_no VARCHAR(64),
                        ADD COLUMN IF NOT EXISTS curve_no VARCHAR(64),
                        ADD COLUMN IF NOT EXISTS level_crossing_no VARCHAR(64),
                        ADD COLUMN IF NOT EXISTS hectometer_post VARCHAR(64),
                        ADD COLUMN IF NOT EXISTS bridge_start VARCHAR(128),
                        ADD COLUMN IF NOT EXISTS bridge_end VARCHAR(128),
                        ADD COLUMN IF NOT EXISTS level_crossing_lc_in VARCHAR(128),
                        ADD COLUMN IF NOT EXISTS level_crossing_lc_out VARCHAR(128),
                        ADD COLUMN IF NOT EXISTS kilometer_post VARCHAR(128),
                        ADD COLUMN IF NOT EXISTS points_crossing VARCHAR(128),
                        ADD COLUMN IF NOT EXISTS curve_in VARCHAR(128),
                        ADD COLUMN IF NOT EXISTS curve_out VARCHAR(128),
                        ADD COLUMN IF NOT EXISTS ohe_mast_location VARCHAR(128),
                        ADD COLUMN IF NOT EXISTS switch_expansion_joint VARCHAR(128),
                        ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION,
                        ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION,
                        ADD COLUMN IF NOT EXISTS distance DOUBLE PRECISION,
                        ADD COLUMN IF NOT EXISTS gauge DOUBLE PRECISION,
                        ADD COLUMN IF NOT EXISTS crosslevel DOUBLE PRECISION,
                        ADD COLUMN IF NOT EXISTS twist DOUBLE PRECISION;
                """)
        _schema_ready = True


def _row_value(row, *names):
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return str(value).strip()
    return None


def _number(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _recorded_at(value):
    if not value:
        return None
    for fmt in ("%d-%m-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except ValueError:
            pass
    return value


def _store_rows_in_db(filename, rows):
    _ensure_db_schema()
    first = rows[0]
    station = _row_value(first, "Station No", "station_no", "Station Code", "station")
    with _db_connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO surveys (filename, station_code, surveyor_name, designation, row_count) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (filename, station, _row_value(first, "Name"), _row_value(first, "Designation"), len(rows)),
            )
            survey_id = cursor.fetchone()[0]
            columns = [
                "survey_id", "sample_no", "recorded_at", "name", "designation", "station_no", "station_code",
                "chainage", "loop_line_siding", "turnout_no", "curve_no", "level_crossing_no", "hectometer_post",
                "bridge_start", "bridge_end", "level_crossing_lc_in", "level_crossing_lc_out",
                "kilometer_post", "points_crossing", "curve_in", "curve_out",
                "ohe_mast_location", "switch_expansion_joint", "latitude", "longitude", "distance", "gauge",
                "crosslevel", "twist",
            ]
            placeholders = ", ".join(["%s"] * len(columns))
            query = f"INSERT INTO survey_records ({', '.join(columns)}) VALUES ({placeholders})"
            for row in rows:
                cursor.execute(query, (
                    survey_id, _number(_row_value(row, "Sample No")),
                    _recorded_at(_row_value(row, "Date & Time", "Date Time")),
                    _row_value(row, "Name"), _row_value(row, "Designation"),
                    _row_value(row, "Station No", "station_no"), _row_value(row, "Station Code", "station_code"),
                    _number(_row_value(row, "Chainage")), _row_value(row, "Loop/Line Siding"),
                    _row_value(row, "Turn-out No", "Turnout No"), _row_value(row, "Curve No"),
                    _row_value(row, "Level Crossing No"), _row_value(row, "Hectometer Post"),
                    _row_value(row, "Bridge (Start)"), _row_value(row, "Bridge (End)"),
                    _row_value(row, "Level Crossing (LC) In"), _row_value(row, "Level Crossing (LC) Out"),
                    _row_value(row, "Kilometer Post (KM)"), _row_value(row, "Points & Crossing (P&C)"),
                    _row_value(row, "Curve-In"), _row_value(row, "Curve Out"),
                    _row_value(row, "OHE Mast (OHEM) Location"), _row_value(row, "Switch Expansion Joint (SEJ)"),
                    _number(_row_value(row, "Latitude", "Lattitude")), _number(_row_value(row, "Longitude")),
                    _number(_row_value(row, "Distance")), _number(_row_value(row, "Gauge")),
                    _number(_row_value(row, "Crosslevel")),
                    _number(_row_value(row, "Twist")),
                ))
            return survey_id


def _db_records(station=""):
    _ensure_db_schema()
    where = ""
    params = []
    if station:
        where = "WHERE COALESCE(sr.station_no, sr.station_code, s.station_code) = %s"
        params.append(station)
    fields = ", ".join(
        f"sr.{key}"
        for key, _ in DB_RECORD_FIELDS
        if key not in {"station_no", "station_code", "crosslevel", "twist"}
    )
    query = f"""
        SELECT sr.survey_id, {fields},
               COALESCE(sr.station_no, s.station_code) AS station_no,
               COALESCE(sr.station_code, s.station_code) AS station_code,
               sr.crosslevel,
               sr.twist
        FROM survey_records sr JOIN surveys s ON s.id = sr.survey_id
        {where}
        ORDER BY sr.recorded_at ASC NULLS LAST, sr.sample_no ASC, sr.id ASC
    """
    with _db_connect() as connection:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, params)
            records = cursor.fetchall()
    for record in records:
        if record.get("recorded_at"):
            record["recorded_at"] = record["recorded_at"].isoformat()
    return records


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


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
  .filters {
    display: flex;
    gap: 10px;
    align-items: center;
    flex-wrap: wrap;
    margin-bottom: 14px;
  }
  .filters a, .filters button, .filters select {
    background: var(--panel-2);
    border: 1px solid var(--line);
    border-radius: 6px;
    color: var(--text);
    font: inherit;
    padding: 7px 10px;
  }
  .filters a.active {
    border-color: var(--good);
    color: var(--good);
  }
  .filters button { cursor: pointer; }
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
    cleaned_rows = [
        {key: value for key, value in row.items() if key in CSV_FIELDS or key not in set(CSV_FIELDS)}
        for row in rows
    ]
    fields = _ordered_fields(cleaned_rows)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(cleaned_rows)


def _read_csv(path: Path) -> List[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [
        {key: value for key, value in row.items() if key in CSV_FIELDS or key not in set(CSV_FIELDS)}
        for row in rows
    ]


def _csv_files() -> Iterable[Path]:
    return sorted(STORAGE_DIR.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)


def _first_value(row: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _parse_session_datetime(value: str, path: Path) -> datetime:
    value = (value or "").strip()
    for fmt in (
        "%d-%m-%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d_%H-%M-%S",
        "%b %d, %Y, %I:%M:%S %p",
        "%b %d, %Y %I:%M:%S %p",
    ):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(float(value))
    except (TypeError, ValueError):
        return datetime.fromtimestamp(path.stat().st_mtime)


def _session_info(path: Path, serial_no: int) -> dict:
    rows = _read_csv(path)
    first = rows[0] if rows else {}
    started_at = _parse_session_datetime(
        _first_value(first, "Date & Time", "Date and Time", "date and time", "epoch_time"),
        path,
    )
    return {
        "serial_no": serial_no,
        "name": path.name,
        "date_time": started_at.strftime("%d-%m-%Y %H:%M:%S"),
        "date": started_at.date(),
        "type": "Survey",
        "station": _first_value(first, "Station No", "station_no", "Station Code", "station", "Station"),
        "rows": len(rows),
        "size_kb": f"{path.stat().st_size / 1024:.1f}",
    }


def _filter_sessions(files: List[dict], time_range: str, station: str) -> List[dict]:
    filtered = files
    if time_range == "today":
        today = date_cls.today()
        filtered = [file for file in filtered if file["date"] == today]
    if station:
        station_key = station.strip().casefold()
        filtered = [file for file in filtered if file["station"].casefold() == station_key]
    for idx, file in enumerate(filtered, start=1):
        file["serial_no"] = idx
    return filtered


def _all_session_info() -> List[dict]:
    return [_session_info(path, idx) for idx, path in enumerate(_csv_files(), start=1)]


def _selected_sessions_from_request() -> List[dict]:
    time_range = (
        request.args.get("range")
        or request.args.get("timeRange")
        or request.args.get("time_range")
        or "all"
    ).strip().lower()
    if time_range in {"today", "daily"}:
        time_range = "today"
    elif time_range not in {"all", ""}:
        time_range = "all"
    station = (
        request.args.get("station")
        or request.args.get("stationNo")
        or request.args.get("station_no")
        or ""
    ).strip()
    return _filter_sessions(_all_session_info(), time_range or "all", station)


def _session_api_record(file: Mapping[str, object]) -> dict:
    filename = str(file["name"])
    return {
        "S.NO": file["serial_no"],
        "Date and Time": file["date_time"],
        "Station No": file["station"],
        "Type": file["type"],
        "View CSV": url_for("view_survey", filename=filename, _external=True),
        "Export": {
            "excel": url_for("download_csv", filename=filename, _external=True),
            "pdf": url_for("download_pdf", filename=filename, _external=True),
        },
        "serial_no": file["serial_no"],
        "date_time": file["date_time"],
        "type": file["type"],
        "station_no": file["station"],
        "filename": filename,
        "view_csv_url": url_for("view_survey", filename=filename, _external=True),
        "download_excel_url": url_for("download_csv", filename=filename, _external=True),
        "download_pdf_url": url_for("download_pdf", filename=filename, _external=True),
    }


def _pdf_escape(text: object) -> str:
    return str(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _make_text_pdf(title: str, lines: List[str]) -> bytes:
    pages = []
    for start in range(0, max(len(lines), 1), 42):
        pages.append(lines[start : start + 42] or ["No data"])

    objects = ["<< /Type /Catalog /Pages 2 0 R >>"]
    page_refs = []
    next_obj = 3
    for page_lines in pages:
        page_obj = next_obj
        content_obj = next_obj + 1
        next_obj += 2
        page_refs.append(f"{page_obj} 0 R")
        text_ops = ["BT", "/F1 10 Tf", "40 790 Td", f"({_pdf_escape(title)}) Tj", "0 -18 Td"]
        for line in page_lines:
            text_ops.append(f"({_pdf_escape(line[:120])}) Tj")
            text_ops.append("0 -14 Td")
        text_ops.append("ET")
        stream = "\n".join(text_ops).encode("latin-1", errors="replace")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] "
            f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Courier >> >> >> "
            f"/Contents {content_obj} 0 R >>"
        )
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream.decode('latin-1')}\nendstream")

    objects.insert(1, f"<< /Type /Pages /Kids [{' '.join(page_refs)}] /Count {len(page_refs)} >>")
    pdf = BytesIO()
    pdf.write(b"%PDF-1.4\n")
    offsets = [0]
    for obj_no, body in enumerate(objects, start=1):
        offsets.append(pdf.tell())
        pdf.write(f"{obj_no} 0 obj\n{body}\nendobj\n".encode("latin-1", errors="replace"))
    xref = pdf.tell()
    pdf.write(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        pdf.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.write(f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return pdf.getvalue()


@app.get("/")
def index():
    files = []
    for idx, path in enumerate(_csv_files(), start=1):
        files.append(_session_info(path, idx))
    stations = sorted({file["station"] for file in files if file["station"]})
    time_range = request.args.get("range", "all").strip().lower()
    if time_range not in {"all", "today"}:
        time_range = "all"
    station = request.args.get("station", "").strip()
    files = _filter_sessions(files, time_range, station)
    return render_template_string(
        BASE_CSS
        + """
        <main>
          <h1>Rail Inspection Cloud Dashboard</h1>
          <div class="status">
            Status: <b>ACTIVE</b> | Storage: <b>{{ storage }}</b> | Files: <b>{{ files|length }}</b>
          </div>
          <h2>Stored Surveys</h2>
          <form class="filters" method="get">
            <a class="{{ 'active' if time_range == 'all' else '' }}" href="{{ url_for('index', station=station) }}">All</a>
            <a class="{{ 'active' if time_range == 'today' else '' }}" href="{{ url_for('index', range='today', station=station) }}">Today</a>
            <select name="station">
              <option value="">All Station Nos</option>
              {% for option in stations %}
              <option value="{{ option }}" {{ 'selected' if option == station else '' }}>{{ option }}</option>
              {% endfor %}
            </select>
            <input type="hidden" name="range" value="{{ time_range }}">
            <button type="submit">Apply</button>
          </form>
          {% if files %}
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>S.NO</th>
                  <th>Date and Time</th>
                  <th>Station No</th>
                  <th>Type</th>
                  <th>View CSV</th>
                  <th>Export</th>
                </tr>
              </thead>
              <tbody>
                {% for file in files %}
                <tr>
                  <td class="num">{{ file.serial_no }}</td>
                  <td>{{ file.date_time }}</td>
                  <td>{{ file.station }}</td>
                  <td>{{ file.type }}</td>
                  <td>
                    <a href="{{ url_for('view_survey', filename=file.name) }}" target="_blank" rel="noopener">View</a>
                  </td>
                  <td>
                    <a href="{{ url_for('download_csv', filename=file.name) }}">CSV Excel</a>
                    <a href="{{ url_for('download_pdf', filename=file.name) }}">PDF</a>
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
        stations=stations,
        station=station,
        time_range=time_range,
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
            <a href="{{ url_for('download_csv', filename=path.name) }}">Download CSV Excel</a>
            <a href="{{ url_for('download_pdf', filename=path.name) }}">Download PDF</a>
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


@app.get("/pdf/<path:filename>")
def download_pdf(filename: str):
    path = _csv_path(filename)
    if not path.exists():
        abort(404)
    rows = _read_csv(path)
    fields = _ordered_fields(rows) if rows else CSV_FIELDS
    lines = [", ".join(fields)]
    for row in rows:
        lines.append(", ".join(str(row.get(field, "")) for field in fields))
    pdf_name = f"{path.stem}.pdf"
    return Response(
        _make_text_pdf(path.name, lines),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={pdf_name}"},
    )


@app.get("/api/surveys")
@app.get("/api/csv-records")
@app.get("/api/records")
def api_surveys():
    if DATABASE_URL:
        station = (
            request.args.get("station")
            or request.args.get("stationNo")
            or request.args.get("station_no")
            or ""
        ).strip()
        try:
            records = _db_records(station)
            return jsonify({
                "ok": True,
                "records": records,
                "columns": [{"key": key, "label": label} for key, label in DB_RECORD_FIELDS],
                "total_rows": len(records),
            })
        except Exception as exc:
            app.logger.exception("PostgreSQL records query failed")
            return jsonify({"ok": False, "error": str(exc)}), 500

    sessions = _selected_sessions_from_request()
    records = [_session_api_record(file) for file in sessions]
    return jsonify(
        {
            "ok": True,
            "records": records,
            "surveys": records,
            "columns": ["S.NO", "Date and Time", "Station No", "Type", "View CSV", "Export"],
            "total_sessions": len(records),
            "total_rows": sum(int(file.get("rows", 0)) for file in sessions),
        }
    )


@app.get("/api/surveys/<path:filename>")
def api_survey_file(filename: str):
    path = _csv_path(filename)
    if not path.exists():
        abort(404)
    rows = _read_csv(path)
    return jsonify(
        {
            "ok": True,
            "filename": path.name,
            "rows": rows,
            "download_excel_url": url_for("download_csv", filename=path.name, _external=True),
            "download_pdf_url": url_for("download_pdf", filename=path.name, _external=True),
        }
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
    if DATABASE_URL:
        try:
            survey_id = _store_rows_in_db(filename, rows)
            return jsonify({
                "ok": True,
                "surveyId": survey_id,
                "filename": filename,
                "rows": len(rows),
            })
        except Exception as exc:
            app.logger.exception("PostgreSQL survey upload failed")
            return jsonify({"ok": False, "error": str(exc)}), 500

    path = _csv_path(filename)
    _write_csv(path, rows)
    return jsonify({"ok": True, "filename": path.name, "rows": len(rows), "view": url_for("view_survey", filename=path.name)})


@app.get("/health")
def health():
    if DATABASE_URL:
        try:
            _ensure_db_schema()
            with _db_connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT COUNT(*) FROM survey_records")
                    row_count = cursor.fetchone()[0]
            return jsonify({"ok": True, "database": "postgresql", "rows": row_count})
        except Exception as exc:
            return jsonify({"ok": False, "database": "postgresql", "error": str(exc)}), 503
    return jsonify({"ok": True, "database": "file", "storage": str(STORAGE_DIR)})
