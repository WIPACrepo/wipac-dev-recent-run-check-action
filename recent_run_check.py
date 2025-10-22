#!/usr/bin/env python3
"""
Outputs: ran_recently=true/false for a different-commit run on the same branch within a time window.
"""

import os
import json
import urllib.request
import logging
from datetime import datetime, timezone


# ────────────────────────── Logging ──────────────────────────


def _setup_logging() -> None:
    """Configure logging with level set by LOG_LEVEL (DEBUG/INFO/WARNING/ERROR)."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(level=level, format="[%(levelname)s] %(message)s")
    logging.debug(f"logging level={level_name}")


# ────────────────────────── Utilities ──────────────────────────


def gh_api(url: str) -> dict:
    """Call the GitHub API and return parsed JSON."""
    logging.debug(f"http get {url}")
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
            f"http {status}; rate_limit_remaining={rl_rem}; keys={list(data.keys())}"
        )
        return data


def parse_utc(ts: str) -> datetime:
    """Convert a GitHub ISO8601 timestamp into a timezone-aware datetime."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def calculate_age_seconds(ts: str) -> float:
    """Return how many seconds have elapsed since the given timestamp."""
    return (datetime.now(timezone.utc) - parse_utc(ts)).total_seconds()


def humanize_seconds(s: float | None) -> str:
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
    return ref.split(".github/workflows/")[1].split("@")[0]


def get_owner_repo() -> tuple[str, str]:
    """Return (owner, repo) parsed from GITHUB_REPOSITORY."""
    return tuple(os.environ["GITHUB_REPOSITORY"].split("/", 1))


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
            logging.debug(f"skip run_id={rid} (current)")
            continue
        if sha == current_sha:
            logging.debug(f"skip run_id={rid} (same sha={sha})")
            continue
        logging.info(f"prior different-commit run found: run_id={rid} sha={sha}")
        return run
    logging.info("no prior different-commit workflow run found on this branch")
    return None


def workflow_decision(window_seconds: int) -> tuple[bool, dict[str, str]]:
    """Return (recent, details) for workflow scope decision."""
    details: dict[str, str] = {}
    prior = get_latest_prior_different_commit_run()
    if not prior:
        details.update(
            reason="no prior different-commit workflow run on this branch",
            age_seconds="—",
            prior_run_id="—",
            prior_timestamp="—",
        )
        return False, details

    ts = prior.get("run_started_at") or prior.get("created_at")
    if not ts:
        details.update(
            reason="prior run has no usable timestamp",
            age_seconds="—",
            prior_run_id=str(prior.get("id")),
            prior_timestamp="—",
        )
        return False, details

    age = calculate_age_seconds(ts)
    recent = age < window_seconds
    details.update(
        reason=(
            "prior workflow run is within the window"
            if recent
            else "prior workflow run is outside the window"
        ),
        age_seconds=str(int(age)),
        prior_run_id=str(prior.get("id")),
        prior_timestamp=ts,
    )
    return recent, details


# ────────────────────── Job-level logic ──────────────────────


def get_latest_prior_different_commit_run_id() -> str | None:
    """Return the run ID of the latest prior different-commit workflow run, or None if none."""
    prior = get_latest_prior_different_commit_run()
    return str(prior["id"]) if prior else None


def get_job_timestamp_in_run(run_id: str, job_name: str) -> str | None:
    """Return the job timestamp (start or created_at) for a given job name in a given run ID."""
    owner, repo = get_owner_repo()
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/jobs?per_page=100"
    jobs = gh_api(url).get("jobs", [])
    for job in jobs:
        if job.get("name") == job_name:
            return job.get("started_at") or job.get("created_at")
    logging.warning(f"job name='{job_name}' not found in prior run_id={run_id}")
    return None


