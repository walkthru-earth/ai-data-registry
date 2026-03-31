# Project: ai-data-registry

## Overview
Git-native, PR-driven data platform. Each workspace is an isolated data pipeline with its own language, dependencies, and compute backend. Contributors add workspaces via PRs. Maintainers manage infrastructure. DuckLake federates all workspace catalogs into one queryable global catalog.

Full architecture: `research/architecture.md`

## Data Registry Architecture (summary)
- **Workspace** = isolated pixi environment under `workspaces/` (own deps, tasks, scripts)
- **Schema** = data namespace in `[tool.registry].schema` (S3 prefix + DuckLake schema). Often matches the workspace name but they are separate concepts
- Each workspace writes Parquet to local `$OUTPUT_DIR/`, never directly to S3
- Workflows upload to S3 via `s5cmd` on the workspace's behalf
- Runner backends: `github` (free, lightweight), `hetzner` (ephemeral ARM), `huggingface` (GPU/Docker)
- Contributors pick backend + flavor from supported list. Maintainer manages all infra.
- DuckLake: one SQLite catalog per workspace + one global. Zero-copy federation via `ducklake_add_data_files()`
- Task contract: `pipeline` entry point chains `extract` -> `validate` via `depends-on`
- PR validation: 4 layers (static analysis, collision detection, live catalog check, dry-run)
- See `.claude/rules/workspace-contract.md` for the full MUST/MUST NOT contract
- Working example: `workspaces/test-minimal/` (minimal reference implementation)

## Package Manager: Pixi
- **Config**: Each workspace has its own `pixi.toml`
- **Root**: `pixi.toml` at project root defines shared tools
- **Lock file**: Single `pixi.lock` at root for all workspaces (auto-generated, never edit manually)
- **Environments**: `.pixi/envs/` (gitignored)
- **Channels**: conda-forge

---

## Multi-Workspace Architecture

```
ai-data-registry/
├── pixi.toml              # Root, shared tools (GDAL, DuckDB, gpio, s5cmd, pnpm, Python)
├── pixi.lock              # Single lock file for ALL workspaces
├── .claude/               # AI rules, skills, agents, commands (project-wide)
├── .github/
│   ├── registry.config.toml  # Backend definitions, storage secret names
│   ├── scripts/               # CI scripts (run via uv, PEP 723 inline deps)
│   └── workflows/             # GitHub Actions (validation, extraction, scheduling)
├── research/
│   └── architecture.md    # Full platform architecture
├── workspaces/
│   ├── test-minimal/      # Example workspace (reference implementation)
│   │   ├── pixi.toml      # [workspace] + [tool.registry] + deps + tasks
│   │   ├── extract.py     # Extraction script
│   │   └── validate_local.py
│   ├── weather/           # backend: hetzner
│   │   └── pixi.toml
│   └── sanctions/         # backend: github (lightweight)
│       └── pixi.toml
```

### Creating a Sub-Workspace
```bash
# From project root:
mkdir -p workspaces/my-workspace
cd workspaces/my-workspace
pixi init . --channel conda-forge --platform osx-arm64 --platform linux-64 --platform win-64
cd ../..
pixi workspace register --name my-workspace --path workspaces/my-workspace
rm workspaces/my-workspace/pixi.lock  # root lock covers all

# Add deps targeting the workspace (from root):
# Or with Claude Code: /new-workspace my-workspace python
pixi add -w my-workspace python
pixi add -w my-workspace <other-deps>
```
Or use /new-workspace <name> <language> for guided setup.

### Workspace Isolation Principles

**What lives in root `pixi.toml` (shared):**
- GDAL, DuckDB, gpio, s5cmd, pnpm, Python, Node.js — tools used by ALL workspaces
- Cross-workspace orchestration tasks only

**What lives in each workspace `pixi.toml` (isolated):**
- The workspace's own language runtime (may differ from root Python)
- All workspace-specific dependencies
- All workspace-specific tasks
- Platform-specific deps via `[target.<platform>.dependencies]`

