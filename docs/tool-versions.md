# Shared Tool Versions

All tools are managed by the root `pixi.toml` and run via `pixi run <tool>`. Never run directly.

| Tool | Version | Command | Purpose |
|------|---------|---------|---------|
| GDAL | >=3.12.3 | `pixi run gdal ...` | Unified vector/raster CLI (v3.11+ new CLI, NOT legacy ogr2ogr/gdalinfo/ogrinfo) |
| DuckDB | >=1.5.1 | `pixi run duckdb ...` | Analytical SQL engine with spatial extension |
| gpio | 1.0.0b2 | `pixi run gpio ...` | GeoParquet optimization/validation. Install: `pixi add --pypi geoparquet-io --pre` |
| libgdal-arrow-parquet | >=3.12.3 | (GDAL driver) | Parquet I/O via Arrow. Version MUST match GDAL version |
| Python | >=3.12.13 | `pixi run python ...` | Default runtime |
| Node.js | via pixi | `pixi run node ...` | Node.js runtime |
| pnpm | >=10.32.1 | `pixi run pnpm ...` | Node package manager (NEVER npm or yarn) |
| s5cmd | >=2.3.0 | `pixi run s5cmd ...` | Parallel S3 uploads (256 workers, 12-32x faster than AWS CLI) |

## Platforms

osx-arm64, linux-64, win-64. All dependencies must be cross-platform compatible.

## Adding Dependencies

Always prefer conda-forge. Fall back to PyPI only when not available.

| Source | Root command | Workspace command | Config section |
|--------|-------------|-------------------|---------------|
| conda-forge | `pixi add <pkg>` | `pixi add -w <ws> <pkg>` | `[dependencies]` |
| PyPI | `pixi add --pypi <pkg>` | `pixi add -w <ws> --pypi <pkg>` | `[pypi-dependencies]` |

**Decision flow:**
1. `pixi search <pkg>`. If found, use conda-forge
2. If not on conda-forge, use `--pypi`
3. Never add the same package from both sources
4. Use `>=X.Y,<Z` version constraints (not `*` or pinned exact)
