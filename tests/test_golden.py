"""Golden files: what the assistant writes, word for word.

Every comment in both languages, the files of the context of each task, the plan
and the requests of each event, and the job summaries of the commands. A change
of a text or of its Markdown fails here until the golden file in tests/golden/
shows it; `UPDATE_GOLDEN=1 pytest tests/test_golden.py` writes the files anew,
and the diff of the pull request shows what readers see differently.
"""

import dataclasses
import importlib.util
import json
import re
from pathlib import Path

import pytest

import issue_assistant as assistant
from support import (
    BOT_USER,
    BUG_BODY,
    FakeGitHub,
    Frozen,
    analysis,
    assert_golden,
    at,
    discussion_comment,
    discussion_reply,
    event,
    files_of,
    golden_files,
    make_alert,
    make_answer,
    make_comment,
    make_discussion,
    make_issue,
    make_release,
    run_main,
    shown,
)

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "mkdocs_hooks", ROOT / "scripts" / "mkdocs_hooks.py"
)
assert _spec and _spec.loader
hooks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hooks)


# The comments: every kind, in every language, as the website shows them


def comment_files():
    return {
        f"comments/{example.kind}.{language}.md.txt": text
        for example in hooks.examples()
        for language, text in example.texts.items()
    }


@pytest.mark.parametrize("name", sorted(comment_files()))
def test_every_comment_reads_as_its_golden_file(name):
    assert_golden(name, comment_files()[name])


# The context of each task


# The context is written into config.root, which is then a copy of the sample.
@pytest.mark.usefixtures("root")
@pytest.mark.parametrize("mode", assistant.MODES)
def test_the_context_of_each_task_reads_as_its_golden_file(config, mode):
    issue = make_issue(
        body="### What happened?\n\nThe card <!-- hidden --> is empty.\n"
        "\n### Version\n\n1.9.0\n"
    )
    older = make_issue(
        7, body="### What happened?\n\nOld\n\n### Area\n\n_No response_\n"
    )
    older.update(state="closed", state_reason="completed", closed_at=at(5))
    github = FakeGitHub(config, issue, older, make_issue(8, pull_request={"url": "x"}))
    github.discussion_store = [
        {
            "number": 104,
            "title": "Testers",
            "category": {"name": "Announcements"},
            "isAnswered": None,
            "closed": False,
            "body": "Please test",
            "answer": None,
        }
    ]
    if mode in assistant.DISCUSSION_MODES:
        # The discussion #12, with a comment, its reply and a deleted account.
        github.discussion_store.append(
            make_discussion(
                12,
                body="The card <!-- hidden --> is empty. Which setting fills it?",
                comments=[
                    discussion_comment(
                        "maintainer",
                        "Which version?",
                        association="OWNER",
                        replies=[discussion_reply("asker", "1.9.0")],
                    ),
                    discussion_comment(None, "Same here"),
                ],
            )
        )
    github.comment_store[12] = [
        analysis(questions=1, days_ago=2),
        make_comment(2, "maintainer", "Which version?", 1.5, "OWNER"),
        make_comment(3, "reporter", "Still broken in 1.9.0", 1),
        make_comment(
            4,
            "maintainer",
            "/assistant ask Which file reads the version?",
            0.5,
            "OWNER",
        ),
    ]
    judged = {"maintainer-reply": 2, "release-reply": 3, "maintainer-question": 4}
    written = assistant.write_context(github, 12, mode, config, judged.get(mode))
    folder = config.root / assistant.CONTEXT
    # The rules, the engine's system prompt, and its task; a template from before
    # the system prompt gets both in one prompt.
    rules = (folder / "rules.md").read_text("utf-8")
    assert (folder / "prompt.md").read_text("utf-8") == written["task"]
    assert written["prompt"] == f"{rules}\n\n{written['task']}"
    assert (folder / "schema.json").read_text("utf-8") == written["schema"]
    assert_golden(f"context/{mode}.txt", files_of(folder))


@pytest.mark.usefixtures("root")
def test_the_context_with_related_repositories_reads_as_its_golden_file(config):
    """A first analysis in a repository with a related one: its task, its schema and
    the issues that a search there finds."""
    config = dataclasses.replace(config, related_repositories=("owner/server",))
    issue = make_issue(title="Live card stays empty", body="The card is empty.")
    found = make_issue(31, title="Empty card after a reconnect", body="Same here.")
    server = FakeGitHub(config, found, repository="owner/server")
    server.search_store[
        "repo:owner/server is:issue (live OR card OR stays OR empty)"
    ] = [found]
    github = FakeGitHub(config, issue)
    github.sibling_store["owner/server"] = server
    assistant.write_context(github, 12, "triage", config)
    assert_golden(
        "context/triage-related.txt", files_of(config.root / assistant.CONTEXT)
    )


