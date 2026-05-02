## Task
{{task}}

## Table Metadata and DQ Rules
```json
{{metadata}}
```

## User Context
{{context}}

## Instructions
1. For each DQ rule in the metadata, generate one executable SQL query.
2. Each query must return a single integer: 0 = pass, >0 = failure count or flag.
3. Use the SQL templates from your knowledge base where applicable.
4. Make queries reusable — use the actual table name from metadata, not placeholders.
5. Include a short monitoring_label for each query (used in dashboards).
6. Return ONLY the JSON structure.
