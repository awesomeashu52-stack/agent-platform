# Data Profiler — Rules

## Output Rules
- Always include: table_name, row_count, column_count, and per-column stats.
- Per-column stats must include: name, data_type, nullable, null_pct, distinct_count, sample_values (max 3).
- Never include raw row data in the output.
- If row_count is unavailable from metadata, report as "unknown" — do not estimate.

## Execution Rules
- This agent uses PySpark only — no LLM calls.
- On Databricks with an active SparkSession, wire to real spark.sql() for accurate stats.
- Locally (no Spark), derive stats from the input metadata dict.
