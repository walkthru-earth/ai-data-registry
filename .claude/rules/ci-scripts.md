---
paths:
  - ".github/scripts/**/*.py"
  - ".github/workflows/**/*.yml"
  - ".github/registry.config.toml"
---
# CI Scripts and Workflows Rules

## Runtime: uv, NOT pixi

Scripts in `.github/scripts/` run via **uv** with PEP 723 inline deps. They are NOT workspace pipelines and do NOT use pixi.

```bash
uv run .github/scripts/<script>.py       # correct
# pixi run python .github/scripts/...    # WRONG
```

Each script declares its own dependencies inline (PEP 723 `# /// script` block). No lock file, no shared environment.

## Shared Config Module

`registry_config.py` is imported by all other scripts. It:
- Loads `.github/registry.config.toml` (backends, flavors, storage config)
- Discovers workspaces by scanning `workspaces/*/pixi.toml`
- Validates workspace names (regex: `^[a-z][a-z0-9-]*$`)
- Defines valid licenses (OSI-approved code, CC/CDLA/ODbL data)
- Defines required registry fields, required tasks, valid modes

## CI Scripts

| Script | Purpose | Used by |
|--------|---------|---------|
| `registry_config.py` | Shared config: loads registry.config.toml, workspace discovery, constants | All scripts |
| `validate_manifest.py` | Layer 1: static analysis (fields, cron, backend/flavor, SPDX, tasks) | `pr-validate.yml` |
| `check_collisions.py` | Layer 2: schema.table uniqueness | `pr-validate.yml` |
| `check_catalog.py` | Layer 3: live DuckLake catalog compatibility (skips without S3 creds) | `pr-validate.yml` |
| `validate_output.py` | Layer 4: Parquet quality (rows, nulls, uniqueness, gpio geometry) | `pr-validate.yml`, `pr-extract.yml` |
| `merge_catalog.py` | DuckLake merge: diff file lists, register new via `ducklake_add_data_files()` | `merge-catalog.yml` |
| `find_due.py` | Scheduler: evaluate cron vs state, dispatch backend workflows | `scheduler.yml` |
| `maintenance.py` | Weekly CHECKPOINT on workspace catalogs (expire/delete old snapshots) | `maintenance.yml` |
| `submit_hf_job.py` | HuggingFace Jobs: submit container, poll status | `extract-huggingface.yml` |
| `test_local_merge.py` | Local DuckLake merge test (no S3 needed) | Development |

## Registry Config (`registry.config.toml`)

Single source of truth for backend definitions and storage secret names. Forks edit this file and set corresponding secrets in GitHub repo settings. Secret VALUES live in GitHub, not here. This file only declares which secret NAMES workflows expect.

When editing:
- Adding a new backend: add `[backends.<name>]` with `workflow` and `flavors`
- Changing flavors: update the `flavors` array. `validate_manifest.py` reads this at runtime
- Storage layout: `catalog_prefix`, `global_catalog`, `staging_prefix` control S3 paths

## Workflows

| Workflow | Trigger |
|----------|---------|
| `pr-validate.yml` | PR open/sync to main (workspaces/ changes) |
| `pr-extract.yml` | `/run-extract` comment or `workflow_dispatch` |
| `pr-cleanup.yml` | PR close or `workflow_dispatch` |
| `extract-github.yml` | Scheduler or `workflow_dispatch` |
| `extract-hetzner.yml` | Scheduler or `workflow_dispatch` |
| `extract-huggingface.yml` | Scheduler or `workflow_dispatch` |
| `merge-catalog.yml` | Post-extraction or `workflow_dispatch` |
| `scheduler.yml` | Cron (every 15 min) or `workflow_dispatch` |
| `maintenance.yml` | Cron (Sunday 3 AM UTC) or `workflow_dispatch` |
| `build-image.yml` | Push to main with Dockerfile changes |

## Key Patterns

- All extract workflows trigger `merge-catalog.yml` on success
- `concurrency: catalog-merge` serializes global catalog writes
- `concurrency: extract-{workspace}` prevents parallel extractions of same workspace
- Hetzner: three-job pattern (create, work, delete always)
- HF: container writes directly to S3 (no workflow upload step)
- Scheduler state: workflow artifact (`scheduler-state.json`), not git
- `issue_comment` workflows run from `main`, not the PR branch
