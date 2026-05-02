# Data Engineering Agent Platform

Modular, token-efficient agent platform built on Databricks with Unity Catalog.

## Quick start on Databricks

```bash
# 1. Upload this folder to Databricks Repos or DBFS
# 2. Install dependencies on your cluster
%pip install pyyaml databricks-sdk

# 3. Run the setup notebook (creates UC schemas and verifies LLM connectivity)
# Open: notebooks/00_setup.py

# 4. Launch the Streamlit UI (Databricks Apps)
streamlit run app/catalog.py
```

## Project structure

```
agent_platform/
├── app/                    # Streamlit UI
│   ├── catalog.py          # Main Agent Catalog application
│   ├── form_builder.py     # Dynamic form utilities
│   └── result_viewer.py    # Output display helpers
├── agents/                 # One folder per agent
│   ├── data_model_gen/     # config.yaml, prompt.md, rules.md, knowledge/
│   ├── test_case_gen/
│   ├── dq_recommender/
│   ├── lineage_creator/
│   ├── sttm_gen/
│   ├── data_profiler/      # Non-agentic (PySpark, no LLM)
│   ├── sample_gen/
│   └── ingestion_cfg/
├── core/                   # Platform backbone
│   ├── config_loader.py    # Three-level config merge
│   ├── llm_client.py       # SSO-authenticated Claude client
│   ├── prompt_builder.py   # System + user prompt assembly
│   ├── token_optimizer.py  # Metadata trimming, budget checks
│   ├── knowledge_manager.py# Chunk loading and retrieval
│   ├── rule_injector.py    # Governance rule injection
│   ├── orchestrator.py     # Central execution engine
│   └── execution_handler.py# Non-agentic handlers + UC output registration
├── configs/
│   ├── platform.yaml       # Global settings (LLM, UC, token limits)
│   ├── agent_registry.yaml # All agents — UI reads this dynamically
│   └── env_dev.yaml        # Dev overrides
├── shared_knowledge/       # Cross-agent Markdown reference files
├── notebooks/              # Databricks notebooks for setup and batch runs
├── outputs/                # Generated artifacts (gitignored in prod)
└── requirements.txt
```

## Adding a new agent

1. Add an entry to `configs/agent_registry.yaml`
2. Create folder `agents/<id>/` with:
   - `config.yaml` — persona, output format, token overrides
   - `prompt.md` — user prompt template with `{{task}}`, `{{metadata}}`, `{{context}}`
   - `rules.md` — governance rules (injected into system prompt)
   - `knowledge/` — agent-specific reference Markdown files (optional)
3. No changes to UI, core, or orchestrator required.

## Environment configuration

Set `AGENT_PLATFORM_ENV=prod` to use `env_prod.yaml` overrides:
```bash
export AGENT_PLATFORM_ENV=prod
```

## Cost management

- Non-agentic agents (data_profiler, sample_gen, ingestion_cfg_gen) use **zero LLM tokens**.
- Token optimizer trims metadata to `max_columns_in_prompt` and `max_sample_values`.
- Prompt cache returns cached responses for identical inputs (configurable TTL).
- Session cost is tracked in the Streamlit sidebar.
