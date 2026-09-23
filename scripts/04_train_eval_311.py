# Databricks notebook source
# MAGIC %md
# MAGIC # Phase 4b: Train, evaluate, explain, export
# MAGIC Trains the breach-risk model on `ml_features_tickets`, compares it against two baselines on
# MAGIC the **untouched test month**, computes SHAP reasons, and exports everything the Render API
# MAGIC needs so the live app runs with the Databricks workspace turned off.
# MAGIC
# MAGIC ## What "good" looks like (from the spec)
# MAGIC - Primary metric: **PR-AUC** (breach is the ~25% positive class; PR-AUC > base rate = signal).
# MAGIC - Also ROC-AUC and Brier score.
# MAGIC - Operational: precision/recall and **lift in the top 10%** of scored tickets (a
# MAGIC   capacity-limited supervisor only works the top of the queue).
# MAGIC - Per-agency PR-AUC to show where it fails.
# MAGIC - Beat both baselines or report honestly that it didn't.
# MAGIC
# MAGIC ## Baselines
# MAGIC - **B0**: constant train base rate (a model that predicts the average for everyone).
# MAGIC - **B1**: historical breach rate by (agency, complaint_type) looked up from train -- the
# MAGIC   "no-ML" operational baseline a supervisor could build in a spreadsheet.
# MAGIC
# MAGIC ## Leakage posture
# MAGIC Everything leakage-sensitive was already frozen in `03_ml_features_311.py` (train-only
# MAGIC thresholds, point-in-time features, purge gaps). Here we only fit encoders and the model on
# MAGIC `split == "train"`, tune on `val`, and touch `test` exactly once for the final report.

# COMMAND ----------

# MAGIC %pip install xgboost==2.1.4 scikit-learn==1.6.1 --quiet
# MAGIC
# MAGIC # Use the Databricks Runtime's bundled MLflow. Pinning an older MLflow client can be
# MAGIC # incompatible with newer Databricks model metadata (for example `PlanMetrics`).

# COMMAND ----------

import json
import numpy as np
import pandas as pd
import mlflow
import xgboost as xgb
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score, roc_auc_score, brier_score_loss

from pyspark.sql import functions as F

CATALOG_SCHEMA = "workspace.sla_triage"
FEATURES_TABLE = f"{CATALOG_SCHEMA}.ml_features_tickets"

# Volume the Render API artifacts are exported to (download via Catalog UI / Databricks CLI).
EXPORT_VOLUME = "/Volumes/workspace/sla_triage/artifacts"
MODEL_VERSION = "v1"
TOP_DECILE = 0.10

# A path under /Volumes only works after the Unity Catalog Volume itself exists.
spark.sql("CREATE VOLUME IF NOT EXISTS workspace.sla_triage.artifacts")

CATEGORICAL = ["borough", "channel", "complaint_type_grouped"]
NUMERIC = [
    "agency_open_backlog", "complaint_open_backlog",
    "agency_arrivals_24h", "agency_closures_24h",
    "hist_median_res_hrs_28d", "hist_breach_rate_28d",
    "sla_target_hours", "hour_of_day_created", "day_of_week",
]
FEATURE_COLS = NUMERIC + CATEGORICAL

# COMMAND ----------

# 1) Pull the full point-in-time table once. Labeled rows drive train/evaluation; all rows
#    remain available for replay scoring (including censored tickets and carry-in backlog).
SERVING_COLS = [
    "unique_key", "split", "breach", "label_state", "is_open",
    "agency", "complaint_type", "descriptor", "created_date", "closed_date",
    "resolution_hours", "status", "age_at_observation_hours",
] + FEATURE_COLS
all_pdf = spark.table(FEATURES_TABLE).select(*SERVING_COLS).toPandas()
labeled_pdf = all_pdf[all_pdf.breach.notna()].copy()

