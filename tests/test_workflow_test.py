"""The Workflow test runs every reusable workflow as a dry run.

.github/workflows/workflow-test.yml calls each reusable workflow of this repository,
which every repository that uses the issue assistant calls at the commit of a
release, as such a repository calls it. scripts/workflow_test.py decides whether a
pull request changes what the calls run, and its last job, the check "reusable
workflows" that main requires, reads the jobs of the run: each one passed, with the
step that writes the plan of its dry run, or was skipped where its call has to skip
it. These tests keep the calls, the names that the script looks for and the files
that it watches in step with the workflows, and check the script itself.
"""

import importlib.util
import json
import runpy
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

import issue_assistant as assistant
from support import FakeGitHub, run_main

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "workflow_test", ROOT / "scripts" / "workflow_test.py"
)
assert _spec and _spec.loader
script = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = script
_spec.loader.exec_module(script)

WORKFLOWS = ROOT / assistant.WORKFLOWS
TEST = yaml.safe_load((WORKFLOWS / "workflow-test.yml").read_text("utf-8"))
JOBS = TEST["jobs"]
CALLERS = {key: job for key, job in JOBS.items() if "uses" in job}
CANARY_ISSUE = "tests/fixtures/canary-issue.json"
SMOKE_ANSWER = "tests/fixtures/smoke-answer.json"
LEVELS = ("none", "read", "write")


def load(name):
    # PyYAML reads the key "on" as True.
    return yaml.safe_load((WORKFLOWS / name).read_text("utf-8"))


# ---------------------------------------------------------------- the workflow


def test_every_reusable_workflow_has_a_call_with_the_permissions_of_its_template():
    """A called job gets at most the permissions of its call, and a call that
    grants more than the template would hide a job that asks for too much."""
    called = sorted(path.name for path in WORKFLOWS.glob("reusable-*.yml"))
    assert sorted(job["name"] for job in CALLERS.values()) == called
    assert list(script.CALLS) == [job["name"] for job in CALLERS.values()]
    for key, job in CALLERS.items():
        name = job["name"]
        assert job["uses"] == f"./{assistant.WORKFLOWS}/{name}", key
        assert job["needs"] == "changes", key
        assert job["if"] == "needs.changes.outputs.run == 'true'", key
        template = yaml.safe_load(
            (
                assistant.TEMPLATES
                / assistant.WORKFLOWS
                / name.removeprefix(assistant.REUSABLE_PREFIX)
            ).read_text("utf-8")
        )
        (caller,) = template["jobs"].values()
        assert job["permissions"] == caller["permissions"], key
        # No secret: the engine is the Canary's.
        assert "secrets" not in job, key
        expected = {"dry_run": True}
        if name == "reusable-issue-assistant.yml":
            expected = {"smoke": True, "dry_run": True}
        assert job["with"] == expected, key


def test_only_the_workflow_test_passes_the_input_smoke():
    callers = [
        (path.name, key)
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for key, job in load(path.name)["jobs"].items()
        if "smoke" in (job.get("with") or {})
    ]
    assert callers == [("workflow-test.yml", "issues")]


def test_the_script_knows_every_job_and_the_step_that_writes_its_plan():
    """The script finds a job by the name that GitHub gives it, the name of the call
    and that of the job, and a step by its name."""
    for call, expected in script.CALLS.items():
        jobs = load(call)["jobs"]
        assert list(expected) == [job["name"] for job in jobs.values()], call
        for job in jobs.values():
            plan = expected[job["name"]]
            if plan is not None:
                assert plan in [step.get("name") for step in job["steps"]], plan
    # Of the issues, the two jobs of the AI alone are skipped: the smoke test runs no
    # engine. A pull request is no event of the discussions.
    skipped = {
        f"{call} / {name}"
        for call, expected in script.CALLS.items()
        for name, plan in expected.items()
        if plan is None
    }
    assert skipped == {
        "reusable-issue-assistant.yml / Analyze with the AI engine",
        "reusable-issue-assistant.yml / Analyze with the AI engine on the runner in "
        "reserve",
        "reusable-discussion-assistant.yml / Handle the event",
        "reusable-discussion-assistant.yml / Analyze with the AI engine",
        "reusable-discussion-assistant.yml / Analyze with the AI engine on the runner "
        "in reserve",
        "reusable-discussion-assistant.yml / Check and post the answer",
    }


