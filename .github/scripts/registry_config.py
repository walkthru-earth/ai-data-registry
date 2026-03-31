"""Shared configuration module for the data registry platform.

Reads .github/registry.config.toml and provides helpers for parsing
workspace manifests, discovering workspaces, and resolving backend configs.
"""

from __future__ import annotations

import os
import re
import tomllib
from glob import glob
from pathlib import Path

# Repo root: two levels up from .github/scripts/
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = REPO_ROOT / ".github" / "registry.config.toml"
WORKSPACES_DIR = REPO_ROOT / "workspaces"

# Valid SPDX license identifiers for code (OSI-approved)
VALID_CODE_LICENSES = {
    "Apache-2.0", "MIT", "BSD-2-Clause", "BSD-3-Clause", "ISC",
    "MPL-2.0", "LGPL-2.1-only", "LGPL-3.0-only", "GPL-2.0-only",
    "GPL-3.0-only", "AGPL-3.0-only", "Unlicense", "0BSD",
}

# Valid SPDX license identifiers for data
VALID_DATA_LICENSES = {
    "CC-BY-4.0", "CC-BY-SA-4.0", "CC0-1.0", "ODbL-1.0", "PDDL-1.0",
    "CC-BY-3.0", "CC-BY-2.0", "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0",
    "public-domain", "CDLA-Permissive-2.0", "CDLA-Sharing-1.0",
}

# Restrictive data licenses that trigger a warning (not a block)
RESTRICTIVE_DATA_LICENSES = {"CC-BY-NC-4.0", "CC-BY-NC-SA-4.0"}

# Valid workspace name pattern
WORKSPACE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")

# Required fields in [tool.registry]
REQUIRED_REGISTRY_FIELDS = {"description", "schedule", "timeout", "tags", "schema", "table", "mode"}

# Valid modes
VALID_MODES = {"append", "replace", "upsert"}

# Required tasks every workspace must define
REQUIRED_TASKS = {"pipeline", "extract", "validate", "dry-run"}


def load_config() -> dict:
    """Load the registry config from .github/registry.config.toml."""
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def get_storage_config() -> dict:
    """Return the [storage] section of the registry config."""
    return load_config()["storage"]


def get_backends() -> dict[str, dict]:
    """Return backends as {name: {workflow, flavors}}."""
    config = load_config()
    backends = {}
    for key, value in config.items():
        if key.startswith("backends."):
            name = key.split(".", 1)[1]
            backends[name] = value
        elif key != "storage" and isinstance(value, dict) and "flavors" in value:
            backends[key] = value
    # Also check for [backends] as a table with sub-tables
    if "backends" in config and isinstance(config["backends"], dict):
        for name, section in config["backends"].items():
            if isinstance(section, dict) and "flavors" in section:
                backends[name] = section
    return backends


# Eagerly load for convenience
SUPPORTED_BACKENDS = get_backends()


def parse_workspace_manifest(path: str | Path) -> dict:
    """Parse a workspace pixi.toml and return its full content."""
    with open(path, "rb") as f:
        return tomllib.load(f)


def parse_workspace_registry(path: str | Path) -> dict | None:
    """Parse [tool.registry] from a workspace pixi.toml. Returns None if missing."""
    manifest = parse_workspace_manifest(path)
    tool = manifest.get("tool", {})
    return tool.get("registry")


def get_workspace_name(path: str | Path) -> str:
    """Extract workspace name from its pixi.toml path."""
    return Path(path).parent.name


def discover_workspaces(workspaces_dir: str | Path | None = None) -> list[dict]:
    """Find all workspaces and return their parsed registry configs.

    Returns a list of dicts with keys: name, path, registry, manifest.
    """
    ws_dir = Path(workspaces_dir) if workspaces_dir else WORKSPACES_DIR
    results = []
    for pixi_path in sorted(ws_dir.glob("*/pixi.toml")):
        manifest = parse_workspace_manifest(pixi_path)
        registry = manifest.get("tool", {}).get("registry")
        results.append({
            "name": pixi_path.parent.name,
            "path": str(pixi_path),
            "registry": registry,
            "manifest": manifest,
        })
    return results


def resolve_secret_env(secret_name: str) -> str | None:
    """Resolve a secret name to its environment variable value (for CI scripts)."""
    return os.environ.get(secret_name)
