#!/usr/bin/env python3
"""
Outputs: ran_recently=true/false

Determines whether this workflow/job was executed recently on the same branch
for a *different commit* within the time window.
"""

import os
import json
import urllib.request
import logging
from datetime import datetime, timezone


# ────────────────────────── Logging Setup ──────────────────────────


def _setup_logging() -> None:
    """Configure logging based on DEBUG environment variable."""
    logging.basicConfig(level="DEBUG", format="[%(levelname)s] %(message)s")
    logging.debug("Debug logging is enabled.")


# ────────────────────────── Utilities ──────────────────────────


def gh_api(url: str) -> dict:
    """Call the GitHub API and return parsed JSON."""
    logging.debug(f"Requesting URL: {url}")
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "recent-run-check",
        },
    )
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        logging.debug(f"GitHub API response keys: {list(data.keys())}")
        return data


def parse_utc(ts: str) -> datetime:
    """Convert a GitHub ISO8601 timestamp into a timezone-aware datetime."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def calculate_age_seconds(ts: str) -> float:
    """Return how many seconds have elapsed since the given timestamp."""
    age = (datetime.now(timezone.utc) - parse_utc(ts)).total_seconds()
    logging.debug(f"Timestamp {ts} is {age:.2f}s old")
    return age


def get_workflow_file() -> str:
    """Extract the workflow filename from GITHUB_WORKFLOW_REF."""
    ref = os.environ["GITHUB_WORKFLOW_REF"]
    wf_file = ref.split(".github/workflows/")[1].split("@")[0]
    logging.debug(f"Detected workflow file: {wf_file}")
    return wf_file


def get_owner_repo() -> tuple[str, str]:
    """Return (owner, repo) parsed from GITHUB_REPOSITORY."""
    owner, repo = tuple(os.environ["GITHUB_REPOSITORY"].split("/", 1))
    logging.debug(f"Repository owner={owner}, repo={repo}")
    return owner, repo


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
        rid = str(run["id"])
        sha = run.get("head_sha")
        if rid == current_run:
            logging.debug(f"Skipping run {rid} (current run id)")
            continue
        if sha == current_sha:
            logging.debug(f"Skipping run {rid} (same commit SHA: {sha})")
            continue
        logging.debug(f"Found prior different-commit run: {rid} (SHA={sha})")
        return run
    logging.debug("No prior different-commit run found.")
    return None


def workflow_ran_recently(window_seconds: int) -> bool:
    """Return True if a different-commit workflow run occurred on this branch within the time window."""
    prior = get_latest_prior_different_commit_run()
    if not prior:
        return False
    ts = prior.get("run_started_at") or prior.get("created_at")
    if not ts:
        return False
    recent = calculate_age_seconds(ts) < window_seconds
    logging.debug(f"Workflow-level recent? {recent}")
    return recent


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
            ts = job.get("started_at") or job.get("created_at")
            logging.debug(f"Job {job_name} in run {run_id} timestamp={ts}")
            return ts
    logging.debug(f"Job {job_name} not found in run {run_id}.")
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
    recent = calculate_age_seconds(ts) < window_seconds
    logging.debug(f"Job-level recent? {recent}")
    return recent


# ─────────────────────────── Main ───────────────────────────


def main() -> bool:
    """Evaluate environment configuration and return whether the run is recent."""
    _setup_logging()

    window = int(os.environ["WINDOW_SECONDS"])
    scope = os.environ["SCOPE"].lower()
    branch = os.environ["GITHUB_REF_NAME"]
    default_branch = os.environ["GITHUB_DEFAULT_BRANCH"]

    logging.debug(
        f"SCOPE={scope}, WINDOW_SECONDS={window}, branch={branch}, default={default_branch}"
    )

    if (
        os.environ["ALWAYS_FALSE_ON_DEFAULT_BRANCH"].lower() == "true"
        and branch == default_branch
    ):
        logging.debug(
            "On default branch with ALWAYS_FALSE_ON_DEFAULT_BRANCH=true → returning False"
        )
        return False

    if scope == "workflow":
        return workflow_ran_recently(window)
    elif scope == "job":
        return job_ran_recently(window)
    else:
        raise ValueError(f"Unrecognized SCOPE: {os.environ['SCOPE']}")


if __name__ == "__main__":
    print(f"ran_recently={'true' if main() else 'false'}")
