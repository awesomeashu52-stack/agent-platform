## Task
{{task}}

## Table Metadata (Source and Target)
```json
{{metadata}}
```

## User Context
{{context}}

## Instructions
1. Map every target column to a source column or mark as DERIVED.
2. Write the exact SQL transformation expression for each mapped column.
3. Include load_date and record_source as standard derived columns.
4. For DV2 targets, include hash key and hash_diff derivation logic.
5. List any open questions where the mapping is ambiguous.
6. Return ONLY the JSON structure.
