---
paths:
  - ".github/registry.config.toml"
---
# Registry Config Rules

`.github/registry.config.toml` is the single source of truth for backend definitions and storage layout. Secret VALUES live in GitHub repo settings, not here. This file only declares which secret NAMES workflows expect.

## Structure

```toml
# Named storage targets (first = default)
[storage.eu-hetzner]
provider = "hetzner"                   # informational
region = "fsn1"                        # informational
public = true                          # whether bucket is publicly readable
endpoint_url_secret = "S3_ENDPOINT_URL"
bucket_secret = "S3_BUCKET"
region_secret = "S3_REGION"
write_key_id_secret = "S3_WRITE_KEY_ID"
write_secret_key_secret = "S3_WRITE_SECRET"
catalog_prefix = ".catalogs"
global_catalog = "catalog.duckdb"
staging_prefix = "pr"

[backends.github]
workflow = "extract-github.yml"
flavors = ["ubuntu-latest"]

[backends.hetzner]
workflow = "extract-hetzner.yml"
flavors = ["cax11", "cax21", "cax31", "cax41"]

[backends.huggingface]
workflow = "extract-huggingface.yml"
flavors = ["cpu-basic", "cpu-upgrade", "t4-small", "t4-medium", "l4x1", "a10g-small", "a10g-large", "a10g-largex2", "a100-large"]
```

## S3 Path Layout

All paths are prefixed with `{owner}/{repo}/{branch}/` derived from GitHub env vars:

```
s3://{bucket}/{owner}/{repo}/
├── {branch}/
│   ├── catalog.duckdb                           # global catalog
│   └── {schema}/{table}/{timestamp}.parquet
└── pr/{pr_number}/{workspace}/{table}.parquet   (no branch prefix)
```

## Multi-Storage

- Workspaces declare `storage = "eu-hetzner"` or `storage = ["eu-hetzner", "us-east"]`
- If omitted, the first defined storage is the default
- Data is replicated to all declared storages simultaneously
- Each storage has its own independent global DuckLake catalog
- Each storage needs its own set of 5 GitHub secrets (endpoint, bucket, region, key, secret)

## When Editing

- Adding a new storage: add `[storage.<name>]` with all required fields. Set corresponding GitHub secrets.
- Adding a new backend: add `[backends.<name>]` with `workflow` and `flavors`
- Changing flavors: update the `flavors` array. `validate_manifest.py` reads this at runtime.
- Storage layout: `catalog_prefix`, `global_catalog`, `staging_prefix` are per-storage
- `registry_config.py` auto-discovers storages and backends from this file
- Legacy flat `[storage]` format is auto-detected and treated as `[storage.default]`
