# ai-data-registry

Geospatial data processing template with a full AI-powered development ecosystem.

## What's Included

**Package Manager:** [Pixi](https://pixi.sh) for reproducible environments across osx-arm64, linux-64, win-64.

**Shared Tools (root `pixi.toml`):**
- GDAL (>=3.12.3) — new unified `gdal` CLI for vector/raster
- DuckDB (>=1.5.1) — analytical SQL engine with spatial extension
- gpio (geoparquet-io) — GeoParquet optimization, validation, STAC
- pnpm, Python 3.14, Node.js

**Claude Code Ecosystem (`.claude/`):**
- 13 skills (GDAL, DuckDB, GeoParquet, Spatial Analysis, Playwright, etc.)
- 3 agents (data-explorer, data-quality, pipeline-orchestrator)
- 6 rules (tool execution, pixi, workspaces, DuckDB, geospatial, Node.js)
- 5 slash commands (new-workspace, env-info, query, add-dep, run-in)

**Multi-Workspace Architecture:** Create isolated sub-workspaces per language/concern.

## Quick Start

### Prerequisites

#### Pixi (required)

| Platform | Command |
|----------|---------|
| **macOS** (Homebrew) | `brew install pixi` |
| **macOS / Linux** | `curl -fsSL https://pixi.sh/install.sh \| bash` |
| **Windows** (winget) | `winget install prefix-dev.pixi` |
| **Windows** (PowerShell) | `iwr -useb https://pixi.sh/install.ps1 \| iex` |

See [pixi.sh](https://pixi.sh) for more options.

#### Claude Code (recommended)

This template includes a full [Claude Code](https://docs.anthropic.com/en/docs/claude-code) ecosystem — skills, agents, rules, and slash commands for AI-assisted geospatial development.

| Platform | Command |
|----------|---------|
| **macOS / Linux** | `curl -fsSL https://claude.ai/install.sh \| bash` |
| **macOS** (Homebrew) | `brew install --cask claude-code` |
| **Windows** (PowerShell) | `irm https://claude.ai/install.ps1 \| iex` |
| **Windows** (WinGet) | `winget install Anthropic.ClaudeCode` |

> **Note:** Windows requires [Git for Windows](https://git-scm.com/download/win) installed first.

After installation, start Claude Code in your project directory:
```bash
claude
```

### From Template (GitHub)

1. Click **"Use this template"** → **"Create a new repository"**
2. Clone your new repo
3. Run the setup script:
   ```bash
   # macOS / Linux
   ./setup.sh

   # Windows (PowerShell 7+)
   .\setup.ps1
   ```
4. Or edit `template-config.json` and push — GitHub Actions will auto-configure.

### Manual Setup

```bash
# Install dependencies
pixi install

# Verify environment
pixi run duckdb --version
pixi run gdal --version
```

### Create Your First Workspace

```bash
mkdir my-workspace && cd my-workspace
pixi init . --channel conda-forge --platform osx-arm64 --platform linux-64 --platform win-64
pixi add python  # or go, nodejs, rust
cd .. && pixi workspace register --name my-workspace --path my-workspace
```

Or with Claude Code: `/project:new-workspace my-workspace python`

## Project Structure

```
├── pixi.toml              # Root — shared tools
├── CLAUDE.md              # AI instructions (team-wide)
├── .claude/
│   ├── settings.json      # Permissions
│   ├── rules/             # Auto-loaded context rules
│   ├── commands/           # /project:* slash commands
│   ├── skills/            # Auto-invoked workflows
│   └── agents/            # Specialized subagent personas
├── workspace-a/
│   └── pixi.toml          # Isolated environment
```

## Key Conventions

- **All tools via `pixi run`** — never run directly
- **pnpm only** — npm is denied
- **GeoParquet** is the standard interchange format
- **New `gdal` CLI** (v3.11+) — not legacy ogr2ogr/gdalinfo
- **Workspace isolation** — each workspace owns its own deps and tasks

## License

CC BY 4.0 - [Walkthru.Earth](https://walkthru.earth) - See [LICENSE](LICENSE)
