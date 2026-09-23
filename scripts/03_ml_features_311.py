# Databricks notebook source
# MAGIC %md
# MAGIC # Phase 4a: ML feature table `workspace.sla_triage.ml_features_tickets`
# MAGIC Turns Silver into ONE leakage-safe row per ticket, as known at `created_date`, with a
# MAGIC temporal `split` column and the frozen SLA label. This is the table the model trains on.
# MAGIC
# MAGIC ## Why this notebook exists separately from Gold
# MAGIC `02_gold_311_sla.py` computes SLA targets and dashboard metrics over **all** closed
# MAGIC tickets. That is correct for *descriptive* metrics ("what actually happened"), but it is
# MAGIC **leaky for modeling**: the P75 threshold defines the label, so letting val/test tickets
# MAGIC influence the threshold leaks the future into the label. Everything the model consumes is
# MAGIC therefore rebuilt here under strict train-only / point-in-time rules.
# MAGIC
# MAGIC ## Leakage controls (the headline discipline of this project)
# MAGIC 1. **Temporal split** by `created_date`, with purge gaps between train/val/test so a ticket
# MAGIC    near a boundary can't sit in two windows via its history features.
# MAGIC 2. **SLA thresholds frozen on TRAIN closed tickets only**, then applied unchanged to
# MAGIC    val/test. (Overrides the all-data targets from Gold.)
# MAGIC 3. **Backlog excludes the ticket itself** and counts only strictly-earlier events.
# MAGIC 4. **History features (28d) use only tickets CLOSED strictly before the current day.**
# MAGIC 5. **Complaint-type grouping fit on TRAIN frequencies only**; unseen types -> `OTHER_<agency>`.
# MAGIC 6. **No status / closure / resolution fields are used as features** (they are the answer).
# MAGIC 7. **No absolute calendar features** (month, day-of-year) because the split is temporal.
# MAGIC
# MAGIC ## Data window note
# MAGIC The delivered extract covers `created_date` 2025-08-29 .. 2025-10-31 (~600k rows, ~99%
# MAGIC already closed because the pull captured closures through 2026-09). The 8-month split in
# MAGIC the spec assumed a wider pull; the split below is compressed to fit the ~9 weeks we have.
# MAGIC
# MAGIC ## Output
# MAGIC - `ml_sla_targets_train`   -- train-only frozen SLA thresholds (agency x complaint_type)
# MAGIC - `ml_features_tickets`    -- one leakage-safe row per ticket + `split` + `breach` label

# COMMAND ----------

from pyspark.sql import functions as F
from pyspark.sql.window import Window

SILVER = "workspace.sla_triage.silver_311"
CATALOG_SCHEMA = "workspace.sla_triage"

MIN_GROUP_N = 200         # spec: smaller TRAIN groups fall back to agency P75
RARE_TYPE_MIN_N = 200     # complaint types below this many TRAIN rows collapse to OTHER_<agency>
DAY = 24 * 3600           # seconds
OBSERVATION_TS = "2026-09-18 00:00:00"  # frozen extract-as-of (source max close: 2026-09-17)

# The delivered extract is only 64 days, so a full-month test is impossible without
# re-fetching the wider source window. We preserve the more important safeguards here:
# a 7-day warm-up before train and TWO full 7-day purge gaps. Half-open [lo, hi).
SPLITS = {
    "train": ("2025-09-05", "2025-10-01"),   # warm-up: 2025-08-29 .. 09-04
    #  purge   2025-10-01 .. 2025-10-07 (7 full days)
    "val":   ("2025-10-08", "2025-10-15"),
    #  purge   2025-10-15 .. 2025-10-21 (7 full days)
    "test":  ("2025-10-22", "2025-11-01"),   # 10 future days; documented limitation
}

# COMMAND ----------

# 1) Load Silver and assign the temporal split. Tickets created in a purge gap get NULL
#    and are excluded from the feature table (but NOT from the event streams below --
#    a purged ticket still contributes to another ticket's backlog/history in reality).
silver = spark.table(SILVER)

