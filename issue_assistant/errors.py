"""The errors of the assistant, and what a failed call of gh means."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable


class AssistantError(Exception):
    """The assistant refuses to act, for example on an answer that fails the checks."""


class SettingsError(AssistantError):
    """Settings that the assistant can't use, with each of their problems."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


# gh writes `gh: <message> (HTTP <status>)` for a failed REST call, and `gh: HTTP
# <status>` when GitHub answered without JSON, such as in an incident.
HTTP_STATUS = re.compile(r"\bHTTP (\d{3})\b")
# The words of gh when the connection to GitHub broke off, for a moment.
NETWORK_FAILURE = re.compile(
    r"connection reset|i/o timeout|TLS handshake timeout|unexpected EOF",
    re.IGNORECASE,
)
# The waits before a read runs again after a server error, one for each try.
SERVER_ERROR_WAITS = (2, 4, 8)
# GitHub asks to wait a minute after a secondary rate limit when the response has
# no Retry-After header, and gh never shows its headers.
RATE_LIMIT_WAIT = 60


class GitHubError(Exception):
    """A gh call failed, with the HTTP status of GitHub, the types of its GraphQL
    errors and the codes of the fields that it refused in a REST call.

    Only a thing that doesn't exist is an answer about the thing. A rate limit or a
    server error says nothing about it: read as "missing", it would change what the
    assistant posts, such as an analysis without the duplicate it found.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        types: Iterable[str] = (),
        codes: Iterable[str] = (),
    ) -> None:
        super().__init__(message)
        self.status = status
        self.types = frozenset(types)
        self.codes = frozenset(codes)

    @classmethod
    def of(cls, args: list[str], stdout: str, stderr: str) -> GitHubError:
        """The error of a failed gh call, from its message and GitHub's JSON."""
        statuses = HTTP_STATUS.findall(stderr or "")
        try:
            body = json.loads(stdout)
        except ValueError:
            body = None
        # A paginated call answers with a list of pages.
        pages = body if isinstance(body, list) else [body]
        errors = [
            error
            for page in pages
            if isinstance(page, dict)
            for error in page.get("errors") or []
            if isinstance(error, dict)
        ]

        def named(key: str) -> set[str]:
            return {error[key] for error in errors if isinstance(error.get(key), str)}

        return cls(
            f"gh {args[1] if len(args) > 1 else ''}: {(stderr or stdout).strip()}",
            status=int(statuses[-1]) if statuses else None,
            # GraphQL names the kind of an error its type, REST its code.
            types=named("type"),
            codes=named("code"),
        )

    def missing(self) -> bool:
        """Whether GitHub said that the thing doesn't exist, or no longer does."""
        return self.status in {404, 410} or "NOT_FOUND" in self.types

    def already_exists(self) -> bool:
        """Whether GitHub refused to create the thing because it has one of that
        name, such as a label that another workflow created a moment before."""
        return "already_exists" in self.codes

    def rate_limited(self) -> bool:
        """Whether GitHub refused the call for a minute: a secondary rate limit.

        GitHub refuses it before it does anything, so a write can run again too. The
        primary rate limit is no such case: its hour ends too late for a run to wait.
        """
        text = str(self).casefold()
        return (
            "secondary rate limit" in text
            or "abuse detection" in text
            or (self.status == 429 and "api rate limit exceeded" not in text)
        )

    def transient(self) -> bool:
        """Whether GitHub failed for a moment: a server error or a broken connection.

        Whether a write happened is open then, so only a read runs again.
        """
        if self.status is not None:
            return self.status >= 500
        return bool(NETWORK_FAILURE.search(str(self)))

    def wait(self, tries: int, read: bool) -> int | None:
        """The seconds to wait before the call runs again; None if it shouldn't."""
        if tries > len(SERVER_ERROR_WAITS):
            return None
        if self.rate_limited():
            return RATE_LIMIT_WAIT
        if read and self.transient():
            return SERVER_ERROR_WAITS[tries - 1]
        return None


class SweepIncomplete(Exception):
    """Some steps of the sweep failed on GitHub; it did all the others."""

    def __init__(self, done: list[str], failed: list[str]) -> None:
        super().__init__(f"{len(failed)} step(s) of the sweep failed on GitHub")
        self.done = done
        self.failed = failed
