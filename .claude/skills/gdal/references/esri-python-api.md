# Esri File Geodatabase: Python GDAL API Reference

Deep inspection of .gdb files using the Python GDAL/OGR bindings. Use when the CLI cannot expose what you need, such as field domains, relationship classes, subtypes, spatial index state, layer metadata XML, or structured JSON exports (raw, flow graph, ERD schema).

**Run via:** `pixi run python script.py`

## When to Use Python Instead of CLI

| Task | CLI (`gdal vector info`) | Python (`osgeo.gdal`) |
|------|--------------------------|----------------------|
| Layer list, schema, CRS | Yes | Yes |
| Field domains (coded/range) | JSON output only | Full enumeration + per-field binding |
| Relationship classes | JSON output only | Full CRUD: names, cardinality, type, left/right tables |
| Subtypes | No | Read subtype field index |
| Spatial index state | No | `ExecuteSQL("GetLayerSpatialIndexState <layer>")` |
| Layer definition XML | `gdal vector sql --sql "GetLayerDefinition"` | Same via `ExecuteSQL` |
| Export to structured JSON | No | Full control (raw, flow graph, ERD) |
| Feature Dataset grouping | No | Via `layer.GetMetadata()["FEATURE_DATASET_NAME"]` |
| Geometry flags (Z, M) | Partial | `geom_defn.Is3D()`, `geom_defn.IsMeasured()` |

## Setup

```python
from osgeo import ogr, gdal

gdal.UseExceptions()
```

Always call `gdal.UseExceptions()` so errors raise Python exceptions instead of returning silent error codes.

## Open a File Geodatabase

```python
ds = gdal.OpenEx("MyData.gdb", gdal.OF_VECTOR)

# From ZIP
ds = gdal.OpenEx("/vsizip/MyData.zip/MyData.gdb", gdal.OF_VECTOR)

# Read-write (for edits)
ds = gdal.OpenEx("MyData.gdb", gdal.OF_VECTOR | gdal.OF_UPDATE)
```

## Inspect Layers

```python
for i in range(ds.GetLayerCount()):
    layer = ds.GetLayerByIndex(i)
    name = layer.GetName()
    geom_type = ogr.GeometryTypeToName(layer.GetGeomType())
    feature_count = layer.GetFeatureCount()
    srs = layer.GetSpatialRef()
    fid_col = layer.GetFIDColumn() or "OBJECTID"

    # Extent
    try:
        extent = layer.GetExtent()  # (minX, maxX, minY, maxY)
    except Exception:
        extent = None

    # Feature Dataset grouping
    metadata = layer.GetMetadata()
    feature_dataset = metadata.get("FEATURE_DATASET_NAME")
```

## Inspect Fields and Domains Per-Field

```python
defn = layer.GetLayerDefn()
for j in range(defn.GetFieldCount()):
    field = defn.GetFieldDefn(j)
    print(f"  {field.GetName()}: {field.GetFieldTypeName(field.GetType())}")
    print(f"    nullable={field.IsNullable()}, default={field.GetDefault()}")
    print(f"    alias={field.GetAlternativeName()}")

    domain_name = field.GetDomainName()
    if domain_name:
        print(f"    domain={domain_name}")
```

## Inspect Geometry Fields (Z, M Flags)

```python
for k in range(defn.GetGeomFieldCount()):
    geom_defn = defn.GetGeomFieldDefn(k)
    name = geom_defn.GetNameRef()
    gtype = ogr.GeometryTypeToName(geom_defn.GetType())
    is_3d = geom_defn.Is3D() if hasattr(geom_defn, "Is3D") else False
    is_m = geom_defn.IsMeasured() if hasattr(geom_defn, "IsMeasured") else False
    print(f"  {name} ({gtype}) Z={is_3d} M={is_m}")
```

## Read Global Field Domains

```python
domain_names = ds.GetFieldDomainNames()
if domain_names:
    for dom_name in domain_names:
        domain = ds.GetFieldDomain(dom_name)
        dtype = domain.GetDomainType()
        print(f"Domain: {domain.GetName()} (type={dtype})")

        if dtype == ogr.OFDT_CODED:
            # Returns dict: {code: description, ...}
            coded_values = domain.GetEnumeration()
            print(f"  Coded values: {coded_values}")

        elif dtype == ogr.OFDT_RANGE:
            print(f"  Range: {domain.GetMinAsDouble()} to {domain.GetMaxAsDouble()}")
```

**Remember:** When writing features to a field with a coded domain, pass the **description text** (e.g., "Residential"), not the code (e.g., "Res").

## Read Relationship Classes

```python
rel_names = ds.GetRelationshipNames()
if rel_names:
    for rel_name in rel_names:
        rel = ds.GetRelationship(rel_name)
        print(f"Relationship: {rel.GetName()}")
        print(f"  Left:  {rel.GetLeftTableName()}")
        print(f"  Right: {rel.GetRightTableName()}")
        print(f"  Cardinality: {rel.GetCardinality()}")
        print(f"  Type: {rel.GetType()}")
```

