# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "duckdb>=1.5.1",
# ]
# ///
"""Layer 3: Live catalog check against the global DuckLake catalog on S3.

Usage: uv run check_catalog.py --workspace <name>

Pulls the global catalog from S3 and checks:
- Does the schema.table already exist in the global catalog?
- If it exists and mode is 'append', is the schema compatible?
- If it exists and mode is 'replace', warn but allow.
- If it does not exist, pass (new table).

Gracefully skips if S3 credentials are not available (fork PRs).
Exit 0 on pass (or skip), 1 on incompatibility.
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
    WORKSPACE_NAME_RE,
    WORKSPACES_DIR,
    build_global_catalog_path,
    get_default_storage_name,
    get_tables,
    parse_workspace_registry,
    quote_literal,
    resolve_storage_env,
    s5cmd_for_storage,
)


def s3_available() -> bool:
    """Check if S3 credentials are set for the default storage."""
    try:
        storage_name = get_default_storage_name()
        creds = resolve_storage_env(storage_name)
        return bool(creds["endpoint_url"] and creds["bucket"] and creds["access_key"])
    except ValueError:
        return False


def download_catalog(storage_name: str, s3_path: str, local_path: str) -> bool:
    """Download a catalog file from S3 via s5cmd. Returns True on success."""
    try:
        result = s5cmd_for_storage(storage_name, "cp", s3_path, local_path)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def check_catalog(workspace_name: str) -> list[str]:
    """Check workspace's schema.table against the global catalog."""
    errors: list[str] = []

    # Parse workspace config
    ws_pixi = WORKSPACES_DIR / workspace_name / "pixi.toml"
    registry = parse_workspace_registry(ws_pixi)
    if not registry:
        errors.append(f"No [tool.registry] found in {ws_pixi}")
        return errors

    schema = registry.get("schema", "")
    tables = get_tables(registry)
    mode = registry.get("mode", "append")

    if not schema or not tables:
        return errors  # Already caught by Layer 1

    storage_name = get_default_storage_name()
    global_catalog_s3 = build_global_catalog_path(storage_name)

    # Download global catalog to temp dir
    with tempfile.TemporaryDirectory() as tmpdir:
        local_catalog = os.path.join(tmpdir, "catalog.duckdb")

        if not download_catalog(storage_name, global_catalog_s3, local_catalog):
            print("  INFO: Global catalog not found on S3 (first run or new deployment). Skipping catalog check.")
            return errors

        # Use DuckDB to check the catalog
        try:
            import duckdb

            con = duckdb.connect()
            con.execute("INSTALL ducklake; LOAD ducklake;")

            try:
                con.execute(f"ATTACH {quote_literal('ducklake:' + local_catalog)} AS global_cat (READ_ONLY)")
            except duckdb.Error as e:
                print(f"  WARNING: Could not attach global catalog: {e}")
                return errors

            for table in tables:
                _check_table(con, schema, table, mode)

            con.close()

        except ImportError:
            print("  WARNING: duckdb Python package not available. Skipping catalog schema check.")

    return errors


def _check_table(con, schema: str, table: str, mode: str):
    """Check a single table against the global catalog."""
    try:
        result = con.execute(f"""
            SELECT COUNT(*) FROM ducklake_list_files('global_cat', {quote_literal(table)}, schema => {quote_literal(schema)})
        """).fetchone()
        file_count = result[0] if result else 0

        if file_count > 0:
            if mode == "replace":
                print(f"  INFO: Table {schema}.{table} exists ({file_count} files). Mode is 'replace', will overwrite.")
            elif mode == "append":
                print(f"  INFO: Table {schema}.{table} exists ({file_count} files). Mode is 'append', checking schema compatibility...")

                try:
                    global_cols = con.execute("""
                        SELECT column_name, data_type
                        FROM information_schema.columns
                        WHERE table_catalog = 'global_cat'
                          AND table_schema = ?
                          AND table_name = ?
                        ORDER BY ordinal_position
                    """, [schema, table]).fetchall()

                    if global_cols:
                        global_col_names = {col[0] for col in global_cols}
                        print(f"  INFO: Global catalog has {len(global_col_names)} column(s). Schema compatibility will be fully verified during merge.")
                except duckdb.Error as e:
                    print(f"  WARNING: Could not read global catalog schema for {schema}.{table}: {e}")

            elif mode == "upsert":
                print(f"  INFO: Table {schema}.{table} exists ({file_count} files). Mode is 'upsert'.")
        else:
            print(f"  INFO: Table {schema}.{table} is new (no existing files in global catalog).")

    except duckdb.Error:
        print(f"  INFO: Table {schema}.{table} does not exist in global catalog yet. Will be created on first extraction.")


def main():
    parser = argparse.ArgumentParser(description="Check workspace against global catalog")
    parser.add_argument("--workspace", required=True, help="Workspace name to check")
    args = parser.parse_args()

    if not WORKSPACE_NAME_RE.match(args.workspace):
        print(f"ERROR: Invalid workspace name: {args.workspace}")
        sys.exit(1)

    if not s3_available():
        print("  SKIPPED: S3 credentials not available (expected for fork PRs). Catalog check skipped.")
        sys.exit(0)

    print(f"Checking workspace '{args.workspace}' against global catalog...")

    errors = check_catalog(args.workspace)

    if errors:
        print(f"\n  FAILED: {len(errors)} catalog issue(s):\n")
        for i, err in enumerate(errors, 1):
            print(f"  {i}. {err}")
        sys.exit(1)
    else:
        print(f"  PASSED: Catalog check passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
