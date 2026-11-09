"""Whether each reusable workflow ran as the Workflow test called it.

The jobs of .github/workflows/workflow-test.yml call each reusable workflow of this
repository, .github/workflows/reusable-*.yml, as a repository that uses the issue
assistant calls it, as a dry run. A called job that its condition skips passes as
well as one that runs, and so does a job whose steps a condition skips, so a call
that ran nothing would stay green. The last job of the workflow, "reusable
workflows", runs this script: it reads the jobs of its own run and checks that each
job of each call passed, with the step that writes the plan of its dry run, or was
skipped where the call has to skip it. It shows each job in a table in the summary
of the job and fails on every one that didn't run as it should.

GitHub lists the steps of a job some time after the job ends, once minutes after
(issue #288): while it lists a job that has to run as passed without the step that
writes its plan, as not yet completed, or without any step though it ran, the script
reads the jobs again, every PAUSE seconds for up to READS times, before it judges.
It reads them again, too, after a read that may succeed when it comes again: one
without an answer, such as on a failed connection, or with an error of GitHub's
servers, a timeout or a rate limit. A read that GitHub refuses with any other error of
the client, such as 403 or 404, fails the check at once (issue #327): every read
again would repeat it.

Before the calls, the job "what the reusable workflows run" runs
`workflow_test.py changes`, which decides whether they run: on a pull request only
when it changes a file that the dry runs run or read, so that the check, which main
requires, passes in seconds for the others.

    GH_REPO=owner/name EVENT=pull_request NUMBER=12 CHANGED=3 \
        python3 scripts/workflow_test.py changes
    GH_REPO=owner/name RUN=123 CHANGES=success WANTED=true \
        python3 scripts/workflow_test.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

# Each call of the Workflow test, by the name of its job, which is the file of the
# reusable workflow that it calls, with each job of that workflow by its name and the
# step that writes the plan of its dry run; None for a job that the call skips.
CALLS: dict[str, dict[str, str | None]] = {
    "reusable-labels.yml": {
        "Synchronize labels": "Create and update the labels",
    },
    "reusable-findings.yml": {
        # It writes no plan: the step that hands the record on to the findings.
        "Read the record of the reported alerts": (
            "Check the record of the reported alerts"
        ),
        "Accept or report findings": "Dismiss accepted findings and report the others",
    },
    "reusable-issue-lifecycle.yml": {
        "Remind and close": "Remind, close and mark fixed issues",
    },
    # The input smoke: the issue and the answer of a file, without the AI.
    "reusable-issue-assistant.yml": {
        "Handle the event": "Take the issue of the smoke test",
        "Analyze with the AI engine": None,
        "Analyze with the AI engine on the runner in reserve": None,
        "Check and post the analysis": "Check the engine's answer and post it",
    },
    # A pull request is no event of the discussions.
    "reusable-discussion-assistant.yml": {
        "Handle the event": None,
        "Analyze with the AI engine": None,
        "Analyze with the AI engine on the runner in reserve": None,
        "Check and post the answer": None,
    },
}

# What the dry runs run or read, as patterns of fnmatch, whose * also matches a /:
# a pull request that changes none of these files skips the calls.
WATCHED = (
    ".github/workflows/reusable-*.yml",
    ".github/workflows/workflow-test.yml",
    "scripts/workflow_test.py",
    "action.yml",
    "issue_assistant/*",
    "version.txt",
    "prompts/*",
    "locales/*",
    "templates/*",
    "tests/fixtures/canary-issue.json",
    "tests/fixtures/smoke-answer.json",
    # The settings of this repository that the dry runs read.
    ".github/issue-assistant/*",
    ".github/labels.toml",
    ".github/findings.toml",
)
# GitHub lists at most this many files of a pull request.
MOST_FILES = 3000

# An open alert of code scanning on the default branch fails the dry run of the
# findings, as it fails the Findings workflow there, which reports it. A pull request
# can't close it before its merge, so such a failure alone passes here, with a
# warning: the job ran and wrote its plan. Any other annotation of a failure, such as
# a traceback, fails the check.
FINDINGS = "reusable-findings.yml"
OPEN_ALERT = re.compile(r"Alert #\d+ is open: https://\S+")
EXIT_CODE = re.compile(r"Process completed with exit code \d+\.")

TITLE = "### The reusable workflows as dry runs"

# How often the gate reads the jobs again, and how many seconds apart, while GitHub
# lists a job without the steps that it ran: five minutes in all, which the
# timeout of the job "reusable workflows" in workflow-test.yml leaves room for.
READS = 15
PAUSE = 20

# The version of GitHub's REST API that every call asks for, that of the issue
# assistant: the job's sparse checkout holds this script alone, so it keeps a copy of
# API_VERSION_HEADER of issue_assistant/github.py, which tests/test_workflow_test.py
# keeps equal.
API_VERSION_HEADER = "X-GitHub-Api-Version: 2022-11-28"

# How the GitHub CLI names the HTTP status of GitHub's answer to a call that failed,
# such as "gh: Not Found (HTTP 404)", or "gh: HTTP 502" without a message of GitHub's;
# it names none when no answer came, such as on a failed connection.
STATUS = re.compile(r"\bHTTP (\d{3})\b")
# The errors of the client that may pass: a request that timed out, and too many
# requests. GitHub refuses a call over a rate limit with 403 or 429, and lifts it
# later; it repeats every other error of the client, such as 401, 403 or 404.
PASSING = frozenset({408, 429})
RATE_LIMIT = re.compile(r"rate limit", re.IGNORECASE)

Job = Mapping[str, Any]
Annotation = Mapping[str, Any]


@dataclass(frozen=True)
class Failure:
    """A call of the GitHub CLI that failed: the HTTP status of GitHub's answer, None
    when no answer came, and what the CLI said."""

    status: int | None
    said: str

    @property
    def transient(self) -> bool:
        """Whether the call may succeed when it comes again: without an answer, on an
        error of GitHub's servers, a timeout or a rate limit."""
        return (
            self.status is None
            or self.status >= 500
            or self.status in PASSING
            or RATE_LIMIT.search(self.said) is not None
        )


