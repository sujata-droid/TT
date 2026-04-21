"""
app.py -- Rail Inspection Cloud Server (Render)
"""
import os, json, csv, datetime
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
.empty{color:#606080;font-style:italic;padding:20px}
</style></head>
<body>
<h1>Rail Inspection Cloud Dashboard</h1>
<div class="status">Status: <span>ACTIVE</span> | Storage: <span>{{ storage_dir }}</span> | Files: <span>{{ fc }}</span></div>
<h2>Stored Surveys</h2>
{% if surveys %}
<table><thead><tr><th>Filename</th><th>Received</th><th>Rows</th><th>Size</th><th>Download</th></tr></thead><tbody>
{% for s in surveys %}
<tr><td>{{ s.name }}</td><td>{{ s.received }}</td><td>{{ s.rows }}</td><td>{{ s.size_kb }} kB</td>
<td><a href="/api/download/{{ s.name }}">CSV</a></td></tr>
{% endfor %}
</tbody></table>
{% else %}<p class="empty">No surveys yet. Run the BeagleBone and complete a survey.</p>{% endif %}
</body></html>"""

def _survey_list():
    out = []
    for p in sorted(STORAGE_DIR.glob("*.csv"), key=lambda x: x.stat().st_mtime, reverse=True):
        rows = 0
        try:
            with open(p) as f: rows = max(0, sum(1 for _ in f) - 1)
        except Exception: pass
        out.append({"name": p.name,
            "received": datetime.datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "rows": rows, "size_kb": round(p.stat().st_size / 1024, 1)})
    return out

@app.route("/")
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
        fields = ["timestamp_us","cross_level_mm","twist_mm_per_m",
                  "chainage_m","gauge_mm","scl3300_ok","encoder_ok"]
        with open(out,"w",newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader(); w.writerows(rows)
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

@app.route("/api/latest")
def latest_survey():
    surveys = _survey_list()
    if not surveys: return jsonify({"status":"empty"}), 404
    p = STORAGE_DIR / surveys[0]["name"]
    rows = []
    with open(p) as f:
        for row in csv.DictReader(f): rows.append(row)
    return jsonify({"filename":surveys[0]["name"],"rows":len(rows),"data":rows[:1000]}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
