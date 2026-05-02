## Task
{{task}}

## Source Table Metadata
The following metadata describes the source table(s). This is all you have — do not invent columns or business logic not implied by the metadata.

```json
{{metadata}}
```

## User Context & Constraints
{{context}}

## Instructions
1. Identify business keys, descriptive attributes, and relationships from the metadata.
2. Determine the appropriate DV2 entities (Hub / Link / Satellite) or dimensional entities (Dim / Fact).
3. Apply all naming conventions and mandatory columns from your governance rules.
4. For each entity, list all columns with correct data types and descriptions.
5. Describe relationships between entities.
6. Note any assumptions made due to ambiguous metadata.
7. Return ONLY the JSON structure specified in your output format — no additional prose.
