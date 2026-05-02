"""
core/metadata_extractor.py

Extracts rich table metadata from three sources:
  A. Unity Catalog / Hive metastore  — queries INFORMATION_SCHEMA + DESCRIBE TABLE
  B. Cloud file path                  — S3, ADLS, GCS via Spark schema inference
  C. Manual JSON                      — validates and normalises user-provided metadata

All three modes produce the same output schema so the rest of the platform
(agents, prompt builder, token optimizer) works identically regardless of source.

Output schema per table
-----------------------
{
  "table_name":    str,
  "database":      str,             # catalog.schema
  "row_count":     int,
  "source_mode":   "catalog" | "file" | "manual",
  "source_path":   str | None,      # original path/table ref
  "record_source": str | None,      # for DV2 lineage
  "columns": [
    {
      "name":           str,
      "data_type":      str,
      "nullable":       bool,
      "null_pct":       float,      # 0.0 – 1.0
      "distinct_count": int,
      "sample_values":  list[str],  # max 5
      "is_primary_key": bool,
      "is_foreign_key": bool,
      "fk_references":  str | None  # "other_table.other_col"
    }
  ],
  "relationships": [               # cross-table FK relationships (filled by RelationshipDetector)
    {
      "from_column":  str,
      "to_table":     str,
      "to_column":    str,
      "confidence":   "high" | "medium" | "low"
    }
  ]
}
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _spark_available() -> bool:
    try:
        from pyspark.sql import SparkSession  # type: ignore
        return SparkSession.getActiveSession() is not None
    except Exception:
        return False


def _get_spark():
    from pyspark.sql import SparkSession  # type: ignore
    return SparkSession.getActiveSession()


def _safe_distinct(spark, full_table: str, col: str, limit: int = 5) -> list[str]:
    """Return up to `limit` distinct non-null values for a column as strings."""
    try:
        rows = spark.sql(
            f"SELECT DISTINCT `{col}` FROM {full_table} "
            f"WHERE `{col}` IS NOT NULL LIMIT {limit}"
        ).collect()
        return [str(r[0]) for r in rows]
    except Exception:
        return []


def _safe_null_pct(spark, full_table: str, col: str, row_count: int) -> float:
    if row_count == 0:
        return 0.0
    try:
        n = spark.sql(
            f"SELECT COUNT(*) as n FROM {full_table} WHERE `{col}` IS NULL"
        ).first()["n"]
        return round(n / row_count, 4)
    except Exception:
        return 0.0


def _safe_distinct_count(spark, full_table: str, col: str) -> int:
    try:
        return spark.sql(
            f"SELECT COUNT(DISTINCT `{col}`) as n FROM {full_table}"
        ).first()["n"]
    except Exception:
        return 0


def _normalise_dtype(dtype: str) -> str:
    """Normalise Spark/Hive type strings to simple lowercase names."""
    dtype = dtype.lower().strip()
    if dtype.startswith("decimal"):    return "decimal"
    if dtype.startswith("array"):      return "array"
    if dtype.startswith("map"):        return "map"
    if dtype.startswith("struct"):     return "struct"
    return dtype


# ─────────────────────────────────────────────────────────────────────────────
# Relationship detector — finds FK links across multiple tables
# ─────────────────────────────────────────────────────────────────────────────

class RelationshipDetector:
    """
    Infers likely FK relationships between a set of tables based on:
      1. Column name matching (order.customer_id → customer.customer_id)
      2. Data type compatibility
      3. Distinct count ratio (FK col distinct count ≤ PK col distinct count)

    Only works when multiple tables are provided together.
    """

    # Patterns that suggest a column is a foreign key pointing to another table
    # e.g. "customer_id" → likely references "customer" table
    FK_PATTERN = re.compile(r"^(.+?)_id$", re.IGNORECASE)

    def detect(self, tables: list[dict]) -> list[dict]:
        """
        Annotate each table's columns with is_primary_key, is_foreign_key,
        fk_references, and populate the top-level relationships list.
        """
        # Index: column_name → list of tables that have it
        col_index: dict[str, list[dict]] = {}
        for t in tables:
            for col in t.get("columns", []):
                name = col["name"].lower()
                col_index.setdefault(name, []).append(t)

        # Index: table_name → table dict (for fast lookup)
        table_index = {t["table_name"].lower(): t for t in tables}

        for t in tables:
            t.setdefault("relationships", [])
            row_count = t.get("row_count", 0)

            for col in t.get("columns", []):
                col.setdefault("is_primary_key", False)
                col.setdefault("is_foreign_key",  False)
                col.setdefault("fk_references",   None)

                col_name  = col["name"].lower()
                col_dcount= col.get("distinct_count", 0)

                # Heuristic 1: <table_name>_id column with distinct_count = row_count → PK
                expected_pk = t["table_name"].lower() + "_id"
                if col_name == expected_pk and col_dcount == row_count and row_count > 0:
                    col["is_primary_key"] = True

                # Heuristic 2: col matches pattern <X>_id and table X exists → FK
                m = self.FK_PATTERN.match(col_name)
                if m:
                    ref_table_name = m.group(1).lower()
                    if ref_table_name != t["table_name"].lower() and ref_table_name in table_index:
                        ref_table = table_index[ref_table_name]
                        ref_pk    = ref_table_name + "_id"

                        # Check the referenced table actually has that PK column
                        ref_col_names = [c["name"].lower() for c in ref_table.get("columns", [])]
                        if ref_pk in ref_col_names:
                            col["is_foreign_key"] = True
                            col["fk_references"]  = f"{ref_table['table_name']}.{ref_pk}"

                            t["relationships"].append({
                                "from_column": col["name"],
                                "to_table":    ref_table["table_name"],
                                "to_column":   ref_pk,
                                "confidence":  "high",
                            })
                        else:
                            # Partial match — lower confidence
                            col["is_foreign_key"] = True
                            col["fk_references"]  = f"{ref_table['table_name']} (inferred)"
                            t["relationships"].append({
                                "from_column": col["name"],
                                "to_table":    ref_table["table_name"],
                                "to_column":   "?",
                                "confidence":  "medium",
                            })

                # Heuristic 3: col_name == <something>_id but matching table not in set
                elif m and col_name != expected_pk:
                    ref_table_name = m.group(1).lower()
                    col["is_foreign_key"] = True
                    col["fk_references"]  = f"{ref_table_name} (not in current set)"
                    t["relationships"].append({
                        "from_column": col["name"],
                        "to_table":    ref_table_name,
                        "to_column":   ref_table_name + "_id",
                        "confidence":  "low",
                    })

        return tables


# ─────────────────────────────────────────────────────────────────────────────
# Mode A — Unity Catalog / Hive metastore
# ─────────────────────────────────────────────────────────────────────────────

class CatalogExtractor:
    """
    Extracts metadata for all tables in a given catalog.schema using Spark.
    Falls back gracefully if a table can't be described.
    """

    def extract_schema(self, catalog_schema: str, record_source_prefix: str = "") -> list[dict]:
        """
        Extract metadata for every table in `catalog_schema` (e.g. "crm_prod.raw").

        Parameters
        ----------
        catalog_schema      : "catalog.schema" or just "schema"
        record_source_prefix: prepended to table name for record_source field
                              e.g. "salesforce.crm" → record_source = "salesforce.crm.customer"
        """
        if not _spark_available():
            raise RuntimeError(
                "No active Spark session. "
                "CatalogExtractor requires Databricks or a running SparkSession."
            )
        spark = _get_spark()

        # List all tables in the schema
        try:
            tables_df = spark.sql(f"SHOW TABLES IN {catalog_schema}")
            table_names = [r["tableName"] for r in tables_df.collect()]
        except Exception as e:
            raise RuntimeError(f"Cannot list tables in {catalog_schema}: {e}") from e

        logger.info(f"CatalogExtractor: found {len(table_names)} tables in {catalog_schema}")

        results = []
        for table_name in table_names:
            try:
                meta = self.extract_table(
                    catalog_schema=catalog_schema,
                    table_name=table_name,
                    record_source_prefix=record_source_prefix,
                )
                results.append(meta)
                logger.info(f"  Extracted: {table_name} ({meta['row_count']:,} rows)")
            except Exception as e:
                logger.warning(f"  Skipped {table_name}: {e}")

        return results

    def extract_table(
        self,
        catalog_schema: str,
        table_name: str,
        record_source_prefix: str = "",
    ) -> dict:
        """Extract metadata for a single table."""
        spark      = _get_spark()
        full_table = f"{catalog_schema}.{table_name}"

        # Row count
        try:
            row_count = spark.sql(f"SELECT COUNT(*) as n FROM {full_table}").first()["n"]
        except Exception:
            row_count = 0

        # Schema
        schema_rows = spark.sql(f"DESCRIBE TABLE {full_table}").collect()
        columns = []
        for row in schema_rows:
            col_name = row["col_name"]
            if not col_name or col_name.startswith("#") or col_name.startswith("--"):
                continue
            dtype = _normalise_dtype(row.get("data_type", "string"))

            # Per-column stats
            null_pct      = _safe_null_pct(spark, full_table, col_name, row_count)
            distinct_count= _safe_distinct_count(spark, full_table, col_name)
            sample_values = _safe_distinct(spark, full_table, col_name, limit=5)

            columns.append({
                "name":           col_name,
                "data_type":      dtype,
                "nullable":       True,       # refined by RelationshipDetector
                "null_pct":       null_pct,
                "distinct_count": distinct_count,
                "sample_values":  sample_values,
                "is_primary_key": False,
                "is_foreign_key": False,
                "fk_references":  None,
            })

        record_source = (
            f"{record_source_prefix}.{table_name}" if record_source_prefix
            else full_table
        )

        return {
            "table_name":    table_name,
            "database":      catalog_schema,
            "row_count":     row_count,
            "source_mode":   "catalog",
            "source_path":   full_table,
            "record_source": record_source,
            "columns":       columns,
            "relationships": [],
        }


# ─────────────────────────────────────────────────────────────────────────────
# Mode B — Cloud file path (S3 / ADLS / GCS)
# ─────────────────────────────────────────────────────────────────────────────

class FileExtractor:
    """
    Infers schema and computes column stats from a cloud file path.
    Reads a configurable sample size to keep costs low.
    Supports: parquet, delta, csv, json, avro.
    """

    SUPPORTED_FORMATS = ["parquet", "delta", "csv", "json", "avro"]

    def extract(
        self,
        path: str,
        file_format: str = "parquet",
        sample_rows: int = 10_000,
        table_name: str | None = None,
    ) -> dict:
        """
        Parameters
        ----------
        path        : cloud path e.g. s3://bucket/prefix/ or abfss://...
        file_format : parquet | delta | csv | json | avro
        sample_rows : how many rows to read for stats (default 10k — low cost)
        table_name  : override for the inferred table name (defaults to last path segment)
        """
        if not _spark_available():
            raise RuntimeError(
                "No active Spark session. "
                "FileExtractor requires Databricks or a running SparkSession."
            )
        spark = _get_spark()

        file_format = file_format.lower()
        if file_format not in self.SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported format: {file_format}. "
                f"Choose from: {self.SUPPORTED_FORMATS}"
            )

        # Infer table name from path
        if not table_name:
            table_name = path.rstrip("/").split("/")[-1].split(".")[0] or "file_table"

        logger.info(f"FileExtractor: reading {file_format} from {path} (sample={sample_rows})")

        # Read sample
        try:
            if file_format == "delta":
                df = spark.read.format("delta").load(path)
            elif file_format == "csv":
                df = spark.read.option("header", "true").option("inferSchema", "true").csv(path)
            elif file_format == "json":
                df = spark.read.json(path)
            elif file_format == "avro":
                df = spark.read.format("avro").load(path)
            else:
                df = spark.read.parquet(path)

            # Sample for stats
            total_count  = df.count()
            sample_df    = df.limit(sample_rows)
            sample_count = min(sample_rows, total_count)

        except Exception as e:
            raise RuntimeError(f"Cannot read file at {path}: {e}") from e

        # Register as temp view for SQL stats
        view_name = f"__fe_{table_name}_{abs(hash(path)) % 100000}"
        sample_df.createOrReplaceTempView(view_name)

        columns = []
        for field in sample_df.schema.fields:
            col_name = field.name
            dtype    = _normalise_dtype(str(field.dataType))

            null_count    = spark.sql(
                f"SELECT COUNT(*) as n FROM {view_name} WHERE `{col_name}` IS NULL"
            ).first()["n"]
            null_pct      = round(null_count / max(sample_count, 1), 4)
            distinct_count= spark.sql(
                f"SELECT COUNT(DISTINCT `{col_name}`) as n FROM {view_name}"
            ).first()["n"]
            sample_values = [
                str(r[0]) for r in spark.sql(
                    f"SELECT DISTINCT `{col_name}` FROM {view_name} "
                    f"WHERE `{col_name}` IS NOT NULL LIMIT 5"
                ).collect()
            ]

            columns.append({
                "name":           col_name,
                "data_type":      dtype,
                "nullable":       field.nullable,
                "null_pct":       null_pct,
                "distinct_count": distinct_count,
                "sample_values":  sample_values,
                "is_primary_key": False,
                "is_foreign_key": False,
                "fk_references":  None,
            })

        # Drop temp view
        spark.catalog.dropTempView(view_name)

        return {
            "table_name":    table_name,
            "database":      path,
            "row_count":     total_count,
            "source_mode":   "file",
            "source_path":   path,
            "record_source": path,
            "columns":       columns,
            "relationships": [],
        }


# ─────────────────────────────────────────────────────────────────────────────
# Mode C — Manual JSON normaliser
# ─────────────────────────────────────────────────────────────────────────────

class ManualExtractor:
    """
    Validates and normalises user-provided metadata JSON.
    Fills in missing optional fields with sensible defaults so the
    rest of the platform always sees a complete metadata object.
    """

    REQUIRED_FIELDS = {"table_name", "columns"}
    REQUIRED_COL_FIELDS = {"name", "data_type"}

    def normalise(self, raw: list[dict] | dict) -> list[dict]:
        """
        Accept either a single table dict or a list, validate, and normalise.
        Raises ValueError with a clear message on validation failure.
        """
        if isinstance(raw, dict):
            raw = [raw]
        if not isinstance(raw, list) or len(raw) == 0:
            raise ValueError("Metadata must be a JSON object or non-empty array of objects.")

        normalised = []
        for i, table in enumerate(raw):
            missing = self.REQUIRED_FIELDS - set(table.keys())
            if missing:
                raise ValueError(
                    f"Table {i+1} is missing required fields: {missing}. "
                    f"Every table must have at least 'table_name' and 'columns'."
                )
            if not isinstance(table["columns"], list) or len(table["columns"]) == 0:
                raise ValueError(f"Table '{table['table_name']}' has no columns.")

            normalised_cols = []
            for j, col in enumerate(table["columns"]):
                missing_col = self.REQUIRED_COL_FIELDS - set(col.keys())
                if missing_col:
                    raise ValueError(
                        f"Column {j+1} in '{table['table_name']}' is missing: {missing_col}. "
                        f"Every column must have at least 'name' and 'data_type'."
                    )
                normalised_cols.append({
                    "name":           col["name"],
                    "data_type":      _normalise_dtype(col.get("data_type", "string")),
                    "nullable":       col.get("nullable", True),
                    "null_pct":       col.get("null_pct", None),
                    "distinct_count": col.get("distinct_count", None),
                    "sample_values":  col.get("sample_values", [])[:5],
                    "is_primary_key": col.get("is_primary_key", False),
                    "is_foreign_key": col.get("is_foreign_key", False),
                    "fk_references":  col.get("fk_references", None),
                })

            normalised.append({
                "table_name":    table["table_name"],
                "database":      table.get("database", ""),
                "row_count":     table.get("row_count", 0),
                "source_mode":   "manual",
                "source_path":   None,
                "record_source": table.get("record_source", None),
                "columns":       normalised_cols,
                "relationships": table.get("relationships", []),
                # Preserve any extra fields the user added (e.g. role, existing_dq_rules)
                **{k: v for k, v in table.items()
                   if k not in {"table_name","database","row_count","columns","relationships"}},
            })

        return normalised


# ─────────────────────────────────────────────────────────────────────────────
# Unified facade
# ─────────────────────────────────────────────────────────────────────────────

class MetadataExtractor:
    """
    Single entry point for all three extraction modes.

    Usage
    -----
    extractor = MetadataExtractor()

    # Mode A: catalog
    tables = extractor.from_catalog("crm_prod.raw", record_source_prefix="salesforce")

    # Mode B: file
    tables = extractor.from_file("s3://bucket/customer/", file_format="parquet")

    # Mode C: manual JSON (string or already-parsed list)
    tables = extractor.from_manual('[{"table_name": "customer", "columns": [...]}]')

    # In all cases: run relationship detection before passing to agents
    tables = extractor.detect_relationships(tables)
    """

    def __init__(self):
        self._catalog = CatalogExtractor()
        self._file    = FileExtractor()
        self._manual  = ManualExtractor()
        self._rel     = RelationshipDetector()

    def from_catalog(
        self,
        catalog_schema: str,
        table_names: list[str] | None = None,
        record_source_prefix: str = "",
    ) -> list[dict]:
        """
        Extract all tables from a catalog.schema.
        If table_names is provided, only those tables are extracted.
        """
        if table_names:
            return [
                self._catalog.extract_table(catalog_schema, t, record_source_prefix)
                for t in table_names
            ]
        return self._catalog.extract_schema(catalog_schema, record_source_prefix)

    def from_file(
        self,
        path: str,
        file_format: str = "parquet",
        sample_rows: int = 10_000,
        table_name: str | None = None,
    ) -> list[dict]:
        """Extract schema and stats from a cloud file path."""
        result = self._file.extract(path, file_format, sample_rows, table_name)
        return [result]

    def from_manual(self, raw: str | list[dict] | dict) -> list[dict]:
        """Parse, validate, and normalise user-provided JSON metadata."""
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON: {e}") from e
        return self._manual.normalise(raw)

    def detect_relationships(self, tables: list[dict]) -> list[dict]:
        """
        Run cross-table relationship detection.
        Should always be called after extraction when multiple tables are present.
        """
        if len(tables) > 1:
            tables = self._rel.detect(tables)
        return tables

    def summary(self, tables: list[dict]) -> str:
        """Return a human-readable summary of extracted tables."""
        lines = [f"{len(tables)} table(s) loaded:"]
        for t in tables:
            mode  = t.get("source_mode", "?")
            cols  = len(t.get("columns", []))
            rows  = t.get("row_count", 0)
            rels  = len(t.get("relationships", []))
            lines.append(
                f"  • {t['table_name']} [{mode}] — "
                f"{cols} columns, {rows:,} rows"
                + (f", {rels} FK relationships detected" if rels else "")
            )
        return "\n".join(lines)
