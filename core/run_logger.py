"""
core/run_logger.py

Thread-safe in-process run logger.
Stores timestamped log entries for each run_id so the UI can poll
and display live progress without needing a database or file system.

Usage
-----
    from core.run_logger import RunLogger
    log = RunLogger.get()           # singleton

    log.start(run_id, agent_id, total_steps)
    log.step(run_id, "Optimising metadata…")
    log.step(run_id, "Building prompt…")
    log.step(run_id, "Calling LLM (this may take 30–90s)…")
    log.done(run_id, tokens=450, cost=0.0023)
    log.fail(run_id, "Timeout after 120s")

    entries = log.get_entries(run_id)   # list of {time, msg, level}
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class LogEntry:
    timestamp: str
    message:   str
    level:     str = "info"   # info | warn | error | done


class RunLogger:
    """Thread-safe singleton run logger."""

    _instance: ClassVar[RunLogger | None] = None
    _lock:     ClassVar[threading.Lock]   = threading.Lock()

    def __init__(self):
        self._runs:  dict[str, list[LogEntry]] = {}
        self._rlock: threading.Lock = threading.Lock()

    @classmethod
    def get(cls) -> RunLogger:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── Write ──────────────────────────────────────────────────────────────

    def start(self, run_id: str, agent_id: str, total_tables: int) -> None:
        with self._rlock:
            self._runs[run_id] = []
        self._append(run_id, f"▶  Starting {agent_id} on {total_tables} table(s)…", "info")

    def step(self, run_id: str, message: str) -> None:
        self._append(run_id, f"   {message}", "info")

    def warn(self, run_id: str, message: str) -> None:
        self._append(run_id, f"⚠️  {message}", "warn")

    def done(self, run_id: str, tokens: int = 0, cost: float = 0.0) -> None:
        self._append(
            run_id,
            f"✅ Done — {tokens:,} tokens · est. ${cost:.4f}",
            "done",
        )

    def fail(self, run_id: str, error: str) -> None:
        self._append(run_id, f"❌ Failed: {error}", "error")

    # ── Read ───────────────────────────────────────────────────────────────

    def get_entries(self, run_id: str) -> list[LogEntry]:
        with self._rlock:
            return list(self._runs.get(run_id, []))

    def is_done(self, run_id: str) -> bool:
        entries = self.get_entries(run_id)
        return any(e.level in ("done", "error") for e in entries)

    def clear_old(self, keep_last: int = 20) -> None:
        """Keep only the most recent N run logs to avoid memory growth."""
        with self._rlock:
            keys = list(self._runs.keys())
            for k in keys[:-keep_last]:
                del self._runs[k]

    # ── Internal ───────────────────────────────────────────────────────────

    def _append(self, run_id: str, message: str, level: str) -> None:
        entry = LogEntry(
            timestamp=datetime.now(timezone.utc).strftime("%H:%M:%S"),
            message=message,
            level=level,
        )
        with self._rlock:
            if run_id not in self._runs:
                self._runs[run_id] = []
            self._runs[run_id].append(entry)
