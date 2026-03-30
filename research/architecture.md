# Data Registry Platform Architecture

A git-native, PR-driven data platform. Each workspace is an isolated data pipeline with its own language, dependencies, and compute backend. Contributors add workspaces via PRs. Maintainers manage the supported infrastructure. DuckLake federates all workspace catalogs into one queryable global catalog via zero-copy file registration.

## Design Principles

1. **Git is the source of truth** for pipeline definitions, schedules, and workspace config
2. **Pixi is the runtime** for reproducible, cross-platform, multi-language environments
3. **Each workspace owns its own DuckLake SQLite catalog** on S3 (no catalog in git, no PostgreSQL)
4. **One global catalog** is assembled by zero-copy `ducklake_add_data_files()` from workspace catalogs
5. **All catalogs live on S3**, pulled at runtime, never stored in git
6. **Free GitHub runners orchestrate**, Hetzner runners do the heavy lifting
7. **PR-based contribution** model for adding/modifying data sources (like conda-forge recipes)

---

## Architecture

```mermaid
graph TB
    subgraph "GitHub Repository (Code Only)"
        direction TB
        ROOT["pixi.toml (root)"]
        WS_A["workspaces/weather/<br/>runner: hetzner/cax11"]
        WS_B["workspaces/sanctions/<br/>runner: github"]
        WS_C["workspaces/weather-index/<br/>runner: huggingface/a10g"]
        SCHED["scheduler.yml<br/>(routes by backend)"]
        MERGE_WF["merge-catalog.yml"]
    end

    subgraph "Compute Backends (maintainer-managed)"
        direction TB
        GH["GitHub Runner<br/>(free, ubuntu-latest)"]
        HZ["Hetzner Runner<br/>(ephemeral ARM)"]
        HF["HuggingFace Jobs<br/>(GPU, Docker)"]
    end

    subgraph "Hetzner Object Storage (S3)"
        direction TB
        S3["s3://registry/{schema}/<br/>*.parquet"]
        CATS["s3://registry/.catalogs/<br/>{name}.ducklake"]
        GLOBAL["s3://registry/<br/>catalog.ducklake"]
    end

    subgraph "Free GitHub Runner"
        MQ["Merge Queue<br/>(concurrency: 1)"]
    end

    SCHED -->|"backend: github"| GH
    SCHED -->|"backend: hetzner"| HZ
    SCHED -->|"backend: huggingface"| HF

    GH & HZ & HF -->|"Parquet"| S3
    GH & HZ & HF -->|"catalog"| CATS
    GH & HZ & HF -->|"done"| MQ
    MQ -->|"diff + add_data_files()"| GLOBAL
```

---

## How It Works

### 1. Workspace Extraction (parallel, backend-dependent)

Each workspace runner (GitHub, Hetzner, or HF Jobs, depending on `[tool.registry.runner].backend`):
1. Pulls its workspace catalog from S3 (`s3://registry/.catalogs/{name}.ducklake`)
2. Attaches it as a DuckLake with `DATA_PATH 's3://registry/{schema}/'`, `META_JOURNAL_MODE 'WAL'`, `META_BUSY_TIMEOUT 500`
3. Runs `pixi run -w {name} pipeline` which chains setup → extract → validate (stops on failure)
4. Uploads the updated workspace catalog back to S3

The workspace catalog is the workspace's ground truth. It has its own snapshots, time travel, and schema evolution.

### 2. Global Catalog Merge (Free GitHub Runner, serial)

A single merge job with `concurrency: 1`:
1. Downloads ALL workspace catalogs + the global catalog from S3
2. For each workspace, diffs `ducklake_list_files()` between workspace and global
3. Registers only NEW files via `ducklake_add_data_files()` (zero-copy, metadata only)
4. Uploads the updated global catalog back to S3

```mermaid
sequenceDiagram
    participant S3 as Hetzner S3
    participant MQ as Merge Queue<br/>(concurrency: 1)

    MQ->>S3: Pull catalog.ducklake
    MQ->>S3: Pull weather.ducklake
    MQ->>S3: Pull census.ducklake

    Note over MQ: Attach global READ_WRITE<br/>Attach workspaces READ_ONLY

    MQ->>MQ: Diff weather files:<br/>ws has 5 files, global has 3<br/>= 2 new files to register

    MQ->>MQ: ducklake_add_data_files()<br/>for 2 new weather files<br/>(zero-copy, S3 paths only)

    MQ->>MQ: Diff census files:<br/>ws has 2 files, global has 2<br/>= 0 new files, skip

    MQ->>S3: Push updated catalog.ducklake
```

### Why This Works

- **No concurrent writes**: Only the merge queue writes to the global catalog, with `concurrency: 1`
- **Zero-copy**: Global catalog stores pointers to workspace Parquet files. No data duplication.
- **Incremental**: File list diff ensures only new files are registered. No duplicates.
- **Crash-safe**: If a workspace extraction fails, its catalog is unchanged and nothing enters global
- **No catalog in git**: All catalogs live on S3. Pulled at runtime, pushed after mutation.
- **No PostgreSQL**: SQLite is fine because only one process writes to each catalog at a time

### Validated Behavior (DuckDB 1.5.1 + DuckLake + SQLite)

| Behavior | Tested Result |
|----------|--------------|
| `ducklake_add_data_files()` zero-copy | Files stay in workspace S3 prefix. Global catalog stores path pointers only. |
| Incremental registration | New files can be added without re-registering old ones. |
| Duplicate risk | Same file registered twice = duplicate rows. Must diff file lists before registering. |
| Time travel on global | Each `add_data_files` call creates a new snapshot. `AT (VERSION => N)` works. |
| `COPY FROM DATABASE` between DuckLakes | Works for initial load but fails on 2nd run (`Table already exists`). Not incremental. |
| Multiple DuckLake catalogs attached | Works. Can attach N catalogs simultaneously, mix READ_ONLY and READ_WRITE. |

---

## Deduplication: The File List Diff

Since `ducklake_add_data_files` has no built-in duplicate detection, the merge queue must compute the diff:

```python
def get_new_files(ws_catalog, global_catalog, schema, table):
    """Files in workspace catalog not yet in global catalog."""
    ws_files = duckdb.sql(f"""
        SELECT data_file
        FROM ducklake_list_files('{ws_catalog}', '{table}', schema => '{schema}')
    """).fetchall()

    global_files = duckdb.sql(f"""
        SELECT data_file
        FROM ducklake_list_files('{global_catalog}', '{table}', schema => '{schema}')
    """).fetchall()

    global_set = {f[0] for f in global_files}
    return [f[0] for f in ws_files if f[0] not in global_set]
```

---

## Compaction Safety

`ducklake_add_data_files` transfers file ownership to the target catalog. If the global catalog runs compaction (`CHECKPOINT`, `merge_adjacent_files`), it could DELETE workspace Parquet files and replace them with compacted copies in the global DATA_PATH.

**Rule: exclude the global catalog from bulk maintenance.**

```sql
-- Exclude all global catalog tables from bulk maintenance calls.
-- Note: auto_compact does NOT trigger automatic compaction. DuckLake compaction is always explicit.
-- This flag controls whether tables are INCLUDED when maintenance functions (CHECKPOINT,
-- ducklake_merge_adjacent_files, etc.) are called WITHOUT specifying a table name.
CALL global.set_option('auto_compact', false);
```

Run compaction only on individual workspace catalogs (which own their data paths). The global catalog is a zero-copy index, not a data owner.

---

## S3 Layout

```
s3://registry/
    .catalogs/                         # All DuckLake SQLite catalog files
        weather.ducklake               # Workspace catalog (workspace-owned)
        census.ducklake
        osm.ducklake
    catalog.ducklake                   # Global catalog (merge-queue-owned)

    weather/                           # Workspace data prefix (= schema name)
        observations/                  # Table directory
            year=2026/
                ducklake-abc123.parquet
    census/
        population/
            ducklake-def456.parquet
    osm/
        buildings/
            ducklake-789xyz.parquet
```

**Rule: `schema` in pixi.toml = S3 prefix = workspace write boundary.** Each workspace can only write to its own prefix.

---

## Workspace Manifest (`pixi.toml`)

Each workspace carries pipeline metadata in `[tool.registry]` (pixi ignores unknown `[tool.*]` tables).

```toml
# workspaces/weather/pixi.toml
[workspace]
channels = ["conda-forge"]
name = "weather"
platforms = ["osx-arm64", "linux-64", "win-64"]

[tool.registry]
description = "Daily weather observations from national stations"
schedule = "0 6 * * *"        # cron: daily at 06:00 UTC
timeout = 30                  # minutes
tags = ["weather", "climate", "daily"]
schema = "weather"            # DuckLake schema = S3 prefix
table = "observations"        # DuckLake table name
mode = "append"               # append | replace | upsert
partition_by = "year(date)"   # optional DuckLake partitioning

[tool.registry.runner]
backend = "hetzner"           # Must be supported: github | hetzner | huggingface
flavor = "cax11"              # Must be allowed for the backend (validated in PR checks)
# image = "ghcr.io/..."       # Docker image (required for huggingface)

[tool.registry.license]
code = "Apache-2.0"           # SPDX for extraction code (OSI-approved)
data = "CC-BY-4.0"            # SPDX for output data
data_source = "National Weather Service"
mixed = false
# When mixed = true, list per-source:
# sources = [
#   { name = "NWS", license = "public-domain", url = "https://..." },
#   { name = "ECMWF", license = "CC-BY-4.0", url = "https://..." },
# ]

[tool.registry.checks]
min_rows = 1000
max_null_pct = 5
geometry = true               # gpio check all
unique_cols = ["station_id", "date"]
schema_match = true

[dependencies]
python = ">=3.12,<3.13"

[pypi-dependencies]
requests = ">=2.31"

[tasks]
# --- Required tasks (every workspace) ---
# setup: download source data, auth checks, environment prep (optional but standardized)
setup = "python scripts/setup.py"

# extract: core pipeline, writes Parquet to $OUTPUT_DIR
extract = { cmd = "python extract.py", depends-on = ["setup"], env = { OUTPUT_DIR = "output" } }

# validate: quality checks on extracted output
validate = { cmd = "python validate.py", depends-on = ["extract"] }

# --- Entry point (what the runner calls) ---
# pipeline: single entry point that chains the full lifecycle
# pixi stops the chain on any non-zero exit, so a failed extract skips validate
pipeline = { depends-on = ["setup", "extract", "validate"] }

# --- Optional tasks ---
# dry-run: PR validation mode (sample output, no full extraction)
dry-run = { cmd = "python extract.py", env = { DRY_RUN = "1", OUTPUT_DIR = "output" } }
```

### Task Lifecycle

The runner calls **one command**: `pixi run -w {name} pipeline`. Pixi's `depends-on` chains handle ordering and halt on first failure (non-zero exit). No custom hooks needed.

```
setup ──→ extract ──→ validate
                         │
                    (chain stops on any non-zero exit)
```

| Phase | Task | Required? | What it does |
|-------|------|-----------|------|
| **Setup** | `setup` | Optional | Download source data, auth checks, prep temp dirs |
| **Extract** | `extract` | Required | Core pipeline. Writes Parquet to `$OUTPUT_DIR/` |
| **Validate** | `validate` | Required | gpio checks, row counts, null pct, schema match |
| **Entry point** | `pipeline` | Required | Alias that chains setup → extract → validate |
| **PR mode** | `dry-run` | Required | Runs extract with `DRY_RUN=1` for sample output |

**Key pixi features used:**
- `depends-on`: chains tasks, stops on failure
- `env`: passes `OUTPUT_DIR`, `DRY_RUN` to tasks without hardcoding paths
- `args`: optional, for parameterized tasks (e.g., `pixi run extract --date 2026-03-30`)
- `inputs/outputs`: optional, enables caching (skip extract if source unchanged)

```toml
# Example: task with arguments (MiniJinja templating)
[tasks.extract]
cmd = "python extract.py --date {{ date }}"
args = [{ arg = "date", default = "today" }]
depends-on = ["setup"]
env = { OUTPUT_DIR = "output" }

# Example: task with caching (skip if input unchanged)
[tasks.extract]
cmd = "python extract.py"
depends-on = ["setup"]
env = { OUTPUT_DIR = "output" }
inputs = ["extract.py", "config/*.yaml"]
outputs = ["output/*.parquet"]
```

