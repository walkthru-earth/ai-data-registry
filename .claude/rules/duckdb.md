---
paths:
  - "workspaces/**/*.sql"
  - "workspaces/**/*.py"
  - "research/**/*.sql"
  - "research/**/*.py"
---
# DuckDB SQL Rules

- Run DuckDB via `pixi run duckdb` to use the project-managed version
- Use DuckDB SQL dialect, not PostgreSQL or MySQL syntax
- Friendly SQL: `FROM table` without SELECT, `GROUP BY ALL`, `ORDER BY ALL`, `EXCLUDE`, `REPLACE`
- For spatial queries: `INSTALL spatial; LOAD spatial;` must be called first
- Use `ST_*` functions for geometry operations (ST_Read, ST_Area, ST_Distance, etc.)
- Prefer `read_parquet()` and `read_csv_auto()` for file-based queries
- For GeoParquet: `SELECT * FROM ST_Read('file.parquet')`
- Use CTEs to break complex queries into readable parts
- Use `QUALIFY` for window function filtering
- Use `arg_max()`/`arg_min()` for "most recent" patterns
- JSON access: `col->>'key'` returns text, `col->'$.path'` returns JSON

## DuckDB → GeoParquet Best Practices
When writing GeoParquet from DuckDB, apply optimizations manually:
```sql
COPY (
    SELECT *, ST_Envelope(geometry) as bbox
    FROM read_parquet('input.parquet')
    ORDER BY ST_Hilbert(geometry)
) TO 'output.parquet' (
    FORMAT PARQUET, COMPRESSION ZSTD, COMPRESSION_LEVEL 15, ROW_GROUP_SIZE 100000
);
```
Then validate: `pixi run gpio check all output.parquet`

## Common Pitfalls
- **ST_SetCRS requires VARCHAR, not INTEGER**: `ST_SetCRS(geom, 4326)` fails. Always pass a string: `ST_SetCRS(geom, 'EPSG:4326')`. This applies to `arcgis_read(url, crs)` and `arcgis_read_json(url, crs)` too.
- **INTEGER vs DATE comparison**: DuckDB does NOT auto-cast integers to dates. If a column stores dates as integers (e.g., `20260324`), cast explicitly before comparing:
  ```sql
  -- WRONG: AND SQLDATE >= CURRENT_DATE - INTERVAL '7 days'
  -- RIGHT: Cast the integer column to a DATE first
  AND CAST(SQLDATE::VARCHAR AS DATE) >= CURRENT_DATE - INTERVAL '7 days'
  -- Or use strptime:
  AND strptime(SQLDATE::VARCHAR, '%Y%m%d')::DATE >= CURRENT_DATE - INTERVAL '7 days'
  ```
- **File not found after failed COPY**: If a query fails mid-COPY, the output file won't exist. Subsequent queries referencing it will fail with "No files found". Fix the source query first.

## S3 Access Tips
- S3 buckets with dots in the name (e.g., `source.coop`) need path-style URLs because virtual-hosted style breaks SSL certificate validation:
  ```sql
  SET s3_url_style = 'path';
  ```
- Use `CREATE SECRET` with `PROVIDER credential_chain` for automatic credential discovery
- For public buckets: `SET s3_access_key_id = ''; SET s3_secret_access_key = '';`

## SQL Reference Files
- `.sql` files in `.claude/skills/duckdb/references/` (e.g., `state.sql`, `arcgis.sql`) are runtime files loaded via `-init`. Reading them requires user approval (ask permission in `settings.json`).
- Prefer the `.md` documentation first (e.g., `arcgis.md` documents all ArcGIS macros, `state.md` documents session state).
- To use the macros, pass the `.sql` file to DuckDB: `pixi run duckdb -init ".claude/skills/duckdb/references/arcgis.sql"`

## Session State
- Use the **duckdb** skill ([state.md](../skills/duckdb/references/state.md) reference) to initialize and manage `state.sql` (extensions, credentials, macros)
- State file location: `.claude/skills/duckdb/references/state.sql`
- Core extensions pre-loaded in state: spatial, httpfs, fts

## Cross-references
- **duckdb** skill → unified DuckDB hub with references for query execution, file reading, spatial analysis, ArcGIS macros, docs search, extension management, session state
- **geoparquet** skill → validate and optimize GeoParquet output from DuckDB