train = labeled_pdf[labeled_pdf.split == "train"].reset_index(drop=True)
val = labeled_pdf[labeled_pdf.split == "val"].reset_index(drop=True)
test = labeled_pdf[labeled_pdf.split == "test"].reset_index(drop=True)
print(f"train={len(train):,}  val={len(val):,}  test={len(test):,}")
print(f"train breach rate={train.breach.mean():.3f}  "
      f"val={val.breach.mean():.3f}  test={test.breach.mean():.3f}")

# COMMAND ----------

# 2) Ordinal-encode categoricals with mappings fit on TRAIN only. Unknown/unseen categories
#    (including anything only present in val/test) map to code 0 = "OTHER/unknown".
def fit_ordinal(series):
    cats = sorted(series.dropna().unique().tolist())
    return {c: i + 1 for i, c in enumerate(cats)}  # 0 reserved for unknown

encoders = {c: fit_ordinal(train[c]) for c in CATEGORICAL}

def encode(df):
    out = df.copy()
    for c in CATEGORICAL:
        out[c] = out[c].map(encoders[c]).fillna(0).astype("int32")
    for c in NUMERIC:
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0)
    return out

Xtr, ytr = encode(train)[FEATURE_COLS], train.breach.astype(int).values
Xva, yva = encode(val)[FEATURE_COLS], val.breach.astype(int).values
Xte, yte = encode(test)[FEATURE_COLS], test.breach.astype(int).values

# COMMAND ----------

# 3) Metric helpers, including top-decile lift (the operational headline). Ties use
#    unique_key as a deterministic secondary key instead of accidental DataFrame row order.
def top_decile_lift(y_true, y_score, ticket_keys, frac=TOP_DECILE):
    n = max(1, int(len(y_score) * frac))
    order = np.lexsort((np.asarray(ticket_keys, dtype=str), -np.asarray(y_score)))
    top = order[:n]
    top_rate = y_true[top].mean()
    base = y_true.mean()
    recall_captured = y_true[top].sum() / max(1, y_true.sum())
    return {
        "top_decile_precision": float(top_rate),
        "top_decile_lift": float(top_rate / base) if base > 0 else float("nan"),
        "top_decile_recall": float(recall_captured),
        "n_top": int(n),
        "tie_policy": "score_desc_then_unique_key_asc",
    }


def evaluate(name, y_true, y_score, ticket_keys):
    m = {
        "model": name,
        "pr_auc": float(average_precision_score(y_true, y_score)),
        "roc_auc": float(roc_auc_score(y_true, y_score)),
        "brier": float(brier_score_loss(y_true, np.clip(y_score, 0, 1))),
        "base_rate": float(y_true.mean()),
    }
    m.update(top_decile_lift(y_true, y_score, ticket_keys))
    return m

# COMMAND ----------

# 4) Baselines on TEST.
# B0: constant train base rate.
b0_rate = float(train.breach.mean())
b0_scores = np.full(len(test), b0_rate)

# B1: historical breach rate by (agency, complaint_type) from TRAIN; unseen -> train base rate.
b1_lookup = train.groupby(["agency", "complaint_type"]).breach.mean()
b1_scores = test.apply(
    lambda r: b1_lookup.get((r.agency, r.complaint_type), b0_rate), axis=1
).values.astype(float)

b0 = evaluate("B0_base_rate", yte, b0_scores, test.unique_key)
# A constant score has no ranking power. Report the analytic capacity result rather than
# pretending a deterministic tie-break is meaningful: top 10% captures 10% at base precision.
b0.update({
    "top_decile_precision": float(yte.mean()),
    "top_decile_lift": 1.0,
    "top_decile_recall": TOP_DECILE,
    "tie_policy": "analytic_no_ranking",
})
b1 = evaluate("B1_group_lookup", yte, b1_scores, test.unique_key)
print("B0:", json.dumps(b0, indent=2))
print("B1:", json.dumps(b1, indent=2))

# COMMAND ----------

