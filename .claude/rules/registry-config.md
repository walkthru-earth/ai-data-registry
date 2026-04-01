---
paths:
  - ".github/registry.config.toml"
---
# Registry Config Rules

`.github/registry.config.toml` is the single source of truth for backend definitions and storage layout. Secret VALUES live in GitHub repo settings, not here. This file only declares which secret NAMES workflows expect.

## Structure

```toml
[storage]
catalog_prefix = ".catalogs"       # Where workspace catalogs live in S3
global_catalog = "catalog.duckdb"
staging_prefix = "pr"              # PR staging: s3://bucket/pr/{pr_number}/

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

## When Editing

- Adding a new backend: add `[backends.<name>]` with `workflow` and `flavors`
- Changing flavors: update the `flavors` array. `validate_manifest.py` reads this at runtime.
- Storage layout: `catalog_prefix`, `global_catalog`, `staging_prefix` control S3 paths
- `registry_config.py` auto-discovers backends from this file. Update `find_due.py` if dispatch inputs differ.
