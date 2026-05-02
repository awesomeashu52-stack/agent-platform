"""
test_full_platform.py — Full platform integration test

Tests all modules end-to-end without a live Databricks connection.
Run: python test_full_platform.py
"""

import sys, json, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.WARNING)

from core.config_loader    import ConfigLoader
from core.llm_client       import LLMClient
from core.token_optimizer  import TokenOptimizer
from core.knowledge_manager import KnowledgeManager
from core.rule_injector    import RuleInjector
from core.prompt_builder   import PromptBuilder
from core.orchestrator     import Orchestrator
from core.execution_handler import ExecutionHandler

ROOT  = Path(__file__).parent
PASS  = "  PASS"
FAIL  = "  FAIL"
results = []

def check(label, cond, detail=""):
    results.append((label, cond))
    print(f"{'  PASS' if cond else '  FAIL'} | {label}" + (f"  ({detail})" if detail else ""))

SAMPLE_META = [{
    "table_name": "customer",
    "database": "crm",
    "row_count": 1_200_000,
    "raw_data": [{"customer_id": "C001"}],   # should be stripped
    "columns": [
        {"name": "customer_id",  "data_type": "string",    "nullable": False,
         "null_pct": 0.0, "distinct_count": 1200000,
         "sample_values": ["C001","C002","C003","C004","C005","C006","C007"]},
        {"name": "email",        "data_type": "string",    "nullable": True,
         "null_pct": 0.05, "distinct_count": 1150000},
        {"name": "country_code", "data_type": "string",    "nullable": False,
         "null_pct": 0.0, "distinct_count": 45,
         "sample_values": ["GB","US","DE"]},
        {"name": "created_at",   "data_type": "timestamp", "nullable": False,
         "null_pct": 0.0, "distinct_count": 1200000},
    ]
}]

# ─── Step 1: ConfigLoader ───────────────────────────────────────────────────
print("\n" + "="*60)
print("  ConfigLoader")
print("="*60)
cfg = ConfigLoader(base_dir=ROOT)
check("Platform config loads",     bool(cfg.platform_cfg))
check("Registry has 10 agents",   len(cfg.registry) == 10, str(len(cfg.registry)))
check("Agentic agents = 7",       len(cfg.list_agents(agent_type="agentic")) == 7)
check("Non-agentic agents = 3",   len(cfg.list_agents(agent_type="non_agentic")) == 3)
check("UC table format correct",   cfg.get_uc_table("outputs","t").count(".") == 2)
check("Volume path correct",       "knowledge" in cfg.get_volume_path("knowledge"))

# ─── Step 2: TokenOptimizer ─────────────────────────────────────────────────
print("\n" + "="*60)
print("  TokenOptimizer")
print("="*60)
to = TokenOptimizer(cfg)
opt = to.optimize_metadata(SAMPLE_META[0])
check("raw_data stripped",         "raw_data" not in opt)
check("table_name preserved",      opt.get("table_name") == "customer")
check("row_count preserved",       opt.get("row_count") == 1_200_000)
check("sample_values capped at 5", len(opt["columns"][0]["sample_values"]) <= 5)
check("Token estimator works",     to.estimate_tokens("hello world") > 0)
opt_list = to.optimize_metadata_list(SAMPLE_META)
check("optimize_metadata_list works", len(opt_list) == 1)
chunks = to.fit_knowledge_chunks(["chunk a"*100, "chunk b"*100], reserved_tokens=100)
check("fit_knowledge_chunks returns list", isinstance(chunks, list))

# ─── Step 3: KnowledgeManager ───────────────────────────────────────────────
print("\n" + "="*60)
print("  KnowledgeManager")
print("="*60)
km = KnowledgeManager(cfg, base_dir=ROOT)
chunks = km.get_relevant_chunks("data_model_gen", ["hub", "customer", "hash"])
check("Knowledge chunks returned",  isinstance(chunks, list))
check("Chunks are non-empty strings", all(isinstance(c,str) and len(c)>0 for c in chunks))
check("Max chunks respected",       len(chunks) <= cfg.token_config["max_knowledge_chunks"],
      str(len(chunks)))

# ─── Step 4: RuleInjector ───────────────────────────────────────────────────
print("\n" + "="*60)
print("  RuleInjector")
print("="*60)
ri = RuleInjector(base_dir=ROOT)
rules_block = ri.get_rules_block("data_model_gen")
check("Rules block non-empty",     len(rules_block) > 0)
check("Rules block has header",    "governance rules" in rules_block.lower())
check("Rules are numbered",        "1." in rules_block)
missing = ri.get_rules_block("nonexistent_agent")
check("Missing rules.md returns ''", missing == "")

