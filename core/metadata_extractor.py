"""
core/metadata_extractor.py

Extracts table metadata from three sources — all work inside a Databricks App
(no Spark session required for modes A and C):

  A. Unity Catalog / Hive metastore
     Uses the Databricks SDK catalog APIs (REST) — no Spark needed.
     For column-level stats (null_pct, distinct_count, sample_values) it runs
     SQL statements via the SQL Statement Execution API if a warehouse ID is
     configured, otherwise returns schema-only metadata.

  B. Cloud file path (S3 / ADLS / GCS)
     Requires Spark. Only available when running on a cluster notebook,
     not inside a Databricks App. The UI shows a clear message when unavailable.

  C. Manual JSON
     Always available. Validates and normalises user-provided metadata.

All three modes produce identical output — agents see the same structure
regardless of how the metadata was obtained.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Environment probes
# ─────────────────────────────────────────────────────────────────────────────

def _spark_available() -> bool:
    try:
        from pyspark.sql import SparkSession          # type: ignore
        return SparkSession.getActiveSession() is not None
    except Exception:
        return False


def _sdk_available() -> bool:
    try:
        from databricks.sdk import WorkspaceClient   # type: ignore
        WorkspaceClient().current_user.me()
        return True
    except Exception:
        return False


def _get_sdk():
    from databricks.sdk import WorkspaceClient       # type: ignore
    return WorkspaceClient()


def _get_spark():
    from pyspark.sql import SparkSession             # type: ignore
    return SparkSession.getActiveSession()


def _normalise_dtype(dtype: str) -> str:
    dtype = str(dtype).lower().strip()
    for prefix in ("decimal","array","map","struct"):
        if dtype.startswith(prefix):
            return prefix
    return dtype


# ─────────────────────────────────────────────────────────────────────────────
# Cross-table relationship detector
# ─────────────────────────────────────────────────────────────────────────────

class RelationshipDetector:
    """
    Infers FK relationships across tables using column-name heuristics.
    Example: orders.customer_id → customer.customer_id (high confidence)
    """

    FK_PATTERN = re.compile(r"^(.+?)_id$", re.IGNORECASE)

    def detect(self, tables: list[dict]) -> list[dict]:
        table_index = {t["table_name"].lower(): t for t in tables}

        for t in tables:
            t.setdefault("relationships", [])
            row_count = t.get("row_count", 0)

            for col in t.get("columns", []):
                col.setdefault("is_primary_key", False)
                col.setdefault("is_foreign_key",  False)
                col.setdefault("fk_references",   None)

                col_name   = col["name"].lower()
                col_dcount = col.get("distinct_count") or 0
                own_pk     = t["table_name"].lower() + "_id"

                # PK heuristic: <table>_id column where distinct = row_count
                if col_name == own_pk and row_count > 0 and col_dcount == row_count:
                    col["is_primary_key"] = True

                m = self.FK_PATTERN.match(col_name)
                if not m:
                    continue

                ref_name = m.group(1).lower()
                if ref_name == t["table_name"].lower():
                    continue   # that's the PK, not a FK

                if ref_name in table_index:
                    ref_table = table_index[ref_name]
                    ref_pk    = ref_name + "_id"
                    ref_cols  = [c["name"].lower() for c in ref_table.get("columns", [])]
                    confidence = "high" if ref_pk in ref_cols else "medium"
                    to_col     = ref_pk if ref_pk in ref_cols else "?"
                    col["is_foreign_key"] = True
                    col["fk_references"]  = f"{ref_table['table_name']}.{to_col}"
                    t["relationships"].append({
                        "from_column": col["name"],
                        "to_table":    ref_table["table_name"],
                        "to_column":   to_col,
                        "confidence":  confidence,
                    })
                else:
                    # Referenced table not in current set
                    col["is_foreign_key"] = True
                    col["fk_references"]  = f"{ref_name} (not loaded)"
                    t["relationships"].append({
                        "from_column": col["name"],
                        "to_table":    ref_name,
                        "to_column":   ref_name + "_id",
                        "confidence":  "low",
                    })

        return tables


# ─────────────────────────────────────────────────────────────────────────────
# SQL Statement Execution API helper (optional stats enrichment)
# ─────────────────────────────────────────────────────────────────────────────

class _SQLExecutor:
    """
    Runs SQL via the Databricks SQL Statement Execution REST API.
    Used to fetch column stats (null_pct, distinct_count, sample_values)
    when a SQL warehouse ID is available.

    Warehouse ID is read from env var DATABRICKS_WAREHOUSE_ID or passed in.
    If not configured, stats are left as None and the schema-only path is used.
    """

    def __init__(self, ws, warehouse_id: str | None = None):
        self._ws           = ws
        self._warehouse_id = (
            warehouse_id
            or os.getenv("DATABRICKS_WAREHOUSE_ID", "")
        ).strip()

    @property
    def available(self) -> bool:
        return bool(self._warehouse_id)

    def run(self, sql: str, timeout_seconds: int = 30) -> list[list]:
        """Execute SQL and return rows as list of lists. Returns [] on error."""
        if not self.available:
            return []
        try:
            from databricks.sdk.service.sql import StatementState  # type: ignore
            stmt = self._ws.statement_execution.execute_statement(
                warehouse_id=self._warehouse_id,
                statement=sql,
                wait_timeout=f"{timeout_seconds}s",
            )
            # Poll until done
            deadline = time.time() + timeout_seconds
            while stmt.status.state in (
                StatementState.PENDING, StatementState.RUNNING
            ):
                if time.time() > deadline:
                    logger.warning(f"SQL timed out: {sql[:80]}")
                    return []
                time.sleep(1)
                stmt = self._ws.statement_execution.get_statement(stmt.statement_id)

            if stmt.status.state != StatementState.SUCCEEDED:
                logger.warning(f"SQL failed ({stmt.status.state}): {sql[:80]}")
                return []

            if not stmt.result or not stmt.result.data_array:
                return []
            return stmt.result.data_array           # list of lists

        except Exception as e:
            logger.debug(f"SQL execution error: {e}")
            return []

    def scalar(self, sql: str) -> Any:
        """Return the first cell of the first row, or None."""
        rows = self.run(sql)
        return rows[0][0] if rows else None

    def column_stats(self, full_table: str, col: str, row_count: int) -> dict:
        """Fetch null_pct, distinct_count, and sample_values for one column."""
        stats = {"null_pct": None, "distinct_count": None, "sample_values": []}
        if not self.available or row_count == 0:
            return stats

        try:
            null_count = int(self.scalar(
                f"SELECT COUNT(*) FROM {full_table} WHERE `{col}` IS NULL"
            ) or 0)
            stats["null_pct"] = round(null_count / max(row_count, 1), 4)
        except Exception:
            pass

        try:
            stats["distinct_count"] = int(self.scalar(
                f"SELECT COUNT(DISTINCT `{col}`) FROM {full_table}"
            ) or 0)
        except Exception:
            pass

        try:
            rows = self.run(
                f"SELECT DISTINCT `{col}` FROM {full_table} "
                f"WHERE `{col}` IS NOT NULL LIMIT 5"
            )
            stats["sample_values"] = [str(r[0]) for r in rows if r[0] is not None]
        except Exception:
            pass

        return stats


# ─────────────────────────────────────────────────────────────────────────────
# Mode A — Unity Catalog / Hive metastore via SDK (no Spark needed)
# ─────────────────────────────────────────────────────────────────────────────

class CatalogExtractor:
    """
    Extracts table schemas using the Databricks SDK catalog REST APIs.
    Works inside Databricks Apps, notebooks, and local dev.

    Column stats (null_pct, distinct_count, sample_values) are fetched
    via the SQL Statement Execution API if DATABRICKS_WAREHOUSE_ID is set.
    Without it, schema-only metadata is returned (still fully usable by agents).

    Supports both Unity Catalog (3-level: catalog.schema.table)
    and Hive metastore (2-level: schema.table → hive_metastore.schema.table).
    """

    def __init__(self, warehouse_id: str | None = None):
        self._ws  = _get_sdk()
        self._sql = _SQLExecutor(self._ws, warehouse_id)

    # ── Public ────────────────────────────────────────────────────────────────

    def list_catalogs(self) -> list[str]:
        """Return all catalog names visible to the current user."""
        try:
            return [c.name for c in self._ws.catalogs.list() if c.name]
        except Exception as e:
            logger.warning(f"Cannot list catalogs: {e}")
            return []

    def list_schemas(self, catalog_name: str) -> list[str]:
        """Return all schema names in a catalog."""
        try:
            return [
                s.name for s in self._ws.schemas.list(catalog_name=catalog_name)
                if s.name
            ]
        except Exception as e:
            logger.warning(f"Cannot list schemas in {catalog_name}: {e}")
            return []

    def list_tables(self, catalog_name: str, schema_name: str) -> list[str]:
        """Return all table names in a catalog.schema."""
        try:
            return [
                t.name
                for t in self._ws.tables.list(
                    catalog_name=catalog_name,
                    schema_name=schema_name,
                )
                if t.name
            ]
        except Exception as e:
            logger.warning(f"Cannot list tables in {catalog_name}.{schema_name}: {e}")
            return []

    def extract_schema(
        self,
        catalog_name: str,
        schema_name:  str,
        table_names:  list[str] | None = None,
        record_source_prefix: str = "",
    ) -> list[dict]:
        """Extract metadata for all (or specified) tables in catalog.schema."""
        names = table_names or self.list_tables(catalog_name, schema_name)
        if not names:
            raise RuntimeError(
                f"No tables found in {catalog_name}.{schema_name}. "
                "Check the catalog and schema names and your access permissions."
            )
        results = []
        for name in names:
            try:
                meta = self.extract_table(catalog_name, schema_name, name, record_source_prefix)
                results.append(meta)
                logger.info(f"  Extracted: {name} ({len(meta['columns'])} cols)")
            except Exception as e:
                logger.warning(f"  Skipped {name}: {e}")
        return results

    def extract_table(
        self,
        catalog_name: str,
        schema_name:  str,
        table_name:   str,
        record_source_prefix: str = "",
    ) -> dict:
        """Extract metadata for a single table using the SDK tables API."""

        full_ref = f"{catalog_name}.{schema_name}.{table_name}"

        # ── Get table info from SDK ────────────────────────────────────────
        try:
            table_info = self._ws.tables.get(full_name=full_ref)
        except Exception as e:
            raise RuntimeError(f"Cannot retrieve table info for {full_ref}: {e}") from e

        # ── Row count ─────────────────────────────────────────────────────
        row_count = 0
        # Try from table properties first (cheap)
        if table_info.properties:
            rc = table_info.properties.get("numRows") or table_info.properties.get("delta.numRows")
            if rc:
                try: row_count = int(rc)
                except Exception: pass

        # Fall back to SQL count if warehouse available and no property
        if row_count == 0 and self._sql.available:
            try:
                row_count = int(self._sql.scalar(f"SELECT COUNT(*) FROM {full_ref}") or 0)
            except Exception:
                pass

        # ── Columns ───────────────────────────────────────────────────────
        columns = []
        col_infos = table_info.columns or []

        for col in col_infos:
            col_name  = col.name
            dtype     = _normalise_dtype(col.type_text or col.type_name or "string")
            nullable  = col.nullable if col.nullable is not None else True

            # Column stats via SQL warehouse (optional)
            if self._sql.available and row_count > 0:
                stats = self._sql.column_stats(full_ref, col_name, row_count)
            else:
                stats = {"null_pct": None, "distinct_count": None, "sample_values": []}

            columns.append({
                "name":           col_name,
                "data_type":      dtype,
                "nullable":       nullable,
                "null_pct":       stats["null_pct"],
                "distinct_count": stats["distinct_count"],
                "sample_values":  stats["sample_values"],
                "is_primary_key": False,
                "is_foreign_key": False,
                "fk_references":  None,
            })

        record_source = (
            f"{record_source_prefix}.{table_name}"
            if record_source_prefix else full_ref
        )

        return {
            "table_name":    table_name,
            "database":      f"{catalog_name}.{schema_name}",
            "row_count":     row_count,
            "source_mode":   "catalog",
            "source_path":   full_ref,
            "record_source": record_source,
            "columns":       columns,
            "relationships": [],
        }

    @staticmethod
    def parse_catalog_schema(catalog_schema: str) -> tuple[str, str]:
        """
        Parse a user-provided string into (catalog_name, schema_name).

        Accepts:
          "samples.tpch"              → ("samples", "tpch")
          "hive_metastore.default"    → ("hive_metastore", "default")
          "default"                   → ("hive_metastore", "default")
        """
        parts = [p.strip() for p in catalog_schema.strip().split(".") if p.strip()]
        if len(parts) == 2:
            return parts[0], parts[1]
        if len(parts) == 1:
            return "hive_metastore", parts[0]
        raise ValueError(
            f"Cannot parse '{catalog_schema}' as catalog.schema. "
            "Use the format: catalog_name.schema_name  (e.g. samples.tpch)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Mode B — Cloud file path via Spark (cluster/notebook only)
# ─────────────────────────────────────────────────────────────────────────────

class FileExtractor:
    """
    Reads a cloud file path with Spark schema inference.
    Requires an active SparkSession — available on cluster notebooks,
    NOT inside a Databricks App container.
    """

    SUPPORTED_FORMATS = ["parquet", "delta", "csv", "json", "avro"]

    def extract(
        self,
        path:         str,
        file_format:  str = "parquet",
        sample_rows:  int = 10_000,
        table_name:   str | None = None,
    ) -> dict:
        if not _spark_available():
            raise RuntimeError(
                "No active Spark session.\n\n"
                "File path extraction requires a running Spark cluster. "
                "This mode is available in Databricks notebooks but NOT inside "
                "a Databricks App.\n\n"
                "To use file data in the App: run the extraction in a notebook "
                "first (using the MetadataExtractor helper), copy the JSON output, "
                "and paste it into the 'Paste JSON manually' tab."
            )

        spark      = _get_spark()
        file_format = file_format.lower()
        if file_format not in self.SUPPORTED_FORMATS:
            raise ValueError(f"Unsupported format '{file_format}'. Choose from: {self.SUPPORTED_FORMATS}")

        if not table_name:
            table_name = path.rstrip("/").split("/")[-1].split(".")[0] or "file_table"

        try:
            if file_format == "delta":
                df = spark.read.format("delta").load(path)
            elif file_format == "csv":
                df = spark.read.option("header","true").option("inferSchema","true").csv(path)
            elif file_format == "json":
                df = spark.read.json(path)
            elif file_format == "avro":
                df = spark.read.format("avro").load(path)
            else:
                df = spark.read.parquet(path)

            total_count = df.count()
            sample_df   = df.limit(sample_rows)
        except Exception as e:
            raise RuntimeError(f"Cannot read {file_format} file at {path}: {e}") from e

        view = f"__fe_{table_name}_{abs(hash(path)) % 100000}"
        sample_df.createOrReplaceTempView(view)
        sample_n = min(sample_rows, total_count)

        columns = []
        for field in sample_df.schema.fields:
            col_name = field.name
            dtype    = _normalise_dtype(str(field.dataType))
            null_n   = spark.sql(f"SELECT COUNT(*) FROM {view} WHERE `{col_name}` IS NULL").first()[0]
            dist_n   = spark.sql(f"SELECT COUNT(DISTINCT `{col_name}`) FROM {view}").first()[0]
            samples  = [str(r[0]) for r in spark.sql(
                f"SELECT DISTINCT `{col_name}` FROM {view} WHERE `{col_name}` IS NOT NULL LIMIT 5"
            ).collect()]
            columns.append({
                "name": col_name, "data_type": dtype, "nullable": field.nullable,
                "null_pct": round(null_n / max(sample_n,1), 4),
                "distinct_count": dist_n, "sample_values": samples,
                "is_primary_key": False, "is_foreign_key": False, "fk_references": None,
            })

        spark.catalog.dropTempView(view)

        return {
            "table_name": table_name, "database": path, "row_count": total_count,
            "source_mode": "file", "source_path": path, "record_source": path,
            "columns": columns, "relationships": [],
        }


# ─────────────────────────────────────────────────────────────────────────────
# Mode C — Manual JSON normaliser
# ─────────────────────────────────────────────────────────────────────────────

class ManualExtractor:
    """Validates and normalises user-provided metadata JSON."""

    def normalise(self, raw: list | dict) -> list[dict]:
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or not raw:
            raise ValueError("Metadata must be a JSON object or non-empty array.")

        out = []
        for i, table in enumerate(raw):
            if "table_name" not in table:
                raise ValueError(f"Table {i+1}: missing required field 'table_name'.")
            if "columns" not in table or not table["columns"]:
                raise ValueError(f"Table '{table['table_name']}': missing or empty 'columns'.")
            for j, col in enumerate(table["columns"]):
                if "name" not in col or "data_type" not in col:
                    raise ValueError(
                        f"Column {j+1} in '{table['table_name']}': "
                        "each column needs at least 'name' and 'data_type'."
                    )

            normalised_cols = [
                {
                    "name":           c["name"],
                    "data_type":      _normalise_dtype(c.get("data_type","string")),
                    "nullable":       c.get("nullable", True),
                    "null_pct":       c.get("null_pct", None),
                    "distinct_count": c.get("distinct_count", None),
                    "sample_values":  c.get("sample_values", [])[:5],
                    "is_primary_key": c.get("is_primary_key", False),
                    "is_foreign_key": c.get("is_foreign_key", False),
                    "fk_references":  c.get("fk_references", None),
                }
                for c in table["columns"]
            ]
            out.append({
                "table_name":    table["table_name"],
                "database":      table.get("database", ""),
                "row_count":     table.get("row_count", 0),
                "source_mode":   "manual",
                "source_path":   None,
                "record_source": table.get("record_source", None),
                "columns":       normalised_cols,
                "relationships": table.get("relationships", []),
                **{k: v for k, v in table.items()
                   if k not in {"table_name","database","row_count",
                                "columns","relationships","source_mode","source_path"}},
            })
        return out


# ─────────────────────────────────────────────────────────────────────────────
# Unified facade
# ─────────────────────────────────────────────────────────────────────────────

class MetadataExtractor:
    """
    Single entry point for all three extraction modes.

    Usage
    -----
    ex = MetadataExtractor()

    # Mode A — catalog (works in App and notebooks)
    tables = ex.from_catalog("samples", "tpch", table_names=["customer","orders"])
    tables = ex.from_catalog("samples", "tpch")          # all tables in schema

    # Mode B — file (notebooks only, not in App)
    tables = ex.from_file("s3://bucket/customer/", file_format="parquet")

    # Mode C — manual JSON (always works)
    tables = ex.from_manual('[{"table_name":"customer","columns":[...]}]')

    # Always run relationship detection when multiple tables are loaded
    tables = ex.detect_relationships(tables)
    print(ex.summary(tables))
    """

    def __init__(self, warehouse_id: str | None = None):
        self._cat    = None          # lazy-init — avoids SDK errors at import time
        self._file   = FileExtractor()
        self._manual = ManualExtractor()
        self._rel    = RelationshipDetector()
        self._wh_id  = warehouse_id or os.getenv("DATABRICKS_WAREHOUSE_ID","")

    def _get_catalog_extractor(self) -> CatalogExtractor:
        if self._cat is None:
            self._cat = CatalogExtractor(warehouse_id=self._wh_id)
        return self._cat

    # ── Mode A ────────────────────────────────────────────────────────────────

    def from_catalog(
        self,
        catalog_name: str,
        schema_name:  str,
        table_names:  list[str] | None = None,
        record_source_prefix: str = "",
    ) -> list[dict]:
        ext = self._get_catalog_extractor()
        return ext.extract_schema(catalog_name, schema_name, table_names, record_source_prefix)

    def from_catalog_string(
        self,
        catalog_schema: str,
        table_names: list[str] | None = None,
        record_source_prefix: str = "",
    ) -> list[dict]:
        """Convenience: accepts 'catalog.schema' as a single string."""
        ext = self._get_catalog_extractor()
        cat, sch = ext.parse_catalog_schema(catalog_schema)
        return self.from_catalog(cat, sch, table_names, record_source_prefix)

    def list_catalogs(self) -> list[str]:
        return self._get_catalog_extractor().list_catalogs()

    def list_schemas(self, catalog: str) -> list[str]:
        return self._get_catalog_extractor().list_schemas(catalog)

    def list_tables(self, catalog: str, schema: str) -> list[str]:
        return self._get_catalog_extractor().list_tables(catalog, schema)

    # ── Mode B ────────────────────────────────────────────────────────────────

    def from_file(
        self,
        path: str,
        file_format: str = "parquet",
        sample_rows: int = 10_000,
        table_name:  str | None = None,
    ) -> list[dict]:
        return [self._file.extract(path, file_format, sample_rows, table_name)]

    # ── Mode C ────────────────────────────────────────────────────────────────

    def from_manual(self, raw: str | list | dict) -> list[dict]:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON: {e}") from e
        return self._manual.normalise(raw)

    # ── Shared ────────────────────────────────────────────────────────────────

    def detect_relationships(self, tables: list[dict]) -> list[dict]:
        if len(tables) > 1:
            tables = self._rel.detect(tables)
        return tables

    def summary(self, tables: list[dict]) -> str:
        lines = [f"{len(tables)} table(s) loaded:"]
        for t in tables:
            mode = {"catalog":"🗄️ catalog","file":"📁 file","manual":"✏️ manual"}.get(
                t.get("source_mode","manual"), "📋")
            rels = len(t.get("relationships", []))
            stats_note = ""
            if any(c.get("null_pct") is not None for c in t.get("columns",[])):
                stats_note = " · column stats included"
            else:
                stats_note = " · schema only (no stats)"
            lines.append(
                f"  • {t['table_name']} [{mode}] — "
                f"{len(t.get('columns',[]))} cols, {t.get('row_count',0):,} rows"
                + stats_note
                + (f", {rels} FK links" if rels else "")
            )
        return "\n".join(lines)
