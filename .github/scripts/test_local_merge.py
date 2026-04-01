# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "duckdb>=1.5.1",
# ]
# ///
"""Local test for DuckLake catalog merge logic (no S3 needed).

Creates workspace + global catalogs as local DuckDB files,
simulates extract -> merge -> query -> incremental merge.

CRITICAL: Catalog files use the DuckDB backend (.duckdb), NOT SQLite (.ducklake).
DuckDB catalogs support remote S3/HTTPS read-only access via httpfs.
SQLite catalogs do NOT support remote access (blocked by duckdb/ducklake#912).

Usage: uv run .github/scripts/test_local_merge.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import duckdb


def step(msg: str):
    print(f"\n{'='*60}\n  {msg}\n{'='*60}")


def main():
    tmpdir = tempfile.mkdtemp(prefix="registry-test-")
    print(f"Working directory: {tmpdir}")

    data_dir = os.path.join(tmpdir, "data")
    os.makedirs(data_dir)

    ws_catalog = os.path.join(tmpdir, "test-minimal.duckdb")
    global_catalog = os.path.join(tmpdir, "catalog.duckdb")
    errors = 0

    # ── Step 1: Extract test data (simulates pixi run -w test-minimal pipeline)
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

    # ── Step 2: Create workspace catalog and register batch 1
    step("Step 2: Create workspace DuckLake catalog")

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")

    con.execute(f"""
        ATTACH 'ducklake:{ws_catalog}' AS ws (
            DATA_PATH '{data_dir}/'
        )
    """)
    print(f"  Attached workspace catalog: {ws_catalog}")

    con.execute("CREATE SCHEMA IF NOT EXISTS ws.\"test-minimal\"")
    con.execute("""
        CREATE TABLE ws."test-minimal".data AS
        SELECT * FROM read_parquet(?)
    """, [batch1_path])

    files = con.execute("""
        SELECT data_file
        FROM ducklake_list_files('ws', 'data', schema => 'test-minimal')
    """).fetchall()
    print(f"  Workspace catalog has {len(files)} file(s)")

    ws_cols = con.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_catalog = 'ws'
          AND table_schema = 'test-minimal'
          AND table_name = 'data'
        ORDER BY ordinal_position
    """).fetchall()
    print(f"  Schema: {[(c, t) for c, t in ws_cols]}")
    con.close()

    # ── Step 3: Merge workspace catalog into global catalog (first merge)
    step("Step 3: Merge into global catalog (first merge)")

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")

    # Attach workspace (read-only)
    con.execute(f"""
        ATTACH 'ducklake:{ws_catalog}' AS ws (READ_ONLY)
    """)

    # Attach global (create new)
    con.execute(f"""
        ATTACH 'ducklake:{global_catalog}' AS global_cat (
            DATA_PATH '{data_dir}/'
        )
    """)
    print(f"  Created global catalog: {global_catalog}")

    # Disable auto_compact on global
    try:
        con.execute("CALL global_cat.set_option('auto_compact', false)")
        print("  Set auto_compact = false on global catalog")
    except duckdb.Error as e:
        print(f"  WARNING: Could not set auto_compact: {e}")

    # Table doesn't exist yet in global, create it
    con.execute("CREATE SCHEMA IF NOT EXISTS global_cat.\"test-minimal\"")

    col_defs = ", ".join(f'"{name}" {dtype}' for name, dtype in ws_cols)
    con.execute(f'CREATE TABLE global_cat."test-minimal".data ({col_defs})')
    print(f"  Created table test-minimal.data in global catalog")

    # Get files from workspace catalog
    ws_files = con.execute("""
        SELECT data_file
        FROM ducklake_list_files('ws', 'data', schema => 'test-minimal')
    """).fetchall()

    # Get files from global catalog (should be empty)
    try:
        global_files = con.execute("""
            SELECT data_file
            FROM ducklake_list_files('global_cat', 'data', schema => 'test-minimal')
        """).fetchall()
    except duckdb.Error:
        global_files = []

    global_set = {f[0] for f in global_files}
    new_files = [f[0] for f in ws_files if f[0] not in global_set]
    print(f"  New files to register: {len(new_files)}")

    # Register new files
    for file_path in new_files:
        con.execute(f"""
            CALL ducklake_add_data_files('global_cat', 'data', '{file_path}',
                schema => 'test-minimal',
                allow_missing => true,
                ignore_extra_columns => true
            )
        """)
    print(f"  Registered {len(new_files)} file(s) in global catalog")

    # Verify
    count = con.execute("""
        SELECT COUNT(*) FROM global_cat."test-minimal".data
    """).fetchone()[0]
    print(f"  Global catalog row count: {count}")

    if count != 100:
        print(f"  FAIL: Expected 100 rows, got {count}")
        errors += 1
    else:
        print(f"  PASS: Row count matches")

    con.close()

    # ── Step 4: Simulate incremental extract (batch 2)
    step("Step 4: Generate batch 2 (incremental)")

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

    # ── Step 5: Add batch 2 to workspace catalog
    step("Step 5: Append batch 2 to workspace catalog")

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")

    con.execute(f"""
        ATTACH 'ducklake:{ws_catalog}' AS ws (
            DATA_PATH '{data_dir}/'
        )
    """)

    con.execute("""
        INSERT INTO ws."test-minimal".data
        SELECT * FROM read_parquet(?)
    """, [batch2_path])

    ws_count = con.execute('SELECT COUNT(*) FROM ws."test-minimal".data').fetchone()[0]
    ws_files_after = con.execute("""
        SELECT data_file
        FROM ducklake_list_files('ws', 'data', schema => 'test-minimal')
    """).fetchall()
    print(f"  Workspace catalog: {ws_count} rows, {len(ws_files_after)} file(s)")
    con.close()

    # ── Step 6: Incremental merge (only new files)
    step("Step 6: Incremental merge into global catalog")

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")

    con.execute(f"ATTACH 'ducklake:{ws_catalog}' AS ws (READ_ONLY)")
    con.execute(f"""
        ATTACH 'ducklake:{global_catalog}' AS global_cat (
            DATA_PATH '{data_dir}/'
        )
    """)

    ws_files = con.execute("""
        SELECT data_file
        FROM ducklake_list_files('ws', 'data', schema => 'test-minimal')
    """).fetchall()

    global_files = con.execute("""
        SELECT data_file
        FROM ducklake_list_files('global_cat', 'data', schema => 'test-minimal')
    """).fetchall()

    global_set = {f[0] for f in global_files}
    new_files = [f[0] for f in ws_files if f[0] not in global_set]
    print(f"  Workspace files: {len(ws_files)}, Global files: {len(global_files)}, New: {len(new_files)}")

    for file_path in new_files:
        con.execute(f"""
            CALL ducklake_add_data_files('global_cat', 'data', '{file_path}',
                schema => 'test-minimal',
                allow_missing => true,
                ignore_extra_columns => true
            )
        """)
    print(f"  Registered {len(new_files)} new file(s)")

    # Verify final state
    final_count = con.execute("""
        SELECT COUNT(*) FROM global_cat."test-minimal".data
    """).fetchone()[0]
    print(f"  Global catalog final row count: {final_count}")

    if final_count != 150:
        print(f"  FAIL: Expected 150 rows, got {final_count}")
        errors += 1
    else:
        print(f"  PASS: Incremental merge correct")

    # ── Step 7: Query the global catalog (simulates end-user access)
    step("Step 7: Query global catalog")

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

    if stats[1] != 150:
        print(f"  FAIL: Expected 150 unique IDs, got {stats[1]}")
        errors += 1
    else:
        print(f"  PASS: All IDs unique across batches")

    # ── Step 8: Test CHECKPOINT (maintenance on workspace, not global)
    step("Step 8: Run CHECKPOINT on workspace catalog")

    # CHECKPOINT must run on workspace catalogs, never on the global catalog.
    # Global catalog has auto_compact=false, but CHECKPOINT still runs
    # expire_snapshots + cleanup_old_files which could delete shared files.
    # Production maintenance.py also sets auto_compact=false on workspace
    # catalogs to protect files shared with the global catalog.
    con.execute("DETACH global_cat")
    con.execute("DETACH ws")

    con2 = duckdb.connect()
    con2.execute("INSTALL ducklake; LOAD ducklake;")
    con2.execute(f"""
        ATTACH 'ducklake:{ws_catalog}' AS ws (
            DATA_PATH '{data_dir}/'
        )
    """)
    con2.execute("CALL ws.set_option('auto_compact', false)")

    try:
        con2.execute("USE ws")
        con2.execute("CHECKPOINT")
        print("  PASS: CHECKPOINT completed (auto_compact=false)")
    except duckdb.Error as e:
        print(f"  FAIL: CHECKPOINT failed: {e}")
        errors += 1

    con2.close()
    con.close()

    # ── Summary
    step("Summary")
    print(f"  Workspace catalog: {ws_catalog}")
    print(f"  Global catalog:    {global_catalog}")
    print(f"  Data directory:    {data_dir}")

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
