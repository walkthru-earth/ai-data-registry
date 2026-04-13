# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "duckdb>=1.5.2",
# ]
# ///
"""Layer 4: Validate extracted Parquet output against workspace quality checks.

Usage: uv run validate_output.py <workspace_pixi_toml> <output_dir>

Reads [tool.registry] from the workspace pixi.toml.  For each declared table,
looks for <table>.parquet in output_dir and validates against per-table checks
(falling back to global [tool.registry.checks] when no table-specific section
exists).

Checks:
- Row count >= min_rows
- Null percentage per column <= max_null_pct
- Uniqueness of unique_cols (no duplicates)
- Geometry validation via gpio (if geometry = true)

Exit 0 on pass, 1 on failure.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.registry_config import (
    get_table_checks,
    get_tables,
    parse_workspace_manifest,
    quote_ident,
    quote_literal,
)


def validate_table_with_duckdb(parquet_path: str, table_name: str, checks: dict) -> list[str]:
    """Run DuckDB-based quality checks on a single Parquet file."""
    errors: list[str] = []

    try:
        import duckdb
    except ImportError:
        errors.append("duckdb Python package not available. Cannot run quality checks.")
        return errors

    con = duckdb.connect()

    try:
        con.execute(f"CREATE VIEW output AS SELECT * FROM read_parquet({quote_literal(parquet_path)})")
    except duckdb.Error as e:
        errors.append(f"Failed to read {parquet_path}: {e}")
        return errors

    # Row count
    min_rows = checks.get("min_rows", 0)
    row_count = con.execute("SELECT COUNT(*) FROM output").fetchone()[0]
    if min_rows > 0 and row_count < min_rows:
        errors.append(
            f"[{table_name}] Row count {row_count} is below minimum {min_rows}."
        )
    else:
        print(f"  [{table_name}] Row count: {row_count} (minimum: {min_rows})")

    # Null percentages
    max_null_pct = checks.get("max_null_pct", 100)
    if max_null_pct < 100 and row_count > 0:
        columns = con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'output'"
        ).fetchall()
        for (col_name,) in columns:
            null_count = con.execute(
                f'SELECT COUNT(*) FROM output WHERE {quote_ident(col_name)} IS NULL'
            ).fetchone()[0]
            null_pct = (null_count / row_count) * 100
            if null_pct > max_null_pct:
                errors.append(
                    f"[{table_name}] Column '{col_name}' has {null_pct:.1f}% nulls "
                    f"(max allowed: {max_null_pct}%)."
                )

    # Uniqueness
    unique_cols = checks.get("unique_cols", [])
    if unique_cols:
        cols_str = ", ".join(quote_ident(c) for c in unique_cols)
        try:
            dup_count = con.execute(f"""
                SELECT COUNT(*) FROM (
                    SELECT {cols_str}, COUNT(*) AS cnt
                    FROM output
                    GROUP BY {cols_str}
                    HAVING cnt > 1
                )
            """).fetchone()[0]
            if dup_count > 0:
                errors.append(
                    f"[{table_name}] {dup_count} duplicate groups on [{', '.join(unique_cols)}]."
                )
            else:
                print(f"  [{table_name}] Uniqueness [{', '.join(unique_cols)}]: passed")
        except duckdb.Error as e:
            errors.append(f"[{table_name}] Uniqueness check failed: {e}")

    con.close()
    return errors


def validate_geometry(parquet_path: str, table_name: str) -> list[str]:
    """Run gpio geometry validation on a Parquet file."""
    errors: list[str] = []
    try:
        result = subprocess.run(
            ["pixi", "run", "gpio", "check", "all", parquet_path],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            errors.append(
                f"[{table_name}] Geometry validation failed: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        else:
            print(f"  [{table_name}] Geometry check: passed")
    except subprocess.TimeoutExpired:
        errors.append(f"[{table_name}] Geometry validation timed out")
    except FileNotFoundError:
        errors.append("gpio not found. Run 'pixi install' to set up the environment.")
    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate extracted Parquet output")
    parser.add_argument("manifest", help="Path to workspace pixi.toml")
    parser.add_argument("output_dir", help="Directory containing output Parquet files")
    args = parser.parse_args()

    manifest = parse_workspace_manifest(args.manifest)
    registry = manifest.get("tool", {}).get("registry", {})
    tables = get_tables(registry)
    ws_name = Path(args.manifest).parent.name
    output_dir = Path(args.output_dir)

    print(f"Validating output for workspace '{ws_name}' in {output_dir}...")

    if not tables:
        print("\n  FAILED: No tables declared in [tool.registry]")
        sys.exit(1)

    all_errors: list[str] = []

    for table_name in tables:
        parquet_path = output_dir / f"{table_name}.parquet"
        checks = get_table_checks(registry, table_name)
        optional = checks.get("optional", False)

        if not parquet_path.exists():
            if optional:
                print(f"  [{table_name}] File not found (optional table, skipping)")
                continue
            all_errors.append(
                f"[{table_name}] Expected {parquet_path.name} not found in output directory."
            )
            continue

        print(f"  Validating {table_name}.parquet...")
        all_errors.extend(validate_table_with_duckdb(str(parquet_path), table_name, checks))

        if checks.get("geometry", False):
            all_errors.extend(validate_geometry(str(parquet_path), table_name))

    if all_errors:
        print(f"\n  FAILED: {len(all_errors)} quality issue(s):\n")
        for i, err in enumerate(all_errors, 1):
            print(f"  {i}. {err}")
        sys.exit(1)
    else:
        print(f"  PASSED: All quality checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
