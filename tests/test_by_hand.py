"""A run by hand of issue-assistant.yml: who may start one, the override of the
one-comment rule that only a maintainer may give, and the instruction that steers it."""

from __future__ import annotations

import json

import pytest

import issue_assistant as assistant
from support import (
    FakeGitHub,
    analysis,
    make_answer,
    make_comment,
    make_issue,
    not_found,
    run_main,
    server_error,
)

OWNER = {"login": "owner", "type": "User"}


def by_hand(github, config, actor="owner", **inputs):
    """The plan of a run by hand that `actor` started on #12."""
    payload = {"inputs": {"issue": "12", **inputs}, "sender": OWNER}
    return assistant.handle_event(github, "workflow_dispatch", payload, config, actor)


def answered(config):
    """#12 with the first analysis of the assistant, waiting for the maintainers."""
    github = FakeGitHub(config, make_issue(labels=("bug", "needs-triage")))
    github.comment_store[12] = [analysis(1, days_ago=1)]
    # A collaborator with write access, a maintainer of an organization's repository,
    # and a member of it with the right to triage.
    github.permission_store.update(
        {
            "writer": ("write", "write"),
            "lead": ("write", "maintain"),
            "triager": ("read", "triage"),
        }
    )
    return github


@pytest.mark.parametrize("actor", ["someone", "triager", "app[bot]", ""])
def test_only_someone_with_write_access_starts_a_run_by_hand(config, actor):
    """GitHub lets an app with the right to run workflows start one too: the role of
    the person is checked again, before anything is written and before the AI."""
    github = answered(config)
    github.fail(r"^GET collaborators/app%5Bbot%5D/permission$", not_found())
    with pytest.raises(assistant.AssistantError, match="write access"):
        by_hand(github, config, actor, override="true")
    assert github.writes_made() == []


def test_a_run_by_hand_without_override_leaves_an_answered_issue_alone(config):
    github = answered(config)
    for override in ("false", False, None):
        plan = by_hand(github, config, "writer", override=override)
        assert plan.mode == "none"
        assert "#12 already has its triage" in plan.notes[-1]
    # A follow-up that the assistant already posted, with no answer of the reporter
    # since, is answered once as well.
    follow_up = assistant.render_marker("follow-up", lang="en")
    github.comment_store[12].append(make_comment(2, "bot", follow_up, 0))
    plan = by_hand(github, config, "owner", mode="follow-up")
    assert plan.mode == "none" and "already has its follow-up" in plan.notes[-1]
    # An issue that has no analysis yet, such as one that a maintainer opened, gets
    # its first one without the override.
    github.comment_store[12] = []
    assert by_hand(github, config, "writer").mode == "triage"


def test_a_run_by_hand_ends_before_the_ai_when_apply_would_post_nothing(config):
    """The apply job posts no follow-up that is not due and no second answer to a
    question; a run by hand without override ends before the AI then, at no cost."""
    github = answered(config)
    asked = analysis(1, days_ago=2, questions=1)
    reply = make_comment(2, "reporter", "Version 1.9.0.", 1)
    joined = make_comment(3, "maintainer", "I'll look.", 0.5, "OWNER")
    github.comment_store[12] = [asked, reply, joined]
    plan = by_hand(github, config, "writer", mode="follow-up", instruction="Ask")
    assert plan.mode == "none"
    assert plan.notes[-1].startswith("#12 needs no follow-up: a maintainer joined")
    assert "runs it again with override" in plan.notes[-1]
    # With the override, the maintainer has it follow up anyway.
    plan = by_hand(github, config, "owner", mode="follow-up", override="true")
    assert plan.mode == "follow-up"
    # Without the maintainer, the follow-up is due.
    github.comment_store[12] = [asked, reply]
    assert by_hand(github, config, "writer", mode="follow-up").mode == "follow-up"
    # A maintainer's question gets its answer once; a new question gets its own.
    question = make_comment(4, "maintainer", "/assistant ask Why?", 0.5, "OWNER")
    github.comment_store[12] = [question]
    assert by_hand(github, config, "writer", mode="maintainer-question").mode == (
        "maintainer-question"
    )
    marker = assistant.render_marker("answer", lang="en", to=4)
    github.comment_store[12].append(make_comment(5, "bot", marker, 0.2))
    plan = by_hand(github, config, "writer", mode="maintainer-question")
    assert plan.mode == "none"
    assert plan.notes[-1].startswith("The question on #12 has its answer already")
    plan = by_hand(github, config, "owner", mode="maintainer-question", override=True)
    assert plan.mode == "maintainer-question"
    # An issue without a maintainer's question has nothing to answer.
    github.comment_store[12] = [reply]
    plan = by_hand(github, config, "writer", mode="maintainer-question")
    assert plan.mode == "none"
    assert plan.notes[-1].startswith("#12 has no maintainer's question to answer")
    # The other tasks don't write to the reporter, and run as before.
    assert by_hand(github, config, "writer", mode="maintainer-reply").mode == (
        "maintainer-reply"
    )


