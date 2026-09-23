# NYC 311 Triage Desk API

File-backed FastAPI service for the exported Databricks model and replay artifacts. It makes no runtime Databricks or database calls.

## Local run

```powershell
python -m pip install -r api/requirements.txt
python -m uvicorn app.main:app --app-dir api --reload
```

Open `http://127.0.0.1:8000/docs`. Set `CORS_ORIGINS` to a comma-separated list of allowed web origins in deployment.

## Artifact contract

The ten files in `api/data/` are loaded once at startup. Queue replay uses strict lifecycle semantics: `created_date < as_of` and (`closed_date` is null or `closed_date > as_of`). Source timestamps are interpreted as `America/New_York` local time.

`kpis_agency.json` and both hotspot exports are all-period aggregates, not dated series. The corresponding endpoints explicitly return `temporal_filter_applied: false` rather than pretending to apply unsupported date filters.
