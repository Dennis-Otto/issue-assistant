"""A fixture: an issue or a discussion from a file, for a run of the action on a
repository of its own."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import AssistantError
from .values import did_you_mean, fits, moment


@dataclass(frozen=True)
class Fixture:
    """An issue and its comments from a file, as GitHub's REST API gives them, or a
    discussion with its comments and their replies, as GitHub's GraphQL API gives it.

    context and apply read it in place of an issue or a discussion on GitHub when
    the environment variable ISSUE_ASSISTANT_FIXTURE names the file, and write
    nothing. So the Canary runs the AI's whole path without a public issue, the
    Workflow test runs apply of the issues and of the discussions, and a prompt can
    be tried on an issue or a discussion that exists nowhere.
    """

    issue: dict[str, Any] | None
    comments: list[dict[str, Any]]
    discussion: dict[str, Any] | None = None

    @property
    def kind(self) -> str:
        return "issue" if self.discussion is None else "discussion"

    @property
    def number(self) -> int:
        number: int = (self.discussion or self.issue or {})["number"]
        return number


FIXTURE_VARIABLE = "ISSUE_ASSISTANT_FIXTURE"
FIXTURE_KEYS = ("issue", "comments", "discussion")
# The keys of a discussion that are true or false.
DISCUSSION_FLAGS = ("closed", "locked", "isAnswered")
MOMENT = "a moment, such as 2026-10-13T07:23:00Z"


def is_time(value: object) -> bool:
    """Whether a value is a moment as GitHub writes it, such as 2026-10-13T07:23:00Z."""
    if not isinstance(value, str):
        return False
    try:
        moment(value)
    except ValueError:
        return False
    return True


def fixture_problems(item: object, where: str, *, issue: bool) -> list[str]:
    """What keeps an issue or a comment of a fixture from being read as GitHub's:
    the keys that the assistant reads, in their types."""
    if not isinstance(item, dict):
        return [f"{where} must be an object"]
    problems = []
    if issue:
        problems += number_and_title_problems(item, where)
        if item.get("state") not in {"open", "closed"}:
            problems.append(f'{where}.state must be "open" or "closed"')
        labels = item.get("labels") or []
        if not isinstance(labels, list) or not all(
            isinstance(label, dict) and fits(label.get("name"), str) for label in labels
        ):
            problems.append(f"{where}.labels must be a list of objects with a name")
    if not fits(item.get("body") or "", str):
        problems.append(f"{where}.body must be a string")
    user = item.get("user")
    if not isinstance(user, dict) or not fits(user.get("login"), str):
        problems.append(f"{where}.user must be an object with a login")
    if not is_time(item.get("created_at")):
        problems.append(f"{where}.created_at must be {MOMENT}")
    return problems


def number_and_title_problems(item: dict[str, Any], where: str) -> list[str]:
    """What keeps the number and the title of an issue or a discussion of a fixture
    from being GitHub's."""
    problems = []
    number = item.get("number")
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        problems.append(f"{where}.number must be a whole number, 1 or more")
    if not fits(item.get("title"), str):
        problems.append(f"{where}.title must be a string")
    return problems


def post_problems(item: object, where: str) -> list[str]:
    """What keeps a discussion, a comment or a reply of a fixture from being read as
    GitHub's GraphQL API gives it: who wrote it, in which role, when, and its text."""
    if not isinstance(item, dict):
        return [f"{where} must be an object"]
    problems = []
    author = item.get("author")
    # GraphQL always sends the key, with null for a deleted account.
    if "author" not in item or (
        author is not None
        and not (isinstance(author, dict) and fits(author.get("login"), str))
    ):
        problems.append(f"{where}.author must be an object with a login, or null")
    if not fits(item.get("authorAssociation"), str):
        problems.append(f"{where}.authorAssociation must be a string")
    if not fits(item.get("body") or "", str):
        problems.append(f"{where}.body must be a string")
    if not is_time(item.get("createdAt")):
        problems.append(f"{where}.createdAt must be {MOMENT}")
    return problems


