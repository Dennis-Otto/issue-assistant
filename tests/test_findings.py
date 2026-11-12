"""Findings of code scanning: accepted ones are dismissed, others fail a run once."""

import dataclasses
import json

import pytest

import issue_assistant as assistant
from support import OPENED, REPOSITORY, FakeGitHub, make_alert, run_main

ACCEPTED = """
[[accept]]
tool = "Scorecard"
rule = "CodeReviewID"
reason = "won't fix"
comment = "One maintainer: nobody else can approve a pull request."

[[accept]]
tool = "codeql"
rule = "py/unused-import"
reason = "used in tests"
comment = "Fixtures import what they provide."
path = "tests/"
"""


def write_findings(root, text=ACCEPTED):
    (root / assistant.FINDINGS_FILE).write_text(text, encoding="utf-8")


def test_accepted_findings_are_dismissed_with_their_reason(config, root):
    write_findings(root)
    github = FakeGitHub(config)
    github.alert_store = [
        make_alert(3),
        make_alert(5, "CodeQL", "py/unused-import", "tests/test_x.py"),
    ]
    lines, left = assistant.watch_findings(github, root)
    assert left == 0
    assert lines == [
        "Alert #3 (Scorecard CodeReviewID) dismissed as won't fix.",
        "Alert #5 (codeql py/unused-import) dismissed as used in tests.",
    ]
    first, second = github.alert_store
    assert first["state"] == "dismissed" and first["dismissed_reason"] == "won't fix"
    assert first["dismissed_comment"] == (
        "One maintainer: nobody else can approve a pull request. "
        "(accepted in .github/findings.toml)"
    )
    assert second["dismissed_reason"] == "used in tests"


def test_other_findings_stay_open_and_name_only_number_and_link(config, root):
    write_findings(root)
    github = FakeGitHub(config)
    github.alert_store = [
        make_alert(7, "CodeQL", "py/sql-injection", "app/db.py"),
        make_alert(8, "CodeQL", "py/unused-import", "app/main.py"),
        make_alert(9, "Scorecard", "FuzzingID"),
    ]
    lines, left = assistant.watch_findings(github, root)
    assert left == 3
    assert lines[:3] == [
        f"Alert #{number} is open: https://github.com/owner/project/security/"
        f"code-scanning/{number}"
        for number in (7, 8, 9)
    ]
    assert "accept them with a reason in .github/findings.toml" in lines[-1]
    # Nothing about the rule or the file reaches the public run.
    assert not any("sql" in line or "app/" in line for line in lines)
    assert all(alert["state"] == "open" for alert in github.alert_store)


def test_without_accepted_findings_or_code_scanning(config):
    github = FakeGitHub(config)
    assert assistant.watch_findings(github, config.root) == ([], 0)
    github.alert_store = None
    assert assistant.watch_findings(github, config.root) == (
        ["Code scanning isn't set up; there is nothing to check."],
        0,
    )


def test_other_errors_of_code_scanning_are_raised(config):
    github = FakeGitHub(config)
    github.alert_error = "gh api: Resource not accessible by integration (HTTP 403)"
    with pytest.raises(assistant.GitHubError):
        assistant.watch_findings(github, config.root)


