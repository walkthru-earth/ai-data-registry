# ai-data-registry

Git-native, PR-driven data platform. Fork this repo, add data workspaces via PRs, and get automated validation, extraction, and catalog federation out of the box.

Each workspace is an isolated pipeline with its own language, dependencies, and compute backend. DuckLake federates all workspace outputs into one queryable global catalog on S3-compatible storage.

## How It Works

```
Contributor opens PR          Maintainer reviews
        |                           |
  4-layer validation          /run-extract (staging test)
  (static, collisions,             |
   catalog, dry-run)          Merge to main
        |                           |
  Automated feedback          Scheduler dispatches
  (no infra knowledge           to correct backend
   needed)                          |
                              Output uploaded to S3
                                    |
                              Catalog merge (DuckLake)
                                    |
                              Queryable via global catalog
```

**Workspace** = isolated pixi environment under `workspaces/` (own deps, tasks, scripts)

**Schema** = data namespace in `[tool.registry].schema` (S3 prefix + DuckLake schema)

## Quick Start

### Prerequisites

| Tool | Install | Purpose |
|------|---------|---------|
| **Pixi** | [pixi.sh](https://pixi.sh) (`brew install pixi` or `curl -fsSL https://pixi.sh/install.sh \| bash`) | Package manager, workspace environments |
| **Claude Code** (recommended) | [claude.ai/code](https://claude.ai/code) (`brew install --cask claude-code`) | AI-assisted workspace scaffolding |

### Setup

```bash
# Clone and install
git clone <your-fork-url>
cd ai-data-registry
pixi install

# Verify
pixi run duckdb --version
pixi run gdal --version
```

### Create Your First Workspace

Use the slash command for guided setup:
```
/new-workspace my-pipeline python
```

Or manually:
```bash
mkdir -p workspaces/my-pipeline
cd workspaces/my-pipeline
pixi init . --channel conda-forge --platform osx-arm64 --platform linux-64 --platform win-64
cd ../..
pixi workspace register --name my-pipeline --path workspaces/my-pipeline
rm workspaces/my-pipeline/pixi.lock   # root lock covers all workspaces
pixi add -w my-pipeline python
```

**Note:** `pixi workspace register` stores the mapping in `~/.pixi/workspaces.toml` (machine-local, not committed to git). Each developer must run it after cloning. CI workflows register workspaces automatically before running tasks.

See `workspaces/test-minimal/` for a working reference implementation.

## Project Structure

```
ai-data-registry/
├── pixi.toml                  # Root, shared tools (GDAL, DuckDB, gpio, s5cmd)
├── pixi.lock                  # Single lock file for ALL workspaces
├── .github/
│   ├── registry.config.toml   # Backend definitions, storage secret names
│   ├── scripts/               # CI scripts (run via uv, PEP 723 inline deps)
│   └── workflows/             # Validation, extraction, scheduling, maintenance
├── workspaces/
│   └── test-minimal/          # Example workspace (reference implementation)
│       ├── pixi.toml           # [workspace] + [tool.registry] + deps + tasks
│       ├── extract.py          # Extraction script (writes to $OUTPUT_DIR)
│       └── validate_local.py   # Local validation
├── research/
│   └── architecture.md        # Full platform architecture
└── .claude/                   # AI rules, skills, agents, commands
```

## Workspace Contract

Every workspace must declare its pipeline metadata in `[tool.registry]` inside its `pixi.toml`:

```toml
[workspace]
name = "my-pipeline"
channels = ["conda-forge"]
platforms = ["osx-arm64", "linux-64", "win-64"]
version = "0.1.0"

[dependencies]
python = ">=3.12,<4"

[tasks]
extract = "python extract.py"
validate = { cmd = "python validate_local.py", depends-on = ["extract"] }
pipeline = { depends-on = ["extract", "validate"] }
dry-run = { cmd = "python extract.py", env = { DRY_RUN = "1" } }

[tool.registry]
description = "What this pipeline extracts"
schedule = "0 6 * * *"          # cron expression
timeout = 30                    # minutes
tags = ["topic"]
schema = "my-pipeline"          # S3 prefix + DuckLake schema
table = "data"                  # DuckLake table name
mode = "append"                 # append | replace | upsert

[tool.registry.runner]
backend = "github"              # github | hetzner | huggingface
flavor = "ubuntu-latest"

[tool.registry.license]
code = "Apache-2.0"
data = "CC-BY-4.0"
data_source = "Source Name"

[tool.registry.checks]
min_rows = 100
max_null_pct = 5
unique_cols = ["id"]
```

**Key rules:**
- `extract` writes Parquet files to `$OUTPUT_DIR/` (defaults to `output/` locally, CI sets a temp dir)
- Do NOT hardcode `OUTPUT_DIR` in task `env` (breaks CI override)
- `pipeline` is the entry point runners call: `pixi run -w my-pipeline pipeline`
- `dry-run` is what PR validation calls (sample output only)
- Never write to S3 directly. Workflows handle uploads via s5cmd.

## Compute Backends

Workspaces pick a backend + flavor in `[tool.registry.runner]`. Maintainers manage all infrastructure.

| Backend | Flavors | Use case |
|---------|---------|----------|
| **github** | `ubuntu-latest` | Lightweight: API calls, CSV/JSON downloads |
| **hetzner** | `cax11`, `cax21`, `cax31`, `cax41` | Medium: spatial processing, large datasets (ephemeral ARM servers) |
| **huggingface** | `cpu-basic`, `cpu-upgrade`, `t4-small`, `t4-medium`, `l4x1`, `a10g-small`, `a10g-large`, `a10g-largex2`, `a100-large` | GPU: ML inference, embeddings (Docker containers) |

## PR Validation (4 layers)

When a PR touches `workspaces/**`, automated validation runs:

| Layer | What it checks | Secrets needed |
|-------|---------------|----------------|
| **1. Static analysis** | `[tool.registry]` fields, runner backend+flavor, SPDX licenses, cron syntax, required tasks | None |
| **2. Collision detection** | `schema.table` uniqueness across all workspaces | None |
| **3. Catalog compatibility** | Table existence and schema compatibility in global DuckLake catalog | S3 (skips gracefully without) |
| **4. Dry run** | Runs `pixi run -w {name} dry-run`, validates Parquet output (row count, nulls, uniqueness) | None |

Layers 1-2 work on fork PRs without any secrets. Contributors get clear error messages with fix instructions.

### PR Staging Extraction

After validation passes, a maintainer can trigger a full extraction against the PR branch:

```
/run-extract              # auto-detects changed workspaces
/run-extract my-pipeline  # specific workspace
```

This uploads data to `s3://bucket/pr/{pr_number}/{schema}/` (staging prefix, never production). A PR comment is posted with DuckDB query examples to inspect the staged data. Staging data is auto-cleaned when the PR is closed or merged.

## Fork Setup (Maintainer)

### 1. Configure storage

Edit `.github/registry.config.toml` with your backend definitions. The defaults work for most setups.

### 2. Set repository secrets

Go to **Settings > Secrets and variables > Actions** and add:

**Required (all backends):**

| Secret | Description | Example |
|--------|-------------|---------|
| `S3_ENDPOINT_URL` | S3-compatible endpoint | `https://fsn1.your-objectstorage.com` |
| `S3_BUCKET` | Bucket name | `my-registry` |
| `S3_REGION` | Region (optional) | `fsn1` |
| `S3_WRITE_KEY_ID` | S3 access key (write) | |
| `S3_WRITE_SECRET` | S3 secret key | |

**Hetzner backend (if using):**

| Secret | Description |
|--------|-------------|
| `HCLOUD_TOKEN` | Hetzner Cloud API token |
| `RUNNER_PAT` | GitHub PAT with `repo` scope (for self-hosted runner registration) |

**HuggingFace backend (if using):**

| Secret | Description |
|--------|-------------|
| `HF_TOKEN` | HuggingFace API token |

**Per-workspace (optional):**

| Secret | Description |
|--------|-------------|
| `WS_{name}_API_KEY` | Workspace-specific API key (e.g., `WS_weather_API_KEY`) |

Or use the CLI:
```bash
gh secret set S3_ENDPOINT_URL --body "https://fsn1.your-objectstorage.com"
gh secret set S3_BUCKET --body "my-registry"
gh secret set S3_WRITE_KEY_ID --body "<your-key>"
gh secret set S3_WRITE_SECRET --body "<your-secret>"
```

### 3. Verify

Push a commit and open a PR that adds a workspace under `workspaces/`. The PR validation workflow should trigger automatically.

## CI Tooling

Two runtimes with clear separation:

- **pixi** runs workspace pipelines and shared tools (`pixi run -w {name} pipeline`, `pixi run s5cmd`)
- **uv** runs CI helper scripts in `.github/scripts/` using PEP 723 inline deps (`uv run .github/scripts/validate_manifest.py`). No lock file, no extra environment.

CI scripts in `.github/scripts/`:

| Script | Purpose | Layer |
|--------|---------|-------|
| `validate_manifest.py` | Static analysis of workspace contract | PR Layer 1 |
| `check_collisions.py` | Schema.table uniqueness | PR Layer 2 |
| `check_catalog.py` | Live catalog compatibility | PR Layer 3 |
| `validate_output.py` | Parquet quality checks | PR Layer 4 |
| `find_due.py` | Scheduler: evaluate cron, dispatch workflows | Scheduling |
| `merge_catalog.py` | DuckLake catalog federation | Post-extract |
| `maintenance.py` | Weekly CHECKPOINT on workspace catalogs | Maintenance |
| `submit_hf_job.py` | HuggingFace Jobs submission | HF backend |
| `test_local_merge.py` | Local DuckLake merge test (no S3 needed) | Testing |

## Shared Tools

| Tool | Command | Purpose |
|------|---------|---------|
| GDAL (>=3.12.3) | `pixi run gdal ...` | Unified vector/raster CLI (v3.11+) |
| DuckDB (>=1.5.1) | `pixi run duckdb ...` | Analytical SQL engine |
| gpio | `pixi run gpio ...` | GeoParquet optimization/validation |
| s5cmd (>=2.3.0) | `pixi run s5cmd ...` | Parallel S3 uploads |
| Python (>=3.12) | `pixi run python ...` | Default runtime |
| pnpm | `pixi run pnpm ...` | Node package manager |

All tools run through `pixi run`. Never run them directly.

## Claude Code Ecosystem

This repo includes a full AI-assisted development setup in `.claude/`:

- **7 rules** (auto-loaded context for tool execution, pixi, workspaces, DuckDB, geospatial, Node.js, workspace contract)
- **13 skills** (GDAL, DuckDB, GeoParquet, data pipelines, spatial analysis, Playwright, etc.)
- **3 agents** (data-explorer, data-quality, pipeline-orchestrator)
- **7 slash commands** (`/new-workspace`, `/env-info`, `/query`, `/add-dep`, `/run-in`, `/inspect-file`, `/convert`)

Contributors can use Claude Code to scaffold contract-compliant workspaces, debug pipelines, and explore data without needing to understand the infrastructure.

## Architecture

Full design documentation: [`research/architecture.md`](research/architecture.md)

Key concepts:
- **DuckLake** federates workspace catalogs (SQLite on S3) into one global catalog via `ducklake_add_data_files()` (zero-copy file registration)
- **Serial merge queue** (`concurrency: { group: catalog-merge, cancel-in-progress: false }`) ensures one writer to the global catalog
- **Scheduler** runs every 15 minutes, evaluates cron schedules, and dispatches to the correct backend workflow
- **Maintenance** runs weekly, compacts workspace catalogs via CHECKPOINT

## License

CC BY 4.0 - [Walkthru.Earth](https://walkthru.earth) - See [LICENSE](LICENSE)
