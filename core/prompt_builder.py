"""
core/prompt_builder.py

Assembles the final system + user prompt pair for each agent call.

Assembly order (system prompt):
  1. Agent persona / role definition
  2. Output format instructions
  3. Governance rules (from RuleInjector)
  4. Relevant knowledge chunks (from KnowledgeManager)

User prompt:
  1. Task description
  2. Optimized metadata (from TokenOptimizer)
  3. User-supplied context / constraints

Each agent defines its own prompt.md template with placeholders:
  {{task}}        → what the user asked for
  {{metadata}}    → JSON-serialized optimized metadata
  {{context}}     → any extra user context
  {{rules}}       → injected at system level (not in user prompt)
  {{knowledge}}   → injected at system level (not in user prompt)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from string import Template

logger = logging.getLogger(__name__)


class PromptBuilder:
    """
    Builds system + user prompt pairs for agent LLM calls.

    Parameters
    ----------
    config_loader    : ConfigLoader
    token_optimizer  : TokenOptimizer
    knowledge_manager: KnowledgeManager
    rule_injector    : RuleInjector
    base_dir         : Project root path.
    """

    def __init__(
        self,
        config_loader,
        token_optimizer,
        knowledge_manager,
        rule_injector,
        base_dir: Path | None = None,
    ):
        self._cfg   = config_loader
        self._to    = token_optimizer
        self._km    = knowledge_manager
        self._ri    = rule_injector
        self._base  = base_dir or Path(__file__).resolve().parent.parent

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        agent_id: str,
        agent_config: dict,
        metadata: list[dict] | dict,
        user_context: str = "",
        task_override: str = "",
    ) -> tuple[str, str]:
        """
        Build the (system_prompt, user_prompt) pair for one agent call.

        Parameters
        ----------
        agent_id       : e.g. "data_model_gen"
        agent_config   : Merged config from ConfigLoader.get_agent_config()
        metadata       : Optimized table metadata (list of dicts or single dict)
        user_context   : Free-text extra instructions from the UI form
        task_override  : If set, replaces the default task from agent config

        Returns
        -------
        (system_prompt, user_prompt) — both strings ready for LLMClient.complete()
        """
        agent_section = agent_config.get("agent", {})
        agent_settings = agent_config.get("agent_settings", {})

        # --- Normalize metadata ---
        if isinstance(metadata, dict):
            metadata = [metadata]
        optimized_meta = self._to.optimize_metadata_list(metadata)

        # --- Extract keywords for knowledge retrieval ---
        keywords = self._extract_keywords(optimized_meta, user_context, agent_id)

        # --- Fetch relevant knowledge chunks ---
        chunks = self._km.get_relevant_chunks(agent_id, keywords)

        # --- Load governance rules ---
        rules_block = self._ri.get_rules_block(agent_id)

        # --- Build system prompt ---
        system_prompt = self._build_system_prompt(
            agent_id=agent_id,
            agent_section=agent_section,
            agent_settings=agent_settings,
            rules_block=rules_block,
            knowledge_chunks=chunks,
        )

        # --- Build user prompt ---
        task = task_override or agent_settings.get(
            "default_task", f"Perform the {agent_id} task."
        )
        user_prompt = self._build_user_prompt(
            agent_id=agent_id,
            task=task,
            optimized_meta=optimized_meta,
            user_context=user_context,
            agent_settings=agent_settings,
        )

        # --- Token budget check ---
        combined = system_prompt + "\n" + user_prompt
        self._to.check_budget(combined, label=f"{agent_id} combined prompt")

        logger.info(
            f"[PromptBuilder] agent={agent_id} | "
            f"system={self._to.estimate_tokens(system_prompt)} tokens | "
            f"user={self._to.estimate_tokens(user_prompt)} tokens | "
            f"knowledge_chunks={len(chunks)} | "
            f"rules={'yes' if rules_block else 'no'}"
        )

        return system_prompt, user_prompt

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def _build_system_prompt(
        self,
        agent_id: str,
        agent_section: dict,
        agent_settings: dict,
        rules_block: str,
        knowledge_chunks: list[str],
    ) -> str:
        parts: list[str] = []

        # 1. Persona
        persona = agent_settings.get(
            "system_persona",
            f"You are an expert data engineering assistant specialising in {agent_id.replace('_', ' ')}.",
        )
        parts.append(persona)

        # 2. Output format
        output_format = agent_settings.get("output_format_instruction", "")
        if output_format:
            parts.append(f"\n## Output format\n{output_format}")

        # 3. Governance rules
        if rules_block:
            parts.append(f"\n{rules_block}")

        # 4. Knowledge
        if knowledge_chunks:
            knowledge_text = "\n\n---\n\n".join(knowledge_chunks)
            parts.append(
                f"\n## Reference knowledge\n"
                f"Use the following knowledge to inform your response:\n\n"
                f"{knowledge_text}"
            )

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # User prompt
    # ------------------------------------------------------------------

    def _build_user_prompt(
        self,
        agent_id: str,
        task: str,
        optimized_meta: list[dict],
        user_context: str,
        agent_settings: dict,
    ) -> str:
        # Try loading agent's prompt.md template
        template_path = self._base / "agents" / agent_id / "prompt.md"
        if template_path.exists():
            return self._render_template(
                template_path=template_path,
                task=task,
                optimized_meta=optimized_meta,
                user_context=user_context,
            )

        # Fallback: structured default prompt
        return self._default_user_prompt(task, optimized_meta, user_context)

    def _render_template(
        self,
        template_path: Path,
        task: str,
        optimized_meta: list[dict],
        user_context: str,
    ) -> str:
        raw = template_path.read_text(encoding="utf-8")
        meta_json = json.dumps(optimized_meta, indent=2, default=str)

        # Simple {{placeholder}} substitution
        result = raw
        result = result.replace("{{task}}", task)
        result = result.replace("{{metadata}}", meta_json)
        result = result.replace("{{context}}", user_context or "None provided.")
        return result

    def _default_user_prompt(
        self,
        task: str,
        optimized_meta: list[dict],
        user_context: str,
    ) -> str:
        meta_json = json.dumps(optimized_meta, indent=2, default=str)
        parts = [f"## Task\n{task}"]
        parts.append(f"\n## Table metadata\n```json\n{meta_json}\n```")
        if user_context:
            parts.append(f"\n## Additional context\n{user_context}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Keyword extraction
    # ------------------------------------------------------------------

    def _extract_keywords(
        self,
        metadata_list: list[dict],
        user_context: str,
        agent_id: str,
    ) -> list[str]:
        """
        Extract keywords for knowledge chunk scoring from metadata + context.
        """
        keywords: set[str] = set()

        # Agent id words (e.g. "data", "model", "gen")
        keywords.update(agent_id.split("_"))

        # Table and column names
        for meta in metadata_list:
            if name := meta.get("table_name"):
                keywords.add(name.lower())
            for col in meta.get("columns", []):
                if col_name := col.get("name"):
                    keywords.add(col_name.lower())

        # Words from user context (filter short stop words)
        stop = {"the", "a", "an", "and", "or", "is", "in", "of", "to", "for"}
        for word in user_context.lower().split():
            clean = word.strip(".,;:()'\"")
            if len(clean) > 3 and clean not in stop:
                keywords.add(clean)

        return list(keywords)