def test_the_gate_waits_for_every_call_and_is_the_required_check():
    gate = JOBS["gate"]
    assert gate["name"] == "reusable workflows"
    assert gate["needs"] == ["changes", *CALLERS]
    # Also when a call failed, but not for a run that a newer push cancelled.
    assert gate["if"] == "${{ !cancelled() }}"
    assert gate["permissions"] == {
        "actions": "read",
        "checks": "read",
        "contents": "read",
    }
    (check,) = [step for step in gate["steps"] if step.get("run", "").endswith(".py")]
    assert check["run"] == "python3 scripts/workflow_test.py"
    assert check["env"]["RUN"] == "${{ github.run_id }}"
    assert check["env"]["CHANGES"] == "${{ needs.changes.result }}"
    assert check["env"]["WANTED"] == "${{ needs.changes.outputs.run }}"
    # Room for the wait for Harden-Runner, up to two minutes, for GitHub to list the
    # steps of every job and for Harden-Runner to remove Docker, up to two minutes.
    assert gate["timeout-minutes"] >= 2 + script.READS * script.PAUSE / 60 + 2 + 1
    explain = gate["steps"][-2]
    # Not after the step that stopped the job because Harden-Runner didn't block.
    assert explain["if"] == "failure() && steps.blocks.outcome != 'failure'"
    assert explain["env"] == {"CHECK": gate["name"]}
    settings = tomllib.loads(
        (ROOT / ".github/repository.project.toml").read_text("utf-8")
    )
    assert gate["name"] in settings["branch"]["required_checks"]


