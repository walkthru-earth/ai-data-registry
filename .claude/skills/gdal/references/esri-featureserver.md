# ArcGIS FeatureServer Access (ESRIJSON Driver)

Read vector features from ArcGIS REST endpoints. Read-only. Supports automatic pagination (server >= 10.3).

## Connection Strings

```bash
# Direct layer query (most common)
"https://server/arcgis/rest/services/ServiceName/FeatureServer/0/query?where=1%3D1&outFields=*&f=json"

# With ESRIJSON: prefix (disambiguates from GeoJSON driver)
"ESRIJSON:https://server/arcgis/rest/services/ServiceName/FeatureServer/0/query?where=1%3D1&outFields=*&f=json"

# Local .json file containing Esri JSON
"ESRIJSON:data.json"
```

**Critical:** Always use `f=json`, never `f=pjson`. Using `f=pjson` breaks automatic pagination.

## Query Parameters

| Parameter | Purpose | Example |
|-----------|---------|---------|
| `where` | Attribute filter (SQL WHERE) | `where=POP>10000` |
| `outFields` | Fields to return | `outFields=NAME,POP,SHAPE` or `outFields=*` |
| `f` | Response format | `f=json` (always json, not pjson) |
| `resultRecordCount` | Page size | `resultRecordCount=5000` |
| `resultOffset` | Manual pagination start | `resultOffset=0` |
| `orderByFields` | Sort for reliable paging | `orderByFields=OBJECTID+ASC` |
| `geometry` | Spatial filter envelope | `geometry=-180,-90,180,90` |
| `geometryType` | Geometry filter type | `geometryType=esriGeometryEnvelope` |
| `spatialRel` | Spatial relationship | `spatialRel=esriSpatialRelIntersects` |
| `outSR` | Output spatial reference | `outSR=4326` |
| `returnGeometry` | Include geometry | `returnGeometry=true` |
| `token` | Auth token (query param) | `token=abc123...` |

## Open Options

| Option | Values | Default | Purpose |
|--------|--------|---------|---------|
| `FEATURE_SERVER_PAGING` | YES/NO | auto | Force pagination on/off |
| `HTTP_METHOD` | AUTO/GET/POST | AUTO | GET unless URL > 256 chars, then POST |

## Inspect a FeatureServer Layer

```bash
# Quick summary
pixi run gdal vector info \
  "https://sampleserver6.arcgisonline.com/arcgis/rest/services/PoolPermits/FeatureServer/0/query?where=1%3D1&outFields=*&f=json"

# With explicit driver hint
pixi run gdal vector info --if ESRIJSON \
  "https://server/arcgis/rest/services/Svc/FeatureServer/0/query?where=1%3D1&outFields=*&f=json"

# Peek at first 5 features
pixi run gdal vector info --limit 5 \
  "https://server/arcgis/rest/services/Svc/FeatureServer/0/query?where=1%3D1&outFields=*&f=json"
```

## Download to Local Formats

```bash
# To GeoParquet
pixi run gdal vector convert \
  "ESRIJSON:https://server/arcgis/rest/services/Svc/FeatureServer/0/query?where=1%3D1&outFields=*&f=json" \
  output.parquet --overwrite

# To GeoPackage
pixi run gdal vector convert \
  "ESRIJSON:https://server/arcgis/rest/services/Svc/FeatureServer/0/query?where=1%3D1&outFields=*&f=json" \
  output.gpkg --overwrite

# With explicit pagination
pixi run gdal vector convert \
  --oo FEATURE_SERVER_PAGING=YES \
  "ESRIJSON:https://server/arcgis/rest/services/Svc/FeatureServer/0/query?where=1%3D1&outFields=*&f=json" \
  output.parquet --overwrite

# With attribute filter
pixi run gdal vector convert \
  "ESRIJSON:https://server/arcgis/rest/services/Svc/FeatureServer/0/query?where=STATUS%3D%27Active%27&outFields=*&f=json" \
  filtered.parquet --overwrite
```

## Download with Reprojection

```bash
pixi run gdal vector reproject \
  "ESRIJSON:https://server/arcgis/rest/services/Svc/FeatureServer/0/query?where=1%3D1&outFields=*&f=json" \
  output.parquet -d EPSG:4326 --overwrite
```

## Pipeline: Download, Filter, Reproject, Write

```bash
pixi run gdal vector pipeline \
  ! read "ESRIJSON:https://server/arcgis/rest/services/Svc/FeatureServer/0/query?where=1%3D1&outFields=*&f=json" \
  ! reproject --dst-crs EPSG:4326 \
  ! filter --where "POP > 10000" \
  ! write output.parquet --overwrite
```

## Multiple Layers from One FeatureServer

GDAL cannot query multiple layers in a single request. Query each layer individually:

