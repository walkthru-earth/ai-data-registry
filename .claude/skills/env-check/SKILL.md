---
name: env-check
description: >
  Validate project environment health. Use before running pipelines, after setup,
  or when tools fail unexpectedly. Checks pixi, DuckDB, GDAL, gpio, extensions,
  state.sql, and cross-tool compatibility.
allowed-tools: Bash, Read, Glob
---

Run the validation script:

```bash
pixi run python -c "
import subprocess, sys

def run(cmd):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return r.returncode == 0, r.stdout.strip().split('\n')[0] if r.stdout.strip() else ''
    except Exception:
        return False, ''

print('=' * 48)
print('  ai-data-registry — Environment Check')
print('=' * 48)
print()

checks = [
    ('pixi installed',           'pixi --version'),
    ('pixi.lock exists',         'test -f pixi.lock'),
    ('pixi.lock fresh',          'pixi run python -c \"import pathlib; exit(0 if pathlib.Path(\\\"pixi.lock\\\").stat().st_mtime >= pathlib.Path(\\\"pixi.toml\\\").stat().st_mtime else 1)\"'),
    ('DuckDB available',         'pixi run duckdb --version'),
    ('GDAL available',           'pixi run gdal --version'),
    ('gpio available',           'pixi run gpio --version'),
    ('GDAL Parquet driver',      'pixi run gdal vector info --formats 2>/dev/null | grep -qi parquet'),
    ('DuckDB spatial',           'pixi run duckdb -c \"INSTALL spatial; LOAD spatial; SELECT 1;\"'),
    ('DuckDB httpfs',            'pixi run duckdb -c \"INSTALL httpfs; LOAD httpfs; SELECT 1;\"'),
    ('DuckDB fts',               'pixi run duckdb -c \"INSTALL fts; LOAD fts; SELECT 1;\"'),
]

passed = failed = 0
for name, cmd in checks:
    ok, out = run(cmd)
    ver = f' ({out})' if ok and out else ''
    print(f'  {\"OK\" if ok else \"FAIL\":4s}  {name}{ver}')
    passed += ok
    failed += (not ok)

# state.sql check
import pathlib
sf = pathlib.Path('.claude/skills/duckdb/references/state.sql')
if sf.exists():
    ok, _ = run(f'pixi run duckdb -init {sf} -c \"SELECT 1;\"')
    print(f'  {\"OK\" if ok else \"FAIL\":4s}  state.sql valid')
else:
    print(f'  {\"SKIP\":4s}  state.sql (not created yet)')

print()
print(f'  Results: {passed} passed, {failed} failed')
print('=' * 48)
"
```

If gpio fails: install with `pixi add --pypi geoparquet-io --pre`
If extensions fail: run **duckdb** skill ([install.md](../duckdb/references/install.md) reference)
If state.sql invalid: re-initialize via **duckdb** skill ([state.md](../duckdb/references/state.md) reference)
