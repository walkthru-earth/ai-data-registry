# Esri Formats: Common Gotchas and Version History

## CRS Handling

- FileGDB uses Esri WKT for spatial references, which may differ from OGC/EPSG WKT
- OpenFileGDB handles more SRS definitions than the FileGDB SDK driver
- Since GDAL 3.10, `importFromEPSG()` automatically falls back to ESRI codes when an EPSG code is actually an ESRI one (with a warning)
- Since GDAL 3.11, fixed coordinate transformation when one CRS has an EPSG code that is actually an ESRI one
- Always verify CRS after conversion: `pixi run gdal vector info output.parquet`
- Use explicit reprojection when needed: `pixi run gdal vector reproject ... -d EPSG:4326`

## Date/Time Fields

| Format | Date | DateTime | Time |
|--------|------|----------|------|
| FileGDB (ALL target) | Yes | No | No |
| FileGDB (PRO_3_2 target) | Yes | Yes | Yes |
| Shapefile | Date only | No (time lost) | No |
| GeoParquet | Yes | Yes | Yes |
| GeoPackage | Yes | Yes | Yes |

## 64-bit Integer Support

| Format | Int64 Support |
|--------|--------------|
| OpenFileGDB (PRO_3_2 target) | Yes (GDAL 3.9+) |
| FileGDB SDK driver | No |
| Shapefile | Yes (Integer64 type) |
| GeoParquet | Yes |
| GeoPackage | Yes |

## SDC and CDF Compressed Data

- **SDC (Smart Data Compression):** Neither OpenFileGDB nor FileGDB can read it. Must export via ArcGIS to uncompressed .gdb first.
- **CDF (Compressed Data Format):** Only FileGDB SDK driver with SDK 1.4+ can read it. OpenFileGDB shows a warning message pointing users to the FileGDB driver (improved in 3.10).

## Blob Fields and Attachments

- GDAL reads blob fields from FileGDB with limitations
- Attachments (in GDB_Items tables) are not directly accessible through standard layer queries
- Use `--oo LIST_ALL_TABLES=YES` to see attachment tables, then query them directly

## ESRIJSON Pagination Failures

- Always use `f=json`, never `f=pjson`
- Add `orderByFields=OBJECTID+ASC` for reliable page ordering
- Check if server supports pagination: query the service endpoint (without `/query`) and look for `"supportsPagination": true`
- ArcGIS Server < 10.3 does not support pagination at all
- If a single page fails, the entire read fails (no retry, GitHub #12081)

## Feature Count Mismatch

- Some FeatureServer endpoints report incorrect feature counts
- `returnCountOnly=true` may not match actual paginated results
- Always validate row counts after download

## URL Encoding

- Spaces in WHERE clauses: use `%20` or `+`
- Equals sign: `where=STATUS%3D%27Active%27`
- Always test the URL in a browser first to verify valid JSON response

## Coordinate Snapping in FileGDB

- FileGDB applies coordinate snapping based on XY tolerance/scale settings
- This can subtly alter geometries during write operations
- Control with `XYTOLERANCE`, `XYSCALE`, `XORIGIN`, `XYORIGIN` layer creation options
- For high precision, use larger scale values (e.g., `XYSCALE=10000000`)

## Large FileGDB Performance

- OpenFileGDB builds in-memory spatial index on first full scan
- Disable with `OPENFILEGDB_IN_MEMORY_SPI=NO` for very large datasets where you only need a subset
- Use native `.spx` spatial indexes when available (GDAL 3.2+)
- Use `.atx` attribute indexes for WHERE clause acceleration
- `REPACK` after bulk deletes to reclaim disk space

## Reserved Column/Table Names

FileGDB and OpenFileGDB share a unified list of reserved keywords (updated in 3.10) that cannot be used for column or table names. Common ones: `ADD`, `ALTER`, `AND`, `AS`, `ASC`, `BETWEEN`, `BY`, `COLUMN`, `CREATE`, `DATE`, `DELETE`, `DESC`, `DISTINCT`, `DROP`, `EXISTS`, `FROM`, `GROUP`, `IN`, `INDEX`, `INSERT`, `INTO`, `IS`, `JOIN`, `LIKE`, `NOT`, `NULL`, `ON`, `OR`, `ORDER`, `SELECT`, `SET`, `TABLE`, `UPDATE`, `VALUES`, `WHERE`.

If your source data has a column matching a reserved word, GDAL will automatically rename it (typically by appending `_`).

---

## GDAL Version History for Esri Formats

| Version | Changes |
|---------|---------|
| **3.13** | Shapefile .shp.xml sidecar for long field names/aliases. ESRIC: ignore oversized LODs, `IGNORE_OVERSIZED_LODS` option. |
| **3.12** | ESRIJSON: recognizes DateOnly, TimeOnly, BigInteger, GUID, GlobalID field types. Layer definitions sealed (RFC 97). `gdalinfo -wkt_format WKT1_ESRI` restored. |
| **3.11** | FileGDB write/create delegated to OpenFileGDB. `gdal driver openfilegdb repack` command. Accepts zipped .gdb without .gdb extension. UTF-8 identifier truncation fix. GTiff reads `.tif.vat.dbf` ArcGIS RAT files. Fixed ESRI CRS code coordinate transforms. |
| **3.10** | `-if ESRIJSON` flag. Partial 64-bit ObjectID support (read-only). CadastralSpecialServices JSON parsing. Field alias from `'alias'` member. Unified reserved keywords list. ESRI code fallback in `importFromEPSG()`. |
| **3.9** | Coordinate precision (RFC 99) for OpenFileGDB/FileGDB. Int64 fields with `TARGET_ARCGIS_VERSION=ARCGIS_PRO_3_2_OR_LATER`. |
| **3.8** | GeoParquet 1.0.0 (relevant for FileGDB-to-Parquet workflows). |
| **3.7** | Raster layer support in OpenFileGDB (read-only). |
| **3.6** | OpenFileGDB write/update/create. Relationship class CRUD. |
| **3.5** | Field domain write support. REPACK for FileGDB. |
| **3.4** | Hierarchical GDB navigation (Feature Datasets). |
| **3.3** | Field domain read support (coded value, range). |
| **3.2** | Native .spx spatial index reading. |

## Notable Open GitHub Issues

| Issue | Topic | Status |
|-------|-------|--------|
| #14225 | Support opening bare FeatureServer URLs directly | Open (active) |
| #4703 | Auto-switch GET to POST for long URLs | Open |
| #12081 | Retry failed pagination page requests | Open |
| #4613 | Multi-layer discovery from FeatureServer root | Open |
| #9418 | Esri JSON curve/arc geometry parsing | Open |
| #12012 | Dedicated ArcGIS ImageServer driver | Open (feature request) |
| #12552 | Null/NA geometry encoding for ArcGIS round-trips | Open |