```bash
# Download layer 0
pixi run gdal vector convert \
  "ESRIJSON:https://server/arcgis/rest/services/Svc/FeatureServer/0/query?where=1%3D1&outFields=*&f=json" \
  output.gpkg --overwrite --output-layer layer0

# Append layer 1 to same GeoPackage
pixi run gdal vector convert \
  "ESRIJSON:https://server/arcgis/rest/services/Svc/FeatureServer/1/query?where=1%3D1&outFields=*&f=json" \
  output.gpkg --update --append --output-layer layer1
```

## Pagination

Automatic pagination works when:
1. Server is ArcGIS >= 10.3
2. Layer has `supportsPagination=true`
3. URL uses `f=json` (not `f=pjson`)
4. URL does NOT already contain `resultOffset` (or `FEATURE_SERVER_PAGING=YES` is set)

For reliable paging, add `orderByFields=OBJECTID+ASC`:

```bash
pixi run gdal vector convert \
  "ESRIJSON:https://server/arcgis/rest/services/Svc/FeatureServer/0/query?where=1%3D1&outFields=*&orderByFields=OBJECTID+ASC&f=json" \
  output.parquet --overwrite
```

Manual pagination when auto is unavailable:

```bash
OFFSET=0; BATCH=2000
while true; do
  COUNT=$(pixi run gdal vector info --format json \
    "ESRIJSON:https://server/.../FeatureServer/0/query?where=1%3D1&outFields=*&resultOffset=${OFFSET}&resultRecordCount=${BATCH}&f=json" \
    | python -c "import sys,json; print(json.load(sys.stdin).get('featureCount',0))")
  [ "$COUNT" -eq 0 ] && break
  pixi run gdal vector convert \
    "ESRIJSON:https://server/.../FeatureServer/0/query?where=1%3D1&outFields=*&resultOffset=${OFFSET}&resultRecordCount=${BATCH}&f=json" \
    output.gpkg --update --append --output-layer data
  OFFSET=$((OFFSET + BATCH))
done
```

## Authentication

**Method 1: Token as query parameter**

```bash
pixi run gdal vector convert \
  "ESRIJSON:https://server/arcgis/rest/services/SecuredSvc/FeatureServer/0/query?where=1%3D1&outFields=*&f=json&token=YOUR_TOKEN" \
  output.parquet --overwrite
```

**Method 2: HTTP header (recommended, more secure)**

```bash
pixi run gdal vector convert \
  --config GDAL_HTTP_HEADERS="X-Esri-Authorization: Bearer YOUR_TOKEN" \
  "ESRIJSON:https://server/arcgis/rest/services/SecuredSvc/FeatureServer/0/query?where=1%3D1&outFields=*&f=json" \
  output.parquet --overwrite
```

**Method 3: Standard Authorization header**

```bash
pixi run gdal vector convert \
  --config GDAL_HTTP_HEADERS="Authorization: Bearer YOUR_TOKEN" \
  "ESRIJSON:https://server/arcgis/rest/services/SecuredSvc/FeatureServer/0/query?where=1%3D1&outFields=*&f=json" \
  output.parquet --overwrite
```

**Generate a token (ArcGIS Server):**

```bash
curl -s -X POST "https://server/arcgis/tokens/generateToken" \
  -d "username=USER&password=PASS&client=requestip&f=json" \
  | pixi run python -c "import sys,json; print(json.load(sys.stdin)['token'])"
```

**Generate a token (ArcGIS Online):**

```bash
curl -s -X POST "https://www.arcgis.com/sharing/rest/generateToken" \
  -d "username=USER&password=PASS&client=referer&referer=https://www.arcgis.com&f=json" \
  | pixi run python -c "import sys,json; print(json.load(sys.stdin)['token'])"
```

## Field Type Support (GDAL 3.12+)

Since GDAL 3.12, the ESRIJSON driver recognizes these Esri field types natively:
- `esriFieldTypeDateOnly` (mapped to OGR Date)
- `esriFieldTypeTimeOnly` (mapped to OGR Time)
- `esriFieldTypeBigInteger` (mapped to OGR Integer64)
- `esriFieldTypeGUID` (mapped to OGR String)
- `esriFieldTypeGlobalID` (mapped to OGR String)

Before 3.12, these were all read as plain String fields.

## Known Limitations

- **No multi-layer discovery:** Cannot point at a bare FeatureServer URL and list layers. Must query each `/N/query` individually (GitHub #4613, #14225).
- **No curve geometry support:** Esri JSON curve objects (`curveRings`, bezier, circular arcs) are not parsed. Only straight-line rings work (GitHub #9418).
- **No retry on page failure:** If a single page request fails during pagination, the entire read fails. `GDAL_HTTP_MAX_RETRY` does not apply to pagination requests (GitHub #12081).
- **GET/POST switching:** Long WHERE clauses may exceed URL length limits. Use `--oo HTTP_METHOD=POST` or `--config GDAL_HTTP_HEADERS` for complex queries (GitHub #4703).
