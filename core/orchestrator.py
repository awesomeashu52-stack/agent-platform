"""
core/orchestrator.py

Central engine. All tables are always sent in a single LLM call — no batching.
Live run logging via RunLogger records every step with a timestamp.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from core.config_loader      import ConfigLoader
from core.llm_client         import LLMClient, LLMResponse
from core.token_optimizer    import TokenOptimizer
from core.knowledge_manager  import KnowledgeManager
from core.rule_injector      import RuleInjector
from core.prompt_builder     import PromptBuilder
from core.execution_handler  import ExecutionHandler
from core.run_logger         import RunLogger

logger = logging.getLogger(__name__)


class AgentResult:
    def __init__(
        self,
        agent_id:         str,
        status:           str,
        output:           str | dict,
        token_usage:      dict | None = None,
        cost_usd:         float       = 0.0,
        duration_seconds: float       = 0.0,
        run_id:           str         = "",
        output_path:      str         = "",
        error:            str         = "",
        log_entries:      list        = None,
    ):
        self.agent_id         = agent_id
        self.status           = status
        self.output           = output
        self.token_usage      = token_usage or {}
        self.cost_usd         = cost_usd
        self.duration_seconds = duration_seconds
        self.run_id           = run_id
        self.output_path      = output_path
        self.error            = error
        self.log_entries      = log_entries or []
        self.timestamp        = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return self.__dict__


class Orchestrator:

    def __init__(self, base_dir: Path | None = None):
        self._base  = base_dir or Path(__file__).resolve().parent.parent
        self.cfg    = ConfigLoader(base_dir=self._base)
        self.llm    = LLMClient(self.cfg)
        self.to     = TokenOptimizer(self.cfg)
        self.km     = KnowledgeManager(self.cfg, base_dir=self._base)
        self.ri     = RuleInjector(base_dir=self._base)
        self.pb     = PromptBuilder(self.cfg, self.to, self.km, self.ri, base_dir=self._base)
        self.exec_h = ExecutionHandler(self.cfg, base_dir=self._base)
        self.log    = RunLogger.get()
        self._cache: dict[str, AgentResult] = {}
        logger.info(f"Orchestrator ready | env={self.cfg.environment}")

    # ── Public API ────────────────────────────────────────────────────────

    def run(
        self,
        agent_id:      str,
        metadata:      list[dict] | dict,
        user_context:  str  = "",
        task_override: str  = "",
        use_cache:     bool = True,
    ) -> AgentResult:

        run_id = _make_run_id(agent_id)
        start  = time.perf_counter()

        if isinstance(metadata, dict):
            metadata = [metadata]

        self.log.start(run_id, agent_id, len(metadata))
        self.log.clear_old()

        try:
            self.log.step(run_id, "Loading agent configuration…")
            agent_cfg  = self.cfg.get_agent_config(agent_id)
            agent_meta = agent_cfg.get("agent", {})
            agent_type = agent_meta.get("type", "agentic")

            # ── Non-agentic path ─────────────────────────────────────────
            if agent_type == "non_agentic":
                self.log.step(run_id, f"Running deterministic handler ({agent_id})…")
                result_dict = self.exec_h.run_non_agentic(agent_id, metadata, user_context)
                duration    = time.perf_counter() - start
                output_path = self.exec_h.save_output(agent_id, result_dict, run_id=run_id)
                self.log.done(run_id)
                return AgentResult(
                    agent_id=agent_id, status="success",
                    output=result_dict.get("output", result_dict),
                    duration_seconds=round(duration, 3),
                    run_id=run_id, output_path=output_path,
                    log_entries=self.log.get_entries(run_id),
                )

            # ── Cache check ──────────────────────────────────────────────
            cache_enabled = use_cache and self.cfg.token_config.get("cache_enabled", True)
            cache_key = _cache_key(agent_id, metadata, user_context)
            if cache_enabled and cache_key in self._cache:
                self.log.step(run_id, "Cache hit — returning cached result instantly.")
                self.log.done(run_id)
                cached = self._cache[cache_key]
                cached.run_id      = run_id
                cached.log_entries = self.log.get_entries(run_id)
                return cached

            # ── Metadata optimisation ────────────────────────────────────
            self.log.step(run_id, f"Optimising metadata for {len(metadata)} table(s)…")
            optimized  = self.to.optimize_metadata_list(metadata)
            total_cols = sum(len(t.get("columns", [])) for t in optimized)
            self.log.step(
                run_id,
                f"{len(optimized)} table(s) · {total_cols} total columns — "
                "sending all in a single call (no batching)."
            )

            # ── Build prompts ────────────────────────────────────────────
            self.log.step(run_id, "Building system and user prompts…")
            system_p, user_p = self.pb.build(
                agent_id=agent_id, agent_config=agent_cfg,
                metadata=optimized, user_context=user_context,
                task_override=task_override,
            )

            prompt_tokens = self.to.estimate_tokens(system_p + user_p)
            self.log.step(run_id, f"Estimated prompt size: ~{prompt_tokens:,} tokens")

            max_allowed = self.cfg.token_config.get("max_prompt_tokens", 3000)
            if prompt_tokens > max_allowed:
                self.log.warn(
                    run_id,
                    f"Prompt (~{prompt_tokens:,} tokens) is large — "
                    "response may take 60–120s for big schemas."
                )

            # ── LLM call ────────────────────────────────────────────────
            timeout = self.cfg.llm_config.get("timeout_seconds", 120)
            self.log.step(
                run_id,
                f"Calling LLM ({self.llm.model}) — timeout {timeout}s. "
                "Please wait, this can take 30–120s for large schemas…"
            )

            agent_settings = agent_cfg.get("agent_settings", {})
            llm_resp = self.llm.complete(
                prompt=user_p, system=system_p, agent_id=agent_id,
                max_tokens=agent_settings.get("max_tokens"),
                temperature=agent_settings.get("temperature"),
            )

            duration = time.perf_counter() - start
            self.log.step(run_id, f"LLM responded in {duration:.1f}s")
            self.log.step(run_id, "Saving output…")

            # ── Save ─────────────────────────────────────────────────────
            payload = {
                "agent_id":    agent_id,
                "run_id":      run_id,
                "output":      llm_resp.text,
                "token_usage": llm_resp.token_usage,
                "cost_usd":    llm_resp.cost_estimate_usd,
                "duration_s":  round(duration, 3),
                "timestamp":   datetime.now(timezone.utc).isoformat(),
            }
            output_path = self.exec_h.save_output(agent_id, payload, run_id=run_id)

            self.log.done(
                run_id,
                tokens=llm_resp.token_usage.get("total", 0),
                cost=llm_resp.cost_estimate_usd,
            )

            result = AgentResult(
                agent_id=agent_id, status="success",
                output=llm_resp.text,
                token_usage=llm_resp.token_usage,
                cost_usd=llm_resp.cost_estimate_usd,
                duration_seconds=round(duration, 3),
                run_id=run_id, output_path=output_path,
                log_entries=self.log.get_entries(run_id),
            )

            if cache_enabled:
                self._cache[cache_key] = result

            return result

        except Exception as exc:
            duration = time.perf_counter() - start
            self.log.fail(run_id, str(exc))
            logger.error(f"Orchestrator: {agent_id} failed: {exc}", exc_info=True)
            return AgentResult(
                agent_id=agent_id, status="error", output="",
                duration_seconds=round(duration, 3),
                run_id=run_id, error=str(exc),
                log_entries=self.log.get_entries(run_id),
            )

    def list_agents(self, **filters) -> list[dict]:
        return self.cfg.list_agents(**filters)

    def session_cost(self) -> dict:
        return {
            "cumulative_tokens":   self.llm.cumulative_tokens,
            "cumulative_cost_usd": self.llm.cumulative_cost_usd,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_run_id(agent_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{agent_id}_{ts}"


def _cache_key(agent_id: str, metadata: list[dict], context: str) -> str:
    payload = json.dumps(
        {"agent": agent_id, "meta": metadata, "ctx": context},
        sort_keys=True, default=str,
    )
    return hashlib.md5(payload.encode()).hexdigest()
