# Data Vault 2.0 Common Patterns

## Business Key Identification
- Primary keys with names ending in _id, _code, _num, _key → likely business keys
- Composite business keys should be concatenated with | separator in hash computation
- Natural keys preferred over surrogate keys as business keys

## Hub Anti-patterns to Avoid
- Do not create hubs for lookup/reference tables (e.g. status_code, country)
- Do not create hubs for date/time dimension tables
- Separate transaction hubs from entity hubs

## Satellite Splitting Guidelines
- Customer demographics vs customer contact details → separate satellites
- Header attributes vs line-item attributes → separate satellites
- Slowly changing vs rapidly changing attributes → separate satellites

## Silver Layer (Raw Vault)
- Direct mapping from source with no business transformations
- All source columns preserved
- Hash keys computed using SHA-256 or MD5 on business key

## Gold Layer (Business Vault / Dimensional)
- Apply business rules and transformations
- Computed satellites with derived attributes
- Point-in-time (PIT) tables for performance
- Bridge tables for many-to-many relationships
