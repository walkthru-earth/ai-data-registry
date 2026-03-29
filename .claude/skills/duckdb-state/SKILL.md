---
name: duckdb-state
description: >
  Initialize and manage shared DuckDB session state (state.sql). Use when setting
  up extensions, credentials, or macros that all DuckDB skills share. Other skills
  delegate here for session initialization.
allowed-tools: Bash
---

Single source of truth for DuckDB session initialization. All `duckdb-*` skills use this state.

## State location

`.duckdb-skills/state.sql` at project root.

## Initialization

```bash
STATE_DIR=".duckdb-skills"
pixi run python -c "import pathlib; pathlib.Path('$STATE_DIR').mkdir(exist_ok=True)"

cat > "$STATE_DIR/state.sql" << 'SQL'
-- DuckDB session state — managed by duckdb-state skill
INSTALL spatial;  LOAD spatial;
INSTALL httpfs;   LOAD httpfs;
INSTALL fts;      LOAD fts;
INSTALL parquet;  LOAD parquet;

CREATE OR REPLACE MACRO read_any(path) AS TABLE
  SELECT * FROM query_table(
    CASE
      WHEN path LIKE '%.parquet' OR path LIKE '%.geoparquet'
        THEN format('read_parquet(''{}'')', path)
      WHEN path LIKE '%.csv' OR path LIKE '%.tsv'
        THEN format('read_csv(''{}'')', path)
      WHEN path LIKE '%.json' OR path LIKE '%.geojson' OR path LIKE '%.ndjson'
        THEN format('read_json_auto(''{}'')', path)
      ELSE format('read_parquet(''{}'')', path)
    END
  );
SQL
```

## Usage

```bash
pixi run duckdb -init "$STATE_DIR/state.sql" -c "<query>"
```

## Atomic updates

Check-then-append pattern via Python (cross-platform):
```bash
pixi run python -c "
import pathlib
state = pathlib.Path('$STATE_DIR/state.sql')
content = state.read_text()
if '<MARKER>' not in content:
    state.write_text(content + '<NEW_BLOCK>')
"
```

### Credential patterns

| Provider | Marker | Block to append |
|----------|--------|-----------------|
| S3 | `SET s3_region` | `SET s3_region='us-east-1'; SET s3_url_style='path';` + credential_chain |
| GCS | `SET gcs_` | `SET gcs_access_key_id=getenv('GCS_ACCESS_KEY_ID');` |
| Azure | `LOAD azure;` | `INSTALL azure; LOAD azure; CREATE SECRET azure_secret (TYPE AZURE, CONNECTION_STRING getenv('AZURE_STORAGE_CONNECTION_STRING'));` |

## Validation

```bash
pixi run duckdb -init "$STATE_DIR/state.sql" -c "SELECT 'state_ok';" 2>&1 | grep -q "state_ok" && echo "Valid" || echo "Invalid"
```

## Reset

```bash
rm -f "$STATE_DIR/state.sql"
# Re-run initialization above
```

## Addon macro files

| File | Purpose | Load command |
|------|---------|-------------|
| `.duckdb-skills/arcgis.sql` | ArcGIS FeatureServer macros (13 table/scalar macros) | `.read .duckdb-skills/arcgis.sql` or `-init .duckdb-skills/arcgis.sql` |

These are not auto-loaded by state.sql (to keep init fast). Load on demand when working with ArcGIS data. See **spatial-analysis** skill for full usage reference.

## Cross-references
- **duckdb-query** — sources state.sql before running queries
- **duckdb-read-file** — the `read_any` macro is initialized here
- **duckdb-install** — may call duckdb-state to add extensions
- **spatial-analysis** — ArcGIS macros, ST_* spatial functions, GDAL CLI workflows
