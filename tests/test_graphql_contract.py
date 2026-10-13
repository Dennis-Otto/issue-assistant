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
day, which GRAPHQL_SCHEMA names. Refresh the copy here with

    curl -fsSL -o tests/github/schema.docs.graphql \\
      https://docs.github.com/public/fpt/schema.docs.graphql
"""

import ast
import json
import os
import re
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
# The Mutation workflow runs the tests on a copy of the script in which every
# function has mutants beside it: xǁGitHubǁclose__mutmut_1, _2, … and the original,
# xǁGitHubǁclose__mutmut_orig.
MUTANT = re.compile(r"__mutmut_\d+$")


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
        assert command[3:] == ["--input", "-"] and input is not None
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


FIXES = {"nodes": [{"number": 12, "repository": {"nameWithOwner": REPOSITORY}}]}
DISCUSSION = {
    "number": 3,
    "title": "How do I hide the live card?",
    "closed": False,
    "isAnswered": True,
    "body": "The card shows in every view.",
    "category": {"name": "Q&A"},
    "answer": {"body": "Turn it off in the options of the card."},
}
CLOSED = {"closeIssue": {"issue": {"number": 12}}}

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
        lambda github: github.discussions(),
        {"repository": {"discussions": {"nodes": [DISCUSSION]}}},
        [DISCUSSION],
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
    """The calls of a method by that name in the source, without those of mutants."""
    nodes: list[ast.AST] = [ast.parse(source)]
    while nodes:
        node = nodes.pop()
        if isinstance(node, ast.FunctionDef) and MUTANT.search(node.name):
            continue
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == name
        ):
            yield node
        nodes.extend(ast.iter_child_nodes(node))


def calls_in_the_script(name: str) -> Iterator[ast.Call]:
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
    assert len(documents) == 7
    assert documents == checked


def test_every_reason_for_closing_is_checked():
    reasons = {
        node.args[1].value
        for node in calls_in_the_script("close")
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
    }
    assert reasons == set(REASONS)


def test_the_mutants_of_the_mutation_workflow_are_not_the_script():
    """mutmut keeps each original beside its mutants, which break what they like."""
    source = """
def x_close__mutmut_orig(self, issue):
    self.graphql("query { viewer { login } }", {})
    github.close(issue, "COMPLETED", "Done.")
def x_close__mutmut_1(self, issue):
    self.graphql(None, {})
    github.close(issue, "XXCOMPLETEDXX", "Done.")
def x_close__mutmut_2(self, issue):
    self.graphql({})
    github.close(issue, None, "Done.")
"""
    assert [ast.literal_eval(node.args[0]) for node in calls(source, "graphql")] == [
        "query { viewer { login } }"
    ]
    assert [ast.literal_eval(node.args[1]) for node in calls(source, "close")] == [
        "COMPLETED"
    ]


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
