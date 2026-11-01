"""Small helpers that know nothing of the assistant: the values of GitHub's JSON, dates
and workflow commands."""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import Any


def command(name: str, message: str) -> None:
    """Print a workflow command, such as an error or a notice, with its message.

    The runner reads every line of the log that starts with `::` as a command, such
    as `::add-mask::` or `::stop-commands::`. Escaped as GitHub's Actions toolkit
    escapes it, the message stays on its line, so that no text in it, such as the
    engine's, can start another command.
    """
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{name}::{escaped}")


def fits(value: object, kind: type) -> bool:
    """Whether a value of a settings file has the type of its key."""
    if kind is list:
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    # A number may be whole, such as 2 for 2.0.
    kinds = (int, float) if kind is float else kind
    # Python's True is an int, but TOML's true is no whole number.
    return isinstance(value, kinds) and (kind is bool or not isinstance(value, bool))


def did_you_mean(
    problem: str, name: str, known: Iterable[str], form: str = "{}"
) -> str:
    """A problem with a name that the assistant doesn't know, and the known name that
    it probably means, or else every known name, each written in `form`."""
    names = list(known)
    close = difflib.get_close_matches(name, names, n=1)
    if close:
        return f"{problem}; did you mean {form.format(close[0])}?"
    return f"{problem}; known: {', '.join(form.format(item) for item in names)}"


def login(item: dict[str, Any] | None) -> str:
    return str((item or {}).get("login") or "")


def moment(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def created(item: dict[str, Any]) -> datetime:
    return moment(item["created_at"])


def stamp(when: datetime) -> str:
    """The moment as GitHub writes it, in UTC to the second."""
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_of(when: datetime) -> date:
    return when.astimezone(UTC).date()


def over(start: datetime, period: timedelta, now: datetime) -> bool:
    """Whether the period after the start has ended by the day of `now`.

    The waiting for a reporter counts days of the calendar in UTC, as the reminder
    names the day the issue closes: the daily sweep starts a few minutes earlier or
    later each morning, and must keep that day all the same.
    """
    return day_of(now) >= day_of(start + period)


def days_between(start: datetime, end: datetime) -> int:
    return (day_of(end) - day_of(start)).days


def text(limit: int) -> dict[str, Any]:
    return {"type": "string", "maxLength": limit}


def fence(text: str) -> str:
    """The backticks of a code fence that nothing in the text can close: more than
    in any run of backticks in it."""
    longest = max((len(run) for run in re.findall("`+", text)), default=0)
    return "`" * max(3, longest + 1)
