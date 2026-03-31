# Multi-Workspace Rules

This is a multi-workspace mono-repo. Each sub-workspace is an isolated pixi environment under `workspaces/` with its own `pixi.toml`, deps, and tasks. All workspaces share a single `pixi.lock` at the root.

**Naming distinction:**
- **Workspace** = the directory (`workspaces/weather/`) providing an isolated pixi environment
- **Schema** = the data namespace in `[tool.registry].schema`, used as S3 prefix and DuckLake schema

A working example lives at `workspaces/test-minimal/`.

## Architecture

```
ai-data-registry/
├── pixi.toml              # Root — shared tools (GDAL, DuckDB, pnpm, Python)
├── pixi.lock              # Single lock file for ALL workspaces
├── workspaces/
│   ├── test-minimal/      # Example workspace (reference implementation)
│   │   ├── pixi.toml      # [workspace] + [tool.registry] + deps + tasks
│   │   ├── extract.py     # Extraction script (writes to $OUTPUT_DIR)
│   │   └── validate_local.py
│   ├── weather/
│   │   └── pixi.toml
│   └── sanctions/
│       └── pixi.toml
```

## Creating a New Sub-Workspace

Use `/project:new-workspace <name> <language>` or manually:

```bash
# 1. Create directory under workspaces/
mkdir -p workspaces/<name>

# 2. Initialize (from project root)
cd workspaces/<name>
pixi init . --channel conda-forge --platform osx-arm64 --platform linux-64 --platform win-64
cd ../..

# 3. Register in root workspace
pixi workspace register --name <name> --path workspaces/<name>

# 4. Add language runtime and dependencies (using -w flag from root)
pixi add -w <name> python                    # or go, nodejs, rust, etc.
pixi add -w <name> <workspace-specific-deps>

# 5. Delete the workspace-level pixi.lock (root lock covers all)
rm workspaces/<name>/pixi.lock

# 6. Verify registration
pixi workspace register list
```

## Separation of Concerns
- **Never** add workspace-specific deps to the root `pixi.toml` — each workspace owns its own
- Each workspace may use a different language (Python, Go, Node, Rust, etc.)
- Always check the workspace's `pixi.toml` to understand its runtime before making changes

## Shared Root Tools
The root `pixi.toml` provides tools available to all workspaces:
- `pixi run duckdb` — DuckDB CLI
- `pixi run gdal` — unified GDAL CLI
- `pixi run gpio` — GeoParquet CLI
- `pixi run python` — Python runtime
- `pixi run pnpm` — Node package manager (NEVER npm)
- `pixi run node` — Node.js runtime

## Running Commands

### Shared tools (from root)
```bash
pixi run duckdb -csv -c "SELECT 42"
pixi run gdal info input.gpkg
```

### Workspace tasks (from root, using -w flag)
```bash
pixi run -w <workspace> <task>
```

### Adding dependencies
```bash
# To a specific workspace (from root):
pixi add -w <workspace> <pkg>
pixi add -w <workspace> --pypi <pkg>

# To root (shared tools):
pixi add <pkg>
```

## Advanced Task Patterns

### Environment variables in tasks
```toml
[tasks]
serve = { cmd = "python app.py", env = { DB_PATH = "$CONDA_PREFIX/data/db.duckdb" } }
```

### Task dependencies (chaining)
```toml
[tasks]
extract = "python scripts/extract.py"
transform = { cmd = "python scripts/transform.py", depends-on = ["extract"] }
load = { cmd = "python scripts/load.py", depends-on = ["transform"] }
pipeline = { depends-on = ["extract", "transform", "load"] }
```

### Platform-specific dependencies
```toml
[dependencies]
gdal = ">=3.12.3,<4"

[target.unix.dependencies]
libduckdb = ">=1.5.1,<2"

[target.osx-arm64.dependencies]
mac-specific-tool = ">=1.0"

[target.win-64.dependencies]
win-specific-tool = ">=1.0"
```

### Working directory for tasks
```toml
[tasks]
test = { cmd = "pytest", cwd = "tests/" }
```

### Task arguments
```toml
[tasks.greet]
cmd = "echo Hello"
args = [{ arg = "name", default = "World" }]
```

## Workspace Registration
```bash
pixi workspace register --name <name> --path <name>   # Register
pixi workspace register list                            # List all
pixi workspace register remove <name>                   # Unregister
pixi workspace register prune                           # Clean stale entries
```
