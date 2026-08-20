# Rail Inspection Cloud

Flask dashboard and upload API for completed rail inspection survey CSV files.

## Endpoints

- `GET /health` - health check used by Render.
- `GET /` - dashboard with uploaded surveys.
- `POST /api/survey` - upload survey JSON from the BeagleBone.

Upload payload:

```json
{
  "filename": "survey.csv",
  "data": [
    {
      "Sample No": "1",
      "Chainage": "0.000",
      "Gauge": "0.00"
    }
  ]
}
```

## Render Setup

This repo includes a root `render.yaml` Blueprint.

1. Push this project to GitHub.
2. In Render, choose **New > Blueprint**.
3. Select this repository.
4. Approve the `rail-inspection-cloud` web service.
5. After deploy, open:

```text
https://<your-render-service>.onrender.com/health
```

The Blueprint mounts a persistent disk at `/var/data` and sets `STORAGE_DIR=/var/data/rail_surveys`, so uploaded CSV files survive redeploys.

## BeagleBone Setup

Set the cloud endpoint before launching the backend:

```bash
export RAIL_CLOUD_URL=https://<your-render-service>.onrender.com/api/survey
cd /home/debian/trolley
bash bbb_runtime/run_railgui25_backend.sh
```

Manual upload of the latest CSV:

```bash
cd /home/debian/trolley
RAIL_CLOUD_URL=https://<your-render-service>.onrender.com/api/survey bash push_latest_csv.sh /home/debian/surveys
```