**Boundaries — NEVER cross these:**
- Never add workspace-specific deps to root `pixi.toml`
- Never assume a workspace uses Python — always check its `pixi.toml` first
- Never share state between workspaces (each has its own `.pixi/envs/`)
- GeoParquet is the interchange format when workspaces need to share data

**Running commands:**
```bash
# Shared tools (from root — uses root pixi.toml)
pixi run duckdb -csv -c "SELECT 42"
pixi run gdal info input.gpkg
pixi run gpio inspect file.parquet

# Workspace tasks (from root, using -w flag)
pixi run -w workspace-a <task>

# Adding deps to a workspace (from root)
pixi add -w workspace-a <pkg>
pixi add -w workspace-a --pypi <pkg>
```

---

## Conventions
- **Two runtimes, clear separation:**
  - **pixi** runs workspace pipelines and shared tools (`pixi run -w {name} pipeline`, `pixi run s5cmd`)
  - **uv** runs CI helper scripts in `.github/scripts/` (`uv run .github/scripts/validate_manifest.py`). These use PEP 723 inline deps, not pixi.
- **All shared tools run through pixi** -- never run `duckdb`, `gdal`, `gpio`, `s5cmd`, `python`, `node`, `pnpm` directly
- `pixi run pnpm` -- NEVER npm or yarn (npm is denied in settings.json)
- **GeoParquet is the standard interchange format** -- validate with `pixi run gpio check all`
- New unified `gdal` CLI (v3.11+) -- NOT legacy `ogr2ogr`/`gdalinfo`/`ogrinfo`
- Tasks in `[tasks]` of each workspace's `pixi.toml`, not Makefiles
- Workspaces write to `$OUTPUT_DIR/`, never to S3 directly. Workflows upload via `pixi run s5cmd`
- Do NOT hardcode `OUTPUT_DIR` in pixi task `env`. CI passes its own value.
- Every workspace must have `[tool.registry]` metadata and required tasks (see `workspace-contract` rule)
- Never commit `.pixi/` environments (only `.pixi/config.toml` is tracked)
- `pixi.lock` is committed at root only. Workspace-level `pixi.lock` files should be deleted after `pixi init`

### Adding Dependencies: conda vs PyPI

**IMPORTANT:** Pixi supports two package sources. Always prefer conda-forge; fall back to PyPI only when the package is not available on conda-forge.

| Source | Command (root) | Command (workspace) | When to use |
|--------|---------------|---------------------|-------------|
| **conda-forge** | `pixi add <pkg>` | `pixi add -w <workspace> <pkg>` | Default — native compiled packages, C/C++ libraries, runtimes (Python, Node, GDAL, DuckDB) |
| **PyPI** | `pixi add --pypi <pkg>` | `pixi add -w <workspace> --pypi <pkg>` | Only when the package does not exist on conda-forge (pure Python packages, niche tools) |

**Decision flow:**
1. Search conda-forge first: `pixi search <pkg>` — if found, use `pixi add <pkg>`
2. If not on conda-forge, use `pixi add --pypi <pkg>`
3. Never mix — do not add the same package from both sources
4. Conda packages go in `[dependencies]`, PyPI packages go in `[pypi-dependencies]` in `pixi.toml`

---

## Reference: Rules (`.claude/rules/`)

Rules load automatically when working with matching files. Path-scoped rules only activate for files matching their `paths:` glob.

| Rule | Scope | When it activates | What it enforces |
|------|-------|-------------------|-----------------|
| `tool-execution.md` | Global | Always | All tools via `pixi run`, workspace targeting patterns |
| `pixi.md` | `**/pixi.toml`, `**/pixi.lock` | Editing pixi config | Deps format, tasks, workspace registration, platform-specific patterns |
| `workspace-contract.md` | `**/pixi.toml` | Editing workspace config | `[tool.registry]` contract: required tasks, runner backends, license, checks |
| `workspaces.md` | Global | Always | Isolation principles, workspace creation, shared vs isolated deps |
| `duckdb.md` | `**/*.sql`, `**/*.py` | SQL or Python files | DuckDB dialect, Friendly SQL, spatial extension, GeoParquet best practices |
| `geospatial.md` | `**/*.parquet`, `**/*.gpkg`, `**/*.shp`, `**/*.tif`, etc. | Spatial files | GeoParquet as standard, tool selection (gpio vs gdal vs duckdb), CRS rules |
| `nodejs.md` | `**/*.js`, `**/*.ts`, `**/package.json` | Node/JS files | pnpm only, workspace Node.js patterns, playwright setup |

