# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Upload workspace output to all target storages with owner/repo/branch prefix.

Usage:
    uv run upload_output.py --workspace <name> --output-dir <path> --timestamp <ts>

Reads workspace pixi.toml for schema, tables, and storage targets.
Reads registry.config.toml for storage configs and credential mappings.
For each storage target, resolves credentials from env vars and uploads
parquet files via s5cmd with the {owner}/{repo}/{branch}/ prefix.

Env vars (set by GitHub Actions):
    GITHUB_REPOSITORY    owner/repo (e.g. walkthru-earth/ai-data-registry)
    GITHUB_REF_NAME      branch name (e.g. main)
    Per-storage secrets   as defined in registry.config.toml
"""

from __future__ import annotations

import argparse
import os
import sys
from glob import glob
from pathlib import Path

# Allow importing from .github/scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.registry_config import (
    WORKSPACE_NAME_RE,
    build_s3_root,
    get_workspace_storages,
    parse_workspace_registry,
    s5cmd_for_storage,
    WORKSPACES_DIR,
)


def upload_data(
    storage_name: str,
    schema: str,
    output_dir: str,
    timestamp: str,
) -> tuple[int, list[str]]:
    """Upload parquet files to a single storage target.

    Returns (count, errors).
    """
    root = build_s3_root(storage_name)
    parquet_files = sorted(glob(os.path.join(output_dir, "*.parquet")))

    if not parquet_files:
        return 0, ["No Parquet files found in output directory."]

    errors = []
    count = 0
    for f in parquet_files:
        table = Path(f).stem
        dest = f"{root}{schema}/{table}/{timestamp}.parquet"
        result = s5cmd_for_storage(storage_name, "cp", f, dest)
        if result.returncode != 0:
            errors.append(f"Failed to upload {table} to {storage_name}: {result.stderr.strip()}")
        else:
            count += 1
            print(f"  [{storage_name}] {schema}/{table}/{timestamp}.parquet")

    return count, errors


def main():
    parser = argparse.ArgumentParser(description="Upload workspace output to storage targets.")
    parser.add_argument("--workspace", required=True, help="Workspace name")
    parser.add_argument("--output-dir", required=True, help="Directory containing .parquet files")
    parser.add_argument("--timestamp", required=True, help="Timestamp for filenames (e.g. 20260401T060000Z)")
    args = parser.parse_args()

    if not WORKSPACE_NAME_RE.match(args.workspace):
        print(f"ERROR: Invalid workspace name: {args.workspace}")
        sys.exit(1)

    # Load workspace config
    ws_pixi = WORKSPACES_DIR / args.workspace / "pixi.toml"
    registry = parse_workspace_registry(ws_pixi)
    if not registry:
        print(f"ERROR: No [tool.registry] found in {ws_pixi}")
        sys.exit(1)

    schema = registry.get("schema", args.workspace)
    storages = get_workspace_storages(registry)

    print(f"Uploading workspace '{args.workspace}' (schema={schema}) to {len(storages)} storage(s): {', '.join(storages)}")

    total_errors = []
    total_count = 0

    for storage_name in storages:
        print(f"\n--- Storage: {storage_name} ---")

        # Upload data files
        count, errors = upload_data(storage_name, schema, args.output_dir, args.timestamp)
        total_count += count
        total_errors.extend(errors)

    print(f"\nUploaded {total_count} file(s) across {len(storages)} storage(s).")

    if total_errors:
        print(f"\n{len(total_errors)} error(s):")
        for err in total_errors:
            print(f"  - {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
