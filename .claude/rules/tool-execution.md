---
paths:
  - "workspaces/**/*.py"
  - "workspaces/**/*.js"
  - "workspaces/**/*.ts"
  - "workspaces/**/*.sql"
  - "workspaces/**/*.sh"
  - "**/pixi.toml"
---
# Tool Execution Rules

All CLI tools MUST be run through pixi. Never run directly.

| Tool | Command |
|------|---------|
| DuckDB | `pixi run duckdb` |
| GDAL | `pixi run gdal` (unified CLI, NOT legacy ogr2ogr/gdalinfo) |
| gpio | `pixi run gpio` |
| Python | `pixi run python` |
| Node.js | `pixi run node` |
| pnpm | `pixi run pnpm` (NEVER npm/yarn) |
| s5cmd | `pixi run s5cmd` |

Shared tools from root: `pixi run <tool>`. Workspace tasks: `pixi run -w <workspace> <task>`.
