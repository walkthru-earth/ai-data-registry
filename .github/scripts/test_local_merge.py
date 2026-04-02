# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "duckdb>=1.5.1",
# ]
# ///
"""Local test for DuckLake catalog merge logic (no S3 needed).

Tests single global catalog with direct S3 scan simulation.
Covers append mode, replace mode, and CHECKPOINT (with compaction).

CRITICAL: Catalog files use the DuckDB backend (.duckdb), NOT SQLite (.ducklake).

Usage: uv run .github/scripts/test_local_merge.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

import duckdb


def step(msg: str):
    print(f"\n{'='*60}\n  {msg}\n{'='*60}")


def main():
    tmpdir = tempfile.mkdtemp(prefix="registry-test-")
    print(f"Working directory: {tmpdir}")

    data_dir = os.path.join(tmpdir, "data")
    os.makedirs(data_dir)

    global_catalog = os.path.join(tmpdir, "catalog.duckdb")
    errors = 0

    # ── Step 1: Generate test Parquet files (batch 1)
    step("Step 1: Generate test Parquet files (batch 1)")

    con = duckdb.connect()
    batch1_path = os.path.join(data_dir, "batch1.parquet")
    con.execute(f"""
        COPY (
            SELECT i AS id, 'item_' || i AS name,
                   37.77 + random()*0.01 AS lat,
                   -122.42 + random()*0.01 AS lon
            FROM range(100) t(i)
        ) TO '{batch1_path}' (FORMAT PARQUET)
    """)
    print(f"  Wrote {batch1_path}")
    con.close()

    # ── Step 2: Create global catalog and register batch 1 directly
    step("Step 2: Create global catalog, scan and register files")

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")

    con.execute(f"""
        ATTACH 'ducklake:{global_catalog}' AS global_cat (
            DATA_PATH '{data_dir}/'
        )
    """)
    print(f"  Attached global catalog: {global_catalog}")

    # Create table from file schema
    con.execute('CREATE SCHEMA IF NOT EXISTS global_cat."test-minimal"')
    con.execute(f"""
        CREATE TABLE global_cat."test-minimal".data AS
        SELECT * FROM read_parquet('{batch1_path}') LIMIT 0
    """)

    # Simulate scan + register (like merge_catalog.py merge_table)
    con.execute(f"""
        CALL ducklake_add_data_files('global_cat', 'data', '{batch1_path}',
            schema => 'test-minimal',
            allow_missing => true,
            ignore_extra_columns => true
        )
    """)

    count = con.execute('SELECT COUNT(*) FROM global_cat."test-minimal".data').fetchone()[0]
    files = con.execute("""
        SELECT data_file FROM ducklake_list_files('global_cat', 'data', schema => 'test-minimal')
    """).fetchall()
    print(f"  Global catalog: {count} rows, {len(files)} file(s)")

    if count != 100:
        print(f"  FAIL: Expected 100 rows, got {count}")
        errors += 1
    else:
        print(f"  PASS: Row count matches")

    con.close()

    # ── Step 3: Generate batch 2 (incremental append)
    step("Step 3: Generate batch 2 and incremental merge (append)")

    con = duckdb.connect()
    batch2_path = os.path.join(data_dir, "batch2.parquet")
    con.execute(f"""
        COPY (
            SELECT 100 + i AS id, 'new_' || i AS name,
                   38.0 + random()*0.01 AS lat,
                   -121.0 + random()*0.01 AS lon
            FROM range(50) t(i)
        ) TO '{batch2_path}' (FORMAT PARQUET)
    """)
    print(f"  Wrote {batch2_path}")
    con.close()

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"""
        ATTACH 'ducklake:{global_catalog}' AS global_cat (
            DATA_PATH '{data_dir}/'
        )
    """)

    # Simulate incremental merge: scan files, diff, register new
    registered = set(r[0] for r in con.execute("""
        SELECT data_file FROM ducklake_list_files('global_cat', 'data', schema => 'test-minimal')
    """).fetchall())

    all_files = [batch1_path, batch2_path]
    new_files = [f for f in all_files if f not in registered]
    print(f"  Registered: {len(registered)}, On disk: {len(all_files)}, New: {len(new_files)}")

    for file_path in new_files:
        con.execute(f"""
            CALL ducklake_add_data_files('global_cat', 'data', '{file_path}',
                schema => 'test-minimal',
                allow_missing => true,
                ignore_extra_columns => true
            )
        """)
    print(f"  Registered {len(new_files)} new file(s)")

    count = con.execute('SELECT COUNT(*) FROM global_cat."test-minimal".data').fetchone()[0]
    print(f"  Global catalog: {count} rows")

    if count != 150:
        print(f"  FAIL: Expected 150 rows, got {count}")
        errors += 1
    else:
        print(f"  PASS: Incremental append correct")

    con.close()

    # ── Step 4: Replace mode test
    step("Step 4: Replace mode (drop old, keep latest)")

    con = duckdb.connect()
    batch3_path = os.path.join(data_dir, "batch3.parquet")
    con.execute(f"""
        COPY (
            SELECT i AS id, 'replaced_' || i AS name,
                   39.0 + random()*0.01 AS lat,
                   -120.0 + random()*0.01 AS lon
            FROM range(75) t(i)
        ) TO '{batch3_path}' (FORMAT PARQUET)
    """)
    print(f"  Wrote {batch3_path} (replace-mode data)")
    con.close()

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"""
        ATTACH 'ducklake:{global_catalog}' AS global_cat (
            DATA_PATH '{data_dir}/'
        )
    """)

    # Simulate replace mode: get latest file, drop + recreate, register only latest
    cols = con.execute("""
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_catalog = 'global_cat' AND table_schema = 'test-minimal' AND table_name = 'data'
    """).fetchall()
    col_defs = ", ".join(f'"{name}" {dtype}' for name, dtype in cols)

    con.execute('DROP TABLE global_cat."test-minimal".data')
    con.execute(f'CREATE TABLE global_cat."test-minimal".data ({col_defs})')
    print(f"  Dropped and recreated table (replace mode)")

    con.execute(f"""
        CALL ducklake_add_data_files('global_cat', 'data', '{batch3_path}',
            schema => 'test-minimal',
            allow_missing => true,
            ignore_extra_columns => true
        )
    """)

    count = con.execute('SELECT COUNT(*) FROM global_cat."test-minimal".data').fetchone()[0]
    files = con.execute("""
        SELECT data_file FROM ducklake_list_files('global_cat', 'data', schema => 'test-minimal')
    """).fetchall()
    print(f"  Global catalog: {count} rows, {len(files)} file(s)")

    if count != 75:
        print(f"  FAIL: Expected 75 rows, got {count}")
        errors += 1
    elif len(files) != 1:
        print(f"  FAIL: Expected 1 file, got {len(files)}")
        errors += 1
    else:
        print(f"  PASS: Replace mode correct (1 file, 75 rows)")

    con.close()

    # ── Step 5: CHECKPOINT (compaction is now safe, single catalog owns all files)
    step("Step 5: Run CHECKPOINT (compaction safe, sole file owner)")

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"""
        ATTACH 'ducklake:{global_catalog}' AS global_cat (
            DATA_PATH '{data_dir}/'
        )
    """)

    try:
        con.execute("USE global_cat")
        con.execute("CHECKPOINT")
        print("  PASS: CHECKPOINT completed (compaction enabled)")
    except duckdb.Error as e:
        print(f"  FAIL: CHECKPOINT failed: {e}")
        errors += 1

    # Verify data survived compaction
    count = con.execute('SELECT COUNT(*) FROM global_cat."test-minimal".data').fetchone()[0]
    if count != 75:
        print(f"  FAIL: Post-CHECKPOINT count is {count}, expected 75")
        errors += 1
    else:
        print(f"  PASS: Data intact after CHECKPOINT ({count} rows)")

    con.close()

    # ── Step 6: Query the global catalog
    step("Step 6: Query global catalog")

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"ATTACH 'ducklake:{global_catalog}' AS global_cat (READ_ONLY)")

    sample = con.execute("""
        SELECT id, name, round(lat, 4) AS lat, round(lon, 4) AS lon
        FROM global_cat."test-minimal".data
        ORDER BY id
        LIMIT 5
    """).fetchall()
    print("  Sample rows:")
    for row in sample:
        print(f"    {row}")

    stats = con.execute("""
        SELECT COUNT(*) AS total,
               COUNT(DISTINCT id) AS unique_ids,
               round(MIN(lat), 2) AS min_lat,
               round(MAX(lat), 2) AS max_lat
        FROM global_cat."test-minimal".data
    """).fetchone()
    print(f"  Stats: total={stats[0]}, unique_ids={stats[1]}, lat=[{stats[2]}, {stats[3]}]")

    if stats[1] != 75:
        print(f"  FAIL: Expected 75 unique IDs, got {stats[1]}")
        errors += 1
    else:
        print(f"  PASS: All IDs unique after replace")

    con.close()

    # ── Summary
    step("Summary")
    print(f"  Global catalog: {global_catalog}")
    print(f"  Data directory:  {data_dir}")

    if errors == 0:
        print(f"\n  ALL TESTS PASSED")
    else:
        print(f"\n  {errors} TEST(S) FAILED")

    # Cleanup
    shutil.rmtree(tmpdir)
    print(f"  Cleaned up {tmpdir}")

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
