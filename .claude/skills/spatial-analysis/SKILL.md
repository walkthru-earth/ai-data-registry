---
name: spatial-analysis
description: >
  Geospatial analysis with DuckDB spatial (155+ ST_* functions) and unified GDAL CLI.
  Use when the user asks about spatial queries, geometry ops, coordinate transforms,
  distance/area calculations, spatial joins, or any map data processing.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
---

All tools via pixi: `pixi run duckdb`, `pixi run gdal`.

## Step 0 — Discover available ST_* functions (MUST run first)

Before writing any spatial query, run this to get the latest function signatures directly from the engine:

```sql
LOAD spatial;
SELECT function_name, function_type,
       STRING_AGG(DISTINCT return_type, ', ') AS return_types,
       COUNT(*) AS overloads,
       FIRST(description) AS description
FROM duckdb_functions()
WHERE function_name LIKE 'ST_%'
GROUP BY function_name, function_type
ORDER BY function_name;
```

This is the **authoritative source** — it reflects the exact version installed, including any new functions added in updates. Use it to verify parameter names, return types, and overloads before composing queries. To look up a specific function, add `AND function_name = 'ST_Transform'`.

## ST_* Quick Reference (113 functions)

| Category | Functions |
|----------|-----------|
| **Constructors** | `ST_Point`, `ST_MakeLine`, `ST_MakePolygon`, `ST_MakeEnvelope`, `ST_Collect`, `ST_Multi` |
| **Serialization** | `ST_GeomFromText`, `ST_GeomFromGeoJSON`, `ST_AsGeoJSON`, `ST_AsHEXWKB`, `ST_AsSVG` |
| **Measurement** | `ST_Area`, `ST_Length`, `ST_Distance`, `ST_Perimeter` + `_Spheroid`/`_Sphere` variants |
| **Predicates** | `ST_Contains`, `ST_Intersects`, `ST_Within`, `ST_Crosses`, `ST_Touches`, `ST_DWithin`, `ST_Overlaps`, `ST_Equals` |
| **Operations** | `ST_Buffer`, `ST_Union`, `ST_Intersection`, `ST_Difference`, `ST_Simplify`, `ST_ConvexHull`, `ST_ConcaveHull`, `ST_BuildArea` |
| **Coordinates** | `ST_X`, `ST_Y`, `ST_Z`, `ST_M`, `ST_XMin/Max`, `ST_YMin/Max`, `ST_ZMin/Max` |
| **Transform** | `ST_Transform`, `ST_FlipCoordinates`, `ST_Force2D/3DZ/3DM/4D`, `ST_Rotate`, `ST_Scale`, `ST_Translate` |
| **Line ops** | `ST_LineInterpolatePoint`, `ST_LineLocatePoint`, `ST_LineSubstring`, `ST_LineMerge`, `ST_ShortestLine` |
| **Indexing** | `ST_Hilbert`, `ST_QuadKey`, `ST_TileEnvelope` |
| **Coverage** | `ST_CoverageUnion`, `ST_CoverageSimplify`, `ST_CoverageInvalidEdges` + `_Agg` variants |
| **I/O** | `ST_Read`, `ST_ReadOSM`, `ST_ReadSHP`, `ST_Read_Meta`, `ST_Drivers` |
| **MVT** | `ST_AsMVT`, `ST_AsMVTGeom` |
| **Aggregates** | `ST_Union_Agg`, `ST_Extent_Agg`, `ST_Intersection_Agg`, `ST_MemUnion_Agg`, `ST_Collect` |
| **Validation** | `ST_IsValid`, `ST_IsSimple`, `ST_IsRing`, `ST_IsClosed`, `ST_IsEmpty`, `ST_MakeValid` |

## DuckDB Spatial (`pixi run duckdb`)
- Load: `INSTALL spatial; LOAD spatial;`
- Read: `SELECT * FROM ST_Read('file.parquet')`
- CRS: `ST_Transform(geom, 'EPSG:4326', 'EPSG:3857')`
- Joins: ST_Contains, ST_Within, ST_Intersects
- Aggregations: ST_Union_Agg, ST_Collect with GROUP BY
- Indexing: H3/S2 for large-scale point data