def nodes_of(item: dict[str, Any], key: str) -> list[Any] | None:
    """The nodes of a connection of GraphQL, such as the comments of a discussion;
    None when the key holds no object with a list of nodes."""
    connection = item.get(key)
    if not isinstance(connection, dict):
        return None
    nodes = connection.get("nodes")
    return nodes if isinstance(nodes, list) else None


def discussion_problems(item: object) -> list[str]:
    """What keeps the discussion of a fixture from being read as GitHub's GraphQL API
    gives it to GitHub.discussion: the discussion, its category, its comments and
    their replies."""
    where = "discussion"
    problems = post_problems(item, where)
    if not isinstance(item, dict):
        return problems
    if not fits(item.get("id"), str):
        problems.append(f"{where}.id must be a string, the ID of its node")
    problems += number_and_title_problems(item, where)
    problems += [
        f"{where}.{flag} must be true or false"
        for flag in DISCUSSION_FLAGS
        if not fits(item.get(flag), bool)
    ]
    category = item.get("category")
    if not (
        isinstance(category, dict)
        and fits(category.get("name"), str)
        and fits(category.get("isAnswerable"), bool)
    ):
        problems.append(
            f"{where}.category must be an object with a name and isAnswerable"
        )
    comments = nodes_of(item, "comments")
    if comments is None or not fits(item["comments"].get("totalCount"), int):
        problems.append(
            f"{where}.comments must be an object with totalCount and a list of nodes"
        )
        return problems
    for index, comment in enumerate(comments):
        here = f"{where}.comments.nodes[{index}]"
        problems += post_problems(comment, here)
        if not isinstance(comment, dict):
            continue
        replies = nodes_of(comment, "replies")
        if replies is None:
            problems.append(f"{here}.replies must be an object with a list of nodes")
            continue
        for position, reply in enumerate(replies):
            problems += post_problems(reply, f"{here}.replies.nodes[{position}]")
    return problems


def read_fixture(root: Path, name: str) -> Fixture:
    """The fixture file that ISSUE_ASSISTANT_FIXTURE names, relative to the checkout:
    a JSON object with the issue and the list of its comments, each as GitHub's REST
    API gives it, such as the issue that `gh api repos/OWNER/REPO/issues/12` prints,
    or with the discussion alone, which holds its comments and their replies, as the
    query of GitHub.discussion gives it."""
    try:
        data = json.loads((root / name).read_text(encoding="utf-8"))
    except OSError as error:
        raise AssistantError(
            f"The fixture {name} can't be read: {error.strerror or error}."
        ) from None
    except ValueError as error:
        raise AssistantError(f"The fixture {name} is no JSON: {error}.") from None
    if not isinstance(data, dict):
        raise AssistantError(
            f"The fixture {name} must be an object with an issue and its comments, "
            "or with a discussion."
        )
    problems = [
        did_you_mean(f"unknown key {key}", key, FIXTURE_KEYS)
        for key in data
        if key not in FIXTURE_KEYS
    ]
    if "discussion" in data:
        # One issue or one discussion, which holds its comments.
        problems += [
            f"{key} doesn't go with a discussion, which holds its comments"
            for key in ("issue", "comments")
            if key in data
        ]
        problems += discussion_problems(data["discussion"])
        if problems:
            raise AssistantError(
                f"The fixture {name} doesn't fit: {'; '.join(problems)}."
            )
        return Fixture(None, [], data["discussion"])
    problems += fixture_problems(data.get("issue"), "issue", issue=True)
    comments = data.get("comments", [])
    if isinstance(comments, list):
        for index, comment in enumerate(comments):
            problems += fixture_problems(comment, f"comments[{index}]", issue=False)
    else:
        problems.append("comments must be a list")
    if problems:
        raise AssistantError(f"The fixture {name} doesn't fit: {'; '.join(problems)}.")
    return Fixture(data["issue"], comments)