def test_a_dry_run_dismisses_nothing(config, root):
    write_findings(root)
    github = FakeGitHub(config, dry_run=True)
    github.alert_store = [make_alert(3)]
    lines, left = assistant.watch_findings(github, root)
    assert left == 0 and github.alert_store[0]["state"] == "open"
    assert github.writes == ["dismiss alert #3 as won't fix"]


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        (
            '[[accept]]\ntool = "Scorecard"\n',
            "every [[accept]] needs tool, rule, reason and comment (KeyError",
        ),
        ("[[accept]\n", ".github/findings.toml is no valid TOML: "),
        (
            '[[accept]]\ntool = ""\nrule = "x"\nreason = "won\'t fix"\ncomment = "y"\n',
            ".github/findings.toml: name the tool and the rule",
        ),
        (
            '[[accept]]\ntool = "A"\nrule = "B"\nreason = "ignore"\ncomment = "y"\n',
            ".github/findings.toml: A B: the reason must be one of false positive, "
            "won't fix, used in tests",
        ),
        (
            '[[accept]]\ntool = "A"\nrule = "B"\nreason = "won\'t fix"\ncomment = " "\n',
            ".github/findings.toml: A B: explain it in a comment of 1 to 200 characters",
        ),
        # A typo of path would accept the findings of the rule in every file.
        (
            '[[accept]]\ntool = "A"\nrule = "B"\nreason = "won\'t fix"\ncomment = "y"\n'
            'paths = "tests/"\n',
            ".github/findings.toml: A B: unknown key paths; did you mean path?",
        ),
        (
            '[[accepted]]\ntool = "A"\nrule = "B"\n',
            ".github/findings.toml: unknown key accepted; did you mean accept?",
        ),
        (
            'accept = "all"\n',
            "every [[accept]] needs tool, rule, reason and comment (TypeError",
        ),
    ],
)
def test_the_check_finds_broken_acceptances(config, root, text, problem):
    write_findings(root, text)
    problems = assistant.check_accepted_findings(config)
    assert len(problems) == 1 and problem in problems[0]


def test_a_path_covers_its_file_or_folder_and_no_name_that_starts_alike():
    accepted = assistant.Acceptance(
        "CodeQL", "py/x", "used in tests", "Tests.", "tests"
    )
    for path, covered in (
        ("tests", True),
        ("tests/test_a.py", True),
        ("testsuite/test_a.py", False),
        ("tests.py", False),
    ):
        assert accepted.covers(make_alert(1, "CodeQL", "py/x", path)) is covered, path
    folder = dataclasses.replace(accepted, path="tests/")
    assert folder.covers(make_alert(1, "CodeQL", "py/x", "tests/test_a.py"))
    assert not folder.covers(make_alert(1, "CodeQL", "py/x", "testsuite/test_a.py"))
    everywhere = dataclasses.replace(accepted, path="")
    assert everywhere.covers(make_alert(1, "CodeQL", "py/x", "src/app.py"))


def test_a_typo_in_the_accepted_findings_dismisses_nothing(
    monkeypatch, tmp_path, root, config, capsys
):
    write_findings(root, ACCEPTED.replace('path = "tests/"', 'paths = "tests/"'))
    github = FakeGitHub(config)
    github.alert_store = [make_alert(5, "CodeQL", "py/unused-import", "src/app.py")]
    code, _, _ = run_main(monkeypatch, tmp_path, root, github, "findings")
    assert code == 1 and github.writes_made() == []
    assert capsys.readouterr().out == (
        "::error::.github/findings.toml: codeql py/unused-import: unknown key paths; "
        "did you mean path?\n"
    )


def test_the_starter_and_accepted_findings_pass_the_check(config, root):
    assert assistant.check_accepted_findings(config) == []
    write_findings(root)
    assert assistant.check_accepted_findings(config) == []


def link(number):
    return f"https://github.com/{REPOSITORY}/security/code-scanning/{number}"


ANNOTATIONS = ("::error::", "::warning::")


def findings(monkeypatch, tmp_path, root, github, capsys, **env):
    """A run of the findings command: its exit code, its summary, and the
    annotations of its log."""
    (tmp_path / "summary").unlink(missing_ok=True)
    capsys.readouterr()
    code, _, summary = run_main(monkeypatch, tmp_path, root, github, "findings", **env)
    log = capsys.readouterr().out.splitlines()
    return code, summary, [line for line in log if line.startswith(ANNOTATIONS)]


def record(root):
    return json.loads((root / assistant.FINDINGS_RECORD).read_text("utf-8"))


STILL_OPEN = (
    "An open alert fails only the first run that finds it, and again once it opens "
    "again; until it is fixed or accepted, the runs after it name it here."
)
FIX = "Fix these findings, or accept them with a reason in .github/findings.toml."