### The Contract

Every workspace MUST:
1. Have a `pixi.toml` with `[tool.registry]` metadata including `[tool.registry.license]` and `[tool.registry.runner]`
2. Declare a `[tool.registry.runner].backend` from the supported list (`github`, `hetzner`, `huggingface`)
3. Declare an allowed `flavor` for the chosen backend
4. Have a `pipeline` task as the runner entry point (chains setup → extract → validate)
5. Have an `extract` task that writes Parquet to `$OUTPUT_DIR/`
6. Have a `validate` task that checks output quality
7. Have a `dry-run` task for PR validation (sample output with `DRY_RUN=1`)
8. Declare a unique `schema.table` that no other workspace owns
9. Exit 0 on success, non-zero on failure

Every workspace MUST NOT:
1. Write to S3 directly (the workflow uploads on its behalf)
2. Declare a `schema` that conflicts with another workspace's prefix
3. Bundle credentials in code (use `$WORKSPACE_SECRET_*` env vars)
4. Declare unsupported backends or flavors (PR validation will reject)
5. Include infrastructure configs (Terraform, cloud provisioning scripts, etc.)

Any language is fine. Python, TypeScript, SQL, GDAL CLI, whatever works. The compute backend is separate from the pipeline language.

---

## S3 Write Isolation

Workspace code gets READ-ONLY S3 credentials. It writes Parquet to local `$OUTPUT_DIR`. The workflow validates output, then uploads to the correct S3 prefix on the workspace's behalf. The workspace code never gets S3 write access.

```mermaid
sequenceDiagram
    participant WF as Extract Workflow
    participant HR as Hetzner Runner
    participant S3 as Hetzner S3

    WF->>HR: Start with READ-ONLY S3 creds
    HR->>HR: pixi run -w weather extract<br/>(writes to local $OUTPUT_DIR)
    HR->>HR: Validate output<br/>(prefix, gpio, row counts)
    HR->>WF: Upload validated files as artifact
    Note over WF: Workflow has WRITE creds
    WF->>S3: Upload Parquet to s3://registry/weather/
    WF->>S3: Upload workspace catalog
```

---

## PR Pre-Merge Checks (4 Layers)

```mermaid
flowchart TD
    PR["PR: add workspaces/my-source/"] --> L1

    subgraph L1["Layer 1: Static Analysis"]
        SCHEMA_VALID["Required fields present?"]
        LICENSE["SPDX license IDs valid?"]
        CRON["Cron syntax parseable?"]
        NAME["Name: lowercase, no reserved words"]
        BACKEND["Runner backend + flavor in supported list?"]
        TASKS["Required tasks exist? (pipeline, extract, validate, dry-run)"]
    end

    L1 --> L2

    subgraph L2["Layer 2: Collision Detection"]
        TABLE["schema.table unique across all workspaces?"]
        S3_PREFIX["S3 prefix not overlapping?"]
    end

    L2 --> L3

    subgraph L3["Layer 3: Live Catalog Check"]
        EXISTS["Table exists in global catalog?<br/>Mode compatibility?"]
        COMPAT["Schema compatible for appends?"]
    end

    L3 --> L4

    subgraph L4["Layer 4: Dry Run"]
        INSTALL["pixi install"]
        EXTRACT["pixi run -w {name} dry-run"]
        VALIDATE["gpio, nulls, rows, uniqueness"]
    end

    L4 --> RESULT{Pass?}
    RESULT -->|Yes| OK["ready-to-merge label"]
    RESULT -->|No| BLOCK["Block + comment details"]
```

Layer 3 pulls the global catalog from S3 at runtime to check for conflicts. No catalog in git means the CI always works with the live state.

### Collision Rule

**One workspace owns one schema.table.** Enforced by scanning all workspace `pixi.toml` files on `main` plus the PR's changes. Two workspaces declaring the same `schema.table` is a hard block.

### License Validation

- `code` must be OSI-approved SPDX (Apache-2.0, MIT, etc.)
- `data` must be recognized SPDX (CC-BY-4.0, CC0-1.0, ODbL-1.0, etc.)
- `mixed = true` requires per-source `sources` array
- Restrictive licenses (CC-BY-NC) trigger a warning label, not a block

---

## Scheduling

The scheduler runs on a free GitHub runner via cron. It reads `[tool.registry].schedule` from every workspace's `pixi.toml`, compares against a state file (stored as workflow artifact, not in git), and dispatches Hetzner runners for due workspaces.

```json
{
  "weather": { "last_run": "2026-03-30T06:00:00Z", "status": "success", "snapshot": 42 },
  "census":  { "last_run": "2026-03-01T00:00:00Z", "status": "success", "snapshot": 38 },
  "osm":     { "last_run": "2026-03-29T00:00:00Z", "status": "failed",  "snapshot": null }
}
```

| Pattern | Cron | Example |
|---------|------|---------|
| Every 5 min | `*/5 * * * *` | Real-time feeds |
| Hourly | `0 * * * *` | Sensor data |
| Daily | `0 6 * * *` | Most datasets |
| Weekly | `0 0 * * 1` | Aggregations |
| Monthly | `0 0 1 * *` | Census, reports |

### Merge Timing

- **High-frequency** (< 1 hour): merge immediately after each extraction
- **Daily**: batch all daily jobs, single merge when batch completes
- **Weekly/monthly**: merge immediately (rare jobs, no batching benefit)

All merge jobs share `concurrency: catalog-merge` so they never overlap.

---

## Runner Backends

Not all workspaces need the same compute. A lightweight CSV download runs fine on a free GitHub runner. A GPU ML inference pipeline needs Hugging Face Jobs. A one-shot global DEM conversion needs a 360-vCPU bare-metal server.

### Maintainer vs Contributor Responsibility

**Infrastructure is maintainer-managed.** Contributors pick from a menu of supported backends. They never bring their own infrastructure, workflows, Terraform configs, or cloud credentials via PRs.