## Reference: Commands (`.claude/commands/`)

Slash commands for common operations. Invoked directly (e.g., `/new-workspace`). All use `pixi run` and work cross-platform.

| Command | Usage | What it does |
|---------|-------|------|
| `/new-workspace` | `<name> <language>` | Scaffold a sub-workspace with full contract: init, register, `[tool.registry]`, required tasks, license |
| `/env-info` | (no args) | Show pixi env, installed packages, tool versions, registered workspaces |
| `/add-dep` | `<package> [--pypi] [-w workspace]` | Add dependency (conda-forge preferred, PyPI fallback) |
| `/query` | `<SQL or description>` | Run DuckDB query via `pixi run duckdb` |
| `/run-in` | `<workspace> <task>` | Run a pixi task in a specific workspace |
| `/inspect-file` | `<file-path>` | Inspect any data file — schema, row count, samples, spatial info |
| `/convert` | `<input> <output>` | Convert between geospatial formats (GeoParquet, GeoJSON, GeoPackage, etc.) |

## Reference: Skills (`.claude/skills/`)

Skills are invoked automatically when the task matches, or explicitly. All use `pixi run`.

| Skill | When to use | Tool |
|-------|-------------|------|
| **geoparquet** | Creating, validating, optimizing, partitioning GeoParquet; STAC metadata; spatial indexing (H3/S2/A5) | `pixi run gpio` |
| **gdal** | Vector/raster format conversion, reprojection, pipeline, terrain analysis, VSI remote files. Esri refs in `references/esri-*.md` | `pixi run gdal` |
| **duckdb** | SQL queries, file exploration, spatial analysis (ST_*, s2_*, h3_*, a5_*), ArcGIS REST macros, DuckLake, session state, docs search, extension management. References: `query.md`, `read-file.md`, `attach-db.md`, `state.md`, `docs-search.md`, `install.md`, `read-memories.md`, `spatial.md`, `h3.md`, `a5.md`, `geography.md`, `arcgis.md`, `ducklake.md` | `pixi run duckdb` |
| **data-pipeline** | Building ETL pipelines as pixi tasks with `depends-on`, multi-tool chaining | all tools |
| **env-check** | Validate environment health: pixi, DuckDB, GDAL, gpio versions, extension status, compatibility | `pixi run` |
| **playwright-skill** | Browser automation, testing, screenshots, responsive design, form testing, link checking | `pixi run node` |

## Reference: Agents (`.claude/agents/`)

Agents are spawned as subprocesses for complex tasks. They run autonomously and report back.

| Agent | When it's used | What it does |
|-------|---------------|------|
| **data-explorer** | Proactively when investigating any data file | Profiles datasets: row count, schema, nulls, types, CRS, geometry, Parquet metadata |
| **data-quality** | When validating data integrity | Deep checks: null rates, cardinality, duplicates, outliers, geometry validity, CRS consistency, GeoParquet spec |
| **pipeline-orchestrator** | When planning multi-step workflows | Routes to right tool (GDAL/DuckDB/gpio), generates contract-compliant pixi tasks, knows runner backends and S3 upload pattern |

---

## Root-Level Shared Tools

| Tool | Version | Command | Purpose |
|------|---------|---------|---------|
| GDAL | >=3.12.3 | `pixi run gdal ...` | Unified vector/raster CLI (v3.11+) |
| DuckDB | >=1.5.1 | `pixi run duckdb ...` | Analytical SQL engine |
| gpio | 1.0.0b2 | `pixi run gpio ...` | GeoParquet optimization/validation (PyPI: `pixi add --pypi geoparquet-io --pre`) |
| libgdal-arrow-parquet | >=3.12.3 | (GDAL driver) | Parquet I/O via Arrow |
| pnpm | >=10.32.1 | `pixi run pnpm ...` | Node package manager (NEVER npm) |
| Python | >=3.12.13 | `pixi run python ...` | Default runtime |
| Node.js | via pixi | `pixi run node ...` | Node.js runtime |
| s5cmd | >=2.3.0 | `pixi run s5cmd ...` | Parallel S3 uploads (256 workers, 12-32x faster than AWS CLI) |

