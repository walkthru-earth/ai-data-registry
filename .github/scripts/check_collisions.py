# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Layer 2: Check schema.table uniqueness across all workspaces.

Usage: uv run check_collisions.py [--workspace <name>]

Scans all workspaces/*/pixi.toml files, extracts schema.table from
[tool.registry], and verifies no two workspaces claim the same combination.

If --workspace is given, only reports collisions involving that workspace.
Exit 0 on pass, 1 if collisions detected.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.registry_config import discover_workspaces


def check_collisions(target_workspace: str | None = None) -> list[str]:
    """Check for schema.table collisions. Returns list of error messages."""
    workspaces = discover_workspaces()
    errors: list[str] = []

    # Build map of schema.table -> list of workspace names
    claims: dict[str, list[str]] = {}
    for ws in workspaces:
        registry = ws.get("registry")
        if not registry:
            continue
        schema = registry.get("schema", "")
        table = registry.get("table", "")
        if schema and table:
            key = f"{schema}.{table}"
            claims.setdefault(key, []).append(ws["name"])

    # Check for duplicates
    for key, owners in claims.items():
        if len(owners) > 1:
            # If filtering by workspace, only report if it's involved
            if target_workspace and target_workspace not in owners:
                continue
            errors.append(
                f"Collision: schema.table '{key}' is claimed by multiple workspaces: "
                f"{', '.join(sorted(owners))}. Each schema.table must be owned by exactly one workspace."
            )

    # Also check for S3 prefix (schema) overlaps
    schema_owners: dict[str, list[str]] = {}
    for ws in workspaces:
        registry = ws.get("registry")
        if not registry:
            continue
        schema = registry.get("schema", "")
        if schema:
            schema_owners.setdefault(schema, []).append(ws["name"])

    for schema, owners in schema_owners.items():
        if len(owners) > 1:
            if target_workspace and target_workspace not in owners:
                continue
            # Multiple workspaces can share a schema if they have different tables
            # This is a warning, not a hard block
            pass

    return errors


def main():
    parser = argparse.ArgumentParser(description="Check schema.table collisions across workspaces")
    parser.add_argument("--workspace", help="Only report collisions involving this workspace")
    args = parser.parse_args()

    print("Checking schema.table uniqueness across all workspaces...")

    errors = check_collisions(args.workspace)

    if errors:
        print(f"\n  FAILED: {len(errors)} collision(s) found:\n")
        for i, err in enumerate(errors, 1):
            print(f"  {i}. {err}")
        print(f"\n  Each workspace must own a unique schema.table combination.")
        sys.exit(1)
    else:
        print(f"  PASSED: No schema.table collisions detected.")
        sys.exit(0)


if __name__ == "__main__":
    main()
