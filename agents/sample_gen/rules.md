# Sample Data Generator — Rules

## Generation Rules
- Generate realistic values consistent with the column's data_type and sample_values.
- Never generate real PII — all names, emails, and IDs must be clearly synthetic.
- Respect nullable: false columns — never generate NULL for these.
- For string columns with sample_values, cycle through the provided samples rather than inventing new ones.
- Default row count: 5. Maximum: 50. User can override via context.

## Output Rules
- Output must include: table_name, row_count, and rows (list of dicts).
- Column names in each row must exactly match the source schema column names.
- This agent uses Python only — no LLM calls.
