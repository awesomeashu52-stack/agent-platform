"""
app/catalog.py — Agent Catalog UI

Streamlit application hosted on Databricks.
Run with:  streamlit run app/catalog.py

Architecture:
  - Reads agent registry from ConfigLoader (no hardcoded agent list)
  - Builds input forms dynamically per agent
  - Calls Orchestrator.run() and displays structured output
  - Shows session-level cost and token tracking in the sidebar
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import streamlit as st

# Make project root importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.orchestrator import Orchestrator

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Data Engineering Agent Platform",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "orchestrator" not in st.session_state:
    st.session_state.orchestrator = Orchestrator(base_dir=ROOT)
if "results_history" not in st.session_state:
    st.session_state.results_history = []

orch: Orchestrator = st.session_state.orchestrator

# ---------------------------------------------------------------------------
# Sidebar — catalog + session stats
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("## 🤖 Agent Catalog")
    st.markdown("---")

    # Category filter
    all_agents  = orch.list_agents(enabled_only=True)
    categories  = sorted({a["category"] for a in all_agents})
    sel_category = st.selectbox("Filter by category", ["All"] + categories)

    # Type filter
    sel_type = st.radio("Agent type", ["All", "Agentic (LLM)", "Non-agentic (free)"],
                        horizontal=True)
    type_map = {"All": None, "Agentic (LLM)": "agentic", "Non-agentic (free)": "non_agentic"}

    filtered = orch.list_agents(
        agent_type=type_map[sel_type],
        category=None if sel_category == "All" else sel_category,
    )

    # Agent selector
    agent_labels = {f"{a['icon']}  {a['display_name']}": a["id"] for a in filtered}
    chosen_label = st.radio("Select agent", list(agent_labels.keys()))
    chosen_id    = agent_labels[chosen_label]
    chosen_meta  = next(a for a in filtered if a["id"] == chosen_id)

    st.markdown("---")
    st.markdown("### 📊 Session stats")
    cost_info = orch.session_cost()
    tokens    = cost_info["cumulative_tokens"]
    st.metric("Total tokens used",  tokens.get("total", 0))
    st.metric("Estimated cost",     f"${cost_info['cumulative_cost_usd']:.4f}")
    st.metric("Runs this session",  len(st.session_state.results_history))

    if st.button("🔄 Reset session stats"):
        orch.llm.reset_cumulative_tokens()
        st.session_state.results_history = []
        st.rerun()

# ---------------------------------------------------------------------------
# Main panel
# ---------------------------------------------------------------------------
st.title("🏗️ Data Engineering Agent Platform")
st.caption(f"Environment: `{orch.cfg.environment}` · Model: `{orch.cfg.llm_config.get('model')}`")

# Agent header
col1, col2 = st.columns([1, 6])
with col1:
    st.markdown(f"<div style='font-size:3rem'>{chosen_meta['icon']}</div>", unsafe_allow_html=True)
with col2:
    st.subheader(chosen_meta["display_name"])
    st.caption(chosen_meta["description"])
    badge_color = "#1f77b4" if chosen_meta["type"] == "agentic" else "#2ca02c"
    badge_label = "LLM-powered" if chosen_meta["type"] == "agentic" else "No LLM cost"
    st.markdown(
        f"<span style='background:{badge_color};color:white;padding:2px 10px;"
        f"border-radius:12px;font-size:0.75rem'>{badge_label}</span> &nbsp;"
        + " ".join(f"`{t}`" for t in chosen_meta.get("tags", [])),
        unsafe_allow_html=True,
    )

st.markdown("---")

# ---------------------------------------------------------------------------
# Input form
# ---------------------------------------------------------------------------
with st.form(key="agent_form"):
    st.markdown("### ⚙️ Configure run")

    # --- Metadata input ---
    st.markdown("#### Table metadata")
    st.caption(
        "Paste JSON metadata for one or more tables. "
        "Raw data is automatically stripped — only schema info is sent to the LLM."
    )

    default_meta = json.dumps([{
        "table_name": "customer",
        "database": "crm",
        "row_count": 1200000,
        "columns": [
            {"name": "customer_id",   "data_type": "string",    "nullable": False,
             "null_pct": 0.0, "distinct_count": 1200000, "sample_values": ["C001", "C002"]},
            {"name": "first_name",    "data_type": "string",    "nullable": True,
             "null_pct": 0.01, "distinct_count": 45000, "sample_values": ["Alice", "Bob"]},
            {"name": "last_name",     "data_type": "string",    "nullable": True,
             "null_pct": 0.01, "distinct_count": 80000, "sample_values": ["Smith", "Jones"]},
            {"name": "email",         "data_type": "string",    "nullable": True,
             "null_pct": 0.05, "distinct_count": 1150000, "sample_values": ["a@b.com"]},
            {"name": "date_of_birth", "data_type": "date",      "nullable": True,
             "null_pct": 0.08, "distinct_count": 25000},
            {"name": "country_code",  "data_type": "string",    "nullable": False,
             "null_pct": 0.0, "distinct_count": 45, "sample_values": ["GB", "US", "DE"]},
            {"name": "created_at",    "data_type": "timestamp", "nullable": False,
             "null_pct": 0.0, "distinct_count": 1200000},
        ]
    }], indent=2)

    metadata_input = st.text_area(
        "Metadata JSON",
        value=default_meta,
        height=300,
        help="Must be a JSON array of table metadata objects.",
    )

    # --- Agent-specific options ---
    col_a, col_b = st.columns(2)
    with col_a:
        if chosen_id in ("data_model_gen",):
            model_type = st.selectbox(
                "Model type",
                ["Data Vault 2.0 (silver layer)", "Dimensional model (gold layer)"]
            )
        if chosen_id in ("sample_gen",):
            num_rows = st.number_input("Number of sample rows", min_value=1, max_value=50, value=5)
        if chosen_id in ("ingestion_cfg_gen",):
            load_type = st.selectbox("Load type", ["incremental", "full", "streaming"])

    with col_b:
        use_cache = st.checkbox("Use prompt cache", value=True,
                                help="Return cached result if same inputs were run before.")

    # --- Free-text context ---
    st.markdown("#### Additional context (optional)")
    user_context = st.text_area(
        "Any extra instructions, constraints, or domain context",
        placeholder="e.g. 'Target is the silver layer. Use SHA-256 for hash keys. Source system is Salesforce.'",
        height=100,
    )

    submitted = st.form_submit_button("▶  Run agent", type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
if submitted:
    # Validate metadata JSON
    try:
        metadata = json.loads(metadata_input)
        if isinstance(metadata, dict):
            metadata = [metadata]
    except json.JSONDecodeError as e:
        st.error(f"❌ Invalid metadata JSON: {e}")
        st.stop()

    # Build context string
    context_parts = []
    if user_context:
        context_parts.append(user_context)
    if chosen_id == "data_model_gen" and "model_type" in locals():
        context_parts.append(f"Model type: {model_type}")
    if chosen_id == "sample_gen" and "num_rows" in locals():
        context_parts.append(str(num_rows))
    if chosen_id == "ingestion_cfg_gen" and "load_type" in locals():
        context_parts.append(f"Load type: {load_type}")
    full_context = "\n".join(context_parts)

    # Run
    with st.spinner(f"Running {chosen_meta['display_name']}…"):
        result = orch.run(
            agent_id=chosen_id,
            metadata=metadata,
            user_context=full_context,
            use_cache=use_cache,
        )

    st.session_state.results_history.append(result)

    # Result display
    st.markdown("---")
    if result.status == "success":
        st.success(
            f"✅ Completed in {result.duration_seconds}s · "
            f"{result.token_usage.get('total', 0)} tokens · "
            f"est. cost ${result.cost_usd:.4f}",
        )

        st.markdown("### 📄 Output")

        # Try to parse as JSON for pretty display
        output = result.output
        parsed_json = None
        if isinstance(output, str):
            try:
                parsed_json = json.loads(output)
            except (json.JSONDecodeError, TypeError):
                pass
        elif isinstance(output, dict):
            parsed_json = output

        tab_pretty, tab_raw, tab_download = st.tabs(["📋 Formatted", "🔤 Raw", "⬇️ Download"])

        with tab_pretty:
            if parsed_json:
                _render_structured_output(chosen_id, parsed_json)
            else:
                st.markdown(output if isinstance(output, str) else str(output))

        with tab_raw:
            if parsed_json:
                st.json(parsed_json)
            else:
                st.code(str(output), language="sql" if "sql" in chosen_id else "text")

        with tab_download:
            download_str = (
                json.dumps(parsed_json, indent=2)
                if parsed_json
                else str(output)
            )
            st.download_button(
                label="Download output as JSON",
                data=download_str,
                file_name=f"{result.run_id}.json",
                mime="application/json",
            )
            st.caption(f"Run ID: `{result.run_id}`")
            if result.output_path:
                st.caption(f"Saved to: `{result.output_path}`")

    else:
        st.error(f"❌ Agent run failed: {result.error}")
        with st.expander("Error details"):
            st.code(result.error)

# ---------------------------------------------------------------------------
# History panel
# ---------------------------------------------------------------------------
if st.session_state.results_history:
    st.markdown("---")
    with st.expander(f"📜 Run history ({len(st.session_state.results_history)} runs this session)"):
        for r in reversed(st.session_state.results_history[-10:]):
            status_icon = "✅" if r.status == "success" else "❌"
            st.markdown(
                f"{status_icon} **{r.agent_id}** · `{r.run_id}` · "
                f"{r.duration_seconds}s · {r.token_usage.get('total', 0)} tokens"
            )


# ---------------------------------------------------------------------------
# Structured output renderer
# ---------------------------------------------------------------------------
def _render_structured_output(agent_id: str, data: dict) -> None:
    """Render agent-specific structured JSON output nicely in Streamlit."""

    if agent_id == "data_model_gen":
        entities = data.get("entities", [])
        st.markdown(f"**Model type:** `{data.get('model_type', 'N/A')}` · "
                    f"**Target layer:** `{data.get('target_layer', 'N/A')}` · "
                    f"**Entities:** {len(entities)}")
        for entity in entities:
            with st.expander(f"{entity.get('entity_type', '').upper()} — `{entity.get('table_name')}`"):
                st.caption(entity.get("description", ""))
                cols = entity.get("columns", [])
                if cols:
                    st.dataframe(cols, use_container_width=True)
                st.markdown(f"**Load strategy:** `{entity.get('load_strategy', 'N/A')}`")
        if notes := data.get("notes"):
            st.info(f"📝 {notes}")

    elif agent_id == "test_case_gen":
        cases = data.get("test_cases", [])
        st.markdown(f"**{data.get('test_suite', '')}** · {len(cases)} test cases")
        p1 = [c for c in cases if c.get("priority") == "P1"]
        p2 = [c for c in cases if c.get("priority") == "P2"]
        p3 = [c for c in cases if c.get("priority") == "P3"]
        for priority, group in [("🔴 P1 — Critical", p1), ("🟠 P2 — High", p2), ("🟡 P3 — Medium", p3)]:
            if group:
                st.markdown(f"**{priority}**")
                for tc in group:
                    with st.expander(f"`{tc.get('test_id')}` {tc.get('description')}"):
                        st.markdown(f"**Category:** `{tc.get('category')}`")
                        st.markdown(f"**Input condition:** {tc.get('input_condition')}")
                        st.markdown(f"**Expected result:** {tc.get('expected_result')}")
                        if sql := tc.get("validation_sql"):
                            st.code(sql, language="sql")

    elif agent_id == "dq_recommender":
        rules = data.get("dq_rules", [])
        st.markdown(f"**Table:** `{data.get('table_name')}` · {len(rules)} DQ rules")
        for rule in rules:
            pri = rule.get("priority", "P4")
            colour = {"P1": "🔴", "P2": "🟠", "P3": "🟡", "P4": "⚪"}.get(pri, "⚪")
            with st.expander(f"{colour} `{rule.get('rule_id')}` — {rule.get('rule_type')} on `{rule.get('column', 'table')}`"):
                st.markdown(f"**Description:** {rule.get('description')}")
                st.markdown(f"**Threshold:** `{rule.get('threshold')}`")
                st.markdown(f"**Pass condition:** {rule.get('pass_condition')}")
                if sql := rule.get("sql_template"):
                    st.code(sql, language="sql")

    elif agent_id == "lineage_creator":
        graph = data.get("lineage_graph", {})
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        st.markdown(f"**Nodes:** {len(nodes)} · **Edges:** {len(edges)}")
        st.markdown(f"*{data.get('summary', '')}*")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**Nodes**")
            st.dataframe(nodes, use_container_width=True)
        with col2:
            st.markdown("**Edges (transformations)**")
            st.dataframe(edges, use_container_width=True)

    elif agent_id == "sttm_gen":
        sttm = data.get("sttm", {})
        st.markdown(f"**{sttm.get('document_title', 'STTM')}** · v{sttm.get('version', '1.0')}")
        st.markdown(f"**Source:** `{sttm.get('source_table')}` → **Target:** `{sttm.get('target_table')}`")
        st.markdown(f"**Load type:** `{sttm.get('load_type')}`")
        mappings = sttm.get("column_mappings", [])
        if mappings:
            st.markdown(f"**{len(mappings)} column mappings**")
            st.dataframe(mappings, use_container_width=True)
        if questions := sttm.get("open_questions"):
            st.warning("Open questions:\n" + "\n".join(f"- {q}" for q in questions))

    elif agent_id in ("data_profiler",):
        profiles = data.get("profiles", [])
        for p in profiles:
            st.markdown(f"**{p.get('table_name')}** — {p.get('row_count')} rows, {p.get('column_count')} columns")
            st.dataframe(p.get("columns", []), use_container_width=True)

    elif agent_id == "sample_gen":
        samples = data.get("samples", [])
        for s in samples:
            st.markdown(f"**{s.get('table_name')}** — {s.get('row_count')} sample rows")
            st.dataframe(s.get("rows", []), use_container_width=True)

    elif agent_id == "ingestion_cfg_gen":
        configs = data.get("ingestion_configs", [])
        for cfg in configs:
            with st.expander(f"Config: `{cfg.get('source_table')}` → `{cfg.get('target_table')}`"):
                st.json(cfg)

    else:
        # Generic fallback
        st.json(data)
