"""
app/form_builder.py

Utility functions for building Streamlit input forms dynamically
from agent config metadata. Kept separate from catalog.py to allow
reuse and unit testing without a running Streamlit session.
"""

from __future__ import annotations

import json
from typing import Any


def parse_metadata_input(raw: str) -> list[dict]:
    """
    Parse user-supplied metadata string into a list of table metadata dicts.
    Accepts both a JSON array and a single JSON object.

    Raises
    ------
    ValueError with a user-friendly message if JSON is invalid.
    """
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON: {e}") from e

    if isinstance(parsed, dict):
        return [parsed]
    if isinstance(parsed, list):
        return parsed
    raise ValueError("Metadata must be a JSON object or array of objects.")


def build_metadata_summary(metadata: list[dict]) -> str:
    """
    Return a one-line human-readable summary of table metadata for display.
    e.g. "2 tables: customer (7 cols), order (12 cols)"
    """
    parts = []
    for m in metadata:
        name = m.get("table_name", "unknown")
        n_cols = len(m.get("columns", []))
        parts.append(f"{name} ({n_cols} cols)")
    prefix = f"{len(metadata)} table{'s' if len(metadata) > 1 else ''}: "
    return prefix + ", ".join(parts)


def get_sample_metadata(agent_id: str) -> str:
    """
    Return a sensible default metadata JSON string for each agent
    so the form is pre-populated and usable out of the box.
    """
    base = [{
        "table_name": "customer",
        "database": "crm",
        "row_count": 1200000,
        "columns": [
            {"name": "customer_id",   "data_type": "string",    "nullable": False,
             "null_pct": 0.0, "distinct_count": 1200000, "sample_values": ["C001", "C002"]},
            {"name": "first_name",    "data_type": "string",    "nullable": True,
             "null_pct": 0.01, "distinct_count": 45000},
            {"name": "email",         "data_type": "string",    "nullable": True,
             "null_pct": 0.05, "distinct_count": 1150000, "sample_values": ["a@b.com"]},
            {"name": "country_code",  "data_type": "string",    "nullable": False,
             "null_pct": 0.0, "distinct_count": 45, "sample_values": ["GB", "US", "DE"]},
            {"name": "created_at",    "data_type": "timestamp", "nullable": False,
             "null_pct": 0.0, "distinct_count": 1200000},
        ]
    }]

    if agent_id == "lineage_creator":
        return json.dumps([{
            "table_name": "customer",
            "database": "crm",
            "record_source": "salesforce.crm.contact",
            "columns": base[0]["columns"],
        }], indent=2)

    if agent_id == "sttm_gen":
        return json.dumps([
            {**base[0], "role": "source"},
            {
                "table_name": "HUB_CUSTOMER",
                "database": "silver",
                "role": "target",
                "columns": [
                    {"name": "customer_hk",   "data_type": "binary",    "nullable": False},
                    {"name": "customer_id",   "data_type": "string",    "nullable": False},
                    {"name": "load_date",     "data_type": "timestamp", "nullable": False},
                    {"name": "record_source", "data_type": "string",    "nullable": False},
                ]
            }
        ], indent=2)

    return json.dumps(base, indent=2)