split_col = (
    F.when(
        (F.col("created_date") >= F.lit(SPLITS["train"][0])) & (F.col("created_date") < F.lit(SPLITS["train"][1])),
        F.lit("train"),
    )
    .when(
        (F.col("created_date") >= F.lit(SPLITS["val"][0])) & (F.col("created_date") < F.lit(SPLITS["val"][1])),
        F.lit("val"),
    )
    .when(
        (F.col("created_date") >= F.lit(SPLITS["test"][0])) & (F.col("created_date") < F.lit(SPLITS["test"][1])),
        F.lit("test"),
    )
    .otherwise(F.lit(None))
)

tickets = silver.withColumn("split", split_col)

print("Split sizes (NULL = purge gap / out of window, excluded from modeling):")
display(tickets.groupBy("split").count().orderBy("split"))

# COMMAND ----------

# 2) SLA thresholds frozen on TRAIN closed tickets only.
#    threshold(agency, complaint_type) = P75 resolution_hours over TRAIN closed tickets
#    in that group (>= MIN_GROUP_N), else the agency-level TRAIN P75.
train_closed = tickets.filter((F.col("split") == "train") & (~F.col("is_open")))

grp = (
    train_closed.groupBy("agency", "complaint_type")
    .agg(
        F.count("*").alias("n_train_closed"),
        F.expr("percentile_approx(resolution_hours, 0.75)").alias("group_p75"),
    )
)
agy = (
    train_closed.groupBy("agency")
    .agg(F.expr("percentile_approx(resolution_hours, 0.75)").alias("agency_p75"))
)

sla_targets = (
    grp.join(agy, "agency", "left")
    .withColumn(
        "sla_target_hours",
        F.when(F.col("n_train_closed") >= MIN_GROUP_N, F.col("group_p75")).otherwise(F.col("agency_p75")),
    )
    .withColumn(
        "target_source",
        F.when(F.col("n_train_closed") >= MIN_GROUP_N, F.lit("group_p75")).otherwise(F.lit("agency_p75_fallback")),
    )
    .select("agency", "complaint_type", "n_train_closed", "sla_target_hours", "agency_p75", "target_source")
)

sla_targets.write.mode("overwrite").saveAsTable(f"{CATALOG_SCHEMA}.ml_sla_targets_train")
print(f"Train-only SLA target groups: {sla_targets.count():,}")
display(sla_targets.groupBy("target_source").count())

# Broadcastable lookup for later joins; carry agency_p75 to cover (agency, complaint_type)
# combos that never appeared in train.
targets_lookup = sla_targets.select("agency", "complaint_type", "sla_target_hours")
agency_fallback = sla_targets.select("agency", "agency_p75").dropDuplicates(["agency"])

# COMMAND ----------

# 3) Attach the frozen threshold + breach label.
#    Closed tickets are labeled from their realized duration. Still-open tickets are
#    right-censored: if their age at the frozen observation time already exceeds the SLA,
#    the positive outcome is known (label 1); younger open tickets remain unlabeled.
labeled = (
    tickets.join(F.broadcast(targets_lookup), ["agency", "complaint_type"], "left")
    .join(F.broadcast(agency_fallback), ["agency"], "left")
    .withColumn("sla_target_hours", F.coalesce(F.col("sla_target_hours"), F.col("agency_p75")))
    .drop("agency_p75")
    .withColumn(
        "age_at_observation_hours",
        (F.to_timestamp(F.lit(OBSERVATION_TS)).cast("long") - F.col("created_date").cast("long")) / 3600.0,
    )
    .withColumn(
        "breach",
        F.when(F.col("sla_target_hours").isNull(), F.lit(None).cast("int"))
         .when(~F.col("is_open"), (F.col("resolution_hours") > F.col("sla_target_hours")).cast("int"))
         .when(F.col("age_at_observation_hours") > F.col("sla_target_hours"), F.lit(1))
         .otherwise(F.lit(None).cast("int")),
    )
    .withColumn(
        "label_state",
        F.when(~F.col("is_open"), F.lit("closed_outcome"))
         .when(F.col("breach") == 1, F.lit("open_known_breach"))
         .otherwise(F.lit("open_censored")),
    )
)

train_rate = labeled.filter((F.col("split") == "train") & F.col("breach").isNotNull()).agg(F.avg("breach")).first()[0]
print(f"TRAIN breach rate with frozen threshold: {train_rate:.1%}")
print("Right-censoring states:")
display(labeled.groupBy("label_state").count())

