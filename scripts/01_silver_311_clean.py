# Databricks notebook source
# MAGIC %md
# MAGIC # Phase 2: Silver `workspace.sla_triage.silver_311`
# MAGIC Cleans Bronze into a typed, validated table with explicit data-quality expectations.
# MAGIC
# MAGIC What this does:
# MAGIC - Parses `created_date` / `closed_date` from strings to timestamps.
# MAGIC - Normalizes text columns (trim, upper-case categoricals, blank -> NULL).
# MAGIC - Splits rows into **valid**, **rejected** (fail a hard expectation), and keeps
# MAGIC   still-open tickets (no `closed_date`) because those are what we later score.
# MAGIC - Computes `resolution_hours` for closed tickets.
# MAGIC - Writes rejected rows to `silver_311_rejected` with a `_reject_reason` so the
# MAGIC   data-quality report can quantify exactly what was dirty and why it was dropped.
# MAGIC
# MAGIC Expectations (why each exists):
# MAGIC - `created_date` must parse            -> a ticket with no valid start is unusable.
# MAGIC - `closed_date >= created_date`        -> closed-before-created is impossible; bad data.
# MAGIC - `resolution_hours <= 180 days`       -> guards against far-future close dates
# MAGIC                                           (source has closed_date up to 2026-09) that
# MAGIC                                           would poison SLA targets.
# MAGIC
# MAGIC Note: SLA breach is NOT defined here. Source `due_date` is only ~0.5% populated,
# MAGIC so the SLA target is derived in the Gold layer from resolution-time percentiles.

# COMMAND ----------

from pyspark.sql import functions as F

BRONZE = "workspace.sla_triage.bronze_311_raw"
SILVER = "workspace.sla_triage.silver_311"
REJECTS = "workspace.sla_triage.silver_311_rejected"

MAX_RESOLUTION_HOURS = 180 * 24  # 180 days; anything longer is treated as bad data

# COMMAND ----------

# 1) Type + normalize. Timestamps come in like 2025-09-01T13:45:00.000
b = spark.table(BRONZE)

norm = (
    b.select(
        F.col("unique_key").cast("string").alias("unique_key"),
        F.to_timestamp("created_date").alias("created_date"),
        F.to_timestamp("closed_date").alias("closed_date"),
        F.upper(F.trim("agency")).alias("agency"),
        F.trim("complaint_type").alias("complaint_type"),
        F.trim("descriptor").alias("descriptor"),
        F.upper(F.trim("borough")).alias("borough"),
        F.upper(F.trim("open_data_channel_type")).alias("channel"),
        F.initcap(F.trim("status")).alias("status"),
    )
    # blank strings -> NULL so downstream logic is clean
    .withColumn("descriptor", F.when(F.trim("descriptor") == "", None).otherwise(F.col("descriptor")))
    .withColumn("borough", F.when(F.col("borough").isin("UNSPECIFIED", ""), None).otherwise(F.col("borough")))
)

# is_open: no closed_date OR status is not a terminal "Closed"
norm = norm.withColumn(
    "is_open",
    F.col("closed_date").isNull() | (F.col("status") != "Closed"),
)

# resolution_hours only for tickets that actually closed
norm = norm.withColumn(
    "resolution_hours",
    F.when(
        F.col("closed_date").isNotNull(),
        (F.col("closed_date").cast("long") - F.col("created_date").cast("long")) / 3600.0,
    ),
)

# COMMAND ----------

# 2) Apply expectations. Tag each row with a reject reason (NULL = passes).
tagged = norm.withColumn(
    "_reject_reason",
    F.when(F.col("created_date").isNull(), F.lit("created_date_unparseable"))
     .when(
        F.col("closed_date").isNotNull() & (F.col("closed_date") < F.col("created_date")),
        F.lit("closed_before_created"),
     )
     .when(
        F.col("resolution_hours") > MAX_RESOLUTION_HOURS,
        F.lit("resolution_exceeds_180d"),
     )
     .otherwise(F.lit(None)),
)

valid = tagged.filter(F.col("_reject_reason").isNull()).drop("_reject_reason")
rejected = tagged.filter(F.col("_reject_reason").isNotNull())

# COMMAND ----------

# 3) Write both tables.
valid.write.mode("overwrite").saveAsTable(SILVER)
rejected.write.mode("overwrite").saveAsTable(REJECTS)

# COMMAND ----------

# 4) Verify + data-quality summary (copy these numbers into the DQ report).
bronze_n = spark.table(BRONZE).count()
silver_n = spark.table(SILVER).count()
rej = spark.table(REJECTS)
rej_n = rej.count()

print(f"Bronze rows:   {bronze_n:,}")
print(f"Silver valid:  {silver_n:,}")
print(f"Rejected:      {rej_n:,}  ({rej_n / bronze_n:.2%})")
print(f"Check total:   {silver_n + rej_n:,}  (should equal Bronze)")

print("\nRejected rows by reason:")
display(rej.groupBy("_reject_reason").count().orderBy(F.desc("count")))

print("\nOpen vs closed in Silver (open tickets are what we score later):")
display(spark.table(SILVER).groupBy("is_open").count())

print("\nResolution-hours distribution for CLOSED tickets (sanity check):")
display(
    spark.table(SILVER)
    .filter(~F.col("is_open"))
    .select(
        F.round(F.min("resolution_hours"), 1).alias("min_h"),
        F.round(F.expr("percentile_approx(resolution_hours, 0.5)"), 1).alias("median_h"),
        F.round(F.expr("percentile_approx(resolution_hours, 0.75)"), 1).alias("p75_h"),
        F.round(F.expr("percentile_approx(resolution_hours, 0.95)"), 1).alias("p95_h"),
        F.round(F.max("resolution_hours"), 1).alias("max_h"),
    )
)
