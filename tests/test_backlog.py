"""The task backlog of a run by hand: the open issues that were there before the
assistant came, which a maintainer has analyzed a few at a time, labels only unless
they ask for the comments too."""

from __future__ import annotations

import dataclasses
import json

import pytest

import issue_assistant as assistant
from support import (
    FakeGitHub,
    analysis,
    make_answer,
    make_comment,
    make_issue,
    run_main,
)

OWNER = {"login": "owner", "type": "User"}
BOT = {"login": "renovate[bot]", "type": "Bot"}


def backlog(github, config, actor="owner", number=0, **inputs):
    """The plan of a run by hand with the task backlog that `actor` started; with
    `number`, of the call for that issue of the backlog."""
    payload = {"inputs": {"issue": "0", "mode": "backlog", **inputs}, "sender": OWNER}
    return assistant.handle_event(
        github, "workflow_dispatch", payload, config, actor, number
    )


def repository(config):
    """Open issues that the backlog takes, and others that it leaves out."""
    github = FakeGitHub(
        config,
        # With the first analysis of the assistant, and with comments of others.
        make_issue(1, comments=1),
        make_issue(2, association="OWNER"),
        make_issue(3, labels=()),
        make_issue(4, pull_request={"url": "..."}),
        make_issue(5, labels=("bug", assistant.MUTED)),
        make_issue(6, state="closed"),
        make_issue(7, comments=1),
        make_issue(8, user=BOT),
        make_issue(9, locked=True),
        make_issue(10),
        make_issue(11),
    )
    github.comment_store[1] = [analysis(1, days_ago=3)]
    github.comment_store[7] = [make_comment(2, "someone", "Me too", 2)]
    return github


def test_a_run_on_the_backlog_chooses_the_oldest_issues_without_an_analysis(config):
    github = repository(config)
    plan = backlog(github, dataclasses.replace(config, backlog_per_run=3))
    assert (plan.issue, plan.mode, plan.backlog) == (None, "none", [3, 7, 10])
    assert plan.notes == [
        "Started by hand by @owner: backlog after #0, at most 3.",
        "Chosen for a first analysis: #3, #7, #10.",
        "Left out, with a first analysis already: #1.",
        "Left out, opened by a maintainer: #2.",
        "Left out, muted, or its reporter keeps the AI out: #5.",
        "Left out, opened by a bot: #8.",
        "Left out, locked: #9.",
        "The next run of the backlog goes on after #10: give that number as the issue.",
    ]
    # Choosing writes nothing, and reads the comments only of an issue with some.
    assert github.writes_made() == []
    read = [call[1] for call in github.calls if "/comments" in call[1]]
    assert read == ["issues/1/comments?per_page=100", "issues/7/comments?per_page=100"]


def test_the_next_run_goes_on_after_the_issue_that_it_names(config):
    github = repository(config)
    plan = backlog(github, config, issue="7")
    assert plan.backlog == [10, 11]
    assert plan.notes[1:] == [
        "Chosen for a first analysis: #10, #11.",
        "Left out, opened by a bot: #8.",
        "Left out, locked: #9.",
        "That is the whole backlog: no other open issue after #7 waits for a first "
        "analysis.",
    ]
    plan = backlog(github, config, issue="11")
    assert plan.backlog == []
    assert plan.notes[1] == "Chosen for a first analysis: none."
    # None at all, with backlog_per_run = 0.
    plan = backlog(github, dataclasses.replace(config, backlog_per_run=0))
    assert plan.backlog == []
    assert plan.notes[1:] == [
        "Chosen for a first analysis: none.",
        "[engine] backlog_per_run is 0, so the backlog analyzes no issue.",
    ]


@pytest.mark.parametrize(
    ("actor", "role"), [("writer", "write"), ("", "none"), ("someone", "read")]
)
def test_only_a_maintainer_starts_a_run_on_the_backlog(config, actor, role):
    """It may start many runs of the AI, which no daily limit stops."""
    github = repository(config)
    github.permission_store["writer"] = ("write", "write")
    with pytest.raises(assistant.AssistantError, match=f"has the role {role}"):
        backlog(github, config, actor)
    with pytest.raises(assistant.AssistantError, match="Only a maintainer"):
        backlog(github, config, actor, number=3)
    github.permission_store["lead"] = ("write", "maintain")
    assert backlog(github, config, "lead").backlog == [3, 7, 10, 11]


