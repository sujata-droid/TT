"""
app.py -- Rail Inspection Cloud Server (Render)
"""
import csv
import datetime
import os
from pathlib import Path
from flask import Flask, request, jsonify, render_template_string, send_file

app = Flask(__name__)
STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", "/tmp/rail_surveys"))
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
MAX_MB = 50

DASHBOARD_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Rail Cloud Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Courier New',monospace;background:#0d0d1a;color:#e0e0f0;padding:20px}
h1{color:#60b0ff;margin-bottom:20px;border-bottom:1px solid #334;padding-bottom:10px}
h2{color:#80d080;margin:20px 0 10px;font-size:1em}
.status{background:#1a1a2e;border:1px solid #334;border-radius:6px;padding:12px;margin-bottom:16px}
.status span{color:#40e0a0;font-weight:bold}
table{width:100%;border-collapse:collapse;margin-bottom:20px}
th{background:#1a2040;color:#80c0ff;padding:8px;text-align:left;border-bottom:2px solid #334;font-size:.85em}
td{padding:7px 8px;border-bottom:1px solid #222;font-size:.85em}
tr:hover td{background:#1a1a2e}
a{color:#60b0ff;text-decoration:none}
a:hover{text-decoration:underline}
.viewer-link{margin-left:14px}
.empty{color:#606080;font-style:italic;padding:20px}
</style></head>
<body>
<h1>Rail Inspection Cloud Dashboard</h1>
<div class="status">Status: <span>ACTIVE</span> | Storage: <span>{{ storage_dir }}</span> | Files: <span>{{ fc }}</span></div>
<h2>Stored Surveys</h2>
{% if surveys %}
<table><thead><tr><th>Filename</th><th>Received</th><th>Rows</th><th>Size</th><th>Actions</th></tr></thead><tbody>
{% for s in surveys %}
<tr><td>{{ s.name }}</td><td>{{ s.received }}</td><td>{{ s.rows }}</td><td>{{ s.size_kb }} kB</td>
<td><a href="/view/{{ s.name }}">View</a><a class="viewer-link" href="/api/download/{{ s.name }}">CSV</a></td></tr>
{% endfor %}
</tbody></table>
{% else %}<p class="empty">No surveys yet. Run the BeagleBone and complete a survey.</p>{% endif %}
</body></html>"""

VIEWER_HTML = """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>{{ filename }} - Rail Survey Viewer</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Courier New',monospace;background:#0d0d1a;color:#e0e0f0;padding:20px}
h1{color:#60b0ff;margin-bottom:12px}
.meta{background:#1a1a2e;border:1px solid #334;border-radius:6px;padding:12px;margin-bottom:16px}
.meta a{color:#80c0ff;text-decoration:none}
.meta a:hover{text-decoration:underline}
.wrap{overflow:auto;border:1px solid #334;border-radius:6px;background:#121826}
table{min-width:100%;border-collapse:collapse}
th{position:sticky;top:0;background:#1a2040;color:#80c0ff;padding:8px;text-align:left;border-bottom:2px solid #334;font-size:.85em;white-space:nowrap}
td{padding:7px 8px;border-bottom:1px solid #222;font-size:.82em;white-space:nowrap}
tr:hover td{background:#1a1a2e}
.empty{color:#9098aa;padding:20px}
</style></head>
<body>
<h1>{{ filename }}</h1>
<div class="meta">Rows: <strong>{{ row_count }}</strong> | Columns: <strong>{{ col_count }}</strong> | <a href="/">Dashboard</a> | <a href="/api/download/{{ filename }}">Download CSV</a></div>
{% if columns and rows %}
<div class="wrap"><table><thead><tr>{% for col in columns %}<th>{{ col }}</th>{% endfor %}</tr></thead><tbody>
{% for row in rows %}
<tr>{% for col in columns %}<td>{{ row.get(col, "") }}</td>{% endfor %}</tr>
{% endfor %}
</tbody></table></div>
{% else %}
<p class="empty">No rows found in this CSV file.</p>
{% endif %}
</body></html>"""

def _survey_list():
    out = []
    for p in sorted(STORAGE_DIR.glob("*.csv"), key=lambda x: x.stat().st_mtime, reverse=True):
        rows = 0
        try:
            _, data_rows = _read_csv(p)
            rows = len(data_rows)
        except Exception:
            pass
        out.append({"name": p.name,
            "received": datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "rows": rows, "size_kb": round(p.stat().st_size / 1024, 1)})
    return out


def _read_csv(path, limit=None):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames or []
        rows = []
        for idx, row in enumerate(reader):
            rows.append(row)
            if limit is not None and idx + 1 >= limit:
                break
    return columns, rows

@app.route("/")
@app.route("/dashboard")
def dashboard():
    surveys = _survey_list()
    return render_template_string(DASHBOARD_HTML, surveys=surveys, fc=len(surveys),
                                  storage_dir=str(STORAGE_DIR))

@app.route("/healthz")
def health(): return "OK", 200

@app.route("/api/survey", methods=["POST"])
def receive_survey():
    if (request.content_length or 0) > MAX_MB * 1024 * 1024:
        return jsonify({"status":"error","message":"Payload too large"}), 413
    try:
        data = request.get_json(force=True, silent=True)
        if not data: return jsonify({"status":"error","message":"Invalid JSON"}), 400
        filename = Path(data.get("filename","survey_unknown.csv")).name
        if not filename.endswith(".csv"): filename += ".csv"
        rows = data.get("data", [])
        if not isinstance(rows,list) or not rows:
            return jsonify({"status":"error","message":"Empty data"}), 400
        out = STORAGE_DIR / filename
        fields = []
        for row in rows:
            if isinstance(row, dict):
                for key in row.keys():
                    if key not in fields:
                        fields.append(key)
        if not fields:
            return jsonify({"status":"error","message":"Rows have no columns"}), 400
        with open(out,"w",newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        app.logger.info("Stored %s (%d rows)", filename, len(rows))
        return jsonify({"status":"success","filename":filename,"row_count":len(rows)}), 200
    except Exception as e:
        app.logger.error("Error: %s", e)
        return jsonify({"status":"error","message":str(e)}), 500

@app.route("/api/surveys")
def list_surveys(): return jsonify({"surveys": _survey_list()}), 200

@app.route("/api/download/<filename>")
def download_survey(filename):
    p = STORAGE_DIR / Path(filename).name
    if not p.exists(): return jsonify({"status":"error","message":"Not found"}), 404
    return send_file(str(p), mimetype="text/csv", as_attachment=True,
                     download_name=p.name)


@app.route("/view/<filename>")
def view_survey(filename):
    p = STORAGE_DIR / Path(filename).name
    if not p.exists():
        return jsonify({"status":"error","message":"Not found"}), 404
    columns, rows = _read_csv(p, limit=1000)
    return render_template_string(
        VIEWER_HTML,
        filename=p.name,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        col_count=len(columns),
    )

@app.route("/api/latest")
def latest_survey():
    surveys = _survey_list()
    if not surveys: return jsonify({"status":"empty"}), 404
    p = STORAGE_DIR / surveys[0]["name"]
    columns, rows = _read_csv(p, limit=1000)
    return jsonify({"filename":surveys[0]["name"],"rows":len(rows),"columns":columns,"data":rows}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
