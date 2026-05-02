"""
core/token_optimizer.py

Reduces prompt size before LLM calls.

Rules (in order of application):
1. Strip raw row data — only metadata is ever sent
2. Cap columns per table at platform limit
3. Trim sample values to platform limit
4. Estimate token count and warn if over budget
5. Truncate knowledge chunks to fit remaining budget

Rough token estimator: 1 token ≈ 4 characters (good enough for budgeting)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4  # conservative approximation


def _count_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


class TokenOptimizer:
    """
    Trims and shapes metadata + knowledge before prompt assembly.

    Parameters
    ----------
    config_loader : ConfigLoader
        Reads token_optimization settings from platform config.
    """

    def __init__(self, config_loader):
        tc = config_loader.token_config
        self.enabled             = tc.get("enabled", True)
        self.max_prompt_tokens   = tc.get("max_prompt_tokens", 3000)
        self.max_sample_values   = tc.get("max_sample_values", 5)
        self.max_columns         = tc.get("max_columns_in_prompt", 50)
        self.chunk_size          = tc.get("knowledge_chunk_size", 500)
        self.max_chunks          = tc.get("max_knowledge_chunks", 3)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def optimize_metadata(self, metadata: dict) -> dict:
        """
        Sanitise and slim a table-metadata dict before prompt injection.

        Input shape (from spark.catalog or manual input):
        {
            "table_name": "customer",
            "database": "crm",
            "row_count": 1_200_000,
            "columns": [
                {
                    "name": "customer_id",
                    "data_type": "string",
                    "nullable": false,
                    "sample_values": ["C001","C002",...],
                    "null_pct": 0.0,
                    "distinct_count": 1200000
                },
                ...
            ],
            "raw_data": [...]   # ← NEVER sent to LLM
        }
        """
        if not self.enabled:
            return metadata

        result = {k: v for k, v in metadata.items() if k != "raw_data"}

        columns = result.get("columns", [])
        if len(columns) > self.max_columns:
            logger.warning(
                f"Table '{result.get('table_name')}' has {len(columns)} columns — "
                f"truncating to {self.max_columns} for prompt efficiency."
            )
            columns = columns[: self.max_columns]

        trimmed_columns = []
        for col in columns:
            trimmed = {
                "name":           col.get("name", ""),
                "data_type":      col.get("data_type", "unknown"),
                "nullable":       col.get("nullable", True),
                "null_pct":       col.get("null_pct"),
                "distinct_count": col.get("distinct_count"),
            }
            samples = col.get("sample_values", [])
            if samples:
                trimmed["sample_values"] = samples[: self.max_sample_values]
            trimmed_columns.append(trimmed)

        result["columns"] = trimmed_columns
        return result

    def optimize_metadata_list(self, metadata_list: list[dict]) -> list[dict]:
        """Optimize a list of table metadata dicts."""
        return [self.optimize_metadata(m) for m in metadata_list]

    def fit_knowledge_chunks(
        self, chunks: list[str], reserved_tokens: int
    ) -> list[str]:
        """
        Select as many knowledge chunks as fit within the remaining token budget.

        Parameters
        ----------
        chunks          : Ordered list of text chunks (most relevant first).
        reserved_tokens : Tokens already consumed by system prompt + metadata.
        """
        budget = self.max_prompt_tokens - reserved_tokens
        selected = []
        used = 0
        for chunk in chunks[: self.max_chunks]:
            chunk_tokens = _count_tokens(chunk)
            if used + chunk_tokens <= budget:
                selected.append(chunk)
                used += chunk_tokens
            else:
                logger.debug(
                    f"Knowledge chunk dropped — budget exhausted "
                    f"({used}/{budget} tokens used)."
                )
                break
        return selected

    def estimate_tokens(self, text: str) -> int:
        return _count_tokens(text)

    def check_budget(self, prompt: str, label: str = "prompt") -> None:
        """Log a warning if the prompt exceeds the token budget."""
        tokens = _count_tokens(prompt)
        if tokens > self.max_prompt_tokens:
            logger.warning(
                f"[TokenOptimizer] {label} exceeds budget: "
                f"{tokens} estimated tokens (limit {self.max_prompt_tokens}). "
                "Consider reducing column count or knowledge chunks."
            )
        else:
            logger.debug(
                f"[TokenOptimizer] {label}: ~{tokens} tokens "
                f"({self.max_prompt_tokens - tokens} remaining)."
            )