Cardinality values: `ogr.GRC_ONE_TO_ONE`, `ogr.GRC_ONE_TO_MANY`, `ogr.GRC_MANY_TO_MANY`.

## Check Spatial Index State

```python
sql_lyr = ds.ExecuteSQL(f"GetLayerSpatialIndexState {layer_name}")
if sql_lyr:
    feat = sql_lyr.GetNextFeature()
    state = int(feat.GetField(0))  # 0 = no index, 1 = indexed
    ds.ReleaseResultSet(sql_lyr)
```

**Important:** Always call `ds.ReleaseResultSet(sql_lyr)` after `ExecuteSQL` to avoid memory leaks.

## Get Layer Definition XML

```python
sql_lyr = ds.ExecuteSQL(f"GetLayerDefinition {layer_name}", dialect="OGRSQL")
if sql_lyr:
    feat = sql_lyr.GetNextFeature()
    xml_def = feat.GetField(0)  # Full XML layer definition
    ds.ReleaseResultSet(sql_lyr)
```

## Full Inspection Script

The script below inspects a .gdb and exports four structured JSON files:

1. **raw_data.json** - Per-layer metadata (schema, fields, domains, extent, spatial index)
2. **flow_data.json** - Graph nodes (layers) and edges (relationships) for visualization
3. **schema_connections.json** - Hierarchical grouping by Feature Dataset with domain usage
4. **erd_schema.json** - ERD representation (entities, relationships, domains with usage)