# 5) XGBoost with a small random search, tuned on VAL PR-AUC, logged in MLflow.
mlflow.set_experiment("/Shared/311_triage_breach")

dtrain = xgb.DMatrix(Xtr, label=ytr, feature_names=FEATURE_COLS)
dval = xgb.DMatrix(Xva, label=yva, feature_names=FEATURE_COLS)
dtest = xgb.DMatrix(Xte, label=yte, feature_names=FEATURE_COLS)

rng = np.random.default_rng(42)
N_TRIALS = 15
search_space = lambda: {
    "max_depth": int(rng.integers(4, 7)),
    "eta": float(rng.uniform(0.03, 0.08)),
    "subsample": float(rng.uniform(0.7, 0.9)),
    "colsample_bytree": float(rng.uniform(0.7, 0.9)),
    "min_child_weight": float(rng.integers(1, 8)),
}
FIXED = {
    "objective": "binary:logistic",
    "tree_method": "hist",
    "eval_metric": "aucpr",
    # No class weighting: prevalence is ~25%, and raw logistic outputs are presented as
    # probabilities. Weighting would improve rank at the cost of probability calibration.
}

best = {"val_pr_auc": -1.0, "params": None, "booster": None, "n_rounds": None}

with mlflow.start_run(run_name="xgb_search") as parent:
    mlflow.log_params({"n_trials": N_TRIALS, "probability_weighting": "none"})
    for t in range(N_TRIALS):
        params = {**FIXED, **search_space()}
        with mlflow.start_run(run_name=f"trial_{t}", nested=True):
            booster = xgb.train(
                params, dtrain, num_boost_round=600,
                evals=[(dtrain, "train"), (dval, "val")],
                early_stopping_rounds=30, verbose_eval=False,
            )
            val_pred = booster.predict(dval, iteration_range=(0, booster.best_iteration + 1))
            val_pr = average_precision_score(yva, val_pred)
            mlflow.log_params(params)
            mlflow.log_metric("val_pr_auc", val_pr)
            mlflow.log_metric("best_iteration", booster.best_iteration)
            if val_pr > best["val_pr_auc"]:
                best.update(val_pr_auc=val_pr, params=params,
                            booster=booster, n_rounds=booster.best_iteration + 1)
    mlflow.log_metric("best_val_pr_auc", best["val_pr_auc"])
    mlflow.log_params({f"best_{k}": v for k, v in best["params"].items()})

print(f"Best val PR-AUC={best['val_pr_auc']:.4f} at {best['n_rounds']} rounds")
print("Best params:", json.dumps(best["params"], indent=2))

# Slice away post-early-stopping trees before evaluation/export so API `predict()` cannot
# accidentally score with all 600 rounds.
booster = best["booster"][:best["n_rounds"]]

# COMMAND ----------

