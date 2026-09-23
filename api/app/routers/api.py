"""Public API routes."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import HealthResponse, ScoreRequest
from app.services.repository import ArtifactRepository, clean_value, local_iso
from app.services.scoring import ScoringService

router = APIRouter()


def repository(request: Request) -> ArtifactRepository:
    return request.app.state.repository


def scorer(request: Request) -> ScoringService:
    return request.app.state.scorer


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> dict:
    repo = repository(request)
    return {
        "status": "ok",
        "model_version": repo.metadata["model_version"],
        "replay_start": local_iso(repo.replay_start),
        "replay_end_exclusive": local_iso(repo.replay_end),
        "snapshot_meaning": "Historical replay window; not a live-data timestamp",
        "tickets_loaded": len(repo.tickets),
    }


@router.get("/meta")
def meta(request: Request) -> dict:
    repo = repository(request)
    complaints_by_agency: dict[str, list[str]] = {}
    for agency in repo.agencies:
        complaints_by_agency[agency] = sorted(
            row["complaint_type"]
            for row in repo.sla_targets
            if row["agency"] == agency
        )
    return {
        "model_version": repo.metadata["model_version"],
        "timezone": "America/New_York",
        "agencies": repo.agencies,
        "boroughs": repo.boroughs,
        "channels": repo.channels,
        "complaint_types_by_agency": complaints_by_agency,
        "valid_replay_range": {
            "start": local_iso(repo.replay_start),
            "end_exclusive": local_iso(repo.replay_end),
        },
        "sla_thresholds": repo.sla_targets,
        "portfolio_sla_assumption": repo.model_card["sla_definition"],
    }


@router.get("/kpis")
def kpis(
    request: Request,
    agency: str | None = None,
    from_date: date | None = Query(default=None, alias="from"),
    to_date: date | None = Query(default=None, alias="to"),
) -> dict:
    repo = repository(request)
    rows = repo.kpis
    if agency:
        rows = [row for row in rows if row["agency"] == agency.upper()]
    return {
        "grain": "all_exported_data_by_agency",
        "requested_period": {
            "from": from_date.isoformat() if from_date else None,
            "to": to_date.isoformat() if to_date else None,
        },
        "temporal_filter_applied": False,
        "limitation": "The current KPI artifact has no date column; dated trends require a new Gold export.",
        "items": rows,
    }


@router.get("/queue")
def queue(
    request: Request,
    as_of: datetime,
    agency: str | None = None,
    borough: str | None = None,
    min_risk: float = Query(default=0.0, ge=0.0, le=1.0),
    limit: int = Query(default=100, ge=1, le=500),
    reveal_outcome: bool = False,
) -> dict:
    try:
        return repository(request).queue(
            as_of=as_of,
            agency=agency,
            borough=borough,
            min_risk=min_risk,
            limit=limit,
            reveal_outcome=reveal_outcome,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/tickets/{unique_key}")
def ticket(
    unique_key: str,
    request: Request,
    reveal_outcome: bool = False,
) -> dict:
    repo = repository(request)
    row = repo.get_ticket(unique_key)
    if row is None:
        raise HTTPException(status_code=404, detail="Ticket not found in replay artifact")
    return repo.ticket_payload(row, reveal_outcome)


@router.post("/score")
def score(payload: ScoreRequest, request: Request) -> dict:
    try:
        return scorer(request).score(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/hotspots")
def hotspots(
    request: Request,
    level: Literal["agency_complaint", "borough"],
    period: str | None = None,
) -> dict:
    repo = repository(request)
    rows = (
        repo.hotspots_complaint
        if level == "agency_complaint"
        else repo.hotspots_borough
    )
    return {
        "level": level,
        "requested_period": period,
        "temporal_filter_applied": False,
        "limitation": "The current hotspot artifacts are all-period aggregates with no date column.",
        "items": [{key: clean_value(value) for key, value in row.items()} for row in rows],
    }


@router.get("/model-card")
def model_card(request: Request) -> dict:
    return repository(request).model_card
