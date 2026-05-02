# STTM Generator — Rules

## Mapping Rules
- Every target column must have a corresponding source_column or be marked as "DERIVED".
- Derived columns must have a complete transformation_rule (e.g., "SHA256(UPPER(TRIM(customer_id)))").
- DIRECT COPY means no transformation — source column maps to target column as-is.
- Data type changes must be explicitly noted in transformation_rule (e.g., "CAST(amount AS DECIMAL(18,2))").

## Mandatory STTM Columns
- Always include: load_date (CURRENT_TIMESTAMP()), record_source (from pipeline config).
- For DV2 targets: always include hash key derivation rules.
- For DV2 Satellites: always include hash_diff derivation (hash of all non-key attributes).

## Documentation Standards
- transformation_rule must be a SQL expression, not prose.
- open_questions must list every assumption where source intent is unclear.
- Do not leave any target column unmapped without a note.
