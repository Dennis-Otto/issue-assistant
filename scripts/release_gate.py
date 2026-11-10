"""Whether the Canary of main passed, before a release brings main to every repository.

An update of an engine merges on its own once every required check of its pull
request passed (decision 0030), and no check of a pull request runs the AI with a
key: only main may use the engine's secret. The Canary, .github/workflows/canary.yml,
runs the AI's whole path with it on main, after every change of what it runs, every
Monday and by hand, so it is the first run of a new version of the engine's action.
The job "the Canary of main" of .github/workflows/release-gate.yml, a required check,
runs this script on the pull request of the release bot alone, so that the release
waits for that run instead of a person who watches it.

It reads the runs of the Canary on main every POLL seconds while one of them is
unfinished, such as the one that the push of the latest change started, which
starts the canary as a run by hand and waits for it. Once none is, the latest run
decides: it passes or the check fails, whatever an earlier run did. A run that a
later one cancelled is never the latest once that one ended, so the latest run
that was cancelled is one that a person stopped, and the check fails for it too,
as for a push whose job start failed before it could start the canary. A run of
the push of a change exists as soon as GitHub took the push, before the release
bot, which that push starts as well, updates its pull request, so the check never
decides before the canary of the change it releases started. Past DEADLINE, it
fails and can run again.

    GH_REPO=owner/name BRANCH=main python3 scripts/release_gate.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

WORKFLOW = "canary.yml"
# The runs that tell whether the canary passed: those that run it, every Monday and
# by hand, and those of a push, whose job start runs it by hand and fails with it.
EVENTS = frozenset({"push", "schedule", "workflow_dispatch"})
# Seconds between two looks at the runs, the looks in a row that may fail before
# the script gives up, and how long it waits for an unfinished run: longer than the
# job start of a push, which waits for an earlier canary and then for its own.
POLL = 60
MOST_FAILED_LOOKS = 5
DEADLINE = timedelta(minutes=100)
TITLE = "### The Canary of main"
AGAIN = (
    "Once the cause is fixed, such as an update of an engine reverted on main, or "
    "when the canary only failed for a moment, run it again with "
    "`gh workflow run canary.yml`; this check passes when it runs again after that, "
    "and so does the next push to main, which updates the pull request of the "
    "release."
)

# The version of GitHub's REST API that every call asks for, that of the issue
# assistant: the job's sparse checkout holds this script alone, so it keeps a copy of
# API_VERSION_HEADER of issue_assistant/github.py, which tests/test_release_gate.py
# keeps equal.
API_VERSION_HEADER = "X-GitHub-Api-Version: 2022-11-28"

Run = Mapping[str, Any]


@dataclass(frozen=True)
class Verdict:
    """What the runs of the Canary tell: passed, failed, or None while one of them
    is unfinished; the line for the log and the summary; and, while one is
    unfinished, the latest of those."""

    passed: bool | None
    line: str
    run: str = ""


def created(run: Run) -> tuple[datetime, int]:
    """When GitHub created the run, such as 2026-11-10T07:23:45Z, and its number for
    two runs of the same second."""
    return datetime.fromisoformat(str(run["created_at"])), int(run["id"])


def describe(run: Run) -> str:
    """The run, its event and the commit that it ran, for a line of the log."""
    commit = str(run.get("head_sha") or "")[:7]
    message = str((run.get("head_commit") or {}).get("message") or "")
    subject = message.partition("\n")[0]
    of = f"{commit} ({subject})" if subject else commit
    return f"the run of {run['event']} on {of}: {run['html_url']}"


def decide(runs: Sequence[Run]) -> Verdict:
    """The verdict of the runs of the Canary on main, as GitHub lists them."""
    counted = [run for run in runs if run.get("event") in EVENTS]
    if not counted:
        return Verdict(False, f"No run of the Canary on main was found. {AGAIN}")
    unfinished = [run for run in counted if run.get("status") != "completed"]
    if unfinished:
        run = describe(max(unfinished, key=created))
        return Verdict(None, f"The Canary still runs, {run}", run)
    latest = max(counted, key=created)
    if latest.get("conclusion") == "success":
        return Verdict(True, f"The latest Canary of main passed, {describe(latest)}")
    return Verdict(
        False,
        f"The latest Canary of main ended as {latest.get('conclusion')}, "
        f"{describe(latest)}. The release waits until a Canary of main passes, so "
        f"that a broken engine reaches no repository. {AGAIN}",
    )


def watch(
    fetch: Callable[[], list[Run] | None],
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
    say: Callable[[str], None],
) -> Verdict:
    """Look at the runs every POLL seconds until none of them is unfinished, or
    until DEADLINE, and say once which run it waits for."""
    start = clock()
    failed = 0
    shown = ""
    while True:
        runs = fetch()
        if runs is None:
            failed += 1
            if failed == MOST_FAILED_LOOKS:
                raise SystemExit(
                    "::error::GitHub didn't list the runs of the Canary "
                    f"{MOST_FAILED_LOOKS} times in a row; run this check again."
                )
        else:
            failed = 0
            verdict = decide(runs)
            if verdict.passed is not None:
                return verdict
            if verdict.line != shown:
                say(verdict.line)
                shown = verdict.line
            if clock() - start >= DEADLINE:
                minutes = int(DEADLINE.total_seconds()) // 60
                return Verdict(
                    False,
                    f"The Canary still runs after {minutes} minutes, {verdict.run}. "
                    "Run this check again once it ended.",
                    verdict.run,
                )
        sleep(POLL)


def gh_api(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run `gh api` with the arguments and the version of the API, the one place
    that starts it."""
    return subprocess.run(
        ["gh", "api", *arguments, "--header", API_VERSION_HEADER],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def runs_of(repository: str, branch: str) -> list[Run] | None:
    """The latest runs of the Canary on the branch, newest first, with the GitHub
    CLI; None when it fails."""
    result = gh_api(
        [
            f"repos/{repository}/actions/workflows/{WORKFLOW}/runs"
            f"?branch={branch}&per_page=50"
        ]
    )
    if result.returncode != 0:
        print(f"::warning::GitHub didn't list the runs: {result.stderr.strip()}")
        return None
    runs: list[Run] = json.loads(result.stdout)["workflow_runs"]
    return runs


def main() -> int:
    repository = os.environ["GH_REPO"]
    branch = os.environ.get("BRANCH") or "main"
    verdict = watch(
        lambda: runs_of(repository, branch),
        lambda: datetime.now(UTC),
        time.sleep,
        lambda line: print(line, flush=True),
    )
    print(f"::{'notice' if verdict.passed else 'error'}::{verdict.line}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"{TITLE}\n\n{verdict.line}\n")
    return 0 if verdict.passed else 1


if __name__ == "__main__":
    sys.exit(main())
