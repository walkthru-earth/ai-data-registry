# Maintaining: Infrastructure, CI/CD, and DuckLake

This guide covers everything maintainers manage: DuckLake federation, S3 storage, CI workflows, runner backends, and scheduling. Contributors never touch these systems directly.

Full architecture: `research/architecture.md`

## DuckLake Federation

- One SQLite catalog per workspace on S3 (`s3://registry/.catalogs/{name}.ducklake`)
- One global catalog (`s3://registry/catalog.ducklake`) assembled by merge queue
- Zero-copy: global catalog stores pointers to workspace Parquet files via `ducklake_add_data_files()`
- No PostgreSQL. SQLite works because only one process writes to each catalog at a time

### Global Catalog Merge (serial, concurrency: 1)

1. Downloads all workspace catalogs + global from S3
2. Diffs `ducklake_list_files()` between each workspace and global
3. Registers only NEW files via `ducklake_add_data_files()` (zero-copy, metadata only)
4. Uploads updated global catalog to S3

Dedup is mandatory. `ducklake_add_data_files` has no built-in duplicate detection. Same file registered twice = duplicate rows.

### Compaction Safety

Global catalog must NOT run compaction. It would DELETE workspace Parquet files and replace them with copies in the global DATA_PATH.

```sql
CALL global.set_option('auto_compact', false);
```

Run compaction only on individual workspace catalogs (which own their data paths). Weekly maintenance (`maintenance.py`) handles this automatically via CHECKPOINT with `expire_older_than = 30 days` and `delete_older_than = 7 days`.

## S3 Layout

```
s3://registry/
    .catalogs/                    # DuckLake SQLite catalogs
        weather.ducklake          # Workspace-owned
        census.ducklake
    catalog.ducklake              # Global (merge-queue-owned)
    weather/                      # Workspace data prefix (= schema)
        observations/
            year=2026/
                ducklake-abc123.parquet
    pr/                           # PR staging (ephemeral, auto-cleaned)
        42/
            weather/...
        42.ducklake               # PR-scoped catalog (optional)
```

**Rule:** `schema` in pixi.toml = S3 prefix = workspace write boundary.

## S3 Write Isolation

Workspace code gets READ-ONLY S3 credentials. It writes Parquet to local `$OUTPUT_DIR/`. The workflow (not workspace code) has WRITE creds and uploads via `s5cmd`.

Exception: HuggingFace backend passes S3 write creds to the container (accepted trade-off, containers run on external infra without workflow-level upload steps).

## Registry Configuration

`.github/registry.config.toml` defines backends, flavors, and secret names:

```toml
[storage]
catalog_prefix = ".catalogs"       # Where workspace catalogs live in S3
global_catalog = "catalog.ducklake"
staging_prefix = "pr"              # PR staging: s3://bucket/pr/{pr_number}/{schema}/
# Secret names (values in GitHub Secrets, not here):
# S3_ENDPOINT_URL, S3_BUCKET, S3_REGION, S3_WRITE_KEY_ID, S3_WRITE_SECRET

[backends.github]
workflow = "extract-github.yml"
flavors = ["ubuntu-latest"]

[backends.hetzner]
workflow = "extract-hetzner.yml"
flavors = ["cax11", "cax21", "cax31", "cax41"]
# Secrets: HCLOUD_TOKEN, RUNNER_PAT

[backends.huggingface]
workflow = "extract-huggingface.yml"
flavors = ["cpu-basic", "cpu-upgrade", "t4-small", "t4-medium", "l4x1", "a10g-small", "a10g-large", "a10g-largex2", "a100-large"]
# Secrets: HF_TOKEN
```

## CI Workflows

| Workflow | Trigger | What it does |
|----------|---------|-------------|
| `pr-validate.yml` | PR open/sync to main | 4-layer validation (static, collision, catalog, dry-run) |
| `pr-extract.yml` | `/run-extract` comment or `workflow_dispatch` | Full extraction to staging S3 prefix |
| `pr-cleanup.yml` | PR close or `workflow_dispatch` | Deletes staging data under `s3://bucket/pr/{pr_number}/` |
| `extract-github.yml` | Scheduler dispatch or `workflow_dispatch` | Runs workspace on free GitHub runner |
| `extract-hetzner.yml` | Scheduler dispatch or `workflow_dispatch` | Create-run-delete ephemeral Hetzner ARM server |
| `extract-huggingface.yml` | Scheduler dispatch or `workflow_dispatch` | Submit to HF Jobs API (GPU/Docker) |
| `merge-catalog.yml` | Post-extraction success or `workflow_dispatch` | Serial merge of workspace catalogs into global |
| `scheduler.yml` | Cron (every 15 min) or `workflow_dispatch` | Reads schedules, dispatches due workspaces by backend |
| `maintenance.yml` | Cron (Sunday 3 AM UTC) or `workflow_dispatch` | Weekly CHECKPOINT on workspace catalogs |
| `build-image.yml` | Push to main with Dockerfile changes | Build + push Docker image to GHCR for HF workspaces |
| `template-setup.yml` | One-time template init | Replaces placeholders, deletes itself |