# COMMAND ----------

# MAGIC %md
# MAGIC ## Event-stream backlog (features 1-2)
# MAGIC Backlog at creation = how many of that agency's tickets were already in flight the moment
# MAGIC this ticket was created. A ticket `j` is open just before ticket `i`'s creation time `t_i`
# MAGIC when `created_j < t_i` AND (`closed_j` is null OR `closed_j >= t_i`). Counting that
# MAGIC directly with a self-join is O(n^2). Instead:
# MAGIC
# MAGIC   open just before t_i  =  (# created strictly before t_i)  -  (# closed strictly before t_i)
# MAGIC
# MAGIC Both terms are cumulative counts over a single time-ordered stream, so each is one window
# MAGIC pass. Using **strictly before** (`rangeBetween(unboundedPreceding, -1 second)`) drops the
# MAGIC ticket's own creation event and any same-second ties, which keeps the feature self-excluded
# MAGIC and leakage-safe. The whole population feeds the stream (purged tickets included) because
# MAGIC real backlog doesn't care about our split.

# COMMAND ----------

def backlog_stream(df, part_cols, out_col):
    """Net open count (created_before - closed_before) at each ticket's created_date,
    partitioned by part_cols. Self-excluded via strict < on epoch seconds."""
    created_epoch = F.col("created_date").cast("long")
    closed_epoch = F.col("closed_date").cast("long")

    # Union of +1 (creation) and -1 (closure) events on a common time axis `ts`.
    creations = df.select(*part_cols, created_epoch.alias("ts"), F.lit(1).alias("delta"))
    closures = (
        df.filter(F.col("closed_date").isNotNull())
        .select(*part_cols, closed_epoch.alias("ts"), F.lit(-1).alias("delta"))
    )
    events = creations.unionByName(closures)

    w = Window.partitionBy(*part_cols).orderBy("ts").rangeBetween(Window.unboundedPreceding, -1)
    net = events.withColumn("net_open_before", F.sum("delta").over(w))

    # Keep only creation events (delta = 1) and read the net-open-before value for each.
    # Join back to tickets on (part_cols, created_epoch). Ties on the same second collapse,
    # so we take the max net value seen for that (key, second) -- a negligible coarsening.
    creation_net = (
        net.filter(F.col("delta") == 1)
        .groupBy(*part_cols, "ts")
        .agg(F.max("net_open_before").alias(out_col))
    )
    return creation_net.withColumnRenamed("ts", "created_epoch")


base = labeled.withColumn("created_epoch", F.col("created_date").cast("long"))

agency_backlog = backlog_stream(labeled, ["agency"], "agency_open_backlog")
complaint_backlog = backlog_stream(labeled, ["agency", "complaint_type"], "complaint_open_backlog")

