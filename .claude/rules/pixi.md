---
paths:
  - "pixi.toml"
  - "pixi.lock"
  - "**/pixi.toml"
  - "**/pixi.lock"
---
# Pixi Package Manager Rules

## Dependencies
- **Always prefer conda-forge** — use `pixi add <pkg>` (goes in `[dependencies]`)
- **Fall back to PyPI only** when not on conda-forge — use `pixi add --pypi <pkg>` (goes in `[pypi-dependencies]`)
- Check availability first: `pixi search <pkg>` — if found, use conda; if not, use `--pypi`
- Never add the same package from both sources
- Never edit `pixi.lock` manually — it is auto-generated
- Version constraints: use `>=X.Y,<Z` format (not `*` or pinned exact versions)
- Channel: conda-forge (do not add other channels without explicit approval)
- Supported platforms: osx-arm64, linux-64, win-64

## Platform-Specific Dependencies
```toml
[target.unix.dependencies]
libduckdb = ">=1.5.1,<2"

[target.osx-arm64.dependencies]
mac-only-tool = ">=1.0"

[target.win-64.dependencies]
win-only-tool = ">=1.0"
```

## Tasks
- Define tasks in `[tasks]` section of `pixi.toml`, not in Makefiles or shell scripts
- Run via `pixi run <task>` to ensure correct environment
- Chain tasks with `depends-on`:
  ```toml
  [tasks]
  build = "make build"
  test = { cmd = "pytest", depends-on = ["build"] }
  ```
- Use environment variables in tasks:
  ```toml
  [tasks]
  serve = { cmd = "python app.py", env = { DB_PATH = "$CONDA_PREFIX/data/db.duckdb" } }
  ```
- Use `cwd` for working directory: `{ cmd = "pytest", cwd = "tests/" }`
- Use `args` for parameterized tasks: `args = [{ arg = "name", default = "World" }]`

## Workspace Commands

**Important:** Workspace registrations are machine-local (stored in `~/.pixi/workspaces.toml`, not in `pixi.toml`). Do NOT add a `members` key to root `pixi.toml`. CI workflows register workspaces explicitly.

```bash
pixi workspace register --name <name> --path workspaces/<name>  # Register sub-workspace
pixi workspace register list                                     # List all registered
pixi workspace register remove <name>                            # Unregister
pixi workspace register prune                                    # Clean stale entries
```

## Running Tools
- Run shared tools via pixi: `pixi run duckdb`, `pixi run gdal`, `pixi run gpio`, `pixi run pnpm`
- Never run tools directly without `pixi run`
- See `.claude/rules/tool-execution.md` for complete execution patterns
- See `.claude/rules/workspaces.md` for multi-workspace dependency rules
