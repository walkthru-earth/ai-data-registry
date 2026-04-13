# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "duckdb>=1.5.2",
# ]
# ///
"""Merge workspace data into the global DuckLake catalog.

Usage:
    uv run merge_catalog.py --workspace <name> [--catalog-dir <path>] [--storage <name>]
    uv run merge_catalog.py --all [--catalog-dir <path>] [--storage <name>]

For each table declared in the workspace pixi.toml:
  1. Scans S3 for Parquet files under s3://bucket/{owner}/{repo}/{branch}/{schema}/{table}/
  2. Diffs S3 files against the global catalog
  3. Registers new files in the global catalog (mode-dependent):
     - append: registers all unregistered files
     - replace: drops old registrations, keeps only the latest file

Modes:
  --workspace <name>  Merge a single workspace (backward compatible)
  --all               Discover all workspaces, group by storage target, merge all
                      pending. Downloads/uploads the global catalog once per storage
                      instead of once per workspace. Idempotent: exits fast when
                      nothing is pending.

Supports multi-storage: runs merge independently for each target storage.
Pass --storage to merge a specific storage, or omit to merge all workspace storages.

Triggered by workflow_run (after any extract completes) and a 10-minute cron
backstop. The concurrency group serializes runs so only one merge executes at
a time.

CRITICAL: Catalog files use the DuckDB backend (.duckdb), NOT SQLite (.ducklake).
DuckDB catalogs support remote S3/HTTPS read-only access via httpfs, enabling
direct querying without downloading. SQLite catalogs do NOT support remote access
(blocked by duckdb/ducklake#912).
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
    WORKSPACE_NAME_RE,
    WORKSPACES_DIR,
    build_global_catalog_path,
    build_s3_root,
    discover_workspaces,
    get_tables,
    get_workspace_storages,
    load_storage_configs,
    parse_workspace_registry,
    quote_ident,
    quote_literal,
    resolve_storage_env,
    s5cmd_for_storage,
)


def download_catalog(storage_name: str, s3_path: str, local_path: str) -> bool:
    """Download a catalog file from S3. Returns True on success."""
    result = s5cmd_for_storage(storage_name, "cp", s3_path, local_path)
    return result.returncode == 0


def upload_catalog(storage_name: str, local_path: str, s3_path: str) -> bool:
    """Upload a catalog file to S3. Returns True on success."""
    result = s5cmd_for_storage(storage_name, "cp", local_path, s3_path)
    return result.returncode == 0


def create_s3_secret(con, storage_name: str):
    """Configure S3 credentials via CREATE SECRET for DuckLake operations."""
    creds = resolve_storage_env(storage_name)
    endpoint = creds["endpoint_url"]
    if not endpoint:
        return
    parsed = urlparse(endpoint)
    s3_host = parsed.hostname or endpoint.replace("https://", "").replace("http://", "")
    access_key = creds["access_key"] or ""
    secret_key = creds["secret_key"] or ""
    region = creds["region"] or "auto"
    con.execute(f"""
        CREATE OR REPLACE SECRET registry_s3 (
            TYPE S3,
            KEY_ID {quote_literal(access_key)},
            SECRET {quote_literal(secret_key)},
            ENDPOINT {quote_literal(s3_host)},
            URL_STYLE 'path',
            USE_SSL {str(parsed.scheme == 'https').lower()},
            REGION {quote_literal(region)}
        )
    """)


def list_registered_files(con, catalog: str, schema: str, table: str) -> set[str]:
    """Get set of file paths already registered in a DuckLake catalog table."""
    try:
        rows = con.execute(f"""
            SELECT data_file
            FROM ducklake_list_files({quote_literal(catalog)}, {quote_literal(table)}, schema => {quote_literal(schema)})
        """).fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


def scan_s3_files(con, data_path: str, schema: str, table: str) -> list[str]:
    """Discover Parquet files on S3 for a given table.

    Looks for: s3://bucket/{prefix}/{schema}/{table}/*.parquet
    """
    found = []

    table_glob = f"{data_path}{schema}/{table}/*.parquet"
    try:
        rows = con.execute(f"SELECT file FROM glob({quote_literal(table_glob)})").fetchall()
        found.extend(r[0] for r in rows)
    except Exception:
        pass

    return found


def _get_table_columns(con, catalog: str, schema: str, table: str) -> list[tuple[str, str]]:
    """Return (column_name, data_type) pairs for a table in a DuckLake catalog."""
    return con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_catalog = ? AND table_schema = ? AND table_name = ?",
        [catalog, schema, table],
    ).fetchall()


def _recreate_table(con, catalog: str, schema: str, table: str, col_defs: str):
    """Drop and recreate a DuckLake table with the given column definitions."""
    con.execute(f'DROP TABLE {catalog}.{quote_ident(schema)}.{quote_ident(table)}')
    con.execute(f'CREATE TABLE {catalog}.{quote_ident(schema)}.{quote_ident(table)} ({col_defs})')


def merge_table(con, data_path: str, schema: str, table: str, mode: str = "append") -> int:
    """Scan S3 for files and register them in the global catalog.

    For mode=replace, only the latest file is kept.
    For mode=append, all unregistered files are added.

    Returns the number of newly registered files.
    """
    s3_files = scan_s3_files(con, data_path, schema, table)
    if not s3_files:
        print(f"  No S3 files found for {schema}.{table}")
        return 0

    # Ensure table exists in global catalog
    table_exists = True
    try:
        con.execute(f'SELECT 1 FROM global_cat.{quote_ident(schema)}.{quote_ident(table)} LIMIT 0')
    except Exception:
        table_exists = False
        print(f"  Creating {schema}.{table} in global catalog...")
        con.execute(f'CREATE SCHEMA IF NOT EXISTS global_cat.{quote_ident(schema)}')
        created = False
        for candidate in sorted(s3_files, reverse=True):
            try:
                con.execute(f"""
                    CREATE TABLE global_cat.{quote_ident(schema)}.{quote_ident(table)} AS
                    SELECT * FROM read_parquet({quote_literal(candidate)}) LIMIT 0
                """)
                created = True
                break
            except Exception as e:
                print(f"  WARNING: Cannot read {candidate}, trying next file: {e}")
        if not created:
            print(f"  ERROR: No readable files for {schema}.{table}, skipping")
            return 0

    registered = list_registered_files(con, "global_cat", schema, table)

    # --- Replace mode: keep only the latest file ---
    if mode == "replace":
        latest_file = sorted(s3_files)[-1]
        # Normalize to check if already registered
        latest_rel = latest_file.removeprefix(data_path) if latest_file.startswith(data_path) else latest_file
        already_current = (
            (len(registered) == 1)
            and (latest_rel in registered or latest_file in registered)
        )
        if already_current:
            print(f"  {schema}.{table}: replace mode, latest file already registered")
            return 0

        # Drop and recreate to clear old file registrations
        if table_exists and registered:
            cols = _get_table_columns(con, "global_cat", schema, table)
            col_defs = ", ".join(f'{quote_ident(name)} {dtype}' for name, dtype in cols)
            _recreate_table(con, "global_cat", schema, table, col_defs)
            print(f"  {schema}.{table}: replace mode, cleared {len(registered)} old file(s)")

        try:
            con.execute(f"""
                CALL ducklake_add_data_files('global_cat', {quote_literal(table)}, {quote_literal(latest_file)},
                    schema => {quote_literal(schema)},
                    allow_missing => true,
                    ignore_extra_columns => true
                )
            """)
        except Exception as e:
            print(f"  WARNING: Failed to register {latest_file}: {e}")
            return 0

        total = con.execute(f'SELECT COUNT(*) FROM global_cat.{quote_ident(schema)}.{quote_ident(table)}').fetchone()[0]
        print(f"  {schema}.{table}: replace mode, registered latest file ({total} rows)")
        return 1

    # --- Append mode (default): register all unregistered files ---
    new_files = []
    for full_path in s3_files:
        rel_path = full_path.removeprefix(data_path) if full_path.startswith(data_path) else full_path
        if rel_path not in registered and full_path not in registered:
            new_files.append(full_path)

    if not new_files:
        print(f"  {schema}.{table}: {len(s3_files)} file(s) on S3, all registered")
        return 0

    count = 0
    for file_path in new_files:
        try:
            con.execute(f"""
                CALL ducklake_add_data_files('global_cat', {quote_literal(table)}, {quote_literal(file_path)},
                    schema => {quote_literal(schema)},
                    allow_missing => true,
                    ignore_extra_columns => true
                )
            """)
            count += 1
        except Exception as e:
            print(f"  WARNING: Failed to register {file_path} in global catalog: {e}")

    total = con.execute(f'SELECT COUNT(*) FROM global_cat.{quote_ident(schema)}.{quote_ident(table)}').fetchone()[0]
    print(f"  {schema}.{table}: registered {count} new file(s), {total} total rows")
    return count


def merge_workspace_storage(
    workspace: str,
    storage_name: str,
    schema: str,
    tables: list[str],
    catalog_dir: str,
    *,
    mode: str = "append",
    global_catalog_local: str | None = None,
    skip_global_upload: bool = False,
) -> tuple[bool, bool]:
    """Merge a workspace's S3 data into the global catalog for a single storage target.

    Args:
        global_catalog_local: Pre-downloaded global catalog path. If provided,
            skips downloading the global catalog (used by --all mode to share
            one global catalog file across workspaces).
        skip_global_upload: If True, skip uploading the global catalog after
            merge (caller handles upload in --all mode).

    Returns:
        (success, global_changed) tuple.
    """
    print(f"\n=== Storage: {storage_name} ===")

    global_catalog_s3 = build_global_catalog_path(storage_name)
    data_path = build_s3_root(storage_name)

    # Use storage-specific subdirectory to avoid conflicts between storages
    storage_catalog_dir = os.path.join(catalog_dir, storage_name)
    os.makedirs(storage_catalog_dir, exist_ok=True)
    _global_catalog_local = global_catalog_local or os.path.join(storage_catalog_dir, "catalog.duckdb")

    # Setup DuckDB
    try:
        import duckdb
    except ImportError:
        print("  ERROR: duckdb Python package not available.")
        return False, False

    con = duckdb.connect()
    con.execute("INSTALL ducklake; LOAD ducklake;")
    con.execute("INSTALL httpfs; LOAD httpfs;")

    create_s3_secret(con, storage_name)

    # Download global catalog (skip if caller provided a pre-downloaded one)
    if global_catalog_local is None:
        print(f"Downloading global catalog: {global_catalog_s3}")
        if not download_catalog(storage_name, global_catalog_s3, _global_catalog_local):
            print(f"  INFO: No global catalog found. Will create a new one.")

    # Attach global catalog
    try:
        con.execute(f"""
            ATTACH {quote_literal('ducklake:' + _global_catalog_local)} AS global_cat (
                DATA_PATH {quote_literal(data_path)},
                AUTOMATIC_MIGRATION true
            )
        """)
    except duckdb.Error as e:
        print(f"  ERROR: Failed to attach global catalog: {e}")
        con.close()
        return False, False

    global_changed = False
    for table in tables:
        newly_merged = merge_table(con, data_path, schema, table, mode)
        if newly_merged > 0:
            global_changed = True

    con.close()

    # Upload global catalog if it changed (unless caller handles upload)
    if global_changed and not skip_global_upload:
        print(f"Uploading global catalog: {global_catalog_s3}")
        if not upload_catalog(storage_name, _global_catalog_local, global_catalog_s3):
            print(f"  ERROR: Failed to upload global catalog.")
            return False, global_changed
        print(f"  Merge complete for workspace '{workspace}' on storage '{storage_name}'.")
    elif global_changed:
        print(f"  Workspace '{workspace}' merged to global (upload deferred).")
    else:
        print(f"  No changes to global catalog for workspace '{workspace}' on storage '{storage_name}'.")

    return True, global_changed


def merge_workspace(workspace: str, catalog_dir: str, storage_name: str | None = None) -> bool:
    """Merge a workspace's data into the global catalog for one or all storages."""
    # Parse workspace config
    ws_pixi = WORKSPACES_DIR / workspace / "pixi.toml"
    registry = parse_workspace_registry(ws_pixi)
    if not registry:
        print(f"  ERROR: No [tool.registry] found for workspace '{workspace}'.")
        return False

    schema = registry.get("schema", workspace)
    tables = get_tables(registry)
    mode = registry.get("mode", "append")
    if not tables:
        print(f"  ERROR: No tables defined for workspace '{workspace}'.")
        return False

    # Determine which storages to merge
    if storage_name:
        storage_names = [storage_name]
    else:
        storage_names = get_workspace_storages(registry)

    print(f"Merging workspace '{workspace}' (schema={schema}, mode={mode}) for {len(storage_names)} storage(s): {', '.join(storage_names)}")

    all_ok = True
    for sn in storage_names:
        ok, _changed = merge_workspace_storage(workspace, sn, schema, tables, catalog_dir, mode=mode)
        if not ok:
            all_ok = False

    return all_ok


