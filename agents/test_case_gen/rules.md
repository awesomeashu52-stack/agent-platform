# Test Case Generator — Rules

## Coverage Requirements
- Always include at least one P1 test for every primary key column (NOT NULL + UNIQUE).
- Always include a ROW_COUNT > 0 test.
- Always include a FRESHNESS test (load_date within last 25 hours).
- Always include an idempotency test for the main load pipeline.
- Always cover NULL values for every nullable column.
- Always cover duplicate business key handling.

## SQL Rules
- Use Databricks SQL syntax (Delta Lake compatible).
- Validation queries must return a single integer COUNT — 0 means test passes.
- Use fully qualified table names: {catalog}.{schema}.{table}.
- Use backtick quoting for reserved words.

## Test ID Conventions
- Format: TC_<zero-padded 3-digit number> (TC_001, TC_002, ...)
- IDs must be unique within a suite.
- Order by priority: P1 tests first, then P2, then P3.

## Anti-Patterns to Avoid
- Do not generate redundant tests that check the same condition twice.
- Do not generate tests with no clear pass/fail criteria.
- Do not generate SQL that requires manual inspection to evaluate.
