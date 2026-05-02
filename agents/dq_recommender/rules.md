# DQ Recommender — Rules

## Always Recommend (P1)
- NOT_NULL on all primary key and hash key columns.
- UNIQUE on all business key and hash key columns.
- REFERENTIAL_INTEGRITY on all foreign key / hash key reference columns.

## Recommend Where Applicable (P2)
- FRESHNESS_CHECK on load_date columns (threshold: 25 hours).
- ROW_COUNT_THRESHOLD for tables expected to have > 0 rows.
- COMPLETENESS_RATIO > 95% for columns where NULL percentage is expected to be low.

## Recommend Based on Data Type (P3/P4)
- REGEX for columns named email, phone, postal_code, country_code.
- RANGE_CHECK for numeric columns where sample values imply a bounded domain.
- ENUM_CHECK for columns with very low distinct count (< 20 distinct values).
- LENGTH_CHECK for string columns where sample values show consistent length.

## Anti-Patterns
- Do NOT recommend both NOT_NULL and COMPLETENESS_RATIO on the same column.
- Do NOT recommend UNIQUE on columns with many-to-one relationships.
- Do NOT recommend RANGE_CHECK on string or date columns.
- Limit rules to 15 maximum per table — prioritise P1 and P2.
