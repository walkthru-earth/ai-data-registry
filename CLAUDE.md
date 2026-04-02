# Project: ai-data-registry

Git-native, PR-driven data platform. Workspaces are isolated data pipelines under `workspaces/`. DuckLake federates all outputs into one queryable global catalog.

## Role Detection

Determine which guide applies based on what the user is doing:

- **Editing `workspaces/`** or asking about pipelines, extraction, data: Read @CONTRIBUTING.md
- **Editing `.github/`** or asking about CI, DuckLake, infra, scheduling: Read @MAINTAINING.md
- **Fresh clone with `setup.sh`/`setup.ps1`** or asking about template setup: Read @docs/template-setup.md

If the user's intent is unclear, ask: "Are you contributing a workspace, maintaining infrastructure, or setting up a new registry from the template?"

## Core Conventions

- **Package manager:** Pixi. Each workspace has its own `pixi.toml` and committed `pixi.lock`. Root `pixi.toml` and `pixi.lock` for shared tools only.
- **Tool execution:** ALL tools via `pixi run <tool>`. Never run directly.
- **Channels:** conda-forge only. Fall back to PyPI when unavailable.
- **GeoParquet** is the standard interchange format. Validate with `pixi run gpio check all`.
- **GDAL:** New unified CLI (v3.11+), NOT legacy `ogr2ogr`/`gdalinfo`/`ogrinfo`.
- **Platforms:** osx-arm64, linux-64, win-64.
- **Two runtimes:** pixi for workspace pipelines and shared tools. uv for CI scripts in `.github/scripts/`.
- **No `.pixi/` in git** (only `.pixi/config.toml` is tracked).

## Key Commands

| Command | What it does |
|---------|-------------|
| `/new-workspace <name> <lang>` | Scaffold workspace with full contract |
| `/inspect-file <path>` | Inspect any data file |
| `/query <SQL>` | Run DuckDB query |
| `/add-dep <pkg> [-w ws]` | Add dependency |
| `/convert <in> <out>` | Convert geospatial formats |
| `/run-in <ws> <task>` | Run task in workspace |
| `/env-info` | Show environment info |

## Watch-Outs

- Run `/env-info` or **env-check** skill after setup or when things break
- Always run `pixi install` after pulling
- DuckDB spatial: `INSTALL spatial; LOAD spatial;` (or use duckdb skill with state.sql)
- GDAL: `vector convert` is format-only. Use `vector reproject -d EPSG:xxxx` for CRS changes
- Each workspace may use a different language. Check its `pixi.toml` first
