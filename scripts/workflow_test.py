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
from collections.abc import Callable, Mapping, Sequence
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
        "Accept or report findings": "Dismiss accepted findings and report the others",
    },
    "reusable-issue-lifecycle.yml": {
        "Remind and close": "Remind, close and mark fixed issues",
    },
    # The input smoke: the issue and the answer of a file, without the AI.
    "reusable-issue-assistant.yml": {
        "Handle the event": "Take the issue of the smoke test",
        "Analyze with the AI engine": None,
        "Check and post the analysis": "Check the engine's answer and post it",
    },
    # A pull request is no event of the discussions.
    "reusable-discussion-assistant.yml": {
        "Handle the event": None,
        "Analyze with the AI engine": None,
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

Job = Mapping[str, Any]
Annotation = Mapping[str, Any]


def gh(*arguments: str) -> str | None:
    """What the GitHub CLI printed; None when it failed, after a warning."""
    result = subprocess.run(
        ["gh", *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        print(
            f"::warning::gh {arguments[0]} {arguments[1]} failed: {result.stderr.strip()}"
        )
        return None
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
        "api",
        f"repos/{repository}/pulls/{number}/files?per_page=100",
        "--paginate",
        "--jq",
        ".[] | .filename, (.previous_filename // empty)",
    )
    return None if printed is None else printed.splitlines()


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
            job = next((job for job in jobs if job.get("name") == full), None)
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


def jobs_of(repository: str, run: str) -> list[Job] | None:
    """The latest jobs of the run, also of a run again of its failed jobs."""
    printed = gh(
        "api", f"repos/{repository}/actions/runs/{run}/jobs?filter=latest&per_page=100"
    )
    if printed is None:
        return None
    jobs: list[Job] = json.loads(printed)["jobs"]
    return jobs


def annotations_of(repository: str) -> Callable[[Job], Sequence[Annotation] | None]:
    def read(job: Job) -> Sequence[Annotation] | None:
        printed = gh(
            "api", f"repos/{repository}/check-runs/{job['id']}/annotations?per_page=100"
        )
        if printed is None:
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
    jobs = jobs_of(repository, os.environ["RUN"])
    if jobs is None:
        print("::error::GitHub didn't list the jobs of this run; run it again.")
        return 1
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
