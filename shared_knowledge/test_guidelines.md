# Testing Guidelines for Data Engineering

## Test Categories
- Functional Tests: Verify data is loaded and transformed as specified.
- Boundary Tests: Test edge cases — empty inputs, NULL values, max/min values.
- Referential Tests: Verify FK relationships are maintained after transformation.
- Idempotency Tests: Running the pipeline twice must produce the same result.
- Reconciliation Tests: Row counts and sum of measures must match source-to-target.

## Test Case Structure
Each test case must include:
- test_id: Unique identifier (e.g., TC_001)
- description: What is being tested
- input: Describe the input state or data condition
- expected_result: The expected outcome
- sql_query (optional): SQL to validate the expectation
- priority: P1 / P2 / P3

## Edge Cases to Always Cover
- Empty source table
- All NULL values in a column
- Duplicate business keys in source
- Special characters in string columns
- Maximum value for numeric columns
- Future-dated timestamps
- Records with RECORD_SOURCE missing or NULL
