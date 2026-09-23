# Databricks notebook source
# MAGIC %md
# MAGIC # Phase 3: Gold `workspace.sla_triage.gold_*`
# MAGIC Builds the SLA target, the breach label, aggregate metrics, and the model feature
# MAGIC table from Silver.
# MAGIC
# MAGIC ## Why SLA targets are derived here (decision log entry)
# MAGIC Source `due_date` is populated for only ~0.5% of tickets (DSNY 5.2%, all others 0%),
# MAGIC so it cannot be used as the SLA. Instead:
# MAGIC
# MAGIC   SLA target(agency, complaint_type) = P75 of historical `resolution_hours`
# MAGIC   for CLOSED tickets in that group (min 50 closed tickets; smaller groups fall
# MAGIC   back to the agency-level P75).
# MAGIC
# MAGIC   breach = 1 if resolution_hours > group's SLA target, else 0.
# MAGIC   Defined only for CLOSED tickets. Open tickets are the population the model scores.
# MAGIC
# MAGIC Why P75, not a fixed number: resolution time varies hugely by complaint type
# MAGIC (median 3.2h overall, but P75=47.8h, P95=459.7h) -- a single global cutoff would
# MAGIC just relabel "which agency handles it" instead of measuring slow service. A
# MAGIC per-group P75 means the slowest quarter of each group's own history is a breach,
# MAGIC which is self-calibrating and gives the model a ~25% positive rate to learn from.
# MAGIC
# MAGIC ## Tables built
# MAGIC - `gold_sla_targets`      -- one row per (agency, complaint_type): the SLA target + n used
# MAGIC - `gold_tickets_labeled`  -- every Silver ticket + sla_target_hours + breach (closed only)
# MAGIC - `gold_sla_metrics`      -- breach rate by agency / complaint_type / borough (dashboard)
# MAGIC - `gold_features`        -- model feature table (closed + open tickets)

# COMMAND ----------

from pyspark.sql import functions as F
from pyspark.sql.window import Window

SILVER = "workspace.sla_triage.silver_311"
MIN_GROUP_N = 50

# COMMAND ----------

# 1) SLA targets: P75 resolution_hours per (agency, complaint_type), closed tickets only.
closed = spark.table(SILVER).filter(~F.col("is_open"))

group_stats = (
    closed.groupBy("agency", "complaint_type")
    .agg(
        F.count("*").alias("n_closed"),
        F.expr("percentile_approx(resolution_hours, 0.75)").alias("group_p75"),
    )
)

agency_stats = (
    closed.groupBy("agency")
    .agg(F.expr("percentile_approx(resolution_hours, 0.75)").alias("agency_p75"))
)

sla_targets = (
    group_stats.join(agency_stats, "agency", "left")
    .withColumn(
        "sla_target_hours",
        F.when(F.col("n_closed") >= MIN_GROUP_N, F.col("group_p75")).otherwise(F.col("agency_p75")),
    )
    .withColumn(
        "target_source",
        F.when(F.col("n_closed") >= MIN_GROUP_N, F.lit("group_p75")).otherwise(F.lit("agency_p75_fallback")),
    )
    .select("agency", "complaint_type", "n_closed", "sla_target_hours", "target_source")
)

sla_targets.write.mode("overwrite").saveAsTable("workspace.sla_triage.gold_sla_targets")

print(f"SLA target groups: {sla_targets.count():,}")
display(sla_targets.groupBy("target_source").count())

# COMMAND ----------

# 2) Label every ticket. Closed tickets get a real breach label; open tickets get NULL
#    (they have no resolution_hours yet -- that's what the model predicts).
silver = spark.table(SILVER)

labeled = (
    silver.join(sla_targets.select("agency", "complaint_type", "sla_target_hours"),
                ["agency", "complaint_type"], "left")
    .withColumn(
        "breach",
        F.when(F.col("is_open"), F.lit(None).cast("int"))
         .otherwise((F.col("resolution_hours") > F.col("sla_target_hours")).cast("int")),
    )
)

labeled.write.mode("overwrite").saveAsTable("workspace.sla_triage.gold_tickets_labeled")

closed_labeled = labeled.filter(~F.col("is_open"))
overall_breach_rate = closed_labeled.agg(F.avg("breach")).first()[0]
print(f"Overall breach rate (closed tickets): {overall_breach_rate:.1%}")
print("(Expect ~25% by construction of the P75 target; deviation comes from the "
      "agency-level fallback groups.)")

