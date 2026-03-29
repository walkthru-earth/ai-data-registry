# Shapefile (Esri-Relevant Details)

## Key Limitations for Esri Workflows

| Limitation | Detail |
|------------|--------|
| Field name length | Max 10 characters (truncated with serial suffix if duplicates) |
| File size | 2 GB recommended max per .shp/.dbf (4 GB hard limit) |
| Geometry types | Single type per layer (no mixed) |
| Date fields | Date only (no DateTime, no Time) |
| Encoding | Reads .cpg file or DBF LDID for encoding, converts to UTF-8 |
| Field types | Integer, Integer64, Real, String, Date only |

## Long Field Names (GDAL 3.13+)

Starting with GDAL 3.13, `.shp.xml` sidecar files preserve field names longer than 10 characters and field aliases. Older GDAL versions lose this information during conversion.

## Convert Shapefile to GeoParquet (Recommended Migration)

```bash
pixi run gdal vector convert input.shp output.parquet --overwrite

# With CRS override if .prj is missing or wrong
pixi run gdal vector reproject input.shp output.parquet \
  -s EPSG:2193 -d EPSG:4326 --overwrite
```

## Spatial Index

```bash
# Create .qix quadtree index (MapServer compatible)
pixi run gdal vector sql input.shp \
  --sql "CREATE SPATIAL INDEX ON input" --dialect OGRSQL
```

GDAL reads Esri `.sbn/.sbx` indexes but cannot write them.

## DateTime Handling

When converting FileGDB DateTime fields to Shapefile, the time component is lost (Shapefile only supports Date). Since GDAL 3.11, DateTime values written to Shapefile use ISO 8601 string format.

## Encoding Gotchas

- Shapefile stores text encoding in `.cpg` sidecar file
- If `.cpg` is missing, GDAL falls back to DBF LDID byte
- ArcGIS sometimes uses Windows code pages (e.g., CP1252) without a `.cpg` file
- Force encoding: `--config SHAPE_ENCODING="UTF-8"` or `--config SHAPE_ENCODING="CP1252"`

## Null/NA Geometry Issue

ArcGIS may replace NaN float values with NULL, then NULL with 0 during round-trips. There is no reliable way to encode sentinel values for null/NA in Shapefile that survive through ArcGIS (GitHub #12552).
