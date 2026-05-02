# Data Model Generator — Governance Rules

## Naming Conventions
- Hub tables: prefix HUB_ (e.g., HUB_CUSTOMER)
- Link tables: prefix LNK_ (e.g., LNK_CUSTOMER_ORDER)
- Satellite tables: prefix SAT_ (e.g., SAT_CUSTOMER_DEMOGRAPHICS)
- Dimension tables: prefix DIM_ (e.g., DIM_DATE)
- Fact tables: prefix FACT_ (e.g., FACT_SALES)
- All column names: snake_case, lowercase

## Mandatory Columns
- Every Hub must include: <entity>_hk, load_date, record_source, and the business key column(s).
- Every Satellite must include: <parent>_hk, load_date, load_end_date, record_source, hash_diff.
- Every Link must include: <link>_hk, load_date, record_source, plus hash keys of all parent Hubs.

## Design Rules
- Never store descriptive attributes in Hub or Link tables.
- Split Satellites by rate of change when multiple attribute groups have clearly different update frequencies.
- Avoid Hub Explosion — only create a separate Hub if the entity has its own independent business key.
- For the silver layer, generate Data Vault 2.0 structures (Hub, Link, Satellite).
- For the gold layer, generate dimensional structures (Dim, Fact) derived from DV2 Satellites.
- Never use sequences or surrogate keys as Hash Keys — always hash the business key.
- Always include ghost records (zero-hash) in Hub load scripts.
