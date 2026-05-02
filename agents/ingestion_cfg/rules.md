# Ingestion Config Generator — Rules

## Config Rules
- Always generate a cloudFiles (Autoloader) config block.
- Default load_type: incremental. Respect override from user context.
- target_table must use the pattern: bronze.<source_table_name>.
- checkpointLocation must be unique per table: /checkpoints/<table_name>/checkpoint.
- schemaLocation must be: /checkpoints/<table_name>/schema.
- selected_columns must list all columns from the source schema.

## Output Rules
- Output must include: source_table, target_table, format, load_type, autoloader block, selected_columns, generated_at.
- This agent uses Python only — no LLM calls.
- One config object per source table in the input metadata.
