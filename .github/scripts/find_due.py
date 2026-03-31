# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "croniter>=6.0",
# ]
# ///
"""Scheduler: find workspaces due for extraction and dispatch to backend workflows.

Usage: uv run find_due.py [--state-file <path>] [--dry-run]

Reads all workspace pixi.toml [tool.registry] configs, evaluates cron schedules
against the last-run state, and dispatches due workspaces to their backend
workflow via the GitHub API.

State is stored as a JSON file (downloaded/uploaded as a workflow artifact).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from croniter import croniter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.registry_config import SUPPORTED_BACKENDS, discover_workspaces


DEFAULT_STATE_FILE = "scheduler-state.json"


def load_state(state_file: str) -> dict:
    """Load scheduler state from JSON file."""
    if os.path.exists(state_file):
        with open(state_file) as f:
            return json.load(f)
    return {}


def save_state(state: dict, state_file: str):
    """Save scheduler state to JSON file."""
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2, default=str)


def is_due(schedule: str, last_run: str | None, now: datetime) -> bool:
    """Check if a workspace is due for extraction based on its cron schedule."""
    if not croniter.is_valid(schedule):
        return False

    if not last_run:
        return True  # Never run before

    last_run_dt = datetime.fromisoformat(last_run)
    cron = croniter(schedule, last_run_dt)
    next_run = cron.get_next(datetime)

    return now >= next_run


def dispatch_workflow(
    workspace: str,
    backend: str,
    flavor: str,
    image: str | None = None,
    dry_run: bool = False,
) -> bool:
    """Dispatch a backend workflow via the GitHub API."""
    if backend not in SUPPORTED_BACKENDS:
        print(f"  ERROR: Unsupported backend '{backend}' for workspace '{workspace}'.")
        return False

    spec = SUPPORTED_BACKENDS[backend]
    if flavor not in spec["flavors"]:
        print(f"  ERROR: Flavor '{flavor}' not allowed for backend '{backend}' (workspace '{workspace}').")
        return False

    workflow = spec["workflow"]

    # Build dispatch inputs
    inputs = {"workspace": workspace}
    if backend == "hetzner":
        inputs["server_type"] = flavor
    elif backend == "huggingface":
        inputs["flavor"] = flavor
        if image:
            inputs["image"] = image
        else:
            print(f"  ERROR: HuggingFace backend requires 'image' for workspace '{workspace}'.")
            return False

    if dry_run:
        print(f"  DRY RUN: Would dispatch {workflow} with inputs: {inputs}")
        return True

    # Dispatch via GitHub API
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")

    if not token or not repo:
        print(f"  ERROR: GITHUB_TOKEN and GITHUB_REPOSITORY must be set.")
        return False

    import urllib.request

    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"
    data = json.dumps({"ref": "main", "inputs": inputs}).encode()

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            if resp.status in (200, 204):
                print(f"  Dispatched {workflow} for '{workspace}' (flavor: {flavor}).")
                return True
            else:
                print(f"  ERROR: Dispatch failed with status {resp.status}.")
                return False
    except urllib.error.HTTPError as e:
        print(f"  ERROR: Dispatch failed: {e.code} {e.reason}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Find due workspaces and dispatch extraction")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE, help="Path to state JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Print dispatches without executing")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    state = load_state(args.state_file)
    workspaces = discover_workspaces()

    if not workspaces:
        print("No workspaces found.")
        return

    print(f"Checking {len(workspaces)} workspace(s) at {now.isoformat()}...")

    dispatched = 0
    skipped = 0
    errors = 0

    for ws in workspaces:
        name = ws["name"]
        registry = ws.get("registry")

        if not registry:
            print(f"  {name}: No [tool.registry] section, skipping.")
            skipped += 1
            continue

        schedule = registry.get("schedule", "")
        if not schedule:
            print(f"  {name}: No schedule defined, skipping.")
            skipped += 1
            continue

        ws_state = state.get(name, {})
        last_run = ws_state.get("last_run")

        if not is_due(schedule, last_run, now):
            skipped += 1
            continue

        runner = registry.get("runner", {})
        backend = runner.get("backend", "github")
        flavor = runner.get("flavor", SUPPORTED_BACKENDS.get(backend, {}).get("flavors", [""])[0])
        image = runner.get("image")

        print(f"  {name}: DUE (last run: {last_run or 'never'}, schedule: {schedule})")

        success = dispatch_workflow(
            workspace=name,
            backend=backend,
            flavor=flavor,
            image=image,
            dry_run=args.dry_run,
        )

        if success:
            state[name] = {
                "last_run": now.isoformat(),
                "status": "dispatched",
                "snapshot": ws_state.get("snapshot"),
            }
            dispatched += 1
        else:
            errors += 1

    save_state(state, args.state_file)

    print(f"\nSummary: {dispatched} dispatched, {skipped} skipped, {errors} errors.")

    if errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
