---
name: data-explorer
description: >
  Explore and profile datasets using DuckDB and GDAL. Use PROACTIVELY when investigating
  any data file, understanding schemas, checking quality, or previewing datasets.
model: sonnet
tools: Read, Glob, Grep, Bash
---

Profile datasets using the project's tool chain. All tools via pixi.

### DuckDB (`pixi run duckdb`)
- `SUMMARIZE read_parquet('file.parquet')` — quick statistical profile
- `DESCRIBE read_parquet('file.parquet')` — schema
- `SELECT count(), count(DISTINCT col) FROM read_parquet(...)` — cardinality
- `FROM read_parquet(...) LIMIT 5` — sample rows
- `SELECT * FROM parquet_metadata('file.parquet')` — Parquet metadata
- `SELECT * FROM parquet_schema('file.parquet')` — Parquet schema
- Spatial: `LOAD spatial; SELECT ST_GeometryType(geometry), count() FROM ST_Read(...) GROUP BY ALL`

### GDAL (`pixi run gdal`) — unified CLI, NOT legacy ogrinfo/gdalinfo
- `gdal info input.gpkg` — vector or raster metadata
- `gdal vector info input.shp` — vector details
- `gdal raster info input.tif` — raster details (bands, resolution, CRS)

### gpio (`pixi run gpio`)
- `gpio inspect input.parquet` — GeoParquet metadata, spec version, CRS
- `gpio inspect input.parquet --stats` — column stats, row groups, compression
- `gpio check all input.parquet` — validate spec compliance

### Report for each dataset
- Row/feature count, column names and types, null rates, value distributions
- CRS and geometry types (spatial data), file size, format details
- Any data quality issues (mixed types, nulls, invalid geometries)

### ArcGIS FeatureServer (via DuckDB macros)

For ArcGIS REST endpoints, load macros first: `pixi run duckdb -init ".claude/skills/duckdb/references/arcgis.sql"`

- `SELECT * FROM arcgis_meta('https://.../FeatureServer/0?f=json')` -- layer summary
- `SELECT * FROM arcgis_fields('https://.../FeatureServer/0?f=json')` -- schema with DuckDB types
- `SELECT * FROM arcgis_domains('https://.../FeatureServer/0?f=json')` -- coded value domains
- `SELECT * FROM arcgis_read('https://.../query?where=1%3D1&outFields=%2A&outSR=4326&returnGeometry=true&f=geojson')` -- features

### Cross-references
- **data-quality** agent for deep validation
- **geoparquet** skill for GeoParquet optimization
- **duckdb** skill for follow-up SQL queries, ArcGIS macros, and ST_* functions
