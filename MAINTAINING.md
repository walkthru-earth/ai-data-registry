# Maintaining: Infrastructure, CI/CD, and DuckLake

This guide covers everything maintainers manage: DuckLake federation, S3 storage, CI workflows, runner backends, and scheduling. Contributors never touch these systems directly.

Full architecture: `research/architecture.md`

## DuckLake Federation

- One global catalog (`s3://{bucket}/{owner}/{repo}/{branch}/catalog.duckdb`) is the single source of truth
- The merge script scans S3 for Parquet files and registers them directly in the global catalog via `ducklake_add_data_files()`
- No PostgreSQL. DuckDB backend works because only one process writes at a time (concurrency group)

**CRITICAL: DuckDB catalog backend, NOT SQLite.** Catalog files use `.duckdb` extension (DuckDB backend), not `.ducklake` (SQLite backend). DuckDB catalogs support remote S3/HTTPS read-only access via httpfs, enabling `ATTACH 'ducklake:s3://bucket/{owner}/{repo}/{branch}/catalog.duckdb' AS cat (READ_ONLY)` without downloading. SQLite catalogs do NOT support remote access (blocked by duckdb/ducklake#912).

### Catalog Merge Flow (serial, concurrency: 1)

For each workspace, the merge script (`merge_catalog.py`):

1. Downloads the global catalog from S3 (or creates a new one)
2. Scans S3 for Parquet files under `s3://bucket/{owner}/{repo}/{branch}/{schema}/{table}/*.parquet`
3. Diffs scanned files against `ducklake_list_files()` in the global catalog
4. Registers new files (mode-dependent):
   - **append**: registers all unregistered files
   - **replace**: drops old registrations, keeps only the latest timestamped file
5. Uploads the updated global catalog to S3

Dedup is mandatory. `ducklake_add_data_files` has no built-in cross-call duplicate detection. Same file registered twice = duplicate rows. The merge script diffs file lists before registering to prevent this.

**Never overwrite a registered file.** `ducklake_add_data_files` caches `file_size_bytes` and `footer_size` at registration time. If a file is later overwritten at the same S3 path (e.g., re-extracted with different data), DuckLake will use stale metadata for range requests, causing HTTP 416 errors. The extract workflow prevents this by uploading each extraction with a timestamped filename (`<timestamp>.parquet`).

### Compaction

The global catalog is the sole owner of all data files, so compaction is safe. Weekly maintenance (`maintenance.py`) runs CHECKPOINT with `expire_older_than = 30 days` and `delete_older_than = 7 days`.

## S3 Layout

All paths are prefixed with `{owner}/{repo}/{branch}/` for repo and branch isolation:

```
s3://registry/
    walkthru-earth/               # GitHub repo owner
        ai-data-registry/         # GitHub repo name
            main/                 # Git branch name
                catalog.duckdb    # Global catalog (single source of truth)
                weather/          # Workspace data prefix (= schema)
                    observations/ # Table subdirectory
                        20260401T060000Z.parquet
                        20260401T120000Z.parquet
                opensky-flights/  # Multi-table workspace
                    states/
                        20260401T000000Z.parquet
                    flights/
                        20260401T000000Z.parquet
            pr/                   # PR staging (no branch, keyed on PR number)
                42/
                    weather/...
```

**Rule:** `schema` in pixi.toml = S3 prefix = workspace write boundary.
Each table declared in the workspace gets its own subdirectory under the schema prefix. Files are timestamped by the extract workflow to prevent overwrites.

The `{owner}/{repo}/{branch}` prefix is derived from GitHub Actions env vars (`GITHUB_REPOSITORY`, `GITHUB_REF_NAME`). In local dev, these are absent and paths fall back to flat layout.

## S3 Write Isolation

Workspace code gets READ-ONLY S3 credentials. It writes Parquet to local `$OUTPUT_DIR/`. The workflow (not workspace code) has WRITE creds and uploads via `s5cmd`.

Exception: HuggingFace backend passes S3 write creds to the container (accepted trade-off, containers run on external infra without workflow-level upload steps).

## Registry Configuration

`.github/registry.config.toml` defines named storage targets, backends, flavors, and secret names:

```toml
# Named storage targets (first = default)
[storage.eu-hetzner]
provider = "hetzner"
region = "fsn1"
public = true
endpoint_url_secret = "S3_ENDPOINT_URL"
bucket_secret = "S3_BUCKET"
region_secret = "S3_REGION"
write_key_id_secret = "S3_WRITE_KEY_ID"
write_secret_key_secret = "S3_WRITE_SECRET"
catalog_prefix = ".catalogs"
global_catalog = "catalog.duckdb"
staging_prefix = "pr"

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

### Multi-Storage

Workspaces declare storage targets in `[tool.registry]`:

```toml
storage = "eu-hetzner"                  # single storage (default if omitted)
storage = ["eu-hetzner", "us-east"]     # replicate to multiple storages
```

- Data is replicated to ALL declared storages simultaneously
- Each storage has independent DuckLake catalogs (no cross-storage catalog)
- Each storage needs its own set of 5 GitHub secrets
- A future external repo can build a "super-global" catalog referencing files across storages
- Storage metadata (`provider`, `region`, `public`) is informational and available to catalogs

## CI Workflows

| Workflow | Trigger | What it does |
|----------|---------|-------------|
| `pr-validate.yml` | PR open/sync to main | 4-layer validation (static, collision, catalog, dry-run) |
| `pr-extract.yml` | `/run-extract` comment or `workflow_dispatch` | Full extraction to staging S3 prefix |
| `pr-cleanup.yml` | PR close or `workflow_dispatch` | Deletes staging data under `s3://bucket/{owner}/{repo}/pr/{pr_number}/` |
| `extract-github.yml` | Scheduler dispatch or `workflow_dispatch` | Runs workspace on free GitHub runner |
| `extract-hetzner.yml` | Scheduler dispatch or `workflow_dispatch` | Create-run-delete ephemeral Hetzner ARM server |
| `extract-huggingface.yml` | Scheduler dispatch or `workflow_dispatch` | Submit to HF Jobs API (GPU/Docker) |
| `merge-catalog.yml` | `workflow_run` (extract success) + cron (every 10 min) + `workflow_dispatch` | Scans S3, registers new files in global catalog (`--all` mode) |
| `scheduler.yml` | Cron (every 15 min) or `workflow_dispatch` | Reads schedules, dispatches due workspaces by backend |
| `maintenance.yml` | Cron (Sunday 3 AM UTC) or `workflow_dispatch` | Weekly CHECKPOINT on global catalog |
| `build-image.yml` | Push to main with Dockerfile changes | Build + push Docker image to GHCR for HF workspaces |
| `template-setup.yml` | One-time template init | Replaces placeholders, deletes itself |

## CI Scripts

All scripts in `.github/scripts/` run via **uv** with PEP 723 inline deps (not pixi):

```bash
uv run .github/scripts/<script>.py
```

| Script | Purpose | Used by |
|--------|---------|---------|
| `registry_config.py` | Shared config module: loads `registry.config.toml`, discovers workspaces, resolves multi-storage configs, builds S3 paths with owner/repo/branch prefix. Provides `quote_ident()`/`quote_literal()` for SQL escaping and path validation | All scripts |
| `validate_manifest.py` | Layer 1: static analysis of `[tool.registry]` (fields, cron, backend/flavor, SPDX licenses, required tasks). HF special case: requires `image` field | `pr-validate.yml` |
| `check_collisions.py` | Layer 2: `schema.table` uniqueness across all workspaces | `pr-validate.yml` |
| `check_catalog.py` | Layer 3: downloads global catalog from S3, checks table existence and schema compatibility. Gracefully skips when S3 creds unavailable (fork PRs) | `pr-validate.yml` |
| `validate_output.py` | Layer 4: validates Parquet output (min rows, max null pct, unique cols, gpio geometry check) | `pr-validate.yml`, `pr-extract.yml` |
| `upload_output.py` | Uploads parquet files to all declared storages with owner/repo/branch prefix via s5cmd | `extract-github.yml`, `extract-hetzner.yml` |
| `merge_catalog.py` | Scans S3 for Parquet files, diffs against global catalog, registers new files. Supports append and replace modes. `--all` mode groups by storage, downloads/uploads global catalog once per storage. `--workspace` mode for single workspace. `--storage` flag for single-storage merge | `merge-catalog.yml` |
| `find_due.py` | Evaluates workspace cron schedules against state file (workflow artifact), dispatches backend workflows. Builds per-backend inputs (workspace, server_type/flavor/image). `--dry-run` and `--state-file` flags | `scheduler.yml` |
| `maintenance.py` | Downloads global catalog, runs CHECKPOINT with compaction (`expire_older_than = 30 days`, `delete_older_than = 7 days`). `--dry-run` and `--storage` flags | `maintenance.yml` |
| `submit_hf_job.py` | Submits container job to HF Jobs API via `huggingface_hub.run_job()`, passes S3 creds + workspace secrets, polls status every 30s (max 2h timeout) | `extract-huggingface.yml` |
| `test_local_merge.py` | Local DuckLake merge test (no S3 needed). Tests append mode, replace mode, compaction, and CHECKPOINT. Run: `uv run .github/scripts/test_local_merge.py` | Development |

## Security

Security patterns are enforced across all CI workflows and scripts. Full details in `SECURITY.md`, enforced rules in `.claude/rules/ci-security.md`.

**Key principles:**
- **Env var indirection**: `${{ }}` expressions never appear directly in `run:` blocks
- **SQL escaping**: All DuckDB f-string SQL uses `quote_ident()` / `quote_literal()` from `registry_config.py`
- **Input validation**: workspace names, PR numbers, and paths validated before use
- **Credential isolation**: workspace code never receives S3 write credentials
- **Cache isolation**: pixi cache writes restricted to main branch, Docker caches scoped by ref

## CI Gotchas

- **`issue_comment` workflows run from `main`, not the PR branch.** Changes to workflow files must merge to `main` before `/run-extract` uses them.
- **PR cleanup can race with pr-extract.** If `/run-extract` uploaded data after merge, clean up manually: `gh workflow run pr-cleanup.yml --field pr_number=<N>`
- **SQL identifiers with hyphens need double-quoting.** `"test-minimal"` not `test-minimal` in DuckDB SQL.
- **Scheduler state** lives as a workflow artifact (`scheduler-state.json`), not in git. First run has no state.
- **All PRs skip Layer 3** (S3 write credentials removed from `pr-validate.yml`). Use `/run-extract` for full end-to-end testing.

### Debugging CI Failures

```bash
gh run list --workflow "PR Validation" --limit 5
gh run watch <run-id> --exit-status
gh run view <run-id> --log-failed

# Manual triggers
gh workflow run pr-extract.yml --field pr_number=1 --field workspace=my-workspace
gh workflow run pr-cleanup.yml --field pr_number=1
gh workflow run extract-github.yml --field workspace=my-workspace
gh workflow run merge-catalog.yml                                # merge all pending
gh workflow run merge-catalog.yml --field workspace=my-workspace  # merge single workspace
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

### Merge Trigger Model

Extract workflows do NOT dispatch merges directly. Instead, `merge-catalog.yml` has dual triggers:

1. **`workflow_run`**: fires automatically when any extract workflow completes successfully. Runs `--all` to merge every workspace with pending data.
2. **Cron backstop** (every 10 min): catches any merges dropped by the concurrency group's 1-pending limit. Idempotent, exits fast when nothing is pending.
3. **`workflow_dispatch`**: manual trigger. Pass `--workspace` for a single workspace, or leave empty for `--all`.

The `--all` mode groups workspaces by storage target, downloads the global catalog once per storage, merges all pending workspaces, then uploads once. This avoids redundant S3 round-trips when multiple extracts finish close together.

All merge runs share `concurrency: catalog-merge` so they never overlap. GitHub allows 1 running + 1 pending per concurrency group. Since each run merges ALL pending workspaces, dropped pending runs do not lose data.

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
- Workspaces are standalone pixi projects (own `pixi.toml` + committed `pixi.lock`). CI uses `--manifest-path` to target them
- `merge-catalog.yml` triggers automatically via `workflow_run` when any extract completes
- `build-image.yml` triggers on Dockerfile changes pushed to main
