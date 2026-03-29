---
description: Run a DuckDB SQL query via pixi (accepts SQL or natural language)
argument-hint: <SQL or natural language description>
allowed-tools: Bash(pixi:*)
---
Execute the following query or translate the description into DuckDB SQL and execute it:

$ARGUMENTS

Use `pixi run duckdb` to run queries. For natural language input, convert to DuckDB Friendly SQL first.

## Query execution

- Ad-hoc: `pixi run duckdb -csv -c "<SQL>"`
- With spatial: `pixi run duckdb -csv -c "INSTALL spatial; LOAD spatial; <SQL>"`
- From file: `pixi run duckdb -csv -c "SELECT * FROM read_parquet('file.parquet') LIMIT 10"`
- JSON output: `pixi run duckdb -json -c "<SQL>"`

## Tips

- Use `read_parquet()`, `read_csv_auto()`, or `ST_Read()` for file-based queries
- Use `GROUP BY ALL`, `ORDER BY ALL`, `SELECT * EXCLUDE(col)` (Friendly SQL)
- For spatial queries, always `LOAD spatial` first

Show the results in a readable table format.

If the query fails, use the **duckdb** skill ([docs-search.md](../skills/duckdb/references/docs-search.md) reference) to look up the correct syntax.
