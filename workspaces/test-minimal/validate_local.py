"""Validate extracted Parquet output locally."""

import os

import duckdb

out = os.environ.get("OUTPUT_DIR", "output")
path = f"{out}/data.parquet"

r = duckdb.sql(f"SELECT COUNT(*) AS n FROM read_parquet('{path}')").fetchone()
print(f"Row count: {r[0]}")
assert r[0] >= 10, f"Too few rows: {r[0]}"
print("Validation passed.")