def test_an_open_alert_fails_the_first_run_that_finds_it_alone(
    monkeypatch, tmp_path, root, config, capsys
):
    """The workflow runs after every scan and every morning, and GitHub mails the
    maintainer about every failed run: an alert that stays open fails one."""
    write_findings(root)
    github = FakeGitHub(config)
    github.alert_store = [make_alert(3), make_alert(4, "CodeQL", "js/xss", "x.js")]
    code, summary, annotations = findings(monkeypatch, tmp_path, root, github, capsys)
    assert code == 1
    assert summary == (
        "### Findings\n\n"
        "- Alert #3 (Scorecard CodeReviewID) dismissed as won't fix.\n"
        f"- Alert #4 is open: {link(4)}\n"
        f"- {FIX}\n"
    )
    assert annotations == [f"::error::Alert #4 is open: {link(4)}"]
    # Only the alerts that stay open, with the time each opened.
    assert record(root) == {"4": OPENED}
    for _ in range(2):
        code, summary, annotations = findings(
            monkeypatch, tmp_path, root, github, capsys
        )
        assert code == 0
        assert summary == (
            "### Findings\n\n"
            "- Alert #3 (Scorecard CodeReviewID) dismissed as won't fix.\n"
            f"- Alert #4 is still open: {link(4)}\n"
            f"- {STILL_OPEN}\n"
            f"- {FIX}\n"
        )
        assert annotations == [f"::warning::Alert #4 is still open: {link(4)}"]
        assert record(root) == {"4": OPENED}
    github.alert_store = [alert for alert in github.alert_store if alert["number"] == 3]
    code, summary, annotations = findings(monkeypatch, tmp_path, root, github, capsys)
    assert code == 0 and annotations == []
    assert "open" not in summary


def test_an_alert_that_opens_again_fails_a_run_again(
    monkeypatch, tmp_path, root, config, capsys
):
    """A fixed alert that a later scan finds again keeps its number, and GitHub
    gives it the time it opened again; a new alert fails the run beside one that
    an earlier run reported, which stays a warning."""
    github = FakeGitHub(config)
    github.alert_store = [make_alert(4), make_alert(5)]
    assert findings(monkeypatch, tmp_path, root, github, capsys)[0] == 1
    later = "2026-11-09T07:17:00Z"
    github.alert_store = [make_alert(4, opened=later), make_alert(5), make_alert(6)]
    code, summary, annotations = findings(monkeypatch, tmp_path, root, github, capsys)
    assert code == 1
    assert annotations == [
        f"::error::Alert #4 is open: {link(4)}",
        f"::error::Alert #6 is open: {link(6)}",
        f"::warning::Alert #5 is still open: {link(5)}",
    ]
    assert summary.endswith(f"- {STILL_OPEN}\n- {FIX}\n")
    assert record(root) == {"4": later, "5": OPENED, "6": OPENED}
    # A fixed alert leaves the record with the next one that opens.
    github.alert_store = [make_alert(5), make_alert(7)]
    assert findings(monkeypatch, tmp_path, root, github, capsys)[0] == 1
    assert record(root) == {"5": OPENED, "7": OPENED}


def test_a_run_without_new_alerts_leaves_the_record_as_it_is(
    monkeypatch, tmp_path, root, config, capsys
):
    """The workflow keeps the record in the cache only when it changed: a run that
    finds nothing new writes nothing, also with no alert open at all."""
    github = FakeGitHub(config)
    assert findings(monkeypatch, tmp_path, root, github, capsys)[0] == 0
    path = root / assistant.FINDINGS_RECORD
    assert not path.exists()
    github.alert_store = [make_alert(5), make_alert(4)]
    findings(monkeypatch, tmp_path, root, github, capsys)
    before = path.read_bytes()
    assert before == (
        b'{\n  "4": "2026-11-08T04:08:48Z",\n  "5": "2026-11-08T04:08:48Z"\n}\n'
    )
    github.alert_store = [make_alert(5)]
    assert findings(monkeypatch, tmp_path, root, github, capsys)[0] == 0
    github.alert_store = []
    assert findings(monkeypatch, tmp_path, root, github, capsys)[0] == 0
    assert path.read_bytes() == before