def job_decision(window_seconds: int) -> tuple[bool, dict[str, str]]:
    """Return (recent, details) for job scope decision."""
    details: dict[str, str] = {}
    last_run_id = get_latest_prior_different_commit_run_id()
    if not last_run_id:
        details.update(
            reason="no prior different-commit workflow run on this branch",
            age_seconds="—",
            prior_run_id="—",
            prior_timestamp="—",
            job_name=os.environ["GITHUB_JOB"],
        )
        return False, details

    job_name = os.environ["GITHUB_JOB"]
    ts = get_job_timestamp_in_run(last_run_id, job_name)
    if not ts:
        details.update(
            reason="prior run did not include a matching job",
            age_seconds="—",
            prior_run_id=last_run_id,
            prior_timestamp="—",
            job_name=job_name,
        )
    else:
        age = calculate_age_seconds(ts)
        recent = age < window_seconds
        details.update(
            reason=(
                "prior job run is within the window"
                if recent
                else "prior job run is outside the window"
            ),
            age_seconds=str(int(age)),
            prior_run_id=last_run_id,
            prior_timestamp=ts,
            job_name=job_name,
        )
        return recent, details

    return False, details


# ─────────────────────────── Summary ───────────────────────────


def summary_lines(
    scope: str, recent: bool, details: dict[str, str], window_seconds: int
) -> list[str]:
    """Return compact human-readable summary lines with a single decision emoji."""
    branch = os.environ["GITHUB_REF_NAME"]
    default_branch = os.environ["GITHUB_DEFAULT_BRANCH"]
    always_false_default = (
        os.environ["ALWAYS_FALSE_ON_DEFAULT_BRANCH"].lower() == "true"
    )

    age_raw = details.get("age_seconds")
    age_h = humanize_seconds(float(age_raw) if age_raw and age_raw.isdigit() else None)
    emoji = "⚡⌚" if recent else "🐢🗓️"  # recent vs not recent

    lines = [
        f"{emoji} ran_recently: {'true' if recent else 'false'}",
        f"reason: {details.get('reason', '—')}",
        f"context: scope={scope}, branch={branch}, window={window_seconds}s, age={age_h} ({age_raw or '—'}s)",
        f"prior: run_id={details.get('prior_run_id', '—')}, timestamp={details.get('prior_timestamp', '—')}",
    ]
    if scope == "job":
        lines.append(f"job: name={details.get('job_name', os.environ['GITHUB_JOB'])}")
    if always_false_default and branch == default_branch:
        lines.append("note: default-branch protection active")
    return lines


# ─────────────────────────── Runner ───────────────────────────


def compute_decision() -> tuple[bool, dict[str, str], int, str]:
    """Compute and return (recent, details, window_seconds, scope)."""
    _setup_logging()

    window = int(os.environ["WINDOW_SECONDS"])
    scope = os.environ["SCOPE"].lower()
    branch = os.environ["GITHUB_REF_NAME"]
    default_branch = os.environ["GITHUB_DEFAULT_BRANCH"]
    always_false_default = (
        os.environ["ALWAYS_FALSE_ON_DEFAULT_BRANCH"].lower() == "true"
    )

    logging.debug(
        f"scope={scope}, window={window}s, branch={branch}, default={default_branch}, "
        f"always_false_on_default={always_false_default}"
    )

    if always_false_default and branch == default_branch:
        details = {
            "reason": "default-branch protection active",
            "age_seconds": "—",
            "prior_run_id": "—",
            "prior_timestamp": "—",
        }
        return False, details, window, scope

    if scope == "workflow":
        recent, details = workflow_decision(window)
    elif scope == "job":
        recent, details = job_decision(window)
    else:
        logging.error(f"unrecognized SCOPE: {os.environ['SCOPE']}")
        raise ValueError(f"Unrecognized SCOPE: {os.environ['SCOPE']}")

    return recent, details, window, scope


def main() -> bool:
    """Execute the decision, print summary, and return bool."""
    try:
        recent, details, window, scope = compute_decision()
        for line in summary_lines(scope, recent, details, window):
            print(line)
    except Exception as e:
        logging.exception(f"unhandled error: {e}")
        raise

    return recent


if __name__ == "__main__":
    with open(os.environ["GITHUB_OUTPUT"], "a") as f:
        f.write(f"ran_recently={'true' if main() else 'false'}\n")
