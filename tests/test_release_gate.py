"""The release waits until the Canary of main passed.

Updates of the engines merge on their own (decision 0030), and only the Canary on main
runs the AI with the engine's secret. scripts/release_gate.py, which the job "the
Canary of main" of .github/workflows/release-gate.yml runs on the pull request of the
release, waits while a Canary of main runs and passes only when the latest one passed.
"""

import importlib.util
import json
import runpy
import subprocess
import sys
import tomllib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

import issue_assistant as assistant

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "release_gate", ROOT / "scripts" / "release_gate.py"
)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
# The dataclasses of the module look it up by its name.
sys.modules[_spec.name] = gate
_spec.loader.exec_module(gate)

WORKFLOWS = ROOT / ".github" / "workflows"
GATE = yaml.safe_load((WORKFLOWS / "release-gate.yml").read_text("utf-8"))
CANARY = yaml.safe_load((WORKFLOWS / gate.WORKFLOW).read_text("utf-8"))
JOB = GATE["jobs"]["canary"]
START = datetime(2026, 11, 10, 7, 0, tzinfo=UTC)
URL = "https://github.com/o/r/actions/runs/"


def run(number, event="workflow_dispatch", conclusion="success", **fields):
    """A run of the Canary as GitHub lists it, created `number` minutes after START."""
    created = START + timedelta(minutes=number)
    return {
        "id": number,
        "event": event,
        "status": "completed" if conclusion else "in_progress",
        "conclusion": conclusion,
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "head_sha": f"{number:07d}abcdef",
        "head_commit": {"message": f"fix(deps): change {number}\n\nSigned-off-by: o"},
        "html_url": f"{URL}{number}",
        **fields,
    }


def described(number, event="workflow_dispatch"):
    return f"the run of {event} on {number:07d} (fix(deps): change {number}): {URL}{number}"


# ---------------------------------------------------------------- the workflow


def test_the_release_waits_for_the_check_and_every_other_pull_request_skips_it():
    """A required check that doesn't report keeps a pull request from merging, and
    GitHub counts a job that its condition skipped as passed: so the job reports on
    every pull request, and runs only on the release bot's own, without a runner on
    every other one."""
    # PyYAML reads the key on as True.
    assert GATE[True] == {"pull_request": None}
    assert GATE["permissions"] == {}
    assert JOB["name"] == "the Canary of main"
    project = tomllib.loads(
        (ROOT / ".github" / "repository.project.toml").read_text(encoding="utf-8")
    )
    assert JOB["name"] in project["branch"]["required_checks"]
    assert JOB["if"] == (
        "startsWith(github.head_ref, 'release-please--') && "
        "github.event.pull_request.head.repo.full_name == github.repository"
    )
    # It reads the runs of the Canary and nothing else.
    assert JOB["permissions"] == {"actions": "read"}
    (wait,) = [
        step
        for step in JOB["steps"]
        if step.get("run") == "python3 scripts/release_gate.py"
    ]
    assert wait["env"]["GH_REPO"] == "${{ github.repository }}"
    assert wait["env"]["BRANCH"] == "${{ github.event.repository.default_branch }}"
    # The script ends before the job's timeout.
    assert JOB["timeout-minutes"] * 60 > gate.DEADLINE.total_seconds() + 5 * 60
    # The run of the release's new head takes the place of the earlier one.
    assert GATE["concurrency"]["cancel-in-progress"] is True


def test_the_runs_that_count_are_those_that_run_the_canary_on_main():
    """The events of the Canary: every Monday, by hand, and a push, whose job start
    runs it by hand. No pull request runs it."""
    assert set(CANARY[True]) == gate.EVENTS
    assert CANARY[True]["push"]["branches"] == ["main"]
    assert CANARY["jobs"]["start"]["if"] == "github.event_name == 'push'"
    # The job start waits for its canary for less than the script waits for it.
    assert CANARY["jobs"]["start"]["timeout-minutes"] * 60 < (
        gate.DEADLINE.total_seconds()
    )


# ---------------------------------------------------------------- the verdict