@pytest.mark.parametrize(
    ("inputs", "says"),
    [
        ({"instruction": " ask  for logs "}, "takes no instruction and no override"),
        ({"override": "true"}, "takes no instruction and no override"),
        ({"issue": "-1"}, "a whole number, 0 or more, not '-1'"),
        ({"issue": "x"}, "not 'x'"),
    ],
)
def test_a_run_on_the_backlog_refuses_what_it_cant_do(config, inputs, says):
    with pytest.raises(assistant.AssistantError, match=says):
        backlog(repository(config), config, **inputs)


@pytest.mark.parametrize(
    ("value", "says"),
    [
        (257, "backlog_per_run may be at most 256, the most jobs"),
        (-1, "backlog_per_run must be a whole number"),
    ],
)
def test_a_backlog_that_no_matrix_takes_stops_the_run(config, value, says):
    broken = dataclasses.replace(config, backlog_per_run=value)
    with pytest.raises(assistant.AssistantError, match=says):
        backlog(repository(config), broken)
    assert assistant.backlog_size(dataclasses.replace(config, backlog_per_run=256))


def test_the_call_for_an_issue_of_the_backlog_starts_its_first_analysis(config):
    github = repository(config)
    plan = backlog(github, config, number=3)
    assert (plan.issue, plan.mode) == (3, "triage")
    assert plan.notes == [
        "#3 of the backlog after #0.",
        "Started by hand by @owner: triage.",
    ]
    # As every run by hand, with the labels of its form.
    plan = backlog(github, config, number=10, issue="7")
    assert (plan.issue, plan.mode) == (10, "triage")


def test_the_call_leaves_out_an_issue_that_changed_since_it_was_chosen(config):
    github = repository(config)
    github.store[3]["state"] = "closed"
    github.store[10]["comments"] = 1
    github.comment_store[10] = [analysis(3, days_ago=0)]
    for number, reason in [(3, "closed"), (10, "with a first analysis already")]:
        plan = backlog(github, config, number=number)
        assert (plan.issue, plan.mode) == (number, "none")
        assert plan.notes == [f"The backlog leaves #{number} out now: {reason}."]
    assert github.writes_made() == []


@pytest.mark.parametrize(
    ("number", "inputs", "says"),
    [
        (3, {"issue": "3"}, "#3 is no issue of the backlog after #3"),
        (4, {}, "#4 is a pull request"),
        (3, {"mode": "triage"}, "the task triage, not backlog"),
        (3, {"mode": ""}, "the task triage, not backlog"),
    ],
)
def test_a_call_for_an_issue_of_the_backlog_is_one_of_its_run(
    config, number, inputs, says
):
    with pytest.raises(assistant.AssistantError, match=says):
        backlog(repository(config), config, number=number, **inputs)


def run_on_backlog(monkeypatch, tmp_path, root, github, command, inputs, **env):
    """A command of the jobs of a run on the backlog that owner started."""
    path = tmp_path / "event.json"
    payload = {"inputs": {"issue": "0", "mode": "backlog", **inputs}, "sender": OWNER}
    path.write_text(json.dumps(payload), encoding="utf-8")
    for written in ("output", "summary"):
        (tmp_path / written).unlink(missing_ok=True)
    return run_main(
        monkeypatch,
        tmp_path,
        root,
        github,
        command,
        GITHUB_EVENT_NAME="workflow_dispatch",
        GITHUB_EVENT_PATH=str(path),
        **env,
    )


