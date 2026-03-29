# Spatial Analysis with DuckDB

All tools via pixi: `pixi run duckdb`, `pixi run gdal`.

## Step 0 -- Discover available ST_* functions (MUST run first)

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

This is the **authoritative source**, it reflects the exact version installed, including any new functions added in updates. Use it to verify parameter names, return types, and overloads before composing queries. To look up a specific function, add `AND function_name = 'ST_Transform'`.

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

## DuckDB Spatial patterns

- Load: `INSTALL spatial; LOAD spatial;`
- Read: `SELECT * FROM ST_Read('file.parquet')`
- CRS: `ST_Transform(geom, 'EPSG:4326', 'EPSG:3857')`
- Joins: ST_Contains, ST_Within, ST_Intersects
- Aggregations: ST_Union_Agg, ST_Collect with GROUP BY
- Indexing: H3/S2 for large-scale point data

## GDAL CLI (unified v3.12+)

- Info: `gdal info input.gpkg`
- Convert: `gdal vector convert input.shp output.parquet`
- Reproject: `gdal vector reproject input.gpkg output.gpkg -d EPSG:4326`
- Pipeline: `gdal vector pipeline read in.gpkg ! reproject --dst-crs EPSG:4326 ! write out.parquet`
- Terrain: `gdal raster hillshade dem.tif hillshade.tif`

See the **gdal** skill for the complete CLI reference.

## GeoParquet -- preferred interchange format

- Columnar, compressed, embedded CRS metadata
- Read: DuckDB `read_parquet`/`ST_Read`, GDAL `gdal info`
- Write from DuckDB: `COPY (...) TO 'out.parquet' (FORMAT PARQUET)`
- Write from GDAL: `gdal vector convert in.gpkg out.parquet`

See the **geoparquet** skill for optimization (Hilbert sorting, bbox covering, validation).

## Analysis patterns

- Distance: use projected CRS (not EPSG:4326) for metric accuracy
- Large-scale points: H3 or S2 indexing
- Raster+vector: `gdal vector rasterize` / `gdal raster polygonize`
- Zonal stats: `gdal raster zonal-stats <raster> <zones> <out>`
