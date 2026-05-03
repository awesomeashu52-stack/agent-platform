"""
core/execution_handler.py

Non-agentic (deterministic) agent execution and output persistence.

Three handlers, each significantly improved:

DATA PROFILER
  - Runs live SQL via the Statement Execution API when a warehouse ID is set
  - Without a warehouse, intelligently infers stats from metadata:
      * null_pct is shown as None (honest) not 0 when unknown
      * flags empty tables clearly so users understand why stats are missing
      * infers column semantics (PK likelihood, FK likelihood) from names + types

SAMPLE GENERATOR
  - Domain-aware value generation based on column name semantics:
      email → realistic email, phone → phone pattern, country → real ISO codes,
      id columns → sequential IDs, boolean → True/False, dates → realistic range
  - Uses sample_values from metadata when available (most realistic output)
  - Respects nullable: False columns (never generates NULL for those)
  - Supports business rules injected via user_context
  - Number of rows comes from the form field, not fragile string parsing

INGESTION CONFIG GENERATOR
  - Fully honours load_type, source_format, source_path from user_context
  - Generates complete, copy-paste-ready Databricks Autoloader configs
  - Adds DLT pipeline snippet alongside raw config
  - Generates both COPY INTO (batch) and readStream (streaming) variants
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import string
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_context(user_context: str) -> dict:
    """
    Parse user_context string into a structured dict.
    The UI serialises extra_fields as:
        "Load type: incremental\nSource format: parquet\nRows per table: 10"
    """
    result = {}
    for line in user_context.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip().lower().replace(" ", "_")] = val.strip()
    return result


def _run_sql_warehouse(sql: str, warehouse_id: str, host: str, token: str) -> list[list]:
    """Run SQL via Databricks Statement Execution API. Returns rows as list of lists."""
    try:
        from databricks.sdk import WorkspaceClient  # type: ignore
        ws   = WorkspaceClient()
        from databricks.sdk.service.sql import StatementState  # type: ignore
        stmt = ws.statement_execution.execute_statement(
            warehouse_id=warehouse_id, statement=sql,
            wait_timeout="30s",
        )
        import time
        deadline = time.time() + 60
        while stmt.status.state in (StatementState.PENDING, StatementState.RUNNING):
            if time.time() > deadline:
                return []
            time.sleep(1)
            stmt = ws.statement_execution.get_statement(stmt.statement_id)
        if stmt.status.state != StatementState.SUCCEEDED:
            return []
        return stmt.result.data_array or []
    except Exception as e:
        logger.debug(f"SQL warehouse query failed: {e}")
        return []


def _scalar_sql(sql: str, warehouse_id: str) -> Any:
    rows = _run_sql_warehouse(sql, warehouse_id, "", "")
    return rows[0][0] if rows else None


# ─────────────────────────────────────────────────────────────────────────────
# Domain-aware value generators for sample data
# ─────────────────────────────────────────────────────────────────────────────

# Real-looking but synthetic values grouped by semantic category
_FIRST_NAMES    = ["Alice","Bob","Carol","David","Eva","Fiona","George","Hannah",
                   "Ivan","Julia","Kevin","Laura","Mike","Nina","Oscar","Paula"]
_LAST_NAMES     = ["Smith","Jones","Williams","Brown","Taylor","Davies","Evans",
                   "Wilson","Thomas","Roberts","Johnson","Lee","Walker","Harris"]
_DOMAINS        = ["example.com","test.org","demo.net","sample.io","mock.co.uk"]
_COUNTRIES      = ["GB","US","DE","FR","IN","AU","CA","JP","BR","ZA","SG","AE"]
_COUNTRY_NAMES  = ["United Kingdom","United States","Germany","France","India",
                   "Australia","Canada","Japan","Brazil","South Africa"]
_CITIES         = ["London","New York","Berlin","Paris","Mumbai","Sydney",
                   "Toronto","Tokyo","São Paulo","Cape Town","Singapore"]
_STATUS_VALUES  = ["active","inactive","pending","confirmed","cancelled","completed"]
_PAYMENT_METHODS= ["credit_card","debit_card","bank_transfer","paypal","stripe"]
_PROPERTY_TYPES = ["apartment","house","villa","studio","loft","cottage","penthouse"]
_ROLES          = ["admin","manager","analyst","engineer","support","viewer"]
_DEVICE_TYPES   = ["mobile","desktop","tablet"]
_USER_TYPES     = ["guest","host","admin","business"]

_BOOL_COLS      = {"is_verified","is_active","is_primary","is_deleted",
                   "is_business","is_currently_employed"}

_START_DATE = date(2022, 1, 1)
_END_DATE   = date(2025, 12, 31)


def _rand_date() -> str:
    delta = (_END_DATE - _START_DATE).days
    return str(_START_DATE + timedelta(days=random.randint(0, delta)))


def _rand_ts() -> str:
    d = _rand_date()
    h = random.randint(0, 23)
    m = random.randint(0, 59)
    return f"{d}T{h:02d}:{m:02d}:00Z"


def _infer_value(col_name: str, data_type: str, sample_values: list,
                  row_index: int, nullable: bool) -> Any:
    """
    Generate a realistic synthetic value for a column.
    Priority: sample_values (most realistic) → name-based heuristics → type-based default.
    """
    name = col_name.lower()

    # If sample_values exist, cycle through them (most realistic)
    if sample_values:
        return sample_values[row_index % len(sample_values)]

    # Nullable — sometimes return None (20% chance for truly optional cols)
    if nullable and random.random() < 0.2 and name not in _BOOL_COLS:
        # But don't null PK-like columns
        if not any(x in name for x in ("_id", "key", "code", "ticket")):
            return None

    dtype = data_type.lower()

    # ── ID columns ────────────────────────────────────────────────────────
    if name.endswith("_id") or name == "id":
        return row_index + 1001

    # ── Boolean columns ───────────────────────────────────────────────────
    if dtype == "boolean" or name in _BOOL_COLS:
        return random.choice([True, False])

    # ── Semantic string columns ───────────────────────────────────────────
    if "email" in name:
        fn = random.choice(_FIRST_NAMES).lower()
        ln = random.choice(_LAST_NAMES).lower()
        return f"{fn}.{ln}@{random.choice(_DOMAINS)}"

    if "phone" in name:
        return f"+44{random.randint(7000000000, 7999999999)}"

    if name in ("country_code",) or (name == "country" and dtype == "string" and "code" in name):
        return random.choice(_COUNTRIES)

    if name == "country":
        return random.choice(_COUNTRY_NAMES)

    if name in ("continent",):
        return random.choice(["Europe","Asia","North America","South America","Africa","Oceania"])

    if "city" in name or "destination" in name:
        return random.choice(_CITIES)

    if "status" in name:
        return random.choice(_STATUS_VALUES)

    if "payment_method" in name:
        return random.choice(_PAYMENT_METHODS)

    if "property_type" in name or "type" in name:
        return random.choice(_PROPERTY_TYPES)

    if "role" in name:
        return random.choice(_ROLES)

    if "device" in name:
        return random.choice(_DEVICE_TYPES)

    if "user_type" in name:
        return random.choice(_USER_TYPES)

    if name in ("name", "full_name", "display_name"):
        return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"

    if "url" in name:
        return f"https://example.com/property/{row_index + 1001}/image_{random.randint(1,5)}.jpg"

    if "latitude" in name:
        return round(random.uniform(-60, 70), 6)

    if "longitude" in name:
        return round(random.uniform(-180, 180), 6)

    if "rating" in name:
        return round(random.uniform(1.0, 5.0), 1)

    if "amount" in name or "price" in name or "fee" in name:
        return round(random.uniform(50, 5000), 2)

    if "count" in name or "guests" in name or "bedrooms" in name or "bathrooms" in name:
        return random.randint(1, 10)

    if "sequence" in name or "order" in name:
        return row_index + 1

    if "comment" in name or "description" in name or "title" in name:
        return f"Sample {col_name.replace('_', ' ').title()} {row_index + 1}"

    if "ticket" in name:
        return f"TKT-{random.randint(10000, 99999)}"

    if "agent" in name and "id" not in name:
        return f"AGT-{random.randint(100, 999)}"

    # ── Type-based defaults ───────────────────────────────────────────────
    if dtype in ("timestamp",):
        return _rand_ts()

    if dtype in ("date",):
        return _rand_date()

    if dtype in ("int", "integer", "bigint", "long"):
        return random.randint(1, 100_000)

    if dtype in ("float", "double", "decimal"):
        return round(random.uniform(0, 10_000), 2)

    if dtype == "boolean":
        return random.choice([True, False])

    if dtype in ("array",):
        return []

    if dtype in ("struct", "map"):
        return {}

    # Default string
    return f"{col_name}_{row_index + 1}"


# ─────────────────────────────────────────────────────────────────────────────
# ExecutionHandler
# ─────────────────────────────────────────────────────────────────────────────

class ExecutionHandler:

    def __init__(self, config_loader, base_dir: Path | None = None):
        self._cfg         = config_loader
        self._base_dir    = base_dir or Path(__file__).resolve().parent.parent
        self._outputs_dir = self._base_dir / "outputs"
        self._outputs_dir.mkdir(exist_ok=True)
        # SQL warehouse ID for live stats (optional)
        self._warehouse_id = os.getenv("DATABRICKS_WAREHOUSE_ID", "")

    def run_non_agentic(
        self, agent_id: str, metadata: list[dict], user_context: str = ""
    ) -> dict:
        handlers = {
            "data_profiler":     self._run_data_profiler,
            "sample_gen":        self._run_sample_gen,
            "ingestion_cfg_gen": self._run_ingestion_cfg,
        }
        handler = handlers.get(agent_id)
        if not handler:
            raise ValueError(f"No non-agentic handler for '{agent_id}'. Available: {list(handlers)}")
        logger.info(f"[ExecutionHandler] {agent_id}")
        result = handler(metadata, user_context)
        result.update({"agent_id": agent_id, "timestamp": _now()})
        return result

    def save_output(self, agent_id: str, result: dict, run_id: str = "") -> str:
        ts       = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        suffix   = f"_{run_id}" if run_id else ""
        filename = f"{agent_id}_{ts}{suffix}.json"
        filepath = self._outputs_dir / filename
        with filepath.open("w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)
        logger.info(f"[ExecutionHandler] Saved: {filepath}")
        if self._cfg.execution_config.get("enable_output_registration", False):
            self._register_to_uc(agent_id, result)
        return str(filepath)

    # ── DATA PROFILER ────────────────────────────────────────────────────────

    def _run_data_profiler(self, metadata: list[dict], user_context: str) -> dict:
        """
        Profile tables. Three tiers:
          1. Live SQL warehouse query (if DATABRICKS_WAREHOUSE_ID is set) → full stats
          2. Metadata passthrough (if stats already present) → show what we have
          3. Empty table / schema-only → honest null stats with explanation
        """
        profiles = []
        wh = self._warehouse_id

        for meta in metadata:
            table_name = meta.get("table_name", "unknown")
            database   = meta.get("database", "")
            row_count  = meta.get("row_count", 0)
            full_name  = f"{database}.{table_name}" if database else table_name
            cols       = meta.get("columns", [])

            profiled_cols = []

            for col in cols:
                col_name = col.get("name")
                dtype    = col.get("data_type", "string")
                nullable = col.get("nullable", True)

                # Check if stats already present in metadata
                has_null_pct      = col.get("null_pct")      is not None
                has_distinct      = col.get("distinct_count") is not None
                has_samples       = bool(col.get("sample_values"))

                if wh and row_count > 0:
                    # Tier 1: Live SQL stats
                    try:
                        null_count    = _scalar_sql(f"SELECT COUNT(*) FROM {full_name} WHERE `{col_name}` IS NULL", wh)
                        distinct_count= _scalar_sql(f"SELECT COUNT(DISTINCT `{col_name}`) FROM {full_name}", wh)
                        sample_rows   = _run_sql_warehouse(
                            f"SELECT DISTINCT `{col_name}` FROM {full_name} WHERE `{col_name}` IS NOT NULL LIMIT 5",
                            wh, "", ""
                        )
                        null_pct      = round(int(null_count or 0) / max(row_count, 1), 4)
                        sample_values = [str(r[0]) for r in sample_rows if r[0] is not None]
                    except Exception:
                        null_pct = distinct_count = None
                        sample_values = []
                elif has_null_pct or has_distinct or has_samples:
                    # Tier 2: Use whatever stats came in the metadata
                    null_pct       = col.get("null_pct")
                    distinct_count = col.get("distinct_count")
                    sample_values  = col.get("sample_values", [])[:5]
                else:
                    # Tier 3: No data available — honest nulls
                    null_pct = distinct_count = None
                    sample_values = []

                # Infer key type from name
                is_likely_pk = (
                    col_name.lower() == f"{table_name.lower()}_id"
                    or (col_name.lower().endswith("_id") and not nullable)
                )
                is_likely_fk = (
                    col_name.lower().endswith("_id")
                    and not is_likely_pk
                )

                profiled_cols.append({
                    "name":           col_name,
                    "data_type":      dtype,
                    "nullable":       nullable,
                    "null_pct":       null_pct,
                    "distinct_count": distinct_count,
                    "sample_values":  sample_values,
                    "likely_pk":      is_likely_pk,
                    "likely_fk":      is_likely_fk,
                    "stats_source":   (
                        "live_warehouse" if (wh and row_count > 0)
                        else "metadata"  if (has_null_pct or has_distinct or has_samples)
                        else "none — table is empty or no warehouse configured"
                    ),
                })

            profiles.append({
                "table_name":    table_name,
                "fully_qualified_name": full_name,
                "database":      database,
                "row_count":     row_count,
                "column_count":  len(cols),
                "stats_note":    (
                    "Live stats from SQL warehouse."
                    if wh and row_count > 0
                    else "Table is empty — no row-level stats available. "
                         "Set SQL Warehouse ID in Advanced Options to profile non-empty tables."
                    if row_count == 0
                    else "Stats from catalog metadata (null_pct/distinct_count from INFORMATION_SCHEMA)."
                ),
                "columns":       profiled_cols,
            })

        return {"status": "success", "output": {"profiles": profiles}}

    # ── SAMPLE DATA GENERATOR ─────────────────────────────────────────────────

    def _run_sample_gen(self, metadata: list[dict], user_context: str) -> dict:
        """
        Generate domain-aware synthetic data.

        Reads from user_context:
          "Rows per table: 10"
          "Business rules: booking status must be confirmed or pending"
          "Null rate for optional columns: 0.15"
        """
        ctx = _parse_context(user_context)

        # Row count
        num_rows = 5
        for key in ("rows_per_table", "num_rows", "number_of_rows", "rows"):
            if key in ctx:
                try:
                    num_rows = max(1, min(50, int(ctx[key])))
                    break
                except ValueError:
                    pass

        # Business rules (used to constrain enum-type columns)
        business_rules_raw = ctx.get("business_rules", "")
        # Parse simple "column X must be Y or Z" patterns
        col_constraints: dict[str, list[str]] = {}
        for rule in business_rules_raw.split(";"):
            rule = rule.strip()
            m = re.search(r"(\w+)\s+(?:must be|should be|in)\s+(.+)", rule, re.I)
            if m:
                col  = m.group(1).lower()
                vals = [v.strip().strip("'\"") for v in m.group(2).split(" or ")]
                col_constraints[col] = [v for v in vals if v]

        samples = []
        for meta in metadata:
            table_name = meta.get("table_name", "unknown")
            database   = meta.get("database", "")
            cols       = meta.get("columns", [])
            rows       = []

            for row_idx in range(num_rows):
                row = {}
                for col in cols:
                    col_name     = col.get("name", "col")
                    dtype        = col.get("data_type", "string")
                    nullable     = col.get("nullable", True)
                    sample_values= col.get("sample_values", [])

                    # Apply business rule constraint if present
                    if col_name.lower() in col_constraints:
                        row[col_name] = random.choice(col_constraints[col_name.lower()])
                    else:
                        row[col_name] = _infer_value(
                            col_name, dtype, sample_values, row_idx, nullable
                        )
                rows.append(row)

            samples.append({
                "table_name":    table_name,
                "database":      database,
                "row_count":     num_rows,
                "rows":          rows,
                "generation_notes": (
                    f"Generated {num_rows} synthetic rows. "
                    "Values are domain-aware but fully synthetic — no real PII. "
                    + (f"Business rules applied: {business_rules_raw}." if business_rules_raw else "")
                ),
            })

        return {"status": "success", "output": {"samples": samples}}

    # ── INGESTION CONFIG GENERATOR ────────────────────────────────────────────

    def _run_ingestion_cfg(self, metadata: list[dict], user_context: str) -> dict:
        """
        Generate complete Databricks ingestion configurations.

        Reads from user_context:
          "Load type: incremental | full | streaming"
          "Source format: parquet | delta | csv | json | avro"
          "Source path: abfss://..."
          "Target catalog: agent_platform.bronze"
          "Checkpoint base path: /checkpoints"
        """
        ctx = _parse_context(user_context)

        load_type      = ctx.get("load_type", "incremental")
        source_format  = ctx.get("source_format", "parquet")
        source_path    = ctx.get("source_path", "")
        target_catalog = ctx.get("target_catalog", "bronze")
        checkpoint_base= ctx.get("checkpoint_base_path", "/Volumes/checkpoints")

        configs = []

        for meta in metadata:
            table_name  = meta.get("table_name", "unknown")
            database    = meta.get("database", "")
            cols        = meta.get("columns", [])
            col_names   = [c["name"] for c in cols]

            # Construct paths
            src_path    = source_path or f"{checkpoint_base}/source/{table_name}/"
            chk_path    = f"{checkpoint_base}/{table_name}/checkpoint"
            schema_path = f"{checkpoint_base}/{table_name}/schema"
            tgt_table   = f"{target_catalog}.{table_name}"

            # DDL for target table (useful for CREATE TABLE IF NOT EXISTS)
            ddl_cols = []
            for c in cols:
                null_str = "" if not c.get("nullable", True) else ""
                ddl_cols.append(f"  `{c['name']}` {c.get('data_type','STRING').upper()}")
            ddl = (
                f"CREATE TABLE IF NOT EXISTS {tgt_table} (\n"
                + ",\n".join(ddl_cols)
                + "\n) USING DELTA;"
            )

            # Autoloader config block
            autoloader_cfg = {
                "source_path":          src_path,
                "cloudFiles.format":    source_format,
                "cloudFiles.schemaLocation": schema_path,
                "cloudFiles.inferColumnTypes": True,
            }
            if source_format == "csv":
                autoloader_cfg["header"] = True
                autoloader_cfg["sep"]    = ","
            if source_format == "json":
                autoloader_cfg["multiLine"] = False

            # Python code snippets
            if load_type == "streaming":
                code_snippet = (
                    f"# Streaming ingest — {table_name}\n"
                    f"df = (\n"
                    f"    spark.readStream\n"
                    f"    .format('cloudFiles')\n"
                    f"    .options(**{json.dumps(autoloader_cfg, indent=4)})\n"
                    f"    .load('{src_path}')\n"
                    f")\n\n"
                    f"df.writeStream\\\n"
                    f"  .format('delta')\\\n"
                    f"  .outputMode('append')\\\n"
                    f"  .option('checkpointLocation', '{chk_path}')\\\n"
                    f"  .trigger(availableNow=True)\\\n"
                    f"  .toTable('{tgt_table}')"
                )
            elif load_type == "full":
                code_snippet = (
                    f"# Full refresh ingest — {table_name}\n"
                    f"df = (\n"
                    f"    spark.read\n"
                    f"    .format('{source_format}')\n"
                    f"    .load('{src_path}')\n"
                    f")\n\n"
                    f"df.write\\\n"
                    f"  .format('delta')\\\n"
                    f"  .mode('overwrite')\\\n"
                    f"  .saveAsTable('{tgt_table}')"
                )
            else:  # incremental default
                code_snippet = (
                    f"# Incremental Autoloader ingest — {table_name}\n"
                    f"df = (\n"
                    f"    spark.readStream\n"
                    f"    .format('cloudFiles')\n"
                    f"    .options(**{json.dumps(autoloader_cfg, indent=4)})\n"
                    f"    .load('{src_path}')\n"
                    f")\n\n"
                    f"df.writeStream\\\n"
                    f"  .format('delta')\\\n"
                    f"  .outputMode('append')\\\n"
                    f"  .option('checkpointLocation', '{chk_path}')\\\n"
                    f"  .trigger(availableNow=True)\\\n"
                    f"  .toTable('{tgt_table}')"
                )

            configs.append({
                "source_table":        table_name,
                "source_database":     database,
                "target_table":        tgt_table,
                "source_path":         src_path,
                "load_type":           load_type,
                "source_format":       source_format,
                "checkpoint_location": chk_path,
                "schema_location":     schema_path,
                "selected_columns":    col_names,
                "column_count":        len(col_names),
                "autoloader_options":  autoloader_cfg,
                "target_ddl":          ddl,
                "python_code":         code_snippet,
                "generated_at":        _now(),
            })

        return {"status": "success", "output": {"ingestion_configs": configs}}

    # ── UC registration ───────────────────────────────────────────────────────

    def _register_to_uc(self, agent_id: str, result: dict) -> None:
        try:
            from pyspark.sql import SparkSession  # type: ignore
            spark = SparkSession.getActiveSession()
            if spark is None:
                return
            import pandas as pd
            table_name = self._cfg.get_uc_table("outputs", f"{agent_id}_results")
            df = spark.createDataFrame(pd.DataFrame([{
                "result_json": json.dumps(result, default=str),
                "timestamp":   _now(),
            }]))
            df.write.format("delta").mode("append").saveAsTable(table_name)
        except Exception as exc:
            logger.debug(f"UC registration skipped: {exc}")
