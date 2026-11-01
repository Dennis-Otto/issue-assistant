"""Every GraphQL document of the script, checked against GitHub's own schema.

The fake of the API in support.py answers GraphQL by the words of a query, so a
misspelt field, or one that GitHub renames or removes, passes every other test; in a
repository that uses the action, the request then fails, and a fallback hides it
behind a notice: labels lose their rationale, a duplicate its link. Here the real
request code runs down to `gh`, and what it sends there is checked against GitHub's
public schema, kept in tests/github/schema.docs.graphql: every field and argument
exists, and none that GitHub has marked for removal, every variable fits its type,
and the answer, worked out from the schema, holds only the fields that the document
asks for, so that the method reads no other.

The Action test workflow checks the documents every week against the schema of that
day, which GRAPHQL_SCHEMA names, and brings a new copy that still passes in a pull
request of its own. Refresh the copy here with

    python3 scripts/github_schemas.py download tests/github
"""

import ast
import json
import os
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from graphql import (
    GraphQLSchema,
    build_schema,
    execute_sync,
    parse,
    print_ast,
    validate,
)
from graphql.validation import NoDeprecatedCustomRule, specified_rules

import issue_assistant as assistant
from support import REF, REPOSITORY

SCHEMA = Path(
    os.environ.get("GRAPHQL_SCHEMA")
    or Path(__file__).parent / "github" / "schema.docs.graphql"
)
ISSUE = {"number": 12, "node_id": "I_kwDOMpLSs86Xc1Ab"}
ORIGINAL = {"number": 7, "node_id": "I_kwDOMpLSs86Xa9Zz"}
REASONS = ("COMPLETED", "NOT_PLANNED", "DUPLICATE")
# GitHub deprecates a field, an argument or a value months before it removes it.
RULES = (*specified_rules, NoDeprecatedCustomRule)


@pytest.fixture(scope="module")
def schema() -> GraphQLSchema:
    # The definitions themselves are checked: every type that a field names exists.
    # GitHub's schema breaks a newer rule of the GraphQL specification, though: some
    # fields are deprecated in a type and not in its interface. GitHub doesn't hold
    # itself to that rule, and graphql-core would refuse the whole schema for it.
    return build_schema(SCHEMA.read_text(encoding="utf-8"), assume_valid=True)


class GitHubOfTheSchema:
    """`gh api` that answers GraphQL as GitHub would, from the data that it holds.

    A document that the schema refuses or that uses what GitHub deprecated, or
    variables that don't fit it, fail the test at once: an AssertionError passes every fallback of the script, which
    would turn the error of GitHub into a notice.
    """

    def __init__(
        self, schema: GraphQLSchema, holds: dict[str, Any], rest: dict[str, Any]
    ) -> None:
        self.schema = schema
        self.holds = holds
        self.rest = rest
        self.requests: list[dict[str, Any]] = []

    def run(
        self, command: list[str], *, input: str | None = None, **options: Any
    ) -> subprocess.CompletedProcess[str]:
        if command[:3] != ["gh", "api", "graphql"]:
            assert command[2] in self.rest, f"unexpected call {command}"
            return answered(command, self.rest[command[2]])
        assert command[3:5] == ["--input", "-"] and input is not None
        request = json.loads(input)
        self.requests.append(request)
        document = parse(request["query"])
        problems = validate(self.schema, document, RULES)
        assert not problems, "\n".join(problem.message for problem in problems)
        result = execute_sync(
            self.schema,
            document,
            root_value=self.holds,
            variable_values=request["variables"],
        )
        assert not result.errors, "\n".join(error.message for error in result.errors)
        return answered(command, {"data": result.data})


def answered(command: list[str], body: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, 0, json.dumps(body), "")


@dataclass(frozen=True)
class Request:
    """A method of the client, the data that GitHub holds for it, what it returns."""

    send: Callable[[assistant.GitHub], object]
    holds: dict[str, Any]
    returns: object = None
    # Answers to the REST calls that the method makes besides, by their path.
    rest: dict[str, Any] = field(default_factory=dict)


