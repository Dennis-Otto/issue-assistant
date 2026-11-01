"""A fixture: an issue from a file, for a run of the action on a repository of its own."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import AssistantError
from .values import did_you_mean, fits, moment


@dataclass(frozen=True)
class Fixture:
    """An issue and its comments from a file, as GitHub's REST API gives them.

    context and apply read it in place of an issue on GitHub when the environment
    variable ISSUE_ASSISTANT_FIXTURE names the file, and write nothing. So the Canary
    runs the AI's whole path without a public issue, and a prompt can be tried on an
    issue that exists nowhere.
    """

    issue: dict[str, Any]
    comments: list[dict[str, Any]]

    @property
    def number(self) -> int:
        number: int = self.issue["number"]
        return number


FIXTURE_VARIABLE = "ISSUE_ASSISTANT_FIXTURE"
FIXTURE_KEYS = ("issue", "comments")


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
        number = item.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            problems.append(f"{where}.number must be a whole number, 1 or more")
        if not fits(item.get("title"), str):
            problems.append(f"{where}.title must be a string")
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
        problems.append(
            f"{where}.created_at must be a moment, such as 2026-10-13T07:23:00Z"
        )
    return problems


def read_fixture(root: Path, name: str) -> Fixture:
    """The fixture file that ISSUE_ASSISTANT_FIXTURE names, relative to the checkout:
    a JSON object with the issue and the list of its comments, each as GitHub's REST
    API gives it, such as the issue that `gh api repos/OWNER/REPO/issues/12` prints."""
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
            f"The fixture {name} must be an object with the issue and its comments."
        )
    problems = [
        did_you_mean(f"unknown key {key}", key, FIXTURE_KEYS)
        for key in data
        if key not in FIXTURE_KEYS
    ]
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
