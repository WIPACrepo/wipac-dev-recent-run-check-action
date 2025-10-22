#!/usr/bin/env python3
"""
Outputs: ran_recently=true/false

Determines whether this workflow/job was executed recently on the same branch
for a *different commit* within the time window.
"""

import os
import json
import urllib.request
from datetime import datetime, timezone


# ────────────────────────── Utilities ──────────────────────────


def gh_api(url: str) -> dict:
    """Call the GitHub API and return parsed JSON."""
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "recent-run-check",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_utc(ts: str) -> datetime:
    """Convert a GitHub ISO8601 timestamp into a timezone-aware datetime."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def calculate_age_seconds(ts: str) -> float:
    """Return how many seconds have elapsed since the given timestamp."""
    return (datetime.now(timezone.utc) - parse_utc(ts)).total_seconds()


def get_workflow_file() -> str:
    """Extract the workflow filename from GITHUB_WORKFLOW_REF."""
    ref = os.environ["GITHUB_WORKFLOW_REF"]
    return ref.split(".github/workflows/")[1].split("@")[0]


def get_owner_repo() -> tuple[str, str]:
    """Return (owner, repo) parsed from GITHUB_REPOSITORY."""
    return tuple(os.environ["GITHUB_REPOSITORY"].split("/", 1))  # type: ignore


# ───────────────────── Workflow-level logic ─────────────────────


def get_latest_prior_different_commit_run() -> dict | None:
    """Return the most recent prior workflow run on this branch that used a different commit SHA."""
    owner, repo = get_owner_repo()
    wf = get_workflow_file()
    branch = os.environ["GITHUB_REF_NAME"]
    current_run = os.environ["GITHUB_RUN_ID"]
    current_sha = os.environ["GITHUB_SHA"]

    url = (
        f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{wf}/runs"
        f"?branch={branch}&per_page=10"
    )
    for run in gh_api(url).get("workflow_runs", []):
        if str(run["id"]) == current_run:
            continue
        if run.get("head_sha") == current_sha:
            continue
        return run
    return None


def workflow_ran_recently(window_seconds: int) -> bool:
    """Return True if a different-commit workflow run occurred on this branch within the time window."""
    prior = get_latest_prior_different_commit_run()
    if not prior:
        return False
    ts = prior.get("run_started_at") or prior.get("created_at")
    if not ts:
        return False
    return calculate_age_seconds(ts) < window_seconds


# ────────────────────── Job-level logic ──────────────────────


def get_latest_prior_different_commit_run_id() -> str | None:
    """Return the run ID of the latest prior different-commit workflow run, or None if none."""
    prior = get_latest_prior_different_commit_run()
    return str(prior["id"]) if prior else None


def get_job_timestamp_in_run(run_id: str, job_name: str) -> str | None:
    """Return the job timestamp (start or created_at) for a given job name in a given run ID."""
    owner, repo = get_owner_repo()
    url = (
        f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
        f"?per_page=100"
    )
    for job in gh_api(url).get("jobs", []):
        if job.get("name") == job_name:
            return job.get("started_at") or job.get("created_at")
    return None


def job_ran_recently(window_seconds: int) -> bool:
    """Return True if this same job ran in a different-commit workflow run within the time window."""
    last_run_id = get_latest_prior_different_commit_run_id()
    if not last_run_id:
        return False

    job_name = os.environ["GITHUB_JOB"]
    ts = get_job_timestamp_in_run(last_run_id, job_name)
    if not ts:
        return False

    return calculate_age_seconds(ts) < window_seconds


# ─────────────────────────── Main ───────────────────────────


def main() -> bool:
    """Evaluate environment configuration and return whether the run is recent."""
    window = int(os.environ["WINDOW_SECONDS"])
    scope = os.environ["SCOPE"].lower()

    if (
        os.environ["ALWAYS_FALSE_ON_DEFAULT_BRANCH"].lower() == "true"
        and os.environ["GITHUB_REF_NAME"] == os.environ["GITHUB_DEFAULT_BRANCH"]
    ):
        return False

    if scope == "workflow":
        return workflow_ran_recently(window)
    elif scope == "job":
        return job_ran_recently(window)
    else:
        raise ValueError(f"Unrecognized SCOPE: {os.environ['SCOPE']}")


if __name__ == "__main__":
    print(f"ran_recently={'true' if main() else 'false'}")
