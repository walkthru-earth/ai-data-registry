---
paths:
  - "workspaces/**/*.py"
  - "workspaces/**/*.sql"
  - "workspaces/**/*.sh"
---
# Extract Script Development Rules

When building or editing a workspace extraction script, follow this workflow.

## 1. Research the Data Source First

Before writing any code:
- **Web search** the API docs for the latest version, endpoints, rate limits, auth requirements
- Check if the API is **free and keyless** (preferred) or needs `$WORKSPACE_SECRET_*` env vars
- Check response format: JSON array? Paginated? Nested? CSV? GeoJSON?
- Check if DuckDB can read it directly via `read_json_auto('https://...')` or `read_csv_auto('https://...')`

## 2. Validate with curl Before Coding

Test the endpoint manually to understand the real response shape:
```bash
curl -s 'https://api.example.com/data?limit=5' | python3 -m json.tool | head -50
```
This catches auth issues, unexpected formats, and pagination behavior before you write a line of code.

## 3. Choose the Simplest Technology

**Decision order (prefer the first that works):**

1. **Pure DuckDB SQL** if the API is HTTP GET with JSON/CSV response and no complex auth:
   ```sql
   -- DuckDB can fetch and parse in one step
   CREATE TABLE data AS
   SELECT * FROM read_json_auto('https://api.example.com/data');
   ```
   Use this for: simple REST APIs, static file downloads, public S3/HTTP Parquet

2. **Python + DuckDB** if you need batching, POST requests, pagination, retry logic, or API key headers:
   ```python
   # Python for HTTP, DuckDB for everything else
   response = urllib.request.urlopen(req)
   data = json.loads(response.read())
   db.executemany("INSERT INTO t VALUES (?, ?, ?)", rows)
   ```
   Use this for: rate-limited APIs, POST-only endpoints, multi-page pagination, complex auth

3. **Python + external libraries** only when the above cannot work:
   - Raster data needing GDAL/rasterio
   - Binary protocols (gRPC, websockets)
   - APIs with official Python SDKs that handle auth/pagination

**Never use pandas/geopandas for simple tabular transforms.** DuckDB SQL handles joins, filters, aggregations, and geometry natively.

## 4. HTTP Patterns

**DuckDB native (GET only, no auth):**
```sql
SELECT * FROM read_json_auto('https://api.example.com/data',
    maximum_object_size=10000000);
```

**Python urllib (POST, auth, batching):**
```python
import urllib.request, urllib.parse, json

data = urllib.parse.urlencode(params).encode()
req = urllib.request.Request(url, data=data,
    headers={"User-Agent": "ai-data-registry/workspace-name"})
response = urllib.request.urlopen(req, timeout=60)
result = json.loads(response.read())
```

**Retry with exponential backoff (rate-limited APIs):**
```python
for attempt in range(max_retries):
    try:
        response = urllib.request.urlopen(req, timeout=60)
        break
    except urllib.error.HTTPError as e:
        if e.code == 429 and attempt < max_retries - 1:
            wait = base_delay * (2 ** attempt)
            logging.warning("Rate limited, waiting %ds", wait)
            time.sleep(wait)
        else:
            raise
```

Prefer stdlib `urllib` over `requests`/`httpx` to avoid extra dependencies.

## 5. GeoParquet Output Standard

**Geometry must have CRS set at creation time**, not at export:
```sql
-- When building the table, always attach CRS as VARCHAR (never integer)
CREATE TABLE my_table AS
SELECT *, ST_SetCRS(ST_Point(lon, lat), 'EPSG:4326') AS geometry
FROM raw_data;
```

Then export with CRS, spatial sort, and bbox:
```sql
COPY (
    SELECT * REPLACE (ST_SetCRS(geometry, 'EPSG:4326') AS geometry),
           ST_Envelope(geometry) AS bbox
    FROM my_table
    ORDER BY ST_Hilbert(geometry), time_col  -- spatial sort + temporal tiebreak
) TO '{output_dir}/{table_name}.parquet' (
    FORMAT PARQUET,
    COMPRESSION ZSTD,
    COMPRESSION_LEVEL 15,
    ROW_GROUP_SIZE 100000
);
```
Validate: `pixi run gpio check all output.parquet`

Non-spatial tables skip geometry/Hilbert but keep ZSTD and row group size.

## 6. Required Environment Variables

```python
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "output")  # CI overrides this
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"
```

- **Never hardcode** output paths. Always use `OUTPUT_DIR`.
- **DRY_RUN must produce valid output** with fewer rows (for PR validation).
- Use `os.makedirs(OUTPUT_DIR, exist_ok=True)` at the start.

## 7. Logging

Use Python `logging`, never `print()`. See `logging.md` rule for details.
```python
logging.basicConfig(
    level=logging.DEBUG if DRY_RUN else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
```

## 8. Output Filenames Must Match Declared Tables

If `pixi.toml` declares `table = "data"`, the script must write `{OUTPUT_DIR}/data.parquet`.
If it declares `tables = ["states", "flights"]`, write `states.parquet` and `flights.parquet`.