```python
import os
import json
from osgeo import ogr, gdal

gdal.UseExceptions()


def inspect_fgdb(fgdb_path,
                 raw_json_path="raw_data.json",
                 flow_json_path="flow_data.json",
                 schema_connections_path="schema_connections.json",
                 erd_schema_path="erd_schema.json"):
    if not os.path.exists(fgdb_path) and not fgdb_path.startswith("/vsizip/"):
        raise FileNotFoundError(f"Path not found: {fgdb_path}")

    ds = gdal.OpenEx(fgdb_path, gdal.OF_VECTOR)
    if not ds:
        raise RuntimeError(f"Failed to open: {fgdb_path}")

    raw_data = {"file_geodatabase": fgdb_path, "layers": [],
                "global_domains": {}, "relationships": []}
    flow_data = {"nodes": [], "edges": []}
    erd_entities = []
    erd_relationships = []
    erd_domains = []
    feature_dataset_dict = {}
    domain_usage = {}

    for i in range(ds.GetLayerCount()):
        layer = ds.GetLayerByIndex(i)
        if not layer:
            continue

        name = layer.GetName()
        geom_type = ogr.GeometryTypeToName(layer.GetGeomType())
        feature_count = layer.GetFeatureCount()
        spatial_ref = layer.GetSpatialRef()
        srs_wkt = spatial_ref.ExportToWkt() if spatial_ref else None
        defn = layer.GetLayerDefn()
        fid_column = layer.GetFIDColumn() or "OBJECTID"

        metadata = layer.GetMetadata()
        dataset_name = metadata.get("FEATURE_DATASET_NAME")
        ds_key = dataset_name or "No Feature Dataset"

        # Geometry fields
        geometry_fields = []
        for k in range(defn.GetGeomFieldCount()):
            gd = defn.GetGeomFieldDefn(k)
            flags = []
            if hasattr(gd, "Is3D") and gd.Is3D():
                flags.append("Z")
            if hasattr(gd, "IsMeasured") and gd.IsMeasured():
                flags.append("M")
            geometry_fields.append({
                "name": gd.GetNameRef(),
                "type": ogr.GeometryTypeToName(gd.GetType()),
                "flags": flags or None
            })

        # Attribute fields
        fields = []
        layer_domains = {}
        for j in range(defn.GetFieldCount()):
            fd = defn.GetFieldDefn(j)
            field_name = fd.GetName()
            field_info = {
                "name": field_name,
                "type": fd.GetFieldTypeName(fd.GetType()),
                "nullable": bool(fd.IsNullable()),
                "default": fd.GetDefault(),
                "alias": fd.GetAlternativeName(),
                "domain": None
            }
            dom = fd.GetDomainName()
            if dom:
                field_info["domain"] = dom
                layer_domains[field_name] = dom
                domain_usage.setdefault(dom, []).append(
                    {"table": name, "field": field_name})
            fields.append(field_info)

        # Extent
        try:
            extent = list(layer.GetExtent())
        except Exception:
            extent = None

        # Spatial index state
        spatial_index_state = None
        try:
            sql_lyr = ds.ExecuteSQL(f"GetLayerSpatialIndexState {name}")
            if sql_lyr:
                spatial_index_state = int(
                    sql_lyr.GetNextFeature().GetField(0))
                ds.ReleaseResultSet(sql_lyr)
        except Exception:
            pass

        layer_info = {
            "name": name, "geometry_type": geom_type,
            "feature_count": feature_count,
            "spatial_reference": srs_wkt,
            "primary_key": fid_column,
            "feature_dataset": dataset_name,
            "geometry_fields": geometry_fields,
            "fields": fields, "layer_domains": layer_domains,
            "extent": extent,
            "spatial_index_state": spatial_index_state
        }
        raw_data["layers"].append(layer_info)

        flow_data["nodes"].append({
            "id": name, "label": name,
            "data": {"geometry_type": geom_type,
                     "feature_count": feature_count,
                     "primary_key": fid_column,
                     "fields": fields}
        })

        erd_entities.append({
            "name": name, "dataset": ds_key,
            "primary_key": fid_column,
            "attributes": fields,
            "geometry_type": geom_type
        })

        feature_dataset_dict.setdefault(ds_key, []).append({
            "name": name, "geometry_type": geom_type,
            "fields": fields, "primary_key": fid_column
        })

    # Global domains
    global_domains = {}
    dom_names = ds.GetFieldDomainNames()
    if dom_names:
        for dom_name in dom_names:
            domain = ds.GetFieldDomain(dom_name)
            info = {"name": domain.GetName(),
                    "domain_type": domain.GetDomainType()}
            if domain.GetDomainType() == ogr.OFDT_CODED:
                info["coded_values"] = domain.GetEnumeration()
            elif domain.GetDomainType() == ogr.OFDT_RANGE:
                info["range"] = [domain.GetMinAsDouble(),
                                 domain.GetMaxAsDouble()]
            global_domains[dom_name] = info
    raw_data["global_domains"] = global_domains or None

    for dom_name, usages in domain_usage.items():
        detail = global_domains.get(dom_name, {})
        erd_domains.append({
            "name": dom_name,
            "domain_type": detail.get("domain_type"),
            "details": detail.get("coded_values",
                                  detail.get("range")),
            "usage": [{"entity": u["table"], "attribute": u["field"]}
                      for u in usages]
        })

    # Relationships
    rel_names = ds.GetRelationshipNames()
    if rel_names:
        for rel_name in rel_names:
            rel = ds.GetRelationship(rel_name)
            rel_info = {
                "name": rel.GetName(),
                "left_table": rel.GetLeftTableName(),
                "right_table": rel.GetRightTableName(),
                "cardinality": rel.GetCardinality(),
                "type": rel.GetType()
            }
            raw_data["relationships"].append(rel_info)
            flow_data["edges"].append({
                "id": rel_name,
                "source": rel.GetLeftTableName(),
                "target": rel.GetRightTableName(),
                "label": rel.GetName(),
                "cardinality": rel.GetCardinality(),
                "type": rel.GetType()
            })
            erd_relationships.append({
                "name": rel.GetName(),
                "source": rel.GetLeftTableName(),
                "target": rel.GetRightTableName(),
                "cardinality": rel.GetCardinality(),
                "type": rel.GetType()
            })
    else:
        raw_data["relationships"] = None

    # Schema connections
    schema_connections = {
        "feature_datasets": [
            {"dataset_name": k, "feature_classes": v}
            for k, v in feature_dataset_dict.items()
        ],
        "domains": [
            {"domain_name": k, "usage": v}
            for k, v in domain_usage.items()
        ]
    }

    erd_schema = {
        "entities": erd_entities,
        "relationships": erd_relationships,
        "domains": erd_domains
    }

    # Write all JSON outputs
    for path, data in [
        (raw_json_path, raw_data),
        (flow_json_path, flow_data),
        (schema_connections_path, schema_connections),
        (erd_schema_path, erd_schema),
    ]:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    return raw_data, flow_data, schema_connections, erd_schema


if __name__ == "__main__":
    import sys
    fgdb = sys.argv[1] if len(sys.argv) > 1 else "data.gdb"
    inspect_fgdb(fgdb)
```

**Run:**
```bash
pixi run python inspect_fgdb.py MyData.gdb
```

## JSON Output Schemas

### raw_data.json
Per-layer dump with schema, fields (including domain bindings), extent, spatial index state, plus global domains and relationships.

### flow_data.json
Graph structure for visualization (Svelte Flow, D3, Mermaid):
- `nodes[]` - one per layer (id, label, fields, geometry type)
- `edges[]` - one per relationship (source, target, cardinality, type)

### schema_connections.json
Hierarchical grouping:
- `feature_datasets[]` - Feature Dataset name with its feature classes
- `domains[]` - domain name with table/field usage list

### erd_schema.json
Entity-Relationship Diagram structure:
- `entities[]` - each table with primary key, attributes, geometry type
- `relationships[]` - links between entities (cardinality, type)
- `domains[]` - domain definitions with which entity/attribute uses them
