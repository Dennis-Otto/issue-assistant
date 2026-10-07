"""Findings of code scanning: accepted ones are dismissed, others fail the run."""

import pytest

import issue_assistant as assistant
from support import FakeGitHub, make_alert, run_main

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


def test_without_accepted_findings_or_code_scanning(config, root):
    github = FakeGitHub(config)
    assert assistant.watch_findings(github, root) == ([], 0)
    github.alert_store = None
    assert assistant.watch_findings(github, root) == (
        ["Code scanning isn't set up; there is nothing to check."],
        0,
    )


def test_other_errors_of_code_scanning_are_raised(config, root):
    github = FakeGitHub(config)
    github.alert_error = "gh api: Resource not accessible by integration (HTTP 403)"
    with pytest.raises(assistant.GitHubError):
        assistant.watch_findings(github, root)


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
        ("[[accept]\n", "every [[accept]] needs tool, rule, reason and comment"),
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
    ],
)
def test_the_check_finds_broken_acceptances(config, root, text, problem):
    write_findings(root, text)
    problems = assistant.check_accepted_findings(config)
    assert len(problems) == 1 and problem in problems[0]


def test_the_starter_and_accepted_findings_pass_the_check(config, root):
    assert assistant.check_accepted_findings(config) == []
    write_findings(root)
    assert assistant.check_accepted_findings(config) == []


def test_the_findings_command_fails_while_findings_are_open(
    monkeypatch, tmp_path, root, config
):
    write_findings(root)
    github = FakeGitHub(config)
    github.alert_store = [make_alert(3), make_alert(4, "CodeQL", "js/xss", "x.js")]
    code, _, summary = run_main(monkeypatch, tmp_path, root, github, "findings")
    assert code == 1
    assert "### Findings\n\n- Alert #3 (Scorecard CodeReviewID) dismissed" in summary
    assert "Alert #4 is open" in summary
    code, _, summary = run_main(monkeypatch, tmp_path, root, github, "findings")
    assert code == 1
    github.alert_store = [alert for alert in github.alert_store if alert["number"] == 3]
    code, _, summary = run_main(monkeypatch, tmp_path, root, github, "findings")
    assert code == 0


def test_the_findings_command_needs_no_issue_settings(monkeypatch, tmp_path, config):
    github = FakeGitHub(config)
    code, _, summary = run_main(monkeypatch, tmp_path, tmp_path, github, "findings")
    assert code == 0 and "### Findings\n\n- No open findings." in summary
