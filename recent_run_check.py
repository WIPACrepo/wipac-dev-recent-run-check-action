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
from typing import Dict, Optional, Tuple


# ────────────────────────── Logging Setup ──────────────────────────


def _setup_logging() -> None:
    """Configure logging with level set by LOG_LEVEL (DEBUG/INFO/WARNING/ERROR)."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="[%(levelname)s] %(message)s")
    logging.debug(f"Logging initialized at level={level_name}")


def _begin_group(title: str) -> None:
    """Start a GitHub Actions log group if LOG_GROUPING=true."""
    if os.environ.get("LOG_GROUPING", "true").lower() == "true":
        print(f"::group::{title}")


def _end_group() -> None:
    """End a GitHub Actions log group if LOG_GROUPING=true."""
    if os.environ.get("LOG_GROUPING", "true").lower() == "true":
        print("::endgroup::")


# ────────────────────────── Utilities ──────────────────────────


def gh_api(url: str) -> dict:
    """Call the GitHub API and return parsed JSON."""
    logging.debug(f"HTTP GET {url}")
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "recent-run-check",
        },
    )
    with urllib.request.urlopen(req) as resp:
        status = resp.getcode()
        rl_rem = resp.headers.get("x-ratelimit-remaining")
        data = json.loads(resp.read().decode("utf-8"))
        logging.debug(
            f"HTTP {status}; rate_limit_remaining={rl_rem}; keys={list(data.keys())}"
        )
        return data


def parse_utc(ts: str) -> datetime:
    """Convert a GitHub ISO8601 timestamp into a timezone-aware datetime."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def calculate_age_seconds(ts: str) -> float:
    """Return how many seconds have elapsed since the given timestamp."""
    age = (datetime.now(timezone.utc) - parse_utc(ts)).total_seconds()
    logging.debug(f"Age since {ts} is {age:.2f}s")
    return age


def humanize_seconds(s: Optional[float]) -> str:
    """Return a short human string for seconds like '8m 12s' or '—' if None."""
    if s is None:
        return "—"
    s = int(max(0, s))
    m, sec = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m {sec}s"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def get_workflow_file() -> str:
    """Extract the workflow filename from GITHUB_WORKFLOW_REF."""
    ref = os.environ["GITHUB_WORKFLOW_REF"]
    wf_file = ref.split(".github/workflows/")[1].split("@")[0]
    logging.debug(f"Workflow file detected: {wf_file}")
    return wf_file


def get_owner_repo() -> Tuple[str, str]:
    """Return (owner, repo) parsed from GITHUB_REPOSITORY."""
    owner, repo = tuple(os.environ["GITHUB_REPOSITORY"].split("/", 1))
    logging.debug(f"Repo parsed as owner={owner}, repo={repo}")
    return owner, repo


# ───────────────────── Workflow-level logic ─────────────────────


def get_latest_prior_different_commit_run() -> Optional[dict]:
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
            logging.debug(f"Skip run_id={rid} (current run).")
            continue
        if sha == current_sha:
            logging.debug(f"Skip run_id={rid} (same commit sha={sha}).")
            continue
        logging.info(f"Prior different-commit run found: run_id={rid}, sha={sha}.")
        return run

    logging.info("No prior different-commit workflow run found on this branch.")
    return None


def workflow_decision(window_seconds: int) -> Tuple[bool, Dict[str, str]]:
    """Return (result, details) for workflow scope decision."""
    details: Dict[str, str] = {}
    prior = get_latest_prior_different_commit_run()
    if not prior:
        details["reason"] = "No prior different-commit workflow run on this branch."
        details["age_seconds"] = "—"
        details["prior_run_id"] = "—"
        details["prior_timestamp"] = "—"
        return False, details

    ts = prior.get("run_started_at") or prior.get("created_at")
    if not ts:
        details["reason"] = "Prior run had no usable timestamp."
        details["age_seconds"] = "—"
        details["prior_run_id"] = str(prior.get("id"))
        details["prior_timestamp"] = "—"
        return False, details

    age = calculate_age_seconds(ts)
    recent = age < window_seconds
    details["reason"] = (
        "Recent prior workflow run detected."
        if recent
        else "Prior workflow run is outside the window."
    )
    details["age_seconds"] = f"{int(age)}"
    details["prior_run_id"] = str(prior.get("id"))
    details["prior_timestamp"] = ts
    return recent, details


# ────────────────────── Job-level logic ──────────────────────


def get_latest_prior_different_commit_run_id() -> Optional[str]:
    """Return the run ID of the latest prior different-commit workflow run, or None if none."""
    prior = get_latest_prior_different_commit_run()
    return str(prior["id"]) if prior else None


