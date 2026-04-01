---
paths:
  - "workspaces/**/*.sql"
  - "workspaces/**/*.py"
  - "research/**/*.sql"
  - "research/**/*.py"
---
# DuckDB SQL Rules

Run via `pixi run duckdb`. Use the **duckdb** skill for detailed references.

## Skill Routing

Load the right reference based on what you are doing:

| Task | Read |
|------|------|
| Spatial ops, ST_* functions, CRS | [spatial.md](../skills/duckdb/references/spatial.md) |
| ArcGIS REST queries | [arcgis.md](../skills/duckdb/references/arcgis.md) |
| DuckLake catalogs, time travel | [ducklake.md](../skills/duckdb/references/ducklake.md) |
| H3 hex grid (LAT, LNG order) | [h3.md](../skills/duckdb/references/h3.md) |
| A5 penta grid (LON, LAT order) | [a5.md](../skills/duckdb/references/a5.md) |
| S2 spherical geography | [geography.md](../skills/duckdb/references/geography.md) |
| File reading (CSV, Parquet, S3) | [read-file.md](../skills/duckdb/references/read-file.md) |
| Session state, credentials | [state.md](../skills/duckdb/references/state.md) |

## Pitfalls (memorize these)

**CRS must be VARCHAR, never INTEGER:**
```sql
-- WRONG: ST_SetCRS(geom, 4326)
-- RIGHT:
ST_SetCRS(geom, 'EPSG:4326')
ST_Transform(geom, 'EPSG:4326', 'EPSG:3857')  -- both source AND target required
```
Same rule applies to `arcgis_read(url, crs)` and `arcgis_read_json(url, crs)`.

**ST_Distance on EPSG:4326 returns degrees, not meters.** Project first or use `ST_Distance_Spheroid`:
```sql
-- Degrees (wrong for most use cases):
ST_Distance(a.geom, b.geom)
-- Meters (correct):
ST_Distance_Spheroid(a.geom, b.geom)
```

**Grid index coordinate order differs:**
- H3: `h3_latlng_to_cell(latitude, longitude, res)` (LAT, LNG)
- A5: `a5_lonlat_to_cell(longitude, latitude, res)` (LON, LAT)
- S2: `s2_cellfromlonlat(longitude, latitude)` (LON, LAT)

**S3 credentials: `SET s3_*` vs `CREATE SECRET`:**
- `SET s3_*` works for direct Parquet reads (`FROM 's3://...'`)
- `CREATE SECRET` required for DuckLake ATTACH and credential_chain
- Dots in bucket names (e.g. `source.coop`): `SET s3_url_style = 'path';`

**Integer dates need explicit casting:**
```sql
-- WRONG: WHERE date_col >= CURRENT_DATE - INTERVAL '7 days'  (if date_col is INTEGER)
-- RIGHT:
WHERE strptime(date_col::VARCHAR, '%Y%m%d')::DATE >= CURRENT_DATE - INTERVAL '7 days'
```

## GeoParquet Output

```sql
COPY (
    SELECT *, ST_Envelope(geometry) AS bbox
    FROM read_parquet('input.parquet')
    ORDER BY ST_Hilbert(geometry)
) TO 'output.parquet' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000);
```
Then validate: `pixi run gpio check all output.parquet`