## CI Scripts

All scripts in `.github/scripts/` run via **uv** with PEP 723 inline deps (not pixi):

```bash
uv run .github/scripts/<script>.py
```

| Script | Purpose | Used by |
|--------|---------|---------|
| `registry_config.py` | Shared config module: loads `registry.config.toml`, discovers workspaces, validates names, defines valid licenses and required fields | All scripts |
| `validate_manifest.py` | Layer 1: static analysis of `[tool.registry]` (fields, cron, backend/flavor, SPDX licenses, required tasks). HF special case: requires `image` field | `pr-validate.yml` |
| `check_collisions.py` | Layer 2: `schema.table` uniqueness across all workspaces | `pr-validate.yml` |
| `check_catalog.py` | Layer 3: downloads global catalog from S3, checks table existence and schema compatibility. Gracefully skips when S3 creds unavailable (fork PRs) | `pr-validate.yml` |
| `validate_output.py` | Layer 4: validates Parquet output (min rows, max null pct, unique cols, gpio geometry check) | `pr-validate.yml`, `pr-extract.yml` |
| `merge_catalog.py` | Downloads workspace + global catalogs, diffs file lists, registers new files via `ducklake_add_data_files()`, uploads updated global. Sets `auto_compact = false` on global. Uses `allow_missing => true, ignore_extra_columns => true` for schema drift tolerance | `merge-catalog.yml` |
| `find_due.py` | Evaluates workspace cron schedules against state file (workflow artifact), dispatches backend workflows. Builds per-backend inputs (workspace, server_type/flavor/image). `--dry-run` and `--state-file` flags | `scheduler.yml` |
| `maintenance.py` | Lists all workspace catalogs from S3, runs CHECKPOINT with `expire_older_than = 30 days`, `delete_older_than = 7 days`, cleans orphaned files. `--dry-run` flag | `maintenance.yml` |
| `submit_hf_job.py` | Submits container job to HF Jobs API via `huggingface_hub.run_job()`, passes S3 creds + workspace secrets, polls status every 30s (max 2h timeout) | `extract-huggingface.yml` |
| `test_local_merge.py` | Local DuckLake merge test (no S3 needed). Simulates batch generation, catalog creation, incremental merge, and CHECKPOINT. Run: `uv run .github/scripts/test_local_merge.py` | Development |

## CI Gotchas

- **`issue_comment` workflows run from `main`, not the PR branch.** Changes to workflow files must merge to `main` before `/run-extract` uses them.
- **PR cleanup can race with pr-extract.** If `/run-extract` uploaded data after merge, clean up manually: `gh workflow run pr-cleanup.yml --field pr_number=<N>`
- **SQL identifiers with hyphens need double-quoting.** `"test-minimal"` not `test-minimal` in DuckDB SQL.
- **Fork PRs skip Layer 3** (no S3 credentials). The script handles this gracefully.
- **Scheduler state** lives as a workflow artifact (`scheduler-state.json`), not in git. First run has no state.

### Debugging CI Failures

```bash
gh run list --workflow "PR Validation" --limit 5
gh run watch <run-id> --exit-status
gh run view <run-id> --log-failed

# Manual triggers
gh workflow run pr-extract.yml --field pr_number=1 --field workspace=my-workspace
gh workflow run pr-cleanup.yml --field pr_number=1
gh workflow run extract-github.yml --field workspace=my-workspace
gh workflow run merge-catalog.yml --field workspace=my-workspace
gh workflow run maintenance.yml   # manual maintenance run
```

## Runner Backends (Maintainer-Managed)

Contributors pick backend + flavor. Maintainers own all infrastructure, credentials, and workflow files.

