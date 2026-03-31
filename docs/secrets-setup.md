# Repository Secrets Setup

Go to **Settings > Secrets and variables > Actions** in your GitHub repository.

## Required (All Backends)

| Secret | Description | Example |
|--------|-------------|---------|
| `S3_ENDPOINT_URL` | S3-compatible endpoint | `https://fsn1.your-objectstorage.com` |
| `S3_BUCKET` | Bucket name | `my-registry` |
| `S3_REGION` | Region (optional for some providers) | `fsn1` |
| `S3_WRITE_KEY_ID` | S3 access key with write permission | |
| `S3_WRITE_SECRET` | S3 secret key | |

## Hetzner Backend

| Secret | Description |
|--------|-------------|
| `HCLOUD_TOKEN` | Hetzner Cloud API token |
| `RUNNER_PAT` | GitHub PAT with `actions:write` scope (self-hosted runner registration). Prefer GitHub App token for automatic rotation. |

## HuggingFace Backend

| Secret | Description |
|--------|-------------|
| `HF_TOKEN` | HuggingFace API token with job submission access |

## Per-Workspace Secrets (Optional)

Pattern: `WS_{WORKSPACE_NAME}_API_KEY`

| Secret | Description |
|--------|-------------|
| `WS_weather_API_KEY` | API key for the `weather` workspace |
| `WS_sanctions_API_KEY` | API key for the `sanctions` workspace |

Workspace code accesses these via `$WORKSPACE_SECRET_API_KEY` environment variable (injected by the extract workflow).

## CLI Setup

```bash
gh secret set S3_ENDPOINT_URL --body "https://fsn1.your-objectstorage.com"
gh secret set S3_BUCKET --body "my-registry"
gh secret set S3_WRITE_KEY_ID --body "<your-key>"
gh secret set S3_WRITE_SECRET --body "<your-secret>"

# Hetzner (if using)
gh secret set HCLOUD_TOKEN --body "<your-token>"
gh secret set RUNNER_PAT --body "<your-pat>"

# HuggingFace (if using)
gh secret set HF_TOKEN --body "<your-token>"

# Per-workspace (optional)
gh secret set WS_weather_API_KEY --body "<api-key>"
```
