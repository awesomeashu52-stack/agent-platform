# Lineage Creator — Rules

## Node Rules
- Every source system is a node of type "source".
- Every intermediate table (bronze, silver, gold) is a separate node.
- Node IDs must be unique, lowercase, underscore-separated (e.g., "hub_customer").
- Node labels must match actual table or system names.

## Edge Rules
- Edges must be directed (from source to target — left to right in the graph).
- Every edge must have a transformation description.
- Do not create edges between nodes that have no documented transformation relationship.
- If a transformation is unknown, write "passthrough" not "unknown".

## Layer Classification
- Raw/source extracts → bronze layer.
- DV2 Hubs, Links, Satellites → silver layer.
- Dimensional models, aggregations → gold layer.
- Reports, dashboards, ML features → serving layer.

## Output Rules
- Include ALL intermediate nodes — do not skip layers.
- The graph must be acyclic (no circular dependencies).