| Responsibility | Maintainer | Contributor |
|----------------|-----------|-------------|
| Runner backend workflows (`extract-*.yml`) | Creates and maintains | Uses as-is |
| Cloud credentials and secrets | Provisions and rotates | Never touches |
| Supported backend list | Decides which backends are available | Picks from the list |
| `[tool.registry.runner]` | Approves in PR review | Declares in workspace `pixi.toml` |
| Flavor/machine sizing | Sets allowed flavors per backend | Requests within allowed range |
| Docker images (HF backend) | Owns base images and build workflow | Extends via Dockerfile in workspace |
| Custom/heavy infra | Provisions on request | Opens an issue to request |

**Why:** A contributor PR should never be able to provision servers, access cloud APIs, or run Terraform with maintainer credentials. The workflow files, secrets, and runner configs are all in the maintainer's domain. The contributor only declares intent ("I need a GPU"), the maintainer's infrastructure fulfills it.

### Runner Configuration (`[tool.registry.runner]`)

Contributors declare compute needs. PR validation checks that the backend and flavor are in the supported list.

```toml
[tool.registry.runner]
backend = "hetzner"           # Must be in supported list (PR validation enforces this)
flavor = "cax11"              # Must be an allowed flavor for this backend
# image = "ghcr.io/..."       # Docker image (required for huggingface)
# gpu = true                  # hint: workspace needs GPU
```

### Supported Backends (maintainer-managed)

| Backend | `backend` | Allowed `flavor` values | GPU? | Pixi? | Cost | When to use |
|---------|-----------|------------------------|------|-------|------|-------------|
| **GitHub Free** | `github` | `ubuntu-latest` | No | Yes (setup-pixi) | $0 | Lightweight: CSV/JSON downloads, API calls, small transforms |
| **Hetzner Cloud** | `hetzner` | `cax11`, `cax21`, `cax31`, `cax41` | No | Yes (setup-pixi) | ~0.006 EUR/min | Medium: spatial processing, large downloads, moderate compute |
| **Hugging Face Jobs** | `huggingface` | `t4-medium`, `a10g-large`, `a100-large` | Yes | No (Docker) | Pay-per-use | GPU: ML inference, weather models, embeddings |

New backends (e.g., Verda bare-metal, AWS Batch) are added by the maintainer when needed, not by contributors. To request a new backend or a heavy one-shot job, open an issue.

### How the Scheduler Routes

The scheduler reads `[tool.registry.runner]` and dispatches to the corresponding maintainer-managed workflow. Unknown backends are rejected.

```python
# .github/scripts/find-due.py (simplified)
SUPPORTED_BACKENDS = {
    "github":      {"workflow": "extract-github.yml",      "flavors": ["ubuntu-latest"]},
    "hetzner":     {"workflow": "extract-hetzner.yml",     "flavors": ["cax11", "cax21", "cax31", "cax41"]},
    "huggingface": {"workflow": "extract-huggingface.yml", "flavors": ["t4-medium", "a10g-large", "a100-large"]},
}

def dispatch_workspace(ws_name, registry_config):
    runner = registry_config.get("runner", {})
    backend = runner.get("backend", "github")
    flavor = runner.get("flavor")

    if backend not in SUPPORTED_BACKENDS:
        raise ValueError(f"Workspace {ws_name}: unsupported backend '{backend}'. "
                         f"Supported: {list(SUPPORTED_BACKENDS.keys())}")

    spec = SUPPORTED_BACKENDS[backend]
    if flavor and flavor not in spec["flavors"]:
        raise ValueError(f"Workspace {ws_name}: flavor '{flavor}' not allowed for {backend}. "
                         f"Allowed: {spec['flavors']}")

    dispatch_workflow(spec["workflow"],
                      workspace=ws_name,
                      flavor=flavor or spec["flavors"][0],
                      **({k: runner[k] for k in ("image",) if k in runner}))
```

### Backend: GitHub Free (`backend = "github"`)

Simplest. No infrastructure to manage. Good for workspaces that download small files or call APIs.

```toml
# workspaces/sanctions/pixi.toml
[tool.registry.runner]
backend = "github"
flavor = "ubuntu-latest"
```

```yaml
# .github/workflows/extract-github.yml
name: Extract (GitHub Runner)
on:
  workflow_call:
    inputs:
      workspace: { required: true, type: string }

concurrency:
  group: extract-${{ inputs.workspace }}
  cancel-in-progress: false

jobs:
  extract:
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4

      - uses: prefix-dev/setup-pixi@v0.9
        with:
          cache: true

      - name: Run pipeline
        env:
          OUTPUT_DIR: ${{ runner.temp }}/output
          WORKSPACE_SECRET_API_KEY: ${{ secrets[format('WS_{0}_API_KEY', inputs.workspace)] }}
        run: pixi run -w ${{ inputs.workspace }} pipeline

      - uses: actions/upload-artifact@v4
        with:
          name: output-${{ inputs.workspace }}
          path: ${{ runner.temp }}/output/
          retention-days: 7
```

### Backend: Hetzner Cloud (`backend = "hetzner"`)

