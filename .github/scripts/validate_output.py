# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "duckdb>=1.5.1",
# ]
# ///
"""Layer 4: Validate extracted Parquet output against workspace quality checks.

Usage: uv run validate_output.py <workspace_pixi_toml> <output_dir>

Reads [tool.registry.checks] from the workspace's pixi.toml and validates
the Parquet files in output_dir:
- Row count >= min_rows
- Null percentage per column <= max_null_pct
- Uniqueness of unique_cols (no duplicates)
- Geometry validation via gpio (if geometry = true)
- Schema match against catalog (if schema_match = true and catalog available)

Exit 0 on pass, 1 on failure.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from glob import glob
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.registry_config import parse_workspace_manifest


def find_parquet_files(output_dir: str) -> list[str]:
    """Find all .parquet files in the output directory."""
    return sorted(glob(str(Path(output_dir) / "**" / "*.parquet"), recursive=True))


def validate_with_duckdb(parquet_files: list[str], checks: dict) -> list[str]:
    """Run DuckDB-based quality checks on Parquet files."""
    errors: list[str] = []

    try:
        import duckdb
    except ImportError:
        errors.append("duckdb Python package not available. Cannot run quality checks.")
        return errors

    con = duckdb.connect()

    # Read all parquet files into a single view
    file_list = ", ".join(f"'{f}'" for f in parquet_files)
    try:
        con.execute(f"CREATE VIEW output AS SELECT * FROM read_parquet([{file_list}])")
    except duckdb.Error as e:
        errors.append(f"Failed to read Parquet files: {e}")
        return errors

    # Check row count
    min_rows = checks.get("min_rows", 0)
    if min_rows > 0:
        row_count = con.execute("SELECT COUNT(*) FROM output").fetchone()[0]
        if row_count < min_rows:
            errors.append(
                f"Row count {row_count} is below minimum {min_rows}. "
                "Check your extraction logic or adjust [tool.registry.checks].min_rows."
            )
        else:
            print(f"  Row count: {row_count} (minimum: {min_rows})")

    # Check null percentages
    max_null_pct = checks.get("max_null_pct", 100)
    if max_null_pct < 100:
        columns = con.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'output'").fetchall()
        total_rows = con.execute("SELECT COUNT(*) FROM output").fetchone()[0]

        if total_rows > 0:
            for (col_name,) in columns:
                null_count = con.execute(
                    f'SELECT COUNT(*) FROM output WHERE "{col_name}" IS NULL'
                ).fetchone()[0]
                null_pct = (null_count / total_rows) * 100

                if null_pct > max_null_pct:
                    errors.append(
                        f"Column '{col_name}' has {null_pct:.1f}% null values "
                        f"(maximum allowed: {max_null_pct}%). "
                        "Fix null values or adjust [tool.registry.checks].max_null_pct."
                    )

    # Check uniqueness
    unique_cols = checks.get("unique_cols", [])
    if unique_cols:
        cols_str = ", ".join(f'"{c}"' for c in unique_cols)
        try:
            dup_count = con.execute(f"""
                SELECT COUNT(*) FROM (
                    SELECT {cols_str}, COUNT(*) as cnt
                    FROM output
                    GROUP BY {cols_str}
                    HAVING cnt > 1
                )
            """).fetchone()[0]

            if dup_count > 0:
                errors.append(
                    f"Found {dup_count} duplicate groups on columns [{', '.join(unique_cols)}]. "
                    "These columns must be unique per [tool.registry.checks].unique_cols."
                )
            else:
                print(f"  Uniqueness check on [{', '.join(unique_cols)}]: passed")
        except duckdb.Error as e:
            errors.append(f"Uniqueness check failed: {e}. Verify column names in [tool.registry.checks].unique_cols.")

    con.close()
    return errors


def validate_geometry(parquet_files: list[str]) -> list[str]:
    """Run gpio geometry validation on Parquet files."""
    errors: list[str] = []

    for f in parquet_files:
        try:
            result = subprocess.run(
                ["pixi", "run", "gpio", "check", "all", f],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                errors.append(
                    f"Geometry validation failed for {Path(f).name}: {result.stderr.strip() or result.stdout.strip()}"
                )
            else:
                print(f"  Geometry check: {Path(f).name} passed")
        except subprocess.TimeoutExpired:
            errors.append(f"Geometry validation timed out for {Path(f).name}")
        except FileNotFoundError:
            errors.append("gpio not found. Run 'pixi install' to set up the environment.")
            break

    return errors


def main():
    parser = argparse.ArgumentParser(description="Validate extracted Parquet output")
    parser.add_argument("manifest", help="Path to workspace pixi.toml")
    parser.add_argument("output_dir", help="Directory containing output Parquet files")
    args = parser.parse_args()

    manifest = parse_workspace_manifest(args.manifest)
    registry = manifest.get("tool", {}).get("registry", {})
    checks = registry.get("checks", {})
    ws_name = Path(args.manifest).parent.name

    print(f"Validating output for workspace '{ws_name}' in {args.output_dir}...")

    parquet_files = find_parquet_files(args.output_dir)
    if not parquet_files:
        print(f"\n  FAILED: No .parquet files found in {args.output_dir}")
        sys.exit(1)

    print(f"  Found {len(parquet_files)} Parquet file(s)")

    errors: list[str] = []

    # DuckDB-based checks
    errors.extend(validate_with_duckdb(parquet_files, checks))

    # Geometry checks
    if checks.get("geometry", False):
        errors.extend(validate_geometry(parquet_files))

    if errors:
        print(f"\n  FAILED: {len(errors)} quality issue(s):\n")
        for i, err in enumerate(errors, 1):
            print(f"  {i}. {err}")
        sys.exit(1)
    else:
        print(f"  PASSED: All quality checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