def test_a_dry_run_keeps_no_record(monkeypatch, tmp_path, root, config, capsys):
    """The dry runs of the Workflow test find the alerts of the default branch as the
    Findings workflow does there, and leave its record alone."""
    github = FakeGitHub(config, dry_run=True)
    github.alert_store = [make_alert(4)]
    for _ in range(2):
        code, summary, _ = findings(
            monkeypatch, tmp_path, root, github, capsys, DRY_RUN="true"
        )
        assert code == 1 and summary.startswith("### Findings (dry run)")
    assert not (root / assistant.FINDINGS_RECORD).exists()


def test_a_dry_run_follows_the_record_of_the_real_runs(
    monkeypatch, tmp_path, root, config, capsys
):
    github = FakeGitHub(config)
    github.alert_store = [make_alert(4)]
    findings(monkeypatch, tmp_path, root, github, capsys)
    dry = FakeGitHub(config, dry_run=True)
    dry.alert_store = [make_alert(4), make_alert(5)]
    code, _, annotations = findings(
        monkeypatch, tmp_path, root, dry, capsys, DRY_RUN="true"
    )
    assert code == 1
    assert annotations == [
        f"::error::Alert #5 is open: {link(5)}",
        f"::warning::Alert #4 is still open: {link(4)}",
    ]
    assert record(root) == {"4": OPENED}


# The keys under which the workflow keeps two records in the cache.
OLDER_KEY = "issue-assistant-findings-" + "1" * 64
NEWER_KEY = "issue-assistant-findings-" + "2" * 64
NEWER = (
    "A run that ended while this one waited for it kept a newer record of the "
    "reported alerts than the one that this run read, so this run fails for none of "
    "them and keeps no record: the next run fails for each open alert that the newer "
    "record lacks."
)


def test_of_two_overlapping_runs_only_the_first_fails_for_a_new_alert(
    monkeypatch, tmp_path, root, config, capsys
):
    """The runs after the scans of a push overlap: each reads the record as it
    starts, and waits for the run ahead before its findings. A run that read its
    record before the run ahead kept a newer one fails for none of the alerts that
    its record lacks, and leaves them to the newer record (decision 0026)."""
    github = FakeGitHub(config)
    github.alert_store = [make_alert(4)]
    findings(monkeypatch, tmp_path, root, github, capsys)
    path = root / assistant.FINDINGS_RECORD
    read = path.read_bytes()
    # The scans open alert 5; the first run fails for it and keeps a newer record,
    # while the second one waits with the record from before.
    github.alert_store = [make_alert(4), make_alert(5)]
    code, _, annotations = findings(
        monkeypatch, tmp_path, root, github, capsys, RECORD_KEY=OLDER_KEY
    )
    assert code == 1
    assert annotations == [
        f"::error::Alert #5 is open: {link(5)}",
        f"::warning::Alert #4 is still open: {link(4)}",
    ]
    path.write_bytes(read)
    code, summary, annotations = findings(
        monkeypatch,
        tmp_path,
        root,
        github,
        capsys,
        RECORD_KEY=OLDER_KEY,
        NEWEST_RECORD_KEY=NEWER_KEY,
    )
    assert code == 0
    left = f"Alert #5 is open, and the newer record may hold it: {link(5)}"
    assert annotations == [
        f"::warning::{NEWER}",
        f"::warning::{left}",
        f"::warning::Alert #4 is still open: {link(4)}",
    ]
    assert summary == (
        f"### Findings\n\n- {NEWER}\n- {left}\n"
        f"- Alert #4 is still open: {link(4)}\n- {STILL_OPEN}\n- {FIX}\n"
    )
    # It keeps no record, which would take the place of the newer one.
    assert path.read_bytes() == read
    # The accepted findings are dismissed all the same.
    write_findings(root)
    github.alert_store = [make_alert(3), make_alert(5)]
    code, summary, _ = findings(
        monkeypatch,
        tmp_path,
        root,
        github,
        capsys,
        RECORD_KEY=OLDER_KEY,
        NEWEST_RECORD_KEY=NEWER_KEY,
    )
    assert code == 0
    assert "- Alert #3 (Scorecard CodeReviewID) dismissed as won't fix.\n" in summary
    assert path.read_bytes() == read


