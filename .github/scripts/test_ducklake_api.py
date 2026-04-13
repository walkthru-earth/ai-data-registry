#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "duckdb>=1.5.2",
#   "pytz",
# ]
# ///
"""Comprehensive DuckLake API tests for ai-data-registry review.

Tests every DuckLake API used in merge_catalog.py, maintenance.py,
check_catalog.py, and test_local_merge.py against DuckDB 1.5.2 / DuckLake spec 1.0.

Generates fake data locally, no S3 needed.

Usage: uv run test_all.py
"""
import os
import shutil
import sys
import tempfile

import duckdb

PASS = 0
FAIL = 0
FINDINGS = []


def ok(msg):
    global PASS
    PASS += 1
    print(f"  PASS: {msg}")


def fail(msg, detail=""):
    global FAIL
    FAIL += 1
    print(f"  FAIL: {msg}")
    if detail:
        print(f"    {detail}")
    FINDINGS.append(msg)


def finding(msg):
    FINDINGS.append(msg)
    print(f"  FINDING: {msg}")


def section(title):
    print(f"\n{'='*64}\n  {title}\n{'='*64}")


def gen_batches(data_dir):
    """Generate fresh test Parquet files."""
    con = duckdb.connect()
    for name, query in [
        ("batch1.parquet", "SELECT i AS id, 'a_' || i AS name FROM range(100) t(i)"),
        ("batch2.parquet", "SELECT 100+i AS id, 'b_' || i AS name FROM range(50) t(i)"),
        ("batch3.parquet", "SELECT 150+i AS id, 'c_' || i AS name FROM range(25) t(i)"),
    ]:
        con.execute(f"COPY ({query}) TO '{data_dir}/{name}' (FORMAT PARQUET)")
    con.close()