def merge_all_workspaces(catalog_dir: str, storage_filter: str | None = None) -> bool:
    """Merge all workspaces, grouped by storage for efficiency.

    Downloads the global catalog once per storage target instead of once per
    workspace. Idempotent: workspaces with no pending files are skipped quickly.
    """
    workspaces = discover_workspaces()

    # Group workspaces by storage target
    storage_groups: dict[str, list[tuple[str, str, list[str], str]]] = {}
    for ws in workspaces:
        if not ws["registry"]:
            continue
        registry = ws["registry"]
        schema = registry.get("schema", ws["name"])
        tables = get_tables(registry)
        mode = registry.get("mode", "append")
        if not tables:
            continue
        try:
            storages = get_workspace_storages(registry)
        except (ValueError, KeyError) as e:
            print(f"WARNING: Skipping workspace '{ws['name']}': {e}")
            continue
        for sn in storages:
            if storage_filter and sn != storage_filter:
                continue
            storage_groups.setdefault(sn, []).append((ws["name"], schema, tables, mode))

    if not storage_groups:
        print("No workspaces to merge.")
        return True

    total_workspaces = sum(len(ws_list) for ws_list in storage_groups.values())
    print(f"Merging {total_workspaces} workspace(s) across {len(storage_groups)} storage(s)...")

    all_ok = True
    for storage_name, ws_list in storage_groups.items():
        print(f"\n{'=' * 60}")
        print(f"Storage: {storage_name} ({len(ws_list)} workspace(s))")
        print(f"{'=' * 60}")

        # Download global catalog ONCE for this storage
        storage_catalog_dir = os.path.join(catalog_dir, storage_name)
        os.makedirs(storage_catalog_dir, exist_ok=True)
        global_catalog_local = os.path.join(storage_catalog_dir, "catalog.duckdb")
        global_catalog_s3 = build_global_catalog_path(storage_name)

        print(f"Downloading global catalog: {global_catalog_s3}")
        if not download_catalog(storage_name, global_catalog_s3, global_catalog_local):
            print(f"  INFO: No global catalog found. Will create a new one.")

        any_global_changed = False
        for ws_name, schema, tables, mode in ws_list:
            print(f"\n--- Workspace: {ws_name} (mode={mode}) ---")
            ok, global_changed = merge_workspace_storage(
                ws_name, storage_name, schema, tables, catalog_dir,
                mode=mode,
                global_catalog_local=global_catalog_local,
                skip_global_upload=True,
            )
            if not ok:
                all_ok = False
            if global_changed:
                any_global_changed = True

        # Upload global catalog ONCE for this storage
        if any_global_changed:
            print(f"\nUploading global catalog: {global_catalog_s3}")
            if not upload_catalog(storage_name, global_catalog_local, global_catalog_s3):
                print(f"  ERROR: Failed to upload global catalog for storage '{storage_name}'.")
                all_ok = False
            else:
                print(f"  Global catalog updated for storage '{storage_name}'.")
        else:
            print(f"\n  No changes to global catalog for storage '{storage_name}'.")

    return all_ok


def main():
    parser = argparse.ArgumentParser(description="Merge workspace data into global catalog")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--workspace", help="Workspace name to merge")
    group.add_argument("--all", action="store_true", dest="merge_all", help="Merge all workspaces (grouped by storage)")
    parser.add_argument("--catalog-dir", help="Directory for catalog files (default: temp dir)")
    parser.add_argument("--storage", default=None, help="Specific storage target (default: all workspace storages)")
    args = parser.parse_args()

    if args.workspace and not WORKSPACE_NAME_RE.match(args.workspace):
        print(f"ERROR: Invalid workspace name: {args.workspace}")
        sys.exit(1)

    def run(catalog_dir: str) -> bool:
        if args.merge_all:
            return merge_all_workspaces(catalog_dir, args.storage)
        return merge_workspace(args.workspace, catalog_dir, args.storage)

    if args.catalog_dir:
        os.makedirs(args.catalog_dir, exist_ok=True)
        success = run(args.catalog_dir)
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            success = run(tmpdir)

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
