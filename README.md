# NYC 311 Triage Desk

A risk-ranked operations queue that helps city-agency supervisors identify open NYC 311 requests likely to run unusually long. The project combines a Databricks Bronze/Silver/Gold pipeline, leakage-safe point-in-time features, XGBoost risk scoring, SHAP explanations, and a file-backed FastAPI service that runs without a live warehouse.

> The SLA is a portfolio assumption, not an official NYC target: a breach means resolution time exceeded the train-only P75 for the ticket's agency and complaint type. Groups below 200 training closures use the agency P75.

## Current results

- 600,000 raw tickets profiled
- 597,106 valid Silver tickets; 2,894 quarantined rejects
- Test PR-AUC: **0.371** vs **0.302** for the agency/complaint lookup baseline
- Top-decile precision: **40.1%** vs a **28.0%** test breach rate
- Top-decile lift: **1.43×**
- 136,117 tickets available for historical queue replay

## Architecture

```text
NYC Open Data
    -> Databricks Bronze (raw fidelity)
    -> Silver (typed, validated, rejects quarantined)
    -> Gold (portfolio SLA and operational aggregates)
    -> Leakage-safe ML features
    -> XGBoost + MLflow + SHAP
    -> Exported model/Parquet/JSON artifacts
    -> FastAPI on Render (no runtime Databricks dependency)
    -> Next.js operations workspace on Vercel
```

## Repository

- `scripts/` — extraction and Databricks notebook-source files
- `api/app/` — FastAPI application and artifact-backed services
- `api/data/` — exported model and serving artifacts
- `web/` — five-view Next.js supervisor workspace
- `data/profile_report.txt` — raw extract profile summary

The large raw CSV is intentionally excluded from Git.

## Run the API locally

```powershell
py -3.12 -m pip install -r api\requirements.txt
py -3.12 -m uvicorn app.main:app --app-dir api --reload
```

Open `http://127.0.0.1:8000/docs`. The deployed API is at [nyc311-triage-api.onrender.com](https://nyc311-triage-api.onrender.com).

## Run the frontend locally

```powershell
Set-Location web
npm install
npm run dev
```

Open `http://localhost:3000`. Set `NEXT_PUBLIC_API_URL` in `web/.env.local` to use a different API deployment.

## Core endpoints

- `GET /health`
- `GET /meta`
- `GET /kpis`
- `GET /queue`
- `GET /tickets/{unique_key}`
- `POST /score`
- `GET /hotspots`
- `GET /model-card`

## Data limitation

The delivered extract spans August 29–October 31, 2025. Seven-day temporal purge gaps are preserved, but the untouched future test period is ten days rather than the originally planned full month. The API's KPI and hotspot artifacts are currently all-period aggregates rather than dated time series.

## Usage note

Portfolio demonstration built from public NYC Open Data. Databricks Free Edition is intended for non-commercial use.
