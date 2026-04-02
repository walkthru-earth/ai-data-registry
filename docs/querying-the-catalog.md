# Querying the DuckLake Catalog

How to connect to the global catalog from any DuckDB session and query all registered data.

## Prerequisites

- DuckDB v1.5.1+ with `ducklake` and `httpfs` extensions
- S3 credentials (endpoint, key, secret, region) for the storage backend
- For local dev: credentials in your `.env` file (see `.env.example`)

## 1. Load credentials and attach the catalog

DuckLake requires `CREATE SECRET`, not `SET s3_*`. The secret must be created before attaching.

### From the repo (pixi + env file)

```bash
set -a && source .env && set +a

pixi run duckdb <<'SQL'
INSTALL ducklake; LOAD ducklake;
INSTALL httpfs;   LOAD httpfs;
SQL
```

Then in the DuckDB session:

```sql
-- Step 1: Create S3 secret (required for DuckLake, SET s3_* will NOT work)
CREATE SECRET registry_s3 (
    TYPE S3,
    KEY_ID    '${S3_WRITE_KEY_ID}',
    SECRET    '${S3_WRITE_SECRET}',
    ENDPOINT  '${S3_ENDPOINT_URL}',       -- without https://
    REGION    '${S3_REGION}',
    URL_STYLE 'path'
);

-- Step 2: Attach the global catalog (read-only)
-- Path: s3://{bucket}/{owner}/{repo}/{branch}/catalog.duckdb
ATTACH 'ducklake:s3://<bucket>/<owner>/<repo>/<branch>/catalog.duckdb'
    AS catalog (READ_ONLY);
```

### Shortcut: one-liner with shell variable expansion

```bash
set -a && source .env && set +a

pixi run duckdb -c "
INSTALL ducklake; LOAD ducklake;
INSTALL httpfs;   LOAD httpfs;

CREATE SECRET registry_s3 (
    TYPE S3,
    KEY_ID    '${S3_WRITE_KEY_ID}',
    SECRET    '${S3_WRITE_SECRET}',
    ENDPOINT  '${S3_ENDPOINT_URL#https://}',
    REGION    '${S3_REGION}',
    URL_STYLE 'path'
);

ATTACH 'ducklake:s3://${S3_BUCKET}/<owner>/<repo>/<branch>/catalog.duckdb'
    AS catalog (READ_ONLY);

-- your queries here
SELECT * FROM ducklake_table_info('catalog');
"
```

### Standalone DuckDB (no pixi)

```sql
INSTALL ducklake; LOAD ducklake;
INSTALL httpfs;   LOAD httpfs;

CREATE SECRET registry_s3 (
    TYPE S3,
    KEY_ID    '<your-access-key>',
    SECRET    '<your-secret-key>',
    ENDPOINT  '<your-s3-endpoint>',
    REGION    '<your-region>',
    URL_STYLE 'path'
);

ATTACH 'ducklake:s3://<bucket>/<owner>/<repo>/<branch>/catalog.duckdb'
    AS catalog (READ_ONLY);
```

## 2. Discover what is in the catalog

### List all schemas and tables

```sql
-- Table summary: name, file count, total size
SELECT * FROM ducklake_table_info('catalog');
```

### List snapshots (write history)

```sql
SELECT snapshot_id, snapshot_time, changes
FROM ducklake_snapshots('catalog')
ORDER BY snapshot_id DESC
LIMIT 20;
```

### List registered files for a table

```sql
SELECT data_file, data_file_size_bytes
FROM ducklake_list_files('catalog', '<table>', schema => '<schema>');
```

## 3. Query data

All data is queryable through the catalog using standard SQL. Schema names with hyphens need double-quoting.

### Row counts

```sql
-- Replace <schema> and <table> with actual names from ducklake_table_info
SELECT COUNT(*) FROM catalog."<schema>"."<table>";

-- Examples with real workspace schemas:
SELECT COUNT(*) FROM catalog."opensky-flights".states;
SELECT COUNT(*) FROM catalog.openmeteo.weather_hourly;
SELECT COUNT(*) FROM catalog."test-minimal".data;
```

### Sample rows

```sql
SELECT * FROM catalog."<schema>"."<table>" LIMIT 10;
```

### Describe a table's schema

```sql
DESCRIBE catalog."<schema>"."<table>";
```

## 4. Example queries

These examples use the reference workspaces shipped with the template. Replace schema and table names with your own.

### Flight positions (opensky-flights workspace)

```sql
SELECT
    icao24,
    callsign,
    origin_country,
    baro_altitude,
    velocity,
    snapshot_time
FROM catalog."opensky-flights".states
ORDER BY snapshot_time DESC
LIMIT 20;
```