def test_every_pull_request_reports_the_check():
    """A filter of paths in the triggers would leave the required check waiting;
    the job changes decides instead, and the gate reports also when it skipped
    every call."""
    triggers = TEST[True]
    # No workflow_dispatch: CodeQL's cache-poisoning query counts it as a trigger
    # that an outsider may start, for the read-only jobs of the discussions.
    assert set(triggers) == {"push", "pull_request"}
    assert triggers["pull_request"] is None
    assert triggers["push"] == {"branches": ["main"]}
    assert TEST["permissions"] == {}
    changes = JOBS["changes"]
    assert changes["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert changes["outputs"] == {"run": "${{ steps.files.outputs.run }}"}
    (decide,) = [step for step in changes["steps"] if step.get("id") == "files"]
    assert decide["run"] == "python3 scripts/workflow_test.py changes"
    assert decide["env"]["NUMBER"] == "${{ github.event.pull_request.number }}"
    assert decide["env"]["CHANGED"] == "${{ github.event.pull_request.changed_files }}"


def test_the_calls_run_for_every_file_that_the_dry_runs_run_or_read():
    for pattern in script.WATCHED:
        assert next(ROOT.glob(pattern), None), f"{pattern} matches no file"
    # What the jobs check out of the issue assistant, the reusable workflows, the
    # files of the smoke test and this workflow with its script.
    for path in (
        "action.yml",
        "issue_assistant/cli.py",
        "version.txt",
        "prompts/triage.md",
        "locales/en.toml",
        "templates/.github/workflows/labels.yml",
        ".github/workflows/reusable-issue-assistant.yml",
        ".github/workflows/workflow-test.yml",
        "scripts/workflow_test.py",
        CANARY_ISSUE,
        SMOKE_ANSWER,
        ".github/issue-assistant/config.toml",
        ".github/labels.toml",
        ".github/findings.toml",
    ):
        assert script.watched(path), path
    for path in ("README.md", "docs/architecture.md", "tests/test_assistant.py"):
        assert not script.watched(path), path


# ---------------------------------------------------------------- the smoke test

ISSUES = load("reusable-issue-assistant.yml")["jobs"]


def smoke_step(job):
    (step,) = [step for step in ISSUES[job]["steps"] if step.get("id") == "smoke"]
    assert step["if"] == "inputs.smoke"
    return step


@pytest.mark.parametrize(
    ("caller", "code", "says"),
    [
        ("Dennis-Otto/issue-assistant", 0, "The smoke test checks the answer of "),
        ("dennis-otto/Issue-Assistant", 0, "The smoke test checks the answer of "),
        (
            "octo/app",
            1,
            "::error::Only the Workflow test of Dennis-Otto/issue-assistant may pass "
            "the input smoke, not octo/app.",
        ),
    ],
)
def test_the_job_event_takes_the_issue_of_the_smoke_test_in_this_repository_alone(
    tmp_path, caller, code, says
):
    """Only this repository has the files of the smoke test; the job sets no
    labels, and analyze, which needs a mode, is skipped by its condition."""
    step = smoke_step("event")
    assert step["env"] == {
        "CALLER": "${{ github.repository }}",
        "WORKFLOW": "${{ fromJSON(toJSON(job)).workflow_repository }}",
    }
    output = tmp_path / "output"
    output.touch()
    result = subprocess.run(
        [shutil.which("bash"), "-e", "-c", step["run"]],
        env={
            "CALLER": caller,
            "WORKFLOW": "Dennis-Otto/issue-assistant",
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == code, result.stdout + result.stderr
    assert says in result.stdout
    assert output.read_text(encoding="utf-8") == ("mode=triage\n" if code == 0 else "")
    condition = " ".join(ISSUES["analyze"]["if"].split())
    assert condition.startswith("!inputs.smoke && ")


def test_apply_takes_the_answer_of_the_smoke_test_in_place_of_the_engine_s():
    step = smoke_step("apply")
    assert step["run"].splitlines() == [
        'answer="$(jq --compact-output . tests/fixtures/smoke-answer.json)"',
        'echo "answer=$answer" >> "$GITHUB_OUTPUT"',
    ]
    steps = ISSUES["apply"]["steps"]
    assert steps.index(step) == len(steps) - 3


def test_apply_accepts_the_answer_of_the_smoke_test_for_the_canary_s_issue(
    monkeypatch, tmp_path
):
    """apply checks the answer of the file as that of an engine, for the issue of
    the Canary, and the fixture makes it a dry run: an answer that a check refuses
    would fail the check of every pull request."""
    repository = "Dennis-Otto/issue-assistant"
    config = assistant.load_config(ROOT, repository)
    github = FakeGitHub(config, repository=repository)
    code, _, summary = run_main(
        monkeypatch,
        tmp_path,
        ROOT,
        github,
        "apply",
        MODE="triage",
        RESULT=(ROOT / SMOKE_ANSWER).read_text(encoding="utf-8"),
        GITHUB_SHA="0" * 40,
        GH_REPO=repository,
        ISSUE_ASSISTANT_FIXTURE=CANARY_ISSUE,
    )
    assert code == 0, summary
    assert github.dry_run and github.writes_made() == []
    assert "### Issue assistant: triage of #1000000 (dry run)" in summary
    assert "- comment on #1000000\n" in summary
    assert "<details><summary>Comment</summary>" in summary


# ---------------------------------------------------------------- the script


# The arguments that end every call of gh: the version of GitHub's API.
VERSION = ["--header", assistant.API_VERSION_HEADER]


def completed(stdout="", code=0, stderr=""):
    return lambda arguments, **options: subprocess.CompletedProcess(
        arguments, code, stdout, stderr
    )


def test_gh_prints_what_the_github_cli_printed(monkeypatch):
    seen = []

    def run(arguments, **options):
        seen.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "out", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert script.gh("repos/o/r") == "out"
    assert seen == [["gh", "api", "repos/o/r", *VERSION]]


def test_the_script_asks_for_the_version_of_the_api_that_the_assistant_asks_for():
    # The job's sparse checkout holds the script alone, so it keeps a copy.
    assert script.API_VERSION_HEADER == assistant.API_VERSION_HEADER


def test_gh_warns_when_the_github_cli_fails(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", completed(code=1, stderr="HTTP 403\n"))
    assert script.gh("repos/o/r") == script.Failure(403, "HTTP 403")
    assert capsys.readouterr().out == "::warning::gh api repos/o/r failed: HTTP 403\n"


@pytest.mark.parametrize(
    ("said", "status", "transient"),
    [
        # As the GitHub CLI says it: GitHub's message, then the status of its answer.
        ("gh: Not Found (HTTP 404)", 404, False),
        ("gh: Resource not accessible by integration (HTTP 403)", 403, False),
        ("gh: Bad credentials (HTTP 401)", 401, False),
        ("gh: Validation Failed (HTTP 422)", 422, False),
        # An answer without a message of GitHub's.
        ("gh: HTTP 502", 502, True),
        ("gh: Service Unavailable (HTTP 503)", 503, True),
        ("gh: HTTP 408", 408, True),
        ("gh: HTTP 429", 429, True),
        # GitHub refuses a call over a rate limit with 403 or 429, and lifts it later.
        ("gh: API rate limit exceeded for installation ID 1. (HTTP 403)", 403, True),
        ("gh: You have exceeded a secondary rate limit. (HTTP 429)", 429, True),
        # No answer came.
        (
            "error connecting to api.github.com\n"
            "check your internet connection or https://githubstatus.com",
            None,
            True,
        ),
        ("", None, True),
    ],
)
def test_a_failure_names_the_status_of_github_s_answer(
    monkeypatch, capsys, said, status, transient
):
    """Only a read that may succeed is worth a read again: GitHub repeats every
    error of the client but a timeout and a rate limit on every read."""
    monkeypatch.setattr(subprocess, "run", completed(code=1, stderr=f"{said}\n"))
    failure = script.gh("repos/o/r")
    assert failure == script.Failure(status, said)
    assert failure.transient is transient
    assert capsys.readouterr().out.startswith("::warning::gh api repos/o/r failed: ")


@pytest.mark.parametrize(
    ("event", "changed", "files", "runs", "reason"),
    [
        ("push", 1, None, True, "A run of push calls every reusable workflow."),
        (
            "workflow_dispatch",
            0,
            None,
            True,
            "A run of workflow_dispatch calls every reusable workflow.",
        ),
        (
            "pull_request",
            3000,
            None,
            True,
            "The pull request changes 3000 files, more than GitHub lists; every "
            "reusable workflow runs.",
        ),
        (
            "pull_request",
            2,
            None,
            True,
            "The files of the pull request are unknown; every reusable workflow runs.",
        ),
        (
            "pull_request",
            2,
            ["README.md", "prompts/triage.md"],
            True,
            "The pull request changes prompts/triage.md, which the dry runs run or "
            "read; every reusable workflow runs.",
        ),
        (
            "pull_request",
            2,
            ["README.md", "docs/faq.md"],
            False,
            "The pull request changes nothing that the dry runs of the reusable "
            "workflows run or read; they are skipped.",
        ),
    ],
)
def test_a_pull_request_runs_the_calls_when_it_changes_what_they_run(
    event, changed, files, runs, reason
):
    asked = []

    def listed():
        asked.append(True)
        return files

    assert script.decide(event, changed, listed) == (runs, reason)
    # Only a pull request with fewer files than GitHub lists asks for them.
    assert asked == ([True] if event == "pull_request" and changed < 3000 else [])


def test_the_files_of_a_pull_request_count_a_renamed_file_by_its_old_name_too(
    monkeypatch,
):
    seen = []

    def run(arguments, **options):
        seen.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "a b.md\nold.md\n", "")

    monkeypatch.setattr(subprocess, "run", run)
    assert script.files_of("o/r", "12") == ["a b.md", "old.md"]
    assert seen == [
        [
            "gh",
            "api",
            "repos/o/r/pulls/12/files?per_page=100",
            "--paginate",
            "--jq",
            ".[] | .filename, (.previous_filename // empty)",
            *VERSION,
        ]
    ]
    monkeypatch.setattr(subprocess, "run", completed(code=1))
    assert script.files_of("o/r", "12") is None


@pytest.mark.parametrize("files", ["docs/faq.md\n", "issue_assistant/cli.py\n"])
def test_changes_writes_its_decision_and_why(monkeypatch, tmp_path, capsys, files):
    monkeypatch.setattr(subprocess, "run", completed(files))
    output, summary = tmp_path / "output", tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GH_REPO", "o/r")
    monkeypatch.setenv("EVENT", "pull_request")
    monkeypatch.setenv("NUMBER", "12")
    monkeypatch.setenv("CHANGED", "1")
    monkeypatch.setattr(sys, "argv", ["workflow_test.py", "changes"])
    assert script.main() == 0
    runs = files.startswith("issue_assistant")
    assert output.read_text("utf-8") == f"run={str(runs).lower()}\n"
    reason = capsys.readouterr().out
    assert summary.read_text("utf-8") == reason
    assert ("issue_assistant/cli.py" in reason) is runs


def test_changes_runs_everything_outside_a_pull_request(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", completed(code=1))
    for name in ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY", "NUMBER", "CHANGED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GH_REPO", "o/r")
    monkeypatch.setenv("EVENT", "push")
    monkeypatch.setattr(sys, "argv", ["workflow_test.py", "changes"])
    assert script.main() == 0
    assert capsys.readouterr().out == "A run of push calls every reusable workflow.\n"


ALERT = {"annotation_level": "failure", "message": "Alert #7 is open: https://x/7"}
EXIT = {"annotation_level": "failure", "message": "Process completed with exit code 1."}
WARNING = {"annotation_level": "warning", "message": "Node 20 is deprecated."}
TRACEBACK = {"annotation_level": "failure", "message": "Traceback (most recent call)"}


@pytest.mark.parametrize(
    ("annotations", "tolerated"),
    [
        ([ALERT, EXIT, WARNING], True),
        ([ALERT], True),
        # The job failed for another reason, or GitHub didn't say why.
        ([ALERT, TRACEBACK], False),
        ([EXIT], False),
        ([], False),
        (None, False),
        ([{"annotation_level": "failure"}], False),
    ],
)
def test_only_open_alerts_excuse_a_failed_dry_run_of_the_findings(
    annotations, tolerated
):
    assert script.tolerated({}, annotations) is tolerated


def job(call, name, conclusion, steps=()):
    return {
        "id": 42,
        "name": f"{call} / {name}",
        "status": "completed",
        "conclusion": conclusion,
        "steps": [
            {"name": step, "status": "completed", "conclusion": result}
            for step, result in steps
        ],
    }


def jobs_as_they_should_be():
    """The jobs of a run in which every call ran as it has to."""
    return [
        job(call, name, "skipped")
        if plan is None
        else job(
            call,
            name,
            "success",
            [
                ("Make sure that GitHub names the commit of this workflow", "success"),
                (plan, "success"),
            ],
        )
        for call, expected in script.CALLS.items()
        for name, plan in expected.items()
    ]


def no_annotations(job):
    raise AssertionError("asked for the annotations of a job that passed")


def test_a_run_in_which_every_call_ran_as_it_has_to_passes(capsys):
    rows, problems = script.check(jobs_as_they_should_be(), no_annotations)
    assert problems == []
    assert rows[0] == (
        "| reusable-labels.yml / Synchronize labels | passes, with “Create and update "
        "the labels” | passed |"
    )
    assert (
        "| reusable-issue-assistant.yml / Analyze with the AI engine | skipped | "
        "skipped |"
    ) in rows
    assert len(rows) == sum(len(expected) for expected in script.CALLS.values())
    assert capsys.readouterr().out == ""


FINDINGS = "reusable-findings.yml"
FINDINGS_JOB = "Accept or report findings"
FINDINGS_PLAN = "Dismiss accepted findings and report the others"
LABELS = "reusable-labels.yml"
LABELS_JOB = "Synchronize labels"


def replaced(*jobs):
    """The jobs of a good run, with these in place of theirs or, None, without."""
    names = {name for name, _ in jobs}
    kept = [item for item in jobs_as_they_should_be() if item["name"] not in names]
    return kept + [item for _, item in jobs if item is not None]


@pytest.mark.parametrize(
    ("jobs", "result", "problem"),
    [
        # A broken condition skips the job, or GitHub never lists it.
        (
            replaced((f"{LABELS} / {LABELS_JOB}", job(LABELS, LABELS_JOB, "skipped"))),
            "skipped",
            "it was skipped, but the call has to run it",
        ),
        (
            replaced((f"{LABELS} / {LABELS_JOB}", None)),
            "missing",
            "GitHub ran no such job; did its name or its condition change?",
        ),
        # The job passed, but its plan was skipped, such as by a broken condition.
        (
            replaced(
                (
                    f"{LABELS} / {LABELS_JOB}",
                    job(
                        LABELS,
                        LABELS_JOB,
                        "success",
                        [("Create and update the labels", "skipped")],
                    ),
                )
            ),
            "success, its plan skipped",
            "it ended as success, and its step “Create and update the labels” as "
            "skipped",
        ),
        # The job failed before it reached its plan, such as in its first step.
        (
            replaced(
                (
                    f"{LABELS} / {LABELS_JOB}",
                    job(LABELS, LABELS_JOB, "failure", [("Set up job", "failure")]),
                )
            ),
            "failure",
            "it has no step “Create and update the labels”, which writes the plan of "
            "its dry run",
        ),
        (
            replaced(
                (
                    f"{LABELS} / {LABELS_JOB}",
                    job(
                        LABELS,
                        LABELS_JOB,
                        "failure",
                        [("Create and update the labels", "failure")],
                    ),
                )
            ),
            "failure, its plan failure",
            "it ended as failure, and its step “Create and update the labels” as "
            "failure",
        ),
    ],
)
def test_a_job_that_did_not_run_as_it_has_to_fails_the_check(jobs, result, problem):
    rows, problems = script.check(jobs, lambda job: [])
    (row,) = [row for row in rows if row.startswith(f"| {LABELS} / ")]
    assert row.endswith(f"| {result} |")
    assert problems == [f"{LABELS} / {LABELS_JOB}: {problem}."]


def test_a_job_that_its_call_has_to_skip_fails_the_check_when_it_runs():
    name = "reusable-discussion-assistant.yml / Handle the event"
    jobs = replaced(
        (name, job("reusable-discussion-assistant.yml", "Handle the event", "failure"))
    )
    rows, problems = script.check(jobs, no_annotations)
    assert f"| {name} | skipped | failure |" in rows
    assert problems == [f"{name}: it ended as failure, but the call has to skip it."]
    # A job that GitHub doesn't list for a skipped call ran neither.
    rows, problems = script.check(replaced((name, None)), no_annotations)
    assert f"| {name} | skipped | not listed, so not run |" in rows
    assert problems == []


def test_a_job_without_a_conclusion_shows_its_status():
    name = f"{LABELS} / {LABELS_JOB}"
    jobs = replaced((name, {"name": name, "status": "in_progress"}))
    rows, problems = script.check(jobs, no_annotations)
    assert problems == [
        f"{name}: GitHub lists none of its steps, so none shows the step “Create and "
        "update the labels”, which writes the plan of its dry run."
    ]
    jobs = replaced(
        (name, {"name": name, "steps": [{"name": "Create and update the labels"}]})
    )
    rows, problems = script.check(jobs, no_annotations)
    assert problems == [
        f"{name}: it ended as unknown, and its step “Create and update the labels” "
        "as unknown."
    ]


@pytest.mark.parametrize(
    ("annotations", "passes"),
    [([ALERT, EXIT], True), ([TRACEBACK, EXIT], False), (None, False)],
)
def test_open_alerts_of_the_default_branch_fail_only_the_findings_job(
    capsys, annotations, passes
):
    """A pull request can't close an alert of main before its merge: the dry run
    of the findings ran, and the Findings workflow on main reports the alert."""
    name = f"{FINDINGS} / {FINDINGS_JOB}"
    failed = job(FINDINGS, FINDINGS_JOB, "failure", [(FINDINGS_PLAN, "failure")])
    asked = []

    def read(job):
        asked.append(job["id"])
        return annotations

    rows, problems = script.check(replaced((name, failed)), read)
    assert asked == [42]
    out = capsys.readouterr().out
    if passes:
        assert problems == []
        assert f"| {name} | passes, with “{FINDINGS_PLAN}” | failed on open alerts" in (
            "\n".join(rows)
        )
        assert out.startswith(f"::warning::{name} failed on open alerts of code ")
    else:
        assert problems == [
            f"{name}: it ended as failure, and its step “{FINDINGS_PLAN}” as failure."
        ]
        assert out == ""
    # Another job's failure is never excused, nor one before the plan.
    labels = job(
        LABELS, LABELS_JOB, "failure", [("Create and update the labels", "failure")]
    )
    _, problems = script.check(replaced((f"{LABELS} / {LABELS_JOB}", labels)), read)
    assert len(problems) == 1
    early = job(FINDINGS, FINDINGS_JOB, "failure", [(FINDINGS_PLAN, "skipped")])
    _, problems = script.check(replaced((name, early)), read)
    assert len(problems) == 1


def test_the_jobs_and_annotations_come_from_the_api(monkeypatch):
    seen = []

    def run(arguments, **options):
        seen.append(arguments)
        if "annotations" in arguments[2]:
            return subprocess.CompletedProcess(arguments, 0, json.dumps([ALERT]), "")
        return subprocess.CompletedProcess(arguments, 0, '{"jobs": [{"id": 1}]}', "")

    monkeypatch.setattr(subprocess, "run", run)
    assert script.jobs_of("o/r", "7") == [{"id": 1}]
    assert script.annotations_of("o/r")({"id": 9}) == [ALERT]
    # The latest attempt of each job, also after a run again of the failed ones.
    assert seen == [
        [
            "gh",
            "api",
            "repos/o/r/actions/runs/7/jobs?filter=latest&per_page=100",
            *VERSION,
        ],
        ["gh", "api", "repos/o/r/check-runs/9/annotations?per_page=100", *VERSION],
    ]
    monkeypatch.setattr(subprocess, "run", completed(code=1, stderr="HTTP 404"))
    assert script.jobs_of("o/r", "7") == script.Failure(404, "HTTP 404")
    assert script.annotations_of("o/r")({"id": 9}) is None


def listing(*reads):
    """A GitHub CLI that lists these jobs, one list after the other, and fails for
    None with an error of GitHub's servers, or for a text with that text as what it
    says; the last one stays."""
    left = list(reads)

    def run(arguments, **options):
        jobs = left.pop(0) if len(left) > 1 else left[0]
        if jobs is None or isinstance(jobs, str):
            return subprocess.CompletedProcess(arguments, 1, "", jobs or "HTTP 502")
        return subprocess.CompletedProcess(arguments, 0, json.dumps({"jobs": jobs}), "")

    return run


SLEPT: list[float] = []


def verify(monkeypatch, tmp_path, listed, *later, **env):
    """The exit code of the gate and its summary, with `listed` as the jobs that
    GitHub lists, or a failure of the GitHub CLI as in `listing()` (for None an
    error of GitHub's servers, for a text a failure that says that text), and
    `later` as what it lists when the gate reads the jobs again. The gate doesn't really wait."""
    monkeypatch.setattr(subprocess, "run", listing(listed, *later))
    monkeypatch.setattr(script.time, "sleep", SLEPT.append)
    SLEPT.clear()
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GH_REPO", "o/r")
    monkeypatch.setenv("RUN", "7")
    for name in ("CHANGES", "WANTED"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["workflow_test.py"])
    code = script.main()
    return code, summary.read_text("utf-8") if summary.exists() else ""


def test_the_gate_passes_when_every_call_ran_as_it_has_to(
    monkeypatch, tmp_path, capsys
):
    code, summary = verify(
        monkeypatch,
        tmp_path,
        jobs_as_they_should_be(),
        CHANGES="success",
        WANTED="true",
    )
    assert code == 0
    assert summary.startswith(
        f"{script.TITLE}\n\n| Job | Expected | Result |\n| --- | --- | --- |\n"
    )
    assert summary.endswith(
        "\n\nEach reusable workflow ran as a dry run, as its call has to.\n"
    )
    assert capsys.readouterr().out == (
        "Each reusable workflow ran as a dry run, as its call has to.\n"
    )


def test_the_gate_names_every_job_that_did_not_run_as_it_has_to(
    monkeypatch, tmp_path, capsys
):
    jobs = replaced((f"{LABELS} / {LABELS_JOB}", None))
    code, summary = verify(
        monkeypatch, tmp_path, jobs, CHANGES="success", WANTED="true"
    )
    assert code == 1
    assert summary.endswith("\n\n1 of the jobs didn't run as their call has to.\n")
    assert capsys.readouterr().out == (
        f"::error::{LABELS} / {LABELS_JOB}: GitHub ran no such job; did its name or "
        "its condition change?.\n"
        "1 of the jobs didn't run as their call has to.\n"
    )


# The jobs that have to run, each with the step that writes its plan.
RAN = sum(
    plan is not None for called in script.CALLS.values() for plan in called.values()
)


def too_early(jobs):
    """The jobs as GitHub listed them for minutes on 2026-11-08 (#288): each job that
    ran, and passed, without any of its steps."""
    return [
        {**item, "steps": []} if item["conclusion"] == "success" else item
        for item in jobs
    ]


def test_the_gate_waits_until_github_lists_the_steps_of_every_job(
    monkeypatch, tmp_path, capsys
):
    """GitHub lists the steps of a job some time after it ends: the gate read the
    jobs too early and failed on each job that had passed (#288)."""
    good = jobs_as_they_should_be()
    code, summary = verify(
        monkeypatch,
        tmp_path,
        too_early(good),
        too_early(good),
        good,
        CHANGES="success",
        WANTED="true",
    )
    assert code == 0
    assert SLEPT == [script.PAUSE, script.PAUSE]
    early = (
        f"GitHub lists {RAN} of the jobs without the steps that they ran yet, such "
        f"as {LABELS} / {LABELS_JOB}; reading the jobs again in 20 s.\n"
    )
    assert capsys.readouterr().out == (
        early * 2 + "GitHub listed every job as it ended after 40 s.\n"
        "Each reusable workflow ran as a dry run, as its call has to.\n"
    )
    assert "| passes, with “Create and update the labels” | passed |" in summary


def test_the_gate_fails_when_github_lists_a_job_too_early_for_too_long(
    monkeypatch, tmp_path, capsys
):
    code, summary = verify(
        monkeypatch,
        tmp_path,
        too_early(jobs_as_they_should_be()),
        CHANGES="success",
        WANTED="true",
    )
    assert code == 1
    assert SLEPT == [script.PAUSE] * script.READS
    out = capsys.readouterr().out.splitlines()
    assert out[script.READS] == (
        f"::warning::GitHub still lists {RAN} of the jobs without the steps that "
        "they "
        "ran, after 5 minutes. Where the check of such a job shows the step that "
        "writes its plan, run this check again."
    )
    assert out[script.READS + 1] == (
        f"::error::{LABELS} / {LABELS_JOB}: GitHub lists none of its steps, so none "
        "shows the step “Create and update the labels”, which writes the plan of "
        "its dry run."
    )
    assert summary.endswith(f"\n\n{RAN} of the jobs didn't run as their call has to.\n")


def test_the_gate_reads_the_jobs_again_when_github_fails_to_list_them(
    monkeypatch, tmp_path, capsys
):
    """A failed read keeps what GitHub listed before, if anything."""
    good = jobs_as_they_should_be()
    code, _ = verify(
        monkeypatch,
        tmp_path,
        None,
        too_early(good),
        None,
        good,
        CHANGES="success",
        WANTED="true",
    )
    assert code == 0
    assert SLEPT == [script.PAUSE] * 3
    out = capsys.readouterr().out
    assert out.startswith(
        "::warning::gh api repos/o/r/actions/runs/7/jobs?filter=latest&per_page=100 "
        "failed: HTTP 502\n"
        "GitHub didn't list the jobs of this run; reading them again in 20 s.\n"
        f"GitHub lists {RAN} of the jobs without"
    )
    assert out.count(f"GitHub lists {RAN} of the jobs without") == 2
    assert "GitHub listed every job as it ended after 60 s.\n" in out


REFUSED = "gh: Resource not accessible by integration (HTTP 403)"


def refusal(status, said, hint):
    return (
        f"::error::GitHub refused to list the jobs of this run with HTTP {status} "
        f"({said}), which every read again would repeat, so the check didn't wait. "
        f"{hint}\n"
    )


def test_the_gate_fails_at_once_when_github_refuses_to_list_the_jobs(
    monkeypatch, tmp_path, capsys
):
    """A read again only repeats an error of the client: the gate read the jobs
    for five minutes before it failed, and then asked for a run again (#327)."""
    code, summary = verify(
        monkeypatch, tmp_path, REFUSED, CHANGES="success", WANTED="true"
    )
    assert code == 1
    assert SLEPT == []
    assert capsys.readouterr().out == (
        "::warning::gh api repos/o/r/actions/runs/7/jobs?filter=latest&per_page=100 "
        f"failed: {REFUSED}\n"
        + refusal(
            403, REFUSED, "Does the job still have the permission `actions: read`?"
        )
    )
    assert summary == ""


def test_the_gate_stops_reading_when_github_refuses_a_read_again(
    monkeypatch, tmp_path, capsys
):
    good = jobs_as_they_should_be()
    code, _ = verify(
        monkeypatch,
        tmp_path,
        too_early(good),
        "gh: Not Found (HTTP 404)",
        good,
        CHANGES="success",
        WANTED="true",
    )
    assert code == 1
    assert SLEPT == [script.PAUSE]
    assert capsys.readouterr().out.endswith(
        refusal(
            404,
            "gh: Not Found (HTTP 404)",
            "Do `GH_REPO` and `RUN` name this run of this repository?",
        )
    )


@pytest.mark.parametrize(
    ("said", "named", "hint"),
    [
        (
            "gh: Bad credentials (HTTP 401)",
            "gh: Bad credentials (HTTP 401)",
            "Is the token of the job still valid?",
        ),
        (
            "gh: Validation Failed (HTTP 422)\n{}",
            "gh: Validation Failed (HTTP 422)",
            "Is the request of the check still valid?",
        ),
    ],
)
def test_a_refusal_names_githubs_message_and_where_to_look(
    monkeypatch, tmp_path, capsys, said, named, hint
):
    """A refusal other than 403 has nothing to do with `actions: read`."""
    code, _ = verify(monkeypatch, tmp_path, said, CHANGES="success", WANTED="true")
    assert code == 1
    status = int(named[-4:-1])
    assert capsys.readouterr().out.endswith(refusal(status, named, hint))


def test_the_gate_reads_the_jobs_again_after_a_rate_limit(
    monkeypatch, tmp_path, capsys
):
    """GitHub lifts a rate limit later, though it answers it with 403."""
    good = jobs_as_they_should_be()
    code, _ = verify(
        monkeypatch,
        tmp_path,
        "gh: API rate limit exceeded for installation ID 1. (HTTP 403)",
        good,
        CHANGES="success",
        WANTED="true",
    )
    assert code == 0
    assert SLEPT == [script.PAUSE]
    assert "GitHub didn't list the jobs of this run; reading them again in 20 s.\n" in (
        capsys.readouterr().out
    )


STEPS_RAN = [("Set up job", "success"), ("Create and update the labels", "success")]


@pytest.mark.parametrize(
    ("listed", "late"),
    [
        # Too early: passed without its plan, not completed, or ran without a step.
        (job(LABELS, LABELS_JOB, "success"), True),
        (job(LABELS, LABELS_JOB, "success", [("Set up job", "success")]), True),
        ({**job(LABELS, LABELS_JOB, None), "status": "in_progress"}, True),
        (job(LABELS, LABELS_JOB, "failure"), True),
        (job(LABELS, LABELS_JOB, "cancelled"), True),
        (
            {
                **job(LABELS, LABELS_JOB, "success"),
                "steps": [
                    {
                        "name": "Set up job",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "name": "Create and update the labels",
                        "status": "in_progress",
                        "conclusion": None,
                    },
                ],
            },
            True,
        ),
        # As it ended: with its plan, failed before its plan, skipped, or not listed.
        (job(LABELS, LABELS_JOB, "success", STEPS_RAN), False),
        (job(LABELS, LABELS_JOB, "failure", STEPS_RAN), False),
        (job(LABELS, LABELS_JOB, "failure", [("Set up job", "failure")]), False),
        (job(LABELS, LABELS_JOB, "skipped"), False),
        (None, False),
    ],
)
def test_a_job_that_has_to_run_waits_while_github_lists_it_too_early(listed, late):
    name = f"{LABELS} / {LABELS_JOB}"
    assert script.unsettled(replaced((name, listed))) == ([name] if late else [])


def test_a_job_that_its_call_skips_never_waits():
    """Its steps say nothing: whatever it did, its conclusion is the verdict."""
    name = "reusable-issue-assistant.yml / Analyze with the AI engine"
    ran = job("reusable-issue-assistant.yml", "Analyze with the AI engine", "success")
    assert script.unsettled(replaced((name, ran))) == []
    assert script.unsettled(jobs_as_they_should_be()) == []


def test_the_gate_passes_when_nothing_had_to_run(monkeypatch, tmp_path, capsys):
    code, summary = verify(
        monkeypatch, tmp_path, None, CHANGES="success", WANTED="false"
    )
    assert code == 0
    line = (
        "Nothing that the dry runs of the reusable workflows run or read changed; "
        "no call had to run."
    )
    assert summary == f"{script.TITLE}\n\n{line}\n"
    assert capsys.readouterr().out == f"{line}\n"


@pytest.mark.parametrize(
    ("changes", "named"), [("failure", "failure"), ("", "unknown")]
)
def test_the_gate_fails_when_nothing_says_whether_the_calls_had_to_run(
    monkeypatch, tmp_path, capsys, changes, named
):
    code, summary = verify(monkeypatch, tmp_path, [], CHANGES=changes, WANTED="")
    assert code == 1
    assert summary == ""
    assert capsys.readouterr().out == (
        "::error::The job that decides whether the reusable workflows run ended as "
        f"{named}, so nothing says whether they had to run.\n"
    )


def test_the_gate_fails_when_github_lists_no_jobs(monkeypatch, tmp_path, capsys):
    code, _ = verify(monkeypatch, tmp_path, None, CHANGES="success", WANTED="true")
    assert code == 1
    assert SLEPT == [script.PAUSE] * script.READS
    out = capsys.readouterr().out
    assert out.count("reading them again in 20 s.") == script.READS
    assert out.endswith(
        "::error::GitHub didn't list the jobs of this run; run it again.\n"
    )


def test_the_script_runs_as_a_program(monkeypatch, capsys):
    monkeypatch.setenv("CHANGES", "success")
    monkeypatch.setenv("WANTED", "false")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(sys, "argv", ["workflow_test.py"])
    with pytest.raises(SystemExit) as stop:
        runpy.run_path(str(ROOT / "scripts" / "workflow_test.py"), run_name="__main__")
    assert stop.value.code == 0
    assert capsys.readouterr().out.startswith("Nothing that the dry runs")