Three-job pattern via [Cyclenerd/hcloud-github-runner](https://github.com/Cyclenerd/hcloud-github-runner): **create → work → delete (always)**. Ephemeral server, destroyed after each run.

```toml
# workspaces/weather/pixi.toml
[tool.registry.runner]
backend = "hetzner"
flavor = "cax11"              # ARM 2 vCPU, 4GB, 40GB
```

```yaml
# .github/workflows/extract-hetzner.yml
name: Extract (Hetzner Runner)
on:
  workflow_call:
    inputs:
      workspace: { required: true, type: string }
      server_type: { required: false, type: string, default: "cax11" }

concurrency:
  group: extract-${{ inputs.workspace }}
  cancel-in-progress: false

jobs:
  create-runner:
    runs-on: ubuntu-latest
    outputs:
      label: ${{ steps.hcloud.outputs.label }}
      server_id: ${{ steps.hcloud.outputs.server_id }}
    steps:
      - id: hcloud
        uses: Cyclenerd/hcloud-github-runner@v1
        with:
          mode: create
          github_token: ${{ secrets.RUNNER_PAT }}
          hcloud_token: ${{ secrets.HCLOUD_TOKEN }}
          server_type: ${{ inputs.server_type }}
          location: fsn1

  extract:
    needs: create-runner
    runs-on: ${{ needs.create-runner.outputs.label }}
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@v4

      - uses: prefix-dev/setup-pixi@v0.9
        with:
          cache: true
          post-cleanup: true    # Security: delete .pixi after job on self-hosted runner

      - name: Run pipeline
        env:
          OUTPUT_DIR: ${{ runner.temp }}/output
          WORKSPACE_SECRET_API_KEY: ${{ secrets[format('WS_{0}_API_KEY', inputs.workspace)] }}
        run: pixi run -w ${{ inputs.workspace }} pipeline

      - uses: actions/upload-artifact@v4
        with:
          name: output-${{ inputs.workspace }}
          path: ${{ runner.temp }}/output/
          retention-days: 7

  delete-runner:
    needs: [create-runner, extract]
    runs-on: ubuntu-latest
    if: ${{ always() }}
    steps:
      - uses: Cyclenerd/hcloud-github-runner@v1
        with:
          mode: delete
          github_token: ${{ secrets.RUNNER_PAT }}
          hcloud_token: ${{ secrets.HCLOUD_TOKEN }}
          server_id: ${{ needs.create-runner.outputs.server_id }}
```

| Model | Cores | Arch | RAM | Disk | Use case |
|-------|-------|------|-----|------|----------|
| `cax11` | 2 | ARM | 4 GB | 40 GB | Most workspaces (default) |
| `cax21` | 4 | ARM | 8 GB | 80 GB | Medium datasets |
| `cax31` | 8 | ARM | 16 GB | 160 GB | Large spatial datasets |
| `cax41` | 16 | ARM | 32 GB | 320 GB | Heavy processing |

### Backend: Hugging Face Jobs (`backend = "huggingface"`)

For GPU workloads (ML inference, weather models, embeddings). The workspace runs inside a Docker container on HF infrastructure. No pixi on the runner. The container is the environment.

Pattern from walkthru-weather-index: GitHub Actions submits to HF Jobs API via `huggingface_hub.run_job()`, which runs the Docker image on HF GPU hardware.

```toml
# workspaces/weather-index/pixi.toml
[tool.registry.runner]
backend = "huggingface"
flavor = "a10g-large"         # A10G 24GB, 12 vCPU, 46GB RAM
image = "ghcr.io/walkthru-earth/weather-index:latest"
gpu = true
```

```yaml
# .github/workflows/extract-huggingface.yml
name: Extract (HuggingFace Jobs)
on:
  workflow_call:
    inputs:
      workspace: { required: true, type: string }
      flavor: { required: false, type: string, default: "a10g-large" }
      image: { required: true, type: string }

concurrency:
  group: extract-${{ inputs.workspace }}
  cancel-in-progress: false

jobs:
  submit:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4

      - uses: prefix-dev/setup-pixi@v0.9
        with:
          cache: true

      # Submit job to HF and wait for completion
      # The workspace's scripts/submit_hf_job.py handles HF API interaction
      - name: Submit HF Job
        env:
          HF_TOKEN: ${{ secrets.HF_TOKEN }}
          HF_JOB_NAMESPACE: ${{ github.repository_owner }}
          HF_JOB_IMAGE: ${{ inputs.image }}
          HF_JOB_FLAVOR: ${{ inputs.flavor }}
          WORKSPACE_SECRET_API_KEY: ${{ secrets[format('WS_{0}_API_KEY', inputs.workspace)] }}
          S3_BUCKET: ${{ secrets.S3_BUCKET }}
          S3_PREFIX: ${{ inputs.workspace }}
          AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          AWS_DEFAULT_REGION: ${{ secrets.AWS_DEFAULT_REGION }}
        run: pixi run -w ${{ inputs.workspace }} pipeline
```

**Key differences from pixi-native backends:**
- The `extract` task calls `submit_hf_job.py` (or similar), not the actual data processing code
- Data processing happens inside the Docker container on HF hardware
- The container writes directly to S3 (no `$OUTPUT_DIR` artifact pattern)
- The workspace needs a `build-image` workflow to build and push the Docker image on code changes
- Validation may run inside the container (post-extract step) or as a separate HF job

**HF Job flavors:**

| Flavor | GPU | VRAM | CPU | RAM | Use case |
|--------|-----|------|-----|-----|----------|
| `t4-medium` | T4 | 16 GB | 4 | 15 GB | Small inference |
| `a10g-large` | A10G | 24 GB | 12 | 46 GB | Weather models (default) |
| `a100-large` | A100 | 80 GB | 12 | 142 GB | Large models, training |

**Event-driven pattern (from walkthru-weather-index):**

HF Jobs can also be triggered by upstream data changes instead of cron schedules. A `detect-new-data.yml` workflow polls the source (e.g., NOAA S3 bucket), finds new files, and dispatches one HF Job per file:

```yaml
# detect-new-data.yml dispatches per-file
- name: Trigger pipeline for each new file
  run: |
    echo "$NEW_FILES" | while IFS= read -r file; do
      gh workflow run extract-huggingface.yml \
        --field workspace="weather-index" \
        --field noaa_file="$file"
      sleep 2
    done
```

### Adding New Backends (maintainer-only)

When a workspace needs compute that no supported backend covers (e.g., bare-metal 360-vCPU, specialized GPU clusters), the maintainer adds a new backend:

1. Create `extract-{backend}.yml` workflow with the lifecycle pattern (provision → run → destroy)
2. Provision credentials and add them as repository secrets
3. Add the backend and allowed flavors to `SUPPORTED_BACKENDS` in `find-due.py`
4. Update `validate-manifest.py` to accept the new backend in PR checks
5. Document allowed flavors and cost in this section

**Prior art for heavy/custom workloads:**
- dem-terrain used Verda Cloud (Terraform: 360 vCPU, 1.4 TB RAM, 2 TB NVMe) for a one-shot global DEM conversion
- walkthru-weather-index uses HF Jobs for recurring GPU inference
- Both patterns can be formalized into supported backends when reuse justifies it

### How the Workflow Maps to Pixi Tasks

Regardless of backend, the pixi task contract is the same. The backend determines WHERE the task runs, not WHAT it runs.

```
┌──────────────────────────────────────────────────────────────────────┐
│ Scheduler reads [tool.registry.runner].backend                       │
│                                                                      │
│  "github"      → extract-github.yml      (direct, ubuntu-latest)     │
│  "hetzner"     → extract-hetzner.yml     (create → run → delete)     │
│  "huggingface" → extract-huggingface.yml (submit to HF Jobs API)     │
│  unsupported   → rejected (PR validation catches this earlier)        │
│                                                                      │
│  All call: pixi run -w {name} pipeline                               │
│  Exception: huggingface runs pipeline inside Docker container         │
└──────────────────────────────────────────────────────────────────────┘
```

**Separation of concerns:**
- **Contributor (pixi tasks)** owns the pipeline logic (setup, extract, validate). Language-agnostic, locally testable.
- **Maintainer (GitHub workflows + infra)** owns runner backends, secrets, cloud credentials, workflow files.
- **`[tool.registry]`** is the contract between them (schedule, timeout, schema.table).
- **`[tool.registry.runner]`** is the compute request (backend + flavor from the supported list).

### setup-pixi Features Used (GitHub + Hetzner backends)

| Feature | How we use it |
|---------|--------------|
| `cache: true` | Caches `.pixi/envs` using `pixi.lock` hash. Skips install on cache hit. |
| `post-cleanup: true` | Deletes `.pixi`, pixi binary, and rattler cache after job. Prevents secret leaks on self-hosted runners. |
| `working-directory` | Not needed. We use `pixi run -w {name}` from root instead. |
| Environment variables | `setup-pixi` exports pixi env vars to `$GITHUB_ENV`, available in later steps. |

### PR Validation Workflow

PR checks always use a free GitHub runner with `dry-run`, regardless of the workspace's production backend:

```yaml
# .github/workflows/pr-validate.yml (relevant step)
- name: Dry run extraction
  env:
    OUTPUT_DIR: ${{ runner.temp }}/output
  run: pixi run -w ${{ matrix.workspace }} dry-run

- name: Validate output
  run: |
    pixi run gpio check all ${{ runner.temp }}/output/*.parquet
    # Additional checks from [tool.registry.checks]
```

---

## Forking and PRs

PRs merge **code only**, never data or catalogs. On merge, the scheduler runs the new workspace against upstream S3.

```mermaid
sequenceDiagram
    participant Fork as Contributor Fork
    participant PR as Pull Request
    participant CI as PR Validation (4 layers)
    participant Main as Main Branch
    participant Sched as Scheduler

    Fork->>Fork: Add workspaces/my-source/
    Fork->>Fork: Test locally with pixi
    Fork->>PR: Open PR (code only)
    PR->>CI: Run 4-layer validation
    CI-->>PR: Checks pass
    PR->>Main: Merge
    Main->>Sched: Next cron tick
    Sched->>Sched: Extract to upstream S3
    Sched->>Sched: Merge into global catalog
```

No SQLite merge complexity. The fork's catalog is local/disposable.

---

## Catalog Federation (Read-Time)

Any DuckDB client can attach multiple catalogs simultaneously for cross-workspace queries:

```sql
-- Attach individual workspace catalogs for direct access
-- META_JOURNAL_MODE 'WAL' and META_BUSY_TIMEOUT improve robustness for SQLite catalogs
ATTACH 'ducklake:sqlite:s3://registry/.catalogs/weather.ducklake' AS weather (
    READ_ONLY, META_JOURNAL_MODE 'WAL', META_BUSY_TIMEOUT 500
);
ATTACH 'ducklake:sqlite:s3://registry/.catalogs/census.ducklake' AS census (
    READ_ONLY, META_JOURNAL_MODE 'WAL', META_BUSY_TIMEOUT 500
);

-- Or attach the global catalog for everything
ATTACH 'ducklake:sqlite:s3://registry/catalog.ducklake' AS registry (
    READ_ONLY, META_JOURNAL_MODE 'WAL', META_BUSY_TIMEOUT 500
);

-- Cross-catalog join
SELECT w.city, c.pop
FROM weather.weather.observations w
JOIN census.census.population c ON w.city = c.country;
```

---

## Maintenance

Weekly compaction runs on **workspace catalogs only** (not the global catalog).

```yaml
# .github/workflows/maintenance.yml
name: Workspace Maintenance
on:
  schedule:
    - cron: '0 3 * * 0'  # Sunday 3 AM UTC
```

Each workspace catalog gets full maintenance via `CHECKPOINT` (v0.4+, runs all steps in order):
```sql
-- Option A: All-in-one (v0.4+). Runs: flush inlined data -> expire snapshots ->
-- merge adjacent files -> rewrite data files -> cleanup old files -> delete orphans.
-- Respects configured options (expire_older_than, delete_older_than, etc.)
USE ws;
CALL ws.set_option('expire_older_than', '30 days');
CALL ws.set_option('delete_older_than', '7 days');
CHECKPOINT;

-- Option B: Individual steps (more control)
CALL ducklake_expire_snapshots('ws', older_than => now() - INTERVAL '30 days');
CALL ducklake_merge_adjacent_files('ws');
CALL ducklake_rewrite_data_files('ws');
CALL ducklake_cleanup_old_files('ws', older_than => now() - INTERVAL '7 days');
CALL ducklake_delete_orphaned_files('ws', older_than => now() - INTERVAL '7 days');
```

The global catalog has `auto_compact = false`. It only gets rebuilt if it becomes corrupted or bloated.

---

## Cost

### 20 Workspaces, Daily Extraction (mixed backends)

| Component | Provider | Monthly Cost |
|-----------|----------|-------------|
| GitHub Actions (scheduler + merge + 5 lightweight workspaces) | GitHub Free | $0 |
| Hetzner Runners (13 workspaces x 30 days x 15 min avg) | CAX11 | ~5 EUR |
| HF Jobs (2 GPU workspaces x 30 days x 20 min avg) | HF a10g-large | ~30 EUR |
| Hetzner Object Storage (100 GB) | Hetzner | ~6.49 EUR |
| SQLite Catalogs | On S3 | $0 |
| **Total** | | **~41 EUR/mo** |

Using free GitHub runners for lightweight workspaces saves ~2 EUR/mo in Hetzner costs per workspace moved. GPU workspaces (HF Jobs) are the most expensive but avoid maintaining GPU infrastructure. Heavy one-shot jobs (DEM conversion, etc.) are handled ad-hoc by the maintainer.

---

## Directory Structure

```
ai-data-registry/
    pixi.toml                        # Root: shared tools (DuckDB, GDAL, gpio)
    pixi.lock                        # Single lock for all workspaces

    workspaces/
        weather/                     # backend: hetzner
            pixi.toml                # [tool.registry] + deps + tasks
            extract.py               # Any language
            README.md

        sanctions/                   # backend: github (lightweight)
            pixi.toml
            extract.py

        weather-index/               # backend: huggingface (GPU)
            pixi.toml
            Dockerfile               # Docker image for HF Jobs
            main.py                  # Runs inside container

        census/                      # backend: github
            pixi.toml
            extract.sql

        osm/                         # backend: hetzner
            pixi.toml
            extract.ts
            package.json

    .github/
        workflows/
            scheduler.yml            # Cron watcher, reads [tool.registry], dispatches per backend
            extract-github.yml       # Backend: free GitHub runner (ubuntu-latest)
            extract-hetzner.yml      # Backend: ephemeral Hetzner (create → run → delete)
            extract-huggingface.yml  # Backend: HF Jobs API (GPU, Docker image)
            build-image.yml          # Build + push Docker images for HF workspaces
            merge-catalog.yml        # Serial catalog merge (concurrency: 1)
            pr-validate.yml          # 4-layer PR validation (dry-run on free runner)
            maintenance.yml          # Weekly workspace compaction
        scripts/
            find-due.py              # Schedule evaluation + backend routing + validation
            merge-catalog.py         # File list diff + add_data_files
            validate-manifest.py     # pixi.toml schema + task contract + supported backend check
            check-collisions.py      # schema.table uniqueness
            check-catalog.py         # Live catalog queries
            validate-output.py       # Parquet quality checks

    .claude/                         # AI rules, skills, agents (existing)
    docs/                            # This file
```

No catalogs in git. No state files in git. Everything ephemeral is on S3 or workflow artifacts.

---

## Known DuckLake Issues (duckdb/ducklake, as of 2026-03-30)

Issues discovered during architecture research that affect this design:

| Issue | What | Impact | Our Mitigation |
|-------|------|--------|----------------|
| **#579** | `add_data_files` does not extract hive partition metadata from file paths | Partition pruning won't work for zero-copy registered files. Queries scan all files. | Accept for now. For large tables, run compaction on workspace catalog (which rewrites with proper metadata), not global. |
| **#572** | SQLite catalog bloat: 369MB metadata for 823MB data | Per-file column stats inflate the catalog. | Monitor workspace catalog sizes. If global catalog exceeds ~50MB, rebuild it fresh. |
| **#128** | SQLite concurrent writes fail | Confirmed single-writer limitation. | Already mitigated: one writer per catalog at all times. |
| **#561** | Concurrent ATTACH to same SQLite file fails | Multiple processes can't open the same .ducklake file. | Already mitigated: merge queue runs serial, workspace runners are isolated. |
| **#300** | Orphaned Parquet files on failed inserts | Runner crash = files on S3 without catalog entry. | Maintenance job can detect orphans via `ducklake_delete_orphaned_files`. |
| **#680** | Corrupt Parquet from unsafe shutdown | Runner OOM/kill leaves partial files. | Validate Parquet files before registering in global catalog. |
| **#791** | `add_data_files` fails on partitioned tables when partition column is also in the Parquet file | Cannot register partitioned files directly when partition col exists in data. | Avoid partitioning on columns present in Parquet for zero-copy registration. |
| *(roadmap)* | No catalog branching/tagging | Can't create PR preview catalogs. | On DuckLake roadmap to v1.0. Not blocking for us. |

---

## Key DuckLake Features Used

| Feature | How We Use It |
|---------|--------------|
| `ducklake_add_data_files()` | Zero-copy registration from workspace to global catalog |
| `ducklake_list_files()` | File list diff for deduplication during merge |
| Time travel | Roll back bad extractions: `AT (VERSION => N)` |
| Schema evolution | Workspaces can add columns without breaking others |
| Snapshots | Every merge creates a snapshot with audit trail |
| Partitioning | Time-series data partitioned by date |
| Data change feed | `table_changes()` for downstream CDC |
| Sorted tables | Spatial data sorted for fast queries |
| Encryption | Optional encrypted Parquet for sensitive datasets |
| `set_option('auto_compact', false)` | Exclude global catalog tables from bulk maintenance calls, preventing file deletion |
| `CHECKPOINT` | All-in-one maintenance: expire snapshots, merge files, rewrite deletes, cleanup (v0.4+) |
| `ducklake_delete_orphaned_files()` | Clean up files left by crashed writes (issue #300 mitigation) |
| Data inlining | Sub-millisecond writes for small datasets (v0.4+, threshold: 10 rows) |
| Stats-only `COUNT(*)` | Answered from metadata without scanning files (v0.4+) |

---

## Resolved Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| Catalog storage | SQLite on S3, pulled at runtime | No git bloat, no PostgreSQL, no infra. Serial merge queue handles locking. |
| Catalog topology | One SQLite per workspace + one global | Each workspace is autonomous. Global is a zero-copy federation layer. |
| Merge method | `ducklake_add_data_files` (not `COPY FROM DATABASE`) | Zero-copy, incremental, works on 2nd+ run. COPY FROM DATABASE fails on re-run and copies data. |
| Compaction | Workspace catalogs only. Global has `auto_compact = false`. | Prevents global catalog from deleting workspace-owned files on S3. |
| Schema.table ownership | One workspace owns one schema.table | Prevents corruption from multiple writers. Enforced in PR checks. |
| Licensing | Required `[tool.registry.license]` with code + data SPDX | Legal clarity. Mixed sources must declare per-source. |
| S3 write isolation | Workspace code gets READ-ONLY creds. Workflow uploads on its behalf. | Safest. No workspace can touch another's prefix or any catalog. |
| Fork strategy | PRs merge code only. Data extracted fresh on upstream after merge. | No SQLite merging. Fork catalogs are disposable. |
| State tracking | Workflow artifacts (not git) | No git commits for ephemeral state. |
| Task contract | `pipeline` entry point chains setup → extract → validate via `depends-on` | Single command for runners. Pixi stops on failure. Language-agnostic. Locally testable. |
| Runner backends | Multi-backend via `[tool.registry.runner]`: github, hetzner, huggingface. Maintainer-managed, contributor picks from supported list. | Each workspace picks compute that fits its needs. New backends added by maintainer only. PR validation enforces supported list. |
| Runner entry point | `pixi run -w {name} pipeline` (production), `pixi run -w {name} dry-run` (PR validation) | One command per mode. Workflow doesn't need to know task internals. Backend-agnostic contract. |

## Open Questions

1. **Warm runner pool for sub-hourly schedules**: Ephemeral runners add 2-3 min create/delete overhead. For workspaces with `*/5 * * * *` or hourly schedules, a pre-warmed pool (Hetzner servers kept alive with `--ephemeral` GitHub runner flag) would avoid this. Adds complexity: pool sizing, idle cost, security (post-cleanup between jobs).

2. **Cross-workspace dependencies**: Can workspace B depend on workspace A's output? Would need `depends_on = ["weather"]` in `[tool.registry]` and DAG resolution in the scheduler.

3. **Secrets per workspace**: API keys needed by some workspaces. Options: GitHub environment secrets scoped per workspace (`WS_{name}_API_KEY`), or encrypted secrets catalog for open-source contributors (DecapCMS + AES-256-GCM pattern from data-research). Single shared S3 credential with application-level prefix isolation (Hetzner doesn't support per-prefix IAM).

4. **License strictness**: Restrictive data licenses (CC-BY-NC) trigger warning or hard block? Leaning warning-only.

5. **Global catalog rebuild**: When the global catalog gets bloated (issue #572), what's the rebuild procedure? Likely: create fresh catalog, iterate all workspace catalogs, re-register all files.

6. **Iceberg as derived output**: DuckDB 1.4.0+ can read AND write Iceberg tables (requires REST catalog like Lakekeeper). Should we generate Iceberg metadata as a post-merge step for multi-engine access (Spark, Trino, Snowflake)? Options: (a) static Iceberg metadata files on S3 (Portolan pattern, no server), (b) Lakekeeper container for full DuckDB read+write Iceberg, (c) defer until external consumers need access. DuckLake vs Iceberg research completed 2026-03-30.

7. **STAC discovery layer**: Generate STAC catalog (static JSON) as a post-merge step for human/LLM-readable dataset discovery. Complementary to DuckLake/Iceberg, not a replacement. Portolan SDI already implements this pattern.

8. **GeoParquet 2.0 / Parquet native geometry**: Parquet adopted native GEOMETRY and GEOGRAPHY logical types (Feb 2026). GeoParquet 2.0 will use these instead of custom metadata. Iceberg v3 also adds native geometry types with spatial partition pruning. Timeline for adoption in our stack depends on DuckDB + GDAL + gpio support. Monitor and upgrade when libraries are ready.

---

## Prior Art and Research

This architecture was informed by:

- **walkthru-earth/walkthru-data**: Previous attempt. Per-tap DuckLake + `aws s3 cp` catalog sync. No pixi, no validation, no merge strategy. Abandoned.
- **berndsen-io/ducklake-hetzner**: DuckLake on Hetzner with PostgreSQL catalog + S3. Good pattern for `init.sql` secrets, but we avoid the PostgreSQL dependency.
- **Cyclenerd/hcloud-github-runner**: Ephemeral Hetzner runners for GitHub Actions. Three-job pattern (create → work → delete always). 97-99% cheaper than GitHub runners. Hourly billing.
- **walkthru-earth/indices/walkthru-weather-index**: GPU pipeline on Hugging Face Jobs. Event-driven (detect-new-data.yml polls NOAA S3, dispatches per-file HF Jobs). Docker image built on push. `huggingface_hub.run_job()` API for submission. A10G GPU, 2h timeout. Auto STAC catalog rebuild when caught up.
- **walkthru-earth/indices/dem-terrain**: Extreme workload on Verda Cloud via Terraform. 360 vCPU, 1.4 TB RAM, 2 TB NVMe. One-shot provisioning with startup script. Maintainer-managed, not contributor-facing. Pattern for future heavy backends if needed.
- **Guepard-Corp/gfs**: Git-like version control for databases. Interesting COW snapshot approach but no merge implementation and not applicable to SQLite catalog replication.
- **DuckLake docs** (v0.4+ stable): `ducklake_add_data_files`, `ducklake_list_files`, `set_option('auto_compact', false)`, conflict resolution, data change feed.
- **DuckLake GitHub issues**: #579 (partition metadata gap), #572 (catalog bloat), #128 (SQLite concurrency), #791 (add_data_files + partitioned tables), #300 (orphaned files), #680 (corrupt Parquet on crash).
- **Pixi docs** (v0.66): `[tool.*]` custom metadata, workspace registration, `depends-on` task chains (halt on failure), `args` with MiniJinja templating, `inputs/outputs` caching, `env` per task, `clean-env` isolation, cross-environment dependencies.
- **setup-pixi** (v0.9): GitHub Action with `pixi.lock`-based caching, `post-cleanup: true` for self-hosted runners (deletes `.pixi` + caches after job), S3 auth support, env var export to `$GITHUB_ENV`.
- **Portolan SDI** (portolan-sdi): Static Iceberg REST catalog on S3, auto STAC generation, three-layer model (STAC discovery + Iceberg/DuckLake query + GeoParquet data). Proves multi-output generation from same source files works.
- **DuckDB Iceberg extension** (v1.4.0+): Full read+write Iceberg support via REST catalogs (Lakekeeper, Polaris). Relevant as a future interop path.
- **Apache Parquet native geo types** (Feb 2026): GEOMETRY and GEOGRAPHY logical types with bounding box statistics for spatial pushdown. Foundation for GeoParquet 2.0.
- **Apache Iceberg v3** (2025): Deletion vectors, native geometry/geography types, row lineage, nanosecond timestamps. Ecosystem-wide support (AWS, Snowflake, Databricks).
- **walkthru-earth/data-research**: Secrets management architecture (single S3 credential + application-level isolation), Hetzner runner cost analysis, infrastructure security guide.
