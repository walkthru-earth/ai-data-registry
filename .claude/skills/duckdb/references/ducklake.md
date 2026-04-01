# DuckLake - Open Data Lake Format

DuckLake is an open data lake and catalog format from the DuckDB team. Stores data as Parquet files, metadata in a SQL database (DuckDB, PostgreSQL, SQLite, MySQL).

## Install

```sql
INSTALL ducklake;
LOAD ducklake;
```

Requires DuckDB v1.3.0+.

## Choosing a catalog backend

| Backend | Multi-client | Setup | Best for | Limitations |
|---------|-------------|-------|----------|-------------|
| **DuckDB** | No, single-client only | Zero config, just a file path | Local dev, prototyping, single-user workflows | Cannot share across processes or machines |
| **PostgreSQL** | Yes, full concurrent access | Requires running PG 12+ server | Production, team collaboration, CI/CD pipelines | Heavier infra, needs `postgres` extension |
| **SQLite** | Limited, no concurrent read+write | Just a file path, lightweight | Embedded apps, edge deployments, simple sharing | No simultaneous readers and writers |
| **MySQL** | Yes (in theory) | Requires running MySQL 8+ server | Environments already running MySQL | Known issues, not recommended by DuckDB team |

**Rule of thumb**: Start with DuckDB for local work. Move to PostgreSQL when you need multiple clients or production durability.