def gh(*arguments: str) -> str | Failure:
    """What the GitHub CLI printed, or its failure, after a warning. Every call is a
    call of `gh api` that asks for the version of the API."""
    result = subprocess.run(
        ["gh", "api", *arguments, "--header", API_VERSION_HEADER],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        said = result.stderr.strip()
        print(f"::warning::gh api {arguments[0]} failed: {said}")
        statuses = STATUS.findall(said)
        return Failure(int(statuses[-1]) if statuses else None, said)
    return result.stdout


def watched(path: str) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in WATCHED)


def decide(
    event: str, changed: int, files: Callable[[], list[str] | None]
) -> tuple[bool, str]:
    """Whether the calls run, and why."""
    if event != "pull_request":
        return True, f"A run of {event} calls every reusable workflow."
    if changed >= MOST_FILES:
        return True, (
            f"The pull request changes {changed} files, more than GitHub lists; "
            "every reusable workflow runs."
        )
    names = files()
    if names is None:
        return (
            True,
            "The files of the pull request are unknown; every reusable workflow runs.",
        )
    first = next((name for name in names if watched(name)), None)
    if first is None:
        return False, (
            "The pull request changes nothing that the dry runs of the reusable "
            "workflows run or read; they are skipped."
        )
    return True, (
        f"The pull request changes {first}, which the dry runs run or read; every "
        "reusable workflow runs."
    )


def files_of(repository: str, number: str) -> list[str] | None:
    """The files of the pull request, a renamed one by its old name too."""
    printed = gh(
        f"repos/{repository}/pulls/{number}/files?per_page=100",
        "--paginate",
        "--jq",
        ".[] | .filename, (.previous_filename // empty)",
    )
    return None if isinstance(printed, Failure) else printed.splitlines()


def changes_command() -> int:
    repository = os.environ["GH_REPO"]
    number = os.environ.get("NUMBER", "")
    run, reason = decide(
        os.environ.get("EVENT", ""),
        int(os.environ.get("CHANGED") or "0"),
        lambda: files_of(repository, number),
    )
    print(reason)
    write("GITHUB_OUTPUT", f"run={str(run).lower()}\n")
    write("GITHUB_STEP_SUMMARY", f"{reason}\n")
    return 0


