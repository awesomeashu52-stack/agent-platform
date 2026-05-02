# Data Vault 2.0 Standards

## Hub Rules
- A Hub represents a unique business key for a core business concept.
- Hub naming: HUB_<ENTITY> (e.g., HUB_CUSTOMER, HUB_ORDER)
- Required columns: <entity>_hk (BINARY/STRING hash key), load_date (TIMESTAMP), record_source (STRING), <business_key_columns>
- Business keys must be natural keys — never surrogate or sequence keys.
- One Hub per business entity. Do not create Hubs for descriptive attributes.
- Hash key = MD5 or SHA-256 of the business key (upper-cased, trimmed, NULL replaced with "N/A").
- Never store descriptive attributes in Hubs.

## Link Rules
- A Link represents a relationship (many-to-many or transaction) between two or more Hubs.
- Link naming: LNK_<ENTITY1>_<ENTITY2> (e.g., LNK_CUSTOMER_ORDER)
- Required columns: <link>_hk (hash key of all FK hash keys), load_date, record_source, <hub1>_hk, <hub2>_hk
- Do not store descriptive data in Links; use Satellite on Link (SAL) instead.
- Avoid Hub Explosion: only create separate Hubs for truly independent business concepts.

## Satellite Rules
- A Satellite stores descriptive attributes and their history.
- Satellite naming: SAT_<ENTITY>_<CONTEXT> (e.g., SAT_CUSTOMER_DEMOGRAPHICS)
- Required columns: <parent>_hk, load_date, load_end_date (TIMESTAMP, nullable), record_source, hash_diff (BINARY), <attribute_columns>
- hash_diff = hash of all non-driving-key attribute columns (used for change detection).
- Split Satellites by rate of change: fast-changing (addresses) vs slow-changing (demographics) in separate Sats.
- One record per hash key per load_date. load_end_date is NULL for the current record.

## General DV2 Rules
- All hash keys are SHA-256 unless the platform standard specifies MD5.
- RECORD_SOURCE must always be populated with the full source system path.
- Batch loads: use Databricks MERGE INTO for idempotent loads.
- Streaming loads: use Delta APPLY CHANGES INTO (DLT).
- Ghost records: insert a zero-hash ghost record during initial load for NULL FK handling.
