# Project: ai-data-registry

Git-native, PR-driven data platform. Each workspace is an isolated data pipeline with its own language, dependencies, and compute backend. Contributors add workspaces via PRs. Maintainers manage infrastructure. DuckLake federates all workspace catalogs into one queryable global catalog.

## Role-Based Guides

Before starting work, determine which guide applies:

- **Contributing a new workspace or pipeline?** Read @CONTRIBUTING.md
- **Maintaining infrastructure, CI, or DuckLake?** Read @MAINTAINING.md

If the user's intent is unclear, ask: "Are you contributing a new workspace, or maintaining infrastructure?"

## Package Manager: Pixi

- Each workspace has its own `pixi.toml`. Root `pixi.toml` defines shared tools.
- Single `pixi.lock` at root for all workspaces (auto-generated, never edit)
- Environments: `.pixi/envs/` (gitignored). Channels: conda-forge
- Always prefer conda-forge. Fall back to PyPI only when not available on conda-forge.

## Project Structure

```
ai-data-registry/
├── pixi.toml              # Root: shared tools (GDAL, DuckDB, gpio, s5cmd, pnpm, Python)
├── pixi.lock              # Single lock file for ALL workspaces
├── CONTRIBUTING.md        # Contributor guide (workspace creation, contract, PR flow)
├── MAINTAINING.md         # Maintainer guide (CI/CD, DuckLake, infra, debugging)
├── .claude/               # AI rules, skills, agents, commands
├── .github/
│   ├── registry.config.toml  # Backend definitions, storage secret names
│   ├── scripts/               # CI scripts (uv + PEP 723 inline deps)
│   └── workflows/             # GitHub Actions
├── research/
│   └── architecture.md    # Full platform architecture
└── workspaces/
    ├── test-minimal/      # Reference implementation
    ├── weather/           # backend: hetzner
    └── sanctions/         # backend: github
```

## Shared Tools (always via `pixi run`)

| Tool | Command | Purpose |
|------|---------|---------|
| GDAL >=3.12.3 | `pixi run gdal ...` | Unified vector/raster CLI (v3.11+) |
| DuckDB >=1.5.1 | `pixi run duckdb ...` | Analytical SQL engine |
| gpio 1.0.0b2 | `pixi run gpio ...` | GeoParquet optimization/validation |
| Python >=3.12 | `pixi run python ...` | Default runtime |
| Node.js | `pixi run node ...` | Node.js runtime |
| pnpm >=10.32 | `pixi run pnpm ...` | Node package manager (NEVER npm) |
| s5cmd >=2.3.0 | `pixi run s5cmd ...` | Parallel S3 uploads |

Never run these tools directly. Always `pixi run <tool>`.

## Conventions

- **Two runtimes:** pixi runs workspace pipelines and shared tools. uv runs CI scripts in `.github/scripts/`.
- **GeoParquet is the standard interchange format.** Validate with `pixi run gpio check all`.
- New unified `gdal` CLI (v3.11+), NOT legacy `ogr2ogr`/`gdalinfo`/`ogrinfo`.
- Tasks in `[tasks]` of each workspace's `pixi.toml`, not Makefiles.
- Never commit `.pixi/` environments (only `.pixi/config.toml` is tracked).

## Platforms

osx-arm64, linux-64, win-64. All dependencies must be cross-platform compatible.

---

## Reference: Rules (`.claude/rules/`)

Rules auto-load when working with matching files. No action needed.

| Rule | Activates on | Enforces |
|------|-------------|----------|
| `tool-execution.md` | Always | All tools via `pixi run` |
| `pixi.md` | `**/pixi.toml`, `**/pixi.lock` | Deps format, tasks, registration |
| `workspace-contract.md` | `**/pixi.toml` | `[tool.registry]` contract, runners, license |
| `workspaces.md` | Always | Isolation principles, shared vs isolated deps |
| `duckdb.md` | `workspaces/**/*.sql`, `workspaces/**/*.py` | DuckDB dialect, spatial, GeoParquet |
| `geospatial.md` | `workspaces/**/*.parquet`, `workspaces/**/*.gpkg`, etc. | GeoParquet standard, tool selection, CRS |
| `nodejs.md` | `workspaces/**/*.js`, `workspaces/**/*.ts` | pnpm only, Node.js patterns |
| `ci-scripts.md` | `.github/scripts/**`, `.github/workflows/**`, `.github/*.toml` | uv runtime (not pixi), CI script reference, workflow patterns |

## Reference: Commands (`.claude/commands/`)

| Command | Usage |
|---------|-------|
| `/new-workspace` | `<name> <language>` - scaffold workspace with full contract |
| `/env-info` | Show pixi env, versions, registered workspaces |
| `/add-dep` | `<package> [--pypi] [-w workspace]` - add dependency |
| `/query` | `<SQL or description>` - run DuckDB query |
| `/run-in` | `<workspace> <task>` - run task in workspace |
| `/inspect-file` | `<file-path>` - inspect data file |
| `/convert` | `<input> <output>` - convert geospatial formats |

## Reference: Skills (`.claude/skills/`)

| Skill | When to use | Tool |
|-------|-------------|------|
| **geoparquet** | GeoParquet creation, validation, optimization, spatial indexing | `pixi run gpio` |
| **gdal** | Format conversion, reprojection, terrain analysis, VSI remote files | `pixi run gdal` |
| **duckdb** | SQL queries, spatial analysis, ArcGIS REST, DuckLake, extensions | `pixi run duckdb` |
| **data-pipeline** | ETL pipelines as pixi tasks with `depends-on` | all tools |
| **env-check** | Validate environment health | `pixi run` |
| **playwright-skill** | Browser automation, testing, screenshots | `pixi run node` |

## Reference: Agents (`.claude/agents/`)

| Agent | Triggers on | What it does |
|-------|------------|-------------|
| **data-explorer** | Investigating any data file | Profile datasets: schema, types, CRS, geometry |
| **data-quality** | Validating data integrity | Null rates, duplicates, geometry validity, CRS consistency |
| **pipeline-orchestrator** | Planning multi-step workflows | Routes to GDAL/DuckDB/gpio, generates pixi tasks |

## Common Watch-Outs

- Run **env-check** skill after setup or when things break
- Always run `pixi install` after pulling to sync the environment
- GDAL: `vector convert` is format-only. Use `vector reproject -d EPSG:xxxx` for CRS changes
- DuckDB spatial: `INSTALL spatial; LOAD spatial;` (or use duckdb skill with state.sql)
- Always validate GeoParquet: `pixi run gpio check all <file>`
- Each workspace may use a different language. Check its `pixi.toml` first