feat = (
    base.join(agency_backlog, ["agency", "created_epoch"], "left")
    .join(complaint_backlog, ["agency", "complaint_type", "created_epoch"], "left")
    .fillna({"agency_open_backlog": 0, "complaint_open_backlog": 0})
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Arrivals & closures in the prior 24h (features 3-4)
# MAGIC Same single-stream trick. On a per-agency time-ordered union of creation and closure
# MAGIC events, a 24h look-back window ending strictly before `t_i` gives:
# MAGIC   - `agency_arrivals_24h`  = creation events in [t_i - 24h, t_i)
# MAGIC   - `agency_closures_24h`  = closure events in  [t_i - 24h, t_i)   (throughput signal)
# MAGIC Closures are indexed by `closed_date`, so only tickets that actually closed before t_i
# MAGIC count -- no future closures leak in.

# COMMAND ----------

created_epoch = F.col("created_date").cast("long")
closed_epoch = F.col("closed_date").cast("long")

arr_events = labeled.select("agency", created_epoch.alias("ts"), F.lit(1).alias("is_arrival"), F.lit(0).alias("is_closure"))
clo_events = (
    labeled.filter(F.col("closed_date").isNotNull())
    .select("agency", closed_epoch.alias("ts"), F.lit(0).alias("is_arrival"), F.lit(1).alias("is_closure"))
)
flow = arr_events.unionByName(clo_events)

w_24h = Window.partitionBy("agency").orderBy("ts").rangeBetween(-DAY, -1)
flow = (
    flow.withColumn("agency_arrivals_24h", F.sum("is_arrival").over(w_24h))
    .withColumn("agency_closures_24h", F.sum("is_closure").over(w_24h))
)

flow_at_creation = (
    flow.filter(F.col("is_arrival") == 1)
    .groupBy("agency", "ts")
    .agg(
        F.max("agency_arrivals_24h").alias("agency_arrivals_24h"),
        F.max("agency_closures_24h").alias("agency_closures_24h"),
    )
    .withColumnRenamed("ts", "created_epoch")
)

feat = (
    feat.join(flow_at_creation, ["agency", "created_epoch"], "left")
    .fillna({"agency_arrivals_24h": 0, "agency_closures_24h": 0})
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Rolling 28-day history (features 5-6)
# MAGIC For each (agency, complaint_type) and each creation day, the median resolution hours and
# MAGIC breach rate of tickets **closed in the prior 28 days**. Built as a daily aggregate keyed by
# MAGIC **closure day**, rolled over a 28-day window, then joined to tickets on `created_day` using
# MAGIC the window that ends on **created_day - 1** (strictly previous day). This is the single
# MAGIC biggest same-day leakage trap in the project, so the join is deliberately offset by a day.
# MAGIC Breach here uses the frozen TRAIN threshold.

# COMMAND ----------

# Closed outcomes available for history. `closed_day < created_day` in every join below
# is the hard point-in-time guard; same-day closures are intentionally excluded.
closed_all = (
    labeled.filter((~F.col("is_open")) & F.col("sla_target_hours").isNotNull())
    .withColumn("closed_day", F.to_date("closed_date"))
    .withColumn("closed_breach", (F.col("resolution_hours") > F.col("sla_target_hours")).cast("int"))
    .select("agency", "complaint_type", "closed_day", "resolution_hours", "closed_breach")
)

feat = feat.withColumn("created_day", F.to_date("created_date"))

# Build history for every group/day that actually has arrivals. This date spine prevents
# closure-free creation days from losing otherwise-valid prior history. The range join expands
# each closure to at most 28 subsequent creation days (roughly O(28*n), not O(n^2)).
group_days = feat.select("agency", "complaint_type", "created_day").distinct()
group_hist = (
    group_days.alias("d")
    .join(
        closed_all.alias("c"),
        (F.col("d.agency") == F.col("c.agency"))
        & (F.col("d.complaint_type") == F.col("c.complaint_type"))
        & (F.col("c.closed_day") >= F.date_sub(F.col("d.created_day"), 28))
        & (F.col("c.closed_day") < F.col("d.created_day")),
        "left",
    )
    .groupBy("d.agency", "d.complaint_type", "d.created_day")
    .agg(
        F.percentile_approx(F.col("c.resolution_hours"), 0.5).alias("hist_median_res_hrs_28d"),
        F.avg(F.col("c.closed_breach")).alias("hist_breach_rate_28d"),
    )
)

# Point-in-time agency fallback for sparse groups. This is also strictly prior-28d; unlike a
# full-training fallback it cannot leak later outcomes into early training rows.
agency_days = feat.select("agency", "created_day").distinct()
agency_hist = (
    agency_days.alias("d")
    .join(
        closed_all.alias("c"),
        (F.col("d.agency") == F.col("c.agency"))
        & (F.col("c.closed_day") >= F.date_sub(F.col("d.created_day"), 28))
        & (F.col("c.closed_day") < F.col("d.created_day")),
        "left",
    )
    .groupBy("d.agency", "d.created_day")
    .agg(
        F.percentile_approx(F.col("c.resolution_hours"), 0.5).alias("agency_hist_median_28d"),
        F.avg(F.col("c.closed_breach")).alias("agency_hist_breach_28d"),
    )
)

feat = (
    feat.join(group_hist, ["agency", "complaint_type", "created_day"], "left")
    .join(agency_hist, ["agency", "created_day"], "left")
    .withColumn(
        "hist_median_res_hrs_28d",
        F.coalesce("hist_median_res_hrs_28d", "agency_hist_median_28d", F.lit(0.0)),
    )
    .withColumn(
        "hist_breach_rate_28d",
        F.coalesce("hist_breach_rate_28d", "agency_hist_breach_28d", F.lit(0.0)),
    )
    .drop("agency_hist_median_28d", "agency_hist_breach_28d")
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Complaint-type grouping (feature 12) + calendar features (8-9) + categoricals (10-11)
# MAGIC Rare complaint types (< 200 TRAIN rows) collapse to `OTHER_<agency>`. Frequencies are
# MAGIC counted on TRAIN only, so val/test can't influence which types are "common", and any type
# MAGIC unseen in train also maps to OTHER. `hour_of_day` and `day_of_week` are cyclical and safe;
# MAGIC month / day-of-year are intentionally omitted (temporal split would make them leak).

# COMMAND ----------

train_type_counts = (
    tickets.filter(F.col("split") == "train")
    .groupBy("agency", "complaint_type")
    .count()
)
common_types = (
    train_type_counts.filter(F.col("count") >= RARE_TYPE_MIN_N)
    .select("agency", "complaint_type")
    .withColumn("_is_common", F.lit(True))
)

feat = (
    feat.join(F.broadcast(common_types), ["agency", "complaint_type"], "left")
    .withColumn(
        "complaint_type_grouped",
        F.when(F.col("_is_common") == True, F.col("complaint_type"))
         .otherwise(F.concat(F.lit("OTHER_"), F.col("agency"))),
    )
    .drop("_is_common")
    .withColumn("borough", F.coalesce(F.col("borough"), F.lit("UNKNOWN")))
    .withColumn("hour_of_day_created", F.hour("created_date"))
    .withColumn("day_of_week", F.dayofweek("created_date"))
)

# COMMAND ----------

# 4) Final projection: identifiers + 12 features + label + split. No status/closure/resolution
#    columns are exposed as features (they are kept only for the app's "reveal outcome" view and
#    are explicitly NOT in FEATURE_COLS below).
FEATURE_COLS = [
    "agency_open_backlog",       # 1
    "complaint_open_backlog",    # 2
    "agency_arrivals_24h",       # 3
    "agency_closures_24h",       # 4
    "hist_median_res_hrs_28d",   # 5
    "hist_breach_rate_28d",      # 6
    "sla_target_hours",          # 7
    "hour_of_day_created",       # 8
    "day_of_week",               # 9
    "borough",                   # 10
    "channel",                   # 11
    "complaint_type_grouped",    # 12
]

ml = feat.select(
    "unique_key", "split", "breach", "label_state", "is_open", "age_at_observation_hours",
    "agency", "complaint_type", "descriptor",       # display fields, NOT model features
    "created_date", "closed_date", "resolution_hours", "status",  # replay/reveal only
    *FEATURE_COLS,
)

ml.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{CATALOG_SCHEMA}.ml_features_tickets")

print(f"ml_features_tickets rows: {ml.count():,}")
print("\nLabeled (closed, non-null breach) rows by split -- what the model can learn/evaluate on:")
display(
    ml.filter(F.col("breach").isNotNull())
    .groupBy("split")
    .agg(F.count("*").alias("n"), F.round(F.avg("breach"), 3).alias("breach_rate"))
    .orderBy("split")
)

print("\nSample of the feature table:")
display(ml.limit(10))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Leakage self-checks (cheap asserts to eyeball in the output)
# MAGIC - Backlog must never be negative.
# MAGIC - No feature column may be null after fills (except the deliberately-null open-ticket label).
# MAGIC - Train/val/test creation-date ranges must be disjoint and ordered (purge gaps present).

# COMMAND ----------

neg_backlog = ml.filter((F.col("agency_open_backlog") < 0) | (F.col("complaint_open_backlog") < 0)).count()
print(f"Negative backlog rows (must be 0): {neg_backlog:,}")

null_feats = ml.select([
    F.sum(F.col(c).isNull().cast("int")).alias(c) for c in FEATURE_COLS
])
print("Null counts per feature column (all should be 0):")
display(null_feats)

print("Creation-date range per split (gaps between them = purge windows):")
display(
    ml.groupBy("split").agg(
        F.min("created_date").alias("min_created"),
        F.max("created_date").alias("max_created"),
    ).orderBy("min_created")
)
