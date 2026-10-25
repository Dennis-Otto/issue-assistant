"""How long the job analyze of the Canary waited for its runner.

The AI runs on GitHub's egress-firewall runner, a technical preview. Should GitHub
rename or end it, analyze waits in the queue for a runner, up to a day, while its
timeout-minutes count only from its start, and every later run of the same issue
waits behind it in every repository. The job "the AI's wait for its runner" of
.github/workflows/canary.yml runs this script beside the call of the reusable
workflow: it reads the jobs of its own run until analyze starts and shows the
wait in the summary of the job. Above WARN it warns, above FAIL it fails, also
while analyze still waits, so that the Canary shows a runner that never comes
within the hour.

The wait counts from the moment analyze could start: when GitHub created it, or
when the job event that it needs ended, whichever is later. So neither the event
nor an earlier canary that the call waits for counts.

    GH_REPO=owner/name RUN=123 ATTEMPT=1 python3 scripts/canary_queue.py
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

# The jobs of the reusable workflow, which GitHub names after the job of the call,
# such as "Issue assistant / Analyze with the AI engine", and this job itself.
ANALYZE = "Analyze with the AI engine"
EVENT = "Handle the event"
WATCHER = "the AI's wait for its runner"
WARN = timedelta(minutes=10)
FAIL = timedelta(minutes=30)
# Seconds between two looks at the jobs, and the looks in a row that may fail
# before the script gives up.
POLL = 30
MOST_FAILED_LOOKS = 5
TITLE = "### The AI's wait for its runner"
ADVICE = (
    "GitHub may have renamed or ended its egress-firewall runner, "
    "ubuntu-24.04-firewall, or the account lost it: see "
    "https://github.com/github-early-access/actions-native-egress-firewall. A "
    "repository can name another runner for the AI in its variable "
    "ISSUE_ASSISTANT_AI_RUNNER, such as ubuntu-24.04, behind Harden-Runner: "
    "https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/decisions/"
    "0019-a-runner-for-the-ai-when-the-firewall-runner-is-gone.md"
)

Job = Mapping[str, Any]


@dataclass(frozen=True)
class Wait:
    """What the jobs of the run tell of analyze: how long it waited for its runner
    and whether it started, or, without a wait, why there is none to measure."""

    waited: timedelta | None
    started: bool = False
    reason: str = ""


def called(job: Job, name: str) -> bool:
    """Whether `job` is the job `name`, also as a job of a called workflow."""
    return str(job.get("name") or "").rpartition(" / ")[2] == name


def moment(text: object) -> datetime | None:
    """A time of the jobs API, such as 2026-10-25T07:23:45Z."""
    return datetime.fromisoformat(text) if isinstance(text, str) and text else None


def look(jobs: Sequence[Job], now: datetime) -> Wait | None:
    """The wait of analyze as the jobs show it now; None while it can't be told
    yet and analyze waits for less than FAIL."""
    analyze = next((job for job in jobs if called(job, ANALYZE)), None)
    event = next((job for job in jobs if called(job, EVENT)), None)
    if analyze is not None and analyze.get("conclusion") == "skipped":
        return Wait(None, reason="analyze was skipped")
    if event is not None and event.get("status") != "completed":
        return None
    if analyze is None:
        others = [job for job in jobs if not called(job, WATCHER)]
        if (
            others
            and all(job.get("status") == "completed" for job in others)
            and (event is None or event.get("conclusion") != "success")
        ):
            return Wait(None, reason="no job of the run is left to start analyze")
        if event is None:
            return None
        # Not listed yet: it could start once the event ended.
        analyze = {}
    times = (
        moment(analyze.get("created_at")),
        moment(event.get("completed_at")) if event is not None else None,
    )
    ready = max((time for time in times if time is not None), default=now)
    started = moment(analyze.get("started_at"))
    if analyze.get("status") in ("in_progress", "completed") and started:
        return Wait(max(started - ready, timedelta()), started=True)
    if analyze.get("status") == "completed":
        return Wait(None, reason="analyze ended without a start, as when cancelled")
    waited = now - ready
    return Wait(waited) if waited > FAIL else None


def duration(span: timedelta) -> str:
    minutes, seconds = divmod(int(span.total_seconds()), 60)
    return f"{minutes} min {seconds} s"


def verdict(wait: Wait) -> tuple[int, str, str]:
    """The exit code, the workflow command for the log and the line of the summary."""
    if wait.waited is None:
        line = f"Nothing to measure: {wait.reason}."
        return 0, f"::notice::{line}", line
    limits = f"warns above {duration(WARN)}, fails above {duration(FAIL)}"
    if not wait.started:
        line = (
            f"analyze still waits for its runner after {duration(wait.waited)} "
            f"({limits}). {ADVICE}"
        )
        return 1, f"::error::{line}", line
    line = f"analyze waited {duration(wait.waited)} for its runner ({limits})."
    if wait.waited > FAIL:
        return 1, f"::error::{line} {ADVICE}", f"{line} {ADVICE}"
    if wait.waited > WARN:
        return 0, f"::warning::{line}", line
    return 0, line, line


def watch(
    fetch: Callable[[], list[Job] | None],
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
) -> Wait:
    """Look at the jobs every POLL seconds until their wait can be told."""
    failed = 0
    while True:
        jobs = fetch()
        if jobs is None:
            failed += 1
            if failed == MOST_FAILED_LOOKS:
                raise SystemExit(
                    f"::error::GitHub didn't list the jobs of the run "
                    f"{MOST_FAILED_LOOKS} times in a row."
                )
        else:
            failed = 0
            wait = look(jobs, clock())
            if wait is not None:
                return wait
        sleep(POLL)


def jobs_of(repository: str, run: str, attempt: str) -> list[Job] | None:
    """The jobs of the attempt of the run, with the GitHub CLI; None when it fails."""
    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repository}/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        print(f"::warning::GitHub didn't list the jobs: {result.stderr.strip()}")
        return None
    jobs: list[Job] = json.loads(result.stdout)["jobs"]
    return jobs


def main() -> int:
    repository = os.environ["GH_REPO"]
    run = os.environ["RUN"]
    attempt = os.environ.get("ATTEMPT") or "1"
    wait = watch(
        lambda: jobs_of(repository, run, attempt),
        lambda: datetime.now(UTC),
        time.sleep,
    )
    code, message, line = verdict(wait)
    print(message)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"{TITLE}\n\n{line}\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
