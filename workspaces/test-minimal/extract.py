"""Generate test Parquet output."""

import os

import duckdb

out = os.environ.get("OUTPUT_DIR", "output")
dry_run = os.environ.get("DRY_RUN", "0") == "1"
row_count = 50 if dry_run else 200

os.makedirs(out, exist_ok=True)

duckdb.sql(f"""
    COPY (
        SELECT i AS id,
               'item_' || i AS name,
               37.77 + random()*0.01 AS lat,
               -122.42 + random()*0.01 AS lon
        FROM range({row_count}) t(i)
    ) TO '{out}/data.parquet' (FORMAT PARQUET)
""")

label = "Dry run" if dry_run else "Extract"
print(f"{label}: wrote {out}/data.parquet ({row_count} rows)")