### Weather for a specific city (openmeteo workspace)

```sql
SELECT
    city,
    country_code,
    time,
    temperature_2m,
    precipitation,
    wind_speed_10m
FROM catalog.openmeteo.weather_hourly
WHERE city = 'Berlin'
ORDER BY time;
```

### Air quality across countries (openmeteo workspace)

```sql
SELECT
    country_code,
    COUNT(DISTINCT city) AS cities,
    ROUND(AVG(us_aqi), 1) AS avg_aqi,
    ROUND(AVG(pm2_5), 1) AS avg_pm25
FROM catalog.openmeteo.air_quality
WHERE us_aqi IS NOT NULL
GROUP BY country_code
ORDER BY avg_aqi DESC
LIMIT 20;
```

### Spatial query (requires spatial extension)

```sql
LOAD spatial;

SELECT icao24, callsign, baro_altitude, velocity
FROM catalog."opensky-flights".states
WHERE ST_Within(
    geometry,
    ST_MakeEnvelope(5.0, 47.0, 15.0, 55.0)  -- Germany bbox
);
```

## 5. Validation queries

Use these to check catalog health and data integrity.

### Check for duplicate file registrations

```sql
-- Every file should appear exactly once
SELECT data_file, COUNT(*) AS n
FROM ducklake_list_files('catalog', '<table>', schema => '<schema>')
GROUP BY data_file
HAVING n > 1;
```

### Check for duplicate rows (by unique key)

Look up each table's `unique_cols` in its workspace `pixi.toml` under `[tool.registry.checks]`, then run:

```sql
-- Generic pattern: GROUP BY the unique_cols, check for count > 1
SELECT <unique_col_1>, <unique_col_2>, COUNT(*) AS n
FROM catalog."<schema>"."<table>"
GROUP BY <unique_col_1>, <unique_col_2>
HAVING n > 1
LIMIT 5;
```

Example for the reference workspaces:

```sql
-- opensky-flights.states: unique on (icao24, snapshot_time)
SELECT icao24, snapshot_time, COUNT(*) AS n
FROM catalog."opensky-flights".states
GROUP BY icao24, snapshot_time
HAVING n > 1
LIMIT 5;

-- openmeteo.weather_hourly: unique on (city, country_code, latitude, longitude, time)
SELECT city, country_code, latitude, longitude, time, COUNT(*) AS n
FROM catalog.openmeteo.weather_hourly
GROUP BY city, country_code, latitude, longitude, time
HAVING n > 1
LIMIT 5;

-- test-minimal.data: unique on (id)
SELECT id, COUNT(*) AS n
FROM catalog."test-minimal".data
GROUP BY id
HAVING n > 1
LIMIT 5;
```

### Compare file count vs row count

```sql
-- Quick health check: one row per table
-- Adapt the schema/table names to your registry
SELECT
    '<schema>.<table>' AS table_name,
    (SELECT COUNT(*) FROM ducklake_list_files('catalog', '<table>', schema => '<schema>')) AS files,
    (SELECT COUNT(*) FROM catalog."<schema>"."<table>") AS rows;
```

## 6. Time travel

DuckLake keeps a snapshot for every write. Query historical state by version or timestamp.

```sql
-- Query a table at a specific snapshot version
SELECT COUNT(*) FROM catalog."<schema>"."<table>" AT (VERSION => 5);

-- Query at a specific point in time
SELECT COUNT(*) FROM catalog."<schema>"."<table>"
    AT (TIMESTAMP => TIMESTAMP '2026-04-02 06:00:00');
```

## S3 layout reference

```
s3://{bucket}/{owner}/{repo}/{branch}/
    catalog.duckdb                              -- global catalog (single source of truth)
    {schema}/
        {table}/{timestamp}.parquet             -- data files
```

The `{owner}/{repo}/{branch}` prefix is derived from `GITHUB_REPOSITORY` and `GITHUB_REF_NAME` in CI. In local dev without those env vars, paths fall back to flat layout under the bucket root.

## Gotchas

- **`SET s3_*` does NOT work for DuckLake.** You must use `CREATE SECRET`. The catalog .duckdb file is opened through a separate DuckDB connection that only resolves credentials via the secret manager.
- **Schema names with hyphens need double-quoting.** Write `catalog."my-schema".my_table`, not `catalog.my-schema.my_table`.
- **Read-only attach is sufficient for queries.** Only the merge pipeline needs read-write access.
- **The global catalog reflects only merged data.** Recently extracted files may not appear until the next `merge-catalog` run (triggers every 10 minutes or on extract completion).
- **Credentials come from `.github/registry.config.toml` secret names.** The config file declares which env var names hold the credentials. See `.env.example` for the full list.