## Platforms
osx-arm64, linux-64, win-64 — all dependencies must be cross-platform compatible.

---

## DuckDB Runtime Files (`.claude/skills/duckdb/references/`)

| File | Purpose | Load |
|------|---------|------|
| `state.sql` | Core session state (spatial, httpfs, fts extensions, read_any macro) | Auto via `-init` |
| `arcgis.sql` | ArcGIS REST macros, VARIANT-optimized (19 macros: catalog, layers, meta, query, read, stats, extent, fields, domains, subtypes, relationships, type mapping, auth) | `pixi run duckdb -init ".claude/skills/duckdb/references/arcgis.sql"` |

The **duckdb** skill documents all ArcGIS macros in [arcgis.md](/.claude/skills/duckdb/references/arcgis.md). The **gdal** skill has Esri driver references in `references/esri-*.md` (FeatureServer, FileGDB, Shapefile, raster services, Python API, gotchas).

## Skill Routing for Esri/ArcGIS Data

| Task | Skill/Agent | Why |
|------|------------|-----|
| Query ArcGIS FeatureServer via SQL | **duckdb** ([arcgis.md](/.claude/skills/duckdb/references/arcgis.md)) | DuckDB reads JSON/GeoJSON natively, macros handle pagination, domains, auth |
| Download FeatureServer via CLI | **gdal** (`esri-featureserver.md` reference) | GDAL ESRIJSON driver with `gdal vector convert` |
| Read/write .gdb (File Geodatabase) | **gdal** (`esri-filegdb.md` reference) | OpenFileGDB driver, full CRUD |
| Deep .gdb inspection (domains, relationships, ERD) | **gdal** (`esri-python-api.md` reference) | Python GDAL API for what CLI can't reach |
| Convert Shapefile to GeoParquet | **gdal** or **duckdb** ([read-file.md](/.claude/skills/duckdb/references/read-file.md)) | Either tool works, GDAL for complex CRS |
| ArcGIS MapServer/ImageServer raster | **gdal** (`esri-raster-services.md` reference) | WMS/TMS/AGS minidriver XML configs |
| Esri CRS, date, encoding issues | **gdal** (`esri-gotchas.md` reference) | Version history, known issues, workarounds |
| Build ArcGIS ingest pipeline | **pipeline-orchestrator** agent | Routes to DuckDB macros or GDAL as needed |
| Profile ArcGIS dataset | **data-explorer** agent | Uses arcgis macros for FeatureServer profiling |

## Watch Out For
- Run **env-check** skill after setup or when things break -- it validates everything
- Always run `pixi install` after pulling to sync the environment
- GDAL version must match libgdal-arrow-parquet version
- GDAL: `vector convert` is format-only. Use `vector reproject -d EPSG:xxxx` for CRS changes
- gpio: install via `pixi add --pypi geoparquet-io --pre` (PyPI beta, not on conda-forge)
- DuckDB spatial extension: `INSTALL spatial; LOAD spatial;` (or use **duckdb** skill, [state.md](/.claude/skills/duckdb/references/state.md) reference)
- Always validate GeoParquet: `pixi run gpio check all <file>`
- Use `pixi run pnpm` not `npm` -- npm is denied in settings.json
- Python 3.12 is the runtime -- stable and widely supported
- Each workspace may use a different language -- check its `pixi.toml` first
- Never mix workspace dependencies -- isolation is enforced
- Use `/new-workspace` to scaffold workspaces with full contract compliance
- Workspace code writes to `$OUTPUT_DIR/`, never to S3. The workflow handles uploads via s5cmd
- Runner backend + flavor must be from the supported list (see `workspace-contract` rule)
