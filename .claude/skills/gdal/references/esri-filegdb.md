# Esri File Geodatabase (OpenFileGDB Driver)

GDAL's built-in, dependency-free driver for .gdb directories. Full read/write/create since GDAL 3.6. Since GDAL 3.11, this is the only write path (FileGDB SDK driver delegates all writes here).

**Always use OpenFileGDB.** It has no external dependency, is thread-safe, handles more SRS definitions, supports VSI (ZIP, HTTP), and reads ArcGIS 9.x geodatabases.

## Read a File Geodatabase

```bash
# List all layers
pixi run gdal vector info MyData.gdb --summary

# Full info on all layers
pixi run gdal vector info MyData.gdb

# Specific layer
pixi run gdal vector info MyData.gdb --layer Parcels

# Preview features
pixi run gdal vector info MyData.gdb --layer Parcels --limit 5

# Include system/internal tables
pixi run gdal vector info MyData.gdb --oo LIST_ALL_TABLES=YES
```

## Read from ZIP

```bash
pixi run gdal vector info MyData.gdb.zip
pixi run gdal vector info /vsizip/MyData.zip/MyData.gdb

# Since GDAL 3.11: works even without .gdb extension in zip
pixi run gdal vector info /vsizip/random_name.zip
```

## Convert FileGDB to Other Formats

```bash
# All layers to GeoPackage
pixi run gdal vector convert MyData.gdb output.gpkg --overwrite

# Single layer to GeoParquet
pixi run gdal vector convert MyData.gdb output.parquet \
  --layer Parcels --overwrite

# Single layer to GeoJSON
pixi run gdal vector convert MyData.gdb output.geojson \
  --layer Parcels --overwrite

# With reprojection
pixi run gdal vector reproject MyData.gdb output.parquet \
  --layer Parcels -d EPSG:4326 --overwrite

# All layers to individual GeoParquet files
for layer in $(pixi run gdal vector info MyData.gdb --summary --format json \
  | pixi run python -c "import sys,json; [print(l['name']) for l in json.load(sys.stdin)['layers']]"); do
  pixi run gdal vector convert MyData.gdb "${layer}.parquet" --layer "$layer" --overwrite
done
```

## Create a New File Geodatabase

```bash
# From GeoParquet
pixi run gdal vector convert input.parquet output.gdb --overwrite

# Multiple layers into one GDB
pixi run gdal vector convert layer1.parquet output.gdb --overwrite --output-layer layer1
pixi run gdal vector convert layer2.parquet output.gdb --update --append --output-layer layer2
```

## Layer Creation Options

| Option | Purpose | Values/Default |
|--------|---------|----------------|
| `TARGET_ARCGIS_VERSION` | ArcGIS compatibility | `ALL` (default), `ARCGIS_PRO_3_2_OR_LATER` |
| `FEATURE_DATASET` | Feature Dataset folder name | string |
| `LAYER_ALIAS` | Layer display alias | string |
| `GEOMETRY_NAME` | Geometry column name | `SHAPE` (default) |
| `GEOMETRY_NULLABLE` | Allow null geometry | `YES` (default), `NO` |
| `FID` | OID column name | `OBJECTID` (default) |
| `XYTOLERANCE` | XY snapping tolerance | double |
| `ZTOLERANCE` | Z snapping tolerance | double |
| `XYSCALE` | XY coordinate precision (1/resolution) | double |
| `COLUMN_TYPES` | Force field types | `field1=type1,field2=type2` |
| `CONFIGURATION_KEYWORD` | Storage config | `DEFAULTS`, `MAX_FILE_SIZE_4GB`, `MAX_FILE_SIZE_256TB` |
| `CREATE_SHAPE_AREA_AND_LENGTH_FIELDS` | Auto Shape_Area/Shape_Length | `NO` (default), `YES` |
| `CREATE_MULTIPATCH` | Enable multipatch geometry (3.11+) | `NO` (default), `YES` |

```bash
pixi run gdal vector convert input.parquet output.gdb \
  --overwrite \
  --lco TARGET_ARCGIS_VERSION=ARCGIS_PRO_3_2_OR_LATER \
  --lco FEATURE_DATASET=Transportation \
  --lco CREATE_SHAPE_AREA_AND_LENGTH_FIELDS=YES
```

