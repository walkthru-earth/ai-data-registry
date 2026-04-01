# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "duckdb>=1.5.1",
# ]
# ///
"""Weekly maintenance for workspace DuckLake catalogs.

Usage: uv run maintenance.py [--catalog-dir <path>] [--dry-run]

Downloads each workspace catalog from S3, runs DuckLake CHECKPOINT
(expire snapshots, merge adjacent files, cleanup old files, delete orphans),
then uploads the updated catalog back to S3.

Iterates over all defined storages. Skips the global catalog (it has auto_compact = false).

CRITICAL: Catalog files use the DuckDB backend (.duckdb), NOT SQLite (.ducklake).
DuckDB catalogs support remote S3/HTTPS read-only access via httpfs.
SQLite catalogs do NOT support remote access (blocked by duckdb/ducklake#912).
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
    build_s3_root,
    load_storage_configs,
    quote_literal,
    resolve_storage_env,
    s5cmd_for_storage,
)


def list_workspace_catalogs(storage_name: str) -> list[str]:
    """List all workspace catalog files on S3 for a storage target."""
    storages = load_storage_configs()
    cfg = storages[storage_name]
    root = build_s3_root(storage_name)
    prefix = f"{root}{cfg['catalog_prefix']}/"
    global_name = cfg["global_catalog"]

    result = s5cmd_for_storage(storage_name, "ls", prefix)
    if result.returncode != 0:
        print(f"  WARNING: Could not list catalogs at {prefix}")
        return []

    catalogs = []
    for line in result.stdout.strip().splitlines():
        # s5cmd ls output: "2026/03/30 12:00:00   1234  s3://bucket/owner/repo/branch/.catalogs/weather.duckdb"
        parts = line.strip().split()
        if not parts:
            continue
        s3_path = parts[-1]
        filename = s3_path.split("/")[-1]
        # Skip global catalog
        if filename == global_name:
            continue
        if filename.endswith(".duckdb"):
            catalogs.append(s3_path)

    return catalogs


def maintain_catalog(
    storage_name: str, s3_path: str, local_dir: str, dry_run: bool = False
) -> bool:
    """Run maintenance on a single workspace catalog."""
    filename = s3_path.split("/")[-1]
    ws_name = filename.replace(".duckdb", "")
    local_path = os.path.join(local_dir, f"{storage_name}_{filename}")

    print(f"\n  [{storage_name}] Processing: {ws_name} ({filename})")

    # Download
    result = s5cmd_for_storage(storage_name, "cp", s3_path, local_path)
    if result.returncode != 0:
        print(f"    ERROR: Failed to download {s3_path}")
        return False

    if dry_run:
        print(f"    DRY RUN: Would run CHECKPOINT on {filename}")
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
            ATTACH {quote_literal('ducklake:' + local_path)} AS ws (
                AUTOMATIC_MIGRATION true
            )
        """)
    except duckdb.Error as e:
        print(f"    ERROR: Failed to attach catalog: {e}")
        con.close()
        return False

    try:
        # Disable compaction to protect files shared with the global catalog.
        # CHECKPOINT with auto_compact=false skips merge_adjacent_files and
        # rewrite_data_files, but still runs expire_snapshots, cleanup_old_files,
        # and delete_orphaned_files. Without this, compaction consolidates files
        # into new ones and schedules the originals for deletion. The global
        # catalog still references those originals via zero-copy merge, so
        # deleting them breaks global catalog queries.
        con.execute("CALL ws.set_option('auto_compact', false)")
        con.execute("CALL ws.set_option('expire_older_than', '30 days')")
        con.execute("CALL ws.set_option('delete_older_than', '7 days')")

        # Run CHECKPOINT (v0.4+). With auto_compact=false this runs:
        # expire_snapshots, cleanup_old_files, delete_orphaned_files
        con.execute("USE ws")
        con.execute("CHECKPOINT")
        print(f"    CHECKPOINT completed (auto_compact=false, shared files protected).")

    except duckdb.Error as e:
        print(f"    WARNING: Maintenance failed: {e}")
        con.close()
        return False

    con.close()

    # Upload updated catalog
    result = s5cmd_for_storage(storage_name, "cp", local_path, s3_path)
    if result.returncode != 0:
        print(f"    ERROR: Failed to upload updated catalog.")
        return False

    print(f"    Upload completed.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Weekly DuckLake catalog maintenance")
    parser.add_argument("--catalog-dir", help="Directory for catalog files (default: temp dir)")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    args = parser.parse_args()

    print("Starting workspace catalog maintenance...")

    storages = load_storage_configs()
    total_succeeded = 0
    total_failed = 0

    def run_maintenance(sn: str, catalog_list: list[str], catalog_dir: str):
        nonlocal total_succeeded, total_failed
        for s3_path in catalog_list:
            if maintain_catalog(sn, s3_path, catalog_dir, dry_run=args.dry_run):
                total_succeeded += 1
            else:
                total_failed += 1

    for storage_name in storages:
        print(f"\n=== Storage: {storage_name} ===")

        catalogs = list_workspace_catalogs(storage_name)
        if not catalogs:
            print(f"No workspace catalogs found for storage '{storage_name}'.")
            continue

        print(f"Found {len(catalogs)} workspace catalog(s).")

        if args.catalog_dir:
            os.makedirs(args.catalog_dir, exist_ok=True)
            run_maintenance(storage_name, catalogs, args.catalog_dir)
        else:
            with tempfile.TemporaryDirectory() as tmpdir:
                run_maintenance(storage_name, catalogs, tmpdir)

    print(f"\nMaintenance complete: {total_succeeded} succeeded, {total_failed} failed.")

    if total_failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
