"""
core/rule_injector.py

Loads governance rules from agent rules.md files and injects them
into the system prompt as a numbered list.

Rules are loaded from (in order, both are merged):
  - agents/<id>/rules.md         → agent-specific rules
  - shared_knowledge/            → platform-wide rules (if flagged)

Format of rules.md:
  Any Markdown file. Each top-level bullet or numbered item becomes one rule.
  Lines starting with # are treated as section headers (included as context).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


class RuleInjector:
    """
    Extracts rules from Markdown and formats them for system prompt injection.

    Parameters
    ----------
    base_dir : Project root. Defaults to two levels above this file.
    """

    def __init__(self, base_dir: Path | None = None):
        self._base_dir = base_dir or Path(__file__).resolve().parent.parent
        self._cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_rules_block(self, agent_id: str) -> str:
        """
        Return a formatted rules block ready for system prompt injection.

        Returns empty string if no rules.md found (non-fatal).
        """
        rules_path = self._base_dir / "agents" / agent_id / "rules.md"
        if not rules_path.exists():
            logger.debug(f"No rules.md found for agent '{agent_id}' — skipping.")
            return ""

        cache_key = str(rules_path)
        if cache_key in self._cache:
            return self._cache[cache_key]

        raw = rules_path.read_text(encoding="utf-8")
        block = self._format_rules_block(raw, agent_id)
        self._cache[cache_key] = block
        return block

    def get_raw_rules(self, agent_id: str) -> str:
        """Return raw rules.md content (for debugging or display)."""
        rules_path = self._base_dir / "agents" / agent_id / "rules.md"
        if not rules_path.exists():
            return ""
        return rules_path.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _format_rules_block(self, raw_md: str, agent_id: str) -> str:
        """
        Convert Markdown rules into a structured block for the system prompt.
        Extracts bullet/numbered list items; preserves section headers as labels.
        """
        lines = raw_md.splitlines()
        rules: list[str] = []
        current_section = ""

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Section header
            if stripped.startswith("#"):
                current_section = stripped.lstrip("#").strip()
                continue

            # Bullet or numbered list item
            match = re.match(r"^[-*+]\s+(.+)$", stripped) or re.match(
                r"^\d+\.\s+(.+)$", stripped
            )
            if match:
                rule_text = match.group(1).strip()
                if current_section:
                    rules.append(f"[{current_section}] {rule_text}")
                else:
                    rules.append(rule_text)

        if not rules:
            return ""

        numbered = "\n".join(f"{i+1}. {r}" for i, r in enumerate(rules))
        return (
            f"## Mandatory governance rules for this agent\n"
            f"You MUST follow ALL of the rules below. "
            f"Violating any rule makes the output invalid.\n\n"
            f"{numbered}"
        )
