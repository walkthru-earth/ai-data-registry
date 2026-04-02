# Contributing: Add a Workspace / Pipeline

Fork the repo, create a workspace under `workspaces/`, open a PR. The CI validates everything automatically.

Working example: `workspaces/test-minimal/` (minimal reference implementation)

## Quick Start

Use `/new-workspace <name> <language>` for guided setup, or manually:

```bash
mkdir -p workspaces/<name>
cd workspaces/<name>
pixi init . --channel conda-forge --platform osx-arm64 --platform linux-64 --platform win-64

pixi add python                  # or go, nodejs, rust, etc.
pixi add <other-deps>
pixi add --pypi <pypi-dep>       # PyPI fallback only
cd ../..
```

Each workspace is a standalone pixi project with its own `pixi.toml` and committed `pixi.lock`. No registration needed.

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
table = "table_name"          # single table (or use tables = [...])
mode = "append"               # append | replace | upsert
storage = "eu-hetzner"        # optional, defaults to first defined storage
# storage = ["eu-hetzner", "us-east"]  # replicate to multiple storages

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

### Multi-Table Workspaces

When your extract produces multiple output files, declare `tables` instead of `table`. Each declared table corresponds to a `<table>.parquet` file in `$OUTPUT_DIR/`.

```toml
[tool.registry]
tables = ["states", "flights"]

# Per-table checks override global defaults
[tool.registry.checks]
schema_match = true

[tool.registry.checks.states]
min_rows = 1000
geometry = true
unique_cols = ["icao24", "snapshot_time"]

[tool.registry.checks.flights]
min_rows = 0
geometry = false
unique_cols = ["icao24", "first_seen"]
optional = true        # don't fail if file is missing
```

The CI workflow handles S3 organization automatically. Each table gets its own timestamped subdirectory: `s3://bucket/{owner}/{repo}/{branch}/{schema}/{table}/{timestamp}.parquet`.

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

- `extract` writes one `<table_name>.parquet` per declared table to `$OUTPUT_DIR/`
- Do NOT hardcode `OUTPUT_DIR` in pixi task `env` (breaks CI)
- `pipeline` is the runner entry point: `pixi run --manifest-path workspaces/{name}/pixi.toml pipeline`
- `dry-run` produces sample output for PR validation
- Chain stops on any non-zero exit

### MUST NOT

1. Write to S3 directly (workflow uploads via s5cmd on your behalf)
2. Declare a `schema.table` that conflicts with another workspace
3. Bundle credentials in code (use `$WORKSPACE_SECRET_*` env vars, see `.env.example` for local setup)
4. Declare unsupported backends or flavors
5. Include infrastructure configs (Terraform, provisioning scripts)
6. Hardcode `OUTPUT_DIR` in pixi task `env`
7. Use output filenames that don't match a declared table name

## Workspace Isolation

- Never add workspace-specific deps to root `pixi.toml`
- Never assume a workspace uses Python. Check its `pixi.toml` first
- Never share state between workspaces (each has its own `.pixi/envs/`)
- GeoParquet is the interchange format when workspaces share data

## Adding Dependencies

Always prefer conda-forge. Fall back to PyPI only when not available.

```bash
pixi search <pkg>                        # check conda-forge first
cd workspaces/<workspace>
pixi add <pkg>                           # conda-forge (default)
pixi add --pypi <pkg>                    # PyPI fallback only
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
3. **Live catalog check** - table existence, schema compatibility for appends (skipped on PRs, runs during `/run-extract`)
4. **Dry run** - `pixi install` + `pixi run --manifest-path workspaces/{name}/pixi.toml dry-run` + output validation

All 4 must pass before merge. A maintainer may also trigger `/run-extract` for full end-to-end testing against a staging S3 prefix.
