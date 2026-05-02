# Databricks notebook source
# =============================================================================
# 01_run_agent.py — Run any agent from a notebook
#
# Use this as an alternative to the Streamlit UI for scheduled runs,
# pipeline integration, or debugging.
# =============================================================================

# COMMAND ----------

# MAGIC %pip install pyyaml --quiet

# COMMAND ----------

import sys, json
sys.path.insert(0, "/Workspace/Users/awesomeashu52@gmail.com/agent_platform")

from core.orchestrator import Orchestrator

orch = Orchestrator()

# COMMAND ----------

# Configure your run here
AGENT_ID = "data_model_gen"   # Change to any agent id from the registry

METADATA = [{
    "table_name": "customer",
    "database": "crm_prod",
    "row_count": 5_000_000,
    "columns": [
        {"name": "customer_id",  "data_type": "string",    "nullable": False,
         "distinct_count": 5000000, "null_pct": 0.0, "sample_values": ["CUST-001"]},
        {"name": "full_name",    "data_type": "string",    "nullable": True,
         "distinct_count": 4800000, "null_pct": 0.02},
        {"name": "email",        "data_type": "string",    "nullable": True,
         "distinct_count": 4950000, "null_pct": 0.01},
        {"name": "country",      "data_type": "string",    "nullable": False,
         "distinct_count": 80,    "null_pct": 0.0, "sample_values": ["UK", "US"]},
        {"name": "signup_date",  "data_type": "date",      "nullable": False,
         "distinct_count": 1800,  "null_pct": 0.0},
    ]
}]

USER_CONTEXT = "Target is the silver layer. Use SHA-256 for hash keys. Source is Salesforce CRM."

# COMMAND ----------

result = orch.run(
    agent_id=AGENT_ID,
    metadata=METADATA,
    user_context=USER_CONTEXT,
)

print(f"Status  : {result.status}")
print(f"Run ID  : {result.run_id}")
print(f"Duration: {result.duration_seconds}s")
print(f"Tokens  : {result.token_usage}")
print(f"Cost    : ${result.cost_usd:.4f}")
print(f"Output  :")
print(result.output)