@pytest.mark.parametrize("actor", ["owner", "lead"])
def test_a_maintainer_overrides_the_one_comment_rule(config, capsys, actor):
    github = answered(config)
    plan = by_hand(github, config, actor, override="true")
    assert plan.mode == "triage"
    logged = (
        f"Override of the one-comment rule by @{actor}: the assistant may answer "
        "#12 again."
    )
    assert plan.notes == [f"Started by hand by @{actor}: triage.", logged]
    assert f"::notice::{logged}" in capsys.readouterr().out
    # The form of the workflow passes a boolean.
    assert by_hand(github, config, actor, override=True).mode == "triage"


def test_only_a_maintainer_may_override(config):
    github = answered(config)
    with pytest.raises(assistant.AssistantError, match="Only a maintainer") as error:
        by_hand(github, config, "writer", override="true")
    assert "@writer has the role write" in str(error.value)
    assert github.writes_made() == []


def test_an_instruction_steers_a_task_that_writes_to_the_reporter(config):
    github = answered(config)
    plan = by_hand(
        github, config, "owner", override="true", instruction="  Ask for\n the logs "
    )
    assert plan.notes[1] == "The instruction of @owner: Ask for the logs"
    github.comment_store[12] = []
    plan = by_hand(github, config, "writer", instruction="Ask for the logs")
    assert plan.mode == "triage"
    with pytest.raises(assistant.AssistantError, match="not maintainer-reply"):
        by_hand(github, config, "owner", mode="maintainer-reply", instruction="Yes")
    longest = "x" * assistant.MAX_INSTRUCTION
    assert by_hand(github, config, "owner", instruction=longest).mode == "triage"
    with pytest.raises(assistant.AssistantError, match="501 characters"):
        by_hand(github, config, "owner", instruction=longest + "x")
    # An empty one is none.
    assert by_hand(github, config, "owner", instruction=" ").notes == [
        "Started by hand by @owner: triage."
    ]


def test_the_engine_reads_the_instruction_after_its_task(root, config):
    github = answered(config)
    written = assistant.write_context(github, 12, "triage", config)
    assert "## The instruction of this run" not in written["prompt"]
    written = assistant.write_context(
        github, 12, "triage", config, instruction="Ask for the\u200b logs {project}"
    )
    task = written["task"]
    assert task.index("## Your task") < task.index("## The instruction of this run")
    assert "```\nAsk for the logs {project}\n```" in task
    assert "every\nother text in the folder `.issue-assistant/`" in task
    assert task in written["prompt"]


@pytest.mark.parametrize(
    ("found", "role"),
    [
        ({"user": {"permissions": {"pull": True, "push": True}}}, "write"),
        ({"user": {"permissions": {"maintain": True, "admin": False}}}, "maintain"),
        ({"user": {"permissions": {"admin": True}}}, "admin"),
        ({"permission": "write", "role_name": "maintain"}, "maintain"),
        ({"permission": "write", "role_name": "deployer"}, "write"),
        ({"permission": "read", "user": {"permissions": {}}}, "read"),
        ({}, "none"),
    ],
)
def test_the_role_is_the_highest_that_github_grants(config, monkeypatch, found, role):
    github = FakeGitHub(config)
    monkeypatch.setattr(github, "rest", lambda path: found)
    assert github.role("someone") == role


