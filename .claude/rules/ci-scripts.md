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
- Loads `.github/registry.config.toml` (named storage targets, backends, flavors)
- Discovers workspaces by scanning `workspaces/*/pixi.toml`
- Resolves multi-storage configs and per-storage credentials
- Builds S3 paths with `{owner}/{repo}/{branch}/` prefix (from GitHub env vars)
- Provides `load_storage_configs()`, `get_workspace_storages()`, `build_s3_root()`, `build_global_catalog_path()` helpers
- Validates workspace names (regex: `^[a-z][a-z0-9-]*$`)
- Provides `quote_ident()` and `quote_literal()` for DuckDB SQL escaping (used by all scripts)
- Validates S3 path inputs: repo prefix format, branch traversal (`..`), numeric PR numbers
- Defines valid licenses, required fields, required tasks, valid modes

## CI Scripts

| Script | Purpose |
|--------|---------|
| `registry_config.py` | Shared config: multi-storage, path builders, workspace discovery |
| `validate_manifest.py` | Layer 1: static analysis (fields, cron, backend, SPDX, tasks) |
| `check_collisions.py` | Layer 2: schema.table uniqueness |
| `check_catalog.py` | Layer 3: live DuckLake catalog compatibility |
| `validate_output.py` | Layer 4: per-table Parquet quality checks |
| `upload_output.py` | Multi-storage upload with owner/repo/branch prefix |
| `merge_catalog.py` | Scan S3, register new files in global catalog (--storage flag) |
| `find_due.py` | Scheduler: evaluate cron vs state, dispatch backends |
| `maintenance.py` | Weekly CHECKPOINT on global catalog (with compaction) |
| `submit_hf_job.py` | HuggingFace Jobs: submit container, poll status |
| `test_local_merge.py` | Local DuckLake merge test (no S3) |

## Key Workflow Patterns

- `merge-catalog.yml` triggers via `workflow_run` when any extract completes, plus a 10-min cron backstop. Runs `--all` to merge every pending workspace
- `concurrency: catalog-merge` serializes global catalog writes (1 running + 1 pending, safe because `--all` merges everything)
- `concurrency: extract-{workspace}` prevents parallel extractions
- Hetzner: three-job pattern (create, work, delete always)
- HF: container writes directly to S3 (no workflow upload step)
- Scheduler state: workflow artifact (`scheduler-state.json`), not git
- `issue_comment` workflows run from `main`, not the PR branch

Security rules for CI scripts and workflows are in `.claude/rules/ci-security.md` (loaded on the same paths).

For full workflow and infrastructure details, see @MAINTAINING.md.