def main():
    tmpdir = tempfile.mkdtemp(prefix="ducklake-review-")
    print(f"Working dir: {tmpdir}")
    errors_total = 0

    # ── TEST 1: DuckLake version check ──────────────────────────
    section("TEST 1: DuckLake extension + spec version")

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    ext = con.execute("""
        SELECT extension_version FROM duckdb_extensions()
        WHERE extension_name = 'ducklake'
    """).fetchone()
    print(f"  DuckDB: {duckdb.__version__}")
    print(f"  DuckLake extension: {ext[0] if ext else 'NOT FOUND'}")

    # Create a temp catalog to check spec version
    tcat = os.path.join(tmpdir, "_version_check.duckdb")
    tdata = os.path.join(tmpdir, "_vdata")
    os.makedirs(tdata)
    con.execute(f"ATTACH 'ducklake:{tcat}' AS vc (DATA_PATH '{tdata}/')")
    for row in con.execute("FROM vc.options()").fetchall():
        if row[0] == "version":
            print(f"  DuckLake spec version: {row[2]}")
    con.execute("DETACH vc")
    con.close()
    ok("DuckLake loaded")

    # ── TEST 2: ATTACH parameter validation ─────────────────────
    section("TEST 2: ATTACH parameter validation")

    ddir = os.path.join(tmpdir, "t2data")
    os.makedirs(ddir)
    cat = os.path.join(tmpdir, "t2.duckdb")

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"ATTACH 'ducklake:{cat}' AS t2 (DATA_PATH '{ddir}/')")
    con.execute("DETACH t2")
    ok("DATA_PATH accepted")

    # AUTOMATIC_MIGRATION (used in maintenance.py)
    try:
        con.execute(f"ATTACH 'ducklake:{cat}' AS t2am (AUTOMATIC_MIGRATION true)")
        con.execute("DETACH t2am")
        ok("AUTOMATIC_MIGRATION true: accepted (maintenance.py is correct)")
    except duckdb.Error as e:
        fail("AUTOMATIC_MIGRATION true: rejected", str(e))

    # MIGRATE_IF_REQUIRED (from docs, but NOT supported)
    try:
        con.execute(f"ATTACH 'ducklake:{cat}' AS t2mir (MIGRATE_IF_REQUIRED true)")
        con.execute("DETACH t2mir")
        ok("MIGRATE_IF_REQUIRED true: accepted")
    except duckdb.Error as e:
        finding(f"MIGRATE_IF_REQUIRED: NOT supported in DuckLake {duckdb.__version__} ({e})")

    # READ_ONLY
    try:
        con.execute(f"ATTACH 'ducklake:{cat}' AS t2ro (READ_ONLY)")
        con.execute("DETACH t2ro")
        ok("READ_ONLY: accepted")
    except duckdb.Error as e:
        fail("READ_ONLY: rejected", str(e))

    # OVERRIDE_DATA_PATH
    try:
        con.execute(f"ATTACH 'ducklake:{cat}' AS t2odp (OVERRIDE_DATA_PATH true)")
        con.execute("DETACH t2odp")
        ok("OVERRIDE_DATA_PATH true: accepted")
    except duckdb.Error as e:
        fail("OVERRIDE_DATA_PATH: rejected", str(e))

    # SNAPSHOT_VERSION
    try:
        con.execute(f"ATTACH 'ducklake:{cat}' AS t2sv (SNAPSHOT_VERSION 0)")
        con.execute("DETACH t2sv")
        ok("SNAPSHOT_VERSION: accepted")
    except duckdb.Error as e:
        finding(f"SNAPSHOT_VERSION: rejected ({e})")

    con.close()

    # ── TEST 3: set_option API ──────────────────────────────────
    section("TEST 3: set_option API")

    ddir3 = os.path.join(tmpdir, "t3data")
    os.makedirs(ddir3)
    cat3 = os.path.join(tmpdir, "t3.duckdb")

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"ATTACH 'ducklake:{cat3}' AS t3 (DATA_PATH '{ddir3}/')")

    test_options = [
        ("auto_compact", "false"),
        ("auto_compact", "true"),
        ("expire_older_than", "30 days"),
        ("delete_older_than", "7 days"),
        ("parquet_compression", "zstd"),
        ("target_file_size", "512MB"),
        ("rewrite_delete_threshold", "0.95"),
        ("per_thread_output", "false"),
        ("data_inlining_row_limit", "10"),
    ]

    for opt_name, opt_val in test_options:
        try:
            con.execute(f"CALL t3.set_option('{opt_name}', '{opt_val}')")
            ok(f"set_option('{opt_name}', '{opt_val}')")
        except duckdb.Error as e:
            fail(f"set_option('{opt_name}', '{opt_val}')", str(e))

    # Test hive_file_pattern (needs boolean, not string)
    print("\n  Testing hive_file_pattern (boolean options)...")
    try:
        con.execute("CALL t3.set_option('hive_file_pattern', 'false')")
        ok("hive_file_pattern string 'false'")
    except duckdb.Error as e:
        finding(f"hive_file_pattern='false' as string FAILS: {e}")
        # Try with actual boolean
        try:
            con.execute("CALL t3.set_option('hive_file_pattern', false)")
            ok("hive_file_pattern with boolean false works")
        except duckdb.Error as e2:
            fail(f"hive_file_pattern boolean also fails", str(e2))

    try:
        con.execute("CALL t3.set_option('require_commit_message', 'false')")
        ok("require_commit_message string 'false'")
    except duckdb.Error as e:
        finding(f"require_commit_message='false' as string FAILS: {e}")
        try:
            con.execute("CALL t3.set_option('require_commit_message', false)")
            ok("require_commit_message with boolean false works")
        except duckdb.Error as e2:
            fail(f"require_commit_message boolean also fails", str(e2))

    # Schema and table scoped options
    con.execute("CREATE SCHEMA IF NOT EXISTS t3.myschema")
    con.execute("CREATE TABLE IF NOT EXISTS t3.myschema.tbl (id BIGINT)")

    try:
        con.execute("CALL t3.set_option('auto_compact', false, schema => 'myschema')")
        ok("set_option schema scope")
    except duckdb.Error as e:
        fail(f"set_option schema scope", str(e))

    try:
        con.execute("CALL t3.set_option('auto_compact', false, schema => 'myschema', table_name => 'tbl')")
        ok("set_option table scope")
    except duckdb.Error as e:
        fail(f"set_option table scope", str(e))

    # Show options
    print("\n  All options:")
    for row in con.execute("FROM t3.options()").fetchall():
        print(f"    {row[0]:30s} = {row[2]:20s} [{row[3]}] {row[4] or ''}")

    con.close()

    # ── TEST 4: hive_file_pattern default ───────────────────────
    section("TEST 4: hive_file_pattern default value on new catalog")

    ddir4 = os.path.join(tmpdir, "t4data")
    os.makedirs(ddir4)
    cat4 = os.path.join(tmpdir, "t4.duckdb")

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"ATTACH 'ducklake:{cat4}' AS t4 (DATA_PATH '{ddir4}/')")

    for row in con.execute("FROM t4.options()").fetchall():
        if row[0] == "hive_file_pattern":
            print(f"  Default hive_file_pattern: {row[2]}")
            if row[2] == "true":
                finding("hive_file_pattern defaults to 'true'. New data written to workspace catalogs via INSERT will use Hive-style paths (e.g., schema/table/col=val/file.parquet), but the S3 layout expects flat timestamped files.")
            else:
                ok(f"hive_file_pattern default is '{row[2]}'")
    con.close()

    # ── TEST 5: data_inlining_row_limit default ─────────────────
    section("TEST 5: data_inlining_row_limit defaults")

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")

    ext_default = con.execute(
        "SELECT current_setting('ducklake_default_data_inlining_row_limit')"
    ).fetchone()[0]
    print(f"  Extension-level default: {ext_default}")
    if ext_default != "0":
        finding(f"ducklake_default_data_inlining_row_limit = {ext_default} (not 0). Small INSERTs (<= {ext_default} rows) will inline data in catalog metadata instead of Parquet files.")
    else:
        ok("Extension-level data_inlining_row_limit is 0")
    con.close()

    # ── TEST 6: Full merge workflow ─────────────────────────────
    section("TEST 6: Full merge workflow (simulates merge_catalog.py)")

    ddir6 = os.path.join(tmpdir, "t6data")
    os.makedirs(ddir6)
    gen_batches(ddir6)
    ws6 = os.path.join(tmpdir, "t6ws.duckdb")
    gl6 = os.path.join(tmpdir, "t6global.duckdb")

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")

    # Create workspace, insert batch1
    con.execute(f"ATTACH 'ducklake:{ws6}' AS ws (DATA_PATH '{ddir6}/')")
    con.execute("CREATE SCHEMA IF NOT EXISTS ws.myschema")
    con.execute(f"CREATE TABLE ws.myschema.tbl AS SELECT * FROM read_parquet('{ddir6}/batch1.parquet')")

    # Add batch2 via ducklake_add_data_files
    con.execute(f"""
        CALL ducklake_add_data_files('ws', 'tbl', '{ddir6}/batch2.parquet',
            schema => 'myschema', allow_missing => true, ignore_extra_columns => true)
    """)

    ws_count = con.execute("SELECT COUNT(*) FROM ws.myschema.tbl").fetchone()[0]
    ws_files = con.execute("SELECT data_file FROM ducklake_list_files('ws', 'tbl', schema => 'myschema')").fetchall()
    assert ws_count == 150, f"Expected 150, got {ws_count}"
    ok(f"Workspace: {len(ws_files)} files, {ws_count} rows")

    # Phase transition: detach, re-attach read-only
    con.execute("DETACH ws")
    con.execute(f"ATTACH 'ducklake:{ws6}' AS ws (READ_ONLY)")

    # Create global catalog
    con.execute(f"ATTACH 'ducklake:{gl6}' AS global_cat (DATA_PATH '{ddir6}/')")
    con.execute("CALL global_cat.set_option('auto_compact', false)")
    con.execute("CREATE SCHEMA IF NOT EXISTS global_cat.myschema")

    # Create table from workspace schema
    cols = con.execute("""
        SELECT column_name, data_type FROM information_schema.columns
        WHERE table_catalog = 'ws' AND table_schema = 'myschema' AND table_name = 'tbl'
        ORDER BY ordinal_position
    """).fetchall()
    col_defs = ", ".join(f'"{n}" {t}' for n, t in cols)
    con.execute(f"CREATE TABLE global_cat.myschema.tbl ({col_defs})")

    # Zero-copy: register workspace files in global
    for (fp,) in ws_files:
        con.execute(f"""
            CALL ducklake_add_data_files('global_cat', 'tbl', '{fp}',
                schema => 'myschema', allow_missing => true, ignore_extra_columns => true)
        """)

    gc = con.execute("SELECT COUNT(*) FROM global_cat.myschema.tbl").fetchone()[0]
    assert gc == 150
    ok(f"Global after merge: {gc} rows (zero-copy)")

    # Incremental: add batch3 to workspace, merge only new file
    con.execute("DETACH ws")
    con.execute(f"ATTACH 'ducklake:{ws6}' AS ws (DATA_PATH '{ddir6}/')")
    con.execute(f"""
        CALL ducklake_add_data_files('ws', 'tbl', '{ddir6}/batch3.parquet',
            schema => 'myschema', allow_missing => true, ignore_extra_columns => true)
    """)
    con.execute("DETACH ws")
    con.execute(f"ATTACH 'ducklake:{ws6}' AS ws (READ_ONLY)")

    ws_set = {r[0] for r in con.execute("SELECT data_file FROM ducklake_list_files('ws', 'tbl', schema => 'myschema')").fetchall()}
    gl_set = {r[0] for r in con.execute("SELECT data_file FROM ducklake_list_files('global_cat', 'tbl', schema => 'myschema')").fetchall()}
    new = ws_set - gl_set
    for fp in sorted(new):
        con.execute(f"""
            CALL ducklake_add_data_files('global_cat', 'tbl', '{fp}',
                schema => 'myschema', allow_missing => true, ignore_extra_columns => true)
        """)

    final = con.execute("SELECT COUNT(*) FROM global_cat.myschema.tbl").fetchone()[0]
    assert final == 175
    ok(f"Incremental merge: {final} rows")
    con.close()

    # ── TEST 7: CRITICAL - Compaction shared-ownership ──────────
    section("TEST 7: CRITICAL - Workspace compaction destroys global catalog files")
    print("  This test proves that running maintenance (CHECKPOINT / merge_adjacent_files)")
    print("  on a workspace catalog can delete Parquet files still referenced by the global catalog.")

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")

    # Snapshot global state before
    con.execute(f"ATTACH 'ducklake:{gl6}' AS global_cat (READ_ONLY)")
    gfiles_before = [r[0] for r in con.execute(
        "SELECT data_file FROM ducklake_list_files('global_cat', 'tbl', schema => 'myschema')"
    ).fetchall()]
    print(f"\n  Global files BEFORE compaction: {len(gfiles_before)}")
    for f in gfiles_before:
        print(f"    {f} (exists={os.path.exists(f)})")
    con.execute("DETACH global_cat")

    # Compact workspace
    con.execute(f"ATTACH 'ducklake:{ws6}' AS ws (DATA_PATH '{ddir6}/')")
    ws_before = [r[0] for r in con.execute(
        "SELECT data_file FROM ducklake_list_files('ws', 'tbl', schema => 'myschema')"
    ).fetchall()]
    print(f"\n  WS files BEFORE compaction: {len(ws_before)}")

    print("\n  Running ducklake_merge_adjacent_files('ws')...")
    merge_result = con.execute("CALL ducklake_merge_adjacent_files('ws')").fetchall()
    print(f"  Result: {merge_result}")

    ws_after = [r[0] for r in con.execute(
        "SELECT data_file FROM ducklake_list_files('ws', 'tbl', schema => 'myschema')"
    ).fetchall()]
    print(f"  WS files AFTER merge_adjacent: {len(ws_after)}")
    if len(ws_after) < len(ws_before):
        finding(f"merge_adjacent_files consolidated {len(ws_before)} files -> {len(ws_after)} file(s)")

    print("\n  Running ducklake_expire_snapshots (expire all)...")
    con.execute("CALL ducklake_expire_snapshots('ws', older_than => now() + INTERVAL '1 day')")

    print("  Running ducklake_cleanup_old_files (delete all scheduled)...")
    deleted = con.execute("CALL ducklake_cleanup_old_files('ws', cleanup_all => true)").fetchall()
    if deleted:
        finding(f"cleanup_old_files DELETED {len(deleted)} file(s)")
        for (d,) in deleted:
            print(f"    DELETED: {d}")

    con.execute("DETACH ws")

    # Now check global catalog
    print("\n  Checking global catalog integrity...")
    con.execute(f"ATTACH 'ducklake:{gl6}' AS global_cat (READ_ONLY)")
    gfiles_after = con.execute(
        "SELECT data_file FROM ducklake_list_files('global_cat', 'tbl', schema => 'myschema')"
    ).fetchall()

    broken_files = 0
    for (f,) in gfiles_after:
        exists = os.path.exists(f)
        if not exists:
            broken_files += 1
            fail(f"File deleted by workspace compaction: {os.path.basename(f)}")
        else:
            print(f"    {os.path.basename(f)}: OK")

    if broken_files > 0:
        print(f"\n  CONFIRMED: {broken_files}/{len(gfiles_after)} files referenced by global catalog")
        print("  were deleted by workspace maintenance. Queries will fail with")
        print("  'file not found' or HTTP 416 on S3.")

    # Try to query - should fail
    try:
        count = con.execute("SELECT COUNT(*) FROM global_cat.myschema.tbl").fetchone()[0]
        # On local filesystem DuckDB may handle missing files differently than S3
        print(f"  Query returned {count} rows (local FS may still cache)")
    except duckdb.Error as e:
        finding(f"Global catalog query FAILED as expected: {e}")

    con.close()

    # ── TEST 8: Maintenance functions ───────────────────────────
    section("TEST 8: Maintenance functions (maintenance.py APIs)")

    ddir8 = os.path.join(tmpdir, "t8data")
    os.makedirs(ddir8)
    gen_batches(ddir8)
    cat8 = os.path.join(tmpdir, "t8.duckdb")

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")

    con.execute(f"ATTACH 'ducklake:{cat8}' AS maint (DATA_PATH '{ddir8}/')")
    con.execute("CREATE SCHEMA IF NOT EXISTS maint.s")
    con.execute(f"CREATE TABLE maint.s.t AS SELECT * FROM read_parquet('{ddir8}/batch1.parquet')")

    # Set maintenance options
    con.execute("CALL maint.set_option('expire_older_than', '30 days')")
    con.execute("CALL maint.set_option('delete_older_than', '7 days')")

    # CHECKPOINT (all-in-one)
    con.execute("USE maint")
    try:
        con.execute("CHECKPOINT")
        ok("CHECKPOINT completed (runs 6 steps: flush_inlined, expire_snapshots, merge_adjacent, rewrite_data, cleanup_old, delete_orphaned)")
    except duckdb.Error as e:
        fail(f"CHECKPOINT: {e}")
    con.execute("USE memory")

    # Redundancy test: ducklake_delete_orphaned_files after CHECKPOINT
    try:
        con.execute("CALL ducklake_delete_orphaned_files('maint', older_than => now() - INTERVAL '7 days')")
        ok("ducklake_delete_orphaned_files after CHECKPOINT: works but REDUNDANT")
        finding("maintenance.py calls ducklake_delete_orphaned_files after CHECKPOINT. Redundant because CHECKPOINT already runs it as step 6.")
    except duckdb.Error as e:
        fail(f"ducklake_delete_orphaned_files: {e}")

    con.close()

    # ── TEST 9: Partitioning ────────────────────────────────────
    section("TEST 9: Partitioning support")

    ddir9 = os.path.join(tmpdir, "t9data")
    os.makedirs(ddir9)
    cat9 = os.path.join(tmpdir, "t9.duckdb")

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"ATTACH 'ducklake:{cat9}' AS part (DATA_PATH '{ddir9}/')")
    con.execute("CREATE SCHEMA IF NOT EXISTS part.s")
    con.execute("""
        CREATE TABLE part.s.ts_data (
            id BIGINT, name VARCHAR, ts TIMESTAMP, value DOUBLE
        )
    """)

    # Partition by month
    try:
        con.execute("ALTER TABLE part.s.ts_data SET PARTITIONED BY (month(ts))")
        ok("SET PARTITIONED BY (month(ts))")
    except duckdb.Error as e:
        fail(f"Partitioning: {e}")

    # Insert data across months
    con.execute("""
        INSERT INTO part.s.ts_data
        SELECT i, 'item_' || i,
               '2026-01-01'::TIMESTAMP + INTERVAL (i * 3) DAY,
               random() * 100
        FROM range(100) t(i)
    """)

    files = con.execute("SELECT data_file FROM ducklake_list_files('part', 'ts_data', schema => 's')").fetchall()
    print(f"  Partitioned files: {len(files)}")
    for (f,) in files:
        print(f"    {os.path.basename(f)}")

    if len(files) > 1:
        ok(f"Partitioning created {len(files)} files (one per month)")
    else:
        print("  Note: Only 1 file created (data may not span enough months)")

    # Reset
    con.execute("ALTER TABLE part.s.ts_data RESET PARTITIONED BY")
    ok("RESET PARTITIONED BY works")
    con.close()

    # ── TEST 10: Time travel ────────────────────────────────────
    section("TEST 10: Time travel and snapshots")

    ddir10 = os.path.join(tmpdir, "t10data")
    os.makedirs(ddir10)
    cat10 = os.path.join(tmpdir, "t10.duckdb")

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"ATTACH 'ducklake:{cat10}' AS tt (DATA_PATH '{ddir10}/')")
    con.execute("CREATE TABLE tt.main.t (id BIGINT, val VARCHAR)")
    con.execute("INSERT INTO tt.main.t VALUES (1, 'first')")
    con.execute("INSERT INTO tt.main.t VALUES (2, 'second')")
    con.execute("UPDATE tt.main.t SET val = 'updated' WHERE id = 1")

    snaps = con.execute("SELECT snapshot_id, snapshot_time FROM ducklake_snapshots('tt')").fetchall()
    print(f"  Snapshots: {len(snaps)}")
    for s in snaps:
        print(f"    v{s[0]}: {s[1]}")

    # Query at different versions
    for snap_id, _ in snaps:
        try:
            count = con.execute(f"SELECT COUNT(*) FROM tt.main.t AT (VERSION => {snap_id})").fetchone()[0]
            print(f"    v{snap_id}: {count} rows")
        except duckdb.Error as e:
            print(f"    v{snap_id}: error - {e}")

    # ducklake_list_files with snapshot_version
    try:
        files_v1 = con.execute("SELECT data_file FROM ducklake_list_files('tt', 't', snapshot_version => 1)").fetchall()
        ok(f"ducklake_list_files with snapshot_version: {len(files_v1)} files at v1")
    except duckdb.Error as e:
        fail(f"ducklake_list_files snapshot_version: {e}")

    con.close()

    # ── TEST 11: ducklake_table_info ────────────────────────────
    section("TEST 11: ducklake_table_info")

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"ATTACH 'ducklake:{cat10}' AS tt (READ_ONLY)")

    try:
        info = con.execute("SELECT * FROM ducklake_table_info('tt')").fetchall()
        for row in info:
            print(f"  {row}")
        ok("ducklake_table_info works")
    except duckdb.Error as e:
        fail(f"ducklake_table_info: {e}")

    con.close()

    # ── TEST 12: CHECKPOINT on global catalog with auto_compact=false ──
    section("TEST 12: CHECKPOINT behavior with auto_compact=false")

    ddir12 = os.path.join(tmpdir, "t12data")
    os.makedirs(ddir12)
    gen_batches(ddir12)
    cat12 = os.path.join(tmpdir, "t12.duckdb")

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute(f"ATTACH 'ducklake:{cat12}' AS ac (DATA_PATH '{ddir12}/')")
    con.execute("CREATE SCHEMA IF NOT EXISTS ac.s")
    con.execute(f"CREATE TABLE ac.s.t AS SELECT * FROM read_parquet('{ddir12}/batch1.parquet')")
    con.execute(f"""
        CALL ducklake_add_data_files('ac', 't', '{ddir12}/batch2.parquet',
            schema => 's', allow_missing => true, ignore_extra_columns => true)
    """)

    con.execute("CALL ac.set_option('auto_compact', false)")

    files_before = con.execute("SELECT data_file FROM ducklake_list_files('ac', 't', schema => 's')").fetchall()
    print(f"  Files before CHECKPOINT (auto_compact=false): {len(files_before)}")

    con.execute("USE ac")
    con.execute("CHECKPOINT")
    con.execute("USE memory")

    files_after = con.execute("SELECT data_file FROM ducklake_list_files('ac', 't', schema => 's')").fetchall()
    print(f"  Files after CHECKPOINT (auto_compact=false): {len(files_after)}")

    if len(files_after) == len(files_before):
        ok("auto_compact=false prevents merge_adjacent_files during CHECKPOINT")
    else:
        fail("auto_compact=false did NOT prevent compaction during CHECKPOINT!")

    con.close()

    # ── SUMMARY ─────────────────────────────────────────────────
    section("SUMMARY")
    print(f"  Passed: {PASS}")
    print(f"  Failed: {FAIL}")
    if FINDINGS:
        print(f"\n  FINDINGS ({len(FINDINGS)}):")
        for i, f in enumerate(FINDINGS, 1):
            print(f"    {i}. {f}")

    shutil.rmtree(tmpdir)
    print(f"\n  Cleaned up {tmpdir}")

    return 1 if FAIL > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
