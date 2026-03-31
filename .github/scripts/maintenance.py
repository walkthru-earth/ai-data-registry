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

Skips the global catalog (it has auto_compact = false).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.registry_config import get_storage_config


def s5cmd(*args: str) -> subprocess.CompletedProcess:
    """Run s5cmd with the configured endpoint URL."""
    endpoint = os.environ.get("S3_ENDPOINT_URL", "")
    cmd = ["pixi", "run", "s5cmd", "--endpoint-url", endpoint, *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def list_workspace_catalogs() -> list[str]:
    """List all workspace catalog files on S3."""
    storage = get_storage_config()
    bucket = os.environ.get("S3_BUCKET", "")
    prefix = f"s3://{bucket}/{storage['catalog_prefix']}/"
    global_name = storage["global_catalog"]

    result = s5cmd("ls", prefix)
    if result.returncode != 0:
        print(f"  WARNING: Could not list catalogs at {prefix}")
        return []

    catalogs = []
    for line in result.stdout.strip().splitlines():
        # s5cmd ls output: "2026/03/30 12:00:00   1234  s3://bucket/.catalogs/weather.ducklake"
        parts = line.strip().split()
        if not parts:
            continue
        s3_path = parts[-1]
        filename = s3_path.split("/")[-1]
        # Skip global catalog
        if filename == global_name:
            continue
        if filename.endswith(".ducklake"):
            catalogs.append(s3_path)

    return catalogs


def maintain_catalog(s3_path: str, local_dir: str, dry_run: bool = False) -> bool:
    """Run maintenance on a single workspace catalog."""
    filename = s3_path.split("/")[-1]
    ws_name = filename.replace(".ducklake", "")
    local_path = os.path.join(local_dir, filename)

    print(f"\n  Processing: {ws_name} ({filename})")

    # Download
    result = s5cmd("cp", s3_path, local_path)
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
    con.execute("INSTALL sqlite; LOAD sqlite;")

    # Configure S3 via CREATE SECRET so DuckLake internal operations
    # use the correct endpoint and credentials.
    endpoint = os.environ.get("S3_ENDPOINT_URL", "")
    access_key = os.environ.get("AWS_ACCESS_KEY_ID", "")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    region = os.environ.get("S3_REGION", "")

    if endpoint:
        from urllib.parse import urlparse
        parsed = urlparse(endpoint)
        s3_host = parsed.hostname or endpoint.replace("https://", "").replace("http://", "")

        con.execute(f"""
            CREATE SECRET registry_s3 (
                TYPE S3,
                KEY_ID '{access_key}',
                SECRET '{secret_key}',
                ENDPOINT '{s3_host}',
                URL_STYLE 'path',
                USE_SSL {str(parsed.scheme == 'https').lower()},
                REGION '{region or "auto"}'
            )
        """)

    try:
        con.execute(f"""
            ATTACH 'ducklake:sqlite:{local_path}' AS ws (
                AUTOMATIC_MIGRATION true
            )
        """)
    except duckdb.Error as e:
        print(f"    ERROR: Failed to attach catalog: {e}")
        con.close()
        return False

    try:
        # Set maintenance options
        con.execute("CALL ws.set_option('expire_older_than', '30 days')")
        con.execute("CALL ws.set_option('delete_older_than', '7 days')")

        # Run all-in-one CHECKPOINT (v0.4+)
        con.execute("USE ws")
        con.execute("CHECKPOINT")
        print(f"    CHECKPOINT completed.")

        # Also clean up orphaned files from crashed writes
        try:
            con.execute("CALL ducklake_delete_orphaned_files('ws', older_than => now() - INTERVAL '7 days')")
            print(f"    Orphan cleanup completed.")
        except duckdb.Error:
            pass  # Function may not exist in older versions

    except duckdb.Error as e:
        print(f"    WARNING: Maintenance failed: {e}")
        con.close()
        return False

    con.close()

    # Upload updated catalog
    result = s5cmd("cp", local_path, s3_path)
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

    catalogs = list_workspace_catalogs()
    if not catalogs:
        print("No workspace catalogs found on S3.")
        return

    print(f"Found {len(catalogs)} workspace catalog(s).")

    succeeded = 0
    failed = 0

    def run_maintenance(catalog_dir: str):
        nonlocal succeeded, failed
        for s3_path in catalogs:
            if maintain_catalog(s3_path, catalog_dir, dry_run=args.dry_run):
                succeeded += 1
            else:
                failed += 1

    if args.catalog_dir:
        os.makedirs(args.catalog_dir, exist_ok=True)
        run_maintenance(args.catalog_dir)
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_maintenance(tmpdir)

    print(f"\nMaintenance complete: {succeeded} succeeded, {failed} failed.")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