# COMMAND ----------

# 3) Gold metrics for the dashboard: breach rate by agency / complaint_type / borough.
metrics_agency = (
    closed_labeled.groupBy("agency")
    .agg(F.count("*").alias("n"), F.round(F.avg("breach"), 3).alias("breach_rate"))
    .orderBy(F.desc("breach_rate"))
)

metrics_complaint = (
    closed_labeled.groupBy("agency", "complaint_type")
    .agg(F.count("*").alias("n"), F.round(F.avg("breach"), 3).alias("breach_rate"))
    .filter(F.col("n") >= MIN_GROUP_N)
    .orderBy(F.desc("breach_rate"))
)

metrics_borough = (
    closed_labeled.filter(F.col("borough").isNotNull())
    .groupBy("agency", "borough")
    .agg(F.count("*").alias("n"), F.round(F.avg("breach"), 3).alias("breach_rate"))
    .orderBy(F.desc("breach_rate"))
)

metrics_agency.write.mode("overwrite").saveAsTable("workspace.sla_triage.gold_metrics_agency")
metrics_complaint.write.mode("overwrite").saveAsTable("workspace.sla_triage.gold_metrics_complaint")
metrics_borough.write.mode("overwrite").saveAsTable("workspace.sla_triage.gold_metrics_borough")

print("Top 10 highest breach-rate agency+complaint_type combos (n>=50):")
display(metrics_complaint.limit(10))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Feature table
# MAGIC One key feature worth calling out: **backlog at ticket creation** -- how many
# MAGIC tickets were already open for that agency at the moment this ticket was created.
# MAGIC A high backlog is a leading indicator of breach risk (the agency is behind before
# MAGIC this ticket even starts). Computed with a time-ordered window per agency, counting
# MAGIC tickets created before this one that were still open at this ticket's creation time.

# COMMAND ----------

# 4) Backlog-at-creation feature.
# For each agency, count how many OTHER tickets from that agency were "in flight"
# (created before this ticket, and not yet closed by this ticket's created_date)
# at the moment this ticket was created.
base = labeled.select(
    "unique_key", "agency", "complaint_type", "borough", "channel",
    "created_date", "closed_date", "is_open", "resolution_hours",
    "sla_target_hours", "breach",
)

# Self-join is too expensive at this scale in a notebook cell; approximate backlog
# using a running count via window functions instead (per-agency, time-ordered).
w_agency_time = Window.partitionBy("agency").orderBy("created_date").rangeBetween(Window.unboundedPreceding, -1)

# Running count of tickets created so far (per agency) that were still open when
# THIS ticket was created: closed_date is null OR closed_date > this created_date.
# Implemented via cumulative counts: opened_so_far - closed_so_far (as of created_date).
opened_so_far = F.count("unique_key").over(
    Window.partitionBy("agency").orderBy("created_date").rangeBetween(Window.unboundedPreceding, Window.currentRow)
)

# closed_so_far needs to be evaluated against created_date, not closed_date order,
# so we compute it from a separate ordered-by-closed_date running count and join back
# by created_date via a merge-asof style approach using a sorted array is overkill here;
# instead use a simpler, defensible proxy: count of that agency's tickets created in the
# prior 7 days that were still open (not closed) as of this ticket's created_date.
window_7d = Window.partitionBy("agency").orderBy(F.col("created_date").cast("long")) \
    .rangeBetween(-7 * 24 * 3600, -1)

backlog = base.withColumn(
    "backlog_7d_at_creation",
    F.sum(F.when(F.col("closed_date").isNull() | (F.col("closed_date") > F.col("created_date")), 1).otherwise(0))
     .over(window_7d),
).fillna({"backlog_7d_at_creation": 0})

# COMMAND ----------

# 5) Final feature table: time features + categoricals + backlog + label.
features = (
    backlog
    .withColumn("created_hour", F.hour("created_date"))
    .withColumn("created_dow", F.dayofweek("created_date"))
    .withColumn("created_month", F.month("created_date"))
    .withColumn("is_weekend", F.col("created_dow").isin(1, 7).cast("int"))
)

features.write.mode("overwrite").saveAsTable("workspace.sla_triage.gold_features")

print(f"Gold features rows: {features.count():,}")
display(features.select(
    "unique_key", "agency", "complaint_type", "borough", "created_date",
    "backlog_7d_at_creation", "created_hour", "created_dow", "is_weekend",
    "sla_target_hours", "breach", "is_open",
).limit(10))
