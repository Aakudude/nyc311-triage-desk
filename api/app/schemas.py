"""Request and response schemas for public API contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HealthResponse(BaseModel):
    status: str
    model_version: str
    replay_start: str
    replay_end_exclusive: str
    snapshot_meaning: str
    tickets_loaded: int


class Reason(BaseModel):
    feature: str
    label: str
    value: Any = None
    train_median: float | None = None
    direction: str
    contribution_log_odds: float


class ScoreRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    agency: str = Field(min_length=1)
    complaint_type: str = Field(min_length=1)
    borough: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    created_at: datetime
    agency_open_backlog: float | None = Field(default=None, ge=0)
    complaint_open_backlog: float | None = Field(default=None, ge=0)
    agency_arrivals_24h: float | None = Field(default=None, ge=0)
    agency_closures_24h: float | None = Field(default=None, ge=0)
    hist_median_res_hrs_28d: float | None = Field(default=None, ge=0)
    hist_breach_rate_28d: float | None = Field(default=None, ge=0, le=1)

    @field_validator("agency", "borough", "channel")
    @classmethod
    def uppercase_categories(cls, value: str) -> str:
        return value.strip().upper()
