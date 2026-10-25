"""The Canary measures how long the AI's job waits for its runner.

GitHub's egress-firewall runner is a technical preview: a job whose runner is gone
waits in the queue for up to a day, and every later run of its issue waits behind
it. scripts/canary_queue.py, which the job "the AI's wait for its runner" of
.github/workflows/canary.yml runs, reads the jobs of its run, warns above 10
minutes and fails above 30, also while analyze still waits.
"""

import importlib.util
import json
import re
import runpy
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "canary_queue", ROOT / "scripts" / "canary_queue.py"
)
assert _spec and _spec.loader
queue = importlib.util.module_from_spec(_spec)
# The dataclasses of the module look it up by its name.
sys.modules[_spec.name] = queue
_spec.loader.exec_module(queue)

CANARY = yaml.safe_load((ROOT / ".github/workflows/canary.yml").read_text("utf-8"))
START = datetime(2026, 10, 26, 7, 23, tzinfo=UTC)
CALL = "Issue assistant"


def at(minutes):
    """The time of the jobs API, `minutes` after START."""
    return (START + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def job(name, status="queued", conclusion=None, **times):
    return {
        "name": f"{CALL} / {name}",
        "status": status,
        "conclusion": conclusion,
        **{key: at(value) for key, value in times.items()},
    }


EVENT_DONE = job(
    "Handle the event", "completed", "success", created_at=0, completed_at=1
)
WATCHER = {"name": "the AI's wait for its runner", "status": "in_progress"}


def test_the_job_of_the_canary_runs_the_script_beside_the_call():
    jobs = CANARY["jobs"]
    watcher = jobs["queue"]
    assert watcher["name"] == queue.WATCHER
    # Beside the call, not after it: a call that waits for its runner doesn't end.
    assert "needs" not in watcher
    assert watcher["if"] == jobs["assistant"]["if"]
    assert watcher["permissions"] == {"actions": "read", "issues": "write"}
    (measure,) = [
        step
        for step in watcher["steps"]
        if step.get("run") == "python3 scripts/canary_queue.py"
    ]
    assert measure["env"]["RUN"] == "${{ github.run_id }}"
    assert measure["env"]["ATTEMPT"] == "${{ github.run_attempt }}"
    # The names that the script looks for are those of the reusable workflow.
    called = yaml.safe_load(
        (ROOT / ".github/workflows/reusable-issue-assistant.yml").read_text("utf-8")
    )["jobs"]
    assert called["analyze"]["name"] == queue.ANALYZE
    assert called["event"]["name"] == queue.EVENT
    assert called["analyze"]["needs"] == "event"
    # Long enough for the call to wait for an earlier canary, then FAIL.
    assert watcher["timeout-minutes"] * 60 > queue.FAIL.total_seconds() * 2


@pytest.mark.parametrize(
    ("jobs", "now", "expected"),
    [
        # Nothing to tell yet: the call waits for an earlier canary, or the event runs.
        ([], 60, None),
        ([WATCHER], 60, None),
        ([job("Handle the event", "in_progress", created_at=0)], 60, None),
        (
            [
                job("Handle the event", "in_progress", created_at=0),
                job("Analyze with the AI engine", "pending", created_at=0),
            ],
            90,
            None,
        ),
        # analyze waits, but not yet for long.
        ([EVENT_DONE, job("Analyze with the AI engine", created_at=0)], 31, None),
        # It started: from the end of the event, which it needs.
        (
            [
                EVENT_DONE,
                job(
                    "Analyze with the AI engine",
                    "in_progress",
                    created_at=0,
                    started_at=3,
                ),
            ],
            5,
            queue.Wait(timedelta(minutes=2), started=True),
        ),
        # From its creation, when GitHub created it after the event ended.
        (
            [
                EVENT_DONE,
                job(
                    "Analyze with the AI engine",
                    "completed",
                    "success",
                    created_at=2,
                    started_at=14,
                ),
            ],
            20,
            queue.Wait(timedelta(minutes=12), started=True),
        ),
        # Without the event in the list, from its creation; never below zero.
        (
            [
                job(
                    "Analyze with the AI engine",
                    "in_progress",
                    created_at=5,
                    started_at=4,
                )
            ],
            6,
            queue.Wait(timedelta(), started=True),
        ),
        # It still waits after FAIL.
        (
            [EVENT_DONE, job("Analyze with the AI engine", created_at=0)],
            32,
            queue.Wait(timedelta(minutes=31)),
        ),
        (
            [{"name": f"{CALL} / Analyze with the AI engine", "status": "queued"}],
            0,
            None,
        ),
        # Nothing to measure.
        (
            [EVENT_DONE, job("Analyze with the AI engine", "completed", "skipped")],
            2,
            queue.Wait(None, reason="analyze was skipped"),
        ),
        # The event passed, and GitHub doesn't list analyze yet: it could start.
        ([EVENT_DONE, WATCHER], 2, None),
        ([EVENT_DONE, WATCHER], 32, queue.Wait(timedelta(minutes=31))),
        (
            [
                job("Handle the event", "completed", "failure", completed_at=1),
                WATCHER,
            ],
            2,
            queue.Wait(None, reason="no job of the run is left to start analyze"),
        ),
        (
            [{"name": CALL, "status": "completed", "conclusion": "cancelled"}, WATCHER],
            2,
            queue.Wait(None, reason="no job of the run is left to start analyze"),
        ),
        (
            [
                EVENT_DONE,
                job(
                    "Analyze with the AI engine",
                    "completed",
                    "cancelled",
                    created_at=1,
                ),
            ],
            2,
            queue.Wait(None, reason="analyze ended without a start, as when cancelled"),
        ),
    ],
)
def test_the_wait_counts_from_when_analyze_could_start(jobs, now, expected):
    assert queue.look(jobs, START + timedelta(minutes=now)) == expected


@pytest.mark.parametrize(
    ("wait", "code", "message"),
    [
        (
            queue.Wait(timedelta(minutes=1, seconds=5), started=True),
            0,
            "analyze waited 1 min 5 s for its runner (warns above 10 min 0 s, "
            "fails above 30 min 0 s).",
        ),
        (
            queue.Wait(timedelta(minutes=10), started=True),
            0,
            "analyze waited 10 min 0 s for its runner (warns above 10 min 0 s, "
            "fails above 30 min 0 s).",
        ),
        (
            queue.Wait(timedelta(minutes=12), started=True),
            0,
            "::warning::analyze waited 12 min 0 s for its runner (warns above "
            "10 min 0 s, fails above 30 min 0 s).",
        ),
        (
            queue.Wait(timedelta(minutes=30), started=True),
            0,
            "::warning::analyze waited 30 min 0 s for its runner (warns above "
            "10 min 0 s, fails above 30 min 0 s).",
        ),
        (
            queue.Wait(timedelta(minutes=45), started=True),
            1,
            "::error::analyze waited 45 min 0 s for its runner (warns above "
            f"10 min 0 s, fails above 30 min 0 s). {queue.ADVICE}",
        ),
        (
            queue.Wait(timedelta(minutes=31)),
            1,
            "::error::analyze still waits for its runner after 31 min 0 s (warns "
            f"above 10 min 0 s, fails above 30 min 0 s). {queue.ADVICE}",
        ),
        (
            queue.Wait(None, reason="analyze was skipped"),
            0,
            "::notice::Nothing to measure: analyze was skipped.",
        ),
    ],
)
def test_the_canary_warns_above_10_minutes_and_fails_above_30(wait, code, message):
    found, command, line = queue.verdict(wait)
    assert (found, command) == (code, message)
    # The summary has the message without its command.
    assert line == re.sub(r"^::[a-z]+::", "", message)


def test_a_failure_says_what_to_do():
    assert "ISSUE_ASSISTANT_AI_RUNNER" in queue.ADVICE
    decision = queue.ADVICE.rpartition("/docs/decisions/")[2]
    assert (ROOT / "docs" / "decisions" / decision).is_file()


def test_the_script_looks_until_the_wait_can_be_told():
    answers = iter(
        [
            None,
            [WATCHER],
            None,
            None,
            [EVENT_DONE, job("Analyze with the AI engine", created_at=0)],
            [
                EVENT_DONE,
                job(
                    "Analyze with the AI engine",
                    "in_progress",
                    created_at=0,
                    started_at=2,
                ),
            ],
        ]
    )
    sleeps = []
    wait = queue.watch(
        lambda: next(answers), lambda: START + timedelta(minutes=3), sleeps.append
    )
    assert wait == queue.Wait(timedelta(minutes=1), started=True)
    assert sleeps == [queue.POLL] * 5


def test_the_script_gives_up_when_github_lists_no_jobs_again_and_again():
    sleeps = []
    with pytest.raises(SystemExit) as stopped:
        queue.watch(lambda: None, lambda: START, sleeps.append)
    assert str(stopped.value) == (
        "::error::GitHub didn't list the jobs of the run 5 times in a row."
    )
    assert len(sleeps) == queue.MOST_FAILED_LOOKS - 1


def test_the_jobs_come_from_the_attempt_of_the_run(monkeypatch, capsys):
    calls = []

    def run(arguments, **options):
        calls.append(arguments)
        listed = {"total_count": 1, "jobs": [WATCHER]}
        if len(calls) == 1:
            return subprocess.CompletedProcess(arguments, 1, "", "HTTP 502\n")
        return subprocess.CompletedProcess(arguments, 0, json.dumps(listed), "")

    monkeypatch.setattr(queue.subprocess, "run", run)
    assert queue.jobs_of("o/r", "7", "2") is None
    assert (
        capsys.readouterr().out == "::warning::GitHub didn't list the jobs: HTTP 502\n"
    )
    assert queue.jobs_of("o/r", "7", "2") == [WATCHER]
    assert calls[0] == [
        "gh",
        "api",
        "repos/o/r/actions/runs/7/attempts/2/jobs?per_page=100",
    ]


@pytest.mark.parametrize(
    ("attempt", "summary"), [("3", True), ("", False)], ids=["summary", "log only"]
)
def test_main_writes_the_wait_into_the_summary(
    monkeypatch, tmp_path, capsys, attempt, summary
):
    asked = []

    def jobs_of(repository, run, attempt):
        asked.append((repository, run, attempt))
        return [
            EVENT_DONE,
            job(
                "Analyze with the AI engine",
                "in_progress",
                created_at=0,
                started_at=45,
            ),
        ]

    monkeypatch.setattr(queue, "jobs_of", jobs_of)
    monkeypatch.setenv("GH_REPO", "o/r")
    monkeypatch.setenv("RUN", "7")
    monkeypatch.setenv("ATTEMPT", attempt)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    path = tmp_path / "summary.md"
    if summary:
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))
    else:
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert queue.main() == 1
    assert asked == [("o/r", "7", attempt or "1")]
    out = capsys.readouterr().out
    assert out.startswith("::error::analyze waited 44 min 0 s for its runner")
    if summary:
        written = path.read_text("utf-8")
        assert written.startswith(f"{queue.TITLE}\n\nanalyze waited 44 min 0 s")
    else:
        assert not path.exists()


