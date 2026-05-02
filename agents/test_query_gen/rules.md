# Test Query Generator — Rules

## SQL Rules
- All SQL must be valid Databricks SQL (Delta Lake compatible).
- Every query must return exactly one row with one integer column.
- Use `COUNT(*)` for failure counts — never SELECT *.
- Use backtick quoting for column names that are reserved words.
- Always use fully qualified table names: catalog.schema.table.
- Queries must be idempotent — running twice produces the same result.

## Query Patterns (use these templates)
- NOT_NULL check: `SELECT COUNT(*) FROM t WHERE col IS NULL`
- UNIQUE check: `SELECT COUNT(*) - COUNT(DISTINCT col) FROM t`
- FRESHNESS check: `SELECT CASE WHEN MAX(load_date) < CURRENT_TIMESTAMP - INTERVAL 25 HOURS THEN 1 ELSE 0 END FROM t`
- ROW_COUNT check: `SELECT CASE WHEN COUNT(*) = 0 THEN 1 ELSE 0 END FROM t`
- REFERENTIAL check: `SELECT COUNT(*) FROM child c LEFT JOIN parent p ON c.fk = p.pk WHERE p.pk IS NULL`

## Priority Rules
- Always generate P1 queries for every PK and hash key column.
- Always generate a freshness query if a load_date column exists.
- Always generate a row count > 0 query.
- Maximum 15 queries per suite. Drop lowest priority if over limit.