@pytest.mark.parametrize(
    ("runs", "passed", "line"),
    [
        # The canary that a push started by hand after the push's own run, passed.
        (
            [run(2), run(1, "push")],
            True,
            f"The latest Canary of main passed, {described(2)}",
        ),
        # A canary of Monday failed after a passed one: the latest decides.
        (
            [run(3, "schedule", "failure"), run(2), run(1, "push")],
            False,
            "The latest Canary of main ended as failure, "
            f"{described(3, 'schedule')}. The release waits",
        ),
        # A later canary passed after a failed one.
        (
            [run(5), run(4, "push"), run(3, "workflow_dispatch", "failure")],
            True,
            f"The latest Canary of main passed, {described(5)}",
        ),
        # The push's run failed before it started the canary of its change.
        (
            [run(4, "push", "failure"), run(2), run(1, "push")],
            False,
            f"The latest Canary of main ended as failure, {described(4, 'push')}",
        ),
        # A person stopped the latest canary.
        (
            [run(4, "workflow_dispatch", "cancelled"), run(2)],
            False,
            "The latest Canary of main ended as cancelled",
        ),
        # The push's run starts the canary of its change, which waits for an earlier.
        (
            [run(6, "push", None), run(2)],
            None,
            f"The Canary still runs, {described(6, 'push')}",
        ),
        (
            [run(7, "workflow_dispatch", None), run(6, "push", None), run(2)],
            None,
            f"The Canary still runs, {described(7)}",
        ),
        # Queued too, and whatever GitHub lists of the same second, by its number.
        (
            [
                run(8, "workflow_dispatch", None, status="queued"),
                run(8, "push", None, id=9, status="queued"),
            ],
            None,
            "The Canary still runs, the run of push on 0000008",
        ),
        # Runs of other events, such as one that a pull request of a fork named main
        # started, don't count.
        ([run(9, "pull_request", "failure"), run(2)], True, "passed"),
        ([run(9, "pull_request")], False, "No run of the Canary on main was found."),
        ([], False, "No run of the Canary on main was found."),
    ],
)
def test_the_latest_run_decides_once_none_is_unfinished(runs, passed, line):
    verdict = gate.decide(runs)
    assert verdict.passed is passed
    assert line in verdict.line
    if passed is False:
        assert verdict.line.endswith(gate.AGAIN)


def test_a_run_without_its_commit_names_the_commit_alone():
    bare = run(2, head_commit=None)
    assert gate.describe(bare) == f"the run of workflow_dispatch on 0000002: {URL}2"


# ---------------------------------------------------------------- the wait


def test_the_script_waits_until_no_canary_runs():
    answers = iter(
        [
            None,
            [run(6, "push", None), run(2)],
            [run(7, "workflow_dispatch", None), run(6, "push", None), run(2)],
            [run(7, "workflow_dispatch", None), run(6, "push", None), run(2)],
            None,
            [run(7), run(6, "push"), run(2)],
        ]
    )
    sleeps, printed = [], []
    verdict = gate.watch(
        lambda: next(answers),
        lambda: START,
        sleeps.append,
        printed.append,
    )
    assert verdict.passed is True
    assert sleeps == [gate.POLL] * 5
    # Each run that the script waits for is told once.
    assert printed == [
        f"The Canary still runs, {described(6, 'push')}",
        f"The Canary still runs, {described(7)}",
    ]


def test_the_script_fails_when_the_canary_runs_past_the_deadline():
    times = iter([START, START + timedelta(minutes=50), START + gate.DEADLINE])
    sleeps = []
    verdict = gate.watch(
        lambda: [run(6, "push", None)], lambda: next(times), sleeps.append, print
    )
    assert verdict.passed is False
    assert verdict.line == (
        f"The Canary still runs after 100 minutes, {described(6, 'push')}. Run this "
        "check again once it ended."
    )
    assert sleeps == [gate.POLL]


def test_the_script_gives_up_when_github_lists_no_runs_again_and_again():
    sleeps = []
    with pytest.raises(SystemExit) as stopped:
        gate.watch(lambda: None, lambda: START, sleeps.append, print)
    assert str(stopped.value) == (
        "::error::GitHub didn't list the runs of the Canary 5 times in a row; run "
        "this check again."
    )
    assert len(sleeps) == gate.MOST_FAILED_LOOKS - 1


# ---------------------------------------------------------------- GitHub


def test_the_script_asks_for_the_version_of_the_api_that_the_assistant_asks_for():
    """Its sparse checkout holds the script alone, so it keeps a copy."""
    assert gate.API_VERSION_HEADER == assistant.API_VERSION_HEADER


