"""Validate extracted GeoParquet output locally."""

import os

import duckdb

OUT = os.environ.get("OUTPUT_DIR", "output")
PATH = f"{OUT}/states.parquet"

db = duckdb.connect()
db.execute("INSTALL spatial; LOAD spatial;")

# Row count check
r = db.execute(f"SELECT COUNT(*) AS n FROM read_parquet('{PATH}')").fetchone()
count = r[0]
print(f"Row count: {count}")
assert count >= 1000, f"Too few rows: {count} (expected >= 1000)"

# Null check on key columns
nulls = db.execute(f"""
    SELECT
        COUNT(*) FILTER (WHERE icao24 IS NULL) AS null_icao24,
        COUNT(*) FILTER (WHERE longitude IS NULL) AS null_lon,
        COUNT(*) FILTER (WHERE latitude IS NULL) AS null_lat,
        COUNT(*) FILTER (WHERE snapshot_time IS NULL) AS null_snapshot
    FROM read_parquet('{PATH}')
""").fetchone()
print(f"Null counts - icao24: {nulls[0]}, lon: {nulls[1]}, lat: {nulls[2]}, snapshot: {nulls[3]}")
assert nulls[0] == 0, "icao24 must not be null"
assert nulls[1] == 0, "longitude must not be null"
assert nulls[2] == 0, "latitude must not be null"

# Unique icao24 per snapshot (each aircraft appears once per snapshot_time)
dupes = db.execute(f"""
    SELECT icao24, snapshot_time, COUNT(*) AS n
    FROM read_parquet('{PATH}')
    GROUP BY icao24, snapshot_time
    HAVING n > 1
    LIMIT 1
""").fetchone()
assert dupes is None, f"Duplicate (icao24, snapshot_time) found: {dupes}"

# Geometry column exists
cols = db.execute(f"DESCRIBE SELECT * FROM read_parquet('{PATH}')").fetchall()
col_names = [c[0] for c in cols]
assert "geometry" in col_names, f"Missing geometry column. Columns: {col_names}"

db.close()
print("Validation passed.")
