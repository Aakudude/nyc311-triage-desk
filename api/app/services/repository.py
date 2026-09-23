"""In-memory access to exported Databricks artifacts."""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

NYC_TZ = ZoneInfo("America/New_York")
REQUIRED_FILES = (
    "dq_summary.json",
    "feature_metadata.json",
    "hotspots_borough.json",
    "hotspots_complaint.json",
    "kpis_agency.json",
    "model.json",
    "model_card.json",
    "score_defaults.json",
    "scored_test.parquet",
    "sla_targets.json",
)


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def clean_value(value: Any) -> Any:
    """Convert pandas/NumPy values into JSON-safe Python primitives."""
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return local_iso(value)
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def local_naive(value: datetime | str | pd.Timestamp) -> pd.Timestamp:
    """Interpret naive timestamps as NYC local time; normalize aware values to NYC."""
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert(NYC_TZ).tz_localize(None)
    return timestamp


def local_iso(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    timestamp = local_naive(value)
    return timestamp.to_pydatetime().replace(tzinfo=NYC_TZ).isoformat()


class ArtifactRepository:
    """Loads immutable runtime artifacts once and serves vectorized replay queries."""

    def __init__(self, data_dir: Path) -> None:
        missing = [name for name in REQUIRED_FILES if not (data_dir / name).is_file()]
        if missing:
            raise FileNotFoundError(f"Missing API artifacts in {data_dir}: {', '.join(missing)}")

        self.data_dir = data_dir
        self.metadata = _read_json(data_dir / "feature_metadata.json")
        self.model_card = _read_json(data_dir / "model_card.json")
        self.dq_summary = _read_json(data_dir / "dq_summary.json")
        self.kpis = _read_json(data_dir / "kpis_agency.json")
        self.hotspots_borough = _read_json(data_dir / "hotspots_borough.json")
        self.hotspots_complaint = _read_json(data_dir / "hotspots_complaint.json")
        self.sla_targets = _read_json(data_dir / "sla_targets.json")
        defaults = _read_json(data_dir / "score_defaults.json")

        self.tickets = pd.read_parquet(data_dir / "scored_test.parquet")
        self.tickets["unique_key"] = self.tickets["unique_key"].astype(str)
        self.tickets["created_date"] = pd.to_datetime(self.tickets["created_date"])
        self.tickets["closed_date"] = pd.to_datetime(self.tickets["closed_date"])
        self.ticket_positions = {
            key: position for position, key in enumerate(self.tickets["unique_key"])
        }

        replay = self.metadata["replay_range"]
        self.replay_start = local_naive(replay["start"])
        self.replay_end = local_naive(replay["end_exclusive"])

        self.agencies = sorted(self.tickets["agency"].dropna().unique().tolist())
        self.boroughs = sorted(self.tickets["borough"].dropna().unique().tolist())
        self.channels = sorted(self.tickets["channel"].dropna().unique().tolist())

        self.sla_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
        self.agency_p75: dict[str, float] = {}
        self.complaint_canonical: dict[tuple[str, str], str] = {}
        for row in self.sla_targets:
            agency = str(row["agency"]).upper()
            complaint = str(row["complaint_type"])
            self.sla_by_pair[(agency, complaint)] = row
            self.complaint_canonical[(agency, complaint.casefold())] = complaint
            self.agency_p75[agency] = float(row["agency_p75"])

        grouped = (
            self.tickets[["agency", "complaint_type", "complaint_type_grouped"]]
            .drop_duplicates(["agency", "complaint_type"])
        )
        self.grouped_complaints = {
            (str(row.agency).upper(), str(row.complaint_type)): str(row.complaint_type_grouped)
            for row in grouped.itertuples(index=False)
        }

        self.defaults_by_key: dict[tuple[str, int, int], dict[str, Any]] = {}
        for row in defaults:
            key = (
                str(row["agency"]).upper(),
                int(row["hour_of_day_created"]),
                int(row["day_of_week"]),
            )
            self.defaults_by_key[key] = row

    def canonical_complaint(self, agency: str, complaint_type: str) -> str:
        return self.complaint_canonical.get(
            (agency.upper(), complaint_type.strip().casefold()),
            complaint_type.strip(),
        )

    def grouped_complaint(self, agency: str, complaint_type: str) -> str:
        return self.grouped_complaints.get(
            (agency.upper(), complaint_type),
            f"OTHER_{agency.upper()}",
        )

    def sla_for(self, agency: str, complaint_type: str) -> tuple[float, str]:
        row = self.sla_by_pair.get((agency.upper(), complaint_type))
        if row is not None:
            return float(row["sla_target_hours"]), str(row["target_source"])
        fallback = self.agency_p75.get(agency.upper())
        if fallback is None:
            raise ValueError(f"Unsupported agency: {agency}")
        return fallback, "agency_p75_unseen_complaint_fallback"

    def default_values(self, agency: str, hour: int, day_of_week: int) -> dict[str, Any]:
        return self.defaults_by_key.get((agency.upper(), hour, day_of_week), {})

    def get_ticket(self, unique_key: str) -> pd.Series | None:
        position = self.ticket_positions.get(str(unique_key))
        if position is None:
            return None
        return self.tickets.iloc[position]

    @staticmethod
    def reasons(row: pd.Series) -> list[dict[str, Any]]:
        value = row.get("top_reasons")
        if not isinstance(value, str) or not value:
            return []
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []

    def queue(
        self,
        as_of: datetime,
        agency: str | None,
        borough: str | None,
        min_risk: float,
        limit: int,
        reveal_outcome: bool,
    ) -> dict[str, Any]:
        replay_time = local_naive(as_of)
        if replay_time < self.replay_start or replay_time >= self.replay_end:
            raise ValueError(
                "as_of must be within "
                f"[{local_iso(self.replay_start)}, {local_iso(self.replay_end)})"
            )

        frame = self.tickets
        mask = (frame["created_date"] < replay_time) & (
            frame["closed_date"].isna() | (frame["closed_date"] > replay_time)
        )
        if agency:
            mask &= frame["agency"] == agency.upper()
        if borough:
            mask &= frame["borough"] == borough.upper()
        mask &= frame["risk"] >= min_risk

        selected = frame.loc[mask].copy()
        selected["age_hours"] = (
            replay_time - selected["created_date"]
        ).dt.total_seconds() / 3600.0
        selected["already_breached"] = (
            selected["age_hours"] > selected["sla_target_hours"]
        )
        selected.sort_values(
            ["risk", "unique_key"], ascending=[False, True], inplace=True
        )
        total = len(selected)
        selected = selected.head(limit)

        items: list[dict[str, Any]] = []
        for row in selected.itertuples(index=False):
            item = {
                "unique_key": str(row.unique_key),
                "agency": row.agency,
                "complaint_type": row.complaint_type,
                "borough": row.borough,
                "created_at": local_iso(row.created_date),
                "age_hours": round(float(row.age_hours), 2),
                "risk": float(row.risk),
                "risk_band": row.risk_band,
                "sla_threshold_hrs": float(row.sla_target_hours),
                "already_breached": bool(row.already_breached),
            }
            if reveal_outcome:
                item["actual_outcome"] = {
                    "breached": bool(row.breach),
                    "closed_at": local_iso(row.closed_date),
                    "resolution_hours": clean_value(row.resolution_hours),
                }
            items.append(item)

        return {
            "as_of": local_iso(replay_time),
            "timezone": "America/New_York",
            "total": total,
            "count": len(items),
            "items": items,
        }

    def ticket_payload(self, row: pd.Series, reveal_outcome: bool) -> dict[str, Any]:
        payload = {
            "unique_key": str(row["unique_key"]),
            "agency": row["agency"],
            "complaint_type": row["complaint_type"],
            "descriptor": clean_value(row.get("descriptor")),
            "borough": row["borough"],
            "channel": row["channel"],
            "status_at_extract": row["status"],
            "created_at": local_iso(row["created_date"]),
            "risk": float(row["risk"]),
            "risk_band": row["risk_band"],
            "sla_threshold_hrs": float(row["sla_target_hours"]),
            "top_reasons": self.reasons(row),
            "feature_values": {
                feature: clean_value(row.get(feature))
                for feature in self.metadata["feature_cols"]
            },
        }
        if reveal_outcome:
            payload["actual_outcome"] = {
                "breached": bool(row["breach"]),
                "label_state": row["label_state"],
                "closed_at": local_iso(row["closed_date"]),
                "resolution_hours": clean_value(row["resolution_hours"]),
            }
        return payload