# The events: what the event job plans, and what it asks of GitHub


def opened(config, author="reporter", association="NONE", **changes):
    issue = make_issue(labels=("bug",), author=author, association=association)
    issue.update(changes)
    return FakeGitHub(config, issue), "issues", event("opened", issue, author)


def edited(config, editor="reporter", **changes):
    github = FakeGitHub(config, make_issue(labels=("bug", "needs-info", "stale")))
    github.comment_store[12] = [analysis(questions=1)]
    found = github.edit(12, editor, **changes)
    payload = event("edited", github.issue(12), editor, changes=found)
    return github, "issues", payload


def commented(config, author, text, *, labels=("bug", "needs-info"), **options):
    """The comment of `author` on #12, after an analysis with a question."""
    association = options.pop("association", "NONE")
    github = FakeGitHub(config, make_issue(labels=labels, **options))
    comment = make_comment(5, author, text, 0, association)
    github.comment_store[12] = [analysis(questions=1, days_ago=2), comment]
    payload = event("created", github.issue(12), author, comment)
    return github, "issue_comment", payload


def closed_unanswered(config):
    """The reporter's answer on an issue that the sweep closed without one."""
    github, name, payload = commented(
        config,
        "reporter",
        "Sorry, here is the log",
        labels=("bug", "needs-info", "stale"),
        state="closed",
    )
    github.store[12]["state_reason"] = "not_planned"
    closing = assistant.render_closed_unanswered("reporter", "en")
    github.comment_store[12].insert(1, make_comment(2, "bot", closing, 0.5))
    github.event_store[12] = [
        {"event": "closed", "actor": BOT_USER, "created_at": at(0.5)}
    ]
    payload["issue"] = github.issue(12)
    return github, name, payload


def released(config):
    """The reporter's comment two days after the release that closed the issue."""
    github, name, payload = commented(
        config, "reporter", "Still broken in v1.10.0", labels=("bug",), state="closed"
    )
    notice = assistant.render_released("reporter", make_release(), [40], "en")
    github.comment_store[12].insert(1, make_comment(2, "bot", notice, 2))
    github.event_store[12] = [
        {"event": "closed", "actor": BOT_USER, "created_at": at(2)}
    ]
    payload["issue"] = github.issue(12)
    return github, name, payload


def by_hand(config):
    github = FakeGitHub(config, make_issue(labels=("bug",)))
    payload = {"inputs": {"issue": "12", "mode": "follow-up"}}
    return github, "workflow_dispatch", payload


EVENTS = {
    "opened": opened,
    "opened by a maintainer": lambda config: opened(config, "maintainer", "OWNER"),
    "opened by a bot": lambda config: opened(config, "renovate[bot]"),
    "pull request": lambda config: opened(config, pull_request={"url": "x"}),
    "edited by the reporter": lambda config: edited(
        config, body=f"{BUG_BODY}\nVersion 1.9.0"
    ),
    "title edited": lambda config: edited(config, title="The live card stays empty"),
    "edited by someone else": lambda config: edited(
        config, "someone", body=f"{BUG_BODY}\nMe too"
    ),
    "answer of the reporter": lambda config: commented(
        config, "reporter", "Version 2.0.2"
    ),
    "comment of someone else": lambda config: commented(config, "someone", "Me too"),
    "objection to a duplicate": lambda config: commented(
        config,
        "someone",
        "Mine is different",
        labels=("bug", "possible-duplicate"),
    ),
    "question of a maintainer": lambda config: commented(
        config,
        "maintainer",
        "Can you send the log?",
        labels=("bug", "stale"),
        association="OWNER",
    ),
    "answer that reopens": closed_unanswered,
    "comment after the release": released,
    "command of a maintainer": lambda config: commented(
        config, "maintainer", "/assistant mute", labels=("bug",), association="OWNER"
    ),
    "question to the assistant": lambda config: commented(
        config,
        "maintainer",
        "/assistant ask Which file reads the version?",
        labels=("bug",),
        association="OWNER",
    ),
    "command the reporter may not give": lambda config: commented(
        config, "reporter", "/assistant mute"
    ),
    "answer on a muted issue": lambda config: commented(
        config,
        "reporter",
        "Version 2.0.2",
        labels=("bug", "needs-info", "assistant-muted"),
    ),
    "run by hand": by_hand,
    "unknown event": lambda config: (
        FakeGitHub(config, make_issue()),
        "issues",
        event("labeled", make_issue()),
    ),
}