def tolerated(job: Job, annotations: Sequence[Annotation] | None) -> bool:
    """Whether the job of the findings failed on open alerts of the default branch
    alone."""
    if annotations is None:
        return False
    failures = [
        str(item.get("message") or "")
        for item in annotations
        if item.get("annotation_level") == "failure"
    ]
    return any(OPEN_ALERT.fullmatch(text) for text in failures) and all(
        OPEN_ALERT.fullmatch(text) or EXIT_CODE.fullmatch(text) for text in failures
    )


def listed(jobs: Sequence[Job], full: str) -> Job | None:
    """The job of that name, call and job, as GitHub lists it; None if it doesn't."""
    return next((job for job in jobs if job.get("name") == full), None)


def unsettled(jobs: Sequence[Job]) -> list[str]:
    """The jobs that have to run and that GitHub doesn't list as they ended yet: not
    completed, or without the step that writes their plan though they passed, or
    without any step though they ran, or completed with that step not yet completed.
    Once completed, a job that passed lists every step as completed, and one that ran
    at least the first; a job that ended earlier, such as one that failed before its
    plan, is judged as it is."""
    late = []
    for call, called in CALLS.items():
        for name, plan in called.items():
            full = f"{call} / {name}"
            job = listed(jobs, full)
            if plan is None or job is None:
                continue
            listed_steps = job.get("steps") or []
            steps = [str(step.get("name")) for step in listed_steps]
            step = next((s for s in listed_steps if s.get("name") == plan), None)
            if step is not None and (
                step.get("status") == "completed" or job.get("status") != "completed"
            ):
                continue
            conclusion = job.get("conclusion")
            if (
                job.get("status") != "completed"
                or conclusion == "success"
                or (not steps and conclusion != "skipped")
            ):
                late.append(full)
    return late


def settled(
    read: Callable[[], list[Job] | Failure], sleep: Callable[[float], None]
) -> tuple[list[Job] | Failure, list[str]]:
    """The jobs of the run once GitHub lists each as it ended, read again every PAUSE
    seconds for up to READS times, and those that it still lists too early then. A
    read that may succeed when it comes again keeps the jobs that GitHub listed
    before, or the failure when it never listed them; a read that GitHub refuses ends
    the reads with its failure."""
    jobs = read()
    late = [] if isinstance(jobs, Failure) else unsettled(jobs)
    reads = 0
    while ((isinstance(jobs, Failure) and jobs.transient) or late) and reads < READS:
        if isinstance(jobs, Failure):
            print(
                "GitHub didn't list the jobs of this run; reading them again in "
                f"{PAUSE} s."
            )
        else:
            print(
                f"GitHub lists {len(late)} of the jobs without the steps that they ran "
                f"yet, such as {late[0]}; reading the jobs again in {PAUSE} s."
            )
        sleep(PAUSE)
        reads += 1
        again = read()
        if not isinstance(again, Failure):
            jobs = again
            late = unsettled(jobs)
        elif not again.transient:
            return again, []
    if reads and not isinstance(jobs, Failure) and not late:
        print(f"GitHub listed every job as it ended after {reads * PAUSE} s.")
    return jobs, late


def judge(
    call: str,
    job: Job | None,
    plan: str | None,
    annotations: Callable[[Job], Sequence[Annotation] | None],
) -> tuple[str, str]:
    """What the job did, and the problem with it, or an empty one."""
    if job is None:
        if plan is None:
            return "not listed, so not run", ""
        return (
            "missing",
            "GitHub ran no such job; did its name or its condition change?",
        )
    conclusion = str(job.get("conclusion") or job.get("status") or "unknown")
    if plan is None:
        if conclusion == "skipped":
            return "skipped", ""
        return conclusion, f"it ended as {conclusion}, but the call has to skip it"
    if conclusion == "skipped":
        return conclusion, "it was skipped, but the call has to run it"
    steps = {str(step.get("name")): step for step in job.get("steps") or []}
    step = steps.get(plan)
    if step is None and not steps:
        return (
            conclusion,
            f"GitHub lists none of its steps, so none shows the step “{plan}”, which "
            "writes the plan of its dry run",
        )
    if step is None:
        return (
            conclusion,
            f"it has no step “{plan}”, which writes the plan of its dry run",
        )
    ran = str(step.get("conclusion") or step.get("status") or "unknown")
    if conclusion == "success" and ran == "success":
        return "passed", ""
    if (
        call == FINDINGS
        and conclusion == "failure"
        and ran == "failure"
        and tolerated(job, annotations(job))
    ):
        return "failed on open alerts of the default branch", ""
    return (
        f"{conclusion}, its plan {ran}",
        f"it ended as {conclusion}, and its step “{plan}” as {ran}",
    )