def get_job_timestamp_in_run(run_id: str, job_name: str) -> Optional[str]:
    """Return the job timestamp (start or created_at) for a given job name in a given run ID."""
    owner, repo = get_owner_repo()
    url = (
        f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/jobs"
        f"?per_page=100"
    )
    jobs = gh_api(url).get("jobs", [])
    for job in jobs:
        if job.get("name") == job_name:
            ts = job.get("started_at") or job.get("created_at")
            logging.info(
                f"Matched job name='{job_name}' in prior run_id={run_id}; ts={ts}."
            )
            return ts

    logging.warning(f"Job name='{job_name}' not found in prior run_id={run_id}.")
    return None


def job_decision(window_seconds: int) -> Tuple[bool, Dict[str, str]]:
    """Return (result, details) for job scope decision."""
    details: Dict[str, str] = {}
    last_run_id = get_latest_prior_different_commit_run_id()
    if not last_run_id:
        details["reason"] = "No prior different-commit workflow run on this branch."
        details["age_seconds"] = "—"
        details["prior_run_id"] = "—"
        details["prior_timestamp"] = "—"
        details["job_name"] = os.environ["GITHUB_JOB"]
        return False, details

    job_name = os.environ["GITHUB_JOB"]
    ts = get_job_timestamp_in_run(last_run_id, job_name)
    if not ts:
        details["reason"] = "Prior run did not include a matching job."
        details["age_seconds"] = "—"
        details["prior_run_id"] = last_run_id
        details["prior_timestamp"] = "—"
        details["job_name"] = job_name
        return False, details

    age = calculate_age_seconds(ts)
    recent = age < window_seconds
    details["reason"] = (
        "Recent prior job run detected."
        if recent
        else "Prior job run is outside the window."
    )
    details["age_seconds"] = f"{int(age)}"
    details["prior_run_id"] = last_run_id
    details["prior_timestamp"] = ts
    details["job_name"] = job_name
    return recent, details


# ─────────────────────────── Summary ───────────────────────────


def log_summary(
    scope: str, result: bool, details: Dict[str, str], window_seconds: int
) -> None:
    """Emit a compact, human-friendly summary of the decision."""
    branch = os.environ["GITHUB_REF_NAME"]
    default_branch = os.environ["GITHUB_DEFAULT_BRANCH"]
    always_false_default = (
        os.environ["ALWAYS_FALSE_ON_DEFAULT_BRANCH"].lower() == "true"
    )

    # Prepare fields
    age_raw = details.get("age_seconds")
    age_h = humanize_seconds(float(age_raw) if age_raw and age_raw.isdigit() else None)

    # Grouped, readable breakdown
    _begin_group("Recent Run Check Summary")
    logging.info(f"Decision: ran_recently={'true' if result else 'false'}")
    logging.info("Context:")
    logging.info(f"  • Scope: {scope}")
    logging.info(f"  • Branch: {branch}")
    logging.info(f"  • Default branch: {default_branch}")
    logging.info(f"  • Always false on default: {always_false_default}")
    logging.info("Evaluation:")
    logging.info(f"  • Reason: {details.get('reason', '—')}")
    logging.info(f"  • Window: {window_seconds}s")
    logging.info(f"  • Age: {age_h} ({age_raw or '—'}s)")
    logging.info(f"  • Prior run id: {details.get('prior_run_id', '—')}")
    logging.info(f"  • Prior timestamp: {details.get('prior_timestamp', '—')}")
    if scope == "job":
        logging.info(
            f"  • Job name: {details.get('job_name', os.environ['GITHUB_JOB'])}"
        )
    _end_group()


# ─────────────────────────── Main ───────────────────────────


def main() -> bool:
    """Evaluate environment configuration and return whether the run is recent."""
    _setup_logging()

    window = int(os.environ["WINDOW_SECONDS"])
    scope = os.environ["SCOPE"].lower()
    branch = os.environ["GITHUB_REF_NAME"]
    default_branch = os.environ["GITHUB_DEFAULT_BRANCH"]
    always_false_default = (
        os.environ["ALWAYS_FALSE_ON_DEFAULT_BRANCH"].lower() == "true"
    )

    logging.debug(
        f"SCOPE={scope}, WINDOW_SECONDS={window}, branch={branch}, default={default_branch}, "
        f"always_false_on_default={always_false_default}"
    )

    if always_false_default and branch == default_branch:
        details = {
            "reason": "Default-branch protection active.",
            "age_seconds": "—",
            "prior_run_id": "—",
            "prior_timestamp": "—",
        }
        log_summary(scope, False, details, window)
        return False

    if scope == "workflow":
        result, details = workflow_decision(window)
    elif scope == "job":
        result, details = job_decision(window)
    else:
        logging.error(f"Unrecognized SCOPE value: {os.environ['SCOPE']}")
        raise ValueError(f"Unrecognized SCOPE: {os.environ['SCOPE']}")

    log_summary(scope, result, details, window)
    return result


if __name__ == "__main__":
    try:
        print(f"ran_recently={'true' if main() else 'false'}")
    except Exception as e:
        # Ensure a clear end-user message while still surfacing failure.
        logging.error(f"Unhandled error: {e}")
        print("ran_recently=false")
        raise
