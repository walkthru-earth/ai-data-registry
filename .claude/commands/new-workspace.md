---
description: Create a new data registry workspace with isolated environment and full contract compliance
argument-hint: <name> <language: python|go|node|rust>
disable-model-invocation: true
allowed-tools: Bash(pixi:*), Bash(mkdir:*), Bash(git:*), Read, Write, Edit
---
Create a new sub-workspace named `$0` with language `$1` in this mono-repo.

Follow the workspace rules in `.claude/rules/workspaces.md` and `.claude/rules/workspace-contract.md`.

## Steps

1. **Parse and validate arguments** - extract workspace name and language from `$ARGUMENTS`
   - Name MUST match `^[a-z][a-z0-9-]*$` (lowercase alphanumeric and hyphens only, starts with letter)
   - Reject names containing `/`, `..`, `_`, spaces, or shell metacharacters (`;`, `$`, `(`, `)`, `{`, `}`, `|`, `&`, `` ` ``)
   - Language must be one of: python, go, node, rust
   - **STOP and report error if validation fails. Do not proceed.**

2. **Create and initialize the workspace** (from project root)
```bash
mkdir -p workspaces/$0
cd workspaces/$0
pixi init . --channel conda-forge --platform osx-arm64 --platform linux-64 --platform win-64
cd ../..
```

3. **Register in root workspace**
```bash
pixi workspace register --name $0 --path workspaces/$0
```

4. **Add the language runtime** (using -w flag from root)
   - python: `pixi add -w $0 python`
   - go: `pixi add -w $0 go`
   - node: `pixi add -w $0 nodejs`
   - rust: `pixi add -w $0 rust`

5. **Add workspace-specific dependencies** (ask the user what they need)
```bash
pixi add -w $0 <dep1> <dep2>
pixi add -w $0 --pypi <pypi-dep>   # only when not on conda-forge
```

6. **Ask the user for registry metadata:**
   - What does this workspace extract? (description)
   - What schedule? (cron expression, e.g., `0 6 * * *` for daily)
   - What compute backend? (`github` for lightweight, `hetzner` for medium, `huggingface` for GPU)
   - What flavor? (see allowed list per backend in workspace-contract rule)
   - What data license? (SPDX, e.g., `CC-BY-4.0`)
   - What is the data source name?
   - Schema name? (= S3 prefix, must be unique across all workspaces)
   - Table name(s)? (single string for one table, or list for multiple outputs)

7. **Generate the full pixi.toml** with `[tool.registry]` contract:

For a single-table workspace:
```toml
[tool.registry]
description = "<user input>"
schedule = "<cron>"
timeout = 30
tags = ["<tag1>", "<tag2>"]
schema = "<unique_schema>"
table = "<table_name>"
mode = "append"

[tool.registry.runner]
backend = "<github|hetzner|huggingface>"
flavor = "<flavor>"
# image = "ghcr.io/..."          # uncomment for huggingface

[tool.registry.license]
code = "Apache-2.0"
data = "<data_license>"
data_source = "<source_name>"
mixed = false

[tool.registry.checks]
min_rows = 1000
max_null_pct = 5
geometry = true
unique_cols = ["<id_col>"]
schema_match = true
```

For a multi-table workspace (extract produces multiple parquet files):
```toml
[tool.registry]
tables = ["<table_a>", "<table_b>"]

[tool.registry.checks]
schema_match = true

[tool.registry.checks.<table_a>]
min_rows = 1000
unique_cols = ["<id_col>"]
geometry = true

[tool.registry.checks.<table_b>]
min_rows = 0
unique_cols = ["<id_col>"]
geometry = false
optional = true        # don't fail if file is missing
```

Each table name must match `^[a-z][a-z0-9_]*$` and corresponds to a `<table_name>.parquet` file in `$OUTPUT_DIR/`.

8. **Delete the workspace-level pixi.lock** (root lock covers all):
```bash
rm workspaces/$0/pixi.lock
```

9. **Generate required tasks** based on language.

**IMPORTANT:** Do NOT hardcode `OUTPUT_DIR` in task `env`. CI passes its own `$OUTPUT_DIR` via the shell environment. Hardcoding it in pixi task `env` would override the CI value. The extract script should default to `output/` when `$OUTPUT_DIR` is not set.

For Python workspaces:
```toml
[tasks]
extract = "python extract.py"
validate = { cmd = "python validate_local.py", depends-on = ["extract"] }
pipeline = { depends-on = ["extract", "validate"] }
dry-run = { cmd = "python extract.py", env = { DRY_RUN = "1" } }
```

For Node workspaces:
```toml
[tasks]
extract = "node extract.js"
validate = { cmd = "node validate_local.js", depends-on = ["extract"] }
pipeline = { depends-on = ["extract", "validate"] }
dry-run = { cmd = "node extract.js", env = { DRY_RUN = "1" } }
```

For Go workspaces:
```toml
[tasks]
extract = "go run ./cmd/extract"
validate = { cmd = "go run ./cmd/validate", depends-on = ["extract"] }
pipeline = { depends-on = ["extract", "validate"] }
dry-run = { cmd = "go run ./cmd/extract", env = { DRY_RUN = "1" } }
```

10. **Create scaffold files** based on language. Use `workspaces/test-minimal/` as a reference:
   - `extract.py` (or .js/.go) - reads `$OUTPUT_DIR` (defaults to `output/`), reads `$DRY_RUN`, writes one `<table_name>.parquet` per declared table
   - `validate_local.py` - validates extracted output locally (row count, basic checks)
   - No `.gitignore` needed (root `.gitignore` covers `**/output/` and workspace `pixi.lock`)

11. **Show the final pixi.toml for review** and verify:
    - All 4 required tasks present (extract, validate, pipeline, dry-run)
    - `[tool.registry]` complete with runner, license, checks
    - Schema name unique (check other workspaces)

## Notes
- Shared tools (DuckDB, GDAL, gpio, s5cmd, pnpm) are available from root, no need to add per workspace
- Workspaces live in `workspaces/` directory
- Run workspace tasks from root: `pixi run -w $0 <task>`
- Workspace code MUST write to `$OUTPUT_DIR/`, never directly to S3
- The workflow uploads via s5cmd on the workspace's behalf
- Full contract details: `.claude/rules/workspace-contract.md`
- Full architecture: `research/architecture.md`
