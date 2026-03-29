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

## Detach

```sql
USE memory;
DETACH my_lake;
```

## Documentation

Search DuckLake docs via [docs-search.md](docs-search.md) using the DuckLake index at `https://ducklake.select/data/docs-search.duckdb`.
Full docs: https://ducklake.select
