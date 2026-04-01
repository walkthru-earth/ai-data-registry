---
paths:
  - "workspaces/**/*.py"
---
# Python Logging Rules

All workspace scripts (extract, validate) MUST use Python's `logging` module, never bare `print()` statements.

## Setup Pattern

```python
import logging
import os

logging.basicConfig(
    level=logging.DEBUG if os.environ.get("DRY_RUN") else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)
```

- Call `basicConfig()` once at module level (before any log calls)
- Use `getLogger(__name__)` for module-level logger
- `DRY_RUN` enables DEBUG level for more verbose output during PR validation

## Log Levels

| Level | When to use |
|-------|-------------|
| `DEBUG` | Batch progress, row counts mid-transform, SQL queries, internal state |
| `INFO` | Pipeline milestones: started, loaded N cities, wrote file, completed |
| `WARNING` | Anomalies that don't stop the pipeline: retries, unexpected null rates, schema drift |
| `ERROR` | Step failures that are handled: optional endpoint down, batch skipped |

## Formatting

- Use `%s`/`%d` style in log calls, NOT f-strings. Deferred formatting is skipped if the level is filtered out:
  ```python
  # Good: deferred formatting
  log.info("Loaded %d cities across %d countries", len(cities), n_countries)

  # Bad: f-string always evaluated
  log.info(f"Loaded {len(cities)} cities across {n_countries} countries")
  ```

- Use `log.exception()` inside `except` blocks. It automatically includes the traceback:
  ```python
  except Exception:
      log.exception("Flights endpoint failed")
  ```

## Required Log Points

Every extract script MUST log at minimum:

1. **Start**: mode (extract vs dry-run), key parameters
2. **Data loading**: source, row/city count loaded
3. **Progress**: batch progress for long operations (every N batches, not every row)
4. **Retries/failures**: what failed, retry attempt number, whether it was critical
5. **Output**: file path, row count, file size if available
6. **Completion**: total counts, elapsed time

## Example

```python
import logging
import os
import time

logging.basicConfig(
    level=logging.DEBUG if os.environ.get("DRY_RUN") else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

def main():
    mode = "dry-run" if os.environ.get("DRY_RUN") else "extract"
    log.info("Starting %s, output_dir=%s", mode, os.environ.get("OUTPUT_DIR", "output"))
    t0 = time.monotonic()

    # ... extraction logic ...

    log.info("Complete: %d rows in %.1fs", total, time.monotonic() - t0)
```

## Validate Scripts

Validate scripts follow the same pattern. Log each check result at INFO, failures at ERROR before asserting.

## What NOT to Do

- Never use `print()` for operational output (use `log.info()`)
- Never log secrets, API keys, or tokens
- Never log at DEBUG level per-row in production (only per-batch)
- Never suppress exceptions silently (always `log.exception()` or `log.error()`)