**CRITICAL for this project**: We use the DuckDB catalog backend (.duckdb), NOT SQLite (.ducklake). DuckDB catalogs support remote S3/HTTPS read-only access via httpfs (`ATTACH 'ducklake:s3://bucket/{owner}/{repo}/{branch}/catalog.duckdb' AS cat (READ_ONLY)`). SQLite catalogs do NOT support remote access (blocked by duckdb/ducklake#912).

## Attach a DuckLake catalog

```sql
-- DuckDB catalog (single-client only)
ATTACH 'ducklake:metadata.ducklake' AS my_lake;

-- PostgreSQL catalog (multi-client, requires postgres extension)
INSTALL postgres;
ATTACH 'ducklake:postgres:dbname=ducklake_catalog host=localhost' AS my_lake
    (DATA_PATH 'data_files/');

-- SQLite catalog (requires sqlite extension)
INSTALL sqlite;
ATTACH 'ducklake:sqlite:metadata.sqlite' AS my_lake
    (DATA_PATH 'data_files/');

-- Cloud storage for data files
ATTACH 'ducklake:metadata.ducklake' AS my_lake
    (DATA_PATH 's3://my-bucket/lake/');
```

## Remote S3 Access (DuckLake + httpfs)

DuckLake remote attach requires `CREATE SECRET` for S3 credentials. The `SET s3_*` variables work for direct Parquet/httpfs reads but do NOT work for DuckLake catalog attachment. This is because DuckLake opens the catalog .duckdb file through a separate connection that only resolves credentials via the secret manager.

```sql
INSTALL ducklake; LOAD ducklake;
INSTALL httpfs;   LOAD httpfs;

-- REQUIRED: CREATE SECRET (SET s3_* variables will NOT work for DuckLake)
CREATE SECRET __default_s3 (
    TYPE S3,
    KEY_ID 'your-access-key',
    SECRET 'your-secret-key',
    ENDPOINT 'fsn1.your-objectstorage.com',
    REGION 'fsn1',
    URL_STYLE 'path'
);

-- Remote read-only attach (metadata queries, snapshots, file listings)
-- Paths include {owner}/{repo}/{branch}/ prefix for repo/branch isolation
ATTACH 'ducklake:s3://bucket/{owner}/{repo}/{branch}/catalog.duckdb' AS global (READ_ONLY);

-- Query catalog metadata
SELECT * FROM ducklake_snapshots('global');
USE global."opensky-flights";
SELECT * FROM ducklake_list_files('global', 'states');

-- Query data through the catalog
SELECT COUNT(*) FROM global."opensky-flights".states;
SELECT * FROM global."test-minimal".data LIMIT 5;

-- Remote read-write (for CI merge scripts)
ATTACH 'ducklake:s3://bucket/{owner}/{repo}/{branch}/.catalogs/weather.duckdb' AS weather
    (DATA_PATH 's3://bucket/{owner}/{repo}/{branch}/', READ_WRITE);
```

**When to use `SET s3_*` vs `CREATE SECRET`:**

| Operation | `SET s3_*` | `CREATE SECRET` |
|-----------|-----------|-----------------|
| `FROM 's3://bucket/file.parquet'` | Works | Works |
| `glob('s3://bucket/*')` | Works | Works |
| `ATTACH 'ducklake:s3://...'` | Does NOT work | Required |
| DuckLake data reads through catalog | Does NOT work | Required |

**Tip:** When using both DuckLake and direct Parquet reads in the same session, just use `CREATE SECRET`. It covers all cases.

## Basic operations

```sql
USE my_lake;
CREATE TABLE tbl (id INTEGER, name VARCHAR);
INSERT INTO tbl VALUES (1, 'hello');
SELECT * FROM tbl;
UPDATE tbl SET name = 'world' WHERE id = 1;
```

## Time travel

```sql
-- By version number
SELECT * FROM tbl AT (VERSION => 3);

-- By timestamp
SELECT * FROM tbl AT (TIMESTAMP => now() - INTERVAL '1 week');

-- Attach entire DB at a specific snapshot
ATTACH 'ducklake:metadata.duckdb' (SNAPSHOT_VERSION 3);
ATTACH 'ducklake:metadata.duckdb' (SNAPSHOT_TIME '2025-05-26 00:00:00');

-- List all snapshots
SELECT * FROM ducklake_snapshots('my_lake');
```

## Schema evolution

```sql
ALTER TABLE tbl ADD COLUMN new_col INTEGER;
ALTER TABLE tbl ADD COLUMN new_col VARCHAR DEFAULT 'my_default';
ALTER TABLE tbl DROP COLUMN old_col;
ALTER TABLE tbl RENAME old_col TO new_col;
ALTER TABLE tbl ALTER col1 SET TYPE BIGINT;
-- Nested struct fields
ALTER TABLE tbl ADD COLUMN nested.new_field INTEGER;
```

## Partitioning

```sql
-- Partition by column (Hive-style)
ALTER TABLE tbl SET PARTITIONED BY (region);

-- Temporal transforms: year, month, day, hour, identity
ALTER TABLE tbl SET PARTITIONED BY (year(created_at));

-- Remove partitioning
ALTER TABLE tbl RESET PARTITIONED BY;
```

## Key features

- **Snapshots**: every write creates a snapshot, queryable via `ducklake_snapshots()`
- **ACID transactions**: multi-table transactional guarantees via the catalog DB
- **Multi-client**: multiple DuckDB instances share one dataset via PostgreSQL/SQLite catalog
- **Cloud storage**: DATA_PATH supports S3, GCS, Azure, R2, NFS
- **Iceberg interop**: read/write Iceberg-compatible tables (v0.3+)
- **Geometry support**: spatial columns stored natively (v0.3+)
- **Data inlining**: sub-millisecond writes for small data

## Limitations

- No indexes, primary keys, foreign keys, UNIQUE, or CHECK constraints
- Parquet-only storage (no .duckdb files as data files)
- DuckDB catalog backend is single-client only
- Not production-ready yet (target: 1.0 in 2026)

## Pitfall: Never Overwrite Registered Files

`ducklake_add_data_files()` records `file_size_bytes` and `footer_size` at registration time. DuckLake uses these cached values for range requests when reading. If the file is later overwritten at the same S3 path (e.g., by a re-extraction), the stored metadata becomes stale. DuckLake will request a byte range past the end of the smaller file, causing **HTTP 416 Range Not Satisfiable** on S3.

**Symptoms:** `ATTACH` succeeds, metadata queries work (`ducklake_snapshots`, `ducklake_list_files`), but `SELECT * FROM table` fails with HTTP 416. Direct `read_parquet('s3://...')` works fine because it issues a fresh HEAD request.

**Diagnosis:**
```sql
-- Compare stored size vs actual
-- Stored (from catalog):
SELECT path, file_size_bytes, footer_size FROM ducklake_data_file WHERE end_snapshot IS NULL;
-- Actual: curl -I on the S3 URL, check Content-Length
```

**Prevention:** Use unique file paths per extraction (e.g., timestamped filenames or DuckLake-managed writes via INSERT). Never overwrite a registered Parquet file at the same path.

## Detach

```sql
USE memory;
DETACH my_lake;
```

## Documentation

Search DuckLake docs via [docs-search.md](docs-search.md) using the DuckLake index at `https://ducklake.select/data/docs-search.duckdb`.
Full docs: https://ducklake.select
