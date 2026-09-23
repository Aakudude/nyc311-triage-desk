# Databricks notebook source
# MAGIC %md
# MAGIC # Phase 1: Bronze `workspace.sla_triage.bronze_311_raw`
# MAGIC Raw fidelity only: all columns as strings, no cleaning, no dedupe, no filtering.
# MAGIC Adds `_ingested_at` and `_source_file`.

# COMMAND ----------

# 1) Schema + Volume (safe to re-run). Then upload the CSV via
#    Catalog > workspace > sla_triage > raw > "Upload to this volume".
spark.sql("CREATE SCHEMA IF NOT EXISTS workspace.sla_triage")
spark.sql("CREATE VOLUME IF NOT EXISTS workspace.sla_triage.raw")
display(dbutils.fs.ls("/Volumes/workspace/sla_triage/raw/"))

# COMMAND ----------

# 2) Build Bronze. Update FILE if your local file name differs.
from pyspark.sql import functions as F

FILE = "/Volumes/workspace/sla_triage/raw/nyc311_2025-03-01_2025-10-31.csv"
TARGET = "workspace.sla_triage.bronze_311_raw"

raw = (spark.read.format("csv")
       .option("header", True)
       .option("inferSchema", False)   # every column stays a string
       .option("multiLine", True)
       .option("escape", '"')
       .load(FILE))

bronze = raw.select(
    "*",
    F.current_timestamp().alias("_ingested_at"),
    F.col("_metadata.file_path").alias("_source_file"),
)
bronze.write.mode("overwrite").saveAsTable(TARGET)

# COMMAND ----------

# 3) Verify: compare these numbers with data/profile_report.txt from the local profile.
b = spark.table(TARGET)
print("Bronze rows:", b.count(), "| columns:", len(b.columns))
print("Non-string columns:", [(n, t) for n, t in b.dtypes if t != "string" and n != "_ingested_at"])

data_cols = [c for c in b.columns if not c.startswith("_")]
blank_counts = b.select([
    F.sum(F.when(F.col(c).isNull() | (F.trim(F.col(c)) == ""), 1).otherwise(0)).alias(c)
    for c in data_cols
])
display(blank_counts)   # Spark reads empty CSV fields as NULL; should equal local blank counts
display(b.limit(5))
