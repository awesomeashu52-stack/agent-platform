"""
core/config_loader.py

Loads and merges the three-level config hierarchy:
    platform.yaml  →  env_<env>.yaml  →  agents/<name>/config.yaml

Usage:
    from core.config_loader import ConfigLoader
    cfg = ConfigLoader()                             # loads platform + env
    agent_cfg = cfg.get_agent_config("data_model_gen")  # merged config
"""

from __future__ import annotations

import os
import copy
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, override: dict) -> dict:
    """
    Recursively merge override into base.
    Lists are replaced (not concatenated). Scalars are overwritten.
    Returns a new dict — neither input is mutated.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _load_yaml(path: Path) -> dict:
    """Load a YAML file and return as dict. Returns {} if file missing."""
    if not path.exists():
        logger.debug(f"Config file not found (skipping): {path}")
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    logger.debug(f"Loaded config: {path}")
    return data


# ---------------------------------------------------------------------------
# ConfigLoader
# ---------------------------------------------------------------------------

class ConfigLoader:
    """
    Central config manager for the agent platform.

    Attributes
    ----------
    platform_cfg : dict
        Fully merged platform + environment config.
    registry : list[dict]
        Flat list of all agent entries from agent_registry.yaml.
    """

    def __init__(self, base_dir: str | Path | None = None):
        """
        Parameters
        ----------
        base_dir : path to the project root (agent_platform/).
                   Defaults to the directory two levels above this file.
        """
        if base_dir is None:
            base_dir = Path(__file__).resolve().parent.parent
        self.base_dir = Path(base_dir)
        self.configs_dir = self.base_dir / "configs"

        self.platform_cfg = self._load_platform_config()
        self.registry = self._load_registry()

        logger.info(
            f"ConfigLoader ready | env={self.environment} | "
            f"{len(self.registry)} agents registered"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def environment(self) -> str:
        return self.platform_cfg.get("platform", {}).get("environment", "dev")

    @property
    def llm_config(self) -> dict:
        return self.platform_cfg.get("llm", {})

    @property
    def token_config(self) -> dict:
        return self.platform_cfg.get("token_optimization", {})

    @property
    def uc_config(self) -> dict:
        return self.platform_cfg.get("unity_catalog", {})

    @property
    def execution_config(self) -> dict:
        return self.platform_cfg.get("execution", {})

    def get_agent_config(self, agent_id: str) -> dict:
        """
        Return fully merged config for a specific agent.

        Merge order (later wins):
            platform.yaml  →  env_<env>.yaml  →  agents/<id>/config.yaml

        Parameters
        ----------
        agent_id : str
            Must match an `id` field in agent_registry.yaml.

        Returns
        -------
        dict with keys: platform, llm, token_optimization, unity_catalog,
                        execution, agent  (agent-specific settings)

        Raises
        ------
        ValueError if agent_id is not found in the registry.
        """
        registry_entry = self._find_registry_entry(agent_id)
        agent_cfg_path = self.base_dir / registry_entry["config_path"]
        agent_overrides = _load_yaml(agent_cfg_path)

        merged = _deep_merge(self.platform_cfg, agent_overrides)

        # Attach the registry metadata under the 'agent' key
        merged["agent"] = registry_entry
        return merged

    def list_agents(
        self,
        agent_type: str | None = None,
        category: str | None = None,
        enabled_only: bool = True,
    ) -> list[dict]:
        """
        Filter and return agents from the registry.

        Parameters
        ----------
        agent_type : "agentic" | "non_agentic" | None (all)
        category   : e.g. "Modelling", "Testing" | None (all)
        enabled_only : skip disabled agents if True (default)
        """
        agents = self.registry
        if enabled_only:
            agents = [a for a in agents if a.get("enabled", True)]
        if agent_type:
            agents = [a for a in agents if a.get("type") == agent_type]
        if category:
            agents = [a for a in agents if a.get("category") == category]
        return agents

    def get_uc_table(self, schema_key: str, table_name: str) -> str:
        """
        Build a fully qualified Unity Catalog table name.

        Example:
            cfg.get_uc_table("outputs", "data_model_gen_results")
            → "agent_platform.outputs.data_model_gen_results"
        """
        catalog = self.uc_config.get("catalog", "agent_platform")
        schema = self.uc_config.get("schemas", {}).get(schema_key, schema_key)
        return f"{catalog}.{schema}.{table_name}"

    def get_volume_path(self, volume_key: str, *sub_paths: str) -> str:
        """
        Build a Unity Catalog volume path.

        Example:
            cfg.get_volume_path("knowledge", "dv2_standards.md")
            → "/Volumes/agent_platform/knowledge/dv2_standards.md"
        """
        base = self.uc_config.get("volumes", {}).get(volume_key, "")
        if sub_paths:
            return str(Path(base) / Path(*sub_paths))
        return base

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_platform_config(self) -> dict:
        """Load platform.yaml then merge env override on top."""
        platform = _load_yaml(self.configs_dir / "platform.yaml")

        # Determine environment: env var takes priority over config file
        env = os.getenv("AGENT_PLATFORM_ENV") or platform.get("platform", {}).get(
            "environment", "dev"
        )
        env_override = _load_yaml(self.configs_dir / f"env_{env}.yaml")

        merged = _deep_merge(platform, env_override)
        return merged

    def _load_registry(self) -> list[dict]:
        """Load agent_registry.yaml and return the agents list."""
        registry_data = _load_yaml(self.configs_dir / "agent_registry.yaml")
        return registry_data.get("agents", [])

    def _find_registry_entry(self, agent_id: str) -> dict:
        """Find registry entry by agent_id. Raises ValueError if not found."""
        for entry in self.registry:
            if entry["id"] == agent_id:
                return entry
        available = [a["id"] for a in self.registry]
        raise ValueError(
            f"Agent '{agent_id}' not found in registry. "
            f"Available agents: {available}"
        )
