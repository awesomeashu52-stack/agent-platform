# DQ Query Generator — Rules

## SQL Generation Rules
- Generate one query per DQ rule — never combine multiple rules into one query.
- Every query must be executable as-is (no placeholders left unfilled).
- Use the table name and catalog from the input metadata.
- Queries must return exactly one integer row: 0 = pass, positive = failure count.
- For COMPLETENESS rules: return the count of NULLs, not the percentage.
- For RANGE_CHECK rules: return the count of out-of-range rows.
- For FRESHNESS rules: return 1 if stale, 0 if fresh.

## Performance Rules
- Use COUNT(*) not COUNT(1).
- Add WHERE clauses to filter early where possible.
- Do not use DISTINCT unless strictly required by the rule type.
- Never use SELECT * in generated queries.

## Naming Rules
- monitoring_label must be ≤ 40 characters.
- Format: `<RULE_TYPE>_<COLUMN_NAME>` e.g. `NOT_NULL_customer_id`.
