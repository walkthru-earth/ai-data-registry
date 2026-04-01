# Template Setup: Create Your Own Data Registry

Use this guide if you want to create your own data registry instance from the GitHub template. This is for organizations or developers who want to run their own infrastructure, not for contributors adding workspaces to an existing registry.

**If you just want to add a workspace to an existing registry, see the [README](../README.md) instead.**

## 1. Create from Template

Click **"Use this template"** on the GitHub repository page, or:

```bash
gh repo create my-org/my-registry --template walkthru-earth/ai-data-registry --public
git clone https://github.com/my-org/my-registry
cd my-registry
```

## 2. Run Setup Script

The setup script replaces placeholder values and initializes the project for your use.

**macOS / Linux:**
```bash
./setup.sh
```

**Windows (PowerShell 7+):**
```powershell
.\setup.ps1
```

The script will prompt for:
- **Project name** (e.g., `my-geo-registry`)
- **Author name** and **email**
- **Description**
- **Version**

It then:
1. Replaces placeholders in `pixi.toml`, `CLAUDE.md`, and `.claude/` files
2. Removes template-specific files (`setup.sh`, `setup.ps1`, `template-setup.yml`)
3. Runs `pixi install` to set up the environment

## 3. Configure Secrets

### Local Development (.env)

The setup script offers to create `.env` from `.env.example` with your S3 credentials. You can also do it manually:

```bash
cp .env.example .env
# Edit .env with your S3 credentials
```

`.env` is gitignored and blocked from Claude Code reading (security). Never commit it.

### CI/CD (GitHub Secrets)

Edit `.github/registry.config.toml` if needed (defaults work for most setups), then set repository secrets.

See [secrets-setup.md](secrets-setup.md) for the full list.

**Minimum required:**
```bash
gh secret set S3_ENDPOINT_URL --body "https://fsn1.your-objectstorage.com"
gh secret set S3_BUCKET --body "my-registry"
gh secret set S3_WRITE_KEY_ID --body "<your-key>"
gh secret set S3_WRITE_SECRET --body "<your-secret>"
```

## 4. Choose Your Backends

By default, all three backends are enabled. Disable any you don't need by removing their section from `registry.config.toml`.

| Backend | Extra secrets needed | When to enable |
|---------|---------------------|---------------|
| `github` | None (uses free runners) | Always. Lightweight workspaces |
| `hetzner` | `HCLOUD_TOKEN`, `RUNNER_PAT` | Medium compute. Spatial processing |
| `huggingface` | `HF_TOKEN` | GPU workloads. ML inference |

## 5. Verify

```bash
# Install environment
pixi install

# Check tools
pixi run duckdb --version
pixi run gdal --version

# Create your first workspace
# (or use Claude Code: /new-workspace my-pipeline python)
mkdir -p workspaces/my-pipeline
cd workspaces/my-pipeline
pixi init . --channel conda-forge --platform osx-arm64 --platform linux-64 --platform win-64
cd ../..
pixi workspace register --name my-pipeline --path workspaces/my-pipeline
rm workspaces/my-pipeline/pixi.lock
pixi add -w my-pipeline python
```

Open a PR with the new workspace. The PR validation workflow should trigger automatically.

## 6. Commit

```bash
git add -A
git commit -m "Initialize my-registry from template"
git push
```

## What the Template Includes

- **11 GitHub Actions workflows** (validation, extraction, scheduling, maintenance, Docker builds)
- **10 CI scripts** (validation layers, catalog merge, scheduler, HF job submission)
- **3 compute backends** (GitHub free, Hetzner ARM, HuggingFace GPU)
- **DuckLake federation** (per-workspace catalogs, global catalog, zero-copy merge)
- **Claude Code integration** (8 rules, 7 commands, 6 skills, 3 agents)
- **Reference workspace** (`workspaces/test-minimal/`)

## Template-Specific Files

These files exist only for template setup and are deleted by the setup script:

| File | Purpose |
|------|---------|
| `setup.sh` | macOS/Linux setup script |
| `setup.ps1` | Windows PowerShell setup script |
| `.env.example` | Template for local development secrets |
| `template-config.json` | Default placeholder values |
| `.github/workflows/template-setup.yml` | One-time automated setup (deletes itself) |
