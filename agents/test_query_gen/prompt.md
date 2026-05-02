## Task
{{task}}

## Table Metadata
```json
{{metadata}}
```

## User Context
{{context}}

## Instructions
1. Analyse the schema — identify PKs, FKs, nullable columns, data types, and load_date columns.
2. Write one SQL query per validation check.
3. Every query MUST return a single integer: 0 = pass, >0 = failure count.
4. Use fully qualified Databricks table names: `{catalog}.{schema}.{table}`.
5. Prioritise: P1 = data integrity (NULLs on keys, duplicates), P2 = freshness/completeness, P3 = format/range.
6. Return ONLY the JSON structure. No prose.
