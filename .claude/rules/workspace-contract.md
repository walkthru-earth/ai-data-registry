---
paths:
  - "workspaces/**/pixi.toml"
---
# Workspace Contract Rules

When creating or editing a workspace `pixi.toml`, enforce the data registry contract.
Working example: `workspaces/test-minimal/pixi.toml`

## Naming

- **Workspace** = the directory (e.g. `workspaces/test-minimal/`)
- **Schema** = `[tool.registry].schema`, the DuckLake schema and S3 prefix. Must be unique across all workspaces.

## Required `[tool.registry]` Section

```toml
[tool.registry]
description = "What this workspace extracts"
schedule = "0 6 * * *"        # cron
timeout = 30                  # minutes
tags = ["topic1", "topic2"]
schema = "unique_name"        # S3 prefix + DuckLake schema
mode = "append"               # append | replace | upsert
storage = "eu-hetzner"        # optional, defaults to first defined storage
# storage = ["eu-hetzner", "us-east"]  # replicate to multiple storages

# Single table:
table = "table_name"
# Multiple tables:
# tables = ["states", "flights"]

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

Per-table checks override globals: `[tool.registry.checks.<table_name>]`

## Required Tasks

```toml
[tasks]
extract = "python extract.py"
validate = { cmd = "python validate_local.py", depends-on = ["extract"] }
pipeline = { depends-on = ["extract", "validate"] }
dry-run = { cmd = "python extract.py", env = { DRY_RUN = "1" } }
```

## MUST NOT

1. Write to S3 directly (workflow uploads via s5cmd)
2. Declare a `schema.table` that conflicts with another workspace
3. Bundle credentials in code (use `$WORKSPACE_SECRET_*` env vars)
4. Declare unsupported backends or flavors
5. Hardcode `OUTPUT_DIR` in pixi task `env` (CI passes its own)
6. Use output filenames that don't match a declared table name
7. Declare storage targets not defined in `.github/registry.config.toml`

For the full contract with multi-table examples and backend details, see @CONTRIBUTING.md.