## GDAL (`pixi run gdal`) — unified CLI v3.12+
- Info: `gdal info input.gpkg`
- Convert: `gdal vector convert input.shp output.parquet`
- Reproject: `gdal vector reproject input.gpkg output.gpkg -d EPSG:4326`
- Pipeline: `gdal vector pipeline read in.gpkg ! reproject --dst-crs EPSG:4326 ! write out.parquet`
- Terrain: `gdal raster hillshade dem.tif hillshade.tif`

## GeoParquet — preferred interchange format
- Columnar, compressed, embedded CRS metadata
- Read: DuckDB `read_parquet`/`ST_Read`, GDAL `gdal info`
- Write from DuckDB: `COPY (...) TO 'out.parquet' (FORMAT PARQUET)`
- Write from GDAL: `gdal vector convert in.gpkg out.parquet`

## Analysis patterns
- Distance: use projected CRS (not EPSG:4326) for metric accuracy
- Large-scale points: H3 or S2 indexing
- Raster+vector: `gdal vector rasterize` / `gdal raster polygonize`
- Zonal stats: `gdal raster zonal-stats <raster> <zones> <out>`

## ArcGIS REST Services via DuckDB (VARIANT-optimized)

Swiss-knife macros for ArcGIS FeatureServer, MapServer, and REST services. Uses VARIANT (typed binary) for metadata access and `json_each()` + VARIANT cast for clean array iteration. Load once per session:

```bash
pixi run duckdb -init ".duckdb-skills/arcgis.sql"
```

Or inside a running session: `.read .duckdb-skills/arcgis.sql`

### Macro Quick Reference

| Level | Macro | Returns |
|-------|-------|---------|
| **L0** | `arcgis_type_map(esri_type)` | DuckDB type name (16 Esri types) |
| **L0** | `arcgis_geom_map(esri_geom)` | WKT geometry type |
| **L0** | `arcgis_query_url(base, layer, ...)` | Full query URL (token, pagination, orderBy) |
| **L1** | `arcgis_catalog(catalog_url)` | TABLE: item_type, name, service_type |
| **L1** | `arcgis_services(catalog_url)` | TABLE: service_name, service_type |
| **L1** | `arcgis_layers(service_url)` | TABLE: layer_id, layer_name, geometry_type, item_type |
| **L1** | `arcgis_layer_meta(layer_url)` | TABLE: meta as VARIANT (dot notation) |
| **L1** | `arcgis_meta(layer_url)` | TABLE: one-row typed summary (18 columns) |
| **L1** | `arcgis_count(query_url)` | TABLE: total |
| **L1** | `arcgis_ids(query_url)` | TABLE: oid_field, id_count |
| **L1** | `arcgis_extent(layer_url)` | TABLE: xmin..ymax, wkid, extent_geom |
| **L1** | `arcgis_fields(layer_url)` | TABLE: field_name, esri_type, duckdb_type, domain, ... |
| **L1** | `arcgis_domains(layer_url)` | TABLE: field_name, code, label |
| **L1** | `arcgis_subtypes(layer_url)` | TABLE: type_field, subtype_id, subtype_name, domains |
| **L1** | `arcgis_relationships(layer_url)` | TABLE: rel_id, rel_name, cardinality, key_field, ... |
| **L2** | `arcgis_check(url)` | TABLE: status, error_code, error_message, feature_count |
| **L2** | `arcgis_raw(url)` | TABLE: response as VARIANT (debugging) |
| **L2** | `arcgis_query(url)` | TABLE: properties + geometry (f=geojson, no CRS) |
| **L2** | `arcgis_read(url, crs)` | TABLE: properties + geometry WITH CRS (f=geojson) |
| **L2** | `arcgis_read_json(url, crs)` | TABLE: attrs JSON + geometry (f=json fallback, ring-safe) |
| **L2** | `arcgis_stats(query_url)` | TABLE: attrs JSON (server-side statistics, f=json) |
| **L2** | `arcgis_query_extent(query_url)` | TABLE: xmin..ymax, feature_count, extent_geom |

### Common Workflows