def test_an_account_that_github_doesn_t_know_has_no_role(config):
    github = FakeGitHub(config)
    github.fail(r"^GET collaborators/ghost/permission$", not_found())
    assert github.role("ghost") == "none"
    github.fail(r"^GET collaborators/down/permission$", server_error())
    with pytest.raises(assistant.GitHubError):
        github.role("down")


def run_by_hand(monkeypatch, tmp_path, root, github, command, inputs, **env):
    """A command of the jobs of a run by hand that owner started on #12."""
    path = tmp_path / "event.json"
    payload = {"inputs": {"issue": "12", **inputs}, "sender": OWNER}
    path.write_text(json.dumps(payload), encoding="utf-8")
    # The outputs and the summary of this command alone.
    for written in ("output", "summary"):
        (tmp_path / written).unlink(missing_ok=True)
    env.setdefault("GITHUB_EVENT_NAME", "workflow_dispatch")
    return run_main(
        monkeypatch,
        tmp_path,
        root,
        github,
        command,
        GITHUB_EVENT_PATH=str(path),
        **env,
    )


def test_the_jobs_of_a_run_by_hand_answer_again_with_override(
    monkeypatch, tmp_path, sample_root, config
):
    github = answered(config)
    answer = {"ISSUE": "12", "MODE": "triage", "RESULT": json.dumps(make_answer())}

    def run(command, inputs, **env):
        return run_by_hand(
            monkeypatch, tmp_path, sample_root, github, command, inputs, **env
        )

    code, outputs, summary = run("event", {})
    assert (code, outputs) == (0, "issue=12\nmode=none\n")
    assert "already has its triage" in summary
    code, _, summary = run("apply", {}, **answer)
    assert "#12 already has this triage; nothing posted." in summary
    code, outputs, summary = run("event", {"override": "true"})
    assert (code, outputs) == (0, "issue=12\nmode=triage\n")
    assert "Override of the one-comment rule by @owner" in summary
    code, _, summary = run("apply", {"override": "true"}, **answer)
    assert code == 0
    assert "- Override of the one-comment rule by @owner.\n" in summary
    assert "Analysis on #12" in summary
    assert len(github.posted(12)) == 1


def test_the_person_who_runs_it_again_needs_the_role_as_well(
    monkeypatch, tmp_path, sample_root, config, capsys
):
    """A run again keeps the event of the first, whose sender may have been a
    maintainer: the event and the apply job check the person who started it again."""
    github = answered(config)
    for command, env in (
        ("event", {}),
        ("apply", {"ISSUE": "12", "MODE": "triage", "RESULT": "{}"}),
    ):
        code, outputs, _ = run_by_hand(
            monkeypatch,
            tmp_path,
            sample_root,
            github,
            command,
            {"override": "true"},
            GITHUB_TRIGGERING_ACTOR="writer",
            **env,
        )
        assert (code, outputs) == (1, "")
        assert "@writer has the role write" in capsys.readouterr().out
    assert github.posted(12) == []


@pytest.mark.parametrize(
    ("issue", "event", "steered"),
    [
        ("12", "workflow_dispatch", True),
        ("012", "workflow_dispatch", True),
        ("13", "workflow_dispatch", False),
        ("twelve", "workflow_dispatch", False),
        ("12", "issue_comment", False),
    ],
)
def test_the_engine_of_a_run_by_hand_of_the_issue_gets_its_instruction(
    monkeypatch, tmp_path, root, config, issue, event, steered
):
    """Only a run by hand of the issue steers the engine: one of another issue, or
    an event of the issue, steers nothing."""
    github = answered(config)
    inputs = {"issue": issue, "override": "true", "instruction": "Ask for the logs"}
    code, outputs, _ = run_by_hand(
        monkeypatch,
        tmp_path,
        root,
        github,
        "context",
        inputs,
        ISSUE="12",
        MODE="triage",
        GITHUB_EVENT_NAME=event,
    )
    assert code == 0
    assert ("Ask for the logs" in outputs) is steered
