# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "croniter>=6.0",
# ]
# ///
"""Layer 1: Static analysis of a workspace pixi.toml against the registry contract.

Usage: uv run validate_manifest.py <path/to/workspace/pixi.toml>

Validates:
- [tool.registry] section exists with all required fields
- [tool.registry.runner] backend + flavor are in the supported list
- [tool.registry.license] has valid SPDX identifiers
- Cron schedule is parseable
- Workspace name matches ^[a-z][a-z0-9-]*$
- Required tasks (pipeline, extract, validate, dry-run) exist
- HuggingFace backend requires image field

Exit 0 on pass, 1 on failure with clear fix instructions.
"""

from __future__ import annotations

import sys
from pathlib import Path

from croniter import croniter

# Allow importing from .github/scripts/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.registry_config import (
    REQUIRED_REGISTRY_FIELDS,
    REQUIRED_TASKS,
    RESTRICTIVE_DATA_LICENSES,
    SUPPORTED_BACKENDS,
    VALID_CODE_LICENSES,
    VALID_DATA_LICENSES,
    VALID_MODES,
    WORKSPACE_NAME_RE,
    get_workspace_name,
    parse_workspace_manifest,
)


def validate(manifest_path: str) -> list[str]:
    """Validate a workspace pixi.toml. Returns list of error messages."""
    errors: list[str] = []
    warnings: list[str] = []
    path = Path(manifest_path)

    if not path.exists():
        return [f"File not found: {manifest_path}"]

    manifest = parse_workspace_manifest(path)
    ws_name = get_workspace_name(path)

    # --- Workspace name ---
    if not WORKSPACE_NAME_RE.match(ws_name):
        errors.append(
            f"Workspace name '{ws_name}' is invalid. "
            "Must match ^[a-z][a-z0-9-]*$ (lowercase, starts with letter, hyphens allowed)."
        )

    # --- [tool.registry] existence ---
    tool = manifest.get("tool", {})
    registry = tool.get("registry")
    if not registry:
        errors.append(
            "Missing [tool.registry] section. Every workspace must declare pipeline metadata. "
            "See .claude/rules/workspace-contract.md for the required fields."
        )
        return errors  # Can't validate further without registry

    # --- Required fields ---
    missing = REQUIRED_REGISTRY_FIELDS - set(registry.keys())
    if missing:
        errors.append(
            f"Missing required fields in [tool.registry]: {', '.join(sorted(missing))}. "
            "All of these are required: " + ", ".join(sorted(REQUIRED_REGISTRY_FIELDS))
        )

    # --- Mode ---
    mode = registry.get("mode")
    if mode and mode not in VALID_MODES:
        errors.append(
            f"Invalid mode '{mode}'. Must be one of: {', '.join(sorted(VALID_MODES))}"
        )

    # --- Schema name ---
    schema = registry.get("schema", "")
    if schema and not WORKSPACE_NAME_RE.match(schema):
        errors.append(
            f"Invalid schema name '{schema}'. Must match ^[a-z][a-z0-9-]*$ "
            "(lowercase, starts with letter, hyphens allowed)."
        )

    # --- Cron schedule ---
    schedule = registry.get("schedule", "")
    if schedule:
        if not croniter.is_valid(schedule):
            errors.append(
                f"Invalid cron schedule '{schedule}'. Must be a valid cron expression "
                "(e.g., '0 6 * * *' for daily at 06:00 UTC)."
            )

    # --- [tool.registry.runner] ---
    runner = registry.get("runner")
    if not runner:
        errors.append(
            "Missing [tool.registry.runner] section. Must declare backend and flavor. "
            "Example: backend = \"github\", flavor = \"ubuntu-latest\""
        )
    else:
        backend = runner.get("backend")
        flavor = runner.get("flavor")

        if not backend:
            errors.append("Missing [tool.registry.runner].backend. Required field.")
        elif backend not in SUPPORTED_BACKENDS:
            errors.append(
                f"Unsupported backend '{backend}'. "
                f"Supported: {', '.join(sorted(SUPPORTED_BACKENDS.keys()))}"
            )
        else:
            allowed_flavors = SUPPORTED_BACKENDS[backend]["flavors"]
            if not flavor:
                errors.append(
                    f"Missing [tool.registry.runner].flavor. "
                    f"Allowed for '{backend}': {', '.join(allowed_flavors)}"
                )
            elif flavor not in allowed_flavors:
                errors.append(
                    f"Flavor '{flavor}' not allowed for backend '{backend}'. "
                    f"Allowed: {', '.join(allowed_flavors)}"
                )

        # HuggingFace requires image
        if backend == "huggingface" and not runner.get("image"):
            errors.append(
                "Backend 'huggingface' requires [tool.registry.runner].image "
                "(Docker image URL, e.g., 'ghcr.io/org/image:latest')."
            )

    # --- [tool.registry.license] ---
    license_cfg = registry.get("license")
    if not license_cfg:
        errors.append(
            "Missing [tool.registry.license] section. Must declare code and data licenses. "
            "Example: code = \"Apache-2.0\", data = \"CC-BY-4.0\""
        )
    else:
        code_license = license_cfg.get("code", "")
        data_license = license_cfg.get("data", "")
        data_source = license_cfg.get("data_source", "")
        mixed = license_cfg.get("mixed", False)

        if not code_license:
            errors.append("Missing [tool.registry.license].code (SPDX identifier for extraction code).")
        elif code_license not in VALID_CODE_LICENSES:
            errors.append(
                f"Code license '{code_license}' is not an OSI-approved SPDX identifier. "
                f"Valid options include: {', '.join(sorted(list(VALID_CODE_LICENSES)[:8]))}..."
            )

        if not data_license:
            errors.append("Missing [tool.registry.license].data (SPDX identifier for output data).")
        elif data_license not in VALID_DATA_LICENSES:
            errors.append(
                f"Data license '{data_license}' is not a recognized SPDX identifier. "
                f"Valid options include: {', '.join(sorted(list(VALID_DATA_LICENSES)[:8]))}..."
            )
        elif data_license in RESTRICTIVE_DATA_LICENSES:
            warnings.append(
                f"Data license '{data_license}' is restrictive (non-commercial). "
                "This is allowed but will be flagged for reviewer attention."
            )

        if not data_source:
            errors.append("Missing [tool.registry.license].data_source (name of the data provider).")

        if mixed and not license_cfg.get("sources"):
            errors.append(
                "When mixed = true, [tool.registry.license].sources array is required. "
                "Each entry needs: name, license, url."
            )

    # --- [tool.registry.checks] ---
    checks = registry.get("checks")
    if not checks:
        errors.append(
            "Missing [tool.registry.checks] section. Must declare quality thresholds. "
            "Example: min_rows = 1000, max_null_pct = 5"
        )

    # --- Required tasks ---
    tasks = manifest.get("tasks", {})
    task_names = set(tasks.keys())
    missing_tasks = REQUIRED_TASKS - task_names
    if missing_tasks:
        errors.append(
            f"Missing required tasks: {', '.join(sorted(missing_tasks))}. "
            "Every workspace must define: " + ", ".join(sorted(REQUIRED_TASKS)) + ". "
            "See .claude/rules/workspace-contract.md for task definitions."
        )

    # --- Print results ---
    if warnings:
        for w in warnings:
            print(f"  WARNING: {w}")

    return errors


def main():
    if len(sys.argv) < 2:
        print("Usage: python validate_manifest.py <path/to/workspace/pixi.toml>")
        sys.exit(2)

    manifest_path = sys.argv[1]
    ws_name = get_workspace_name(manifest_path)
    print(f"Validating workspace '{ws_name}' ({manifest_path})...")

    errors = validate(manifest_path)

    if errors:
        print(f"\n  FAILED: {len(errors)} error(s) found:\n")
        for i, err in enumerate(errors, 1):
            print(f"  {i}. {err}")
        print(f"\n  Fix these issues and push again.")
        sys.exit(1)
    else:
        print(f"  PASSED: All static checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
