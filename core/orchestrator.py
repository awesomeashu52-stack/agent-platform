"""
core/orchestrator.py

Central engine that wires together all core modules and executes an agent run.

Call flow
---------
  Orchestrator.run(agent_id, metadata, user_context)
      │
      ├─ ConfigLoader.get_agent_config()     → merged config
      ├─ TokenOptimizer.optimize_metadata()  → trimmed metadata
      ├─ [agentic]  PromptBuilder.build()    → (system, user) prompts
      │             LLMClient.complete()     → LLMResponse
      └─ [non-agentic] ExecutionHandler.run_non_agentic() → result dict
      │
      └─ ExecutionHandler.save_output()      → persisted to disk / UC
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from core.config_loader    import ConfigLoader
from core.llm_client       import LLMClient, LLMResponse
from core.token_optimizer  import TokenOptimizer
from core.knowledge_manager import KnowledgeManager
from core.rule_injector    import RuleInjector
from core.prompt_builder   import PromptBuilder
from core.execution_handler import ExecutionHandler

logger = logging.getLogger(__name__)


class AgentResult:
    """Unified result object returned from every agent run."""

    def __init__(
        self,
        agent_id: str,
        status: str,                        # "success" | "error"
        output: str | dict,
        token_usage: dict | None = None,
        cost_usd: float = 0.0,
        duration_seconds: float = 0.0,
        run_id: str = "",
        output_path: str = "",
        error: str = "",
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
        self.timestamp        = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return self.__dict__

    def __repr__(self) -> str:
        return (
            f"AgentResult(agent={self.agent_id}, status={self.status}, "
            f"tokens={self.token_usage.get('total', '?')}, "
            f"cost=${self.cost_usd})"
        )


class Orchestrator:
    """
    Single entry point for all agent executions.

    Usage (Streamlit / notebook):
        from core.orchestrator import Orchestrator
        orch   = Orchestrator()
        result = orch.run(
            agent_id     = "data_model_gen",
            metadata     = [{"table_name": "customer", "columns": [...]}],
            user_context = "Target layer is silver. Use Data Vault 2.0.",
        )
        print(result.output)
    """

    def __init__(self, base_dir: Path | None = None):
        self._base   = base_dir or Path(__file__).resolve().parent.parent

        # Instantiate all core modules once — shared across calls
        self.cfg      = ConfigLoader(base_dir=self._base)
        self.llm      = LLMClient(self.cfg)
        self.to       = TokenOptimizer(self.cfg)
        self.km       = KnowledgeManager(self.cfg, base_dir=self._base)
        self.ri       = RuleInjector(base_dir=self._base)
        self.pb       = PromptBuilder(self.cfg, self.to, self.km, self.ri, base_dir=self._base)
        self.exec_h   = ExecutionHandler(self.cfg, base_dir=self._base)

        # Simple in-process prompt cache (agent_id + metadata hash → LLMResponse)
        self._prompt_cache: dict[str, LLMResponse] = {}

        logger.info(f"Orchestrator ready | env={self.cfg.environment}")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        agent_id: str,
        metadata: list[dict] | dict,
        user_context: str = "",
        task_override: str = "",
        use_cache: bool = True,
    ) -> AgentResult:
        """
        Execute a single agent and return an AgentResult.

        Parameters
        ----------
        agent_id      : Must match an id in agent_registry.yaml
        metadata      : Table metadata dict(s) — raw data is stripped automatically
        user_context  : Free-text instructions from the user (e.g. "use DV 2.0")
        task_override : Override the agent's default task description
        use_cache     : Return cached response if inputs are identical (default True)
        """
        import time
        start = time.perf_counter()
        run_id = _make_run_id(agent_id)

        logger.info(f"Orchestrator.run | agent={agent_id} | run_id={run_id}")

        try:
            agent_cfg = self.cfg.get_agent_config(agent_id)
            agent_meta = agent_cfg.get("agent", {})
            agent_type = agent_meta.get("type", "agentic")

            # Normalize metadata
            if isinstance(metadata, dict):
                metadata = [metadata]

            # --- Non-agentic path ---
            if agent_type == "non_agentic":
                result_dict = self.exec_h.run_non_agentic(
                    agent_id, metadata, user_context
                )
                duration = time.perf_counter() - start
                output_path = self.exec_h.save_output(
                    agent_id, result_dict, run_id=run_id
                )
                return AgentResult(
                    agent_id=agent_id,
                    status="success",
                    output=result_dict.get("output", result_dict),
                    duration_seconds=round(duration, 3),
                    run_id=run_id,
                    output_path=output_path,
                )

            # --- Agentic path ---
            # Optimize metadata
            optimized_meta = self.to.optimize_metadata_list(metadata)

            # Check prompt cache
            cache_key = _cache_key(agent_id, optimized_meta, user_context)
            cache_enabled = (
                use_cache
                and self.cfg.token_config.get("cache_enabled", True)
            )

            if cache_enabled and cache_key in self._prompt_cache:
                logger.info(f"[Orchestrator] Cache hit for {agent_id} (run_id={run_id})")
                llm_response = self._prompt_cache[cache_key]
            else:
                # Build prompts
                system_prompt, user_prompt = self.pb.build(
                    agent_id=agent_id,
                    agent_config=agent_cfg,
                    metadata=optimized_meta,
                    user_context=user_context,
                    task_override=task_override,
                )

                # Call LLM
                agent_settings = agent_cfg.get("agent_settings", {})
                llm_response = self.llm.complete(
                    prompt=user_prompt,
                    system=system_prompt,
                    agent_id=agent_id,
                    max_tokens=agent_settings.get("max_tokens"),
                    temperature=agent_settings.get("temperature"),
                )

                if cache_enabled:
                    self._prompt_cache[cache_key] = llm_response

            duration = time.perf_counter() - start

            # Persist output
            result_payload = {
                "agent_id":    agent_id,
                "run_id":      run_id,
                "output":      llm_response.text,
                "token_usage": llm_response.token_usage,
                "cost_usd":    llm_response.cost_estimate_usd,
                "duration_s":  round(duration, 3),
                "timestamp":   datetime.now(timezone.utc).isoformat(),
            }
            output_path = self.exec_h.save_output(
                agent_id, result_payload, run_id=run_id
            )

            return AgentResult(
                agent_id=agent_id,
                status="success",
                output=llm_response.text,
                token_usage=llm_response.token_usage,
                cost_usd=llm_response.cost_estimate_usd,
                duration_seconds=round(duration, 3),
                run_id=run_id,
                output_path=output_path,
            )

        except Exception as exc:
            duration = time.perf_counter() - start
            logger.error(f"[Orchestrator] Agent '{agent_id}' failed: {exc}", exc_info=True)
            return AgentResult(
                agent_id=agent_id,
                status="error",
                output="",
                duration_seconds=round(duration, 3),
                run_id=run_id,
                error=str(exc),
            )

    def list_agents(self, **filters) -> list[dict]:
        """Convenience proxy to ConfigLoader.list_agents()."""
        return self.cfg.list_agents(**filters)

    def session_cost(self) -> dict:
        """Total cost for all LLM calls in this session."""
        return {
            "cumulative_tokens": self.llm.cumulative_tokens,
            "cumulative_cost_usd": self.llm.cumulative_cost_usd,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_run_id(agent_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{agent_id}_{ts}"


def _cache_key(agent_id: str, metadata: list[dict], context: str) -> str:
    payload = json.dumps(
        {"agent": agent_id, "meta": metadata, "ctx": context},
        sort_keys=True, default=str,
    )
    return hashlib.md5(payload.encode()).hexdigest()