def test_the_runs_are_those_of_the_canary_on_the_branch(monkeypatch, capsys):
    calls = []

    def fake(arguments, **options):
        calls.append(arguments)
        if len(calls) == 1:
            return subprocess.CompletedProcess(arguments, 1, "", "HTTP 502\n")
        listed = {"total_count": 1, "workflow_runs": [run(2)]}
        return subprocess.CompletedProcess(arguments, 0, json.dumps(listed), "")

    monkeypatch.setattr(gate.subprocess, "run", fake)
    assert gate.runs_of("o/r", "main") is None
    assert (
        capsys.readouterr().out == "::warning::GitHub didn't list the runs: HTTP 502\n"
    )
    assert gate.runs_of("o/r", "main") == [run(2)]
    assert calls[0] == [
        "gh",
        "api",
        "repos/o/r/actions/workflows/canary.yml/runs?branch=main&per_page=50",
        "--header",
        assistant.API_VERSION_HEADER,
    ]


@pytest.mark.parametrize(
    ("runs", "code", "summary"),
    [
        ([run(2)], 0, True),
        ([run(3, "schedule", "failure")], 1, True),
        ([run(2)], 0, False),
    ],
    ids=["passed", "failed", "log only"],
)
def test_main_tells_the_verdict_in_the_log_and_the_summary(
    monkeypatch, tmp_path, capsys, runs, code, summary
):
    asked = []

    def runs_of(repository, branch):
        asked.append((repository, branch))
        return runs

    monkeypatch.setattr(gate, "runs_of", runs_of)
    monkeypatch.setenv("GH_REPO", "o/r")
    monkeypatch.delenv("BRANCH", raising=False)
    path = tmp_path / "summary.md"
    if summary:
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))
    else:
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert gate.main() == code
    assert asked == [("o/r", "main")]
    verdict = gate.decide(runs)
    command = "notice" if code == 0 else "error"
    assert capsys.readouterr().out == f"::{command}::{verdict.line}\n"
    if summary:
        assert path.read_text("utf-8") == f"{gate.TITLE}\n\n{verdict.line}\n"
    else:
        assert not path.exists()


# ---------------------------------------------------------------- deciding again


def test_every_canary_of_main_that_runs_the_ai_lets_the_release_decide_again():
    """A later Canary turns the check of the release's pull request green after a
    failure, or red after a pass, without a person who runs it again."""
    job = CANARY["jobs"]["decide"]
    assert job["name"] == "the release decides again"
    assert job["needs"] == ["assistant", "queue"]
    # Also after a failed or a cancelled canary, never for a push, whose dispatched
    # run decides, and only on main, which the check reads.
    assert job["if"] == (
        "always() && github.event_name != 'push' "
        "&& github.ref_name == github.event.repository.default_branch"
    )
    assert job["permissions"] == {"actions": "write", "pull-requests": "read"}
    (step,) = [
        step
        for step in job["steps"]
        if step.get("run") == "python3 scripts/release_gate.py again"
    ]
    assert step["env"]["GH_REPO"] == "${{ github.repository }}"
    assert step["env"]["OUTCOME"] == (
        "${{ needs.assistant.result == 'success' && needs.queue.result == 'success' "
        "&& 'success' || 'failure' }}"
    )
    assert GATE["name"] == "Release gate"
    assert (WORKFLOWS / gate.GATE).exists()
    assert gate.RELEASE_BRANCH in JOB["if"]


def pull(ref="release-please--branches--main", repository="o/r", sha="abc1234def"):
    return {"head": {"ref": ref, "sha": sha, "repo": {"full_name": repository}}}


def check(conclusion="failure", status="completed"):
    return {
        "id": 77,
        "status": status,
        "conclusion": conclusion,
        "html_url": f"{URL}77",
    }


def answers(*replies):
    """A stand-in for gh_api that gives the replies in turn and keeps the calls."""
    calls = []
    queue = list(replies)

    def api(arguments):
        calls.append(list(arguments))
        code, body = queue.pop(0)
        out = json.dumps(body) if code == 0 else ""
        return subprocess.CompletedProcess(arguments, code, out, "HTTP 502\n")

    return api, calls


