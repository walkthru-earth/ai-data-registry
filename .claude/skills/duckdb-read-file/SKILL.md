---
name: read-file
description: >
  Read and explore any data file (CSV, JSON, Parquet, Avro, Excel, spatial, remote)
  using DuckDB. Use proactively when the user mentions a data file, asks "what's in
  this file", or wants to explore/preview/describe any dataset — even without saying DuckDB.
argument-hint: <filename or URL> [question about the data]
allowed-tools: Bash
---

Filename: `$0`
Question: `${1:-describe the data}`

## Step 1 — Classify path

- **S3** (`s3://`) -> needs httpfs + S3 secret
- **HTTPS** (`https://`) -> needs httpfs
- **GCS** (`gs://`, `gcs://`) -> needs httpfs + GCS secret
- **Azure** (`azure://`, `az://`, `abfss://`) -> needs httpfs + Azure secret
- **Local** -> resolve with find

### Local file resolution
```bash
pixi run python -c "
import pathlib
matches = [p for p in pathlib.Path('.').rglob('$0') if '.git' not in p.parts]
for m in matches: print(m.resolve())
"
```
Zero results -> stop. Multiple -> ask user. One -> use as `RESOLVED_PATH`.

## Step 2 — Set up state (remote files only)

Look for existing state:
```bash
STATE_DIR=""
test -f .duckdb-skills/state.sql && STATE_DIR=".duckdb-skills"
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "$PWD")"
PROJECT_ID="$(echo "$PROJECT_ROOT" | tr '/' '-')"
test -f "$HOME/.duckdb-skills/$PROJECT_ID/state.sql" && STATE_DIR="$HOME/.duckdb-skills/$PROJECT_ID"
```

For remote files, ensure credentials in state.sql:
- **S3**: `CREATE SECRET IF NOT EXISTS __default_s3 (TYPE S3, PROVIDER credential_chain);`
- **GCS**: `CREATE SECRET IF NOT EXISTS __default_gcs (TYPE GCS, PROVIDER credential_chain);` or use `duckdb-gcs` community extension
- **Azure**: `CREATE SECRET IF NOT EXISTS __default_azure (TYPE AZURE, PROVIDER credential_chain, ACCOUNT_NAME '...');`
- **HTTPS**: just `LOAD httpfs;`

## Step 3 — Ensure read_any macro

Check: `grep -q "read_any" "$STATE_DIR/state.sql"`

If missing, append the macro that dispatches to the right reader based on file extension:
- `.json/.jsonl/.geojson` -> `read_json_auto`
- `.csv/.tsv/.txt` -> `read_csv`
- `.parquet/.pq` -> `read_parquet`
- `.avro` -> `read_avro`
- `.xlsx/.xls` -> `read_xlsx` (needs spatial ext)
- `.shp/.gpkg/.fgb/.kml` -> `st_read` (needs spatial ext)
- `.ipynb` -> `read_json_auto` + unnest cells
- `.db/.sqlite` -> `sqlite_scan` (needs sqlite_scanner ext)

## Step 4 — Read the file

**Local** (sandboxed):
```bash
pixi run duckdb -init "$STATE_DIR/state.sql" -csv -c "
SET allowed_paths=['RESOLVED_PATH'];
SET enable_external_access=false;
SET allow_persistent_secrets=false;
SET lock_configuration=true;
DESCRIBE FROM read_any('RESOLVED_PATH');
SELECT count(*) AS row_count FROM read_any('RESOLVED_PATH');
FROM read_any('RESOLVED_PATH') LIMIT 10;
"
```

**Remote**: same queries but without sandbox settings, use state.sql for secrets.

**Spatial files**: add stem-wildcard to allowed_paths for sidecar files:
`SET allowed_paths=['RESOLVED_PATH', 'RESOLVED_PATH_WITHOUT_EXTENSION.*']`

On failure: install missing extensions, fix reader, or search **duckdb-docs**.

## Step 5 — Answer

Using schema + samples, answer: `${1:-describe the data}`.
Suggest **duckdb-query** for follow-up queries or **duckdb-attach-db** for large files.

## Cross-references
- **duckdb-state** — initialize session state (extensions, credentials)
- **duckdb-query** — follow-up SQL queries on the data
- **duckdb-docs** — search documentation for errors or unknown functions
- **spatial-analysis** — for ArcGIS FeatureServer URLs, use arcgis macros instead (`.duckdb-skills/arcgis.sql`)
