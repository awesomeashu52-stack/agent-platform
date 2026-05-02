"""
core/knowledge_manager.py

Loads shared and agent-specific knowledge files, chunks them, and returns
the most relevant chunks for a given query via keyword matching.

Knowledge files are plain Markdown. They live in:
  - shared_knowledge/          → cross-agent standards (DV2, DQ framework, etc.)
  - agents/<id>/knowledge/     → agent-specific reference material

On Databricks these resolve to Unity Catalog Volumes at runtime.
Locally they resolve to the filesystem paths (for dev/testing).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


class KnowledgeManager:
    """
    Loads, chunks, and retrieves knowledge relevant to an agent's task.

    Parameters
    ----------
    config_loader : ConfigLoader
    base_dir      : Project root path. Defaults to two levels above this file.
    """

    def __init__(self, config_loader, base_dir: Path | None = None):
        self._cfg = config_loader
        self._base_dir = base_dir or Path(__file__).resolve().parent.parent
        self._chunk_size  = config_loader.token_config.get("knowledge_chunk_size", 500)
        self._max_chunks  = config_loader.token_config.get("max_knowledge_chunks", 3)
        self._cache: dict[str, list[str]] = {}   # path → chunks

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_relevant_chunks(
        self,
        agent_id: str,
        query_keywords: list[str],
        include_shared: bool = True,
    ) -> list[str]:
        """
        Return the most relevant knowledge chunks for this agent + query.

        Strategy: keyword frequency scoring (no vector DB needed — keeps
        the platform dependency-free and cost-zero for retrieval).

        Parameters
        ----------
        agent_id        : Used to locate agent-specific knowledge folder.
        query_keywords  : Words to score chunks against (e.g. column names,
                          entity type, target layer).
        include_shared  : Also search shared_knowledge/ (default True).
        """
        all_chunks: list[str] = []

        if include_shared:
            all_chunks.extend(self._load_directory(self._base_dir / "shared_knowledge"))

        agent_knowledge_dir = self._base_dir / "agents" / agent_id / "knowledge"
        all_chunks.extend(self._load_directory(agent_knowledge_dir))

        if not all_chunks:
            logger.debug(f"No knowledge chunks found for agent '{agent_id}'")
            return []

        scored = self._score_chunks(all_chunks, query_keywords)
        top_chunks = [chunk for chunk, _ in scored[: self._max_chunks]]

        logger.debug(
            f"[KnowledgeManager] agent={agent_id} | "
            f"{len(top_chunks)}/{len(all_chunks)} chunks selected"
        )
        return top_chunks

    def load_file(self, path: str | Path) -> str:
        """Load a single knowledge file as raw text."""
        p = Path(path)
        if not p.exists():
            logger.warning(f"Knowledge file not found: {p}")
            return ""
        return p.read_text(encoding="utf-8")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_directory(self, directory: Path) -> list[str]:
        """Load and chunk all .md and .json files in a directory."""
        if not directory.exists():
            return []

        chunks: list[str] = []
        for f in sorted(directory.iterdir()):
            if f.suffix in (".md", ".json", ".txt") and f.is_file():
                if f in self._cache:
                    file_chunks = self._cache[f]
                else:
                    text = f.read_text(encoding="utf-8")
                    file_chunks = self._chunk_text(text, source=f.name)
                    self._cache[f] = file_chunks
                chunks.extend(file_chunks)
        return chunks

    def _chunk_text(self, text: str, source: str = "") -> list[str]:
        """
        Split text into chunks of approximately self._chunk_size tokens.
        Splits on double-newlines (paragraph boundaries) first, then
        falls back to hard character splits.
        CHARS_PER_TOKEN = 4 (same constant as token_optimizer).
        """
        max_chars = self._chunk_size * 4
        paragraphs = re.split(r"\n\s*\n", text.strip())
        chunks: list[str] = []
        current = f"[Source: {source}]\n" if source else ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(current) + len(para) + 2 <= max_chars:
                current += para + "\n\n"
            else:
                if current.strip():
                    chunks.append(current.strip())
                current = f"[Source: {source}]\n{para}\n\n"

        if current.strip():
            chunks.append(current.strip())

        return chunks or [text[:max_chars]]

    def _score_chunks(
        self, chunks: list[str], keywords: list[str]
    ) -> list[tuple[str, float]]:
        """
        Score each chunk by keyword frequency (case-insensitive).
        Returns list sorted by score descending.
        """
        kws = [kw.lower() for kw in keywords if kw]
        if not kws:
            return [(c, 0.0) for c in chunks]

        scored = []
        for chunk in chunks:
            lower = chunk.lower()
            score = sum(lower.count(kw) for kw in kws)
            scored.append((chunk, float(score)))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored
