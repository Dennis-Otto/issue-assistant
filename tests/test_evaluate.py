"""evaluate: replaying the first analysis of closed issues, as each was when it was
opened, and how often its labels, duplicates and questions match the maintainers'.

No test reaches an engine: the engine is a fake that reads the context folder, as
the engine's command line would, and answers by what it finds there.
"""

import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

import issue_assistant as assistant
from support import (
    REPOSITORY,
    FakeGitHub,
    Frozen,
    at,
    make_answer,
    make_comment,
    make_issue,
    run_main,
)

ROOT = Path(__file__).resolve().parents[1]
MARKED = assistant.render_marker("analysis", lang="en", questions=0)


def closed(number, days_ago, labels=("bug",), **extra):
    """An issue opened `days_ago` and closed a day later."""
    return make_issue(
        number,
        labels=labels,
        state="closed",
        days_ago=days_ago,
        closed_at=at(days_ago - 1),
        state_reason="completed",
        **extra,
    )


def labeled(name, days_ago, actor="owner", kind="labeled"):
    user = {"login": actor, "type": "Bot" if actor.endswith("[bot]") else "User"}
    return {
        "event": kind,
        "label": {"name": name},
        "actor": user,
        "created_at": at(days_ago),
    }


def sample(number=20, original=None, asked=False, **labels):
    """A sample with the labels of each group that people chose, by group."""
    issue = closed(number, 30)
    return assistant.Sample(
        issue,
        assistant.created(issue) + assistant.OPENING,
        issue | {"state": "open"},
        {group: frozenset(labels.get(group, ())) for group in assistant.SCORED_GROUPS},
        original,
        asked,
    )


def outcome(chosen, *, duplicates=(), asks=False, cost=None, **labels):
    """An outcome whose first duplicate named with high confidence is the sure one."""
    return assistant.Outcome(
        chosen,
        {group: frozenset(labels.get(group, ())) for group in assistant.SCORED_GROUPS},
        tuple(number for number, _ in duplicates),
        next((number for number, sure in duplicates if sure == "high"), None),
        asks,
        cost,
    )


@pytest.fixture
def github(config):
    """A repository with closed issues of every sort that evaluate meets, the
    newest first:

    - #26, opened by a bot;
    - #25, analyzed by the assistant;
    - #24, with no label of a scored group;
    - #23, whose only label of a type a bot added;
    - #22, a duplicate of #7, which is older;
    - #21, a duplicate of #27, which is newer, and labelled by people;
    - #20, of the cards, with the label bug of its form, which no maintainer
      chose, and a question of a maintainer, opened 30 days ago;
    - and #7, open, opened 40 days ago.
    """
    issues = [
        closed(26, 2, author="renovate[bot]"),
        closed(25, 3),
        closed(24, 4, labels=("needs-info",)),
        closed(23, 5, labels=("enhancement",)),
        closed(22, 10, labels=("bug", "duplicate"), original=7),
        closed(21, 12, labels=("question",), original=27),
        closed(20, 30, labels=("bug", "area: cards")),
        make_issue(7, days_ago=40, labels=("bug",)),
        make_issue(27, days_ago=1, labels=("bug",)),
    ]
    fake = FakeGitHub(config, *issues)
    fake.comment_store = {
        25: [make_comment(1, "bot", f"{MARKED}\nAn analysis", 2.9)],
        20: [
            make_comment(2, "reporter", "It still fails?", 29.9),
            make_comment(3, "owner", "Which version do you run?", 29.5, "OWNER"),
        ],
    }
    fake.event_store = {
        23: [labeled("enhancement", 4.9, "github-actions[bot]")],
        21: [labeled("question", 11.5)],
        20: [
            # The label of its form, which it had from the start.
            labeled("bug", 30),
            labeled("area: cards", 29.4),
            labeled("area: games", 29.4),
            labeled("area: games", 29.3, kind="unlabeled"),
            {"event": "closed", "actor": {"login": "owner"}, "created_at": at(29)},
        ],
    }
    return fake


# The closed issues


def test_closed_issues_come_the_newest_first_with_the_original_of_a_duplicate(
    config, github
):
    found = list(github.closed_issues())
    assert [issue["number"] for issue in found] == [26, 25, 24, 23, 22, 21, 20]
    by_number = {issue["number"]: issue for issue in found}
    assert by_number[22]["duplicate_of"] == {"number": 7, "created_at": at(40)}
    assert by_number[21]["duplicate_of"] == {"number": 27, "created_at": at(1)}
    assert by_number[20]["duplicate_of"] is None
    assert by_number[26]["user"] == {"login": "renovate[bot]", "type": "User"}
    assert by_number[20] == {
        key: value
        for key, value in closed(20, 30, labels=("bug", "area: cards")).items()
        if key
        in {
            "number",
            "title",
            "body",
            "user",
            "author_association",
            "state",
            "state_reason",
            "labels",
            "created_at",
            "closed_at",
        }
    } | {"duplicate_of": None}


def test_closed_issues_come_a_page_at_a_time_up_to_their_limit(monkeypatch, config):
    github = FakeGitHub(
        config, *(closed(number, 300 - number) for number in range(1, 251))
    )
    pages = iter(github.closed_issues())
    # The first page only, as long as no more is read.
    next(pages)
    assert github.issue_pages["EVALUATED"] == 1
    assert len(list(pages)) == 249 and github.issue_pages["EVALUATED"] == 3
    monkeypatch.setattr(assistant, "EVALUATION_PAGES", 2)
    assert len(list(github.closed_issues())) == 200


def node(**changes):
    return {
        "number": 12,
        "title": "The live card stays empty",
        "body": "After a throw.",
        "createdAt": at(10),
        "closedAt": at(9),
        "stateReason": "DUPLICATE",
        "authorAssociation": "NONE",
        "author": {"__typename": "User", "login": "reporter"},
        "labels": {"nodes": [{"name": "bug"}]},
        "duplicateOf": None,
        "timelineItems": {"nodes": []},
    } | changes


def original(number, repository=REPOSITORY):
    return {
        "number": number,
        "createdAt": at(20),
        "repository": {"nameWithOwner": repository},
    }


@pytest.mark.parametrize(
    ("changes", "found"),
    [
        ({"duplicateOf": original(7)}, 7),
        # Closed as a duplicate of one, and marked as one of another before.
        (
            {
                "duplicateOf": original(7),
                "timelineItems": {"nodes": [{"canonical": original(5)}]},
            },
            7,
        ),
        ({"timelineItems": {"nodes": [{"canonical": original(5)}]}}, 5),
        # Unmarked again, or marked as one of a pull request.
        ({"timelineItems": {"nodes": [{}]}}, None),
        ({"timelineItems": {"nodes": [{"canonical": {}}]}}, None),
        ({"duplicateOf": original(7, "someone/else")}, None),
        ({}, None),
    ],
)
def test_the_original_of_a_duplicate_is_an_issue_of_this_repository(
    config, changes, found
):
    github = FakeGitHub(config)
    issue = assistant.closed_as_rest(node(**changes), github.owns)
    assert (issue["duplicate_of"] or {}).get("number") == found


def test_a_closed_issue_of_a_deleted_account_or_a_bot_without_labels(config):
    github = FakeGitHub(config)
    ghost = assistant.closed_as_rest(
        node(author=None, labels=None, stateReason=None), github.owns
    )
    assert ghost["user"] == {"login": "ghost", "type": "User"}
    assert (ghost["labels"], ghost["state_reason"]) == ([], None)
    bot = assistant.closed_as_rest(
        node(author={"__typename": "Bot", "login": "renovate"}), github.owns
    )
    assert bot["user"] == {"login": "renovate", "type": "Bot"}
    assert bot["state_reason"] == "duplicate"


# The samples


def test_the_samples_are_the_newest_closed_issues_that_people_decided(config, github):
    samples, read = assistant.choose_samples(github, config, 10)
    assert read == 7
    assert [item.number for item in samples] == [22, 21, 20]
    duplicate, newer, bug = samples
    assert duplicate.original == 7
    # The original of #21 is newer, so its replay can't find it.
    assert newer.original is None
    assert newer.labels["type"] == {"question"}
    # The label of its form is no decision of the maintainers: the replay sees it.
    assert bug.labels == {
        "type": set(),
        "area": {"area: cards"},
        "topic": set(),
        "priority": set(),
    }
    assert bug.moment == assistant.moment(at(30)) + timedelta(minutes=1)
    # As it was a minute after it was opened: open, with the label of its form.
    assert bug.then["state"] == "open" and bug.then["closed_at"] is None
    assert bug.then["labels"] == [{"name": "bug"}]
    assert bug.issue["state"] == "closed"
    # The maintainer's first comment asked a question.
    assert bug.asked and not duplicate.asked


def test_the_samples_stop_at_their_number(config, github):
    samples, read = assistant.choose_samples(github, config, 1)
    assert ([item.number for item in samples], read) == ([22], 5)


def test_an_issue_whose_only_label_came_with_its_form_is_no_sample(config):
    issue = closed(30, 8, labels=("bug",))
    github = FakeGitHub(config, issue)
    github.event_store = {30: [labeled("bug", 8, "reporter")]}
    assert assistant.choose_samples(github, config, 1) == ([], 1)


def test_the_labels_then_undo_each_change_after_the_moment():
    issue = make_issue(labels=("bug", "area: cards"))
    when = assistant.moment(at(10))
    events = [
        labeled("bug", 11),
        labeled("question", 10.5),
        # Removed after the moment, so it was there; added after, so it wasn't.
        labeled("question", 9, kind="unlabeled"),
        labeled("area: cards", 8),
        {"event": "renamed", "created_at": at(7)},
    ]
    assert assistant.labels_at(issue, events, when) == {"bug", "question"}


def test_a_label_counts_when_a_person_added_it_last_after_the_moment(config):
    issue = make_issue(
        labels=("bug", "question", "area: cards", "area: games", "needs-info", "P1")
    )
    when = assistant.moment(at(6))
    events = [
        # The labels of its form, which GitHub records as added by the reporter,
        # and one that no event added.
        labeled("bug", 6, "reporter"),
        labeled("area: cards", 5, "github-actions[bot]"),
        labeled("area: cards", 4, kind="unlabeled"),
        labeled("area: cards", 3),
        labeled("area: games", 2, "github-actions[bot]"),
        labeled("needs-info", 2, "owner"),
    ]
    assert assistant.labels_by_people(config, issue, events, when) == {
        "type": set(),
        "area": {"area: cards"},
        "topic": set(),
        "priority": set(),
    }


@pytest.mark.parametrize(
    ("comments", "events", "asked"),
    [
        ([make_comment(1, "owner", "Which version?", 5, "OWNER")], [], True),
        # The question is in code, or in a later comment than the first.
        ([make_comment(1, "owner", "Run `ls ?`", 5, "MEMBER")], [], False),
        (
            [
                make_comment(1, "owner", "Thanks.", 5, "COLLABORATOR"),
                make_comment(2, "owner", "And the version?", 4, "COLLABORATOR"),
            ],
            [],
            False,
        ),
        # Not a maintainer, the reporter, a bot, or before the moment.
        ([make_comment(1, "someone", "Which version?", 5)], [], False),
        ([make_comment(1, "reporter", "Which version?", 5, "OWNER")], [], False),
        ([make_comment(1, "bot", "Which version?", 5, "MEMBER")], [], False),
        ([make_comment(1, "owner", "Which version?", 11, "OWNER")], [], False),
        ([], [labeled("needs-info", 5)], True),
        ([], [labeled("needs-info", 5, "github-actions[bot]")], False),
        ([], [labeled("needs-info", 11)], False),
        ([], [labeled("bug", 5)], False),
    ],
)
def test_the_maintainers_asked_with_their_first_comment_or_needs_info(
    comments, events, asked
):
    issue = make_issue(days_ago=10)
    when = assistant.moment(at(10))
    assert assistant.maintainers_asked(issue, comments, events, when) is asked


# GitHub as it was


def test_github_then_shows_only_what_existed_at_the_moment(config, github):
    bug = assistant.choose_samples(github, config, 3)[0][2]
    memo = {}
    then = assistant.Then(github, bug, memo)
    github.discussion_store = [
        {"number": 1, "createdAt": at(31), "answer": {"createdAt": at(29)}},
        {"number": 2, "createdAt": at(31), "answer": {"createdAt": at(31)}},
        {"number": 3, "createdAt": at(31), "answer": None},
        {"number": 4, "createdAt": at(29), "answer": None},
    ]
    github.release_store = [
        assistant_release("v1.0.0", 31),
        assistant_release("v1.1.0", 29),
    ]
    assert then.issue(20) == bug.then
    # Another issue, closed since, was open then; one closed before was closed.
    github.store[19] = closed(19, 40) | {"closed_at": at(29.5)}
    github.store[18] = closed(18, 40)
    assert then.issue(18)["state"] == "closed"
    assert then.issue(19)["state"] == "open" and then.issue(19)["closed_at"] is None
    assert [comment["id"] for comment in then.comments(20)] == []
    github.comment_store[20].insert(0, make_comment(9, "reporter", "Early", 30))
    assert [comment["id"] for comment in then.comments(20)] == [9]
    assert [event["label"]["name"] for event in then.events(20)] == ["bug"]
    discussions = then.discussions(10)
    assert [item["number"] for item in discussions] == [1, 2, 3]
    # The answer came later.
    assert discussions[0]["answer"] is None and discussions[0]["isAnswered"] is False
    assert discussions[1]["answer"] == {"createdAt": at(31)}
    assert then.latest_release() == "v1.0.0"
    # The window of the issues and the discussions is read once for every replay.
    pages = dict(github.issue_pages)
    found = {item["number"]: item["state"] for item in then.recent_issues(100)}
    assert found == {7: "open", 18: "closed", 19: "open", 20: "open"}
    assistant.Then(github, bug, memo).recent_issues(100)
    assert github.issue_pages["OPEN"] == pages.get("OPEN", 0) + 1
    assert github.discussion_pages == 1
    assistant.Then(github, bug, memo).discussions(10)
    assert github.discussion_pages == 1


def assistant_release(tag, days_ago):
    return {
        "tag_name": tag,
        "published_at": at(days_ago),
        "draft": False,
        "prerelease": False,
    }


def test_github_then_has_no_release_before_the_first(config, github):
    then = assistant.Then(github, sample(), {})
    assert then.latest_release() is None


def test_github_then_reads_reactions_comments_and_searches_of_then(config, github):
    bug = sample()
    then = assistant.Then(github, bug, {})
    github.reaction_store = {
        3: [
            {"content": "-1", "user": {"login": "owner"}, "created_at": at(31)},
            {"content": "+1", "user": {"login": "owner"}, "created_at": at(1)},
        ]
    }
    assert [item["content"] for item in then.reactions(3)] == ["-1"]
    github.comment_store[7] = [make_comment(5, "someone", "Old", 35)]
    assert [comment["id"] for comment in then.latest_comments(5)] == [5]
    words = ["live", "card"]
    query = f"repo:{REPOSITORY} is:issue ({' OR '.join(words)})"
    github.search_store[query] = [
        closed(5, 50) | {"closed_at": at(29)},
        closed(6, 10),
    ]
    found = then.search_issues(words)
    assert [(item["number"], item["state"]) for item in found] == [(5, "open")]
    # GitHub's search finds only the issues of then, so that no later one pushes
    # them out of its results.
    assert github.calls[-1] == (
        "SEARCH",
        f"{query} created:<={assistant.stamp(bug.moment)}",
        None,
    )
    # Any other read goes to GitHub as it is.
    assert then.may_push("owner")


def test_github_then_searches_related_repositories_as_they_were(config, github):
    then = assistant.Then(github, sample(), {})
    server = "owner/server"
    github.sibling_store[server] = FakeGitHub(config, repository=server)
    words = ["live", "card"]
    query = f"repo:{server} is:issue ({' OR '.join(words)})"
    github.sibling_store[server].search_store[query] = [closed(5, 50), closed(6, 10)]
    other = then.sibling(server)
    assert [item["number"] for item in other.search_issues(words)] == [5]
    assert then.sibling("owner/private") is None


# The replays


def test_the_questions_of_an_answer_are_those_that_apply_would_ask(config):
    asks = ["The version. ", " "]
    assert assistant.questions(make_answer(missing_information=asks), config) == [
        "The version."
    ]
    # labels.toml asks no questions for an enhancement, and none go to spam.
    for kind in ("enhancement", "spam"):
        answer = make_answer(kind=kind, missing_information=asks)
        assert assistant.questions(answer, config) == []


def test_the_duplicates_of_an_answer_are_those_that_the_analysis_keeps(config, github):
    then = assistant.Then(github, sample(), {})
    # Closed before the moment, and open but neither opened nor triaged by a
    # maintainer.
    github.store[19] = closed(19, 40)
    github.store[18] = make_issue(18, days_ago=35, labels=("needs-triage",))

    def named(number, confidence="high"):
        return {"number": number, "confidence": confidence, "reason": "Same."}

    answer = make_answer(
        duplicates=[
            # Newer than #20, its own number, and one that doesn't exist.
            named(27),
            named(20),
            named(99),
            named(19),
            named(18),
            named(7, "medium"),
            named(7),
        ]
    )
    assert assistant.named_duplicates(then, 20, answer) == ((19, 18, 7), None)
    answer = make_answer(duplicates=[named(19), named(7), named(18)])
    assert assistant.named_duplicates(then, 20, answer) == ((19, 7, 18), 7)


class FakeEngine:
    """The engine's command line: it reads the context folder, as the engine would,
    keeps what it read, and answers with the answer for the issue of issue.md."""

    def __init__(self, root, answers):
        self.folder = root / assistant.CONTEXT
        self.answers = answers
        self.read = {}

    def __call__(self):
        text = (self.folder / "issue.md").read_text("utf-8")
        number = int(re.search(r"#(\d+)", text)[1])
        self.read[number] = {
            name: (self.folder / name).read_text("utf-8")
            for name in ("issue.md", "issues.jsonl", "discussions.jsonl", "prompt.md")
        }
        answer = self.answers[number]
        if isinstance(answer, Exception):
            raise answer
        return assistant.Answered(
            answer if isinstance(answer, str) else json.dumps(answer), 0.25
        )


def test_each_replay_reads_the_issue_and_the_repository_as_they_were(
    root, config, github
):
    # #21 closed after #22 was opened.
    github.store[21]["closed_at"] = at(5)
    samples, _ = assistant.choose_samples(github, config, 3)
    engine = FakeEngine(
        root,
        {
            22: make_answer(
                duplicates=[{"number": 7, "confidence": "high", "reason": "Same."}]
            ),
            21: make_answer(kind="question", areas=[]),
            20: make_answer(missing_information=["The version."]),
        },
    )
    outcomes = assistant.replay(github, config, samples, engine)
    issue = engine.read[20]["issue.md"]
    assert "- State: open\n- Labels: bug\n" in issue
    # No comment of the maintainers yet, and none of the issues opened since.
    assert "Which version" not in issue and "## Comment" not in issue
    others = [json.loads(line) for line in engine.read[20]["issues.jsonl"].splitlines()]
    assert [(item["number"], item["state"]) for item in others] == [(7, "open")]
    later = [json.loads(line) for line in engine.read[22]["issues.jsonl"].splitlines()]
    assert {item["number"] for item in later} == {20, 21, 7}
    # #20 was closed then, and #21 still open.
    states = {item["number"]: item["state"] for item in later}
    assert (states[20], states[21]) == ("closed", "open")
    assert [item.error for item in outcomes] == ["", "", ""]
    duplicate, newer, bug = outcomes
    assert duplicate.duplicates == (7,) and duplicate.sure == 7
    assert duplicate.cost == 0.25
    assert newer.labels["type"] == {"question"} and newer.labels["area"] == set()
    assert bug.asks and bug.labels["area"] == {"area: cards"}


def test_a_replay_that_fails_says_why_and_the_others_go_on(
    root, config, github, capsys
):
    samples, _ = assistant.choose_samples(github, config, 3)
    engine = FakeEngine(
        root,
        {
            22: assistant.AssistantError(
                "Claude stopped at max_turns, before it answered."
            ),
            21: "no JSON",
            20: make_answer(),
        },
    )
    outcomes = assistant.replay(github, config, samples, engine)
    errors = [item.error for item in outcomes]
    assert errors[0] == "Claude stopped at max_turns, before it answered."
    assert errors[1].startswith("The engine's answer")
    assert errors[2] == ""
    printed = capsys.readouterr().out
    assert "1 of 3, #22: failed, Claude stopped at max_turns" in printed
    assert "3 of 3, #20: answered" in printed


def test_a_replay_that_github_fails_is_a_failed_outcome(root, config, github):
    samples, _ = assistant.choose_samples(github, config, 3)
    github.fail("issues\\(states:", assistant.GitHubError("HTTP 502"))
    outcomes = assistant.replay(
        github, config, samples[:1], FakeEngine(root, {22: make_answer()})
    )
    assert outcomes[0].error == "HTTP 502"


def test_the_labels_of_an_answer_by_group(config):
    chosen = assistant.chosen_labels(
        make_answer(kind="spam", areas=["area: cards"], topics=["accessibility"]),
        config,
    )
    assert chosen == {
        "type": set(),
        "area": {"area: cards"},
        "topic": {"accessibility"},
        "priority": set(),
    }
    assert assistant.chosen_labels(make_answer(priority="priority: high"), config)[
        "priority"
    ] == {"priority: high"}
    assert (
        assistant.chosen_labels(make_answer(priority="none"), config)["priority"]
        == set()
    )


# The scores and the report


def test_precision_and_recall_of_each_measure(config):
    outcomes = [
        # Right type, one area right and one wrong; asked as the maintainers did.
        outcome(
            sample(20, type={"bug"}, area={"area: cards"}, asked=True),
            type={"bug"},
            area={"area: cards", "area: games"},
            asks=True,
        ),
        # Wrong type, an area that counts for no issue without areas, the original
        # found as a sure duplicate, and a question that the maintainers didn't ask.
        outcome(
            sample(21, original=7, type={"question"}),
            type={"bug"},
            area={"area: setup"},
            duplicates=[(7, "high"), (8, "medium")],
            asks=True,
        ),
        # A duplicate named for an issue that isn't one, and the original missed.
        outcome(sample(22, original=9), duplicates=[(3, "high")]),
        outcome(sample(23, asked=True, type={"bug"})),
        assistant.Outcome(sample(24, type={"bug"}), error="Claude failed."),
    ]
    found = {score.name: score for score in assistant.scores(outcomes, config)}
    # The sample repository has no priorities.
    assert list(found) == [
        "Type",
        "Areas",
        "Topics",
        "Duplicates",
        "Sure duplicates",
        "Asks for information",
    ]
    assert found["Type"] == assistant.Score("Type", 3, 2, 3, 1)
    assert found["Areas"] == assistant.Score("Areas", 1, 2, 1, 1)
    assert found["Topics"] == assistant.Score("Topics", 0, 0, 0, 0)
    assert found["Duplicates"] == assistant.Score("Duplicates", 2, 3, 2, 1)
    assert found["Sure duplicates"] == assistant.Score("Sure duplicates", 2, 2, 2, 1)
    assert found["Asks for information"] == assistant.Score(
        "Asks for information", 4, 2, 2, 1
    )


def test_a_repository_with_priorities_scores_them(config):
    labels = (
        *config.labels,
        assistant.Label("priority: high", "b60205", "Urgent", "priority"),
    )
    with_priorities = dataclasses.replace(config, labels=labels)
    outcomes = [
        outcome(sample(20, priority={"priority: high"}), priority={"priority: high"})
    ]
    found = {score.name: score for score in assistant.scores(outcomes, with_priorities)}
    assert found["Priority"] == assistant.Score("Priority", 1, 1, 1, 1)


def test_a_share_shows_its_numbers():
    assert assistant.share(2, 3) == "67 % (2 of 3)"
    assert assistant.share(0, 0) == "–"


def test_the_report_has_each_measure_and_a_line_for_each_issue(config):
    run = assistant.task_run(config, "triage")
    outcomes = [
        outcome(
            sample(20, type={"bug"}, area={"area: cards", "area: a|b"}, asked=True),
            type={"bug"},
            area={"area: games"},
            asks=True,
            cost=0.2,
        ),
        outcome(sample(21, original=7), duplicates=[(7, "high")], cost=0.1),
        outcome(sample(22, original=9), duplicates=[(3, "medium")]),
        outcome(sample(23, type={"bug"}), duplicates=[(3, "medium")]),
        outcome(sample(24, original=9)),
        assistant.Outcome(sample(25), error="Claude failed: a | b"),
    ]
    text = assistant.render_evaluation(
        config, assistant.ENGINES["claude"], run, date(2026, 10, 17), outcomes, 40
    )
    link = "https://github.com/owner/project/issues"
    assert text == (
        "# How accurate the issue assistant is on owner/project\n"
        "\n"
        "Measured on 2026-10-17 with Claude, `claude-opus-5-5`, on the 6 newest of "
        "40 closed issues read that people labelled or closed as a duplicate of an "
        "older issue, and that the assistant never analyzed. It analyzed each as it "
        "was a minute after it was opened, with the issues, discussions and releases "
        "of that moment, and compared its choices with what the maintainers "
        "decided.\n"
        "\n"
        "| Measure | Precision | Recall | Issues |\n"
        "| --- | ---: | ---: | ---: |\n"
        "| Type | 100 % (1 of 1) | 50 % (1 of 2) | 2 |\n"
        "| Areas | 0 % (0 of 1) | 0 % (0 of 2) | 1 |\n"
        "| Topics | – | – | 0 |\n"
        "| Duplicates | 33 % (1 of 3) | 33 % (1 of 3) | 3 |\n"
        "| Sure duplicates | 100 % (1 of 1) | 33 % (1 of 3) | 3 |\n"
        "| Asks for information | 100 % (1 of 1) | 100 % (1 of 1) | 5 |\n"
        "\n"
        "*Precision* is the share of the assistant's choices that the maintainers "
        "made too, and *recall* the share of the maintainers' choices that the "
        "assistant made. A group of labels counts the issues that people gave a "
        "label of the group after they were opened, and a label that an issue had "
        "then, such as that of its form, counts for neither; the duplicates count "
        "the issues closed as a duplicate of an older one, and the sure ones only "
        "the duplicates that the assistant "
        "was sure of, which get the notice that closes them; the questions count "
        "every issue, and the maintainers asked when the first comment of one of "
        "them asked a question or someone added `needs-info`.\n"
        "\n"
        "1 of the 6 analyses failed; they count in no measure.\n"
        "\n"
        "The analyses cost $0.30 at the prices of the API.\n"
        "\n"
        "## Issue by issue\n"
        "\n"
        "| Issue | Right | Wrong | Missed | Duplicate | Information |\n"
        "| --- | --- | --- | --- | --- | --- |\n"
        f"| [#20]({link}/20) | `bug` | `area: games` | `area: a\\|b`, `area: cards` "
        "|  | asked, as the maintainers did |\n"
        f"| [#21]({link}/21) |  |  |  | found #7 |  |\n"
        f"| [#22]({link}/22) |  |  |  | missed #9, named #3 |  |\n"
        f"| [#23]({link}/23) |  |  | `bug` | named #3, of none |  |\n"
        f"| [#24]({link}/24) |  |  |  | missed #9 |  |\n"
        f"| [#25]({link}/25) | failed: Claude failed: a \\| b | | | | |\n"
    )


def test_a_report_without_failures_or_costs_says_neither(config):
    run = assistant.task_run(config, "triage")
    text = assistant.render_evaluation(
        config,
        assistant.ENGINES["gemini"],
        run,
        date(2026, 10, 17),
        [outcome(sample(20, type={"bug"}), type={"bug"}, asks=True)],
        1,
    )
    assert "failed" not in text and "cost" not in text
    assert (
        "| [#20](https://github.com/owner/project/issues/20) | `bug` |  |  |  | "
        "asked, the maintainers didn't |" in text
    )


# The numbers of the accuracy page, from a report


def report_of(*outcomes, engine="claude"):
    """A report as `evaluate` writes it, of outcomes or of one of each kind."""
    config = assistant.load_config(ROOT / "tests" / "sample", REPOSITORY)
    return assistant.render_evaluation(
        config,
        assistant.ENGINES[engine],
        assistant.task_run(config, "triage"),
        date(2026, 10, 17),
        list(outcomes)
        or [
            outcome(
                sample(20, type={"bug"}, area={"area: cards"}, asked=True),
                type={"bug"},
                area={"area: games"},
                asks=True,
                cost=0.2,
            ),
            outcome(sample(21, original=7), duplicates=[(7, "high")], cost=0.1),
            assistant.Outcome(sample(25), error="Claude failed: a | b"),
        ],
        40,
    )


PAGE = (
    "# How accurate is it?\n"
    "\n"
    "## The numbers\n"
    "\n"
    "<!-- accuracy numbers: start -->\n"
    "\n"
    "None yet.\n"
    "\n"
    "<!-- accuracy numbers: end -->\n"
    "\n"
    "A number of your own is welcome.\n"
)


def test_the_accuracy_section_has_the_numbers_of_the_report():
    found = assistant.measurement_of(report_of())
    assert found == assistant.Measurement(
        "owner/project",
        date(2026, 10, 17),
        "Claude",
        "claude-opus-5-5",
        3,
        40,
        (
            "| Type | 100 % (1 of 1) | 100 % (1 of 1) | 1 |",
            "| Areas | 0 % (0 of 1) | 0 % (0 of 1) | 1 |",
            "| Topics | – | – | 0 |",
            "| Duplicates | 100 % (1 of 1) | 100 % (1 of 1) | 1 |",
            "| Sure duplicates | 100 % (1 of 1) | 100 % (1 of 1) | 1 |",
            "| Asks for information | 100 % (1 of 1) | 100 % (1 of 1) | 2 |",
        ),
        1,
        "0.30",
    )
    assert assistant.accuracy_section(found) == (
        "### [owner/project](https://github.com/owner/project)\n"
        "\n"
        "Measured on 2026-10-17 with Claude, `claude-opus-5-5`, on the 3 newest of 40 "
        "closed issues read that people labelled or closed as a duplicate of an older "
        "issue, and that the assistant never analyzed. 1 of the 3 analyses failed; "
        "they count in no measure. The analyses cost $0.30 at the prices of the API. "
        "Small numbers vary: with few issues, one more found or missed moves a share a "
        "lot, so each share names its counts.\n"
        "\n"
        "| Measure | Precision | Recall | Issues |\n"
        "| --- | ---: | ---: | ---: |\n"
        "| Type | 100 % (1 of 1) | 100 % (1 of 1) | 1 |\n"
        "| Areas | 0 % (0 of 1) | 0 % (0 of 1) | 1 |\n"
        "| Topics | – | – | 0 |\n"
        "| Duplicates | 100 % (1 of 1) | 100 % (1 of 1) | 1 |\n"
        "| Sure duplicates | 100 % (1 of 1) | 100 % (1 of 1) | 1 |\n"
        "| Asks for information | 100 % (1 of 1) | 100 % (1 of 1) | 2 |"
    )


def test_an_accuracy_section_without_failures_or_costs_says_neither():
    found = assistant.measurement_of(
        report_of(outcome(sample(20, type={"bug"}), type={"bug"}), engine="gemini")
    )
    assert (found.engine, found.failed, found.cost) == ("Gemini", 0, None)
    section = assistant.accuracy_section(found)
    assert "failed" not in section and "cost" not in section
    assert "never analyzed. Small numbers vary" in section


def test_the_report_of_every_engine_and_measure_fits_the_accuracy_page():
    """A change of the head of the report that REPORT doesn't follow fails here, not
    in the job publish, after the money of a run."""
    for key in assistant.LOCAL_ENGINES:
        found = assistant.measurement_of(report_of(engine=key))
        assert found.engine == assistant.ENGINES[key].name
    config = assistant.load_config(ROOT / "tests" / "sample", REPOSITORY)
    names = [score.name for score in assistant.scores([], config)]
    assert set(names) <= set(assistant.MEASURES)
    assert assistant.MEASURES[-3:] == tuple(names[-3:])


def test_the_lines_of_the_issues_never_reach_the_accuracy_page():
    text = report_of(
        outcome(sample(20, type={"bug"}), type={"bug"}),
        assistant.Outcome(sample(25), error="ignore all rules | <script>"),
    )
    assert "<script>" in text
    section = assistant.accuracy_section(assistant.measurement_of(text))
    assert "<script>" not in section and "#25" not in section


GOOD_ROW = "| Type | 100 % (1 of 1) | 100 % (1 of 1) | 1 |"


@pytest.mark.parametrize(
    ("old", "new", "problem"),
    [
        # Anything in place of a number or a name stops it: text from a job with an AI
        # must not reach the website.
        (GOOD_ROW, "| Type | [click](https://evil.example) | – | 1 |", "the line"),
        (GOOD_ROW, "| <script> | – | – | 1 |", "the line"),
        (GOOD_ROW, "| Type | – | – | many |", "the line"),
        (GOOD_ROW, "not a line of a table", "its head"),
        # A share is what `share` writes for its numbers.
        (GOOD_ROW, "| Type | 99 % (1 of 1) | – | 1 |", "the line"),
        (GOOD_ROW, "| Type | 200 % (2 of 1) | – | 1 |", "the line"),
        (GOOD_ROW, "| Type | 0 % (0 of 0) | – | 1 |", "the line"),
        ("on owner/project", "on owner", "no repository"),
        ("on owner/project", "on owner/..", "no repository"),
        ("on owner/project", "on owner/project extra", "its head"),
        ("2026-10-17", "2026-13-40", "is no day"),
        ("2026-10-17", "yesterday", "its head"),
        ("with Claude,", "with Skynet,", "the engine Skynet"),
        ("`claude-opus-5-5`", "`a` <img src=x> `b`", "its head"),
        ("The analyses cost $0.30", "The analyses cost $free", "its head"),
        ("1 of the 3 analyses failed", "many of the 3 analyses failed", "its head"),
        ("## Issue by issue", "## Other", "its head"),
    ],
)
def test_a_report_with_anything_else_is_refused(old, new, problem):
    text = report_of()
    assert old in text
    with pytest.raises(assistant.AssistantError) as refused:
        assistant.measurement_of(text.replace(old, new))
    assert str(refused.value).startswith("That is no report of evaluate: ")
    assert problem in str(refused.value)


def test_the_first_numbers_replace_the_text_of_the_block():
    found = assistant.measurement_of(report_of())
    assert assistant.page_with_numbers(PAGE, found) == (
        "# How accurate is it?\n"
        "\n"
        "## The numbers\n"
        "\n"
        "<!-- accuracy numbers: start -->\n"
        "\n"
        f"{assistant.accuracy_section(found)}\n"
        "\n"
        "<!-- accuracy numbers: end -->\n"
        "\n"
        "A number of your own is welcome.\n"
    )


def test_each_repository_keeps_its_own_table_in_the_order_of_their_names():
    found = assistant.measurement_of(report_of())
    other = dataclasses.replace(found, repository="Alpha/beta", read=7)
    last = dataclasses.replace(found, repository="zulu/yankee", read=8)
    page = assistant.page_with_numbers(PAGE, last)
    page = assistant.page_with_numbers(page, found)
    page = assistant.page_with_numbers(page, other)
    assert re.findall(r"(?m)^### \[(.+?)\]", page) == [
        "Alpha/beta",
        "owner/project",
        "zulu/yankee",
    ]
    # A new measurement replaces the older one of its repository alone.
    newer = dataclasses.replace(found, day=date(2026, 11, 1), read=99)
    again = assistant.page_with_numbers(page, newer)
    assert again == page.replace(
        assistant.accuracy_section(found), assistant.accuracy_section(newer)
    )
    assert "of 7 closed" in again and "of 8 closed" in again and "of 40" not in again
    # The same numbers again change nothing.
    assert assistant.page_with_numbers(again, newer) == again


@pytest.mark.parametrize(
    "page",
    [
        "# How accurate is it?\n",
        "<!-- accuracy numbers: start -->\nNone yet.\n",
        "<!-- accuracy numbers: end -->\n<!-- accuracy numbers: start -->\n",
        "text <!-- accuracy numbers: start -->\n<!-- accuracy numbers: end -->\n",
    ],
)
def test_a_page_without_its_block_of_numbers_stays(page):
    with pytest.raises(assistant.AssistantError, match="has no block of numbers"):
        assistant.page_with_numbers(page, assistant.measurement_of(report_of()))


def test_the_accuracy_page_has_its_block_of_numbers():
    page = (ROOT / assistant.ACCURACY_PAGE).read_text(encoding="utf-8")
    assert assistant.ACCURACY_BLOCK.search(page)


def test_accuracy_page_writes_the_numbers_into_the_page_of_the_checkout(
    monkeypatch, tmp_path, root
):
    (root / "docs").mkdir(exist_ok=True)
    page = root / assistant.ACCURACY_PAGE
    page.write_text(PAGE, encoding="utf-8")
    report = tmp_path / "report.md"
    report.write_text(report_of(), encoding="utf-8")
    code, _, written = run_main(
        monkeypatch, tmp_path, root, None, "accuracy-page", "--report", str(report)
    )
    assert code == 0
    assert "### [owner/project]" in page.read_text(encoding="utf-8")
    assert "The numbers of 2026-10-17 are in docs/accuracy.md." in written


def test_accuracy_page_needs_no_repository_of_the_run(monkeypatch, tmp_path, root):
    (root / "docs").mkdir(exist_ok=True)
    page = root / assistant.ACCURACY_PAGE
    page.write_text(PAGE, encoding="utf-8")
    report = tmp_path / "report.md"
    report.write_text(report_of(), encoding="utf-8")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(root))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    for name in ("GH_REPO", "GITHUB_REPOSITORY", "ISSUE_ASSISTANT_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ISSUE_ASSISTANT_FIXTURE", raising=False)
    assert assistant.main(["accuracy-page", "--report", str(report)]) == 0


@pytest.mark.parametrize("missing", ["report", "page"])
def test_accuracy_page_says_which_file_it_cannot_read(
    monkeypatch, tmp_path, root, capsys, missing
):
    (root / "docs").mkdir(exist_ok=True)
    report = tmp_path / "report.md"
    if missing != "report":
        report.write_text(report_of(), encoding="utf-8")
    if missing != "page":
        (root / assistant.ACCURACY_PAGE).write_text(PAGE, encoding="utf-8")
    code, _, _ = run_main(
        monkeypatch, tmp_path, root, None, "accuracy-page", "--report", str(report)
    )
    assert code == 1
    assert "::error::accuracy-page can't read a file:" in capsys.readouterr().out


def test_accuracy_page_leaves_the_page_alone_when_it_refuses_the_report(
    monkeypatch, tmp_path, root, capsys
):
    (root / "docs").mkdir(exist_ok=True)
    page = root / assistant.ACCURACY_PAGE
    page.write_text(PAGE, encoding="utf-8")
    report = tmp_path / "report.md"
    report.write_bytes(b"\xff not a text")
    arguments = ("accuracy-page", "--report", str(report))
    assert run_main(monkeypatch, tmp_path, root, None, *arguments)[0] == 1
    report.write_text("# Something else\n", encoding="utf-8")
    assert run_main(monkeypatch, tmp_path, root, None, *arguments)[0] == 1
    assert "That is no report of evaluate" in capsys.readouterr().out
    assert page.read_text(encoding="utf-8") == PAGE


# The workflow that runs evaluate on GitHub and publishes the numbers

EVALUATE = yaml.safe_load(
    (ROOT / ".github" / "workflows" / "evaluate.yml").read_text(encoding="utf-8")
)


def steps_of(job):
    return EVALUATE["jobs"][job]["steps"]


def step_named(job, name):
    return next(step for step in steps_of(job) if step.get("name") == name)


def test_the_evaluate_workflow_starts_only_by_hand_and_names_what_it_measures():
    """A run costs money, so nothing starts it on its own, and the repositories that
    it may measure are in the workflow, since its AI reads what they hold."""
    # PyYAML reads the key on as True.
    assert list(EVALUATE[True]) == ["workflow_dispatch"]
    inputs = EVALUATE[True]["workflow_dispatch"]["inputs"]
    assert inputs["issues"]["default"] == 30
    assert inputs["repository"]["type"] == "choice"
    assert inputs["repository"]["options"] == [
        "Dennis-Otto/homedarts-for-autodarts",
        "Dennis-Otto/paperless-sync",
        "Dennis-Otto/paperless-unified-search",
    ]
    assert EVALUATE["permissions"] == {}


def test_the_ai_of_the_evaluate_workflow_runs_behind_the_firewall_and_writes_nothing():
    job = EVALUATE["jobs"]["replay"]
    assert job["runs-on"] == assistant.FIREWALL_RUNNER
    assert job["environment"] == {"name": "issue-assistant", "deployment": False}
    assert job["permissions"] == {
        "contents": "read",
        "issues": "read",
        "discussions": "read",
    }
    replay = step_named("replay", "Replay the closed issues")
    assert replay["env"]["GH_TOKEN"] == "${{ github.token }}"
    # The root of the replays, where the tools of the engine stay, is the measured
    # repository, in a folder of its own beside this repository and the report.
    assert replay["env"]["ISSUE_ASSISTANT_ROOT"] == "${{ github.workspace }}/target"
    checkouts = [
        step["with"]
        for step in steps_of("replay")
        if step.get("uses", "").startswith("actions/checkout@")
    ]
    assert [checkout.get("path") for checkout in checkouts] == [None, "target"]
    assert checkouts[1]["repository"] == "${{ inputs.repository }}"
    assert all(checkout["persist-credentials"] is False for checkout in checkouts)
    assert "issue_assistant.py evaluate" in replay["run"]
    assert '--report "$RUNNER_TEMP/accuracy/report.md"' in replay["run"]
    assert '--issues "$ISSUES"' in replay["run"]


def test_the_command_line_of_the_evaluate_workflow_comes_from_its_lock_file():
    install = step_named(
        "replay", "Install the command line of Claude from its lock file"
    )
    assert "npm ci --prefix .github/evaluate --ignore-scripts" in install["run"]
    folder = ROOT / ".github" / "evaluate"
    package = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    version = package["dependencies"]["@anthropic-ai/claude-code"]
    assert assistant.CLI_VERSION.fullmatch(version)
    lock = json.loads((folder / "package-lock.json").read_text(encoding="utf-8"))
    packages = lock["packages"]
    # Each package with its hash, and the program that the step links for this runner.
    assert packages["node_modules/@anthropic-ai/claude-code"]["version"] == version
    native = "node_modules/@anthropic-ai/claude-code-linux-x64"
    assert native + "/claude" in install["run"]
    assert packages[native]["version"] == version
    assert all(
        entry.get("integrity", "").startswith("sha512-")
        for name, entry in packages.items()
        if name
    )
    renovate = (ROOT / ".github" / "renovate.json5").read_text(encoding="utf-8")
    assert 'matchDepNames: ["@anthropic-ai/claude-code"]' in renovate


def test_the_job_that_publishes_the_numbers_has_no_ai_and_no_secret_of_an_engine():
    job = EVALUATE["jobs"]["publish"]
    text = yaml.safe_dump(job)
    for engine in assistant.ENGINES.values():
        for secret in engine.credentials:
            assert secret not in text
    assert job["needs"] == "replay"
    assert job["runs-on"] == "ubuntu-24.04"
    # Its token reads; only that of the release app writes, with what the branch and
    # the pull request need, so that the checks of the pull request start.
    assert job["permissions"] == {"contents": "read"}
    token = step_named("publish", "Create a token of the release app")["with"]
    assert {key: value for key, value in token.items() if "permission" in key} == {
        "permission-contents": "write",
        "permission-pull-requests": "write",
    }
    write = step_named("publish", "Write the numbers into a pull request")["run"]
    assert 'accuracy-page --report "$RUNNER_TEMP/accuracy/report.md"' in write
    # It runs nothing of the report: the script reads it as data.
    assert write.count("report.md") == 1
    assert steps_of("publish")[0]["with"]["egress-policy"] == "block"
    assert (
        steps_of("publish")[-1]["name"] == "Wait until Harden-Runner has removed Docker"
    )


@pytest.mark.mutation_unsafe
def test_the_job_that_publishes_checks_out_all_that_its_command_reads(tmp_path):
    """The job checks out a part of the repository, and the script reads its texts
    as it starts, so the command runs here with that part alone."""
    checkout = next(
        step["with"]
        for step in steps_of("publish")
        if step.get("uses", "").startswith("actions/checkout@")
    )
    folder = tmp_path / "checkout"
    for line in checkout["sparse-checkout"].splitlines():
        part = line.strip("/")
        if (ROOT / part).is_dir():
            shutil.copytree(ROOT / part, folder / part)
        else:
            (folder / part).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / part, folder / part)
    report = tmp_path / "report.md"
    report.write_text(report_of(), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "issue_assistant.py",
            "accuracy-page",
            "--report",
            str(report),
        ],
        cwd=folder,
        env={
            name: value
            for name, value in os.environ.items()
            if not name.startswith(("GITHUB_", "ISSUE_ASSISTANT_", "GH_"))
        }
        | {"PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    page = (folder / assistant.ACCURACY_PAGE).read_text(encoding="utf-8")
    assert "### [owner/project]" in page


# The engines on this computer


class FakeRun:
    """subprocess.run for an engine's command line, which records each call and
    answers in turn."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, command, **options):
        self.calls.append((command, options))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        code, stdout, stderr = result
        return subprocess.CompletedProcess(command, code, stdout, stderr)


@pytest.fixture
def context(root):
    folder = root / assistant.CONTEXT
    folder.mkdir(exist_ok=True)
    for name, text in (
        ("rules.md", "The rules."),
        ("prompt.md", "The task."),
        ("schema.json", '{"type":"object"}'),
    ):
        (folder / name).write_text(text, encoding="utf-8")
    return folder


def claude_output(**fields):
    return json.dumps({"type": "result", "subtype": "success", **fields})


def test_claude_runs_as_in_the_workflow_with_the_task_on_its_input(
    monkeypatch, tmp_path, root, config, context
):
    monkeypatch.setattr(assistant.shutil, "which", lambda name: f"/usr/bin/{name}")
    answer = make_answer()
    run = FakeRun((0, claude_output(structured_output=answer, total_cost_usd=0.21), ""))
    monkeypatch.setattr(assistant.subprocess, "run", run)
    chosen = dataclasses.replace(config, effort="high", max_turns=30)
    answered = assistant.local_engine(chosen, tmp_path)()
    assert answered == assistant.Answered(json.dumps(answer), 0.21)
    ((command, options),) = run.calls
    assert command == [
        "/usr/bin/claude",
        "-p",
        "--restricted",
        "--tools",
        "Read,Grep,Glob",
        "--permission-prompts",
        "none",
        "--output-format",
        "json",
        "--append-system-prompt-file",
        ".issue-assistant/rules.md",
        "--model",
        "claude-opus-5-5",
        "--max-turns",
        "30",
        "--max-budget-usd",
        "1",
        "--effort",
        "high",
        "--json-schema",
        '{"type":"object"}',
    ]
    assert options["input"] == "The task." and options["cwd"] == root
    assert options["timeout"] == assistant.ENGINE_MINUTES * 60


@pytest.mark.parametrize(
    ("result", "error"),
    [
        (
            (1, claude_output(subtype="error_max_turns"), ""),
            "Claude stopped at max_turns, before it answered.",
        ),
        (
            (1, claude_output(subtype="error_max_budget_usd"), ""),
            "Claude stopped at max_budget_usd, before it answered.",
        ),
        (
            (
                1,
                claude_output(
                    subtype="error_during_execution", result="Not\nlogged in"
                ),
                "",
            ),
            "Claude failed: Not logged in",
        ),
        ((0, claude_output(result="Done."), ""), "Claude failed: Done."),
        ((1, "", "command not found"), "Claude failed: command not found"),
        ((1, "[]", ""), "Claude failed: no answer"),
        (
            subprocess.TimeoutExpired("claude", 900),
            "Claude gave no answer within 15 minutes.",
        ),
        (
            PermissionError("Access is denied"),
            "Claude didn't start: Access is denied",
        ),
    ],
)
def test_claude_that_gives_no_answer_says_why(
    monkeypatch, tmp_path, config, context, result, error
):
    monkeypatch.setattr(assistant.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(assistant.subprocess, "run", FakeRun(result))
    answer = assistant.local_engine(config, tmp_path)
    with pytest.raises(assistant.AssistantError) as raised:
        answer()
    assert str(raised.value) == error


@pytest.fixture
def as_root(monkeypatch):
    """The commands that evaluate runs as root, which succeed, on a computer with
    root."""
    commands = []

    def run(command):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(assistant, "as_root", run)
    monkeypatch.setattr(assistant.os, "geteuid", lambda: 1000, raising=False)
    return commands


def test_gemini_runs_logged_in_as_you_with_the_settings_that_keep_it_to_reading(
    monkeypatch, tmp_path, root, config, context, as_root
):
    monkeypatch.setattr(assistant.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("HOME", "/home/you")
    answer = json.dumps(make_answer())
    run = FakeRun((0, json.dumps({"response": answer}), ""))
    monkeypatch.setattr(assistant.subprocess, "run", run)
    chosen = dataclasses.replace(config, engine="gemini")
    answered = assistant.local_engine(chosen, tmp_path)()
    assert answered == assistant.Answered(answer)
    ((command, options),) = run.calls
    assert command[:3] == ["/usr/bin/gemini", "--prompt", "The rules.\n\nThe task."]
    variables = options["env"]
    # Your own home, with its login, and the settings that leave it only reading.
    assert variables["HOME"] == "/home/you"
    # Root's alone, as Gemini takes them only so, and gone when the run ends.
    folder = f"/etc/issue-assistant/{tmp_path.name}"
    assert variables["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] == f"{folder}/settings.json"
    settings = tmp_path / "settings.json"
    assert [command[0] for command in as_root] == ["install", "install", "rm"]
    assert as_root[1][-2:] == [str(settings), f"{folder}/settings.json"]
    assert as_root[2] == ["rm", "-rf", folder]
    assert variables["GEMINI_TELEMETRY_ENABLED"] == "false"
    run_of_task = assistant.task_run(chosen, "triage")
    cli = assistant.ENGINES["gemini"].cli
    assert json.loads(settings.read_text("utf-8")) == assistant.cli_settings(
        cli, run_of_task
    )
    assert options["cwd"] == root


def test_copilot_takes_no_settings_file(monkeypatch, tmp_path, config, context):
    monkeypatch.setattr(assistant.shutil, "which", lambda name: f"/usr/bin/{name}")
    answer = json.dumps(make_answer())
    event = {"type": "assistant.message", "data": {"content": answer}}
    run = FakeRun((0, json.dumps(event), ""))
    monkeypatch.setattr(assistant.subprocess, "run", run)
    chosen = dataclasses.replace(config, engine="copilot")
    assert assistant.local_engine(chosen, tmp_path)().text == answer
    ((_, options),) = run.calls
    assert "GEMINI_CLI_SYSTEM_SETTINGS_PATH" not in options["env"]
    assert not (tmp_path / "settings.json").exists()


def test_gemini_doesn_t_run_when_root_can_t_take_its_settings(
    monkeypatch, tmp_path, config, context, as_root
):
    """Gemini would run without them, with only a warning."""
    monkeypatch.setattr(assistant.shutil, "which", lambda name: f"/usr/bin/{name}")

    def refuse(command):
        as_root.append(command)
        return subprocess.CompletedProcess(
            command, 1, "", "sudo: a password is required"
        )

    monkeypatch.setattr(assistant, "as_root", refuse)
    run = FakeRun()
    monkeypatch.setattr(assistant.subprocess, "run", run)
    answer = assistant.local_engine(
        dataclasses.replace(config, engine="gemini"), tmp_path
    )
    with pytest.raises(assistant.AssistantError, match="on your computer, run sudo -v"):
        answer()
    assert run.calls == []
    assert as_root[-1] == ["rm", "-rf", f"/etc/issue-assistant/{tmp_path.name}"]


def test_gemini_without_root_is_refused(monkeypatch, tmp_path, config):
    monkeypatch.setattr(assistant.shutil, "which", lambda name: "C:/bin/gemini.exe")
    monkeypatch.delattr(assistant.os, "geteuid", raising=False)
    with pytest.raises(assistant.AssistantError) as refused:
        assistant.local_engine(dataclasses.replace(config, engine="gemini"), tmp_path)
    assert str(refused.value) == (
        "evaluate runs Gemini only on Linux, macOS or WSL: it takes the settings "
        "that keep it to reading only from a file of root's."
    )


@pytest.mark.parametrize("found", ["C:/npm/gemini.cmd", "C:/npm/gemini.BAT"])
def test_an_engine_that_windows_runs_through_cmd_is_refused(
    monkeypatch, tmp_path, config, found
):
    monkeypatch.setattr(assistant.shutil, "which", lambda name: found)
    monkeypatch.setattr(assistant.os, "name", "nt")
    chosen = dataclasses.replace(config, engine="gemini")
    with pytest.raises(assistant.AssistantError, match="cmd.exe, which cuts it"):
        assistant.local_engine(chosen, tmp_path)


def test_an_engine_that_windows_runs_as_a_program_is_run(monkeypatch, tmp_path, config):
    monkeypatch.setattr(assistant.shutil, "which", lambda name: "C:/bin/claude.exe")
    monkeypatch.setattr(assistant.os, "name", "nt")
    assert callable(assistant.local_engine(config, tmp_path))


def test_an_engine_that_evaluate_doesn_t_run_or_can_t_find_is_refused(
    monkeypatch, tmp_path, config
):
    with pytest.raises(
        assistant.AssistantError,
        match="evaluate runs the engines claude, gemini, copilot, not codex.",
    ):
        assistant.local_engine(dataclasses.replace(config, engine="codex"), tmp_path)
    monkeypatch.setattr(assistant.shutil, "which", lambda name: None)
    with pytest.raises(
        assistant.AssistantError,
        match="evaluate runs the command line of Claude, claude, which isn't on the "
        "PATH of this computer.",
    ):
        assistant.local_engine(config, tmp_path)


def test_the_engine_and_model_of_an_evaluation(config):
    mine = dataclasses.replace(config, model="claude-sonnet-5-5", effort="high")
    assert assistant.evaluated_config(mine, None, None) == mine
    assert assistant.evaluated_config(mine, "claude", None) == mine
    other = assistant.evaluated_config(mine, "gemini", None)
    assert (other.engine, other.model, other.effort) == ("gemini", "", "")
    model = assistant.evaluated_config(mine, None, "claude-haiku-5-5")
    assert (model.engine, model.model, model.effort) == (
        "claude",
        "claude-haiku-5-5",
        "high",
    )


# The command


def fake_claude(answers):
    """subprocess.run as Claude's command line: it answers the issue of issue.md in
    the context folder of its working folder."""

    def run(command, *, cwd, **options):
        text = (cwd / assistant.CONTEXT / "issue.md").read_text("utf-8")
        number = int(re.search(r"#(\d+)", text)[1])
        printed = claude_output(structured_output=answers[number], total_cost_usd=0.5)
        return subprocess.CompletedProcess(command, 0, printed, "")

    return run


def test_the_evaluate_command_writes_the_report_next_to_the_checkout(
    monkeypatch, tmp_path, root, config, github
):
    monkeypatch.setattr(assistant, "datetime", Frozen)
    monkeypatch.setattr(assistant.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        assistant.subprocess,
        "run",
        fake_claude(
            {
                22: make_answer(
                    duplicates=[{"number": 7, "confidence": "high", "reason": "Same."}]
                ),
                21: make_answer(kind="question", areas=[]),
            }
        ),
    )
    code, outputs, summary = run_main(
        monkeypatch, tmp_path, root, github, "evaluate", "--issues", "2"
    )
    assert (code, outputs) == (0, "")
    report = tmp_path / "project-accuracy.md"
    text = report.read_text("utf-8")
    assert text.startswith(
        "# How accurate the issue assistant is on owner/project\n\nMeasured on "
        "2026-10-07 with Claude, `claude-opus-5-5`, on the 2 newest of 6 closed "
        "issues read"
    )
    assert "| Sure duplicates | 100 % (1 of 1) | 100 % (1 of 1) | 1 |" in text
    assert "The analyses cost $1.00 at the prices of the API." in text
    assert summary == (
        "### Accuracy on owner/project\n\n"
        # The bug of #22 is the label of its form, which counts for neither.
        "- Type: precision 100 % (1 of 1), recall 100 % (1 of 1), over 1 issue\n"
        "- Areas: precision –, recall –, over 0 issues\n"
        "- Topics: precision –, recall –, over 0 issues\n"
        "- Duplicates: precision 100 % (1 of 1), recall 100 % (1 of 1), over 1 issue\n"
        "- Sure duplicates: precision 100 % (1 of 1), recall 100 % (1 of 1), over 1 "
        "issue\n"
        "- Asks for information: precision –, recall –, over 2 issues\n"
        "- Replayed 2 of the 6 closed issues read with Claude, claude-opus-5-5; 0 "
        "failed.\n"
        f"- The report is in {report.resolve()}.\n"
    )
    # Nothing was written to GitHub, and the context of the last replay is left.
    assert github.writes == []
    assert (root / assistant.CONTEXT / "issue.md").is_file()


def test_the_evaluate_command_takes_another_engine_model_and_report(
    monkeypatch, tmp_path, root, github, as_root
):
    monkeypatch.setattr(assistant, "datetime", Frozen)
    monkeypatch.setattr(assistant.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []
    answer = json.dumps(make_answer())

    def run(command, **options):
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"response": answer}), ""
        )

    monkeypatch.setattr(assistant.subprocess, "run", run)
    report = tmp_path / "reports" / "gemini.md"
    report.parent.mkdir()
    code, _, summary = run_main(
        monkeypatch,
        tmp_path,
        root,
        github,
        "evaluate",
        "--issues",
        "1",
        "--engine",
        "gemini",
        "--model",
        "gemini-3-pro",
        "--report",
        str(report),
    )
    assert code == 0
    assert calls[0][3:5] == ["--model", "gemini-3-pro"]
    assert "with Gemini, `gemini-3-pro`" in report.read_text("utf-8")
    assert f"- The report is in {report.resolve()}.\n" in summary


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        (
            ["--report", "accuracy.md"],
            "must be outside the checkout, where the engine would read it in the "
            "next evaluation.",
        ),
        (
            ["--report", "../missing/accuracy.md"],
            "needs a folder that exists and that you may write to.",
        ),
        (["--issues", "0"], "--issues must be a whole number from 1 to 200, not 0."),
        (
            ["--issues", "201"],
            "--issues must be a whole number from 1 to 200, not 201.",
        ),
    ],
)
def test_the_evaluate_command_refuses_what_would_mislead_it(
    monkeypatch, tmp_path, root, github, capsys, arguments, error
):
    monkeypatch.setattr(assistant.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.chdir(root)
    monkeypatch.setattr(assistant.subprocess, "run", FakeRun())
    code, _, summary = run_main(
        monkeypatch, tmp_path, root, github, "evaluate", *arguments
    )
    assert (code, summary) == (1, "")
    assert error in capsys.readouterr().out


def test_the_evaluate_command_needs_an_issue_that_people_decided(
    monkeypatch, tmp_path, root, config, capsys
):
    monkeypatch.setattr(assistant.shutil, "which", lambda name: f"/usr/bin/{name}")
    github = FakeGitHub(config, closed(3, 5, labels=()))
    code, _, _ = run_main(monkeypatch, tmp_path, root, github, "evaluate")
    assert code == 1
    assert capsys.readouterr().out == (
        "::error::None of the 1 closed issues read has a label that people chose or "
        "the original of a duplicate, without an analysis of the assistant.\n"
    )


def test_the_evaluate_command_writes_nothing_on_github(
    monkeypatch, tmp_path, root, config
):
    made = []

    def connect(repository, dry_run):
        made.append(dry_run)
        raise assistant.AssistantError("stop here")

    monkeypatch.setattr(assistant, "GitHub", connect)
    code, _, _ = run_main(monkeypatch, tmp_path, root, None, "evaluate")
    assert (code, made) == (1, [True])
