"""
core/execution_handler.py

Routes non-agentic agent execution to PySpark / SQL / notebook handlers.
Also handles output registration to Unity Catalog Delta tables.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ExecutionHandler:
    """
    Handles deterministic (non-LLM) agent execution and output persistence.

    Parameters
    ----------
    config_loader : ConfigLoader
    base_dir      : Project root.
    """

    def __init__(self, config_loader, base_dir: Path | None = None):
        self._cfg      = config_loader
        self._base_dir = base_dir or Path(__file__).resolve().parent.parent
        self._outputs_dir = self._base_dir / "outputs"
        self._outputs_dir.mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    # Non-agentic dispatcher
    # ------------------------------------------------------------------

    def run_non_agentic(
        self, agent_id: str, metadata: list[dict], user_context: str = ""
    ) -> dict:
        """
        Dispatch to the appropriate deterministic handler.

        Returns a result dict with keys:
          status, agent_id, output, timestamp
        """
        handlers = {
            "data_profiler":    self._run_data_profiler,
            "sample_gen":       self._run_sample_gen,
            "ingestion_cfg_gen": self._run_ingestion_cfg,
        }

        handler = handlers.get(agent_id)
        if not handler:
            raise ValueError(
                f"No non-agentic handler for agent '{agent_id}'. "
                f"Available: {list(handlers.keys())}"
            )

        logger.info(f"[ExecutionHandler] Running non-agentic agent: {agent_id}")
        result = handler(metadata, user_context)
        result.update({"agent_id": agent_id, "timestamp": _now()})
        return result

    # ------------------------------------------------------------------
    # Output persistence
    # ------------------------------------------------------------------

    def save_output(
        self, agent_id: str, result: dict, run_id: str = ""
    ) -> str:
        """
        Save agent output to the local outputs/ folder.
        On Databricks, this also writes to UC Delta (if enabled by config).

        Returns the output file path.
        """
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        suffix = f"_{run_id}" if run_id else ""
        filename = f"{agent_id}_{ts}{suffix}.json"
        filepath = self._outputs_dir / filename

        with filepath.open("w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, default=str)

        logger.info(f"[ExecutionHandler] Output saved: {filepath}")

        # UC registration (Databricks-only, silently skipped locally)
        if self._cfg.execution_config.get("enable_output_registration", False):
            self._register_to_uc(agent_id, result)

        return str(filepath)

    # ------------------------------------------------------------------
    # Non-agentic handlers
    # ------------------------------------------------------------------

    def _run_data_profiler(
        self, metadata: list[dict], user_context: str
    ) -> dict:
        """
        Compute dataset statistics from metadata.
        On Databricks this would use spark.sql() on the real table.
        Here we derive statistics from the metadata dict itself.
        """
        profiles = []
        for meta in metadata:
            profile = {
                "table_name":    meta.get("table_name", "unknown"),
                "row_count":     meta.get("row_count", "unknown"),
                "column_count":  len(meta.get("columns", [])),
                "columns": [
                    {
                        "name":           col.get("name"),
                        "data_type":      col.get("data_type"),
                        "nullable":       col.get("nullable"),
                        "null_pct":       col.get("null_pct", 0),
                        "distinct_count": col.get("distinct_count"),
                        "sample_values":  col.get("sample_values", [])[:3],
                    }
                    for col in meta.get("columns", [])
                ],
            }
            profiles.append(profile)

        return {"status": "success", "output": {"profiles": profiles}}

    def _run_sample_gen(
        self, metadata: list[dict], user_context: str
    ) -> dict:
        """
        Generate synthetic sample data from schema.
        On Databricks: use spark DataFrames + Faker for realistic values.
        """
        import random, string

        _type_generators = {
            "string":    lambda: "".join(random.choices(string.ascii_uppercase, k=6)),
            "integer":   lambda: random.randint(1, 100_000),
            "long":      lambda: random.randint(1, 10_000_000),
            "double":    lambda: round(random.uniform(0, 10_000), 2),
            "boolean":   lambda: random.choice([True, False]),
            "timestamp": lambda: "2024-01-15T10:30:00Z",
            "date":      lambda: "2024-01-15",
        }

        samples = []
        for meta in metadata:
            cols = meta.get("columns", [])
            rows = []
            for _ in range(min(10, int(user_context) if user_context.isdigit() else 5)):
                row = {}
                for col in cols:
                    dt   = col.get("data_type", "string").lower()
                    gen  = _type_generators.get(dt, _type_generators["string"])
                    row[col["name"]] = gen()
                rows.append(row)

            samples.append({
                "table_name": meta.get("table_name"),
                "row_count":  len(rows),
                "rows":       rows,
            })

        return {"status": "success", "output": {"samples": samples}}

    def _run_ingestion_cfg(
        self, metadata: list[dict], user_context: str
    ) -> dict:
        """
        Generate template-driven ingestion config (Autoloader / COPY INTO).
        """
        configs = []
        for meta in metadata:
            table = meta.get("table_name", "unknown")
            cols  = [c["name"] for c in meta.get("columns", [])]
            cfg = {
                "source_table":   table,
                "target_table":   f"bronze.{table}",
                "format":         "delta",
                "load_type":      "incremental",
                "autoloader": {
                    "cloudFilesFormat":   "json",
                    "inferColumnTypes":   True,
                    "schemaLocation":     f"/checkpoints/{table}/schema",
                    "checkpointLocation": f"/checkpoints/{table}/checkpoint",
                },
                "selected_columns": cols,
                "generated_at": _now(),
            }
            configs.append(cfg)

        return {"status": "success", "output": {"ingestion_configs": configs}}

    # ------------------------------------------------------------------
    # UC Delta registration (Databricks-only)
    # ------------------------------------------------------------------

    def _register_to_uc(self, agent_id: str, result: dict) -> None:
        """Write result to UC Delta table. Silently skips if spark unavailable."""
        try:
            from pyspark.sql import SparkSession  # type: ignore
            spark = SparkSession.getActiveSession()
            if spark is None:
                return

            import pandas as pd
            table_name = self._cfg.get_uc_table("outputs", f"{agent_id}_results")
            df = spark.createDataFrame(
                pd.DataFrame([{"result_json": json.dumps(result, default=str),
                               "timestamp":   _now()}])
            )
            df.write.format("delta").mode("append").saveAsTable(table_name)
            logger.info(f"[ExecutionHandler] Output registered to UC: {table_name}")
        except Exception as exc:
            logger.debug(f"UC registration skipped: {exc}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
