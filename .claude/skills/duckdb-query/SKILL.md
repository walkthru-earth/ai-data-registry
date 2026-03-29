---
name: query
description: >
  Run SQL queries against DuckDB databases or ad-hoc files. Accepts raw SQL or
  natural language. Use whenever the user wants to query data, explore tables, run
  analytics, or ask questions about datasets — even if they don't say "DuckDB" explicitly.
argument-hint: <SQL or question> [--file path]
allowed-tools: Bash
---

Input: `$@`

## Step 1 — Resolve state

```bash
STATE_DIR=""
test -f .duckdb-skills/state.sql && STATE_DIR=".duckdb-skills"
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")"
PROJECT_ID="$(echo "$PROJECT_ROOT" | tr '/' '-')"
test -f "$HOME/.duckdb-skills/$PROJECT_ID/state.sql" && STATE_DIR="$HOME/.duckdb-skills/$PROJECT_ID"
```

**Mode**: Ad-hoc if `--file` flag present, SQL references file paths, or no state. Session if state exists and input references tables/is natural language.

## Step 2 — Generate SQL if needed

For natural language, first get schema context (session mode):
```bash
pixi run duckdb -init "$STATE_DIR/state.sql" -csv -c "SELECT table_name FROM duckdb_tables() ORDER BY table_name;"
pixi run duckdb -init "$STATE_DIR/state.sql" -csv -c "DESCRIBE <table_name>;"
```

## Step 3 — Estimate result size

Skip for DESCRIBE, SUMMARIZE, aggregations, or queries with LIMIT.
For unbounded queries on >1M rows: suggest adding LIMIT or aggregation before running.

## Step 4 — Execute

**Ad-hoc** (sandboxed):
```bash
pixi run duckdb :memory: -csv <<'SQL'
SET allowed_paths=['FILE_PATH'];
SET enable_external_access=false;
SET allow_persistent_secrets=false;
SET lock_configuration=true;
<QUERY>;
SQL
```

**Session**:
```bash
pixi run duckdb -init "$STATE_DIR/state.sql" -csv <<'SQL'
<QUERY>;
SQL
```

## Step 5 — Handle errors

- Syntax error -> suggest fix, re-run
- Missing extension -> delegate to **duckdb-install**, retry
- Table not found -> list tables with `FROM duckdb_tables()`
- File not found -> search with `find "$PWD" -name "<filename>"`
- Unclear error -> search with **duckdb-docs** skill

## DuckDB Friendly SQL Reference

### Compact clauses
- `FROM table WHERE x > 10` — implicit SELECT *
- `GROUP BY ALL` / `ORDER BY ALL` — auto-detect columns
- `SELECT * EXCLUDE (col1, col2)` / `REPLACE (expr AS col)`
- `UNION ALL BY NAME` — combine tables with different column orders
- `LIMIT 10%` — percentage limit
- `SELECT x: 42` — prefix alias syntax
- Trailing commas allowed in SELECT

### Query features
- `count()` instead of `count(*)`
- Reusable aliases in WHERE/GROUP BY/HAVING
- Lateral column aliases: `SELECT i+1 AS j, j+2 AS k`
- `COLUMNS(*)` with regex, EXCLUDE, REPLACE, lambdas
- `FILTER (WHERE ...)` for conditional aggregation
- GROUPING SETS / CUBE / ROLLUP
- `max(col, 3)` — top-N as list; `arg_max(arg, val, n)`, `min_by(arg, val, n)`
- DESCRIBE, SUMMARIZE, PIVOT / UNPIVOT
- `SET VARIABLE x = expr` -> `getvariable('x')`

### Data import
- Direct: `FROM 'file.csv'`, `FROM 'data.parquet'`
- Globbing: `FROM 'data/part-*.parquet'`
- Auto-detection for CSV headers/schemas

### Expressions
- Dot chaining: `'hello'.upper()`, `col.trim().lower()`
- List comprehensions: `[x*2 FOR x IN list_col]`
- Slicing: `col[1:3]`, `col[-1]`
- STRUCT: `SELECT s.* FROM (SELECT {'a': 1} AS s)`
- `format('{}->{}', a, b)`

### Joins
- ASOF joins — approximate matching on ordered data
- POSITIONAL joins — match by position
- LATERAL joins — reference prior expressions

### DDL
- `CREATE OR REPLACE TABLE` — no DROP needed
- CTAS: `CREATE TABLE ... AS SELECT`
- `INSERT INTO ... BY NAME` — match by column name
- `INSERT OR IGNORE INTO` / `INSERT OR REPLACE INTO`

## Cross-references
- **duckdb-state** — initialize session state (extensions, credentials, macros)
- **duckdb-docs** — search DuckDB documentation for syntax/functions
- **duckdb-read-file** — auto-detect and explore unknown file formats
- **spatial-analysis** — DuckDB spatial + GDAL workflows, ArcGIS macros (`.duckdb-skills/arcgis.sql`)
- **duckdb-install** — install missing extensions on error
- **duckdb-attach-db** — attach persistent .duckdb databases
