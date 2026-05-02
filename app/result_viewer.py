"""
app/result_viewer.py

Utility helpers for displaying AgentResult objects in Streamlit.
Kept separate from catalog.py for reusability.
"""

from __future__ import annotations

import json
from typing import Any


def result_to_download_bytes(result_output: Any) -> tuple[bytes, str]:
    """
    Convert agent output to (bytes, mime_type) for st.download_button.
    JSON outputs → application/json
    SQL/text outputs → text/plain
    """
    if isinstance(result_output, dict):
        return json.dumps(result_output, indent=2).encode(), "application/json"
    text = str(result_output)
    if text.strip().startswith("{") or text.strip().startswith("["):
        return text.encode(), "application/json"
    return text.encode(), "text/plain"


def format_token_info(token_usage: dict, cost_usd: float) -> str:
    """Return a formatted one-liner for token usage display."""
    total    = token_usage.get("total", 0)
    prompt   = token_usage.get("prompt", 0)
    complete = token_usage.get("completion", 0)
    return (
        f"{total:,} tokens total "
        f"(prompt: {prompt:,} · completion: {complete:,}) "
        f"· est. cost ${cost_usd:.4f}"
    )