def test_the_job_event_names_the_issues_of_the_backlog_for_the_matrix(
    monkeypatch, tmp_path, sample_root, config
):
    github = repository(config)
    code, outputs, summary = run_on_backlog(
        monkeypatch, tmp_path, sample_root, github, "event", {}
    )
    assert (code, outputs) == (0, "issue=\nmode=none\nbacklog=[3, 7, 10, 11]\n")
    assert "### Issue assistant: the backlog" in summary
    assert "Chosen for a first analysis: #3, #7, #10, #11." in summary
    # The call for one of them; none at all names no backlog, as GitHub runs no
    # matrix of none.
    code, outputs, _ = run_on_backlog(
        monkeypatch,
        tmp_path,
        sample_root,
        github,
        "event",
        {"issue": "11"},
        ISSUE_ASSISTANT_BACKLOG_ISSUE="",
    )
    assert (code, outputs) == (0, "issue=\nmode=none\n")
    code, outputs, summary = run_on_backlog(
        monkeypatch,
        tmp_path,
        sample_root,
        github,
        "event",
        {},
        ISSUE_ASSISTANT_BACKLOG_ISSUE="3",
    )
    assert (code, outputs) == (0, "issue=3\nmode=triage\n")
    assert "### Issue assistant: #3, next task triage" in summary
    code, _, summary = run_on_backlog(
        monkeypatch,
        tmp_path,
        sample_root,
        github,
        "event",
        {},
        ISSUE_ASSISTANT_BACKLOG_ISSUE="3; rm",
    )
    assert code == 1


def apply_on_backlog(monkeypatch, tmp_path, root, github, answer, **inputs):
    return run_on_backlog(
        monkeypatch,
        tmp_path,
        root,
        github,
        "apply",
        inputs,
        ISSUE="3",
        MODE="triage",
        RESULT=json.dumps(answer),
    )


ANSWER = make_answer(
    missing_information=["Which version | of the card?"],
    # An older issue that a maintainer opened.
    duplicates=[{"number": 2, "confidence": "high", "reason": "The same card."}],
)


def test_the_backlog_sets_the_labels_of_the_analysis_alone(
    monkeypatch, tmp_path, sample_root, config
):
    """No comment on an old issue unless the maintainer asks for it, and neither
    possible-duplicate nor needs-info, which only the comment would explain: the
    sweep would close the issue, or remind its reporter of a question that nobody
    asked. The summary shows what the comment would have said, as a table."""
    github = repository(config)
    code, _, summary = apply_on_backlog(
        monkeypatch, tmp_path, sample_root, github, ANSWER
    )
    assert code == 0
    assert github.posted(3) == []
    assert github.labels_of(3) == {"bug", "area: cards"}
    assert not [call for call in github.writes_made() if "assignees" in str(call)]
    assert (
        "Labels of the first analysis on #3, without a comment: bug, area: cards."
        in summary
    )
    assert (
        "| Issue | Labels | Possible duplicate | Missing information |\n"
        "| --- | --- | --- | --- |\n"
        "| #3 | bug, area: cards | #2 | Which version \\| of the card? |"
    ) in summary


def test_the_backlog_posts_the_analysis_when_the_maintainer_asks_for_it(
    monkeypatch, tmp_path, sample_root, config
):
    github = repository(config)
    code, _, summary = apply_on_backlog(
        monkeypatch, tmp_path, sample_root, github, ANSWER, comment="true"
    )
    assert code == 0
    (posted,) = github.posted(3)
    assert "### Possible duplicate" in posted
    labels = {"bug", "area: cards", assistant.NEEDS_INFO, assistant.POSSIBLE_DUPLICATE}
    assert github.labels_of(3) == labels
    assert (
        "| #3 | bug, area: cards, needs-info, possible-duplicate | #2 | "
        "Which version \\| of the card? |"
    ) in summary
    # Once: a run again leaves an issue with its analysis as it is.
    code, _, summary = apply_on_backlog(
        monkeypatch, tmp_path, sample_root, github, ANSWER, comment="true"
    )
    assert "#3 already has this triage; nothing posted." in summary
    assert "| #3 | none | none | none |" in summary


def test_spam_of_the_backlog_is_marked_invalid_without_a_comment(
    monkeypatch, tmp_path, sample_root, config
):
    github = repository(config)
    code, _, summary = apply_on_backlog(
        monkeypatch,
        tmp_path,
        sample_root,
        github,
        make_answer(kind="spam"),
        comment="true",
    )
    assert code == 0 and github.posted(3) == []
    assert assistant.INVALID in github.labels_of(3)
    assert f"| #3 | {assistant.INVALID} | none | none |" in summary