## Field Domains

Field domains (coded value and range) are readable since GDAL 3.3, writable since GDAL 3.5.

```bash
# Inspect domains (visible in JSON output)
pixi run gdal vector info MyData.gdb --format json --layer Parcels

# Inspect all domains
pixi run gdal vector info MyData.gdb --format json | pixi run python -c "
import sys, json
ds = json.load(sys.stdin)
if 'domains' in ds:
    for name, domain in ds['domains'].items():
        print(f'{name}: {domain}')
"
```

**Important:** When writing to a FileGDB field with a coded domain, pass the **description text** (e.g., "Residential"), not the coded value (e.g., "Res").

## Subtypes

GDAL reads FileGDB subtypes but does not fully resolve them automatically. Subtype field values are stored as integers. You need the domain-subtype mapping from GDB metadata to resolve human-readable values.

## Relationship Classes

Supported since GDAL 3.6 (read, write, create, update, delete). Accessible through `gdal vector info --format json` in the JSON output, but not directly manipulable via CLI commands.

## Special SQL Operations

```bash
# Get XML layer definition
pixi run gdal vector sql MyData.gdb \
  --sql "GetLayerDefinition Parcels" --dialect OGRSQL

# Get XML layer metadata
pixi run gdal vector sql MyData.gdb \
  --sql "GetLayerMetadata Parcels" --dialect OGRSQL

# Create attribute index (name max 16 chars, alphanumeric + underscore)
pixi run gdal vector sql MyData.gdb \
  --sql "CREATE INDEX idx_parcel_id ON Parcels(PARCEL_ID)" --dialect OGRSQL

# Recompute extent metadata
pixi run gdal vector sql MyData.gdb \
  --sql "RECOMPUTE EXTENT ON Parcels" --dialect OGRSQL

# Compact/repack (reclaim space from deletions)
pixi run gdal vector sql MyData.gdb \
  --sql "REPACK" --dialect OGRSQL

# Or use the dedicated command (GDAL 3.11+)
pixi run gdal driver openfilegdb repack MyData.gdb
```

## Pipeline Example

```bash
pixi run gdal vector pipeline \
  ! read MyData.gdb --layer Parcels \
  ! reproject --dst-crs EPSG:4326 \
  ! filter --where "AREA > 1000" \
  ! write parcels_wgs84.parquet --overwrite
```

## Spatial Indexing

- OpenFileGDB reads native `.spx` spatial indexes (since GDAL 3.2)
- If no index exists, an in-memory spatial index is built on first sequential read
- Disable with `--config OPENFILEGDB_IN_MEMORY_SPI=NO` for very large datasets where you only need a small subset
- Use `.atx` attribute indexes for WHERE clause acceleration

## Raster Layers

Supported since GDAL 3.7 (read-only):

```bash
pixi run gdal raster info MyData.gdb --summary
```

## Transaction Support

Supports RFC-54 emulated transactions via `StartTransaction(force=TRUE)`. Backs up state during transaction, restores on rollback. Not safe for concurrent writes.

## OpenFileGDB vs FileGDB (SDK) Comparison

| Feature | OpenFileGDB | FileGDB (SDK) |
|---------|-------------|---------------|
| External dependency | None | Esri FileGDB SDK |
| Read ArcGIS 9.x GDB | Yes | No (10.x only) |
| Write/create | Yes (GDAL 3.6+) | Delegates to OpenFileGDB (3.11+) |
| Thread safety | Yes | No |
| VSI support (ZIP, HTTP) | Yes | No |
| 64-bit integers | Yes (3.9+, Pro 3.2 target) | No |
| Any SRS | Yes | Limited |
| SDC compressed data | No | No |
| CDF compressed data | No | Yes (SDK 1.4+) |
| Field domains | Read/Write | Read/Write |
| Relationship classes | Full CRUD (3.6+) | Read only (3.6+) |
| Performance (many fields) | Better | Slower |

**Use FileGDB driver only if you need CDF decompression.** For everything else, OpenFileGDB is superior.
