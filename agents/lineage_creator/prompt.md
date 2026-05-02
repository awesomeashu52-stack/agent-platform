## Task
{{task}}

## Table Metadata
```json
{{metadata}}
```

## User Context
{{context}}

## Instructions
1. Identify source systems from record_source values and context.
2. Map the data flow from source → bronze → silver → gold → serving.
3. Create a node for every distinct table or system in the lineage.
4. Create a directed edge for every transformation step.
5. Describe transformations concisely (e.g., "hash key generation", "SCD type 2 merge", "aggregation").
6. Return ONLY the JSON structure.
