---
paths:
  - ".github/scripts/**/*.py"
  - ".github/workflows/**/*.yml"
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
- Defines valid licenses, required fields, required tasks, valid modes
- Provides `get_tables(registry)` and `get_table_checks(registry, table_name)` helpers

## CI Scripts

| Script | Purpose |
|--------|---------|
| `registry_config.py` | Shared config: workspace discovery, table helpers |
| `validate_manifest.py` | Layer 1: static analysis (fields, cron, backend, SPDX, tasks) |
| `check_collisions.py` | Layer 2: schema.table uniqueness |
| `check_catalog.py` | Layer 3: live DuckLake catalog compatibility |
| `validate_output.py` | Layer 4: per-table Parquet quality checks |
| `merge_catalog.py` | Two-phase DuckLake merge (workspace sync, then global diff) |
| `find_due.py` | Scheduler: evaluate cron vs state, dispatch backends |
| `maintenance.py` | Weekly CHECKPOINT on workspace catalogs |
| `submit_hf_job.py` | HuggingFace Jobs: submit container, poll status |
| `test_local_merge.py` | Local DuckLake merge test (no S3) |

## Key Workflow Patterns

- All extract workflows trigger `merge-catalog.yml` on success
- `concurrency: catalog-merge` serializes global catalog writes
- `concurrency: extract-{workspace}` prevents parallel extractions
- Hetzner: three-job pattern (create, work, delete always)
- HF: container writes directly to S3 (no workflow upload step)
- Scheduler state: workflow artifact (`scheduler-state.json`), not git
- `issue_comment` workflows run from `main`, not the PR branch

For full workflow and infrastructure details, see @MAINTAINING.md.