def pull(
    number: int,
    *,
    merged: bool = True,
    base: str = "main",
    repository: str = REPOSITORY,
) -> dict[str, Any]:
    return {
        "number": number,
        "merged": merged,
        "mergedAt": "2026-10-06T12:00:00Z" if merged else None,
        "baseRefName": base,
        "mergeCommit": {"oid": REF} if merged else None,
        "repository": {"nameWithOwner": repository},
    }


FIXES = {
    "nodes": [
        {"number": 12, "state": "OPEN", "repository": {"nameWithOwner": REPOSITORY}}
    ]
}
DISCUSSION = {
    "number": 3,
    "title": "How do I hide the live card?",
    "closed": False,
    "isAnswered": True,
    "body": "The card shows in every view.",
    "createdAt": "2026-09-01T12:00:00Z",
    "category": {"name": "Q&A"},
    "answer": {
        "body": "Turn it off in the options of the card.",
        "createdAt": "2026-09-02T12:00:00Z",
    },
}
# A discussion with its comments and their replies, as GitHub holds it: a deleted
# account is no author, and Actor is an interface, whose type GitHub answers.
ASKER = {"__typename": "User", "login": "asker"}
THREAD = {
    "id": "D_kwDOMpLSs84AdQ1x",
    "number": 5,
    "title": "How do I hide the live card?",
    "body": "It shows in every view.",
    "createdAt": "2026-10-07T11:00:00Z",
    "closed": False,
    "locked": False,
    "isAnswered": False,
    "authorAssociation": "NONE",
    "author": ASKER,
    "category": {"name": "Q&A", "isAnswerable": True},
    "comments": {
        "totalCount": 1,
        "nodes": [
            {
                "body": "Me too.",
                "createdAt": "2026-10-07T11:30:00Z",
                "authorAssociation": "NONE",
                "author": None,
                "replies": {
                    "nodes": [
                        {
                            "body": "Turn it off in the options.",
                            "createdAt": "2026-10-07T11:40:00Z",
                            "authorAssociation": "OWNER",
                            "author": {"__typename": "User", "login": "owner"},
                        }
                    ]
                },
            }
        ],
    },
}
OF_THE_DAY = [
    {
        "number": 9,
        "createdAt": "2026-10-07T11:00:00Z",
        "authorAssociation": "NONE",
        "author": {"__typename": "Bot", "login": "github-actions"},
        "category": {"name": "Q&A"},
    },
    {
        "number": 6,
        "createdAt": "2026-10-05T11:00:00Z",
        "authorAssociation": "NONE",
        "author": ASKER,
        "category": {"name": "General"},
    },
]
CLOSED = {"closeIssue": {"issue": {"number": 12}}}
PAGE = {"hasNextPage": False, "endCursor": "Y3Vyc29yOjE="}


def issue_node(
    number: int,
    closer: dict[str, Any] | None = None,
    fixes: list[dict[str, Any]] | None = None,
    **changes: Any,
) -> dict[str, Any]:
    """An issue as GitHub holds it, closed last by `closer`, if it was closed, and
    named by the pull requests `fixes`."""
    closings = [{"__typename": "ClosedEvent", "closer": closer}] if closer else []
    return {
        "number": number,
        "title": "The live card stays empty",
        "body": "After a throw.",
        "state": "CLOSED" if closer else "OPEN",
        "stateReason": "COMPLETED" if closer else None,
        "createdAt": "2026-09-01T12:00:00Z",
        "updatedAt": "2026-10-01T12:00:00Z",
        "closedAt": "2026-09-03T12:00:00Z" if closer else None,
        "author": {"__typename": "User", "login": "reporter"},
        "labels": {"nodes": [{"name": "bug"}]},
        "comments": {"totalCount": 2},
        "closedByPullRequestsReferences": {"nodes": fixes or []},
        "timelineItems": {"nodes": closings},
        **changes,
    }


def issue_as_rest(number: int, **changes: Any) -> dict[str, Any]:
    """The issue of issue_node in the form of the REST API, which the method returns;
    without `updatedAt`, which the document doesn't ask for."""
    return {
        "number": number,
        "title": "The live card stays empty",
        "body": "After a throw.",
        "user": {"login": "reporter", "type": "User"},
        "state": "open",
        "state_reason": None,
        "labels": [{"name": "bug"}],
        "created_at": "2026-09-01T12:00:00Z",
        "closed_at": None,
        "comments": 2,
        "closed_by": None,
        **changes,
    }


OPENED = "2026-08-01T12:00:00Z"


def reference(number: int, repository: str = REPOSITORY) -> dict[str, Any]:
    """The original of a duplicate, as the closed issues of evaluate name it."""
    return {
        "number": number,
        "createdAt": OPENED,
        "repository": {"nameWithOwner": repository},
    }


def closed_node(number: int, **changes: Any) -> dict[str, Any]:
    """A closed issue as GitHub holds it for evaluate."""
    return {
        "number": number,
        "title": "The live card stays empty",
        "body": "After a throw.",
        "createdAt": "2026-09-01T12:00:00Z",
        "closedAt": "2026-09-03T12:00:00Z",
        "stateReason": "DUPLICATE",
        "authorAssociation": "NONE",
        "author": {"__typename": "User", "login": "reporter"},
        "labels": {"nodes": [{"name": "bug"}]},
        "duplicateOf": None,
        "timelineItems": {"nodes": []},
        **changes,
    }


def closed_as_rest(number: int, **changes: Any) -> dict[str, Any]:
    """The closed issue of closed_node in the form that closed_issues returns."""
    return {
        "number": number,
        "title": "The live card stays empty",
        "body": "After a throw.",
        "user": {"login": "reporter", "type": "User"},
        "author_association": "NONE",
        "state": "closed",
        "state_reason": "duplicate",
        "labels": [{"name": "bug"}],
        "created_at": "2026-09-01T12:00:00Z",
        "closed_at": "2026-09-03T12:00:00Z",
        "duplicate_of": None,
        **changes,
    }


CLOSED_ISSUE = {
    "state": "closed",
    "state_reason": "completed",
    "closed_at": "2026-09-03T12:00:00Z",
}

REQUESTS = {
    "merged pull requests": Request(
        lambda github: github.merged_pulls(datetime(2026, 9, 23, 12, tzinfo=UTC)),
        {
            "repository": {
                "defaultBranchRef": {"name": "main"},
                "pullRequests": {
                    "pageInfo": {"hasNextPage": False, "endCursor": "Y3Vyc29yOjE="},
                    "nodes": [
                        pull(31)
                        | {
                            "updatedAt": "2026-10-06T13:00:00Z",
                            "closingIssuesReferences": FIXES,
                        }
                    ],
                },
            }
        },
        # Without `merged` and `repository`, which GitHub holds but the document
        # doesn't ask for.
        (
            "main",
            [
                {
                    "number": 31,
                    "mergedAt": "2026-10-06T12:00:00Z",
                    "updatedAt": "2026-10-06T13:00:00Z",
                    "baseRefName": "main",
                    "mergeCommit": {"oid": REF},
                    "closingIssuesReferences": FIXES,
                }
            ],
        ),
    ),
    "the pull requests that fix an issue": Request(
        lambda github: github.fixing_pulls(12),
        {
            "repository": {
                "defaultBranchRef": {"name": "main"},
                "issue": {
                    "closedByPullRequestsReferences": {
                        "nodes": [
                            pull(31),
                            pull(32, merged=False),
                            pull(33, base="release"),
                            pull(34, repository="someone/else"),
                        ]
                    }
                },
            }
        },
        [pull(31)],
    ),
    "discussions": Request(
        lambda github: github.discussions(1000),
        {"repository": {"discussions": {"pageInfo": PAGE, "nodes": [DISCUSSION]}}},
        [DISCUSSION],
    ),
    # GitHub answers the document with the open issues here, and with the closed
    # ones that changed last when asked for any.
    "the issues of the context": Request(
        lambda github: github.recent_issues(0),
        {
            "repository": {
                "issues": {
                    "pageInfo": PAGE,
                    "nodes": [
                        issue_node(12),
                        issue_node(7, {"__typename": "PullRequest", "number": 31}),
                        issue_node(5, {"__typename": "Commit", "oid": REF}),
                        issue_node(3, {"__typename": "ProjectV2", "number": 2}),
                        # Closed by the release after the pull requests that fix it
                        # merged: the last merged one of this repository fixed it.
                        issue_node(
                            4,
                            None,
                            [
                                pull(33),
                                pull(35),
                                pull(36, merged=False),
                                pull(37, repository="someone/else"),
                            ],
                            state="CLOSED",
                            stateReason="COMPLETED",
                            closedAt="2026-09-03T12:00:00Z",
                            timelineItems={
                                "nodes": [{"__typename": "ClosedEvent", "closer": None}]
                            },
                        ),
                        issue_node(2, labels=None),
                    ],
                }
            }
        },
        [
            issue_as_rest(12),
            issue_as_rest(7, **CLOSED_ISSUE, closed_by=31),
            issue_as_rest(5, **CLOSED_ISSUE),
            issue_as_rest(3, **CLOSED_ISSUE),
            issue_as_rest(4, **CLOSED_ISSUE, closed_by=35),
            issue_as_rest(2, labels=[]),
        ],
    ),
    # A duplicate, one marked as a duplicate, one of another repository's, one
    # unmarked again and one of a deleted account, opened by a bot.
    "the closed issues of an evaluation": Request(
        lambda github: list(github.closed_issues()),
        {
            "repository": {
                "issues": {
                    "pageInfo": PAGE,
                    "nodes": [
                        closed_node(12, duplicateOf=reference(7)),
                        closed_node(
                            11,
                            timelineItems={
                                "nodes": [
                                    {
                                        "__typename": "MarkedAsDuplicateEvent",
                                        "canonical": {"__typename": "Issue"}
                                        | reference(5),
                                    }
                                ]
                            },
                        ),
                        closed_node(10, duplicateOf=reference(3, "someone/else")),
                        closed_node(
                            9,
                            timelineItems={
                                "nodes": [{"__typename": "UnmarkedAsDuplicateEvent"}]
                            },
                        ),
                        closed_node(
                            8,
                            author={"__typename": "Bot", "login": "renovate"},
                            labels=None,
                            stateReason=None,
                        ),
                        closed_node(6, author=None),
                    ],
                }
            }
        },
        [
            closed_as_rest(12, duplicate_of={"number": 7, "created_at": OPENED}),
            closed_as_rest(11, duplicate_of={"number": 5, "created_at": OPENED}),
            closed_as_rest(10),
            closed_as_rest(9),
            closed_as_rest(
                8,
                user={"login": "renovate", "type": "Bot"},
                labels=[],
                state_reason=None,
            ),
            closed_as_rest(6, user={"login": "ghost", "type": "User"}),
        ],
    ),
    "a discussion with its comments": Request(
        lambda github: github.discussion(5),
        {"repository": {"discussion": THREAD}},
        {
            "node_id": "D_kwDOMpLSs84AdQ1x",
            "number": 5,
            "title": "How do I hide the live card?",
            "user": {"login": "asker", "type": "User"},
            "author_association": "NONE",
            "created_at": "2026-10-07T11:00:00Z",
            "body": "It shows in every view.",
            "state": "open",
            "locked": False,
            "answered": False,
            "category": {"name": "Q&A", "is_answerable": True},
            "comment_count": 1,
            "comments": [
                {
                    "user": {"login": "ghost", "type": "User"},
                    "author_association": "NONE",
                    "created_at": "2026-10-07T11:30:00Z",
                    "body": "Me too.",
                    "replies": [
                        {
                            "user": {"login": "owner", "type": "User"},
                            "author_association": "OWNER",
                            "created_at": "2026-10-07T11:40:00Z",
                            "body": "Turn it off in the options.",
                        }
                    ],
                }
            ],
        },
    ),
    "no discussion by that number": Request(
        lambda github: github.discussion(4),
        {"repository": {"discussion": None}},
        None,
    ),
    # The newest first; the second is older than the day and ends the list.
    "the discussions opened since a moment": Request(
        lambda github: github.discussions_since(datetime(2026, 10, 6, 12, tzinfo=UTC)),
        {"repository": {"discussions": {"pageInfo": PAGE, "nodes": OF_THE_DAY}}},
        [
            {
                "number": 9,
                "category": {"name": "Q&A"},
                "user": {"login": "github-actions[bot]", "type": "Bot"},
                "author_association": "NONE",
                "created_at": "2026-10-07T11:00:00Z",
                "body": None,
            }
        ],
    ),
    "a comment on a discussion": Request(
        lambda github: github.answer_discussion(
            {"number": 5, "node_id": "D_kwDOMpLSs84AdQ1x"}, "A first answer."
        ),
        {"addDiscussionComment": {"comment": {"id": "DC_kwDOMpLSs84Aq9Zy"}}},
    ),
    "a discussion that exists": Request(
        lambda github: github.discussion_exists(3),
        {"repository": {"discussion": {"number": 3}}},
        True,
    ),
    "a discussion that doesn't": Request(
        lambda github: github.discussion_exists(4),
        {"repository": {"discussion": None}},
        False,
    ),
    "the last edit of an issue": Request(
        lambda github: github.last_edit(12),
        {
            "repository": {
                "issue": {
                    "lastEditedAt": "2026-10-06T12:00:00Z",
                    # Actor is an interface; the type is what GitHub answers.
                    "editor": {"__typename": "User", "login": "reporter"},
                }
            }
        },
        ("reporter", datetime(2026, 10, 6, 12, tzinfo=UTC)),
    ),
    "an issue that nobody edited": Request(
        lambda github: github.last_edit(12),
        {"repository": {"issue": {"lastEditedAt": None, "editor": None}}},
        None,
    ),
    "labels with their rationale": Request(
        lambda github: github.add_explained_labels(
            ISSUE, ["bug", "area: cards"], "The card stays empty. " * 20
        ),
        {"addLabelsToLabelable": {"clientMutationId": None}},
        rest={
            f"repos/{REPOSITORY}/labels?per_page=100": [
                [
                    {"name": "bug", "node_id": "LA_kwDOMpLSs88AAAAB"},
                    {"name": "area: cards", "node_id": "LA_kwDOMpLSs88AAAAC"},
                ]
            ]
        },
    ),
    **{
        f"closing as {reason.lower().replace('_', ' ')}": Request(
            lambda github, reason=reason: github.close(
                ISSUE,
                reason,
                "Closed by the issue assistant, after its rules. " * 6,
                duplicate_of=ORIGINAL if reason == "DUPLICATE" else None,
            ),
            CLOSED,
        )
        for reason in REASONS
    },
    "the issue types of an organization's repository": Request(
        lambda github: github.issue_types(),
        {
            "repository": {
                "issueTypes": {
                    "nodes": [
                        {"name": "Bug", "isEnabled": True},
                        {"name": "Task", "isEnabled": False},
                    ]
                }
            }
        },
        ["Bug"],
    ),
    "the issue types of a person's repository": Request(
        lambda github: github.issue_types(),
        {"repository": {"issueTypes": None}},
        None,
    ),
    "an issue type that exists": Request(
        lambda github: github.issue_type_id("Bug"),
        {"repository": {"issueType": {"id": "IT_kwDOA48Fh84AtH84", "isEnabled": True}}},
        "IT_kwDOA48Fh84AtH84",
    ),
    "an issue type that is turned off": Request(
        lambda github: github.issue_type_id("Bug"),
        {
            "repository": {
                "issueType": {"id": "IT_kwDOA48Fh84AtH84", "isEnabled": False}
            }
        },
        None,
    ),
    "an issue type that doesn't exist": Request(
        lambda github: github.issue_type_id("Bug"),
        {"repository": {"issueType": None}},
        None,
    ),
    "setting the issue type": Request(
        lambda github: github.set_issue_type(ISSUE, "Bug", "IT_kwDOA48Fh84AtH84"),
        {"updateIssueIssueType": {"clientMutationId": None}},
    ),
    "suggesting a duplicate": Request(
        lambda github: github.suggest_duplicate(
            ISSUE, ORIGINAL, "Both describe the empty live card."
        ),
        CLOSED,
    ),
}


def send(
    monkeypatch: pytest.MonkeyPatch, schema: GraphQLSchema, request: Request
) -> tuple[object, list[dict[str, Any]]]:
    """What the method returns, and the GraphQL requests it sent to `gh`."""
    github = GitHubOfTheSchema(schema, request.holds, request.rest)
    monkeypatch.setattr(assistant.subprocess, "run", github.run)
    returned = request.send(assistant.GitHub(REPOSITORY))
    return returned, github.requests


def normal(document: str) -> str:
    return print_ast(parse(document))


def calls(source: str, name: str) -> Iterator[ast.Call]:
    """The calls of a method by that name in the source."""
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == name
        ):
            yield node


def calls_in_the_script(name: str) -> Iterator[ast.Call]:
    """The calls in the script as it is; a test that reads them is mutation_unsafe,
    because mutmut's copy holds every mutant beside its function."""
    return calls(Path(assistant.__file__).read_text(encoding="utf-8"), name)


@pytest.mark.parametrize("name", REQUESTS)
def test_github_s_schema_takes_every_request(monkeypatch, schema, name):
    request = REQUESTS[name]
    returned, sent = send(monkeypatch, schema, request)
    assert len(sent) == 1
    assert returned == request.returns


def test_labels_carry_github_s_rationale_of_at_most_280_characters(monkeypatch, schema):
    _, (sent,) = send(monkeypatch, schema, REQUESTS["labels with their rationale"])
    assert sent["variables"]["issue"] == ISSUE["node_id"]
    assert [label["labelId"] for label in sent["variables"]["labels"]] == [
        "LA_kwDOMpLSs88AAAAB",
        "LA_kwDOMpLSs88AAAAC",
    ]
    assert {len(label["rationale"]) for label in sent["variables"]["labels"]} == {280}


def test_the_issue_type_is_looked_up_by_its_name_and_set_by_its_id(monkeypatch, schema):
    owner, name = REPOSITORY.split("/")
    _, (sent,) = send(monkeypatch, schema, REQUESTS["an issue type that exists"])
    assert sent["variables"] == {"owner": owner, "name": name, "type": "Bug"}
    _, (sent,) = send(monkeypatch, schema, REQUESTS["setting the issue type"])
    assert sent["variables"] == {
        "issue": ISSUE["node_id"],
        "type": "IT_kwDOA48Fh84AtH84",
    }


def test_a_duplicate_is_closed_with_its_original_and_others_without(
    monkeypatch, schema
):
    for reason in REASONS:
        name = f"closing as {reason.lower().replace('_', ' ')}"
        _, (sent,) = send(monkeypatch, schema, REQUESTS[name])
        assert sent["variables"]["reason"] == reason
        assert len(sent["variables"]["rationale"]) == 280
        assert sent["variables"]["duplicate"] == (
            ORIGINAL["node_id"] if reason == "DUPLICATE" else None
        )


def test_the_answer_to_a_discussion_is_one_comment_on_it(monkeypatch, schema):
    """addDiscussionComment with the node of the discussion and the text alone: no
    reply to another comment, and nothing that marks it as the answer."""
    _, (sent,) = send(monkeypatch, schema, REQUESTS["a comment on a discussion"])
    assert sent["variables"] == {
        "discussion": "D_kwDOMpLSs84AdQ1x",
        "body": "A first answer.",
    }


@pytest.mark.mutation_unsafe
def test_the_script_never_marks_a_comment_as_the_answer():
    source = Path(assistant.__file__).read_text(encoding="utf-8")
    assert "markDiscussionCommentAsAnswer" not in source


@pytest.mark.mutation_unsafe
def test_every_graphql_document_of_the_script_is_checked(monkeypatch, schema):
    """A new document needs a request above, or this test fails."""
    nodes = list(calls_in_the_script("graphql"))
    # Literal documents only, so that this test sees each one.
    assert all(isinstance(node.args[0], ast.Constant) for node in nodes)
    documents = {normal(node.args[0].value) for node in nodes}
    checked = {
        normal(sent["query"])
        for request in REQUESTS.values()
        for sent in send(monkeypatch, schema, request)[1]
    }
    assert len(documents) == 16
    assert documents == checked


@pytest.mark.mutation_unsafe
def test_every_reason_for_closing_is_checked():
    reasons = {
        node.args[1].value
        for node in calls_in_the_script("close")
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
    }
    assert reasons == set(REASONS)


def test_a_field_that_github_does_not_know_fails(monkeypatch, schema):
    """The check catches a misspelt field, as a renamed one in GitHub's schema."""
    request = REQUESTS["merged pull requests"]
    _, (sent,) = send(monkeypatch, schema, request)
    broken = sent["query"].replace("mergeCommit { oid }", "mergeCommit { sha }")
    github = GitHubOfTheSchema(schema, request.holds, {})
    monkeypatch.setattr(assistant.subprocess, "run", github.run)
    with pytest.raises(
        AssertionError, match="Cannot query field 'sha' on type 'Commit'"
    ):
        assistant.GitHub(REPOSITORY).graphql(broken, sent["variables"])


def test_a_field_that_github_will_remove_fails(monkeypatch, schema):
    """GitHub announces a removal with the deprecation, months ahead of it."""
    request = REQUESTS["merged pull requests"]
    _, (sent,) = send(monkeypatch, schema, request)
    deprecated = sent["query"].replace(
        "mergeCommit { oid }", "mergeCommit { oid pushedDate }"
    )
    github = GitHubOfTheSchema(schema, request.holds, {})
    monkeypatch.setattr(assistant.subprocess, "run", github.run)
    with pytest.raises(
        AssertionError, match="The field Commit.pushedDate is deprecated"
    ):
        assistant.GitHub(REPOSITORY).graphql(deprecated, sent["variables"])


def test_a_variable_that_does_not_fit_its_type_fails(monkeypatch, schema):
    """The check catches a value that GitHub refuses, before a fallback hides it."""
    github = GitHubOfTheSchema(schema, CLOSED, {})
    monkeypatch.setattr(assistant.subprocess, "run", github.run)
    with pytest.raises(AssertionError, match="'CLOSED' does not exist"):
        assistant.GitHub(REPOSITORY).close(ISSUE, "CLOSED", "Done.")


def test_an_answer_without_a_field_that_github_promises_fails(monkeypatch, schema):
    """The data of a request must be whole, as GitHub's answer always is."""
    github = GitHubOfTheSchema(
        schema, {"repository": {"discussion": {"title": "No number"}}}, {}
    )
    monkeypatch.setattr(assistant.subprocess, "run", github.run)
    with pytest.raises(AssertionError, match="non-nullable field Discussion.number"):
        assistant.GitHub(REPOSITORY).discussion_exists(3)


def test_any_other_call_fails(monkeypatch, schema):
    github = GitHubOfTheSchema(schema, CLOSED, {})
    monkeypatch.setattr(assistant.subprocess, "run", github.run)
    with pytest.raises(AssertionError, match="unexpected call"):
        assistant.GitHub(REPOSITORY).issue(12)