def check(
    jobs: Sequence[Job], annotations: Callable[[Job], Sequence[Annotation] | None]
) -> tuple[list[str], list[str]]:
    """The rows of the table of the summary, and the problems."""
    rows, problems = [], []
    for call, called in CALLS.items():
        for name, plan in called.items():
            full = f"{call} / {name}"
            job = listed(jobs, full)
            result, problem = judge(call, job, plan, annotations)
            expected = f"passes, with “{plan}”" if plan else "skipped"
            rows.append(f"| {full} | {expected} | {result} |")
            if problem:
                problems.append(f"{full}: {problem}.")
            elif result.startswith("failed on open alerts"):
                print(
                    f"::warning::{full} failed on open alerts of code scanning on the "
                    "default branch, which the Findings workflow reports there; the "
                    "dry run itself ran."
                )
    return rows, problems


def jobs_of(repository: str, run: str) -> list[Job] | Failure:
    """The latest jobs of the run, also of a run again of its failed jobs."""
    printed = gh(
        f"repos/{repository}/actions/runs/{run}/jobs?filter=latest&per_page=100"
    )
    if isinstance(printed, Failure):
        return printed
    jobs: list[Job] = json.loads(printed)["jobs"]
    return jobs


def annotations_of(repository: str) -> Callable[[Job], Sequence[Annotation] | None]:
    def read(job: Job) -> Sequence[Annotation] | None:
        printed = gh(
            f"repos/{repository}/check-runs/{job['id']}/annotations?per_page=100"
        )
        if isinstance(printed, Failure):
            return None
        annotations: list[Annotation] = json.loads(printed)
        return annotations

    return read


def verify_command() -> int:
    changes = os.environ.get("CHANGES", "")
    if changes != "success":
        print(
            "::error::The job that decides whether the reusable workflows run ended "
            f"as {changes or 'unknown'}, so nothing says whether they had to run."
        )
        return 1
    if os.environ.get("WANTED") != "true":
        line = (
            "Nothing that the dry runs of the reusable workflows run or read "
            "changed; no call had to run."
        )
        print(line)
        write("GITHUB_STEP_SUMMARY", f"{TITLE}\n\n{line}\n")
        return 0
    repository = os.environ["GH_REPO"]
    run = os.environ["RUN"]
    jobs, late = settled(lambda: jobs_of(repository, run), time.sleep)
    if isinstance(jobs, Failure) and jobs.transient:
        print("::error::GitHub didn't list the jobs of this run; run it again.")
        return 1
    if isinstance(jobs, Failure):
        print(
            "::error::GitHub refused to list the jobs of this run with HTTP "
            f"{jobs.status}, which every read again would repeat, so the check didn't "
            "wait. Does the job still have the permission `actions: read`?"
        )
        return 1
    if late:
        print(
            f"::warning::GitHub still lists {len(late)} of the jobs without the steps "
            f"that they ran, after {READS * PAUSE // 60} minutes. Where the check of "
            "such a job shows the step that writes its plan, run this check again."
        )
    rows, problems = check(jobs, annotations_of(repository))
    table = "| Job | Expected | Result |\n| --- | --- | --- |\n" + "\n".join(rows)
    for problem in problems:
        print(f"::error::{problem}")
    verdict = (
        f"{len(problems)} of the jobs didn't run as their call has to."
        if problems
        else "Each reusable workflow ran as a dry run, as its call has to."
    )
    print(verdict)
    write("GITHUB_STEP_SUMMARY", f"{TITLE}\n\n{table}\n\n{verdict}\n")
    return 1 if problems else 0


def write(variable: str, text: str) -> None:
    """Append to the file that the variable of the runner names, if any."""
    path = os.environ.get(variable)
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def main() -> int:
    if sys.argv[1:] == ["changes"]:
        return changes_command()
    return verify_command()


if __name__ == "__main__":
    sys.exit(main())