# ─── Step 5: PromptBuilder ──────────────────────────────────────────────────
print("\n" + "="*60)
print("  PromptBuilder")
print("="*60)
pb = PromptBuilder(cfg, to, km, ri, base_dir=ROOT)
agent_cfg = cfg.get_agent_config("data_model_gen")
sys_p, usr_p = pb.build(
    agent_id="data_model_gen",
    agent_config=agent_cfg,
    metadata=SAMPLE_META,
    user_context="Target silver layer. DV2.0.",
)
check("System prompt non-empty",   len(sys_p) > 100)
check("User prompt non-empty",     len(usr_p) > 100)
check("Metadata in user prompt",   "customer" in usr_p)
check("Rules in system prompt",    "governance" in sys_p.lower() or "rule" in sys_p.lower())
check("Context in user prompt",    "silver" in usr_p.lower() or "silver" in sys_p.lower()
      or "silver" in usr_p)

# ─── Step 6: ExecutionHandler (non-agentic) ─────────────────────────────────
print("\n" + "="*60)
print("  ExecutionHandler (non-agentic)")
print("="*60)
eh = ExecutionHandler(cfg, base_dir=ROOT)
prof = eh.run_non_agentic("data_profiler", SAMPLE_META)
check("Profiler status=success",   prof["status"] == "success")
check("Profiler has profiles key", "profiles" in prof["output"])
check("Profile has column_count",  prof["output"]["profiles"][0]["column_count"] == 4)

samp = eh.run_non_agentic("sample_gen", SAMPLE_META, "5")
check("Sample gen status=success", samp["status"] == "success")
check("Sample rows generated",     len(samp["output"]["samples"][0]["rows"]) > 0)

ing = eh.run_non_agentic("ingestion_cfg_gen", SAMPLE_META)
check("Ingestion cfg status=success", ing["status"] == "success")
check("Ingestion cfg has target",  "target_table" in ing["output"]["ingestion_configs"][0])

# Test save_output
path = eh.save_output("test_agent", {"output": "test_value"}, run_id="test123")
check("save_output returns path",  path.endswith(".json"))
import os
check("Output file exists",        os.path.exists(path))

# ─── Step 7: LLMClient ──────────────────────────────────────────────────────
print("\n" + "="*60)
print("  LLMClient")
print("="*60)
llm = LLMClient(cfg)
resp = llm.complete("Test prompt", system="Test system", agent_id="test")
check("LLMResponse returned",      resp is not None)
check("Response has text",         isinstance(resp.text, str) and len(resp.text) > 0)
check("cost_estimate_usd is float", isinstance(resp.cost_estimate_usd, float))
check("cumulative_cost_usd works", isinstance(llm.cumulative_cost_usd, float))

# ─── Step 8: Orchestrator end-to-end ────────────────────────────────────────
print("\n" + "="*60)
print("  Orchestrator (full end-to-end)")
print("="*60)
orch = Orchestrator(base_dir=ROOT)

# Agentic agent
r1 = orch.run("data_model_gen", SAMPLE_META, "Target: silver layer.")
check("Agentic run returns result",  r1 is not None)
check("Agentic status=success",     r1.status == "success", r1.error)
check("Agentic output non-empty",   len(str(r1.output)) > 0)
check("Agentic has run_id",         bool(r1.run_id))
check("Agentic has duration",       r1.duration_seconds > 0)

# Cache hit
r2 = orch.run("data_model_gen", SAMPLE_META, "Target: silver layer.", use_cache=True)
check("Cache hit returns same text", r1.output == r2.output)

# Non-agentic agent
r3 = orch.run("data_profiler", SAMPLE_META)
check("Non-agentic status=success", r3.status == "success", r3.error)
check("Non-agentic has output",     bool(r3.output))

# Error handling
r4 = orch.run("nonexistent_agent", SAMPLE_META)
check("Unknown agent returns error status", r4.status == "error")
check("Error message populated",   bool(r4.error))

# Session cost
cost = orch.session_cost()
check("session_cost() returns dict", isinstance(cost, dict))
check("session_cost has keys",      "cumulative_tokens" in cost)

# list_agents proxy
agents = orch.list_agents()
check("list_agents() works on orch", len(agents) == 10)

# ─── Summary ────────────────────────────────────────────────────────────────
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