# 6) Decide calibration on VALIDATION only, then touch TEST once with the frozen transform.
def calibration_bins(y_true, y_score, n_bins=10):
    edges = np.linspace(0, 1, n_bins + 1)
    idx = np.clip(np.digitize(y_score, edges) - 1, 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        mask = idx == b
        if mask.sum() > 0:
            rows.append({
                "bin": b, "n": int(mask.sum()),
                "mean_pred": float(y_score[mask].mean()),
                "actual_rate": float(y_true[mask].mean()),
            })
    return rows


def expected_calibration_error(rows, n):
    return sum((r["n"] / n) * abs(r["mean_pred"] - r["actual_rate"]) for r in rows)


val_raw = booster.predict(dval, iteration_range=(0, best["n_rounds"]))
val_calibration = calibration_bins(yva, val_raw)
val_ece = expected_calibration_error(val_calibration, len(yva))
CALIBRATION_ECE_TRIGGER = 0.03
calibrator = None
if val_ece > CALIBRATION_ECE_TRIGGER:
    calibrator = IsotonicRegression(out_of_bounds="clip").fit(val_raw, yva)
    calibration_method = "isotonic"
else:
    calibration_method = "none"
print(f"Validation ECE={val_ece:.4f}; calibration={calibration_method}")

test_raw = booster.predict(dtest, iteration_range=(0, best["n_rounds"]))
test_pred = calibrator.predict(test_raw) if calibrator is not None else test_raw
xgb_metrics = evaluate("XGBoost", yte, test_pred, test.unique_key)
print("XGBoost TEST:", json.dumps(xgb_metrics, indent=2))

per_agency = []
for ag, idx in test.groupby("agency").groups.items():
    idx = list(idx)
    yt = yte[idx]
    if yt.sum() == 0 or yt.sum() == len(yt):
        per_agency.append({"agency": ag, "n": len(idx), "pr_auc": None, "note": "single-class"})
        continue
    per_agency.append({
        "agency": ag, "n": len(idx),
        "pr_auc": float(average_precision_score(yt, test_pred[idx])),
        "base_rate": float(yt.mean()),
    })
print("Per-agency PR-AUC:", json.dumps(per_agency, indent=2))

calibration = calibration_bins(yte, test_pred)

# COMMAND ----------

# MAGIC %md
# MAGIC ## SHAP reasons via XGBoost `pred_contribs`
# MAGIC No separate `shap` dependency at serving time -- XGBoost gives per-feature log-odds
# MAGIC contributions directly. We build a global mean-|contribution| chart and, per test ticket,
# MAGIC the top-3 positive drivers mapped to plain-language phrases for the ticket-detail view.

# COMMAND ----------

# Explain the untouched labeled TEST set for global model-card importance.
test_contribs = booster.predict(
    dtest, pred_contribs=True, iteration_range=(0, best["n_rounds"])
)[:, :-1]
global_shap = sorted(
    [{"feature": f, "mean_abs_shap": float(np.abs(test_contribs[:, i]).mean())}
     for i, f in enumerate(FEATURE_COLS)],
    key=lambda d: -d["mean_abs_shap"],
)
print("Global feature importance (mean |SHAP|):")
print(json.dumps(global_shap, indent=2))

# Neutral display labels avoid claiming that a raw value is "high" or "slow" merely from
# a positive nonlinear SHAP contribution. Each reason includes value, training median reference,
# direction, and log-odds contribution so the UI can phrase it honestly.
LABELS = {
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
train_references = {c: float(pd.to_numeric(train[c], errors="coerce").median()) for c in NUMERIC}


def native_value(value):
    if pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def top3_reasons(row_contrib, raw_row):
    order = np.argsort(-row_contrib)  # strongest risk-increasing contributions first
    reasons = []
    for i in order:
        if row_contrib[i] <= 0 or len(reasons) == 3:
            break
        feature = FEATURE_COLS[i]
        reasons.append({
            "feature": feature,
            "label": LABELS[feature],
            "value": native_value(raw_row[feature]),
            "train_median": train_references.get(feature),
            "direction": "increases_risk",
            "contribution_log_odds": float(row_contrib[i]),
        })
    return reasons

# COMMAND ----------

# 7) Score the complete replay-serving population, not merely closed/labeled test rows.
# A queue during the test replay range can contain carry-in tickets created before test start,
# so retain every ticket that is open at ANY point in [REPLAY_START, REPLAY_END).
REPLAY_START = pd.Timestamp("2025-10-22")
REPLAY_END = pd.Timestamp("2025-11-01")
created_ts = pd.to_datetime(all_pdf.created_date)
closed_ts = pd.to_datetime(all_pdf.closed_date)
serve_mask = (created_ts < REPLAY_END) & (closed_ts.isna() | (closed_ts >= REPLAY_START))
scored = all_pdf.loc[serve_mask].copy().reset_index(drop=True)

Xserve = encode(scored)[FEATURE_COLS]
dserve = xgb.DMatrix(Xserve, feature_names=FEATURE_COLS)
serve_raw = booster.predict(dserve, iteration_range=(0, best["n_rounds"]))
serve_pred = calibrator.predict(serve_raw) if calibrator is not None else serve_raw
serve_contribs = booster.predict(
    dserve, pred_contribs=True, iteration_range=(0, best["n_rounds"])
)[:, :-1]

val_final = calibrator.predict(val_raw) if calibrator is not None else val_raw
high_risk_threshold = float(np.quantile(val_final, 1 - TOP_DECILE))
scored["risk"] = serve_pred
scored["risk_band"] = np.where(scored["risk"] >= high_risk_threshold, "high", "normal")
scored["top_reasons"] = [
    json.dumps(top3_reasons(serve_contribs[i], scored.iloc[i])) for i in range(len(scored))
]
print(f"Replay-serving tickets scored: {len(scored):,}")

# COMMAND ----------

# 8) Export artifacts to the Volume. Download these into api/data/ for the offline app.
dbutils.fs.mkdirs(EXPORT_VOLUME)

# 8a) Native model artifact. The tuning trials are already tracked in MLflow above.
# Do not open another MLflow run here: an older client installed in an existing notebook
# environment can fail while serializing Databricks `PlanMetrics` when the run closes.
booster.save_model(f"{EXPORT_VOLUME}/model.json")
print(f"Saved native XGBoost model: {EXPORT_VOLUME}/model.json")

# 8b) Contextual /score defaults. Primary lookup is keyed by agency + creation hour + weekday,
# exactly as the API contract states; metadata carries deterministic fallback medians.
DEFAULT_LOOKUP_KEY = ["agency", "hour_of_day_created", "day_of_week"]
DEFAULT_VALUE_COLS = [c for c in NUMERIC if c not in DEFAULT_LOOKUP_KEY]
default_lookup = (
    train.groupby(DEFAULT_LOOKUP_KEY, dropna=False)[DEFAULT_VALUE_COLS]
    .median().reset_index()
)
default_lookup.to_json(f"{EXPORT_VOLUME}/score_defaults.json", orient="records")
global_numeric_defaults = {c: float(pd.to_numeric(train[c], errors="coerce").median()) for c in NUMERIC}

calibration_export = {"method": calibration_method, "validation_ece": float(val_ece)}
if calibrator is not None:
    calibration_export.update({
        "x_thresholds": calibrator.X_thresholds_.tolist(),
        "y_thresholds": calibrator.y_thresholds_.tolist(),
    })

metadata = {
    "model_version": MODEL_VERSION,
    "feature_cols": FEATURE_COLS,
    "numeric": NUMERIC,
    "categorical": CATEGORICAL,
    "encoders": encoders,
    "n_rounds": best["n_rounds"],
    "params": best["params"],
    "train_base_rate": b0_rate,
    "top_decile_threshold": high_risk_threshold,
    "default_lookup_key": DEFAULT_LOOKUP_KEY,
    "default_lookup_file": "score_defaults.json",
    "global_numeric_fallback": global_numeric_defaults,
    "default_provenance_message": "Missing operational values used frozen train medians for agency/hour/weekday; response must list each defaulted field.",
    "calibration": calibration_export,
    "replay_range": {"start": str(REPLAY_START), "end_exclusive": str(REPLAY_END)},
    "splits": {"train": len(train), "val": len(val), "test": len(test)},
}
with open(f"{EXPORT_VOLUME}/feature_metadata.json", "w") as f:
    json.dump(metadata, f, indent=2)

# 8c) Frozen SLA thresholds (for /meta and /score).
sla = spark.table(f"{CATALOG_SCHEMA}.ml_sla_targets_train").toPandas()
sla.to_json(f"{EXPORT_VOLUME}/sla_targets.json", orient="records")

# 8d) Data-quality + censoring evidence for the trust page.
rejects = spark.table(f"{CATALOG_SCHEMA}.silver_311_rejected")
reject_by_reason = json.loads(
    rejects.groupBy("_reject_reason").count().toPandas().to_json(orient="records")
)
dq_summary = {
    "bronze_rows": int(spark.table(f"{CATALOG_SCHEMA}.bronze_311_raw").count()),
    "silver_rows": int(spark.table(f"{CATALOG_SCHEMA}.silver_311").count()),
    "rejected_rows": int(rejects.count()),
    "rejects_by_reason": reject_by_reason,
    "label_states": {str(k): int(v) for k, v in all_pdf.label_state.value_counts(dropna=False).items()},
}
with open(f"{EXPORT_VOLUME}/dq_summary.json", "w") as f:
    json.dump(dq_summary, f, indent=2)

# 8e) Model card = baselines, calibration, SHAP, agency failures, DQ, and limitations.
model_card = {
    "model_version": MODEL_VERSION,
    "sla_definition": ("Portfolio SLA assumption: breach = resolution_hours > per-(agency, "
                       "complaint_type) 75th percentile of TRAIN closed tickets; groups below "
                       "200 training closures use agency P75. Not an official NYC target; "
                       "due_date was only ~0.5% populated."),
    "metrics": {"B0_base_rate": b0, "B1_group_lookup": b1, "XGBoost": xgb_metrics},
    "per_agency_pr_auc": per_agency,
    "calibration_method": calibration_export,
    "calibration": calibration,
    "global_shap": global_shap,
    "data_quality": dq_summary,
    "limitations": [
        "The delivered extract spans only 2025-08-29 through 2025-10-31, so the future test window is 10 days rather than a full month.",
        "Seven-day purge gaps are preserved; re-fetch the planned March-October window before claiming full-month evaluation.",
    ],
}
with open(f"{EXPORT_VOLUME}/model_card.json", "w") as f:
    json.dump(model_card, f, indent=2)

# 8f) Complete replay snapshot. Lifecycle timestamps permit created <= T < closed filtering;
# outcome columns stay in the artifact but the API hides them unless reveal_outcome=true.
# Databricks may attach a non-JSON-serializable `PlanMetrics` object to pandas attrs.
# PyArrow includes DataFrame attrs in Parquet metadata, so clear them before export.
scored.attrs.clear()
for column_name in scored.columns:
    scored[column_name].attrs.clear()

scored.to_parquet(f"{EXPORT_VOLUME}/scored_test.parquet", index=False)


# 8f) Gold aggregates as JSON for the overview/hotspots views (built in 02_gold).
for tbl, fname in [
    ("gold_metrics_agency", "kpis_agency.json"),
    ("gold_metrics_complaint", "hotspots_complaint.json"),
    ("gold_metrics_borough", "hotspots_borough.json"),
]:
    try:
        spark.table(f"{CATALOG_SCHEMA}.{tbl}").toPandas().to_json(
            f"{EXPORT_VOLUME}/{fname}", orient="records"
        )
    except Exception as e:
        print(f"  (skipped {tbl}: {e})")

print("\nExported artifacts to", EXPORT_VOLUME)
display(dbutils.fs.ls(EXPORT_VOLUME))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Headline result (read this cell's output for the model card / README)
# MAGIC XGBoost must beat B1 on PR-AUC and top-decile lift to justify itself. If it doesn't, that
# MAGIC gets reported honestly -- the operational baseline B1 is a real, shippable alternative.

# COMMAND ----------

summary = pd.DataFrame([b0, b1, xgb_metrics])[
    ["model", "pr_auc", "roc_auc", "brier", "top_decile_precision", "top_decile_lift", "top_decile_recall"]
]
print(summary.to_string(index=False))
beat = xgb_metrics["pr_auc"] > b1["pr_auc"]
print(f"\nXGBoost beats B1 on PR-AUC: {beat}  "
      f"({xgb_metrics['pr_auc']:.4f} vs {b1['pr_auc']:.4f})")
display(spark.createDataFrame(summary))