def test_the_script_runs_as_a_program(monkeypatch, capsys):
    listed = {
        "jobs": [
            EVENT_DONE,
            job(
                "Analyze with the AI engine",
                "completed",
                "success",
                created_at=0,
                started_at=2,
            ),
        ]
    }
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda arguments, **options: subprocess.CompletedProcess(
            arguments, 0, json.dumps(listed), ""
        ),
    )
    monkeypatch.setenv("GH_REPO", "o/r")
    monkeypatch.setenv("RUN", "7")
    monkeypatch.delenv("ATTEMPT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    with pytest.raises(SystemExit) as stop:
        runpy.run_path(str(ROOT / "scripts" / "canary_queue.py"), run_name="__main__")
    assert stop.value.code == 0
    assert capsys.readouterr().out == (
        "analyze waited 1 min 0 s for its runner (warns above 10 min 0 s, fails above "
        "30 min 0 s).\n"
    )


# ---------------------------------------------------------------- the alarm

sys.path.insert(0, str(ROOT / ".github"))
from blueprint import bot_health  # noqa: E402

URL = "https://github.com/o/r/actions/runs/7"


def test_the_alarm_opens_its_issue_only_on_the_default_branch():
    steps = CANARY["jobs"]["queue"]["steps"]
    (measure,) = [step for step in steps if step.get("id") == "measure"]
    assert measure["name"] == queue.MEASURE
    (alarm,) = [
        step for step in steps if "canary_queue.py alarm" in step.get("run", "")
    ]
    assert steps.index(alarm) == steps.index(measure) + 1
    assert "steps.measure.outcome == 'failure'" in alarm["if"]
    assert "github.event.repository.default_branch" in alarm["if"]
    assert alarm["env"]["LINE"] == "${{ steps.measure.outputs.line }}"
    assert CANARY["name"] == queue.WORKFLOW


def test_the_issue_is_one_that_bot_health_takes_over():
    # bot-health finds it by its label and the key in its state, and keeps it up to
    # date once the run ends: the same marker, label, type and mark of the place.
    assert (queue.LABEL, queue.TYPE) == (bot_health.LABEL, bot_health.TYPE)
    assert queue.LABEL_DESCRIPTION == bot_health.LABEL_DESCRIPTION
    assert queue.LABEL_COLOR == bot_health.LABEL_COLOR
    assert queue.MARKER.pattern == bot_health.MARKER.pattern
    state = {"key": queue.KEY, "run": 7, "attempt": 1, "where": "a--b"}
    assert queue.marker(state) == bot_health.marker(state)
    body = queue.alarm_body(state | {"since": "2026-10-26"}, "main", URL, "late")
    assert json.loads(bot_health.MARKER.search(body)[1])["key"] == queue.KEY
    found = [bot_health.Failure(queue.WATCHER, URL, queue.MEASURE, "")]
    assert queue.place() == bot_health.where(None, found)


class FakeApi:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        for (want, prefix), answer in self.answers.items():
            if want == method and path.startswith(prefix):
                return answer
        return {}


def issue(number, state, **extra):
    return {"number": number, "body": queue.marker(state), **extra}


def alarm(run=7, attempt=1):
    return queue.alarm("o/r", run, attempt, "main", URL, "waits 31 min", "2026-10-26")


def test_the_alarm_opens_the_issue_and_the_label(monkeypatch):
    fake = FakeApi(
        {
            ("GET", "repos/o/r/issues"): [
                issue(1, {"key": queue.KEY}, pull_request={}),
                {"number": 2, "body": "no marker"},
                {"number": 3, "body": "<!-- bot-health {bad} -->"},
                issue(4, {"key": ".github/workflows/other.yml"}),
            ],
            ("GET", "repos/o/r/labels/"): None,
            ("POST", "repos/o/r/issues"): {"number": 9},
        }
    )
    monkeypatch.setattr(queue, "api", fake)
    assert alarm() == "opened #9"
    assert fake.calls[2][:2] == ("POST", "repos/o/r/labels")
    method, path, payload = fake.calls[3]
    assert (method, path) == ("POST", "repos/o/r/issues")
    assert payload["title"] == "Canary fails on main"
    assert payload["labels"] == ["bot-failure", "maintenance"]
    state = json.loads(queue.MARKER.search(payload["body"])[1])
    assert state == {
        "attempt": 1,
        "failed": 1,
        "key": queue.KEY,
        "run": 7,
        "since": "2026-10-26",
        "where": queue.place(),
    }
    assert payload["body"].endswith("waits 31 min\n")


def test_the_alarm_needs_no_label_when_there_is_one(monkeypatch):
    fake = FakeApi(
        {("GET", "repos/o/r/issues"): [], ("POST", "repos/o/r/issues"): {"number": 9}}
    )
    monkeypatch.setattr(queue, "api", fake)
    assert alarm() == "opened #9"
    assert [call[:2] for call in fake.calls] == [
        ("GET", "repos/o/r/issues?labels=bot-failure&state=open&per_page=100"),
        ("GET", "repos/o/r/labels/bot-failure"),
        ("POST", "repos/o/r/issues"),
    ]


def test_the_alarm_comments_on_the_open_issue(monkeypatch):
    listed = [issue(5, {"key": queue.KEY, "run": 6, "attempt": 1})]
    fake = FakeApi({("GET", "repos/o/r/issues"): listed})
    monkeypatch.setattr(queue, "api", fake)
    assert alarm() == "commented on #5"
    method, path, payload = fake.calls[1]
    assert (method, path) == ("POST", "repos/o/r/issues/5/comments")
    assert URL in payload["body"]
    assert "waits 31 min" in payload["body"]
    # Once per attempt of a run.
    listed[0] = issue(5, {"key": queue.KEY, "run": 7, "attempt": 1})
    assert alarm() == "#5 already reports this run"


@pytest.mark.parametrize("broken", ["GET", "POST"])
def test_the_alarm_fails_when_github_refuses(monkeypatch, broken):
    answers = {("GET", "repos/o/r/issues"): []}
    answers[(broken, "repos/o/r/issues")] = None
    monkeypatch.setattr(queue, "api", FakeApi(answers))
    with pytest.raises(SystemExit, match="::error::"):
        alarm()


def test_api_calls_the_github_cli(monkeypatch, capsys):
    calls = []
    answers = iter(
        [
            subprocess.CompletedProcess([], 0, '{"number": 3}', ""),
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 1, "", "HTTP 404\n"),
        ]
    )

    def run(arguments, **options):
        calls.append((arguments, options.get("input")))
        return next(answers)

    monkeypatch.setattr(queue.subprocess, "run", run)
    assert queue.api("POST", "repos/o/r/issues", {"title": "t"}) == {"number": 3}
    assert calls[0] == (
        ["gh", "api", "--method", "POST", "repos/o/r/issues", "--input", "-"],
        '{"title": "t"}',
    )
    assert queue.api("GET", "repos/o/r/labels/x") == {}
    assert calls[1] == (["gh", "api", "--method", "GET", "repos/o/r/labels/x"], None)
    assert queue.api("GET", "repos/o/r/labels/y") is None
    assert "GitHub refused GET repos/o/r/labels/y: HTTP 404" in capsys.readouterr().out


