## Task
{{task}}

## Table Metadata
```json
{{metadata}}
```

## User Context
{{context}}

## Instructions
1. Analyse each column: data type, nullable flag, null_pct, distinct_count, and sample values.
2. Infer the column's business purpose from its name and sample values.
3. Apply rule selection logic: always recommend P1 rules, then P2, then P3/P4 where justified.
4. Write a parameterised SQL template for each rule.
5. Do not exceed 15 rules per table. Cut lowest-priority rules if needed.
6. Return ONLY the JSON structure.
