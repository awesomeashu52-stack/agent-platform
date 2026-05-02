# Databricks notebook source
# =============================================================================
# 00_setup.py — Platform Setup Notebook
#
# Run this ONCE after uploading the agent_platform/ folder to your
# Databricks workspace (DBFS or Repos). It creates the Unity Catalog
# resources the platform needs.
#
# Prerequisites:
#   - Unity Catalog enabled on the workspace
#   - You have CREATE CATALOG privilege or the catalog already exists
# =============================================================================

# COMMAND ----------

# MAGIC %pip install pyyaml databricks-sdk --quiet
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

import sys
sys.path.insert(0, "/Workspace/Users/awesomeashu52@gmail.com/agent_platform")  # adjust path

from core.config_loader import ConfigLoader
cfg = ConfigLoader()

print(f"Platform: {cfg.platform_cfg['platform']['name']}")
print(f"Environment: {cfg.environment}")
print(f"UC Catalog: {cfg.uc_config['catalog']}")
print(f"Agents registered: {len(cfg.registry)}")

# COMMAND ----------

# Create Unity Catalog resources
catalog = cfg.uc_config["catalog"]
schemas = cfg.uc_config["schemas"]

spark.sql(f"CREATE CATALOG IF NOT EXISTS {catalog}")
spark.sql(f"USE CATALOG {catalog}")

for schema in schemas.values():
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
    print(f"  Schema ready: {catalog}.{schema}")

# COMMAND ----------

# Create run_logs table for execution tracking
spark.sql(f"""
CREATE TABLE IF NOT EXISTS {catalog}.{schemas['metadata']}.run_logs (
    run_id        STRING,
    agent_id      STRING,
    status        STRING,
    token_prompt  BIGINT,
    token_completion BIGINT,
    token_total   BIGINT,
    cost_usd      DOUBLE,
    duration_s    DOUBLE,
    error         STRING,
    timestamp     TIMESTAMP
)
USING DELTA
TBLPROPERTIES ('delta.autoOptimize.optimizeWrite' = 'true')
""")
print(f"Run logs table ready: {catalog}.{schemas['metadata']}.run_logs")

# COMMAND ----------

# Verify LLM connectivity
from core.llm_client import LLMClient
client = LLMClient(cfg)
response = client.complete(
    prompt="Reply with exactly: PLATFORM_READY",
    system="You are a test assistant.",
    agent_id="setup_test"
)
print(f"LLM response: {response.text}")
print(f"Tokens used: {response.token_usage}")
print("\n✅ Platform setup complete.")