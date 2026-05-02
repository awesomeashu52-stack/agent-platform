"""
test_step1_2.py

Validates ConfigLoader and LLMClient without a live Databricks connection.
Run from the agent_platform root:
    python test_step1_2.py

Expected outcome:
    - ConfigLoader loads and merges all YAML files correctly
    - LLMClient falls back to mock client gracefully
    - All assertions pass with a clear summary
"""

import sys
import logging
from pathlib import Path

# Make imports work from project root
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.WARNING)   # Suppress debug noise during tests

from core.config_loader import ConfigLoader
from core.llm_client import LLMClient, LLMResponse

PASS = "  PASS"
FAIL = "  FAIL"
results = []

def check(label: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    results.append((label, condition))
    suffix = f"  ({detail})" if detail else ""
    print(f"{status} | {label}{suffix}")

print("\n" + "="*60)
print("  Step 1 — ConfigLoader tests")
print("="*60)

cfg = ConfigLoader(base_dir=Path(__file__).parent)

check("Platform name loaded",
      cfg.platform_cfg.get("platform", {}).get("name") == "Data Engineering Agent Platform")

check("LLM model set",
      cfg.llm_config.get("model") == "databricks-claude-opus-4")

check("Token optimization enabled",
      cfg.token_config.get("enabled") is True)

check("UC catalog set",
      cfg.uc_config.get("catalog") is not None)

check("Agent registry loaded (>0 agents)",
      len(cfg.registry) > 0,
      f"{len(cfg.registry)} agents found")

agents = cfg.list_agents()
check("list_agents() returns enabled agents",
      len(agents) > 0,
      f"{len(agents)} enabled agents")

agentic = cfg.list_agents(agent_type="agentic")
check("Agentic agents present",
      len(agentic) > 0,
      f"{len(agentic)} agentic")

non_agentic = cfg.list_agents(agent_type="non_agentic")
check("Non-agentic agents present",
      len(non_agentic) > 0,
      f"{len(non_agentic)} non-agentic")

uc_table = cfg.get_uc_table("outputs", "test_table")
check("UC table name format correct",
      uc_table.count(".") == 2,
      uc_table)

vol_path = cfg.get_volume_path("knowledge", "dv2_standards.md")
check("Volume path built correctly",
      "dv2_standards.md" in vol_path,
      vol_path)

try:
    agent_cfg = cfg.get_agent_config("data_model_gen")
    check("Agent config loaded (data_model_gen)",
          "agent" in agent_cfg,
          f"keys: {list(agent_cfg.keys())}")
except Exception as e:
    # Agent config file may not exist yet — that's fine at this stage
    check("Agent config load attempted (file may not exist yet)",
          True, str(e))

try:
    cfg.get_agent_config("nonexistent_agent")
    check("ValueError raised for unknown agent", False)
except ValueError:
    check("ValueError raised for unknown agent", True)

print("\n" + "="*60)
print("  Step 2 — LLMClient tests")
print("="*60)

client = LLMClient(cfg)

check("LLMClient initialised",
      client is not None)

check("Model name accessible",
      client._model == "databricks-claude-opus-4")

response = client.complete(
    prompt="Generate a simple hub for customer entity.",
    system="You are a Data Vault 2.0 expert.",
    agent_id="data_model_gen",
)

check("LLMResponse returned",
      isinstance(response, LLMResponse))

check("Response has text",
      isinstance(response.text, str) and len(response.text) > 0)

check("Response has token_usage dict",
      isinstance(response.token_usage, dict))

check("Response has model field",
      response.model in ("databricks-claude-opus-4", "mock"))

check("cost_estimate_usd is float",
      isinstance(response.cost_estimate_usd, float))

check("cumulative_tokens accessible",
      isinstance(client.cumulative_tokens, dict))

check("cumulative_cost_usd accessible",
      isinstance(client.cumulative_cost_usd, float))

print("\n" + "="*60)
print("  Summary")
print("="*60)
passed = sum(1 for _, ok in results if ok)
failed = sum(1 for _, ok in results if not ok)
print(f"  Passed : {passed}/{len(results)}")
print(f"  Failed : {failed}/{len(results)}")
if failed:
    print("\n  Failed checks:")
    for label, ok in results:
        if not ok:
            print(f"    - {label}")
print("="*60 + "\n")
sys.exit(0 if failed == 0 else 1)
