# Contributing: Add a Workspace / Pipeline

Fork the repo, create a workspace under `workspaces/`, open a PR. The CI validates everything automatically.

Working example: `workspaces/test-minimal/` (minimal reference implementation)

## Quick Start

Use `/new-workspace <name> <language>` for guided setup, or manually:

```bash
mkdir -p workspaces/<name>
cd workspaces/<name>
pixi init . --channel conda-forge --platform osx-arm64 --platform linux-64 --platform win-64
cd ../..
pixi workspace register --name <name> --path workspaces/<name>
rm workspaces/<name>/pixi.lock   # root lock covers all

pixi add -w <name> python        # or go, nodejs, rust, etc.
pixi add -w <name> <other-deps>
```

**Registration is machine-local** (`~/.pixi/workspaces.toml`, not committed). Each developer must run `pixi workspace register` after cloning. CI does this automatically.

## Workspace Contract

Every workspace `pixi.toml` MUST have a `[tool.registry]` section. See `.claude/rules/workspace-contract.md` for the full enforced contract.

### Required Metadata

```toml
[tool.registry]
description = "What this workspace extracts"
schedule = "0 6 * * *"
timeout = 30
tags = ["topic1", "topic2"]
schema = "unique_name"        # S3 prefix + DuckLake schema
table = "table_name"
mode = "append"               # append | replace | upsert

[tool.registry.runner]
backend = "github"            # github | hetzner | huggingface
flavor = "ubuntu-latest"      # must match backend

[tool.registry.license]
code = "Apache-2.0"           # OSI-approved SPDX
data = "CC-BY-4.0"            # recognized SPDX
data_source = "Source Name"
mixed = false

[tool.registry.checks]
min_rows = 1000
max_null_pct = 5
geometry = true
unique_cols = ["id_col"]
schema_match = true
```

### Allowed Backend + Flavor

| Backend | Flavors | GPU | Use case |
|---------|---------|-----|----------|
| `github` | `ubuntu-latest` | No | Lightweight: CSV/JSON downloads, API calls |
| `hetzner` | `cax11`, `cax21`, `cax31`, `cax41` | No | Medium: spatial processing, large downloads |
| `huggingface` | `cpu-basic`, `cpu-upgrade`, `t4-small`, `t4-medium`, `l4x1`, `a10g-small`, `a10g-large`, `a10g-largex2`, `a100-large` | Yes (except cpu-*) | GPU: ML inference, embeddings |

Need a backend not listed? Open an issue. Infrastructure is maintainer-managed.

### Required Tasks

```toml
[tasks]
extract = "python extract.py"
validate = { cmd = "python validate_local.py", depends-on = ["extract"] }
pipeline = { depends-on = ["extract", "validate"] }
dry-run = { cmd = "python extract.py", env = { DRY_RUN = "1" } }
```

- `extract` writes Parquet to `$OUTPUT_DIR/` (defaults to `output/` locally, CI overrides)
- Do NOT hardcode `OUTPUT_DIR` in pixi task `env` (breaks CI)
- `pipeline` is the runner entry point: `pixi run -w {name} pipeline`
- `dry-run` produces sample output for PR validation
- Chain stops on any non-zero exit

### MUST NOT

1. Write to S3 directly (workflow uploads via s5cmd on your behalf)
2. Declare a `schema` that conflicts with another workspace
3. Bundle credentials in code (use `$WORKSPACE_SECRET_*` env vars)
4. Declare unsupported backends or flavors
5. Include infrastructure configs (Terraform, provisioning scripts)
6. Hardcode `OUTPUT_DIR` in pixi task `env`

## Workspace Isolation

- Never add workspace-specific deps to root `pixi.toml`
- Never assume a workspace uses Python. Check its `pixi.toml` first
- Never share state between workspaces (each has its own `.pixi/envs/`)
- GeoParquet is the interchange format when workspaces share data

## Adding Dependencies

Always prefer conda-forge. Fall back to PyPI only when not available.

```bash
pixi search <pkg>                        # check conda-forge first
pixi add -w <workspace> <pkg>            # conda-forge (default)
pixi add -w <workspace> --pypi <pkg>     # PyPI fallback only
```

- Conda packages go in `[dependencies]`, PyPI in `[pypi-dependencies]`
- Never add the same package from both sources
- Version constraints: `>=X.Y,<Z` format

## License Requirements

- `code` must be OSI-approved SPDX (Apache-2.0, MIT, etc.)
- `data` must be recognized SPDX (CC-BY-4.0, CC0-1.0, ODbL-1.0, etc.)
- `mixed = true` requires per-source `sources` array
- Restrictive licenses (CC-BY-NC) trigger a warning label, not a block

## PR Validation (4 Layers)

Your PR goes through automatic checks:

1. **Static analysis** - required fields, SPDX licenses, cron syntax, naming, backend/flavor, tasks
2. **Collision detection** - schema.table unique across all workspaces, no S3 prefix overlap
3. **Live catalog check** - table existence, schema compatibility for appends
4. **Dry run** - `pixi install` + `pixi run -w {name} dry-run` + output validation

All 4 must pass before merge. A maintainer may also trigger `/run-extract` for full end-to-end testing against a staging S3 prefix.

## Useful Commands

| Command | What it does |
|---------|-------------|
| `/new-workspace <name> <lang>` | Scaffold workspace with full contract |
| `/add-dep <pkg> [-w workspace]` | Add dependency (conda preferred, PyPI fallback) |
| `/run-in <workspace> <task>` | Run a pixi task in a workspace |
| `/inspect-file <path>` | Inspect any data file (schema, rows, spatial info) |
| `/convert <in> <out>` | Convert between geospatial formats |