@pytest.mark.parametrize(
    ("read", "newest"),
    [
        # The run ahead kept no newer record, or GitHub's cache didn't answer.
        (OLDER_KEY, OLDER_KEY),
        (OLDER_KEY, ""),
        # The job record read none: the cache may have failed there, and a cache that
        # keeps failing in that job must not keep every run from failing.
        ("", NEWER_KEY),
        ("", ""),
    ],
)
def test_a_run_without_a_newer_record_fails_for_a_new_alert(
    monkeypatch, tmp_path, root, config, capsys, read, newest
):
    github = FakeGitHub(config)
    github.alert_store = [make_alert(4)]
    code, summary, annotations = findings(
        monkeypatch,
        tmp_path,
        root,
        github,
        capsys,
        RECORD_KEY=read,
        NEWEST_RECORD_KEY=newest,
    )
    assert code == 1
    assert annotations == [f"::error::Alert #4 is open: {link(4)}"]
    assert NEWER not in summary
    assert record(root) == {"4": OPENED}


def test_a_dry_run_with_a_newer_record_fails_for_no_alert(
    monkeypatch, tmp_path, root, config, capsys
):
    github = FakeGitHub(config, dry_run=True)
    github.alert_store = [make_alert(4)]
    code, summary, _ = findings(
        monkeypatch,
        tmp_path,
        root,
        github,
        capsys,
        DRY_RUN="true",
        RECORD_KEY=OLDER_KEY,
        NEWEST_RECORD_KEY=NEWER_KEY,
    )
    assert code == 0 and summary.startswith(f"### Findings (dry run)\n\n- {NEWER}")
    assert not (root / assistant.FINDINGS_RECORD).exists()


@pytest.mark.parametrize(
    "text",
    [
        b"not JSON",
        b"\xff\xfe",
        b"[4]",
        b'{"four": "2026-11-08T04:08:48Z"}',
        b'{"-4": "2026-11-08T04:08:48Z"}',
        b'{"4": 1762575000}',
    ],
)
def test_a_broken_record_counts_every_open_alert_as_new(
    monkeypatch, tmp_path, root, config, capsys, text
):
    """The record comes from the cache, which other workflows of the default branch
    can write too: one that isn't a record reports every open alert again, and
    never hides one."""
    path = root / assistant.FINDINGS_RECORD
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text)
    github = FakeGitHub(config)
    github.alert_store = [make_alert(4)]
    code, summary, annotations = findings(monkeypatch, tmp_path, root, github, capsys)
    problem = (
        ".issue-assistant/findings.json holds no record of the alerts that earlier "
        "runs reported, so every open alert counts as new."
    )
    assert code == 1
    assert annotations == [
        f"::warning::{problem}",
        f"::error::Alert #4 is open: {link(4)}",
    ]
    assert summary.startswith(f"### Findings\n\n- {problem}\n- Alert #4 is open")
    assert record(root) == {"4": OPENED}


def test_the_findings_command_needs_no_issue_settings(monkeypatch, tmp_path, config):
    github = FakeGitHub(config)
    code, _, summary = run_main(monkeypatch, tmp_path, tmp_path, github, "findings")
    assert code == 0 and "### Findings\n\n- No open findings." in summary


# The scans of a pull request: its accepted alerts are dismissed, and none fails.

PULL_REQUEST = 12
OPEN_IN_PULL_REQUEST = (
    "The check of code scanning shows these alerts in the pull request: fix them "
    "there, or accept them with a reason in .github/findings.toml of the default "
    "branch, and the next scan of the pull request dismisses them."
)


REPOSITORY_ID = 41