```sql
-- Discover services and layers
SELECT * FROM arcgis_catalog('https://server/arcgis/rest/services?f=json');
SELECT * FROM arcgis_layers('https://server/.../FeatureServer?f=json');

-- Inspect a layer (VARIANT dot notation for deep exploration)
SELECT * FROM arcgis_meta('https://.../FeatureServer/0?f=json');
SELECT meta.name, meta.extent.spatialReference.latestWkid,
       meta.advancedQueryCapabilities.supportsPagination
FROM arcgis_layer_meta('https://.../FeatureServer/0?f=json');

-- Quick recon: extent + count + IDs (no feature transfer)
SELECT * FROM arcgis_extent('https://.../FeatureServer/0?f=json');
SELECT * FROM arcgis_count('https://.../FeatureServer/0/query?where=1%3D1');
SELECT * FROM arcgis_ids('https://.../FeatureServer/0/query?where=1%3D1');

-- Download features with CRS
SELECT * FROM arcgis_read(
    'https://.../FeatureServer/0/query?where=1%3D1&outFields=%2A&outSR=4326&returnGeometry=true&f=geojson');

-- Server-side statistics (no data transfer)
SELECT * FROM arcgis_stats(
    'https://.../FeatureServer/0/query?where=1%3D1'
    '&outStatistics=%5B%7B%22statisticType%22%3A%22sum%22%2C%22onStatisticField%22%3A%22POP%22'
    '%2C%22outStatisticFieldName%22%3A%22total%22%7D%5D&f=json');

-- Paginated download (> maxRecordCount, with orderBy for reliability)
SELECT unnest(feature.properties),
       ST_SetCRS(ST_GeomFromGeoJSON(feature.geometry), 'EPSG:4326') AS geometry
FROM (
    SELECT unnest(features) AS feature
    FROM read_json_auto([
        'https://.../FeatureServer/0/query?where=1%3D1&outFields=%2A&outSR=4326'
        '&returnGeometry=true&orderByFields=OBJECTID+ASC'
        '&resultOffset=' || x::VARCHAR || '&resultRecordCount=2000&f=geojson'
        FOR x IN range(0, 10000, 2000)
    ])
);

-- Paginated + proxy: wrap URL list with _arcgis_apply_proxy_list
SELECT unnest(feature.properties),
       ST_SetCRS(ST_GeomFromGeoJSON(feature.geometry), 'EPSG:4326') AS geometry
FROM (
    SELECT unnest(features) AS feature
    FROM read_json_auto(_arcgis_apply_proxy_list([
        'https://.../FeatureServer/0/query?where=1%3D1&outFields=%2A&outSR=4326'
        '&returnGeometry=true&orderByFields=OBJECTID+ASC'
        '&resultOffset=' || x::VARCHAR || '&resultRecordCount=2000&f=geojson'
        FOR x IN range(0, 10000, 2000)
    ]))
);

-- Domain resolution (3 steps)
SET VARIABLE arcgis_layer = 'https://.../FeatureServer/0?f=json';
CREATE OR REPLACE TEMP TABLE _domains AS
WITH dl AS (SELECT * FROM arcgis_domains(getvariable('arcgis_layer')))
SELECT MAP(list(field_name), list(lookup)) AS all_domains
FROM (SELECT field_name, MAP(list(code::VARCHAR), list(label)) AS lookup FROM dl GROUP BY field_name);
CREATE OR REPLACE MACRO resolve_domain(field_val, field_name) AS
    COALESCE(
        (SELECT all_domains[field_name] FROM _domains)[field_val::VARCHAR],
        (SELECT all_domains[field_name] FROM _domains)[TRY_CAST(field_val AS INTEGER)::VARCHAR]
    );
```

### Reverse Proxy (proxy.ashx-style)

For ArcGIS servers behind a reverse proxy (proxy.ashx-style), where the proxy URL is prepended to the real URL:

```sql
-- Step 1: Set proxy prefix (trailing ? required)
SET VARIABLE arcgis_proxy = 'https://maps.example.com/proxy/proxy.ashx?';

-- Step 2: Set Referer header (scoped to proxy domain)
CREATE SECRET proxy_referer (TYPE HTTP,
    EXTRA_HTTP_HEADERS MAP {'Referer': 'https://maps.example.com/'},
    SCOPE 'https://maps.example.com/');

-- Step 3: Use macros normally, proxy is applied automatically
SELECT * FROM arcgis_layers(
    'https://gis-backend.example.com/server/rest/services/MyService/MapServer?f=json');

-- Disable proxy when switching to direct servers
SET VARIABLE arcgis_proxy = '';
```

### Authentication (11 methods, see arcgis.sql for full reference)

```sql
-- Token as URL param (simplest)
SET VARIABLE arcgis_token = 'YOUR_TOKEN';

-- Bearer token (recommended for ArcGIS Online)
CREATE SECRET arcgis_auth (TYPE HTTP, BEARER_TOKEN 'YOUR_TOKEN');

-- X-Esri-Authorization (Enterprise with web-tier auth like IWA)
CREATE SECRET arcgis_auth (TYPE HTTP,
    EXTRA_HTTP_HEADERS MAP {'X-Esri-Authorization': 'Bearer YOUR_TOKEN'});

-- Scoped secrets (different tokens per server, auto-selected by URL)
CREATE SECRET agol (TYPE HTTP, BEARER_TOKEN 'online_token',
    SCOPE 'https://services1.arcgis.com/');
CREATE SECRET enterprise (TYPE HTTP, BEARER_TOKEN 'ent_token',
    SCOPE 'https://gis.mycompany.com/');

-- Bearer + Referer combo
CREATE SECRET arcgis_auth (TYPE HTTP, BEARER_TOKEN 'TOKEN',
    EXTRA_HTTP_HEADERS MAP {'Referer': 'https://myapp.example.com'});

-- Behind corporate proxy
CREATE SECRET arcgis_proxy (TYPE HTTP, BEARER_TOKEN 'TOKEN',
    HTTP_PROXY 'http://proxy:8080', HTTP_PROXY_USERNAME 'u', HTTP_PROXY_PASSWORD 'p');

-- Persistent (survives restarts)
CREATE PERSISTENT SECRET arcgis_auth (TYPE HTTP, BEARER_TOKEN 'TOKEN');

-- Generate token from Enterprise (requires http_client community ext)
INSTALL http_client FROM community; LOAD http_client;
SET VARIABLE arcgis_token = (
    SELECT ((http_post_form(
        'https://gis.example.com/portal/sharing/rest/generateToken',
        MAP {'username': 'u', 'password': 'p', 'client': 'referer',
             'referer': 'https://myapp.example.com', 'expiration': '60', 'f': 'json'},
        MAP {}
    )).body::JSON->>'token'));

-- OAuth client_credentials (requires http_client community ext)
CREATE SECRET arcgis_oauth (TYPE HTTP, BEARER_TOKEN (
    SELECT ((http_post_form('https://www.arcgis.com/sharing/rest/oauth2/token',
        MAP {'client_id': 'ID', 'client_secret': 'SECRET',
             'grant_type': 'client_credentials', 'f': 'json'}, MAP {}
    )).body::JSON->>'access_token')));

-- Verify secret selection: SELECT * FROM which_secret('https://...', 'http');
```

Not supported (interactive flows): IWA, PKI, SAML, OAuth authorization_code.
Workaround: generate token via browser/tool, then use any method above.

Full reference: `.duckdb-skills/arcgis.sql`

## Cross-references
- **geoparquet** skill — gpio adds Hilbert sorting, bbox covering, validation
- **gdal** skill — complete unified GDAL CLI reference (including Esri references in `references/esri-*.md`)
- **duckdb-query** skill — interactive DuckDB SQL queries
- **duckdb-state** skill — manages `.duckdb-skills/state.sql` (extensions, credentials, macros)
- **duckdb-read-file** skill — explore any data file before analysis
- **data-explorer** agent — proactive dataset profiling (DuckDB + GDAL + gpio)
- **data-quality** agent — deep validation (nulls, geometry, CRS consistency)
- **pipeline-orchestrator** agent — multi-step workflow generation with pixi tasks
