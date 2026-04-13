# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "duckdb>=1.5.2",
# ]
# ///
"""Weekly maintenance for the global DuckLake catalog.

Usage: uv run maintenance.py [--catalog-dir <path>] [--dry-run] [--storage <name>]

Downloads the global catalog from S3, runs DuckLake CHECKPOINT
(flush inlined data, expire snapshots, merge adjacent files,
rewrite data files, cleanup old files, delete orphans),
then uploads the updated catalog back to S3.

Iterates over all defined storages unless --storage is specified.

CRITICAL: Catalog files use the DuckDB backend (.duckdb), NOT SQLite (.ducklake).
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.registry_config import (
    build_global_catalog_path,
    build_s3_root,
    load_storage_configs,
    quote_literal,
    resolve_storage_env,
    s5cmd_for_storage,
)


def maintain_global_catalog(
    storage_name: str, local_dir: str, dry_run: bool = False
) -> bool:
    """Run maintenance on the global catalog for a storage target."""
    global_s3 = build_global_catalog_path(storage_name)
    local_path = os.path.join(local_dir, f"{storage_name}_catalog.duckdb")

    print(f"\n  [{storage_name}] Downloading global catalog: {global_s3}")

    result = s5cmd_for_storage(storage_name, "cp", global_s3, local_path)
    if result.returncode != 0:
        print(f"    WARNING: No global catalog found at {global_s3}, skipping.")
        if result.stderr:
            print(f"    s5cmd stderr: {result.stderr.strip()}")
        return True

    if dry_run:
        print(f"    DRY RUN: Would run CHECKPOINT on global catalog")
        return True

    try:
        import duckdb
    except ImportError:
        print("    ERROR: duckdb Python package not available.")
        return False

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")

    # Configure S3 via CREATE SECRET for DuckLake operations
    creds = resolve_storage_env(storage_name)
    endpoint = creds["endpoint_url"] or ""
    data_path = build_s3_root(storage_name)

    if endpoint:
        parsed = urlparse(endpoint)
        s3_host = parsed.hostname or endpoint.replace("https://", "").replace("http://", "")

        access_key = creds["access_key"] or ""
        secret_key = creds["secret_key"] or ""
        region = creds["region"] or "auto"
        con.execute(f"""
            CREATE SECRET registry_s3 (
                TYPE S3,
                KEY_ID {quote_literal(access_key)},
                SECRET {quote_literal(secret_key)},
                ENDPOINT {quote_literal(s3_host)},
                URL_STYLE 'path',
                USE_SSL {str(parsed.scheme == 'https').lower()},
                REGION {quote_literal(region)}
            )
        """)

    try:
        con.execute(f"""
            ATTACH {quote_literal('ducklake:' + local_path)} AS global_cat (
                DATA_PATH {quote_literal(data_path)},
                AUTOMATIC_MIGRATION true
            )
        """)
    except duckdb.Error as e:
        print(f"    ERROR: Failed to attach global catalog: {e}")
        con.close()
        return False

    try:
        # Global catalog is the sole owner of all data files, so compaction
        # (merge_adjacent_files, rewrite_data_files) is safe.
        # auto_compact must be true for CHECKPOINT to run all 6 steps,
        # including ducklake_delete_orphaned_files (cleans up replace-mode leftovers).
        con.execute("CALL global_cat.set_option('auto_compact', true)")
        con.execute("CALL global_cat.set_option('expire_older_than', '30 days')")
        con.execute("CALL global_cat.set_option('delete_older_than', '7 days')")

        # Log pre-CHECKPOINT state
        snap_count = con.execute("SELECT COUNT(*) FROM ducklake_snapshots('global_cat')").fetchone()[0]
        table_info = con.execute("SELECT table_name, file_count, file_size_bytes FROM ducklake_table_info('global_cat')").fetchall()
        print(f"    Pre-CHECKPOINT: {snap_count} snapshots")
        for name, files, size_bytes in table_info:
            print(f"      {name}: {files} files, {size_bytes / 1024 / 1024:.1f} MB")

        con.execute("USE global_cat")
        con.execute("CHECKPOINT")

        # Log post-CHECKPOINT state
        snap_count_after = con.execute("SELECT COUNT(*) FROM ducklake_snapshots('global_cat')").fetchone()[0]
        table_info_after = con.execute("SELECT table_name, file_count, file_size_bytes FROM ducklake_table_info('global_cat')").fetchall()
        print(f"    Post-CHECKPOINT: {snap_count_after} snapshots (expired {snap_count - snap_count_after})")
        for name, files, size_bytes in table_info_after:
            print(f"      {name}: {files} files, {size_bytes / 1024 / 1024:.1f} MB")
        print(f"    CHECKPOINT completed (all 6 steps, sole file owner).")

    except duckdb.Error as e:
        print(f"    WARNING: Maintenance failed: {e}")
        con.close()
        return False

    con.close()

    # Upload updated catalog
    result = s5cmd_for_storage(storage_name, "cp", local_path, global_s3)
    if result.returncode != 0:
        print(f"    ERROR: Failed to upload updated catalog.")
        return False

    print(f"    Upload completed.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Weekly DuckLake catalog maintenance")
    parser.add_argument("--catalog-dir", help="Directory for catalog files (default: temp dir)")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    parser.add_argument("--storage", default=None, help="Specific storage target (default: all)")
    args = parser.parse_args()

    print("Starting global catalog maintenance...")

    storages = load_storage_configs()
    total_succeeded = 0
    total_failed = 0

    for storage_name in storages:
        if args.storage and storage_name != args.storage:
            continue

        print(f"\n=== Storage: {storage_name} ===")

        if args.catalog_dir:
            os.makedirs(args.catalog_dir, exist_ok=True)
            ok = maintain_global_catalog(storage_name, args.catalog_dir, dry_run=args.dry_run)
        else:
            with tempfile.TemporaryDirectory() as tmpdir:
                ok = maintain_global_catalog(storage_name, tmpdir, dry_run=args.dry_run)

        if ok:
            total_succeeded += 1
        else:
            total_failed += 1

    print(f"\nMaintenance complete: {total_succeeded} succeeded, {total_failed} failed.")

    if total_failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