def scan(
    head=REPOSITORY, branch="feature", event="pull_request", numbers=(), base=None
):
    """The event of the Findings workflow after a scan: workflow_run, whose
    pull_requests GitHub fills by the head branch, each with the repository that it
    goes into (`base`, by its id: this one unless given)."""
    into = {"repo": {"id": REPOSITORY_ID if base is None else base}}
    return {
        "workflow_run": {
            "event": event,
            "head_branch": branch,
            "head_repository": {"full_name": head},
            "pull_requests": [{"number": number, "base": into} for number in numbers],
        },
        "repository": {
            "id": REPOSITORY_ID,
            "full_name": REPOSITORY,
            "default_branch": "main",
        },
    }


@pytest.mark.parametrize(
    ("event", "payload", "numbers"),
    [
        # The runs of the default branch: every morning, by hand, after a push.
        ("schedule", {}, None),
        ("workflow_dispatch", {"inputs": {"dry_run": True}}, None),
        ("push", {"repository": {"default_branch": "main"}}, None),
        ("workflow_run", scan(branch="main", event="push"), None),
        ("workflow_run", scan(branch="main", event="schedule"), None),
        # The scan of a pull request of the repository itself, by its number.
        ("workflow_run", scan(numbers=[PULL_REQUEST]), [PULL_REQUEST]),
        ("workflow_run", scan(numbers=[14, 12, 14]), [12, 14]),
        # Nothing to do after the scan of a fork's pull request, whose head GitHub
        # names with the fork, not even when its branch has the default's name, or
        # after the scan of another branch without a pull request.
        ("workflow_run", scan(head="fork/project", numbers=[PULL_REQUEST]), []),
        ("workflow_run", scan(head="fork/project", branch="main", event="push"), []),
        ("workflow_run", scan(event="push"), []),
        ("workflow_run", scan(numbers=[]), []),
        # A pull request of another repository of the fork network, whose head is
        # this repository's branch, isn't one of this repository's.
        ("workflow_run", scan(numbers=[PULL_REQUEST], base=99), []),
        ("workflow_run", {"workflow_run": {}, "repository": {}}, []),
    ],
)
def test_the_run_knows_the_pull_requests_whose_scan_started_it(event, payload, numbers):
    assert assistant.scanned_pull_requests(event, payload) == numbers


def pull_request_findings(monkeypatch, tmp_path, root, github, capsys, payload, **env):
    """A run of the findings command after the scan of `payload`."""
    path = tmp_path / "event.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return findings(
        monkeypatch,
        tmp_path,
        root,
        github,
        capsys,
        GITHUB_EVENT_NAME="workflow_run",
        GITHUB_EVENT_PATH=str(path),
        **env,
    )


def test_the_scan_of_a_pull_request_dismisses_its_accepted_alerts_and_fails_none(
    monkeypatch, tmp_path, root, config, capsys
):
    """A pull request that moves an accepted finding to another line makes a new
    alert, which GitHub posts as a review thread that may block the merge until it
    is resolved; dismissing the alert resolves it (Dennis-Otto/issue-assistant#319).
    The other alerts of the pull request show in its own check of code scanning, so
    they fail no run, and the alerts of the default branch wait for its runs."""
    write_findings(root)
    github = FakeGitHub(config)
    github.alert_store = [
        make_alert(3),
        make_alert(5, "CodeQL", "py/unused-import", "tests/test_x.py", pull_request=12),
        make_alert(7, "CodeQL", "py/sql-injection", "app/db.py", pull_request=12),
        make_alert(8, "CodeQL", "py/unused-import", "tests/test_y.py", pull_request=9),
    ]
    payload = scan(numbers=[PULL_REQUEST])
    code, summary, annotations = pull_request_findings(
        monkeypatch, tmp_path, root, github, capsys, payload
    )
    assert code == 0 and annotations == []
    assert summary == (
        f"### Findings of pull request #{PULL_REQUEST}\n\n"
        "- Alert #5 (codeql py/unused-import) dismissed as used in tests.\n"
        f"- Alert #7 is open in pull request #{PULL_REQUEST}: {link(7)}\n"
        f"- {OPEN_IN_PULL_REQUEST}\n"
    )
    states = {alert["number"]: alert["state"] for alert in github.alert_store}
    assert states == {3: "open", 5: "dismissed", 7: "open", 8: "open"}
    assert github.alert_store[1]["dismissed_comment"] == (
        "Fixtures import what they provide. (accepted in .github/findings.toml)"
    )
    # Nothing about the rule or the file of an open alert reaches the public run,
    # and the record of the default branch stays as it is.
    assert "sql" not in summary and "app/" not in summary
    assert not (root / assistant.FINDINGS_RECORD).exists()


