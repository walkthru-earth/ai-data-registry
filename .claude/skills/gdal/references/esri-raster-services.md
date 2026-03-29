# Raster Access from ArcGIS Services

Access map tiles, imagery, and raster data from ArcGIS MapServer and ImageServer endpoints.

## MapServer via WMS Driver

Generate a local XML config, then use it as a raster dataset:

```bash
# Generate WMS XML config from ArcGIS MapServer
pixi run gdal raster convert \
  "https://server/arcgis/rest/services/Imagery/MapServer?f=json" \
  wms_config.xml -f WMS

# Use the config to access tiles
pixi run gdal raster info wms_config.xml
pixi run gdal raster convert wms_config.xml output.tif \
  --co COMPRESS=DEFLATE
```

## MapServer Tiles via TMS Minidriver

Create an XML configuration file (`arcgis_tms.xml`):

```xml
<GDAL_WMS>
  <Service name="TMS">
    <ServerUrl>https://server/arcgis/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}</ServerUrl>
  </Service>
  <DataWindow>
    <UpperLeftX>-20037508.34</UpperLeftX>
    <UpperLeftY>20037508.34</UpperLeftY>
    <LowerRightX>20037508.34</LowerRightX>
    <LowerRightY>-20037508.34</LowerRightY>
    <TileLevel>17</TileLevel>
    <TileCountX>1</TileCountX>
    <TileCountY>1</TileCountY>
    <YOrigin>top</YOrigin>
  </DataWindow>
  <Projection>EPSG:3857</Projection>
  <BlockSizeX>256</BlockSizeX>
  <BlockSizeY>256</BlockSizeY>
  <BandsCount>3</BandsCount>
  <MaxConnections>10</MaxConnections>
  <Cache />
</GDAL_WMS>
```

```bash
pixi run gdal raster info arcgis_tms.xml
pixi run gdal raster clip arcgis_tms.xml output.tif \
  --bbox -122.5 37.5 -122.0 38.0
```

## ArcGIS Server REST API Minidriver (Untiled Export)

For full-resolution map image export (`arcgis_ags.xml`):

```xml
<GDAL_WMS>
  <Service name="AGS">
    <ServerUrl>https://server/arcgis/rest/services/Imagery/MapServer</ServerUrl>
  </Service>
  <DataWindow>
    <UpperLeftX>-20037508.34</UpperLeftX>
    <UpperLeftY>20037508.34</UpperLeftY>
    <LowerRightX>20037508.34</LowerRightX>
    <LowerRightY>-20037508.34</LowerRightY>
    <SizeX>512</SizeX>
    <SizeY>512</SizeY>
  </DataWindow>
  <BandsCount>3</BandsCount>
</GDAL_WMS>
```

## ImageServer Access

ArcGIS ImageServer provides raster catalog services. Access via the Export Image endpoint:

```bash
# Direct download of a specific extent
pixi run gdal raster convert \
  "/vsicurl/https://server/arcgis/rest/services/DEM/ImageServer/exportImage?bbox=-122.5,37.5,-122.0,38.0&bboxSR=4326&size=1024,1024&format=tiff&f=image" \
  output.tif
```

**Note:** There is no dedicated ArcGIS ImageServer driver in GDAL yet (GitHub #12012, feature request open). Current workarounds use `/vsicurl/` with the Export Image REST endpoint or the WMS driver with custom XML.

## ESRIC (Esri Compact Cache) Driver

Reads Esri compact cache V2 tile packages (`.tpkx` or exploded cache directories):

```bash
pixi run gdal raster info cache_directory
pixi run gdal raster convert cache_directory output.tif --co COMPRESS=DEFLATE
```

Changes in recent versions:
- GDAL 3.13: Removed record count header check, ignores oversized LODs (>= 2^31 pixels). Added `IGNORE_OVERSIZED_LODS` open option.

## ArcGIS .tif.vat.dbf Raster Attribute Tables

Since GDAL 3.11, the GTiff driver automatically reads Raster Attribute Tables from ArcGIS-style `.tif.vat.dbf` sidecar files. No extra configuration needed.

## Authentication for Raster Services

Same methods as FeatureServer (see `esri-featureserver.md`):

```bash
# Token in URL
pixi run gdal raster convert \
  "/vsicurl/https://server/arcgis/rest/services/DEM/ImageServer/exportImage?bbox=...&f=image&token=YOUR_TOKEN" \
  output.tif

# HTTP header
pixi run gdal raster convert \
  --config GDAL_HTTP_HEADERS="X-Esri-Authorization: Bearer YOUR_TOKEN" \
  "/vsicurl/https://server/arcgis/rest/services/DEM/ImageServer/exportImage?bbox=...&f=image" \
  output.tif
```