@pytest.mark.parametrize("line", ["waits 31 min", ""])
def test_main_raises_the_alarm(monkeypatch, capsys, line):
    seen = []
    monkeypatch.setattr(
        queue, "alarm", lambda *given: seen.append(given) or "opened #9"
    )
    monkeypatch.setattr(queue.sys, "argv", ["canary_queue.py", "alarm"])
    for name, value in {
        "GH_REPO": "o/r",
        "RUN": "7",
        "BRANCH": "main",
        "URL": URL,
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("ATTEMPT", raising=False)
    monkeypatch.setenv("LINE", line)
    assert queue.main() == 0
    assert seen[0][:6] == ("o/r", 7, 1, "main", URL, line or queue.NO_LINE)
    assert capsys.readouterr().out == "::notice::The Canary fails: opened #9.\n"


def test_main_hands_the_line_of_a_failure_to_the_alarm(monkeypatch, tmp_path):
    monkeypatch.setattr(
        queue,
        "watch",
        lambda *given: queue.Wait(timedelta(minutes=39)),
    )
    monkeypatch.setattr(queue.sys, "argv", ["canary_queue.py"])
    monkeypatch.setenv("GH_REPO", "o/r")
    monkeypatch.setenv("RUN", "7")
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert queue.main() == 1
    written = output.read_text("utf-8")
    assert written.startswith("line=analyze still waits for its runner after 39 min")
    assert written.count("\n") == 1
