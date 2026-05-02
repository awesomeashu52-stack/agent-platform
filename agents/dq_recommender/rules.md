# DQ Recommender — Governance Rules

## CRITICAL — SQL Must Use Real Table Names
- Every SQL statement MUST use the exact fully qualified table name from the metadata.
- The format is: `database.table_name` — e.g. `samples.wanderbricks.bookings`
- NEVER write `{table}`, `<table>`, `{column}`, `<column_name>`, `your_table`, or ANY placeholder.
- SQL must be immediately runnable with no substitution needed.
- Return 0 when the rule passes, a positive integer when it fails.

## Always Recommend (P1 — Critical)
- NOT_NULL on every non-nullable column and every primary/foreign key.
  Correct SQL: `SELECT COUNT(*) FROM samples.wanderbricks.bookings WHERE booking_id IS NULL`
- UNIQUE on every business key and primary key.
  Correct SQL: `SELECT COUNT(*) - COUNT(DISTINCT booking_id) FROM samples.wanderbricks.bookings`
- ROW_COUNT > 0 for every table.
  Correct SQL: `SELECT CASE WHEN COUNT(*) = 0 THEN 1 ELSE 0 END FROM samples.wanderbricks.bookings`

## Recommend Where Applicable (P2 — High)
- FRESHNESS on columns named load_date, created_at, updated_at, event_time.
  Correct SQL: `SELECT CASE WHEN MAX(created_at) < CURRENT_TIMESTAMP - INTERVAL 25 HOURS THEN 1 ELSE 0 END FROM samples.wanderbricks.bookings`
- COMPLETENESS where null_pct is between 0.01 and 0.20.
  Correct SQL: `SELECT SUM(CASE WHEN email IS NULL THEN 1 ELSE 0 END) FROM samples.wanderbricks.users`

## Recommend Based on Column Characteristics (P3/P4)
- REGEX on columns named email, phone, postal_code, country_code.
  Correct SQL: `SELECT COUNT(*) FROM samples.wanderbricks.users WHERE email NOT RLIKE '^[a-zA-Z0-9._%+\\-]+@[a-zA-Z0-9.\\-]+\\.[a-zA-Z]{2,}$'`
- RANGE_CHECK on numeric columns with bounded sample values.
  Correct SQL: `SELECT COUNT(*) FROM samples.wanderbricks.bookings WHERE total_price < 0 OR total_price > 100000`
- ENUM_CHECK on string columns with distinct_count < 20.
  Correct SQL: `SELECT COUNT(*) FROM samples.wanderbricks.bookings WHERE status NOT IN ('confirmed','pending','cancelled')`

## Anti-Patterns — Never Do These
- NEVER use {table}, <table>, {column}, <column_name>, or any other placeholder in SQL.
- Do NOT recommend both NOT_NULL and COMPLETENESS on the same column.
- Do NOT recommend UNIQUE on foreign key columns (they are many-to-one by design).
- Do NOT recommend RANGE_CHECK on string or timestamp columns.
- Maximum 12 rules per table. Drop P4 first, then P3 if still over the limit.
