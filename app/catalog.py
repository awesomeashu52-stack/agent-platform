"""
app/catalog.py - Agent Catalog UI v3

Three input modes per agent:
  A. Unity Catalog / Hive metastore  - type catalog.schema, pick tables
  B. Cloud file path                  - paste S3/ADLS/GCS path
  C. Manual JSON                      - paste or edit metadata directly

Multi-table workspace: users build up a list of tables across sessions
before running an agent. Relationship detection runs automatically.
"""

from __future__ import annotations
import json, os, sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.orchestrator        import Orchestrator
from core.metadata_extractor  import MetadataExtractor, _spark_available

# -- page config ---------------------------------------------------------------
st.set_page_config(
    page_title="DE Agent Platform",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -- session state -------------------------------------------------------------

# =============================================================================
# HELPER FUNCTIONS - defined before any UI code so they are always available
# =============================================================================

def _to_dataframe(agent_id: str, data):
    """Convert structured agent JSON output to a flat pandas DataFrame."""
    try:
        import pandas as pd
    except ImportError:
        return None
    try:
        if isinstance(data, list):
            data = data[0] if data else {}

        if agent_id == "data_model_gen":
            rows = []
            for e in data.get("entities", []):
                for col in e.get("columns", []):
                    rows.append({
                        "entity_type": e.get("entity_type"),
                        "table_name":  e.get("table_name"),
                        "load_strategy": e.get("load_strategy"),
                        "column_name": col.get("name"),
                        "data_type":   col.get("data_type"),
                        "nullable":    col.get("nullable"),
                        "description": col.get("description"),
                    })
            return pd.DataFrame(rows) if rows else None

        if agent_id == "test_case_gen":
            return pd.DataFrame(data.get("test_cases", [])) or None

        if agent_id == "test_query_gen":
            return pd.DataFrame(data.get("queries", [])) or None

        if agent_id == "dq_recommender":
            rows = []
            for tbl in (data.get("tables") if "tables" in data else [data]):
                for r in tbl.get("dq_rules", []):
                    rows.append({
                        "table_name": tbl.get("table_name"),
                        "fully_qualified_name": tbl.get("fully_qualified_name"),
                        **r
                    })
            return pd.DataFrame(rows) if rows else None

        if agent_id == "dq_query_gen":
            return pd.DataFrame(data.get("queries", [])) or None

        if agent_id == "lineage_creator":
            graph = data.get("lineage_graph", {})
            edges = pd.DataFrame(graph.get("edges", []))
            return edges if not edges.empty else pd.DataFrame(graph.get("nodes", [])) or None

        if agent_id == "sttm_gen":
            sttm = data.get("sttm", data)
            rows = sttm.get("column_mappings", [])
            if rows:
                df = pd.DataFrame(rows)
                df.insert(0, "source_table", sttm.get("source_table", ""))
                df.insert(1, "target_table",  sttm.get("target_table", ""))
                return df
            return None

        if agent_id == "data_profiler":
            rows = []
            for p in data.get("profiles", []):
                for col in p.get("columns", []):
                    rows.append({"table_name": p.get("table_name"), **col})
            return pd.DataFrame(rows) if rows else None

        if agent_id == "sample_gen":
            frames = []
            for s in data.get("samples", []):
                df = pd.DataFrame(s.get("rows", []))
                if not df.empty:
                    df.insert(0, "table_name", s.get("table_name", ""))
                    frames.append(df)
            return pd.concat(frames, ignore_index=True) if frames else None

        if agent_id == "ingestion_cfg_gen":
            rows = []
            for cfg in data.get("ingestion_configs", []):
                rows.append({
                    "source_table": cfg.get("source_table"),
                    "target_table": cfg.get("target_table"),
                    "format":       cfg.get("format"),
                    "load_type":    cfg.get("load_type"),
                    "checkpoint":   cfg.get("autoloader", {}).get("checkpointLocation"),
                    "schema_loc":   cfg.get("autoloader", {}).get("schemaLocation"),
                    "columns":      ", ".join(cfg.get("selected_columns", [])),
                })
            return pd.DataFrame(rows) if rows else None

        # Generic fallback
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return pd.DataFrame(data)
        return None

    except Exception:
        return None




def _render_output(agent_id: str, data):
    if isinstance(data, list): data = data[0] if data else {}

    if agent_id == "data_model_gen":
        entities = data.get("entities",[])
        st.markdown(f"**{data.get('model_type','?')}** · Layer: `{data.get('target_layer','?')}` · {len(entities)} entities")
        for e in entities:
            with st.expander(f"{e.get('entity_type','').upper()} - `{e.get('table_name')}`"):
                st.caption(e.get("description",""))
                if cols := e.get("columns",[]): st.dataframe(cols, use_container_width=True)
                if fks := e.get("foreign_keys",[]): st.markdown("**FKs:** " + " · ".join(f"`{f['column']}` → `{f['references']}`" for f in fks))
                st.markdown(f"**Load:** `{e.get('load_strategy','?')}`")
        if rels := data.get("relationships",[]): st.dataframe(rels, use_container_width=True)
        if notes := data.get("notes"): st.info(f"📝 {notes}")

    elif agent_id in ("test_case_gen",):
        cases = data.get("test_cases",[])
        st.markdown(f"**{data.get('test_suite','')}** - {len(cases)} test cases")
        for pri,label,colour in [("P1","Critical","🔴"),("P2","High","🟠"),("P3","Medium","🟡")]:
            group = [c for c in cases if c.get("priority")==pri]
            if group:
                st.markdown(f"**{colour} {pri} - {label} ({len(group)})**")
                for tc in group:
                    with st.expander(f"`{tc.get('test_id')}` {tc.get('description')}"):
                        st.markdown(f"**Category:** `{tc.get('category')}`  **Input:** {tc.get('input_condition')}")
                        st.markdown(f"**Expected:** {tc.get('expected_result')}")
                        if sql := tc.get("validation_sql"): st.code(sql, language="sql")

    elif agent_id in ("test_query_gen","dq_query_gen"):
        qs = data.get("queries",[])
        st.markdown(f"{len(qs)} queries generated")
        for q in qs:
            pri = q.get("priority","P3")
            col = {"P1":"🔴","P2":"🟠","P3":"🟡","P4":"⚪"}.get(pri,"⚪")
            lid = q.get("query_id") or q.get("rule_id","")
            desc = q.get("test_description") or q.get("monitoring_label","")
            with st.expander(f"{col} `{lid}` · {desc}"):
                st.markdown(f"**Pass when:** {q.get('pass_condition','')}")
                st.code(q.get("sql",""), language="sql")

    elif agent_id == "dq_recommender":
        # Handle single-table {"dq_rules":[...]} and multi-table {"tables":[...]}
        table_list = data.get("tables") if "tables" in data else [data]
        for tbl in table_list:
            rules = tbl.get("dq_rules", [])
            fqn   = tbl.get("fully_qualified_name") or tbl.get("table_name", "")
            st.markdown(f"**{fqn}** - {len(rules)} DQ rules")
            for rule in sorted(rules, key=lambda r: {"P1":0,"P2":1,"P3":2,"P4":3}.get(r.get("priority","P4"),4)):
                pri = rule.get("priority","P4")
                col = {"P1":"🔴","P2":"🟠","P3":"🟡","P4":"⚪"}.get(pri,"⚪")
                target = f"`{rule.get('column')}`" if rule.get("column") else "table-level"
                with st.expander(f"{col} `{rule.get('rule_id')}` · {rule.get('rule_type')} on {target}"):
                    st.markdown(f"**{rule.get('description')}**  Threshold: `{rule.get('threshold')}`")
                    st.markdown(f"Pass when: {rule.get('pass_condition', '')}")
                    sql_text = rule.get("sql") or rule.get("sql_template") or ""
                    if sql_text:
                        st.code(sql_text, language="sql")

    elif agent_id == "lineage_creator":
        graph = data.get("lineage_graph",{})
        nodes, edges = graph.get("nodes",[]), graph.get("edges",[])
        st.markdown(f"*{data.get('summary','')}*")
        c1,c2 = st.columns(2)
        with c1:
            st.markdown(f"**{len(nodes)} Nodes**"); st.dataframe(nodes, use_container_width=True)
        with c2:
            st.markdown(f"**{len(edges)} Edges**"); st.dataframe(edges, use_container_width=True)

    elif agent_id == "sttm_gen":
        sttm = data.get("sttm", data)
        st.markdown(f"**{sttm.get('document_title','STTM')}** · `{sttm.get('source_table')}` → `{sttm.get('target_table')}`")
        if mappings := sttm.get("column_mappings",[]): st.dataframe(mappings, use_container_width=True)
        if qs := sttm.get("open_questions",[]): st.warning("\n".join(f"- {q}" for q in qs))

    elif agent_id == "data_profiler":
        for p in data.get("profiles", []):
            fqn  = p.get("fully_qualified_name") or p.get("table_name", "?")
            note = p.get("stats_note", "")
            st.markdown(f"**{fqn}** — {p.get('row_count',0):,} rows · {p.get('column_count',0)} cols")
            if note:
                if "empty" in note.lower() or "no row" in note.lower():
                    st.warning(f"ℹ️ {note}")
                else:
                    st.caption(note)
            cols_data = p.get("columns", [])
            if cols_data:
                import pandas as pd
                df = pd.DataFrame(cols_data)
                show_cols = [c for c in df.columns if c not in ("stats_source", "likely_pk", "likely_fk")]
                st.dataframe(df[show_cols], use_container_width=True)
                pks = [c["name"] for c in cols_data if c.get("likely_pk")]
                fks = [c["name"] for c in cols_data if c.get("likely_fk")]
                if pks: st.caption(f"🔑 Likely PK(s): {', '.join(pks)}")
                if fks: st.caption(f"🔗 Likely FK(s): {', '.join(fks)}")
            st.markdown("---")

    elif agent_id == "sample_gen":
        for s in data.get("samples", []):
            st.markdown(f"**{s.get('table_name')}** — {s.get('row_count')} synthetic rows")
            if note := s.get("generation_notes"):
                st.caption(note)
            rows = s.get("rows", [])
            if rows:
                import pandas as pd
                st.dataframe(pd.DataFrame(rows), use_container_width=True)
            else:
                st.info("No rows generated.")

    elif agent_id == "ingestion_cfg_gen":
        for cfg in data.get("ingestion_configs", []):
            with st.expander(
                f"🔧 `{cfg.get('source_table')}` → `{cfg.get('target_table')}` "
                f"[{cfg.get('load_type', '?')} · {cfg.get('source_format', '?')}]",
                expanded=True,
            ):
                col1, col2, col3 = st.columns(3)
                col1.metric("Load type",    cfg.get("load_type", "?"))
                col2.metric("Format",       cfg.get("source_format", "?"))
                col3.metric("Columns",      cfg.get("column_count", 0))

                st.markdown("**Source path:**")
                st.code(cfg.get("source_path", ""), language="text")

                st.markdown("**Autoloader options:**")
                st.json(cfg.get("autoloader_options", {}))

                if ddl := cfg.get("target_ddl"):
                    st.markdown("**Target table DDL:**")
                    st.code(ddl, language="sql")

                if code := cfg.get("python_code"):
                    st.markdown("**Python notebook / DLT snippet:**")
                    st.code(code, language="python")

                with st.expander("Full config JSON"):
                    st.json(cfg)
    else:
        st.json(data)


def _init_state():
    defaults = {
        "orchestrator":   Orchestrator(base_dir=ROOT),
        "extractor":      MetadataExtractor(),
        "history":        [],
        "loaded_tables":  [],   # list of metadata dicts built up by the user
        "last_agent_id":  None, # tracks agent switches - clears workspace on change
        "last_result":    None, # most recent AgentResult - shown after run
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()
orch:      Orchestrator       = st.session_state.orchestrator
extractor: MetadataExtractor  = st.session_state.extractor


# =============================================================================
# AGENT FORM DEFINITIONS
# what_is_input   - plain-English explanation shown in the info box
# needs_multi     - True if the agent should see ALL loaded tables
# extra_fields    - list of additional controls beyond the table workspace
# min_tables      - minimum tables required before Run is enabled
# =============================================================================
AGENT_FORMS = {
    "data_model_gen": {
        "what_is_input": (
            "Load **all source tables** that belong to the same subject area "
            "(e.g. customer, orders, order_items, products). "
            "The agent reads the relationships between them to correctly decide "
            "which tables share a Hub, which form a Link, and which become Satellites - "
            "preventing hub explosion and correctly modelling many-to-many relationships."
        ),
        "needs_multi": True,
        "min_tables": 1,
        "extra_fields": [
            {"type": "selectbox", "key": "model_type", "label": "Target model type",
             "options": ["Data Vault 2.0 (silver layer)", "Dimensional model (gold layer)"]},
            {"type": "selectbox", "key": "hash_algo",  "label": "Hash key algorithm",
             "options": ["SHA-256", "MD5"]},
            {"type": "text_input", "key": "target_schema", "label": "Target schema (optional)",
             "placeholder": "e.g. prod_silver or agent_platform.silver"},
        ],
    },
    "test_case_gen": {
        "what_is_input": (
            "Load the **target table(s)** you want to test. "
            "If you load multiple tables, the agent also generates "
            "cross-table referential integrity tests "
            "(e.g. every order.customer_id must exist in customer.customer_id)."
        ),
        "needs_multi": True,
        "min_tables": 1,
        "extra_fields": [
            {"type": "text_area", "key": "business_rules",
             "label": "Business rules to cover (one per line)",
             "placeholder": "country_code must be a valid ISO 3166-1 alpha-2 code\nemail must be unique per customer",
             "height": 100},
        ],
    },
    "test_query_gen": {
        "what_is_input": (
            "Load the **target table(s)**. The agent writes executable Databricks SQL - "
            "each query returns 0 (pass) or a positive count (failures found). "
            "Multiple tables generate cross-table FK queries automatically."
        ),
        "needs_multi": True,
        "min_tables": 1,
        "extra_fields": [
            {"type": "text_input", "key": "catalog_schema", "label": "Fully qualified prefix for SQL",
             "placeholder": "e.g. agent_platform.silver"},
        ],
    },
    "dq_recommender": {
        "what_is_input": (
            "Load one or more tables. For each table, the agent recommends "
            "column-level and table-level DQ rules based on data types, null rates, "
            "and distinct counts. Loading multiple tables also generates "
            "referential integrity rules across FK relationships."
        ),
        "needs_multi": True,
        "min_tables": 1,
        "extra_fields": [
            {"type": "multiselect", "key": "rule_priorities", "label": "Include priorities",
             "options": ["P1 - Critical","P2 - High","P3 - Medium","P4 - Low"],
             "default": ["P1 - Critical","P2 - High","P3 - Medium"]},
        ],
    },
    "dq_query_gen": {
        "what_is_input": (
            "Load your table(s) **and paste the DQ rules** from a previous "
            "DQ Recommender run into the 'Existing DQ rules JSON' box below. "
            "The agent converts each rule into an executable SQL query."
        ),
        "needs_multi": True,
        "min_tables": 1,
        "extra_fields": [
            {"type": "text_input", "key": "catalog_schema", "label": "Fully qualified prefix for SQL",
             "placeholder": "e.g. agent_platform.silver"},
            {"type": "text_area", "key": "existing_rules_json",
             "label": "Existing DQ rules JSON (from dq_recommender output)",
             "placeholder": '[{"rule_id":"DQ_001","rule_type":"NOT_NULL","column":"customer_hk",...}]',
             "height": 120},
        ],
    },
    "lineage_creator": {
        "what_is_input": (
            "Load **all tables in the pipeline** - source, intermediate, and target. "
            "The agent maps the full upstream → downstream data flow. "
            "The more tables you load, the more complete the lineage graph."
        ),
        "needs_multi": True,
        "min_tables": 2,
        "extra_fields": [
            {"type": "text_area", "key": "pipeline_description",
             "label": "Pipeline description",
             "placeholder": "Daily batch load from Salesforce CRM via Autoloader into DV2 silver layer, then aggregated into gold dimensional model.",
             "height": 80},
        ],
    },
    "sttm_gen": {
        "what_is_input": (
            "Load exactly **two tables**: first the source, then the target. "
            "Mark each with role='source' or role='target' using the JSON editor, "
            "or use the catalog/file loaders and set the role in the table options below. "
            "The agent maps every target column to a source column with the exact SQL transform."
        ),
        "needs_multi": True,
        "min_tables": 2,
        "extra_fields": [
            {"type": "selectbox", "key": "load_type", "label": "Load type",
             "options": ["incremental", "full", "streaming"]},
            {"type": "text_input", "key": "record_source_value",
             "label": "RECORD_SOURCE value",
             "placeholder": "e.g. salesforce.crm.contact"},
        ],
    },
    "data_profiler": {
        "what_is_input": (
            "Load one or more tables. With a SQL Warehouse ID: computes real null rates, "
            "distinct counts, min/max/avg, and sample values. "
            "Without warehouse: schema-only metadata. No LLM."
        ),
        "min_tables": 1,
        "extra_fields": [
            {"type": "text_input", "key": "warehouse_id_ctx",
             "label": "SQL Warehouse ID (enables live column stats)",
             "placeholder": "e.g. 3f2a1b4c5d6e7f8a",
             "help": "Find in SQL Warehouses > warehouse > Connection details."},
        ],
    },
    "sample_gen": {
        "what_is_input": (
            "Load tables. Tier 1: real rows via SQL Warehouse. "
            "Tier 2: smart synthetic from column name patterns "
            "(email, country_code, status, amount, rating etc.). "
            "Tier 3: type fallback. Add business rules to constrain output. No LLM."
        ),
        "min_tables": 1,
        "extra_fields": [
            {"type": "number_input", "key": "num_rows", "label": "Rows per table",
             "min_value": 1, "max_value": 50, "default": 5},
            {"type": "text_area", "key": "business_rules",
             "label": "Business rules (optional)",
             "placeholder": "business rules: status must be one of: confirmed, pending, cancelled",
             "height": 100,
             "help": "Plain-English constraints applied to generated rows."},
            {"type": "text_input", "key": "warehouse_id_ctx",
             "label": "SQL Warehouse ID (for real rows)",
             "placeholder": "e.g. 3f2a1b4c5d6e7f8a",
             "help": "If set and table has data, real rows are fetched."},
        ],
    },
    "ingestion_cfg_gen": {
        "what_is_input": (
            "Load source table(s). Generates: Autoloader config, "
            "notebook code (incremental MERGE/full/streaming), "
            "DLT pipeline skeleton, and target Delta DDL. No LLM."
        ),
        "min_tables": 1,
        "extra_fields": [
            {"type": "selectbox", "key": "load_type", "label": "Load type",
             "options": ["incremental", "full", "streaming"]},
            {"type": "selectbox", "key": "source_format", "label": "Source format",
             "options": ["parquet", "delta", "csv", "json", "avro"]},
            {"type": "text_input", "key": "source_path", "label": "Cloud source path",
             "placeholder": "e.g. abfss://raw@storage.dfs.core.windows.net/crm/"},
            {"type": "text_input", "key": "target_catalog", "label": "Target catalog",
             "placeholder": "e.g. bronze (default: bronze)"},
        ],
    },

}

# -- sample defaults shown in JSON editor --------------------------------------
SAMPLE_TABLE = {
    "table_name": "customer", "database": "crm_prod", "row_count": 1200000,
    "columns": [
        {"name":"customer_id","data_type":"string","nullable":False,
         "null_pct":0.0,"distinct_count":1200000,"sample_values":["CUST-0001","CUST-0002"]},
        {"name":"email","data_type":"string","nullable":True,
         "null_pct":0.05,"distinct_count":1150000,"sample_values":["a@example.com"]},
        {"name":"country_code","data_type":"string","nullable":False,
         "null_pct":0.0,"distinct_count":45,"sample_values":["GB","US","DE","IN"]},
        {"name":"created_at","data_type":"timestamp","nullable":False,
         "null_pct":0.0,"distinct_count":1200000},
    ]
}


# =============================================================================
# SIDEBAR - agent selector + session stats
# =============================================================================

with st.sidebar:
    st.markdown("## 🤖 Agent Catalog")
    st.markdown("---")

    all_agents   = orch.list_agents(enabled_only=True)
    categories   = sorted({a["category"] for a in all_agents})
    sel_category = st.selectbox("Category", ["All"] + categories)
    sel_type     = st.radio("Type", ["All","LLM agents","Free (no LLM)"], horizontal=True)
    type_map     = {"All":None,"LLM agents":"agentic","Free (no LLM)":"non_agentic"}

    filtered     = orch.list_agents(
        agent_type=type_map[sel_type],
        category=None if sel_category=="All" else sel_category,
    )
    agent_labels = {f"{a['icon']}  {a['display_name']}": a["id"] for a in filtered}
    chosen_label = st.radio("Select agent", list(agent_labels.keys()))
    chosen_id    = agent_labels[chosen_label]
    chosen_meta  = next(a for a in filtered if a["id"]==chosen_id)

    st.markdown("---")
    st.markdown("### 📊 Session")
    cost_info = orch.session_cost()
    toks      = cost_info["cumulative_tokens"]
    st.metric("Tokens",   toks.get("total",0))
    st.metric("Est cost", f"${cost_info['cumulative_cost_usd']:.4f}")
    st.metric("Runs",     len(st.session_state.history))
    if st.button("🔄 Reset stats"):
        orch.llm.reset_cumulative_tokens()
        st.session_state.history = []
        st.rerun()


# =============================================================================
# MAIN PANEL - header
# =============================================================================

st.title("🏗️ Data Engineering Agent Platform")
st.caption(f"Model: `{orch.cfg.llm_config.get('model')}` · Env: `{orch.cfg.environment}`")

col1, col2 = st.columns([1,9])
with col1:
    st.markdown(f"<div style='font-size:2.5rem'>{chosen_meta['icon']}</div>", unsafe_allow_html=True)
with col2:
    st.subheader(chosen_meta["display_name"])
    st.caption(chosen_meta["description"])
    bc  = "#1a6fad" if chosen_meta["type"]=="agentic" else "#2a7a3b"
    bl  = "LLM - uses tokens" if chosen_meta["type"]=="agentic" else "No LLM - free to run"
    tgs = " ".join(f"`{t}`" for t in chosen_meta.get("tags",[]))
    st.markdown(
        f"<span style='background:{bc};color:white;padding:2px 10px;border-radius:12px;font-size:0.75rem'>{bl}</span>&nbsp;&nbsp;{tgs}",
        unsafe_allow_html=True,
    )

st.markdown("---")

# -- Auto-clear workspace and last result when agent changes ------------------
if st.session_state.last_agent_id != chosen_id:
    st.session_state.loaded_tables = []
    st.session_state.last_result   = None
    st.session_state.last_agent_id = chosen_id

form_def = AGENT_FORMS.get(chosen_id, {})
if msg := form_def.get("what_is_input"):
    st.info(f"**What to provide:** {msg}", icon="ℹ️")

# =============================================================================
# STEP 1 - TABLE WORKSPACE  (add tables from any source)
# =============================================================================

st.markdown("### Step 1 - Load tables")

tab_catalog, tab_file, tab_json = st.tabs([
    "🗄️  From catalog / metastore",
    "📁  From file path (S3 / ADLS / GCS)",
    "✏️  Paste JSON manually",
])

# -- Tab A: Catalog ------------------------------------------------------------
with tab_catalog:
    st.caption(
        "Browse your Unity Catalog or Hive metastore directly. "
        "Select a catalog → schema → tables. Column stats are fetched automatically "
        "if a SQL warehouse ID is configured (optional)."
    )

    # Step 1 - pick catalog
    with st.spinner("Loading catalogs…"):
        try:
            available_catalogs = extractor.list_catalogs()
        except Exception as e:
            available_catalogs = []
            st.warning(f"Could not list catalogs: {e}")

    if not available_catalogs:
        st.info(
            "No catalogs found via the SDK. "
            "Type the catalog and schema name manually below."
        )
        manual_cat_schema = st.text_input(
            "Catalog.schema",
            placeholder="samples.tpch  or  hive_metastore.default",
        )
        available_catalogs = []
        sel_catalog = manual_cat_schema.split(".")[0] if "." in manual_cat_schema else ""
        sel_schema  = manual_cat_schema.split(".")[1] if "." in manual_cat_schema else ""
        available_schemas = [sel_schema] if sel_schema else []
        available_tables  = []
    else:
        c1, c2 = st.columns(2)
        with c1:
            sel_catalog = st.selectbox("Catalog", available_catalogs)
        with c2:
            if sel_catalog:
                with st.spinner(f"Loading schemas in {sel_catalog}…"):
                    available_schemas = extractor.list_schemas(sel_catalog)
            else:
                available_schemas = []
            sel_schema = st.selectbox(
                "Schema",
                available_schemas if available_schemas else ["- select a catalog first -"],
                disabled=not available_schemas,
            )

        # Step 2 - pick tables
        if sel_catalog and sel_schema and sel_schema != "- select a catalog first -":
            with st.spinner(f"Loading tables in {sel_catalog}.{sel_schema}…"):
                available_tables = extractor.list_tables(sel_catalog, sel_schema)
        else:
            available_tables = []

    if available_tables:
        sel_tables = st.multiselect(
            f"Tables in {sel_catalog}.{sel_schema}  ({len(available_tables)} available)",
            options=available_tables,
            default=[],
            help="Select one or more tables. Leave blank to load ALL tables in the schema.",
        )
    else:
        sel_tables = []

    # Optional settings
    with st.expander("⚙️ Advanced options"):
        rec_src_prefix = st.text_input(
            "Record source prefix (optional)",
            placeholder="e.g. salesforce or sap.erp",
            help="Prepended to table name for the DV2 RECORD_SOURCE column.",
        )
        wh_id_input = st.text_input(
            "SQL Warehouse ID (optional - for column stats)",
            value=os.getenv("DATABRICKS_WAREHOUSE_ID", ""),
            help=(
                "If provided, the platform runs COUNT, COUNT(DISTINCT) and sample "
                "queries to enrich the schema with null_pct, distinct_count, and "
                "sample_values. Find the ID in SQL Warehouses → your warehouse → Connection details."
            ),
        )

    load_catalog_btn = st.button(
        "Load selected tables" if sel_tables else "Load ALL tables in schema",
        use_container_width=True,
        disabled=(not sel_catalog or not sel_schema or sel_schema == "- select a catalog first -"),
    )

    if load_catalog_btn:
        names = sel_tables if sel_tables else None
        # Re-init extractor with warehouse ID if provided
        if wh_id_input.strip():
            from core.metadata_extractor import MetadataExtractor as _ME
            _ex = _ME(warehouse_id=wh_id_input.strip())
        else:
            _ex = extractor

        label = ", ".join(names) if names else f"all tables in {sel_catalog}.{sel_schema}"
        with st.spinner(f"Extracting {label}…"):
            try:
                tables = _ex.from_catalog(
                    sel_catalog, sel_schema, names, rec_src_prefix.strip()
                )
                all_loaded = st.session_state.loaded_tables + tables
                all_loaded = _ex.detect_relationships(all_loaded)
                for t in tables:
                    existing = [x["table_name"] for x in st.session_state.loaded_tables]
                    if t["table_name"] not in existing:
                        st.session_state.loaded_tables.append(t)
                st.success(_ex.summary(tables))
                st.rerun()
            except Exception as e:
                st.error(str(e))

# -- Tab B: File path ----------------------------------------------------------
with tab_file:
    if not _spark_available():
        st.warning(
            "**File path extraction is not available inside a Databricks App.**\n\n"
            "Databricks Apps run as lightweight web containers without a Spark session. "
            "To use data from a cloud file path:\n\n"
            "1. Open a Databricks **notebook** attached to a cluster\n"
            "2. Run this code to extract the metadata:\n"
        )
        st.code(
            """import sys
sys.path.insert(0, "/Workspace/Users/<your-path>/agent_platform")
from core.metadata_extractor import MetadataExtractor
import json

ex = MetadataExtractor()
tables = ex.from_file(
    path="s3://your-bucket/your-path/",
    file_format="parquet",   # parquet | delta | csv | json | avro
    sample_rows=10000,
    table_name="my_table",   # optional override
)
tables = ex.detect_relationships(tables)

# Copy this output and paste into the 'Paste JSON manually' tab in the App:
print(json.dumps(tables, indent=2))""",
            language="python",
        )
        st.info(
            "Copy the JSON output from the notebook and paste it into the "
            "**✏️ Paste JSON manually** tab above."
        )
    else:
        st.caption("Extract schema and column stats from a cloud file path using Spark.")
        f1, f2, f3 = st.columns([3,1,1])
        with f1:
            file_path = st.text_input("Cloud file path",
                placeholder="s3://my-bucket/data/customer/  or  abfss://raw@acct.dfs.core.windows.net/crm/")
        with f2:
            file_fmt = st.selectbox("Format", ["parquet","delta","csv","json","avro"])
        with f3:
            tbl_name_override = st.text_input("Table name", placeholder="customer")

        fc1, fc2 = st.columns([2,1])
        with fc1:
            sample_size = st.number_input("Sample rows", min_value=100, max_value=100000,
                                          value=10000, step=1000)
        with fc2:
            st.markdown("<br>", unsafe_allow_html=True)
            load_file_btn = st.button("Load from file", use_container_width=True)

        if load_file_btn:
            if not file_path.strip():
                st.error("Please enter a file path.")
            else:
                with st.spinner(f"Reading {file_fmt} from {file_path}…"):
                    try:
                        tables = extractor.from_file(
                            path=file_path.strip(), file_format=file_fmt,
                            sample_rows=int(sample_size),
                            table_name=tbl_name_override.strip() or None,
                        )
                        for t in tables:
                            existing = [x["table_name"] for x in st.session_state.loaded_tables]
                            if t["table_name"] not in existing:
                                st.session_state.loaded_tables.append(t)
                        st.session_state.loaded_tables = extractor.detect_relationships(
                            st.session_state.loaded_tables
                        )
                        st.success(extractor.summary(tables))
                        st.rerun()
                    except (RuntimeError, ValueError) as e:
                        st.error(str(e))

# -- Tab C: Manual JSON --------------------------------------------------------
with tab_json:
    st.caption(
        "Paste metadata as JSON. Useful for tables from external systems or quick prototyping. "
        "You can paste a single table or an array of tables."
    )
    manual_json = st.text_area(
        "Table metadata JSON",
        value=json.dumps([SAMPLE_TABLE], indent=2),
        height=280,
        help=(
            "Required per table: table_name, columns (each needs name + data_type). "
            "Optional but recommended: row_count, null_pct, distinct_count, sample_values."
        ),
    )
    add_manual_btn = st.button("Add to table workspace", use_container_width=False)

    if add_manual_btn:
        try:
            tables = extractor.from_manual(manual_json)
            tables = extractor.detect_relationships(
                st.session_state.loaded_tables + tables
            )
            for t in tables:
                existing = [x["table_name"] for x in st.session_state.loaded_tables]
                if t["table_name"] not in existing:
                    st.session_state.loaded_tables.append(t)
            st.success(extractor.summary(tables))
        except ValueError as e:
            st.error(f"Validation error: {e}")

# -- Table workspace summary ---------------------------------------------------
st.markdown("#### Table workspace")

if not st.session_state.loaded_tables:
    st.warning("No tables loaded yet. Use one of the tabs above to add tables.")
else:
    for i, t in enumerate(st.session_state.loaded_tables):
        col_info, col_remove = st.columns([10, 1])
        with col_info:
            mode_icon = {"catalog":"🗄️","file":"📁","manual":"✏️"}.get(t.get("source_mode","manual"),"📋")
            rels = len(t.get("relationships",[]))
            st.markdown(
                f"{mode_icon} **{t['table_name']}** &nbsp;·&nbsp; "
                f"{len(t['columns'])} cols &nbsp;·&nbsp; "
                f"{t.get('row_count',0):,} rows &nbsp;·&nbsp; "
                f"`{t.get('database','')}`"
                + (f" &nbsp;·&nbsp; {rels} FK links detected" if rels else ""),
                unsafe_allow_html=True,
            )
        with col_remove:
            if st.button("✕", key=f"remove_{i}", help=f"Remove {t['table_name']}"):
                st.session_state.loaded_tables.pop(i)
                st.rerun()

    if st.button("🗑️ Clear all tables"):
        st.session_state.loaded_tables = []
        st.rerun()

    # Show detected relationships across all tables
    all_rels = [
        {"from_table": t["table_name"], **r}
        for t in st.session_state.loaded_tables
        for r in t.get("relationships",[])
    ]
    if all_rels:
        with st.expander(f"🔗 {len(all_rels)} cross-table relationships detected"):
            st.dataframe(all_rels, use_container_width=True)


# =============================================================================
# STEP 2 - AGENT OPTIONS + RUN
# =============================================================================

st.markdown("---")
st.markdown("### Step 2 - Configure and run")

min_tables   = form_def.get("min_tables", 1)
ready_to_run = len(st.session_state.loaded_tables) >= min_tables

if not ready_to_run:
    st.warning(
        f"This agent needs at least **{min_tables} table(s)** in the workspace. "
        f"You currently have {len(st.session_state.loaded_tables)}."
    )

extra_fields  = form_def.get("extra_fields", [])
extra_values: dict = {}

with st.form(key=f"run_form_{chosen_id}"):

    if extra_fields:
        cols = st.columns(min(len(extra_fields), 2))
        for i, field in enumerate(extra_fields):
            with cols[i % 2]:
                fk = field["key"]
                fl = field["label"]
                fh = field.get("help","")
                if field["type"] == "selectbox":
                    extra_values[fk] = st.selectbox(fl, field["options"], help=fh)
                elif field["type"] == "multiselect":
                    extra_values[fk] = st.multiselect(
                        fl, field["options"],
                        default=field.get("default", field["options"][:2]), help=fh,
                    )
                elif field["type"] == "number_input":
                    extra_values[fk] = st.number_input(
                        fl, min_value=field.get("min_value",0),
                        max_value=field.get("max_value",100),
                        value=field.get("default",5), help=fh,
                    )
                elif field["type"] == "text_input":
                    extra_values[fk] = st.text_input(
                        fl, placeholder=field.get("placeholder",""), help=fh,
                    )
                elif field["type"] == "text_area":
                    extra_values[fk] = st.text_area(
                        fl, placeholder=field.get("placeholder",""),
                        height=field.get("height",80), help=fh,
                    )

    user_context = st.text_area(
        "Additional context / instructions (optional)",
        placeholder="e.g. Use SHA-256 hashing. Target catalog is prod_silver. Source system is Salesforce.",
        height=70,
    )

    cache_col, run_col = st.columns([2,3])
    with cache_col:
        use_cache = st.checkbox("Use prompt cache", value=True,
                                help="Skip LLM call and return cached result if identical inputs were run before.")
    with run_col:
        submitted = st.form_submit_button(
            f"▶  Run {chosen_meta['display_name']}",
            type="primary",
            use_container_width=True,
            disabled=not ready_to_run,
        )


# =============================================================================
# EXECUTION
# =============================================================================

if submitted and ready_to_run:

    # Build metadata list - always pass all loaded tables
    metadata = st.session_state.loaded_tables

    # Merge extra field values into dq_query_gen if existing rules provided
    if chosen_id == "dq_query_gen" and extra_values.get("existing_rules_json","").strip():
        try:
            parsed_rules = json.loads(extra_values["existing_rules_json"])
            for t in metadata:
                t["existing_dq_rules"] = parsed_rules
        except json.JSONDecodeError:
            st.error("The 'Existing DQ rules JSON' field contains invalid JSON.")
            st.stop()

    # Build context string
    context_parts = []
    for field in extra_fields:
        fk  = field["key"]
        val = extra_values.get(fk)
        if val and fk != "existing_rules_json":
            context_parts.append(
                f"{field['label']}: {', '.join(val) if isinstance(val,list) else val}"
            )
    if user_context.strip():
        context_parts.append(user_context.strip())
    full_context = "\n".join(context_parts)

    # -- Run with st.status - shows animated progress, then full log ------
    n_tables   = len(metadata)
    total_cols = sum(len(t.get("columns", [])) for t in metadata)

    # st.status gives a collapsible panel that stays "Running…" while
    # orch.run() executes, then collapses to "Complete" with the log inside.
    with st.status(
        f"Running {chosen_meta['display_name']} on {n_tables} table(s) "
        f"({total_cols} columns) - please wait…",
        expanded=True,
    ) as status_box:
        st.write(
            f"⏳ Calling **{orch.cfg.llm_config.get('model')}**. "
            f"Large schemas can take 30-120 seconds."
        )

        result = orch.run(
            agent_id=chosen_id,
            metadata=metadata,
            user_context=full_context,
            use_cache=use_cache,
        )

        # Show the full step-by-step log inside the status box
        log_entries = getattr(result, "log_entries", [])
        if log_entries:
            icon_map = {"done":"🟢","error":"🔴","warn":"🟡","info":"⬜"}
            for e in log_entries:
                icon = icon_map.get(e.level, "⬜")
                st.write(f"`{e.timestamp}` {icon} {e.message}")

        if result.status == "success":
            status_box.update(label="✅ Complete", state="complete", expanded=False)
        else:
            status_box.update(label="❌ Failed", state="error", expanded=True)

    st.session_state.history.append(result)
    st.session_state.last_result = result

    st.markdown("---")
    if result.status == "success":
        cost = getattr(result,"cost_usd",None) or getattr(result,"cost_estimate_usd",0.0)
        st.success(
            f"✅ Done in {result.duration_seconds}s · "
            f"{result.token_usage.get('total',0):,} tokens · est. ${cost:.4f}"
        )
        output      = result.output
        parsed_json = None
        if isinstance(output, (dict, list)):
            parsed_json = output
        elif isinstance(output, str):
            # Strip markdown code fences the LLM sometimes wraps around JSON
            cleaned = output.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1]  # drop ```json line
                cleaned = cleaned.rsplit("```", 1)[0]  # drop trailing ```
                cleaned = cleaned.strip()
            try:
                parsed_json = json.loads(cleaned)
            except (json.JSONDecodeError, ValueError):
                pass

        t1, t2, t3, t4 = st.tabs(["📋 Formatted", "📊 Table / CSV", "🔤 Raw JSON", "⬇️ Download"])
        with t1:
            if parsed_json: _render_output(chosen_id, parsed_json)
            else: st.markdown(str(output))
        with t2:
            if parsed_json:
                import pandas as pd
                df = _to_dataframe(chosen_id, parsed_json)
                if df is not None and not df.empty:
                    st.dataframe(df, use_container_width=True)
                    csv_bytes = df.to_csv(index=False).encode("utf-8")
                    st.download_button(
                        "⬇️ Download as CSV",
                        data=csv_bytes,
                        file_name=f"{result.run_id}.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )
                else:
                    st.info("No flat tabular representation available for this output.")
            else:
                st.info("Output is not structured JSON - no table view available.")
        with t3:
            if parsed_json: st.json(parsed_json)
            else: st.code(str(output))
        with t4:
            col_j, col_c = st.columns(2)
            with col_j:
                dl = json.dumps(parsed_json,indent=2) if parsed_json else str(output)
                st.download_button("⬇️ Download JSON", data=dl,
                                   file_name=f"{result.run_id}.json", mime="application/json",
                                   use_container_width=True)
            with col_c:
                if parsed_json:
                    import pandas as pd
                    df2 = _to_dataframe(chosen_id, parsed_json)
                    if df2 is not None and not df2.empty:
                        st.download_button("⬇️ Download CSV",
                                           data=df2.to_csv(index=False).encode("utf-8"),
                                           file_name=f"{result.run_id}.csv", mime="text/csv",
                                           use_container_width=True)
            st.caption(f"Run ID: `{result.run_id}`")
            if getattr(result, "output_path", None):
                st.caption(f"Saved to: `{result.output_path}`")
    else:
        st.error(f"❌ Failed: {result.error}")
        with st.expander("Full error"): st.code(result.error)


# =============================================================================
# HISTORY
# =============================================================================

if st.session_state.history:
    st.markdown("---")
    with st.expander(f"📜 Run history ({len(st.session_state.history)} runs)"):
        for r in reversed(st.session_state.history[-10:]):
            icon = "✅" if r.status=="success" else "❌"
            cost = getattr(r,"cost_usd",None) or getattr(r,"cost_estimate_usd",0.0)
            st.markdown(
                f"{icon} **{r.agent_id}** · `{r.run_id}` · "
                f"{r.duration_seconds}s · {r.token_usage.get('total',0):,} tokens · ${cost:.4f}"
            )


# =============================================================================
# OUTPUT RENDERERS
# =============================================================================


# =============================================================================
# TABULAR FLATTENER - converts agent JSON output to a pandas DataFrame for CSV
