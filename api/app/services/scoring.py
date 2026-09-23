"""Native XGBoost what-if scoring using frozen training metadata."""

from __future__ import annotations

from typing import Any

import numpy as np
import xgboost as xgb

from app.schemas import ScoreRequest
from app.services.repository import ArtifactRepository, local_naive


REASON_LABELS = {
    "agency_open_backlog": "Agency backlog at creation",
    "complaint_open_backlog": "Complaint-type backlog at creation",
    "agency_arrivals_24h": "Agency arrivals in prior 24h",
    "agency_closures_24h": "Agency closures in prior 24h",
    "hist_median_res_hrs_28d": "Prior-28d median resolution time",
    "hist_breach_rate_28d": "Prior-28d breach rate",
    "sla_target_hours": "Portfolio SLA threshold",
    "hour_of_day_created": "Creation hour",
    "day_of_week": "Creation weekday",
    "borough": "Borough",
    "channel": "Intake channel",
    "complaint_type_grouped": "Complaint type",
}
OPTIONAL_OPERATIONAL_FEATURES = (
    "agency_open_backlog",
    "complaint_open_backlog",
    "agency_arrivals_24h",
    "agency_closures_24h",
    "hist_median_res_hrs_28d",
    "hist_breach_rate_28d",
)


class ScoringService:
    def __init__(self, repository: ArtifactRepository) -> None:
        self.repository = repository
        self.metadata = repository.metadata
        self.feature_cols = list(self.metadata["feature_cols"])
        self.numeric = set(self.metadata["numeric"])
        self.encoders = self.metadata["encoders"]
        self.booster = xgb.Booster()
        self.booster.load_model(repository.data_dir / "model.json")

    @staticmethod
    def _spark_weekday(timestamp) -> int:
        # Spark dayofweek: Sunday=1, Monday=2, ..., Saturday=7.
        return timestamp.isoweekday() % 7 + 1

    def _calibrate(self, raw_score: float) -> float:
        calibration = self.metadata.get("calibration", {"method": "none"})
        if calibration.get("method") != "isotonic":
            return raw_score
        return float(
            np.interp(
                raw_score,
                calibration["x_thresholds"],
                calibration["y_thresholds"],
            )
        )

    def score(self, request: ScoreRequest) -> dict[str, Any]:
        agency = request.agency.upper()
        if agency not in self.repository.agencies:
            raise ValueError(f"Unsupported agency: {agency}")

        complaint = self.repository.canonical_complaint(agency, request.complaint_type)
        grouped_complaint = self.repository.grouped_complaint(agency, complaint)
        created_at = local_naive(request.created_at)
        hour = int(created_at.hour)
        day_of_week = self._spark_weekday(created_at)
        context_defaults = self.repository.default_values(agency, hour, day_of_week)
        global_defaults = self.metadata["global_numeric_fallback"]
        request_values = request.model_dump()

        values: dict[str, Any] = {
            "hour_of_day_created": hour,
            "day_of_week": day_of_week,
            "borough": request.borough.upper(),
            "channel": request.channel.upper(),
            "complaint_type_grouped": grouped_complaint,
        }
        sla, sla_source = self.repository.sla_for(agency, complaint)
        values["sla_target_hours"] = sla

        defaulted_fields: list[dict[str, str]] = []
        for feature in OPTIONAL_OPERATIONAL_FEATURES:
            supplied = request_values.get(feature)
            if supplied is not None:
                values[feature] = float(supplied)
                continue
            contextual = context_defaults.get(feature)
            if contextual is not None:
                values[feature] = float(contextual)
                source = "agency_hour_weekday_median"
            else:
                values[feature] = float(global_defaults[feature])
                source = "global_train_median"
            defaulted_fields.append({"feature": feature, "source": source})

        encoded: list[float] = []
        for feature in self.feature_cols:
            value = values[feature]
            if feature in self.numeric:
                encoded.append(float(value))
            else:
                encoded.append(float(self.encoders[feature].get(str(value), 0)))

        matrix = xgb.DMatrix(
            np.asarray([encoded], dtype=np.float32),
            feature_names=self.feature_cols,
        )
        raw_score = float(self.booster.predict(matrix)[0])
        risk = self._calibrate(raw_score)
        contributions = self.booster.predict(matrix, pred_contribs=True)[0][:-1]

        reasons: list[dict[str, Any]] = []
        for index in np.argsort(-contributions):
            contribution = float(contributions[index])
            if contribution <= 0 or len(reasons) == 3:
                break
            feature = self.feature_cols[index]
            reasons.append({
                "feature": feature,
                "label": REASON_LABELS[feature],
                "value": values[feature],
                "train_median": (
                    float(global_defaults[feature])
                    if feature in global_defaults
                    else None
                ),
                "direction": "increases_risk",
                "contribution_log_odds": contribution,
            })

        return {
            "model_version": self.metadata["model_version"],
            "agency": agency,
            "complaint_type": complaint,
            "complaint_type_grouped": grouped_complaint,
            "risk": risk,
            "risk_band": (
                "high"
                if risk >= float(self.metadata["top_decile_threshold"])
                else "normal"
            ),
            "sla_threshold_hrs": sla,
            "sla_source": sla_source,
            "reasons": reasons,
            "defaulted_fields": defaulted_fields,
            "defaults_used": bool(defaulted_fields),
        }