@pytest.mark.parametrize(
    ("passed", "conclusion", "reruns"),
    [
        (True, "failure", True),
        (False, "success", True),
        (True, "cancelled", True),
        (True, "success", False),
        (False, "failure", False),
    ],
    ids=["red to green", "green to red", "cancelled", "green", "red"],
)
def test_the_check_runs_again_when_its_verdict_is_not_that_of_the_canary(
    capsys, passed, conclusion, reruns
):
    replies = [
        (
            0,
            [
                pull("feat/x"),
                pull(repository="fork/r"),
                {"head": {"ref": "release-please--x", "sha": "f", "repo": None}},
                pull(),
            ],
        ),
        (0, {"workflow_runs": [check(conclusion)]}),
    ]
    if reruns:
        replies.append((0, {}))
    api, calls = answers(*replies)
    assert gate.again("o/r", passed, api) == 0
    assert calls[0] == ["repos/o/r/pulls?state=open&per_page=100"]
    assert calls[1] == [
        "repos/o/r/actions/workflows/release-gate.yml/runs"
        "?head_sha=abc1234def&event=pull_request&per_page=1"
    ]
    out = capsys.readouterr().out
    if reruns:
        assert calls[2] == ["--method", "POST", "repos/o/r/actions/runs/77/rerun"]
        assert out == (
            f"The check ended as {conclusion} and runs again, so that this Canary "
            f"decides: {URL}77\n"
        )
    else:
        assert len(calls) == 2
        assert out == f"The check ended as {conclusion} already: {URL}77\n"


@pytest.mark.parametrize(
    ("replies", "code", "out"),
    [
        ([(1, None)], 1, "::warning::GitHub didn't list the pull requests: HTTP 502\n"),
        (
            [(0, [pull("feat/x")])],
            0,
            "No pull request of a release is open; the next one waits for the Canary.\n",
        ),
        (
            [(0, [pull()]), (1, None)],
            1,
            "::warning::GitHub didn't list the runs of the check: HTTP 502\n",
        ),
        (
            [(0, [pull()]), (0, {"workflow_runs": []})],
            0,
            "The check of abc1234 hasn't started yet; it decides on its own.\n",
        ),
        (
            [(0, [pull()]), (0, {"workflow_runs": [check(None, "in_progress")]})],
            0,
            f"The check still waits for the Canary and decides on its own: {URL}77\n",
        ),
        (
            [(0, [pull()]), (0, {"workflow_runs": [check()]}), (1, None)],
            1,
            "::warning::GitHub didn't run the check again: HTTP 502\n",
        ),
    ],
    ids=["no pulls", "no release", "no runs", "not started", "unfinished", "no rerun"],
)
def test_the_check_is_left_alone_when_it_decides_on_its_own_or_github_fails(
    capsys, replies, code, out
):
    api, _ = answers(*replies)
    assert gate.again("o/r", True, api) == code
    assert capsys.readouterr().out == out


@pytest.mark.parametrize(("outcome", "passed"), [("success", True), ("failure", False)])
def test_main_decides_again_with_the_outcome_of_the_canary(
    monkeypatch, outcome, passed
):
    asked = []
    monkeypatch.setattr(
        gate, "again", lambda repository, ok: asked.append((repository, ok)) or 0
    )
    monkeypatch.setenv("GH_REPO", "o/r")
    monkeypatch.setenv("OUTCOME", outcome)
    assert gate.main(["again"]) == 0
    assert asked == [("o/r", passed)]


def test_again_asks_github_through_the_one_call_of_gh(monkeypatch, capsys):
    calls = []

    def fake(arguments, **options):
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "[]", "")

    monkeypatch.setattr(gate.subprocess, "run", fake)
    assert gate.again("o/r", True) == 0
    assert calls == [
        [
            "gh",
            "api",
            "repos/o/r/pulls?state=open&per_page=100",
            "--header",
            assistant.API_VERSION_HEADER,
        ]
    ]
    capsys.readouterr()


def test_the_script_runs_as_a_program(monkeypatch, capsys):
    listed = {"workflow_runs": [run(2)]}
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda arguments, **options: subprocess.CompletedProcess(
            arguments, 0, json.dumps(listed), ""
        ),
    )
    monkeypatch.setenv("GH_REPO", "o/r")
    monkeypatch.setenv("BRANCH", "trunk")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(sys, "argv", ["release_gate.py"])
    with pytest.raises(SystemExit) as stop:
        runpy.run_path(str(ROOT / "scripts" / "release_gate.py"), run_name="__main__")
    assert stop.value.code == 0
    assert capsys.readouterr().out == (
        f"::notice::The latest Canary of main passed, {described(2)}\n"
    )