def request_of(call):
    """A write to GitHub as a golden file shows it: a mutation by its name, the
    document itself being pinned by tests/test_graphql_contract.py."""
    method, target, data = call
    if method == "GRAPHQL":
        target = re.search(r"mutation[^{]*\{\s*(\w+)", target)[1]
    return [method, target, data]


@pytest.mark.parametrize("case", EVENTS)
def test_each_event_plans_as_its_golden_file(config, case):
    github, name, payload = EVENTS[case](config)
    plan = assistant.handle_event(github, name, payload, config)
    done = {
        "plan": dataclasses.asdict(plan),
        "writes": github.writes,
        "requests": [request_of(call) for call in github.writes_made()],
    }
    assert_golden(f"events/{case.replace(' ', '-')}.json.txt", shown(done))


# The job summaries of the commands


def waiting_since(config, days):
    github = FakeGitHub(
        config, make_issue(labels=("bug", "needs-info"), days_ago=days + 1)
    )
    github.event_store[12] = [
        {
            "event": "labeled",
            "label": {"name": "needs-info"},
            "actor": BOT_USER,
            "created_at": at(days),
        }
    ]
    github.comment_store[12] = [analysis(questions=1, days_ago=days)]
    return github


def labels_to_change(config):
    github = FakeGitHub(config)
    github.label_store = github.label_store[1:]
    github.label_store[0]["color"] = "000000"
    return github


def stopped_by_github(config):
    github = FakeGitHub(config, make_issue())
    github.fail("^POST issues/12/comments", assistant.GitHubError("gh api: HTTP 502"))
    return github


def sweep_stopped_by_github(config):
    github = waiting_since(config, 16)
    github.fail("^POST issues/12/comments", assistant.GitHubError("gh api: HTTP 502"))
    return github


def open_alert(config):
    github = FakeGitHub(config)
    github.alert_store = [make_alert(4, "CodeQL", "js/xss", "x.js")]
    return github


def without_gitignore(root):
    (root / ".gitignore").unlink()


ANSWER = json.dumps(make_answer(missing_information=["Which version do you run?"]))
SUMMARIES = {
    "event": (
        lambda config: opened(config)[0],
        ["event"],
        {"GITHUB_EVENT_NAME": "issues"},
    ),
    "apply": (
        lambda config: FakeGitHub(config, make_issue()),
        ["apply", "--issue", "12", "--mode", "triage"],
        {"RESULT": ANSWER, "ENGINE": "claude", "GITHUB_SHA": "abc"},
    ),
    "apply-dry-run": (
        lambda config: FakeGitHub(config, make_issue(), dry_run=True),
        ["apply", "--issue", "12", "--mode", "triage"],
        {"RESULT": ANSWER, "ENGINE": "claude", "GITHUB_SHA": "abc", "DRY_RUN": "true"},
    ),
    "sweep": (lambda config: waiting_since(config, 16), ["sweep"], {}),
    "sync-labels": (labels_to_change, ["sync-labels"], {}),
    "stopped-by-github": (
        stopped_by_github,
        ["apply", "--issue", "12", "--mode", "triage"],
        {"RESULT": ANSWER, "ENGINE": "claude", "GITHUB_SHA": "abc"},
    ),
    "sweep-incomplete": (sweep_stopped_by_github, ["sweep"], {}),
    "findings": (open_alert, ["findings"], {}),
    "check": (lambda config: None, ["check"], {}),
}
# The cases that change the files of the repository, in a copy of their own.
CHANGED_ROOTS = {"check": without_gitignore}


@pytest.mark.parametrize("case", SUMMARIES)
def test_each_job_summary_reads_as_its_golden_file(
    request, monkeypatch, tmp_path, sample_root, config, case
):
    build, argv, env = SUMMARIES[case]
    github = build(config)
    if case in CHANGED_ROOTS:
        sample_root = request.getfixturevalue("root")
        CHANGED_ROOTS[case](sample_root)
    if case == "event":
        path = tmp_path / "event.json"
        path.write_text(json.dumps(event("opened", github.issue(12))), "utf-8")
        env = {**env, "GITHUB_EVENT_PATH": str(path)}
    monkeypatch.setattr(assistant, "datetime", Frozen)
    _, _, summary = run_main(monkeypatch, tmp_path, sample_root, github, *argv, **env)
    assert_golden(f"summaries/{case}.md.txt", summary)


def test_every_golden_file_has_its_test():
    names = {
        *comment_files(),
        *(f"context/{mode}.txt" for mode in assistant.MODES),
        "context/triage-related.txt",
        *(f"events/{case.replace(' ', '-')}.json.txt" for case in EVENTS),
        *(f"summaries/{case}.md.txt" for case in SUMMARIES),
    }
    assert golden_files() == names
