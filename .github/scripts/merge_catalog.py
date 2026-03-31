# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "duckdb>=1.5.1",
# ]
# ///
"""Merge workspace catalogs into the global DuckLake catalog.

Usage: uv run merge_catalog.py --workspace <name> [--catalog-dir <path>]

Downloads the workspace catalog and global catalog from S3, diffs their
file lists, and registers only NEW files in the global catalog via
ducklake_add_data_files(). Uploads the updated global catalog back to S3.

Runs with concurrency: 1 to prevent concurrent writes to the global catalog.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.registry_config import (
    WORKSPACES_DIR,
    get_storage_config,
    parse_workspace_registry,
)


def s5cmd(*args: str) -> subprocess.CompletedProcess:
    """Run s5cmd with the configured endpoint URL."""
    endpoint = os.environ.get("S3_ENDPOINT_URL", "")
    cmd = ["pixi", "run", "s5cmd", "--endpoint-url", endpoint, *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


def download_catalog(s3_path: str, local_path: str) -> bool:
    """Download a catalog file from S3. Returns True on success."""
    result = s5cmd("cp", s3_path, local_path)
    return result.returncode == 0


def upload_catalog(local_path: str, s3_path: str) -> bool:
    """Upload a catalog file to S3. Returns True on success."""
    result = s5cmd("cp", local_path, s3_path)
    return result.returncode == 0


def get_new_files(con, ws_catalog: str, global_catalog: str, schema: str, table: str) -> list[str]:
    """Find files in workspace catalog not yet in global catalog."""
    try:
        ws_files = con.execute(f"""
            SELECT data_file
            FROM ducklake_list_files('{ws_catalog}', '{table}', schema => '{schema}')
        """).fetchall()
    except Exception:
        print(f"  Table {schema}.{table} not found in workspace catalog.")
        return []

    try:
        global_files = con.execute(f"""
            SELECT data_file
            FROM ducklake_list_files('{global_catalog}', '{table}', schema => '{schema}')
        """).fetchall()
    except Exception:
        # Table doesn't exist in global catalog yet, all files are new
        global_files = []

    global_set = {f[0] for f in global_files}
    return [f[0] for f in ws_files if f[0] not in global_set]


def merge_workspace(workspace: str, catalog_dir: str) -> bool:
    """Merge a single workspace's catalog into the global catalog."""
    storage = get_storage_config()
    bucket = os.environ.get("S3_BUCKET", "")

    # Parse workspace config
    ws_pixi = WORKSPACES_DIR / workspace / "pixi.toml"
    registry = parse_workspace_registry(ws_pixi)
    if not registry:
        print(f"  ERROR: No [tool.registry] found for workspace '{workspace}'.")
        return False

    schema = registry.get("schema", workspace)
    table = registry.get("table", "")
    if not table:
        print(f"  ERROR: No table defined for workspace '{workspace}'.")
        return False

    # Download catalogs
    ws_catalog_s3 = f"s3://{bucket}/{storage['catalog_prefix']}/{workspace}.ducklake"
    global_catalog_s3 = f"s3://{bucket}/{storage['global_catalog']}"

    ws_catalog_local = os.path.join(catalog_dir, f"{workspace}.ducklake")
    global_catalog_local = os.path.join(catalog_dir, "catalog.ducklake")

    print(f"Downloading workspace catalog: {ws_catalog_s3}")
    if not download_catalog(ws_catalog_s3, ws_catalog_local):
        print(f"  ERROR: Failed to download workspace catalog for '{workspace}'.")
        return False

    print(f"Downloading global catalog: {global_catalog_s3}")
    if not download_catalog(global_catalog_s3, global_catalog_local):
        print(f"  INFO: No global catalog found. Will create a new one.")
        # Create an empty global catalog
        global_catalog_local = os.path.join(catalog_dir, "catalog.ducklake")

    # Merge using DuckDB
    try:
        import duckdb
    except ImportError:
        print("  ERROR: duckdb Python package not available.")
        return False

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute("INSTALL sqlite; LOAD sqlite;")

    # Configure S3 access for DuckLake data paths
    endpoint = os.environ.get("S3_ENDPOINT_URL", "")
    access_key = os.environ.get("AWS_ACCESS_KEY_ID", "")
    secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "")

    if endpoint:
        # Extract just the hostname for DuckDB s3_endpoint
        from urllib.parse import urlparse
        parsed = urlparse(endpoint)
        s3_host = parsed.hostname or endpoint.replace("https://", "").replace("http://", "")
        con.execute("SET s3_endpoint = ?", [s3_host])
        if parsed.scheme == "https":
            con.execute("SET s3_use_ssl = true")
    if access_key:
        con.execute("SET s3_access_key_id = ?", [access_key])
    if secret_key:
        con.execute("SET s3_secret_access_key = ?", [secret_key])

    # Attach workspace catalog (read-only)
    try:
        con.execute(f"""
            ATTACH 'ducklake:sqlite:{ws_catalog_local}' AS ws (READ_ONLY)
        """)
    except duckdb.Error as e:
        print(f"  ERROR: Failed to attach workspace catalog: {e}")
        con.close()
        return False

    # Attach global catalog (read-write, create if not exists)
    data_path = f"s3://{bucket}/"
    try:
        con.execute(f"""
            ATTACH 'ducklake:sqlite:{global_catalog_local}' AS global_cat (
                DATA_PATH '{data_path}'
            )
        """)
    except duckdb.Error as e:
        print(f"  ERROR: Failed to attach global catalog: {e}")
        con.close()
        return False

    # Disable auto_compact on global catalog to prevent file deletion
    try:
        con.execute("CALL global_cat.set_option('auto_compact', false)")
    except duckdb.Error:
        pass  # Option may already be set or not supported in this version

    # Ensure the table exists in global catalog
    try:
        con.execute(f'SELECT 1 FROM global_cat."{schema}"."{table}" LIMIT 0')
    except duckdb.Error:
        # Table doesn't exist, create it from workspace schema
        print(f"  Creating table {schema}.{table} in global catalog...")
        try:
            con.execute(f'CREATE SCHEMA IF NOT EXISTS global_cat."{schema}"')
            # Get CREATE TABLE from workspace
            cols = con.execute(f"""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_catalog = 'ws' AND table_schema = '{schema}' AND table_name = '{table}'
            """).fetchall()

            if not cols:
                print(f"  ERROR: Table {schema}.{table} not found in workspace catalog.")
                con.close()
                return False

            col_defs = ", ".join(f'"{name}" {dtype}' for name, dtype in cols)
            con.execute(f'CREATE TABLE global_cat."{schema}"."{table}" ({col_defs})')
        except duckdb.Error as e:
            print(f"  ERROR: Failed to create table in global catalog: {e}")
            con.close()
            return False

    # Diff file lists and register new files
    new_files = get_new_files(con, "ws", "global_cat", schema, table)

    if not new_files:
        print(f"  No new files to register for {schema}.{table}.")
        con.close()
        return True

    print(f"  Registering {len(new_files)} new file(s) for {schema}.{table}...")
    registered = 0
    for file_path in new_files:
        try:
            con.execute(f"""
                CALL ducklake_add_data_files('global_cat', '{table}', '{file_path}',
                    schema => '{schema}',
                    allow_missing => true,
                    ignore_extra_columns => true
                )
            """)
            registered += 1
        except duckdb.Error as e:
            print(f"  WARNING: Failed to register {file_path}: {e}")

    con.close()

    print(f"  Registered {registered}/{len(new_files)} files.")

    # Upload updated global catalog back to S3
    print(f"Uploading global catalog: {global_catalog_s3}")
    if not upload_catalog(global_catalog_local, global_catalog_s3):
        print(f"  ERROR: Failed to upload global catalog.")
        return False

    print(f"  Merge complete for workspace '{workspace}'.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Merge workspace catalog into global catalog")
    parser.add_argument("--workspace", required=True, help="Workspace name to merge")
    parser.add_argument("--catalog-dir", help="Directory for catalog files (default: temp dir)")
    args = parser.parse_args()

    if args.catalog_dir:
        catalog_dir = args.catalog_dir
        os.makedirs(catalog_dir, exist_ok=True)
        success = merge_workspace(args.workspace, catalog_dir)
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            success = merge_workspace(args.workspace, tmpdir)

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