| Responsibility | Maintainer | Contributor |
|---------------|-----------|-------------|
| Runner workflows (`extract-*.yml`) | Creates and maintains | Uses as-is |
| Cloud credentials and secrets | Provisions and rotates | Never touches |
| Supported backend list | Decides availability | Picks from list |
| Flavor/machine sizing | Sets allowed flavors | Requests within range |
| Docker images (HF) | Owns base images, `build-image.yml` | Extends via Dockerfile in workspace |

### GitHub Backend (`backend = "github"`)

Direct `ubuntu-latest`. No infra to manage. 30-minute timeout. Simplest option.

### Hetzner Backend (`backend = "hetzner"`)

Three-job pattern via `Cyclenerd/hcloud-github-runner@v1`: create, work, delete (always). Ephemeral ARM servers. 60-minute timeout.

| Flavor | Cores | RAM | Disk |
|--------|-------|-----|------|
| `cax11` | 2 ARM | 4 GB | 40 GB |
| `cax21` | 4 ARM | 8 GB | 80 GB |
| `cax31` | 8 ARM | 16 GB | 160 GB |
| `cax41` | 16 ARM | 32 GB | 320 GB |

**Security:** `RUNNER_PAT` needs minimum scope (`actions:write`). Add cleanup cron for stale servers (tagged `github-runner-*` older than 2h). `post-cleanup: true` in setup-pixi deletes creds after job.

### HuggingFace Backend (`backend = "huggingface"`)

Docker containers on HF GPU hardware. No pixi on runner. Container IS the environment. 130-minute timeout.

- `extract` task calls `submit_hf_job.py`, not the data processing code directly
- Container writes directly to S3 (breaks write-isolation, accepted trade-off)
- Workspace needs `[tool.registry.runner].image` pointing to GHCR
- `build-image.yml` auto-builds on Dockerfile changes to main

### Adding New Backends

1. Create `extract-{backend}.yml` with lifecycle pattern (provision, run, destroy)
2. Provision credentials, add as repository secrets
3. Add backend entry to `.github/registry.config.toml`
4. `registry_config.py` auto-discovers from config. Update `find_due.py` if dispatch inputs differ
5. `validate_manifest.py` auto-validates against config

## Repository Secrets Reference

See `docs/secrets-setup.md` for the full list with setup instructions.

## Scheduling

Scheduler runs every 15 minutes on free GitHub runner. Reads `[tool.registry].schedule` from every workspace pixi.toml, compares against state file (workflow artifact, 90-day retention), dispatches runners for due workspaces.

| Frequency | Merge strategy |
|-----------|---------------|
| < 1 hour | Merge immediately after each extraction |
| Daily | Batch all daily jobs, single merge when done |
| Weekly/monthly | Merge immediately (rare, no batching benefit) |

All merge jobs share `concurrency: catalog-merge` so they never overlap.

## DuckDB Runtime Files

| File | Purpose | Load |
|------|---------|------|
| `state.sql` | Core session state (spatial, httpfs, fts extensions, read_any macro) | Auto via `-init` |
| `arcgis.sql` | ArcGIS REST macros (19 macros: catalog, layers, query, auth, etc.) | `pixi run duckdb -init ".claude/skills/duckdb/references/arcgis.sql"` |

## Esri/ArcGIS Skill Routing

| Task | Skill/Agent |
|------|------------|
| Query FeatureServer via SQL | **duckdb** (arcgis.md) |
| Download FeatureServer via CLI | **gdal** (esri-featureserver.md) |
| Read/write .gdb | **gdal** (esri-filegdb.md) |
| Deep .gdb inspection | **gdal** (esri-python-api.md) |
| ArcGIS raster services | **gdal** (esri-raster-services.md) |
| Esri CRS/date/encoding issues | **gdal** (esri-gotchas.md) |
| Build ArcGIS ingest pipeline | **pipeline-orchestrator** agent |
| Profile ArcGIS dataset | **data-explorer** agent |

## Watch Out For

- GDAL version must match libgdal-arrow-parquet version
- gpio: install via `pixi add --pypi geoparquet-io --pre` (PyPI beta)
- `pixi workspace register` is machine-local. CI must register explicitly
- Do NOT add `members` to `[workspace]` in root `pixi.toml` (not valid in pixi v0.66.0)
- Registry config lives at `.github/registry.config.toml` (backend definitions, secret names)
- All extract workflows trigger `merge-catalog.yml` on success
- `build-image.yml` triggers on Dockerfile changes pushed to main
