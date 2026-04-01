---
paths:
  - "workspaces/**"
  - "pixi.toml"
---
# Multi-Workspace Isolation Rules

This is a multi-workspace mono-repo. Each sub-workspace under `workspaces/` is an isolated pixi environment with its own `pixi.toml`, deps, and tasks. All share a single `pixi.lock` at root.

**Naming distinction:**
- **Workspace** = the directory (`workspaces/weather/`) providing an isolated pixi environment
- **Schema** = the data namespace in `[tool.registry].schema`, used as S3 prefix and DuckLake schema

Reference implementation: `workspaces/test-minimal/`

## Isolation Rules

- **Never** add workspace-specific deps to root `pixi.toml`. Each workspace owns its own.
- Each workspace may use a different language. Check its `pixi.toml` first.
- Never share state between workspaces.
- GeoParquet is the interchange format when workspaces share data.

## Workspace Registration

Registration is **machine-local** (`~/.pixi/workspaces.toml`, not in git). Each developer must register after cloning. CI registers explicitly.

```bash
pixi workspace register --name <name> --path workspaces/<name>
pixi workspace register list
```

Do NOT add a `members` key to `[workspace]` in root `pixi.toml` (not valid in pixi v0.66.0).

## Running Commands

- Shared tools: `pixi run <tool>` from root
- Workspace tasks: `pixi run -w <workspace> <task>` from root
- Add deps: `pixi add -w <workspace> <pkg>` (conda-forge) or `--pypi <pkg>` (fallback)

For workspace creation steps, see @CONTRIBUTING.md.
