# Data Quality Framework

## Column-Level Rules (apply to individual columns)
- NOT_NULL: Column must not contain NULL values (apply to all primary and foreign keys).
- UNIQUE: Column must contain no duplicate values (apply to business keys and hash keys).
- RANGE_CHECK: Numeric values must fall within an expected min/max range.
- REGEX_PATTERN: String values must match a defined regular expression (e.g., email, phone).
- REFERENTIAL_INTEGRITY: Foreign key values must exist in the referenced parent table.
- LENGTH_CHECK: String length must be within defined bounds (min_length, max_length).
- ENUM_CHECK: Value must belong to a defined set of allowed values.

## Table-Level Rules (apply across rows/tables)
- ROW_COUNT_THRESHOLD: Row count must exceed a minimum expected value.
- DUPLICATE_ROWS: No fully duplicate rows should exist.
- FRESHNESS_CHECK: Latest load_date must be within an expected recency window (e.g., < 25 hours ago).
- COMPLETENESS_RATIO: Percentage of non-NULL values in a column must exceed a threshold (e.g., > 95%).
- RECORD_COUNT_VARIANCE: Row count delta between loads must not exceed X% (anomaly detection).

## SQL Templates
- NOT_NULL: SELECT COUNT(*) FROM {table} WHERE {col} IS NULL
- UNIQUE: SELECT COUNT(*) - COUNT(DISTINCT {col}) FROM {table}
- FRESHNESS: SELECT MAX(load_date) < CURRENT_TIMESTAMP - INTERVAL {hours} HOURS FROM {table}
- COMPLETENESS: SELECT SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) * 100.0 / COUNT(*) FROM {table}

## DQ Rule Priority
- P1 (Critical): NOT_NULL on keys, UNIQUE on business keys, REFERENTIAL_INTEGRITY
- P2 (High): FRESHNESS_CHECK, ROW_COUNT_THRESHOLD
- P3 (Medium): COMPLETENESS_RATIO, RANGE_CHECK
- P4 (Low): REGEX_PATTERN, LENGTH_CHECK, ENUM_CHECK
