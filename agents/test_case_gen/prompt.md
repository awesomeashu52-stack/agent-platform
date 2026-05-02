## Task
{{task}}

## Table Metadata
```json
{{metadata}}
```

## User Context
{{context}}

## Instructions
1. Analyse the schema — identify primary keys, foreign keys, nullable columns, data types.
2. Generate test cases covering: functional correctness, boundary conditions, referential integrity, idempotency, and reconciliation.
3. Write a Databricks SQL validation query for every test case where SQL is meaningful.
4. Prioritise: P1 = data integrity, P2 = completeness/freshness, P3 = format/pattern checks.
5. Return ONLY the JSON structure. No additional explanation.
