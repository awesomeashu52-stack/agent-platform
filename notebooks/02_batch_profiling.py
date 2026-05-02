# Databricks notebook source
# =============================================================================
# 02_batch_profiling.py — Batch profile all tables in a schema
#
# Reads table schemas from Unity Catalog and runs the data_profiler
# agent across every table, registering results to UC Delta.
# =============================================================================

# COMMAND ----------

# MAGIC %pip install pyyaml --quiet

# COMMAND ----------

import sys
sys.path.insert(0, "/Workspace/Users/awesomeashu52@gmail.com/agent_platform")

from core.orchestrator import Orchestrator
from pyspark.sql import SparkSession

orch  = Orchestrator()
spark = SparkSession.getActiveSession()

# COMMAND ----------

TARGET_CATALOG = "crm_prod"
TARGET_SCHEMA  = "raw"

tables = spark.sql(f"SHOW TABLES IN {TARGET_CATALOG}.{TARGET_SCHEMA}").collect()
print(f"Found {len(tables)} tables to profile")

# COMMAND ----------

for row in tables:
    table_name = row["tableName"]
    full_name  = f"{TARGET_CATALOG}.{TARGET_SCHEMA}.{table_name}"

    # Get schema from Spark catalog
    schema_df = spark.sql(f"DESCRIBE TABLE {full_name}")
    columns   = [
        {"name": r["col_name"], "data_type": r["data_type"], "nullable": True}
        for r in schema_df.collect()
        if not r["col_name"].startswith("#")
    ]

    # Get row count efficiently
    row_count = spark.sql(f"SELECT COUNT(*) AS n FROM {full_name}").first()["n"]

    metadata = [{
        "table_name": table_name,
        "database": f"{TARGET_CATALOG}.{TARGET_SCHEMA}",
        "row_count": row_count,
        "columns": columns,
    }]

    result = orch.run(agent_id="data_profiler", metadata=metadata)
    print(f"  ✅ {table_name}: {result.status} | saved to {result.output_path}")

print(f"\nBatch profiling complete. Session cost: ${orch.session_cost()['cumulative_cost_usd']:.4f}")