def test_the_scan_of_several_pull_requests_looks_after_each(
    monkeypatch, tmp_path, root, config, capsys
):
    """GitHub names every open pull request of the scanned branch."""
    write_findings(root)
    github = FakeGitHub(config, dry_run=True)
    github.alert_store = [
        make_alert(4, pull_request=12),
        make_alert(6, pull_request=14),
    ]
    payload = scan(numbers=[14, 12])
    code, summary, _ = pull_request_findings(
        monkeypatch, tmp_path, root, github, capsys, payload, DRY_RUN="true"
    )
    assert code == 0
    assert summary.startswith(
        "### Findings of pull request #12, #14 (dry run)\n\n"
        "- Alert #4 (Scorecard CodeReviewID) dismissed as won't fix.\n"
        "- Alert #6 (Scorecard CodeReviewID) dismissed as won't fix.\n"
    )
    # A dry run only says what it would dismiss.
    assert github.writes == [
        "dismiss alert #4 as won't fix",
        "dismiss alert #6 as won't fix",
    ]
    assert all(alert["state"] == "open" for alert in github.alert_store)


@pytest.mark.parametrize(
    ("store", "line"),
    [
        ([], "No open findings."),
        (None, "Code scanning isn't set up; there is nothing to check."),
    ],
)
def test_a_pull_request_without_alerts_or_code_scanning(
    monkeypatch, tmp_path, root, config, capsys, store, line
):
    github = FakeGitHub(config)
    github.alert_store = store
    payload = scan(numbers=[PULL_REQUEST])
    code, summary, annotations = pull_request_findings(
        monkeypatch, tmp_path, root, github, capsys, payload
    )
    assert (code, annotations) == (0, [])
    assert summary == f"### Findings of pull request #{PULL_REQUEST}\n\n- {line}\n"


def test_the_scan_of_a_fork_s_pull_request_asks_github_nothing(
    monkeypatch, tmp_path, root, config, capsys
):
    """An outsider's code could match an accepted rule in an accepted file with a
    real finding: its thread waits for a person."""
    write_findings(root)
    github = FakeGitHub(config)
    github.alert_error = "a request that the run must not make"
    payload = scan(head="fork/project", numbers=[PULL_REQUEST])
    code, summary, annotations = pull_request_findings(
        monkeypatch, tmp_path, root, github, capsys, payload
    )
    assert (code, annotations) == (0, [])
    assert summary == (
        "### Findings\n\n"
        "- GitHub names no pull request of this repository for the scan: nothing to "
        "do.\n"
    )


def test_a_pull_request_without_an_analysis_keeps_none_of_the_others_from_their_turn(
    root, config
):
    """A pull request of the scanned branch that the scans don't cover has no
    analysis; the accepted alerts of the next one are still dismissed."""
    write_findings(root)
    github = FakeGitHub(config)
    github.alert_store = [make_alert(6, pull_request=14)]
    listing = github.open_alerts

    def open_alerts(pull_request=0):
        return None if pull_request == 12 else listing(pull_request=pull_request)

    github.open_alerts = open_alerts
    lines = assistant.accept_in_pull_requests(github, root, [12, 14])
    assert lines == ["Alert #6 (Scorecard CodeReviewID) dismissed as won't fix."]
    assert github.alert_store[0]["state"] == "dismissed"
