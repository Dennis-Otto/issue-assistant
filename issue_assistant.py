"""Issue assistant: labels, a first analysis, duplicates and unanswered questions.

The GitHub Action in action.yml runs this script in the repository it looks after.
That repository keeps its labels in .github/labels.toml, a description of the
project and its settings in .github/issue-assistant/, and copies of the four
workflows and the firewall policy, which install writes from templates/.github/;
see README.md.

An AI engine from ENGINES writes the analysis. It only reads: the checkout and a
context folder that `context` writes, with the prompt and the JSON schema of its
answer. The answer reaches GitHub only through `apply`, which checks it against the
schema, the labels and the issues that exist, and which runs in a job without the
engine. Reminders, closing, reopening and the labels of the issue forms work
without one.
"""

from __future__ import annotations

import argparse
import difflib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Collection, Iterable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cache, partial
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlsplit

TOOL = Path(__file__).resolve().parent
PROMPTS = TOOL / "prompts"
# The files a repository gets, in the places they go: the workflows, the firewall
# policy and the starter files. This repository's own copies of the workflows and
# the policy are the blueprint's, of the release that it pins; install and check
# never read them.
TEMPLATES = TOOL / "templates"
# Files of the repository the assistant looks after, relative to its root. Messages
# name them, so they are written with slashes on every system, Windows too;
# `root / LABELS_FILE` is still a Path of the system.
LABELS_FILE = PurePosixPath(".github/labels.toml")
SETTINGS = PurePosixPath(".github/issue-assistant")
CONTEXT = PurePosixPath(".issue-assistant")
WORKFLOWS = PurePosixPath(".github/workflows")
FIREWALL = PurePosixPath(".github/egress-firewall.yaml")
ACTIONLINT = PurePosixPath(".github/actionlint.yaml")
FINDINGS_FILE = PurePosixPath(".github/findings.toml")
# Files that install writes only when a repository doesn't have them yet.
STARTERS = (
    LABELS_FILE,
    SETTINGS / "project.md",
    SETTINGS / "config.toml",
    FINDINGS_FILE,
)
FIREWALL_RUNNER = "ubuntu-24.04-firewall"
# The workflows of the templates: install copies them, with the commit hash of the
# release in the pin of the action, and check compares a repository's workflows
# with them.
TEMPLATE_WORKFLOWS = (
    "issue-assistant.yml",
    "issue-lifecycle.yml",
    "labels.yml",
    "findings.yml",
)
# The reasons GitHub accepts for dismissing a code scanning alert.
DISMISS_REASONS = ("false positive", "won't fix", "used in tests")
ACTION = "Dennis-Otto/issue-assistant"
ACTION_PIN = re.compile(rf"{re.escape(ACTION)}@[0-9a-f]{{40}} # \S+")
# A repository as owner/name, in the characters GitHub allows; "." and ".." are no
# names.
REPOSITORY_NAME = re.compile(r"[\w-]+/(?!\.\.?\Z)[\w.-]+", re.ASCII)

BOT = "github-actions[bot]"
MAINTAINERS = {"OWNER", "MEMBER", "COLLABORATOR"}

REMIND_AFTER = timedelta(days=15)
CLOSE_AFTER = timedelta(days=30)
# A reminder always gives this much time before the issue closes, also when the
# reminder came late, for example after the workflow was switched off.
WARNING_PERIOD = timedelta(days=15)
DUPLICATE_GRACE = timedelta(days=3)
MAX_FOLLOW_UPS = 2
# Pull requests merged this long ago still mark the issues they fix, in case the
# event of the merge was missed.
MERGE_LOOKBACK = timedelta(days=14)
# A sweep reads the merges of MERGE_LOOKBACK before its start and labels their
# issues later in its run, which the workflows stop after 20 minutes at most. So a
# fix counts for its label for this much longer, or one merged at the edge of the
# lookback would lose the release that ships it.
LABEL_SLACK = timedelta(hours=1)
# How long a comment of the reporter can reopen an issue that a release closed.
REOPEN_AFTER_RELEASE = timedelta(days=30)
# The limits of the AI runs that people who don't maintain the repository start,
# unless [engine] in config.toml sets others: analyses, follow-ups and replies after
# a release in 24 hours, analyses of one reporter's new issues in 24 hours, and the
# reporter's comments after a release that the AI reads.
DAY = timedelta(days=1)
MAX_PER_DAY = 20
MAX_PER_AUTHOR_PER_DAY = 3
RELEASE_REPLIES = 2
LIMITS = ("max_per_day", "max_per_author_per_day", "release_replies")
# GitHub keeps at most this many characters of the text of an issue or a comment;
# the assistant reads no more of any text.
MAX_TEXT = 65_536
# The most of one text, the issue's or a comment's, that the engine reads in
# issue.md; a longer one is cut off with a note.
MAX_RENDERED_TEXT = 20_000

NEEDS_TRIAGE = "needs-triage"
NEEDS_INFO = "needs-info"
STALE = "stale"
POSSIBLE_DUPLICATE = "possible-duplicate"
DUPLICATE = "duplicate"
INVALID = "invalid"
FIXED = "fixed-in-next-release"
LIFECYCLE = (
    NEEDS_TRIAGE,
    NEEDS_INFO,
    STALE,
    POSSIBLE_DUPLICATE,
    DUPLICATE,
    INVALID,
    FIXED,
)
GROUPS = ("type", "area", "topic", "lifecycle", "decision", "release", "dependabot")
# The longest label name that GitHub takes.
MAX_LABEL_NAME = 50
# project.md tells the AI what the project is, before every task: not the starter's
# placeholder, and at least this many characters.
MIN_PROJECT = 80

MODES = ("triage", "follow-up", "maintainer-reply", "release-reply")
# The tasks that reporters start, which the limits of the AI runs apply to; a
# maintainer starts the others.
LIMITED = {"triage", "follow-up", "release-reply"}

MARKER = re.compile(r"<!-- issue-assistant:([a-z-]+)((?: [a-z]+=[\w.+/-]+)*) -->")
# Control characters other than tab and line break, which no comment needs: Python
# takes some of them for spaces, which Markdown doesn't, so they could make the two
# see different lines.
STRAY_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# Characters that GitHub shows as nothing, but that the engine reads, so that a text
# could hold instructions that no maintainer sees: the format characters of Unicode
# (category Cf), such as zero-width spaces and joiners, the controls of the direction
# of writing and the tags U+E0001 to U+E007F, which spell ASCII invisibly; and the
# code points that Unicode calls default ignorable, which a program shows as nothing
# when it doesn't know them, such as the variation selectors, which can carry a byte
# each behind any character, and the fillers of Hangul. A test checks the list
# against the Unicode of the Python that runs it. The pattern is built from the
# numbers of the code points, first and last of each range: CodeQL takes the
# characters above U+FFFF in a pattern for U+FFFD, and their ranges for overlapping.
INVISIBLE_RANGES = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x0600, 0x0605),
    (0x061C, 0x061C),
    (0x06DD, 0x06DD),
    (0x070F, 0x070F),
    (0x0890, 0x0891),
    (0x08E2, 0x08E2),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFFB),
    (0x110BD, 0x110BD),
    (0x110CD, 0x110CD),
    (0x13430, 0x1343F),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)
INVISIBLE = re.compile(
    "[" + "".join(f"{chr(first)}-{chr(last)}" for first, last in INVISIBLE_RANGES) + "]"
)
# Code that Markdown shows as it is, by the rules of CommonMark: a fenced block from
# a fence line to a closing fence line of the same character and at least the same
# length, and a code span within one line between backtick runs of the same length
# that no other backtick touches. A fence counts only at the very start of a line:
# indented, it can belong to a list item before it, which the next line without
# indentation ends, and the code with it. The line break before the fence belongs
# to the block, so that no cleaning of the text before can join the two. The fence
# is all of its characters: a shorter one doesn't close it, so GitHub's block would
# run on, out of the text into the next one of the comment. A span over several
# lines can be cut by a fence or a quote on the next line, so it, like anything else
# that doesn't fit these rules exactly, counts as text: the safe side.
CODE = re.compile(
    r"(?:\A|\n)(?P<fence>(?P<char>[`~])(?P=char){2,})(?!(?P=char))[^`\n]*\n.*?"
    r"^ {0,3}(?P=fence)(?P=char)*[ \t]*$"
    r"|(?<![`\\])(?P<run>`+)(?!`)[^\n]*?(?<!`)(?P=run)(?!`)",
    re.MULTILINE | re.DOTALL,
)
# Also a link without a target, [text](), which points to the page itself. A target
# with a ( is no link here: Markdown's target runs on past the ) that closes it.
MARKDOWN_LINK = re.compile(
    r"(?<!!)\[([^\]\n]*)\]\(\s*<?([^()\s>\0]*)>?(?:\s+\"[^\"]*\")?\s*\)"
)
LINK_DEFINITION = re.compile(r"^ {0,3}\[[^\]\n]*\]:.*$", re.MULTILINE)
IMAGE = re.compile(r"!\[([^\]\n]*)\]\([^)\n]*\)")
# GitHub links http:// and https:// in any case, also right after a digit or an
# underscore: only letters before them make another scheme, such as xhttps://.
BARE_URL = re.compile(r"(?<![A-Za-z])https?://[^\s)<>\]`\0]+", re.IGNORECASE)
# It links www. after an underscore too, as in x_www.…: only a letter, a digit, a
# dot or a dash before it leaves it text. It links www. alone too, to the host www.
WWW_URL = re.compile(r"(?<![^\W_])(?<![.-])www\.[^\s<>`\0]*", re.IGNORECASE)
# GitHub links a bare address only after these characters or at the start.
AUTOLINK_AFTER = frozenset(" \t\n*_~(")
# A www. address stays a link only after the others: a later rule can put an
# underscore before it into code, as in @a/b_www.…, and after code GitHub links no
# www. address, so the rest of it would be text that could mention.
WWW_AFTER = AUTOLINK_AFTER - {"_"}
# Its link runs to the next space, past brackets, backticks and the code that
# follows, and then leaves out these characters at its end.
SPACE = re.compile(r"[ \t\n]")
TRAILING = frozenset(")?!.,:;*_~'\"")
# GitHub turns these into a mention or a link at the start of a piece of text and
# after any character but a letter or digit; an underscore can start or end an
# emphasis, so a reference counts whatever follows its number.
MENTION = re.compile(r"(?<![A-Za-z0-9])@([A-Za-z0-9][A-Za-z0-9-]{0,38}(?:/[\w-]+)?)")
CROSS_REFERENCE = re.compile(r"(?<![A-Za-z0-9])([\w.-]+/[\w.-]+#\d+)")
ISSUE_REFERENCE = re.compile(r"(?<![A-Za-z0-9])(?:#|GH-)(\d+)", re.IGNORECASE)
# GitHub also links an e-mail address, with or without mailto: or xmpp:, and a
# commit of another repository or of a fork: owner/repository@sha or user@sha. Each
# pattern starts only where a run of the characters it reads starts, so that it
# reads each character once.
EMAIL = re.compile(
    r"(?<![\w.+-])(?:(?:mailto|xmpp):)?[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE
)
COMMIT_REFERENCE = re.compile(
    r"(?<![\w.-])(?:[\w.-]+/)?[\w.-]+@[0-9a-f]{7}\w*", re.IGNORECASE
)
# Three tildes or more at the start of a line, which would start or end a fenced
# block: text can't escape them with a backtick, as it does with the other fence.
TILDE_FENCE = re.compile(r"^([ \t]*)(?=~~~)", re.MULTILINE)
# A hyphen next to a pipe or a colon, perhaps with spaces between. Each line of
# hyphens with a pipe or a colon has one, such as `--|--` or `:-:`, the delimiter row
# below the header of a table, and GitHub splits every row of a table at each pipe,
# also inside code, so that the rest of the code would become text.
TABLE_HYPHEN = re.compile(r"(?<=[|:])([ \t]*)-|-(?=[ \t]*[|:])")
# A line of a list item after its first, with the spaces and tabs that indent it.
CONTINUATION = re.compile(r"\n([ \t]*)(?=[^ \t\n])")
# The patterns that read the text of an issue look at each character a bounded
# number of times, so that no text, however crafted, makes them slow. The tests and
# fuzz/fuzz_issue_text.py time them on texts that took earlier patterns hours.
#
# An HTML comment. comment_parts splits a text where its last `-->` ends, so that
# this pattern only reads the part in which a `-->` follows every `<!--`.
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# A line that starts with `<!--`: on GitHub, an HTML block that runs to its `-->`, or
# without one, hides the rest of the text.
COMMENT_LINE = re.compile(r"^ {0,3}<!--", re.MULTILINE)
# An HTML tag within one line.
TAG = r"</?[A-Za-z][^>\n]*>"
# An HTML comment, or a tag. When no `>` closes a tag on its line, no later tag on
# that line can close either: the third alternative takes them all in one step, up
# to the next comment, and strip_html keeps them as they are.
HTML = re.compile(
    rf"{HTML_COMMENT.pattern}|{TAG}"
    r"|(?P<open></?[A-Za-z][^>\n<]*(?:<(?!!--)[^>\n<]*)*)",
    re.DOTALL,
)
# The same for the part of a text after its last `-->`, where no comment can end:
# a `<!--` there is text, and a tag start without its `>` takes the rest of its line.
HTML_TAG = re.compile(rf"{TAG}|(?P<open></?[A-Za-z][^>\n]*)")
# A heading of an issue form: GitHub writes the label of each field as `### Label`
# above its answer.
FORM_HEADING = re.compile(r"^###[ \t]+(.*)$", re.MULTILINE)
# The marks of a heading, without the line break after them.
HEADING_LINE = re.compile(r"^[ \t]{0,3}#{1,6}(?:[ \t]+|$)", re.MULTILINE)
SECRET = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    # Not the end of a word, as in task-runner-configuration-for-tests.
    r"|(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_-]{8,}"
    r"|(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_-]{20,}"
    r"|xox[abposr]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
    # Webhooks, whose address alone lets anyone post as their owner: up to the secret
    # at its end, which placeholders such as services/YOUR/WEBHOOK/URL lack.
    r"|hooks\.slack\.com/(?:services|triggers|workflows)/(?:[A-Za-z0-9]+/)+"
    r"[A-Za-z0-9]{20,}"
    r"|\.webhook\.office\.com/webhookb2/[\w@-]+/IncomingWebhook/[0-9a-f]{20,}/"
    r"[0-9a-f-]{20,}"
)
LONG_TOKEN = re.compile(r"(?<![\w+/=-])[\w+/=-]{40,}(?![\w+/=-])")
# A link to a repository on GitHub, whose owner and name people choose, up to the
# path of a file or folder in it after the commit or branch. A name or a branch of 32
# or more characters is checked like any other text, so that no key hides as one; a
# commit is lower-case hex, which never mixes case.
GITHUB_REPOSITORY = re.compile(
    r"https://github\.com/[A-Za-z0-9-]{1,39}/[\w.-]{1,31}(?![\w.-])"
    r"(?:/(?:blob|tree)/(?:[0-9a-f]{40}|[\w.-]{1,31})(?![\w.-]))?"
)
LETTER_PAIR = re.compile(r"(?=[A-Za-z]{2})")
CASE_CHANGE = re.compile(r"(?=[a-z][A-Z]|[A-Z][a-z])")
LOWER_CASE = re.compile(r"[a-z]+")
SHA = re.compile(r"[0-9a-f]{40}")
GERMAN_WORDS = {
    "und", "der", "die", "das", "nicht", "ich", "ist", "mit", "auf", "wenn",
    "bei", "wird", "kann", "ein", "eine", "habe", "auch", "noch", "dass",
    "funktioniert", "keine", "aber", "wie", "nach", "mein", "meine",
}  # fmt: skip
ENGLISH_WORDS = {
    "the", "and", "is", "not", "with", "when", "it", "this", "that", "does",
    "have", "can", "an", "of", "to", "works", "but", "how", "after", "my",
    "no", "on", "in", "for", "what", "be",
}  # fmt: skip
MONTHS_DE = (
    "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
    "September", "Oktober", "November", "Dezember",
)  # fmt: skip


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
    """A gh call failed, with the HTTP status and the GraphQL error types of GitHub.

    Only a thing that doesn't exist is an answer about the thing. A rate limit or a
    server error says nothing about it: read as "missing", it would change what the
    assistant posts, such as an analysis without the duplicate it found.
    """

    def __init__(
        self, message: str, *, status: int | None = None, types: Iterable[str] = ()
    ) -> None:
        super().__init__(message)
        self.status = status
        self.types = frozenset(types)

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
        types = {
            error["type"]
            for page in pages
            if isinstance(page, dict)
            for error in page.get("errors") or []
            if isinstance(error, dict) and isinstance(error.get("type"), str)
        }
        return cls(
            f"gh {args[1] if len(args) > 1 else ''}: {(stderr or stdout).strip()}",
            status=int(statuses[-1]) if statuses else None,
            types=types,
        )

    def missing(self) -> bool:
        """Whether GitHub said that the thing doesn't exist, or no longer does."""
        return self.status in {404, 410} or "NOT_FOUND" in self.types

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


def command(name: str, message: str) -> None:
    """Print a workflow command, such as an error or a notice, with its message.

    The runner reads every line of the log that starts with `::` as a command, such
    as `::add-mask::` or `::stop-commands::`. Escaped as GitHub's Actions toolkit
    escapes it, the message stays on its line, so that no text in it, such as the
    engine's, can start another command.
    """
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{name}::{escaped}")


# Engines


@dataclass(frozen=True)
class Engine:
    """An AI engine that can write the analysis, named in its comments."""

    name: str
    vendor: str
    model: str


# The engines the workflow template has a step for; .github/issue-assistant/config.toml
# of a repository chooses one. README.md#engines describes how to add one.
ENGINES = {"claude": Engine("Claude", "Anthropic", "claude-opus-5-5")}
DEFAULT_ENGINE = "claude"
# The workflow passes the model to the engine's command line without quotes. The
# names and IDs of models fit, also with a version after @ or :, a path or a suffix
# in brackets; a blank, a quote or a line break would add arguments, and so would a
# leading dash, which the engine's action reads as the next flag.
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/\[\]-]*")


def is_model_name(model: object) -> bool:
    return isinstance(model, str) and bool(MODEL_NAME.fullmatch(model))


def model_problem(model: object) -> str:
    return (
        f"{SETTINGS / 'config.toml'}: the model {model!r} is no model name, "
        f"such as {ENGINES[DEFAULT_ENGINE].model}"
    )


def engine_named(name: str | None) -> str:
    """The key of a known engine; the default for an empty name."""
    key = name or DEFAULT_ENGINE
    if key not in ENGINES:
        raise AssistantError(
            f"Unknown engine {key}; known engines: {', '.join(sorted(ENGINES))}."
        )
    return key


# The repository's settings


@dataclass(frozen=True)
class Label:
    name: str
    color: str
    description: str
    group: str
    form_options: tuple[str, ...] = ()
    # For a kind: whether the assistant may ask the reporter for information.
    ask: bool = True


@dataclass(frozen=True)
class Acceptance:
    """A finding of code scanning that the repository accepts, with its reason."""

    tool: str
    rule: str
    reason: str
    comment: str
    # Only alerts in this file or in the files of this folder; empty for every file.
    path: str = ""

    def covers(self, alert: dict[str, Any]) -> bool:
        location = (alert.get("most_recent_instance") or {}).get("location") or {}
        path = str(location.get("path") or "")
        # A folder covers its files, not every name that starts alike: tests covers
        # tests/test_a.py, but not testsuite/test_a.py.
        folder = self.path.rstrip("/")
        return (
            (alert.get("tool") or {}).get("name", "").casefold() == self.tool.casefold()
            and (alert.get("rule") or {}).get("id") == self.rule
            and (not folder or path == folder or path.startswith(f"{folder}/"))
        )


@dataclass(frozen=True)
class Config:
    """What the assistant knows about the repository it looks after."""

    root: Path
    repository: str
    labels: tuple[Label, ...]
    project: str
    engine: str = DEFAULT_ENGINE
    model: str = ""
    hosts: frozenset[str] = frozenset()
    area_field: str = "Area"
    unmapped_options: tuple[str, ...] = ("Other",)
    notices: tuple[str, ...] = ("SUPPORT.md",)
    # Whether a fixed issue stays open until a release ships the fix.
    releases: bool = True
    # The limits of the AI runs; see LIMITS.
    max_per_day: int = MAX_PER_DAY
    max_per_author_per_day: int = MAX_PER_AUTHOR_PER_DAY
    release_replies: int = RELEASE_REPLIES
    # What `check` reports of labels.toml and config.toml beyond its own checks: the
    # values that the assistant can't use and the keys that it doesn't know.
    problems: tuple[str, ...] = ()

    @property
    def blob(self) -> str:
        return f"https://github.com/{self.repository}/blob"

    @property
    def policy(self) -> str:
        return f"https://github.com/{self.repository}/security/policy"

    def group(self, name: str) -> list[str]:
        return [label.name for label in self.labels if label.group == name]

    def asking_kinds(self) -> set[str]:
        return {
            label.name for label in self.labels if label.group == "type" and label.ask
        }


@dataclass(frozen=True)
class Key:
    """A key of config.toml: the attribute of Config that it sets, and its type."""

    attribute: str
    # str, bool, int for a whole number, or list for a list of strings.
    kind: type
    # The value of the attribute, from the value in the file.
    convert: Callable[[Any], Any] = lambda value: value


# Every key of config.toml, by its section. A key that isn't here is a typo, which
# would keep the default unnoticed.
CONFIG_KEYS: dict[str, dict[str, Key]] = {
    "engine": {
        "name": Key("engine", str),
        "model": Key("model", str),
        "max_per_day": Key("max_per_day", int),
        "max_per_author_per_day": Key("max_per_author_per_day", int),
        "release_replies": Key("release_replies", int),
    },
    "links": {"hosts": Key("hosts", list, frozenset)},
    "forms": {
        "area_field": Key("area_field", str),
        "unmapped_options": Key("unmapped_options", list, tuple),
    },
    "transparency": {"files": Key("notices", list, tuple)},
    "releases": {"close_with_release": Key("releases", bool)},
}
# The keys of a [[label]] of labels.toml with their types; the first four are needed.
LABEL_KEYS: dict[str, type] = {
    "name": str,
    "color": str,
    "description": str,
    "group": str,
    "form_options": list,
    "ask": bool,
}
NEEDED_LABEL_KEYS = ("name", "color", "description", "group")
# The keys of an [[accept]] of findings.toml; all but path are needed.
ACCEPT_KEYS = ("tool", "rule", "reason", "comment", "path")
KINDS = {
    str: "a string",
    bool: "true or false",
    int: "a whole number",
    list: "a list of strings",
}


def fits(value: object, kind: type) -> bool:
    """Whether a value of a settings file has the type of its key."""
    if kind is list:
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    # Python's True is an int, but TOML's true is no whole number.
    return isinstance(value, kind) and (kind is bool or not isinstance(value, bool))


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


@dataclass
class Problems:
    """What reading the settings found."""

    # Values that the assistant can't use, which stop every command.
    wrong: list[str] = field(default_factory=list)
    # Keys that it doesn't know, which change nothing.
    unknown: list[str] = field(default_factory=list)


def read_toml(path: Path, where: PurePosixPath) -> dict[str, Any]:
    """A settings file; one that isn't TOML stops every command."""
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise SettingsError([f"{where} is no valid TOML: {error}"]) from None


def load_labels(path: Path, problems: Problems) -> tuple[Label, ...]:
    """The labels of labels.toml; a label that can't be used is left out."""
    data = read_toml(path, LABELS_FILE)
    for name in data:
        if name in LABEL_KEYS:
            problems.unknown.append(f"{LABELS_FILE}: {name} belongs in a [[label]]")
        elif name != "label":
            problems.unknown.append(
                did_you_mean(f"{LABELS_FILE}: unknown key {name}", name, ["label"])
            )
    items = data.get("label", [])
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        problems.wrong.append(
            f"{LABELS_FILE}: each label must be a table, [[label]], with its keys "
            "below it"
        )
        return ()
    labels = []
    for number, item in enumerate(items, start=1):
        name = item.get("name")
        where = (
            f"{LABELS_FILE}: the label {name}"
            if isinstance(name, str) and name
            else f"{LABELS_FILE}: label {number}"
        )
        for key in item:
            if key not in LABEL_KEYS:
                problems.unknown.append(
                    did_you_mean(f"{where}: unknown key {key}", key, LABEL_KEYS)
                )
        missing = [key for key in NEEDED_LABEL_KEYS if key not in item]
        wrong = [
            key
            for key, kind in LABEL_KEYS.items()
            if key in item and not fits(item[key], kind)
        ]
        if missing:
            problems.wrong.append(f"{where} needs {', '.join(missing)}")
        for key in wrong:
            problems.wrong.append(f"{where}: {key} must be {KINDS[LABEL_KEYS[key]]}")
        if not missing and not wrong:
            labels.append(
                Label(
                    name=item["name"],
                    color=item["color"].lower(),
                    description=item["description"],
                    group=item["group"],
                    form_options=tuple(item.get("form_options", ())),
                    ask=item.get("ask", True),
                )
            )
    return tuple(labels)


def read_settings(path: Path, problems: Problems) -> dict[str, Any]:
    """The attributes of Config that config.toml sets; none without the file."""
    if not path.is_file():
        return {}
    where = SETTINGS / "config.toml"
    home = {name: section for section, keys in CONFIG_KEYS.items() for name in keys}
    values: dict[str, Any] = {}
    for section, table in read_toml(path, where).items():
        keys = CONFIG_KEYS.get(section)
        if keys is None and section in home:
            problems.unknown.append(
                f"{where}: {section} belongs in the section [{home[section]}]"
            )
        elif keys is None:
            problems.unknown.append(
                did_you_mean(
                    f"{where}: unknown section [{section}]",
                    section,
                    CONFIG_KEYS,
                    "[{}]",
                )
            )
        elif not isinstance(table, dict):
            problems.wrong.append(
                f"{where}: {section} must be a section, [{section}], with its keys "
                "below it"
            )
        else:
            for name, value in table.items():
                key = keys.get(name)
                if key is None and name in home:
                    problems.unknown.append(
                        f"{where}: {name} belongs in the section [{home[name]}], "
                        f"not in [{section}]"
                    )
                elif key is None:
                    problems.unknown.append(
                        did_you_mean(
                            f"{where}: unknown key {name} in [{section}]", name, keys
                        )
                    )
                elif not fits(value, key.kind):
                    problems.wrong.append(
                        f"{where}: [{section}] {name} must be {KINDS[key.kind]}"
                    )
                else:
                    values[key.attribute] = key.convert(value)
    return values


def load_config(root: Path, repository: str, *, lenient: bool = False) -> Config:
    """The settings of the repository in `root`; config.toml is optional.

    A file that isn't TOML stops every command. So does a value that the assistant
    can't use, such as a word where a number belongs, with one error that names each
    such value; only `check` reads the rest, `lenient`, and reports them with its
    other problems. A key that the assistant doesn't know changes nothing, so that a
    typo would keep a default unnoticed: `check` fails on it, and the other commands
    warn of it.
    """
    project_file = root / SETTINGS / "project.md"
    if not project_file.is_file() or not (root / LABELS_FILE).is_file():
        raise AssistantError(
            f"{LABELS_FILE} and {SETTINGS / 'project.md'} are needed; "
            "see https://github.com/Dennis-Otto/issue-assistant#set-up-a-repository."
        )
    try:
        project = project_file.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError as error:
        raise SettingsError(
            [f"{SETTINGS / 'project.md'} is no UTF-8 text: {error}"]
        ) from None
    problems = Problems()
    labels = load_labels(root / LABELS_FILE, problems)
    values = read_settings(root / SETTINGS / "config.toml", problems)
    if lenient:
        values["problems"] = (*problems.wrong, *problems.unknown)
    elif problems.wrong:
        raise SettingsError(problems.wrong)
    else:
        for problem in problems.unknown:
            command("warning", problem)
    return Config(
        root=root,
        repository=repository,
        labels=labels,
        project=project,
        **values,
    )


def limit(config: Config, key: str) -> int:
    """A limit of the AI runs, one of LIMITS.

    A value that is no whole number of 0 or more stops the run instead of lifting
    the limit; `check` names it before.
    """
    value = getattr(config, key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AssistantError(
            f"{SETTINGS / 'config.toml'}: {key} must be a whole number, 0 or more"
        )
    return value


def load_findings(root: Path) -> tuple[Acceptance, ...]:
    """The accepted findings of .github/findings.toml; none without the file.

    Only the findings command and the check read it, so a broken file never stops
    the work on issues. A key that it doesn't know stops them, since a typo such as
    `paths` would accept the findings of a rule in every file.
    """
    path = root / FINDINGS_FILE
    if not path.is_file():
        return ()
    data = read_toml(path, FINDINGS_FILE)
    try:
        items = data.get("accept", [])
        accepted = tuple(
            Acceptance(
                tool=str(item["tool"]),
                rule=str(item["rule"]),
                reason=str(item["reason"]),
                comment=str(item["comment"]),
                path=str(item.get("path", "")),
            )
            for item in items
        )
    except (KeyError, TypeError) as error:
        raise SettingsError(
            [
                f"{FINDINGS_FILE}: every [[accept]] needs tool, rule, reason and "
                f"comment ({type(error).__name__}: {error})"
            ]
        ) from error
    problems = [
        did_you_mean(f"{FINDINGS_FILE}: unknown key {name}", name, ["accept"])
        for name in data
        if name != "accept"
    ]
    for item, acceptance in zip(items, accepted, strict=True):
        where = f"{FINDINGS_FILE}: {acceptance.tool} {acceptance.rule}"
        problems += [
            did_you_mean(f"{where}: unknown key {key}", key, ACCEPT_KEYS)
            for key in item
            if key not in ACCEPT_KEYS
        ]
    if problems:
        raise SettingsError(problems)
    return accepted


def label_names(issue: dict[str, Any]) -> set[str]:
    return {label["name"] for label in issue.get("labels") or []}


# A fixture: an issue from a file


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


# GitHub


class GitHub:
    """`gh api` calls for one repository; in a dry run, writes are only reported."""

    def __init__(self, repository: str, *, dry_run: bool = False) -> None:
        self.repository = repository
        self.owner, self.name = repository.split("/")
        self.dry_run = dry_run
        self.writes: list[str] = []
        # The comments a dry run would post, for the job summary.
        self.previews: list[str] = []
        self._labels: list[dict[str, Any]] | None = None
        # The issue that a file holds in place of GitHub.
        self.fixture: Fixture | None = None

    def use_fixture(self, fixture: Fixture) -> None:
        """Read the issue of the fixture and its comments from it, not from GitHub,
        and from now on write nothing, as in a dry run: the issue exists nowhere."""
        self.fixture = fixture
        self.dry_run = True

    def _gh(self, args: list[str], data: object = None, *, read: bool = True) -> Any:
        """Run gh, and run it again when GitHub failed or refused it for a moment.

        A secondary rate limit runs it again after a minute, up to 3 times. After a
        server error only a read runs again, after 2, 4 and 8 seconds: GitHub may
        have done a write anyway, and the next run completes what is missing.
        """
        tries = 1
        while True:
            result = subprocess.run(
                ["gh", *args],
                input=None if data is None else json.dumps(data),
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )
            if result.returncode == 0:
                return json.loads(result.stdout) if result.stdout.strip() else None
            error = GitHubError.of(args, result.stdout, result.stderr)
            wait = error.wait(tries, read)
            if wait is None:
                raise error
            command("notice", f"{error}; trying again in {wait} s")
            time.sleep(wait)
            tries += 1

    def rest(
        self,
        path: str,
        *,
        method: str = "GET",
        data: object = None,
        pages: bool = False,
    ) -> Any:
        args = ["api", f"repos/{self.repository}/{path}"]
        if method != "GET":
            args += ["--method", method]
        if pages:
            args += ["--paginate", "--slurp"]
        if data is not None:
            args += ["--input", "-"]
        response = self._gh(args, data, read=method == "GET")
        if pages:
            return [item for page in response for item in page]
        return response

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = self._gh(
            ["api", "graphql", "--input", "-"],
            {"query": query, "variables": variables},
            read=not query.lstrip().startswith("mutation"),
        )
        if response.get("errors"):
            raise GitHubError(
                "; ".join(error["message"] for error in response["errors"]),
                types=(
                    error["type"] for error in response["errors"] if "type" in error
                ),
            )
        data: dict[str, Any] = response["data"]
        return data

    def _write(self, description: str, action: Callable[[], object]) -> None:
        print(("Would " if self.dry_run else "") + description)
        if not self.dry_run:
            try:
                action()
            except GitHubError:
                # The summary of a failed run says what it wrote, and what failed.
                self.writes.append(f"Failed: {description}")
                raise
        self.writes.append(description)

    # Reads

    def issue(self, number: int) -> dict[str, Any]:
        if self.fixture and number == self.fixture.number:
            # A copy, as GitHub sends a new one for every request.
            return deepcopy(self.fixture.issue)
        issue: dict[str, Any] = self.rest(f"issues/{number}")
        return issue

    def comments(self, number: int) -> list[dict[str, Any]]:
        if self.fixture and number == self.fixture.number:
            return deepcopy(self.fixture.comments)
        comments: list[dict[str, Any]] = self.rest(
            f"issues/{number}/comments?per_page=100", pages=True
        )
        return comments

    def events(self, number: int) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = self.rest(
            f"issues/{number}/events?per_page=100", pages=True
        )
        return events

    def reactions(self, comment_id: int) -> list[dict[str, Any]]:
        reactions: list[dict[str, Any]] = self.rest(
            f"issues/comments/{comment_id}/reactions?per_page=100", pages=True
        )
        return reactions

    def may_push(self, user: str) -> bool:
        """Whether the user may push to the repository, as a maintainer does.

        GitHub's permission is admin or write for the roles admin, maintain and
        write, also through a team or a custom role, and read or none for the rest.
        """
        found = self.rest(f"collaborators/{quote(user, safe='')}/permission")
        permission: str = found.get("permission") or "none"
        return permission in {"admin", "write"}

    def open_issues(self, label: str) -> list[dict[str, Any]]:
        items = self.rest(
            f"issues?state=open&labels={quote(label, safe='')}&per_page=100", pages=True
        )
        return [item for item in items if "pull_request" not in item]

    def all_issues(self) -> list[dict[str, Any]]:
        items = self.rest("issues?state=all&per_page=100", pages=True)
        return [item for item in items if "pull_request" not in item]

    def issues_since(self, since: datetime) -> list[dict[str, Any]]:
        """The issues opened or changed at or after the moment."""
        items = self.rest(
            f"issues?state=all&since={stamp(since)}&per_page=100", pages=True
        )
        return [item for item in items if "pull_request" not in item]

    def comments_since(self, since: datetime) -> list[dict[str, Any]]:
        """The comments on issues and pull requests written or changed at or after
        the moment."""
        comments: list[dict[str, Any]] = self.rest(
            f"issues/comments?since={stamp(since)}&per_page=100", pages=True
        )
        return comments

    def labels(self) -> list[dict[str, Any]]:
        if self._labels is None:
            self._labels = self.rest("labels?per_page=100", pages=True)
        return self._labels

    def latest_release(self) -> str | None:
        try:
            release = self.rest("releases/latest")
        except GitHubError as error:
            if not error.missing():
                raise
            return None
        tag: str = release["tag_name"]
        return tag

    def releases(self) -> list[dict[str, Any]]:
        """The published final releases, oldest first; no drafts or pre-releases.

        Of the newest hundred: a repository with a beta channel publishes a
        pre-release for each fix, and a late label closes with a release up to
        MERGE_LOOKBACK older than it.
        """
        items = self.rest("releases?per_page=100")
        return sorted(
            (
                item
                for item in items
                if not item.get("draft")
                and not item.get("prerelease")
                and item.get("published_at")
            ),
            key=lambda item: item["published_at"],
        )

    def contains(self, sha: str, tag: str) -> bool:
        """Whether the commit `sha` is part of the release `tag`."""
        try:
            compared = self.rest(f"compare/{sha}...{quote(tag)}?per_page=1")
        except GitHubError as error:
            # A commit or a tag that the repository doesn't have.
            if not error.missing():
                raise
            return False
        return compared["status"] in {"ahead", "identical"}

    def owns(self, node: dict[str, Any]) -> bool:
        """Whether a pull request or an issue of GraphQL belongs to this repository."""
        name: str = node["repository"]["nameWithOwner"]
        return name.lower() == self.repository.lower()

    def merged_pulls(self, since: datetime) -> tuple[str, list[dict[str, Any]]]:
        """The default branch, and the pull requests merged since `since`.

        Each with the issues that it fixes and their state. GitHub lists them by
        their last update, which a merge is, so the pages end with one that nobody
        updated since then; a busy repository merges far more than one page in
        MERGE_LOOKBACK.
        """
        pulls: list[dict[str, Any]] = []
        cursor = None
        while True:
            data = self.graphql(
                """
                query($owner: String!, $name: String!, $cursor: String) {
                  repository(owner: $owner, name: $name) {
                    defaultBranchRef { name }
                    pullRequests(states: MERGED, first: 100, after: $cursor,
                                 orderBy: {field: UPDATED_AT, direction: DESC}) {
                      pageInfo { hasNextPage endCursor }
                      nodes {
                        number mergedAt updatedAt baseRefName
                        mergeCommit { oid }
                        closingIssuesReferences(first: 10) {
                          nodes { number state repository { nameWithOwner } }
                        }
                      }
                    }
                  }
                }
                """,
                {"owner": self.owner, "name": self.name, "cursor": cursor},
            )
            repository = data["repository"]
            page = repository["pullRequests"]
            pulls += [
                pull for pull in page["nodes"] if moment(pull["mergedAt"]) >= since
            ]
            if (
                not page["pageInfo"]["hasNextPage"]
                or moment(page["nodes"][-1]["updatedAt"]) < since
            ):
                return repository["defaultBranchRef"]["name"], pulls
            cursor = page["pageInfo"]["endCursor"]

    def fixing_pulls(self, number: int) -> list[dict[str, Any]]:
        """This repository's merged pull requests into the default branch that fix the issue.

        A pull request of another repository can name the issue too, as
        `Fixes owner/project#12`; its commit isn't here, and no release here ships it.
        """
        data = self.graphql(
            """
            query($owner: String!, $name: String!, $number: Int!) {
              repository(owner: $owner, name: $name) {
                defaultBranchRef { name }
                issue(number: $number) {
                  closedByPullRequestsReferences(first: 10, includeClosedPrs: true) {
                    nodes {
                      number merged mergedAt baseRefName mergeCommit { oid }
                      repository { nameWithOwner }
                    }
                  }
                }
              }
            }
            """,
            {"owner": self.owner, "name": self.name, "number": number},
        )
        repository = data["repository"]
        branch = repository["defaultBranchRef"]["name"]
        return [
            pull
            for pull in repository["issue"]["closedByPullRequestsReferences"]["nodes"]
            if self.owns(pull)
            and pull["merged"]
            and pull["baseRefName"] == branch
            and pull["mergeCommit"]
        ]

    def discussions(self) -> list[dict[str, Any]]:
        try:
            data = self.graphql(
                """
                query($owner: String!, $name: String!) {
                  repository(owner: $owner, name: $name) {
                    discussions(first: 100, orderBy: {field: UPDATED_AT, direction: DESC}) {
                      nodes {
                        number title closed isAnswered body
                        category { name }
                        answer { body }
                      }
                    }
                  }
                }
                """,
                {"owner": self.owner, "name": self.name},
            )
        except GitHubError as error:
            # A repository without discussions has none; GitHub says so only when
            # the repository itself is gone.
            if not error.missing():
                raise
            return []
        nodes: list[dict[str, Any]] = data["repository"]["discussions"]["nodes"]
        return nodes

    def discussion_exists(self, number: int) -> bool:
        try:
            data = self.graphql(
                """
                query($owner: String!, $name: String!, $number: Int!) {
                  repository(owner: $owner, name: $name) {
                    discussion(number: $number) { number }
                  }
                }
                """,
                {"owner": self.owner, "name": self.name, "number": number},
            )
        except GitHubError as error:
            if not error.missing():
                raise
            return False
        return bool(data["repository"]["discussion"])

    # Writes

    def comment(self, number: int, body: str) -> None:
        if self.dry_run:
            self.previews.append(body)
        self._write(
            f"comment on #{number}",
            lambda: self.rest(
                f"issues/{number}/comments", method="POST", data={"body": body}
            ),
        )

    def edit_comment(self, comment_id: int, body: str) -> None:
        """Change a comment of the assistant; an edit notifies nobody."""
        self._write(
            f"edit comment {comment_id}",
            lambda: self.rest(
                f"issues/comments/{comment_id}", method="PATCH", data={"body": body}
            ),
        )

    def add_labels(self, number: int, names: list[str]) -> None:
        if names:
            self._write(
                f"add {', '.join(names)} to #{number}",
                lambda: self.rest(
                    f"issues/{number}/labels", method="POST", data={"labels": names}
                ),
            )

    def add_explained_labels(
        self, issue: dict[str, Any], names: list[str], rationale: str
    ) -> None:
        """Add labels with GitHub's rationale, shown on the issue; plain labels if that fails."""
        if not names:
            return
        ids = {label["name"]: label["node_id"] for label in self.labels()}

        def add() -> None:
            try:
                self.graphql(
                    """
                    mutation($issue: ID!, $labels: [LabelUpdateInput!]!) {
                      addLabelsToLabelable(input: {labelableId: $issue, labels: $labels}) {
                        clientMutationId
                      }
                    }
                    """,
                    {
                        "issue": issue["node_id"],
                        "labels": [
                            {"labelId": ids[name], "rationale": rationale[:280]}
                            for name in names
                        ],
                    },
                )
            except (GitHubError, KeyError) as error:
                command(
                    "notice", f"Labels without rationale on #{issue['number']}: {error}"
                )
                self.rest(
                    f"issues/{issue['number']}/labels",
                    method="POST",
                    data={"labels": names},
                )

        self._write(f"add {', '.join(names)} to #{issue['number']}", add)

    def remove_label(self, number: int, name: str) -> None:
        def remove() -> None:
            try:
                # A label name is one part of the path, also with a slash.
                self.rest(
                    f"issues/{number}/labels/{quote(name, safe='')}", method="DELETE"
                )
            except GitHubError as error:
                # Not on the issue; a missing issue is an error.
                if not (error.missing() and "Label does not exist" in str(error)):
                    raise

        self._write(f"remove {name} from #{number}", remove)

    def close(
        self,
        issue: dict[str, Any],
        reason: str,
        rationale: str,
        duplicate_of: dict[str, Any] | None = None,
    ) -> None:
        """Close as COMPLETED, NOT_PLANNED or DUPLICATE, linked to the original."""

        def close() -> None:
            try:
                self.graphql(
                    """
                    mutation($issue: ID!, $reason: IssueClosedStateReason!,
                             $duplicate: ID, $rationale: String) {
                      closeIssue(input: {issueId: $issue, stateReason: $reason,
                                         duplicateIssueId: $duplicate, rationale: $rationale}) {
                        issue { number }
                      }
                    }
                    """,
                    {
                        "issue": issue["node_id"],
                        "reason": reason,
                        "duplicate": duplicate_of["node_id"] if duplicate_of else None,
                        "rationale": rationale[:280],
                    },
                )
            except GitHubError as error:
                command("notice", f"Closing #{issue['number']} through REST: {error}")
                self.rest(
                    f"issues/{issue['number']}",
                    method="PATCH",
                    data={"state": "closed", "state_reason": reason.lower()},
                )

        target = f" as a duplicate of #{duplicate_of['number']}" if duplicate_of else ""
        self._write(f"close #{issue['number']} ({reason.lower()}){target}", close)

    def suggest_duplicate(
        self, issue: dict[str, Any], original: dict[str, Any], rationale: str
    ) -> None:
        """Ask the maintainer to close as a duplicate, in GitHub's suggestions panel."""

        def suggest() -> None:
            try:
                self.graphql(
                    """
                    mutation($issue: ID!, $duplicate: ID!, $rationale: String!) {
                      closeIssue(input: {issueId: $issue, stateReason: DUPLICATE,
                                         duplicateIssueId: $duplicate, rationale: $rationale,
                                         isSuggestion: true, confidence: MEDIUM}) {
                        issue { number }
                      }
                    }
                    """,
                    {
                        "issue": issue["node_id"],
                        "duplicate": original["node_id"],
                        "rationale": rationale[:280],
                    },
                )
            except GitHubError as error:
                command(
                    "notice", f"No duplicate suggestion on #{issue['number']}: {error}"
                )

        self._write(
            f"suggest closing #{issue['number']} as a duplicate of #{original['number']}",
            suggest,
        )

    def reopen(self, number: int) -> None:
        self._write(
            f"reopen #{number}",
            lambda: self.rest(
                f"issues/{number}", method="PATCH", data={"state": "open"}
            ),
        )

    def set_milestone(self, number: int, title: str, due: str) -> None:
        """File the issue under the milestone `title`, a closed one made when missing."""

        def assign() -> None:
            milestones = self.rest("milestones?state=all&per_page=100", pages=True)
            found = next((item for item in milestones if item["title"] == title), None)
            if found is None:
                found = self.rest(
                    "milestones",
                    method="POST",
                    data={
                        "title": title,
                        "state": "closed",
                        "description": f"The issues that {title} fixed.",
                        "due_on": due,
                    },
                )
            self.rest(
                f"issues/{number}", method="PATCH", data={"milestone": found["number"]}
            )

        self._write(f"set the milestone {title} on #{number}", assign)

    def open_alerts(self) -> list[dict[str, Any]] | None:
        """The open alerts of code scanning; None where it isn't set up."""
        try:
            alerts: list[dict[str, Any]] = self.rest(
                "code-scanning/alerts?state=open&per_page=100", pages=True
            )
        except GitHubError as error:
            text = str(error).casefold()
            if "no analysis found" in text or "not enabled" in text:
                return None
            raise
        return alerts

    def dismiss_alert(self, number: int, reason: str, comment: str) -> None:
        self._write(
            f"dismiss alert #{number} as {reason}",
            lambda: self.rest(
                f"code-scanning/alerts/{number}",
                method="PATCH",
                data={
                    "state": "dismissed",
                    "dismissed_reason": reason,
                    "dismissed_comment": comment[:280],
                },
            ),
        )

    def create_label(self, label: Label) -> None:
        self._write(
            f"create label {label.name}",
            lambda: self.rest(
                "labels",
                method="POST",
                data={
                    "name": label.name,
                    "color": label.color,
                    "description": label.description,
                },
            ),
        )

    def update_label(self, label: Label, current: str | None = None) -> None:
        """Set the color and the description of a label, and its name where GitHub
        has it as `current`, in another case."""
        name = current or label.name
        data = {"color": label.color, "description": label.description}
        if name != label.name:
            data["new_name"] = label.name
        self._write(
            f"update label {name}",
            lambda: self.rest(
                f"labels/{quote(name, safe='')}", method="PATCH", data=data
            ),
        )


# Comments and issues


def login(item: dict[str, Any] | None) -> str:
    return str((item or {}).get("login") or "")


def is_bot(user: dict[str, Any] | None) -> bool:
    return (user or {}).get("type") == "Bot" or login(user).endswith("[bot]")


def is_maintainer(item: dict[str, Any]) -> bool:
    return item.get("author_association") in MAINTAINERS


def text_of(item: dict[str, Any]) -> str:
    """The text of an issue or a comment, as much of it as GitHub keeps."""
    return str(item.get("body") or "")[:MAX_TEXT]


def moment(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def created(item: dict[str, Any]) -> datetime:
    return moment(item["created_at"])


def stamp(when: datetime) -> str:
    """The moment as GitHub writes it, in UTC to the second."""
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def marker(comment: dict[str, Any]) -> tuple[str, dict[str, str]] | None:
    """The kind and attributes of an assistant comment; None for every other comment."""
    if login(comment.get("user")) != BOT:
        return None
    match = MARKER.search(text_of(comment))
    if not match:
        return None
    attributes = dict(part.split("=", 1) for part in match[2].split())
    return match[1], attributes


def kind_of(comment: dict[str, Any]) -> str | None:
    found = marker(comment)
    return found[0] if found else None


def render_marker(kind: str, **attributes: object) -> str:
    parts = "".join(f" {key}={value}" for key, value in attributes.items())
    return f"<!-- issue-assistant:{kind}{parts} -->"


def form_fields(body: str) -> dict[str, str]:
    """The answers of an issue form, by the label of their field.

    An answer is the text between its heading and the next one. Each heading is
    found once and the answers are cut out between them, so the time grows with the
    length of the text and no faster. GitHub writes an answer under every heading of
    a form, so a heading on the last line, with no line break after it, is a heading
    of free text and starts no field.
    """
    body = body or ""
    headings = list(FORM_HEADING.finditer(body))
    starts = [heading.start() for heading in headings] + [len(body)]
    fields = {}
    for heading, end in zip(headings, starts[1:], strict=True):
        if heading.end() == len(body):
            continue
        value = body[heading.end() : end].strip()
        fields[heading[1].strip()] = "" if value == "_No response_" else value
    return fields


def form_labels(issue: dict[str, Any], config: Config) -> list[str]:
    """The labels that the answer of the area field chooses.

    GitHub joins the options of a dropdown with `multiple: true` with ", "; an
    option that holds ", " itself stays one answer.
    """
    area = form_fields(text_of(issue)).get(config.area_field, "")
    options = {option for label in config.labels for option in label.form_options}
    answers = {area} if area in options else set(area.split(", "))
    return [label.name for label in config.labels if answers & set(label.form_options)]


def detect_language(text: str) -> str:
    words = re.findall(r"[a-zäöüß]+", text.lower())
    german = sum(word in GERMAN_WORDS for word in words)
    english = sum(word in ENGLISH_WORDS for word in words)
    return "de" if german > english else "en"


def issue_language(issue: dict[str, Any], comments: list[dict[str, Any]]) -> str:
    """The language of the assistant's last analysis, or the one the reporter writes in."""
    for comment in reversed(comments):
        found = marker(comment)
        if found and found[0] in {"analysis", "follow-up"} and "lang" in found[1]:
            return found[1]["lang"] if found[1]["lang"] in {"en", "de"} else "en"
    author = login(issue.get("user"))
    body = text_of(issue)
    answers = " ".join(form_fields(body).values()) or body
    texts = [answers] + [
        text_of(comment) for comment in comments if login(comment.get("user")) == author
    ]
    return detect_language(" ".join(texts))


def format_date(moment: datetime, language: str) -> str:
    if language == "de":
        return f"{moment.day}. {MONTHS_DE[moment.month - 1]} {moment.year}"
    return f"{moment:%B} {moment.day}, {moment.year}"


def follow_up_due(comments: list[dict[str, Any]], author: str) -> dict[str, Any] | None:
    """The assistant's comment with the questions that the reporter answered, when
    the assistant asked them last and may follow up; None otherwise."""
    asks = [
        (index, comment)
        for index, comment in enumerate(comments)
        if kind_of(comment) in {"analysis", "follow-up"}
    ]
    if not asks:
        return None
    index, last = asks[-1]
    found = marker(last)
    if not found or found[1].get("questions") != "1":
        return None
    if sum(kind_of(comment) == "follow-up" for comment in comments) >= MAX_FOLLOW_UPS:
        return None
    # Once the maintainer joined the conversation, the assistant stays out of it.
    if any(
        is_maintainer(comment) and login(comment.get("user")) != author
        for comment in comments[index + 1 :]
    ):
        return None
    return last


def runs_of(comment: dict[str, Any]) -> list[int]:
    """The moments, in seconds since 1970, of the AI runs that answered this comment
    of the assistant, which its marker records; nobody else can change it."""
    found = marker(comment)
    runs = found[1].get("runs", "") if found else ""
    return [int(moment) for moment in re.findall(r"\d{1,10}", runs)]


def record_run(github: GitHub, comment: dict[str, Any], at: datetime) -> None:
    """Add the run of the AI that starts at the moment to the marker of the
    assistant's comment that it answers. Only the assistant can change its
    comments, so the record holds where a reporter could delete their own."""
    runs = ".".join(str(moment) for moment in [*runs_of(comment), int(at.timestamp())])

    def recorded(match: re.Match[str]) -> str:
        others = re.sub(r" runs=\S+", "", match[2])
        return f"<!-- issue-assistant:{match[1]}{others} runs={runs} -->"

    github.edit_comment(comment["id"], MARKER.sub(recorded, text_of(comment), count=1))


# Events


@dataclass
class Plan:
    """What the event job did, and which task the engine gets next."""

    issue: int | None = None
    mode: str = "none"
    notes: list[str] = field(default_factory=list)
    # The assistant's comment that the task answers: the questions that a follow-up
    # takes up, or the release that a reply after it is about. Its marker records
    # the run once the limits let it start.
    answers: dict[str, Any] | None = None


def handle_event(
    github: GitHub, name: str, payload: dict[str, Any], config: Config
) -> Plan:
    if name == "workflow_dispatch":
        inputs = payload.get("inputs") or {}
        number = int(inputs["issue"])
        issue = github.issue(number)
        if "pull_request" in issue:
            raise AssistantError(f"#{number} is a pull request, not an issue.")
        mode = inputs.get("mode") or "triage"
        if mode not in MODES:
            raise AssistantError(f"Unknown mode {mode}.")
        add_form_labels(github, issue, config)
        return Plan(number, mode, [f"Started by hand: {mode}."])
    issue = payload["issue"]
    number = issue["number"]
    if "pull_request" in issue or is_bot(payload.get("sender")):
        return Plan(number, notes=["Pull request or bot: nothing to do."])
    action = payload.get("action")
    current = label_names(issue)
    author = login(issue.get("user"))
    if name == "issues" and action == "opened":
        add_form_labels(github, issue, config)
        if NEEDS_TRIAGE not in current:
            github.add_labels(number, [NEEDS_TRIAGE])
        if is_maintainer(issue):
            return Plan(number, notes=["Opened by a maintainer: no analysis."])
        return within_limits(
            github, Plan(number, "triage"), issue, created(issue), config
        )
    if name == "issues" and action == "edited":
        if (
            issue["state"] == "open"
            and login(payload.get("sender")) == author
            and current & {NEEDS_INFO, STALE}
        ):
            plan = answered(github, issue)
            return within_limits(
                github, plan, issue, moment(issue["updated_at"]), config
            )
        return Plan(number, notes=["Edit without a pending question."])
    if name == "issue_comment" and action == "created":
        comment = payload["comment"]
        if issue["state"] != "open":
            plan = reopen_if_answered(github, issue, comment, config)
            return within_limits(github, plan, comment, created(comment), config)
        plan = Plan(number)
        if POSSIBLE_DUPLICATE in current:
            # Every human comment after the notice stops the automatic closing.
            github.remove_label(number, POSSIBLE_DUPLICATE)
            plan.notes.append("A comment stopped the duplicate closing.")
        if login(comment.get("user")) == author:
            if current & {NEEDS_INFO, STALE}:
                plan = answered(github, issue, plan)
                return within_limits(github, plan, comment, created(comment), config)
            return plan
        if is_maintainer(comment):
            if STALE in current:
                github.remove_label(number, STALE)
            if NEEDS_INFO not in current and not is_maintainer(issue):
                plan.mode = "maintainer-reply"
        return plan
    return Plan(number, notes=[f"Event {name}.{action} is not handled."])


def add_form_labels(github: GitHub, issue: dict[str, Any], config: Config) -> None:
    missing = [
        name for name in form_labels(issue, config) if name not in label_names(issue)
    ]
    github.add_labels(issue["number"], missing)


def answered(github: GitHub, issue: dict[str, Any], plan: Plan | None = None) -> Plan:
    """The reporter answered: the issue no longer waits, and the assistant may follow up."""
    number = issue["number"]
    plan = plan or Plan(number)
    for name in (NEEDS_INFO, STALE):
        if name in label_names(issue):
            github.remove_label(number, name)
    plan.notes.append("The reporter answered.")
    asked = follow_up_due(github.comments(number), login(issue.get("user")))
    if asked:
        plan.mode = "follow-up"
        plan.answers = asked
    return plan


def within_limits(
    github: GitHub,
    plan: Plan,
    trigger: dict[str, Any],
    at: datetime,
    config: Config,
) -> Plan:
    """The plan, or none when its run of the AI would pass a limit of the day.

    `trigger` is the new issue, the edit or the comment that asks for the run, and
    `at` its time; the day is the 24 hours before. Maintainers are never limited,
    and runs by hand never get here. Every new issue of the day that someone who
    doesn't maintain the repository opened counts, also one that a limit kept from
    the AI, so that a flood of issues at once, or issues that keep the AI from
    answering, start no more runs than the limit. A new issue counts only the
    issues opened before it, and only an analysis has a limit for each reporter.
    Every follow-up and reply after a release of the day counts too: as it starts,
    the run is recorded in the marker of the assistant's comment that it answers,
    so it counts also while it still runs. Decision 0009 says why.
    """
    if plan.mode not in LIMITED or is_maintainer(trigger):
        return plan
    per_day = limit(config, "max_per_day")
    per_author = limit(config, "max_per_author_per_day")
    since = at - DAY
    triage = plan.mode == "triage"
    reporters = [
        login(item.get("user"))
        for item in github.issues_since(since)
        if since <= created(item) <= at
        and not is_bot(item.get("user"))
        and not is_maintainer(item)
        and (not triage or item["number"] < plan.issue)
    ]
    # Recording a run changes the comment, so the comments that changed since then
    # hold every recorded run of the day.
    answers = sum(
        moment >= since.timestamp()
        for comment in github.comments_since(since)
        for moment in runs_of(comment)
    )
    if len(reporters) + answers >= per_day:
        return stopped(
            plan,
            f"Daily limit of AI runs reached (max_per_day = {per_day}); "
            "the maintainer takes over.",
        )
    if triage and reporters.count(login(trigger.get("user"))) >= per_author:
        return stopped(
            plan,
            "Daily limit of analyses for this reporter reached "
            f"(max_per_author_per_day = {per_author}); the maintainer takes over.",
        )
    if plan.answers:
        record_run(github, plan.answers, at)
    return plan


def stopped(plan: Plan, note: str) -> Plan:
    """No task for the engine after all; the note says why, in the summary and as a
    notice of the run."""
    command("notice", note)
    return Plan(plan.issue, notes=[*plan.notes, note])


def reopen_if_answered(
    github: GitHub, issue: dict[str, Any], comment: dict[str, Any], config: Config
) -> Plan:
    """Reopen an issue the assistant closed once its reporter answers.

    An issue closed unanswered reopens at once; for one that a release closed, the
    engine decides whether the comment says that the problem persists, up to
    release_replies times after each release. The marker of the assistant's
    comment about the release counts these runs, so a reporter who deletes their
    comments gets no more of them.
    """
    number = issue["number"]
    author = login(issue.get("user"))
    if login(comment.get("user")) != author:
        return Plan(number, notes=["Comment on a closed issue."])
    closes = [event for event in github.events(number) if event["event"] == "closed"]
    if not closes or login(closes[-1].get("actor")) != BOT:
        return Plan(number, notes=["Closed by a person: stays closed."])
    comments = github.comments(number)
    closings = [
        item
        for item in comments
        if kind_of(item) in {"closed-unanswered", "closed-duplicate", "released"}
    ]
    if closings and kind_of(closings[-1]) == "released":
        released = closings[-1]
        if created(comment) - created(released) > REOPEN_AFTER_RELEASE:
            return Plan(number, notes=["The release closed this issue long ago."])
        replies = limit(config, "release_replies")
        if len(runs_of(released)) >= replies:
            return stopped(
                Plan(number),
                "Limit of comments read after the release reached "
                f"(release_replies = {replies}); the maintainer takes over.",
            )
        return Plan(
            number,
            "release-reply",
            ["The reporter wrote after the release."],
            answers=released,
        )
    if STALE not in label_names(issue) or not any(
        kind_of(item) == "closed-unanswered" for item in closings
    ):
        return Plan(number, notes=["Not closed for a missing answer."])
    github.reopen(number)
    for name in (STALE, NEEDS_INFO):
        github.remove_label(number, name)
    github.add_labels(number, [NEEDS_TRIAGE])
    language = issue_language(issue, comments)
    github.comment(number, render_reopened(author, language))
    plan = Plan(number, notes=["Reopened after the reporter's answer."])
    asked = follow_up_due(comments, author)
    if asked:
        plan.mode = "follow-up"
        plan.answers = asked
    return plan


# Context for the engine


@dataclass
class Visible:
    """Takes the characters that GitHub shows as nothing out of texts, and counts
    them."""

    removed: int = 0

    def __call__(self, text: str) -> str:
        text, count = INVISIBLE.subn("", text)
        self.removed += count
        return text

    def everywhere(self, value: Any) -> Any:
        """A value of JSON with each of its texts, at any depth, taken through."""
        if isinstance(value, str):
            return self(value)
        if isinstance(value, dict):
            return {key: self.everywhere(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.everywhere(item) for item in value]
        return value


def excerpt(text: str, limit: int) -> str:
    """The text of an issue in short: form answers as lines, without empty answers.

    The whole text is read, because comments and empty answers can come before the
    words that count, such as the instructions of an issue template; each step takes
    time linear in its length.
    """
    text = (text or "")[:MAX_TEXT]
    fields = form_fields(text)
    if fields:
        text = "\n".join(f"{key}: {value}" for key, value in fields.items() if value)
    comments, rest = comment_parts(text)
    text = HTML_COMMENT.sub("", comments) + rest
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def describe_author(item: dict[str, Any], reporter: str) -> str:
    name = login(item.get("user"))
    if name == BOT and marker(item):
        return "the issue assistant (you, earlier)"
    roles = []
    if name == reporter:
        roles.append("reporter")
    if is_maintainer(item):
        roles.append("maintainer")
    return f"@{name}" + (f" ({', '.join(roles)})" if roles else "")


def render_issue(
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
    visible: Visible | None = None,
) -> str:
    """issue.md: the issue and its comments, each text in a code fence of its own.

    Who wrote a text, in which role and when stands only in the headings and lines
    that the script writes outside the fences, and nothing in a text can close its
    fence, so no text can pass for another comment, for the maintainer or for the
    assistant.
    """
    visible = visible or Visible()
    reporter = login(issue.get("user"))
    lines = [
        f"# Issue #{issue['number']}: {one_line(visible(issue['title']))}",
        "",
        f"- Reporter: {describe_author(issue, reporter)}",
        f"- State: {issue['state']}",
        f"- Labels: {', '.join(sorted(label_names(issue))) or 'none'}",
        f"- Opened: {issue['created_at'][:10]}",
        "",
        "## Text of the issue",
        "",
        rendered_text(text_of(issue), visible),
    ]
    for index, comment in enumerate(comments, start=1):
        lines += [
            "",
            f"## Comment {index} by {describe_author(comment, reporter)}, "
            f"{comment['created_at'][:10]}",
            "",
            rendered_text(text_of(comment), visible),
        ]
    return "\n".join(lines) + "\n"


def rendered_text(text: str, visible: Visible) -> str:
    """A text for issue.md: without HTML and invisible characters, cut off after
    MAX_RENDERED_TEXT, and in a code fence that nothing in it can close.

    The HTML goes first, as GitHub hides it: an invisible character in the end of
    an HTML comment keeps it from ending on GitHub, and taken out first, it would end
    the comment and show the text after it to the engine. The invisible characters
    go before the fence is chosen, so that none can split a run of backticks that
    would close it.
    """
    text = visible(strip_html(text)).strip()
    if not text:
        return "(empty)"
    note = ""
    if len(text) > MAX_RENDERED_TEXT:
        text, note = text[:MAX_RENDERED_TEXT].rstrip(), "\n\n(shortened)"
    marks = fence(text)
    return f"{marks}\n{text}\n{marks}{note}"


def render_labels(config: Config) -> str:
    lines = ["# Labels you may choose", ""]
    for title, name in (("kind", "type"), ("areas", "area"), ("topics", "topic")):
        lines += [f"## {title}", ""]
        lines += [
            f"- `{label.name}`: {label.description}"
            for label in config.labels
            if label.group == name
        ] or ["- none"]
        lines.append("")
    lines.append('A kind of "spam" marks advertising or nonsense as invalid.')
    return "\n".join(lines) + "\n"


def write_context(
    github: GitHub, number: int, mode: str, config: Config
) -> dict[str, str]:
    """Write the files the engine reads and return the prompt and the schema."""
    if mode not in MODES:
        raise AssistantError(f"Unknown mode {mode}.")
    folder = config.root / CONTEXT
    issue = github.issue(number)
    comments = github.comments(number)
    folder.mkdir(parents=True, exist_ok=True)
    # The invisible characters of the issue, and of the texts that every analysis
    # reads, in which anyone could plant instructions for later issues. They go after
    # the HTML comments, as in rendered_text, so that the engine reads no text that
    # GitHub hides.
    own, elsewhere = Visible(), Visible()
    (folder / "issue.md").write_text(
        render_issue(issue, comments, own), encoding="utf-8"
    )
    (folder / "labels.md").write_text(render_labels(config), encoding="utf-8")
    others = [
        {
            "number": item["number"],
            "title": elsewhere(item["title"]),
            "state": item["state"],
            "state_reason": item.get("state_reason"),
            "labels": sorted(label_names(item)),
            "opened": item["created_at"][:10],
            "closed": (item.get("closed_at") or "")[:10] or None,
            "comments": item.get("comments", 0),
            "excerpt": elsewhere(excerpt(item.get("body") or "", 1500)),
        }
        for item in github.all_issues()
        if item["number"] != number
    ]
    (folder / "issues.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in others),
        encoding="utf-8",
    )
    discussions = [
        {
            "number": item["number"],
            "title": elsewhere(item["title"]),
            "category": (item.get("category") or {}).get("name"),
            "answered": item.get("isAnswered"),
            "closed": item.get("closed"),
            "excerpt": elsewhere(excerpt(item.get("body") or "", 1000)),
            "answer": elsewhere(
                excerpt((item.get("answer") or {}).get("body") or "", 1000)
            )
            or None,
        }
        for item in github.discussions()
    ]
    (folder / "discussions.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in discussions),
        encoding="utf-8",
    )
    if own.removed or elsewhere.removed:
        command(
            "notice",
            f"Removed {own.removed + elsewhere.removed} invisible characters, such "
            "as zero-width spaces and Unicode tags, from the texts for the engine: "
            f"{own.removed} from issue #{number}, {elsewhere.removed} from other "
            "issues and discussions.",
        )
    values = {
        "repository": config.repository,
        "issue": str(number),
        "context": str(CONTEXT),
        "release": github.latest_release() or "none yet",
    }
    prompt = "\n\n".join(
        (PROMPTS / name).read_text(encoding="utf-8")
        for name in ("rules.md", f"{mode}.md")
    )
    for key, value in values.items():
        prompt = prompt.replace("{" + key + "}", value)
    # Last, so that braces in the project's own text stay as they are.
    prompt = prompt.replace("{project}", config.project)
    schema = schema_json(mode, config)
    (folder / "prompt.md").write_text(prompt, encoding="utf-8")
    (folder / "schema.json").write_text(schema, encoding="utf-8")
    return {"prompt": prompt, "schema": schema}


# The engine's answer


def text(limit: int) -> dict[str, Any]:
    return {"type": "string", "maxLength": limit}


def strings(limit: int, items: int) -> dict[str, Any]:
    return {"type": "array", "items": text(limit), "maxItems": items}


def choice(values: list[str]) -> dict[str, Any]:
    return {"type": "string", "enum": values}


# What an issue shows that it shouldn't: nothing, personal data such as an e-mail
# address, or a secret that must be replaced.
SENSITIVE = ["none", "personal", "secret"]


def record(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


def many(values: list[str], items: int) -> dict[str, Any]:
    if not values:
        return {"type": "array", "maxItems": 0}
    return {"type": "array", "items": choice(values), "maxItems": items}


def answer_schema(mode: str, config: Config) -> dict[str, Any]:
    """The JSON schema of the engine's answer.

    Engines that can enforce a schema get it; `apply` checks every answer against it.
    """
    if mode == "maintainer-reply":
        return record(
            {"waiting_for_reporter": {"type": "boolean"}, "reason": text(280)}
        )
    if mode == "release-reply":
        return record(
            {
                "language": choice(["en", "de"]),
                "problem_persists": {"type": "boolean"},
                "reason": text(280),
            }
        )
    reference = {
        "type": "object",
        "additionalProperties": False,
        "required": ["path", "reason"],
        "properties": {
            "path": text(200),
            "line": {"type": "integer", "minimum": 1},
            "anchor": text(120),
            "reason": text(200),
        },
    }
    common = {
        "language": choice(["en", "de"]),
        "kind": choice(config.group("type") + ["spam"]),
        "areas": many(config.group("area"), 3),
        "topics": many(config.group("topic"), 2),
        "label_rationale": text(280),
    }
    if mode == "follow-up":
        return record(
            common
            | {
                "reply": text(1500),
                "missing_information": strings(400, 5),
                "references": {"type": "array", "items": reference, "maxItems": 6},
                "sensitive_data": choice(SENSITIVE),
            }
        )
    return record(
        common
        | {
            "summary": text(700),
            "analysis": text(2500),
            "next_steps": strings(400, 5),
            "missing_information": strings(400, 5),
            "duplicates": {
                "type": "array",
                "maxItems": 3,
                "items": record(
                    {
                        "number": {"type": "integer", "minimum": 1},
                        "confidence": choice(["high", "medium"]),
                        "reason": text(280),
                    }
                ),
            },
            "related": {
                "type": "array",
                "maxItems": 5,
                "items": record(
                    {
                        "kind": choice(["issue", "discussion"]),
                        "number": {"type": "integer", "minimum": 1},
                        "reason": text(280),
                    }
                ),
            },
            "references": {"type": "array", "items": reference, "maxItems": 6},
            "sensitive_data": choice(SENSITIVE),
            "security_report": {"type": "boolean"},
        }
    )


def schema_json(mode: str, config: Config) -> str:
    """The schema of the answer as one line of JSON, for the engine's command line.

    The workflow puts it between single quotes, which an apostrophe in a label name,
    as in "area: user's guide", would end. As the escape \\u0027, the apostrophe ends
    nothing and reads back as the same JSON.
    """
    schema = json.dumps(answer_schema(mode, config), separators=(",", ":"))
    return schema.replace("'", "\\u0027")


# The names of the fields of the schema; any other key is the engine's.
FIELD_NAME = re.compile(r"[a-z_]{1,40}")


def key_path(path: str, key: str) -> str:
    """Where a field of the answer is, for a message in the log.

    A key that the engine made up shows as a short JSON string, so that it can't add
    a line or a long text to the message.
    """
    if FIELD_NAME.fullmatch(key):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key if len(key) <= 40 else key[:40] + '...')}]"


def validate(schema: dict[str, Any], value: Any, path: str = "answer") -> list[str]:
    """The ways a value breaks the schema, for the subset of JSON schema used here."""
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return [f"{path} is not an object"]
        problems = [
            f"{path}.{key} is missing" for key in schema["required"] if key not in value
        ]
        problems += [
            f"{key_path(path, key)} is not expected"
            for key in value
            if key not in schema["properties"]
        ]
        for key, item in value.items():
            if key in schema["properties"]:
                problems += validate(schema["properties"][key], item, f"{path}.{key}")
        return problems
    if kind == "array":
        if not isinstance(value, list):
            return [f"{path} is not a list"]
        problems = []
        if len(value) > schema.get("maxItems", len(value)):
            problems.append(f"{path} has more than {schema['maxItems']} items")
        if "items" in schema:
            for index, item in enumerate(value):
                problems += validate(schema["items"], item, f"{path}[{index}]")
        return problems
    if kind == "string":
        if not isinstance(value, str):
            return [f"{path} is not text"]
        if "enum" in schema and value not in schema["enum"]:
            return [f"{path} is not one of {', '.join(schema['enum'])}"]
        if len(value) > schema.get("maxLength", len(value)):
            return [f"{path} is longer than {schema['maxLength']} characters"]
        return []
    if kind == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            return [f"{path} is not a whole number"]
        if value < schema.get("minimum", value):
            return [f"{path} is below {schema['minimum']}"]
        return []
    if not isinstance(value, bool):
        return [f"{path} is not true or false"]
    return []


def all_text(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from all_text(item)
    elif isinstance(value, list):
        for item in value:
            yield from all_text(item)


def reads_like_noise(token: str) -> bool:
    """Whether the letters of a token are mixed like those of a random key.

    Words, in paths and in the names of code, change between lower and upper case
    only where a word starts, and run on in lower case for several letters. The
    letters of a random key change case at about every second pair, and run on in
    lower case for two letters on average.
    """
    pairs = len(LETTER_PAIR.findall(token))
    changes = len(CASE_CHANGE.findall(token))
    runs = LOWER_CASE.findall(token)
    # At least one change in five pairs, and 3.5 letters per run on average or fewer.
    return 5 * changes >= pairs and 2 * sum(map(len, runs)) <= 7 * len(runs)


def names_a_file(token: str, paths: str) -> bool:
    """Whether the token is a part of one of the paths, one per line, that begins and
    ends at a slash or a dot.

    A token ends before the dot of an extension, as in `File.java`, and starts after
    the dot of a folder, as in `.github/`.
    """
    part = re.escape(token.strip("/"))
    return bool(re.search(rf"(?<![^\n./]){part}(?![^\n./])", paths))


def looks_secret(text: str, files: Collection[str] = ()) -> bool:
    """Whether the text holds something that looks like a token or a key.

    A known kind of token counts wherever it is. Any other run of 40 or more letters,
    digits and `+/=_-` counts when it mixes lower case, upper case and digits, and
    either has the `+` or `=` of Base64, or its letters read like noise. It doesn't
    count when it is a part of the path of one of `files`, the files of the
    repository, which are public. In a link to GitHub, the owner and the name of the
    repository, and the commit or branch of a file, are never keys.
    """
    if SECRET.search(text):
        return True
    paths = "\n".join(files)
    return any(
        re.search("[a-z]", token)
        and re.search("[A-Z]", token)
        and re.search("[0-9]", token)
        and ("+" in token or "=" in token or reads_like_noise(token))
        and not names_a_file(token, paths)
        for token in LONG_TOKEN.findall(GITHUB_REPOSITORY.sub(" ", text))
    )


def parse_answer(mode: str, raw: str, config: Config) -> dict[str, Any]:
    text = raw.strip()
    # An engine that can't enforce the schema may wrap its JSON in a code fence.
    if fenced := re.fullmatch(r"```(?:json)?[ \t]*\n(.*)\n```", text, re.DOTALL):
        text = fenced[1]
    try:
        answer = json.loads(text)
    except ValueError as error:
        raise AssistantError(f"The engine's answer is no JSON: {error}") from error
    # Before the checks, so that no invisible character splits a token that the
    # check would find, and nothing that GitHub doesn't show reaches it.
    visible = Visible()
    answer = visible.everywhere(answer)
    if visible.removed:
        command(
            "notice",
            f"Removed {visible.removed} invisible characters from the engine's answer.",
        )
    problems = validate(answer_schema(mode, config), answer)
    if problems:
        raise AssistantError(
            "The engine's answer breaks the schema: " + "; ".join(problems)
        )
    # Paths of the repository may look random and are public. Git lists them only
    # for an answer that would be refused without them.
    if any(map(looks_secret, all_text(answer))):
        files = tracked_files(config.root)
        if any(looks_secret(item, files) for item in all_text(answer)):
            raise AssistantError(
                "The engine's answer contains text that looks like a token or key; "
                "nothing was posted."
            )
    result: dict[str, Any] = answer
    return result


# Text that the engine wrote


def allowed_url(url: str, config: Config) -> bool:
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError:
        # Such as an unclosed IPv6 address: http://[
        return False
    # A backtick or backslash in a link could pair with code elsewhere in the text.
    if parts.scheme != "https" or "`" in url or "\\" in url:
        return False
    # The host alone: a user, a password or a port could hide where a link leads.
    if parts.netloc.lower() != hostname:
        return False
    # Browsers resolve . and .. in a path, also written as %2e, so a path that starts
    # in the repository could lead to any other one.
    segments = unquote(parts.path).split("/")
    if "%2e" in parts.path.lower() or "." in segments or ".." in segments:
        return False
    if hostname == "github.com":
        return bool(
            re.match(
                rf"^/{re.escape(config.repository)}/(?:blob|tree|releases)/", parts.path
            )
        )
    return hostname in config.hosts


def clean_prose(
    text: str,
    numbers: set[int],
    config: Config,
    before: str = " ",
    after: str = " ",
    *,
    lines: bool = True,
) -> str:
    """Text outside code without anything that GitHub turns into a mention, a
    reference, a foreign link, an image or HTML.

    `before` and `after` are the characters next to the text in the comment: a
    space at its edges, or the code around it, and `lines` is whether the text that
    it is part of has more than one line. First, the rules that remove something
    repeat until nothing changes, because a removal can join the characters around
    it into something new. Then the rules that keep or rewrite something repeat,
    each putting what it wrote behind a placeholder, so that no later rule changes
    it again: as they remove nothing, the characters next to a placeholder stay the
    ones they were checked with. Last, the characters that could start code, a
    link, an image, a fence or a table, escape the code written here or form an
    entity are escaped.
    """
    kept: list[str] = []

    def keep(value: str) -> str:
        kept.append(value)
        return f"\0{len(kept) - 1}\0"

    def code(value: str) -> str:
        return keep(f"`{value}`")

    def target(url: str) -> bool:
        # Markdown reads the entities in the target of a link, such as &#46; for a
        # dot, which could make a segment `..`.
        return allowed_url(url, config) and allowed_url(html.unescape(url), config)

    def link(match: re.Match[str]) -> str:
        # The link's text stays in the text, so the later rules clean it too. The
        # removals left only allowed links; should another one ever form, its
        # brackets are escaped below like any others.
        return (
            keep("[") + match[1] + keep(f"]({match[2]})")
            if target(match[2])
            else match[0]
        )

    def address(
        url: str, match: re.Match[str], starts: frozenset[str] = AUTOLINK_AFTER
    ) -> str:
        start, end = match.span()
        space = SPACE.search(match.string, end)
        rest = match.string[end : space.start() if space else None]
        # An address stays a link only where GitHub links all of it and nothing
        # more, and becomes code elsewhere: at the start or after one of `starts`
        # (other text before it, as in @www.…, leaves it text that could mention),
        # and before a space or the end, perhaps after characters that GitHub
        # leaves out of a link, such as ). and ,. Its link would run on past
        # anything else, even into code: https://docs.example.com`@evil.example/`
        # leads to evil.example.
        linked = (
            (match.string[start - 1] if start else before) in starts
            and (space[0] if space else after) in " \t\n"
            and set(rest) <= TRAILING
        )
        return keep(match[0]) if linked and allowed_url(url, config) else code(match[0])

    previous = None
    while text != previous:
        previous = text
        text = strip_html(text)
        text = LINK_DEFINITION.sub("", text)
        text = HEADING_LINE.sub("", text)
        text = IMAGE.sub(r"\1", text)
        text = MARKDOWN_LINK.sub(lambda m: m[0] if target(m[2]) else m[1], text)

    def reference(match: re.Match[str]) -> str:
        # Only a checked number stays a link, and not after an @: once the text
        # before the @ becomes code, the @ starts GitHub's text, and @GH-7 would
        # mention the user GH-7.
        start = match.start()
        checked = int(match[1]) in numbers and match.string[start - 1 : start] != "@"
        return keep(match[0]) if checked else code(match[0])

    rewrites: list[tuple[re.Pattern[str], Callable[[re.Match[str]], str]]] = [
        (MARKDOWN_LINK, link),
        (BARE_URL, lambda m: address(m[0], m)),
        (WWW_URL, lambda m: address(f"https://{m[0]}", m, WWW_AFTER)),
        (EMAIL, lambda m: code(m[0])),
        (COMMIT_REFERENCE, lambda m: code(m[0])),
        (MENTION, lambda m: code(m[0])),
        (CROSS_REFERENCE, lambda m: code(m[1])),
        (ISSUE_REFERENCE, reference),
    ]
    previous = None
    while text != previous:
        previous = text
        for pattern, rewrite in rewrites:
            text = settled(pattern, rewrite, text)
    # Brackets too: links and images can span lines and nest brackets, so no other
    # link or image than the ones kept above may form.
    for character, escaped in (
        ("\\", "\\\\"),
        ("`", "\\`"),
        ("&", "&amp;"),
        ("<", "&lt;"),
        ("[", "\\["),
        ("]", "\\]"),
    ):
        text = text.replace(character, escaped)
    # At any indentation, because trimming the cleaned text can take the
    # indentation of its first line.
    text = TILDE_FENCE.sub(r"\1\\", text)
    # No table without its delimiter row, which needs a line below the header: a
    # text of one line, such as a line of the log, keeps its hyphens.
    if lines:
        text = TABLE_HYPHEN.sub(lambda m: f"{m[1] or ''}\\-", text)
    pieces = re.split(r"\0(\d+)\0", text)
    return joined(
        kept[int(piece)] if index % 2 else piece for index, piece in enumerate(pieces)
    )


def settled(
    pattern: re.Pattern[str], rewrite: Callable[[re.Match[str]], str], text: str
) -> str:
    """The text with each match of the pattern rewritten, again until none is left.

    A rewrite can free the next match glued to it, as in @a@b; repeating one rule
    on its own spares the other rules a reading of the text for each.
    """
    previous = None
    while text != previous:
        previous, text = text, pattern.sub(rewrite, text)
    return text


def comment_parts(text: str) -> tuple[str, str]:
    """A text in two parts: up to the end of its last `-->`, and what GitHub shows of
    the rest.

    In the first part, a `-->` follows every `<!--`, so a pattern finds the end of
    each comment without reading on to the end of the text. In the rest, no comment
    can end: a `<!--` within a line stays text, as GitHub shows it, and one that
    starts a line hides the rest of the text, as GitHub hides it.
    """
    last = text.rfind("-->")
    middle = last + 3 if last >= 0 else 0
    hidden = COMMENT_LINE.search(text, middle)
    return text[:middle], text[middle : hidden.start() if hidden else len(text)]


def strip_html(text: str) -> str:
    """The text as GitHub shows it, without HTML comments and tags, in time linear
    in its length."""
    comments, rest = comment_parts(text)
    return HTML.sub(open_tag, comments) + HTML_TAG.sub(open_tag, rest)


def open_tag(match: re.Match[str]) -> str:
    """What stays of an HTML match: a tag start that no `>` closes, or nothing."""
    return match["open"] or ""


def joined(pieces: Iterable[str]) -> str:
    """The pieces in a row, with a space wherever two backtick runs would touch.

    Touching runs form one longer run, and Markdown would pair the code differently.
    """
    text = ""
    for piece in pieces:
        if text.endswith("`") and piece.startswith("`"):
            text += " "
        text += piece
    return text


def sanitize(text: str, config: Config, numbers: Iterable[int] = ()) -> str:
    """The engine's text without mentions, images, HTML, headings and foreign links.

    Code stays as it is; issue numbers stay links only when they were checked.
    """
    checked = set(numbers)
    # Before the code is found, so that what is code stays code: strip() takes some
    # control characters for spaces, which Markdown doesn't, and would turn a line
    # that starts with one into a fence after the code was found. Invisible
    # characters go too, so that no comment holds a text that nobody sees.
    text = STRAY_CONTROL.sub("", INVISIBLE.sub("", text)).strip()

    def prose(start: int, end: int) -> str:
        """The text between two pieces of code, cleaned for its place among them."""
        return clean_prose(
            text[start:end],
            checked,
            config,
            before=text[start - 1] if start else " ",
            after=text[end] if end < len(text) else " ",
            lines="\n" in text,
        )

    parts, last = [], 0
    for match in CODE.finditer(text):
        parts += [prose(last, match.start()), match[0]]
        last = match.end()
    parts.append(prose(last, len(text)))
    # Only the spaces and line breaks that the cleaning left at the ends, which
    # Markdown reads as such, too.
    return joined(parts).strip(" \t\n")


CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def one_line(text: str) -> str:
    """The text on one line: each run of blanks, line breaks and control characters,
    such as the escapes that color a terminal, becomes one space.

    Also for a place behind other text in a comment, such as `#7: `, where a fence
    would start no code block and its code would become text.
    """
    return " ".join(CONTROL.sub(" ", text).split())


def plain(text: str, config: Config, limit: int = 280) -> str:
    """The engine's text for one line outside its comments, at most `limit` long.

    Such as a line of the log and of the job summary, or a rationale that GitHub
    shows on the timeline of an issue. It is put on one line first, so that it can't
    start a workflow command or hold a block of Markdown, such as a code block whose
    mention would become text when its lines were joined later. Then it is cleaned as
    in a comment, so that it mentions nobody and links nowhere. A text that is too
    long is shortened before the cleaning too, which would otherwise lose the
    backtick that keeps a mention in its code.
    """
    line = sanitize(one_line(text), config)
    if len(line) <= limit:
        return line

    def shortened(end: int) -> str:
        return sanitize(one_line(text[:end]) + "…", config)

    # A longer start of the text can clean into a shorter line, when it closes a
    # tag for example, so the search finds a start that fits, not always the longest.
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if len(shortened(middle)) <= limit:
            low = middle
        else:
            high = middle - 1
    return shortened(low)


def tracked_files(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
    )
    return {name for name in result.stdout.decode("utf-8").split("\0") if name}


def slug(heading: str) -> str:
    """The anchor GitHub gives a heading."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[^\w\- ]", "", text.lower())
    return text.replace(" ", "-")


def headings(path: Path) -> dict[str, str]:
    """The anchors of a Markdown file with the text of their heading."""
    text = re.sub(
        r"^(```|~~~).*?^\1",
        "",
        path.read_text(encoding="utf-8"),
        flags=re.M | re.S,
    )
    found: dict[str, str] = {}
    seen: dict[str, int] = {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.MULTILINE):
        base = slug(heading)
        count = seen.get(base, 0)
        seen[base] = count + 1
        title = re.sub(r"[*_`]", "", re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading))
        found[base if count == 0 else f"{base}-{count}"] = title
    return found


# The files that GitHub shows rendered instead of as their source: Markdown and the
# other markup languages that it renders, notebooks and SVG pictures.
RENDERED = frozenset(
    {
        ".md",
        ".markdown",
        ".mdown",
        ".mkdn",
        ".mkd",
        ".rst",
        ".adoc",
        ".asciidoc",
        ".asc",
        ".textile",
        ".rdoc",
        ".org",
        ".creole",
        ".mediawiki",
        ".wiki",
        ".pod",
        ".ipynb",
        ".svg",
    }
)


def code_span(text: str) -> str:
    """The text as inline code, also when it holds backticks.

    A backslash escapes nothing in code, so the code is set off by one backtick more
    than its longest run of them, and by a blank inside where it starts or ends with
    one, which Markdown then leaves out.
    """
    longest = max((len(run) for run in re.findall("`+", text)), default=0)
    marks = "`" * (longest + 1)
    blank = " " if text[:1] == "`" or text[-1:] == "`" else ""
    return f"{marks}{blank}{text}{blank}{marks}"


def reference_link(
    reference: dict[str, Any],
    tracked: set[str],
    sha: str,
    language: str,
    config: Config,
) -> str | None:
    """A checked link to a file of the repository, or None for a path that doesn't fit.

    Code lines link to the analyzed commit, so they stay right when the file changes;
    files and headings link to the default branch. git also tracks a submodule, as a
    folder, and a link that may lead nowhere; neither is a file to link to.
    """
    path = reference["path"].strip().removeprefix("./")
    file = config.root / path
    if path not in tracked or ".." in path.split("/") or not file.is_file():
        return None
    # A blank or a bracket in the path would end the address of the link.
    address = quote(path)
    name = code_span(path)
    line = reference.get("line")
    anchor = (reference.get("anchor") or "").strip().lstrip("#").lower()
    if line:
        if line > len(file.read_text(encoding="utf-8", errors="replace").splitlines()):
            return None
        word = "Zeile" if language == "de" else "line"
        # GitHub shows a rendered file, such as Markdown, without its lines; the
        # anchor of a line works only in its source.
        source = "?plain=1" if PurePosixPath(path).suffix.lower() in RENDERED else ""
        return f"[{name}, {word} {line}]({config.blob}/{sha}/{address}{source}#L{line})"
    if anchor and path.endswith(".md"):
        titles = headings(file)
        if anchor in titles:
            return (
                f"[{titles[anchor]}]({config.blob}/HEAD/{address}#{anchor}) in {name}"
            )
    return f"[{name}]({config.blob}/HEAD/{address})"


# The heading of the notice to reporters that the footers of an analysis and of a
# follow-up link: the part on what happens with an issue, which the SUPPORT.md of
# the blueprint has, as the README advises.
NOTICE_ANCHOR = "what-happens-with-your-issue"


def notice_link(config: Config) -> str | None:
    """Where a comment tells reporters how the assistant works and who decides.

    The first file of `[transparency] files`, at its part on what happens with an
    issue when it has one; None when the file is missing, which `check` reports.
    """
    if not config.notices:
        return None
    name = config.notices[0].strip().removeprefix("./")
    parts = PurePosixPath(name).parts
    file = config.root / name
    if not parts or parts[0] == "/" or ".." in parts or not file.is_file():
        return None
    address = f"{config.blob}/HEAD/{quote(name)}"
    if name.lower().endswith(".md") and NOTICE_ANCHOR in headings(file):
        return f"{address}#{NOTICE_ANCHOR}"
    return address


# Comments of the assistant

TEXT: dict[str, dict[str, str]] = {
    "en": {
        "thanks.default": "Thanks for opening this issue, @{author}!",
        "thanks.bug": "Thanks for the report, @{author}!",
        "thanks.enhancement": "Thanks for the suggestion, @{author}!",
        "thanks.question": "Thanks for the question, @{author}!",
        "thanks.documentation": "Thanks for the note on the documentation, @{author}!",
        "thanks.compatibility": "Thanks for the compatibility report, @{author}!",
        "thanks.feedback": "Thanks for testing and for the feedback, @{author}!",
        "thanks.maintenance": "Thanks for the report, @{author}!",
        "intro": "I'm the issue assistant of this repository and took a first look. "
        "The maintainers read every issue as well.",
        "summary": "Summary",
        "analysis": "First analysis",
        "next_steps": "Worth trying",
        "missing": "Information needed",
        "missing.intro": "To continue, please add the following, in a comment or by "
        "editing the issue:",
        "missing.outro": "Without an answer, a reminder follows after 15 days and the "
        "issue closes after 30 days. If it closes, an answer reopens it.",
        "duplicate": "Possible duplicate",
        "duplicate.text": "This looks like a duplicate of #{number}: {reason}",
        "duplicate.close": "If that's right, there's nothing to do: the issue closes as "
        "a duplicate of #{number} in 3 days, so the conversation stays in one place. A "
        "👍 on #{number} shows that more people are affected. If it's something "
        "different, write a comment or react to this comment with 👎, and it stays open.",
        "related": "Related",
        "discussion": "discussion",
        "references": "Where to look",
        "links": "Where to look: {count} links",
        "sensitive.secret": "> [!WARNING]\n> This issue seems to show a secret, such "
        "as a token, a key or a password. Please remove it by editing the post, delete "
        "the earlier version from its edit history, and replace the secret, because "
        "others may have seen it.",
        "sensitive.personal": "> [!NOTE]\n> This issue seems to show personal data, "
        "such as an e-mail address, a public IP address or the address of a private "
        "server. If you'd rather not show it, edit the post and delete the earlier "
        "version from its edit history.",
        "security": "> [!CAUTION]\n> This may describe a security vulnerability. Please "
        "report it privately, as described in the [security policy]({policy}), and "
        "remove the details here.",
        "footer": "<sub>🤖 Automated first analysis by {engine}, based on the code, the "
        "documentation and earlier issues. It can be wrong; the maintainers "
        "decide.{labels} {feedback}</sub>",
        "labels": " Labels: {labels}.",
        "about": "[How the assistant works]({notice})",
        "feedback": "A 👍 or 👎 on this comment tells the maintainers whether it helped.",
        "feedback.duplicate": "A 👍 or 👎 on this comment tells the maintainers "
        "whether it helped; a 👎 also keeps the issue open.",
        "follow-up.thanks": "Thanks for your answer, @{author}!",
        "follow-up.missing": "Still needed",
        "follow-up.missing.intro": "Please also add:",
        "follow-up.footer": "<sub>🤖 Automated follow-up by {engine}. It can be wrong; "
        "the maintainers decide. {feedback}</sub>",
        "reminder": "@{author}, a friendly reminder: this issue is still waiting for "
        "your answer to {questions}. Without an answer, it closes automatically on "
        "{date}. A short comment keeps it open, for example if you need more time.",
        "reminder.questions": "the questions above",
        "closed-unanswered": "I'm closing this issue because the information asked for "
        "above has been missing for 30 days. @{author}, as soon as you answer here, it "
        "reopens automatically. Thanks for reporting!",
        "closed-duplicate": "Closed as a duplicate of #{number}, as announced three days "
        "ago. Please continue there; a 👍 on #{number} shows that more people are "
        "affected. If this was a mistake, write a comment and the maintainers will "
        "reopen it.",
        "reopened": "Thanks for your answer, @{author}! I reopened the issue; the "
        "maintainers will take another look.",
        "fixed": "Fixed by #{pull}, which is now on `{branch}`. The fix ships with the "
        "next release; this issue closes then, with a link to it.",
        "released": "🎉 Released in [{tag}]({url}){fixes}. @{author}, please update to "
        "this version. If the problem persists, write a comment here and the issue "
        "reopens, or open a new issue.",
        "released.fixes": " with the fix from {pulls}",
        "reopened-release": "Thanks for letting us know, @{author}, and sorry that it "
        "isn't solved yet. I reopened the issue; the maintainers will take another "
        "look.",
    },
    "de": {
        "thanks.default": "Danke für dein Issue, @{author}!",
        "thanks.bug": "Danke für deine Meldung, @{author}!",
        "thanks.enhancement": "Danke für deinen Vorschlag, @{author}!",
        "thanks.question": "Danke für deine Frage, @{author}!",
        "thanks.documentation": "Danke für deinen Hinweis zur Dokumentation, @{author}!",
        "thanks.compatibility": "Danke für deinen Kompatibilitätsbericht, @{author}!",
        "thanks.feedback": "Danke fürs Testen und für dein Feedback, @{author}!",
        "thanks.maintenance": "Danke für deine Meldung, @{author}!",
        "intro": "Ich bin der Issue-Assistent dieses Repositorys und habe mir das zuerst "
        "angesehen. Die Maintainer lesen jedes Issue ebenfalls.",
        "summary": "Zusammenfassung",
        "analysis": "Erste Analyse",
        "next_steps": "Das kannst du ausprobieren",
        "missing": "Fehlende Informationen",
        "missing.intro": "Damit es weitergehen kann, ergänze bitte Folgendes, als "
        "Kommentar oder indem du das Issue bearbeitest:",
        "missing.outro": "Ohne Antwort folgt nach 15 Tagen eine Erinnerung, nach 30 "
        "Tagen wird das Issue geschlossen. Ist es geschlossen, öffnet eine Antwort es "
        "wieder.",
        "duplicate": "Mögliches Duplikat",
        "duplicate.text": "Das sieht nach einem Duplikat von #{number} aus: {reason}",
        "duplicate.close": "Falls das stimmt, musst du nichts tun: Das Issue wird in 3 "
        "Tagen als Duplikat von #{number} geschlossen, damit alles an einer Stelle "
        "bleibt. Ein 👍 bei #{number} zeigt, dass mehr Leute betroffen sind. Geht es um "
        "etwas anderes, schreib einen Kommentar oder reagiere auf diesen Kommentar mit "
        "👎, dann bleibt es offen.",
        "related": "Verwandte Themen",
        "discussion": "Diskussion",
        "references": "Zum Nachlesen",
        "links": "Zum Nachlesen: {count} Links",
        "sensitive.secret": "> [!WARNING]\n> Dieses Issue scheint ein Geheimnis zu "
        "zeigen, etwa ein Token, einen Schlüssel oder ein Passwort. Bitte entferne es, "
        "indem du den Beitrag bearbeitest, lösche die frühere Fassung aus dem "
        "Bearbeitungsverlauf und ersetze das Geheimnis, denn andere könnten es gesehen "
        "haben.",
        "sensitive.personal": "> [!NOTE]\n> Dieses Issue scheint persönliche Daten zu "
        "zeigen, etwa eine E-Mail-Adresse, eine öffentliche IP-Adresse oder die Adresse "
        "eines privaten Servers. Wenn du sie lieber nicht zeigen willst, bearbeite den "
        "Beitrag und lösche die frühere Fassung aus dem Bearbeitungsverlauf.",
        "security": "> [!CAUTION]\n> Das könnte eine Sicherheitslücke beschreiben. Bitte "
        "melde so etwas vertraulich, wie in der [Sicherheitsrichtlinie]({policy}) "
        "beschrieben, und entferne die Details hier.",
        "footer": "<sub>🤖 Automatische Erstanalyse von {engine} auf Grundlage des Codes, "
        "der Dokumentation und früherer Issues. Sie kann falsch sein; die Maintainer "
        "entscheiden.{labels} {feedback}</sub>",
        "labels": " Labels: {labels}.",
        "about": "[So arbeitet der Assistent]({notice})",
        "feedback": "Ein 👍 oder 👎 auf diesen Kommentar zeigt den Maintainern, ob er "
        "geholfen hat.",
        "feedback.duplicate": "Ein 👍 oder 👎 auf diesen Kommentar zeigt den "
        "Maintainern, ob er geholfen hat; ein 👎 hält das Issue außerdem offen.",
        "follow-up.thanks": "Danke für deine Antwort, @{author}!",
        "follow-up.missing": "Noch offen",
        "follow-up.missing.intro": "Bitte ergänze noch:",
        "follow-up.footer": "<sub>🤖 Automatische Rückmeldung von {engine}. Sie kann "
        "falsch sein; die Maintainer entscheiden. {feedback}</sub>",
        "reminder": "@{author}, eine freundliche Erinnerung: Dieses Issue wartet noch "
        "auf deine Antwort auf {questions}. Ohne Antwort wird es am {date} "
        "automatisch geschlossen. Ein kurzer Kommentar genügt, damit es offen bleibt, "
        "etwa wenn du mehr Zeit brauchst.",
        "reminder.questions": "die Fragen oben",
        "closed-unanswered": "Ich schließe dieses Issue, weil die erbetenen "
        "Informationen seit 30 Tagen fehlen. @{author}, sobald du hier antwortest, wird "
        "es automatisch wieder geöffnet. Danke für deine Meldung!",
        "closed-duplicate": "Als Duplikat von #{number} geschlossen, wie vor drei Tagen "
        "angekündigt. Bitte mach dort weiter; ein 👍 bei #{number} zeigt, dass mehr "
        "Leute betroffen sind. Falls das ein Irrtum war, schreib einen Kommentar, dann "
        "öffnen die Maintainer es wieder.",
        "reopened": "Danke für deine Antwort, @{author}! Ich habe das Issue wieder "
        "geöffnet; die Maintainer sehen es sich erneut an.",
        "fixed": "Behoben durch #{pull}, jetzt auf `{branch}`. Der Fix kommt mit dem "
        "nächsten Release; dann wird dieses Issue mit einem Link darauf geschlossen.",
        "released": "🎉 Veröffentlicht in [{tag}]({url}){fixes}. @{author}, bitte "
        "aktualisiere auf diese Version. Besteht das Problem weiter, schreib hier einen "
        "Kommentar, dann wird das Issue wieder geöffnet, oder eröffne ein neues Issue.",
        "released.fixes": " mit dem Fix aus {pulls}",
        "reopened-release": "Danke für die Rückmeldung, @{author}, und schade, dass es "
        "noch nicht gelöst ist. Ich habe das Issue wieder geöffnet; die Maintainer "
        "sehen es sich erneut an.",
    },
}


def say(language: str, key: str, **values: object) -> str:
    return TEXT[language][key].format(**values)


def thanks(language: str, kind: str, author: str) -> str:
    key = f"thanks.{kind}"
    return say(
        language, key if key in TEXT[language] else "thanks.default", author=author
    )


def bullets(items: Iterable[str]) -> str:
    """A Markdown list of the items, each with all its lines.

    The lines after the first are indented under the bullet: without, the first of
    them would end the list item, and a code block in it with the item, so that the
    rest of the code became text. Tabs that indent a line become spaces, because
    under the bullet they would reach other tab stops than in the item alone.
    """
    return "\n".join(
        "- " + CONTINUATION.sub(lambda m: "\n  " + m[1].expandtabs(4), item)
        for item in items
    )


@dataclass
class Findings:
    """The engine's answer after the checks, ready to post."""

    duplicate: dict[str, Any] | None = None
    duplicate_reason: str = ""
    related: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    numbers: set[int] = field(default_factory=set)


# More links than this, to related issues and to files, fold into one line that
# opens them, so that the questions for the reporter stay in view.
MAX_OPEN_LINKS = 3


def link_sections(language: str, related: list[str], references: list[str]) -> str:
    """The lists of the related issues and of the files to look at, folded when they
    hold more than MAX_OPEN_LINKS links; empty without any."""
    sections = [
        f"### {say(language, key)}\n\n{bullets(items)}"
        for key, items in (("related", related), ("references", references))
        if items
    ]
    count = len(related) + len(references)
    if count <= MAX_OPEN_LINKS:
        return "\n\n".join(sections)
    # The blank lines let GitHub read the Markdown inside the HTML.
    return (
        f"<details><summary>{say(language, 'links', count=count)}</summary>\n\n"
        + "\n\n".join(sections)
        + "\n\n</details>"
    )


def invitation(language: str, config: Config, *, duplicate: bool = False) -> str:
    """The end of a footer: a link to how the assistant works, and the request for a
    👍 or 👎, which on a duplicate notice also objects to the closing."""
    notice = notice_link(config)
    ask = say(language, "feedback.duplicate" if duplicate else "feedback")
    return f"{say(language, 'about', notice=notice)} · {ask}" if notice else ask


def render_analysis(
    answer: dict[str, Any],
    author: str,
    findings: Findings,
    asks: list[str],
    labels: list[str],
    config: Config,
    engine: str = DEFAULT_ENGINE,
) -> str:
    language = answer["language"]
    numbers = findings.numbers

    def clean(value: str) -> str:
        return sanitize(value, config, numbers)

    parts = [
        render_marker(
            "analysis",
            lang=language,
            questions=int(bool(asks)),
            engine=engine,
            **(
                {"duplicate": findings.duplicate["number"]}
                if findings.duplicate
                else {}
            ),
        ),
        f"{thanks(language, answer['kind'], author)} {say(language, 'intro')}",
    ]
    if answer["sensitive_data"] != "none":
        parts.append(say(language, f"sensitive.{answer['sensitive_data']}"))
    if answer["security_report"]:
        parts.append(say(language, "security", policy=config.policy))
    else:
        if findings.duplicate:
            number = findings.duplicate["number"]
            parts.append(
                f"### {say(language, 'duplicate')}\n\n"
                + say(
                    language,
                    "duplicate.text",
                    number=number,
                    reason=clean(one_line(findings.duplicate_reason)),
                )
                + "\n\n"
                + say(language, "duplicate.close", number=number)
            )
        parts.append(f"### {say(language, 'summary')}\n\n{clean(answer['summary'])}")
        parts.append(f"### {say(language, 'analysis')}\n\n{clean(answer['analysis'])}")
        steps = [clean(step) for step in answer["next_steps"] if step.strip()]
        if steps:
            parts.append(f"### {say(language, 'next_steps')}\n\n{bullets(steps)}")
        if asks:
            parts.append(
                f"### {say(language, 'missing')}\n\n{say(language, 'missing.intro')}\n\n"
                f"{bullets(clean(ask) for ask in asks)}\n\n{say(language, 'missing.outro')}"
            )
        links = link_sections(language, findings.related, findings.references)
        if links:
            parts.append(links)
    shown = ", ".join(f"`{name}`" for name in labels)
    parts.append(
        say(
            language,
            "footer",
            labels=say(language, "labels", labels=shown) if labels else "",
            engine=ENGINES[engine].name,
            feedback=invitation(language, config, duplicate=bool(findings.duplicate)),
        )
    )
    return "\n\n".join(parts) + "\n"


def render_follow_up(
    answer: dict[str, Any],
    author: str,
    references: list[str],
    asks: list[str],
    config: Config,
    engine: str = DEFAULT_ENGINE,
    numbers: Collection[int] = (),
) -> str:
    """The follow-up on an answer of the reporter; `numbers` are the issues and
    discussions that exist, which its text may link."""
    language = answer["language"]
    parts = [
        render_marker(
            "follow-up", lang=language, questions=int(bool(asks)), engine=engine
        ),
        say(language, "follow-up.thanks", author=author),
    ]
    if answer["sensitive_data"] != "none":
        parts.append(say(language, f"sensitive.{answer['sensitive_data']}"))
    parts.append(sanitize(answer["reply"], config, numbers))
    if asks:
        parts.append(
            f"### {say(language, 'follow-up.missing')}\n\n"
            f"{say(language, 'follow-up.missing.intro')}\n\n"
            f"{bullets(sanitize(ask, config, numbers) for ask in asks)}\n\n"
            f"{say(language, 'missing.outro')}"
        )
    links = link_sections(language, [], references)
    if links:
        parts.append(links)
    parts.append(
        say(
            language,
            "follow-up.footer",
            engine=ENGINES[engine].name,
            feedback=invitation(language, config),
        )
    )
    return "\n\n".join(parts) + "\n"


def render_reminder(
    author: str, language: str, closes: datetime, questions: str = ""
) -> str:
    """The reminder of the questions, which links the comment that asked them at
    the address `questions`, when it is known."""
    date = format_date(closes, language)
    asked = say(language, "reminder.questions")
    if questions:
        asked = f"[{asked}]({questions})"
    return (
        f"{render_marker('reminder')}\n"
        f"{say(language, 'reminder', author=author, date=date, questions=asked)}\n"
    )


def render_closed_unanswered(author: str, language: str) -> str:
    return (
        f"{render_marker('closed-unanswered')}\n"
        f"{say(language, 'closed-unanswered', author=author)}\n"
    )


def render_closed_duplicate(number: int, language: str) -> str:
    return (
        f"{render_marker('closed-duplicate', of=number)}\n"
        f"{say(language, 'closed-duplicate', number=number)}\n"
    )


def render_reopened(author: str, language: str) -> str:
    return f"{render_marker('reopened')}\n{say(language, 'reopened', author=author)}\n"


def render_fixed(pull: int, branch: str, language: str) -> str:
    return (
        f"{render_marker('fixed', pull=pull)}\n"
        f"{say(language, 'fixed', pull=pull, branch=branch)}\n"
    )


def render_released(
    author: str, release: dict[str, Any], pulls: list[int], language: str
) -> str:
    joined = ", ".join(f"#{pull}" for pull in pulls)
    fixes = say(language, "released.fixes", pulls=joined) if pulls else ""
    text_ = say(
        language,
        "released",
        tag=release["tag_name"],
        url=release["html_url"],
        fixes=fixes,
        author=author,
    )
    tag = release["tag_name"]
    attributes = {"tag": tag} if re.fullmatch(r"[\w.+/-]+", tag) else {}
    return f"{render_marker('released', **attributes)}\n{text_}\n"


def render_reopened_release(author: str, language: str) -> str:
    return (
        f"{render_marker('reopened')}\n"
        f"{say(language, 'reopened-release', author=author)}\n"
    )


# Applying the engine's answer


def other_issue(github: GitHub, number: int, current: int) -> dict[str, Any] | None:
    """Another issue of the repository, or None for a pull request or a wrong number."""
    if number == current:
        return None
    try:
        issue = github.issue(number)
    except GitHubError as error:
        if not error.missing():
            raise
        return None
    return None if "pull_request" in issue else issue


# How many of the issue numbers that the engine's texts name are looked up, each
# with a request or two, so that those that exist stay links.
MAX_NAMED = 10
# The largest number that GitHub's GraphQL API takes, an Int of 32 bits; no issue
# has a larger one.
MAX_NUMBER = 2**31 - 1


def named_numbers(
    github: GitHub, current: int, texts: Iterable[str], known: Collection[int]
) -> set[int]:
    """The other issues and discussions that the texts name, as #123 or GH-123, and
    that exist: of the first MAX_NAMED that the checks didn't find already.

    The cleaning keeps a checked number as a link and turns any other into code, so
    that a number that the engine made up refers to nothing.
    """
    named = dict.fromkeys(
        int(match[1]) for text in texts for match in ISSUE_REFERENCE.finditer(text)
    )
    unknown = [
        number
        for number in named
        if 0 < number <= MAX_NUMBER and number != current and number not in known
    ]
    return {
        number
        for number in unknown[:MAX_NAMED]
        if other_issue(github, number, current) is not None
        or github.discussion_exists(number)
    }


def vouched_for(original: dict[str, Any]) -> bool:
    """Whether a maintainer opened the issue or triaged it, so that another issue may
    close as its duplicate.

    Every analysis reads the text of every issue, so anyone could open one that tells
    the engine to call later issues its duplicates, and close them.
    """
    return is_maintainer(original) or NEEDS_TRIAGE not in label_names(original)


def check_findings(
    github: GitHub,
    issue: dict[str, Any],
    answer: dict[str, Any],
    sha: str,
    config: Config,
) -> Findings:
    """Keep the duplicates and related issues that exist, and the files that fit.

    A sure duplicate of an open, older issue that a maintainer opened or triaged gets
    the notice and closes later; the first other candidate becomes a suggestion in
    GitHub's panel for the maintainer.
    """
    number = issue["number"]
    language = answer["language"]
    findings = Findings()
    related: list[tuple[int, str, str]] = []
    suggested = False
    for candidate in answer["duplicates"]:
        original = other_issue(github, candidate["number"], number)
        # Only an older issue can be the original, so two issues never close each other.
        if not original or original["number"] > number:
            continue
        if original["number"] in findings.numbers:
            continue
        findings.numbers.add(original["number"])
        if (
            candidate["confidence"] == "high"
            and original["state"] == "open"
            and vouched_for(original)
            and not findings.duplicate
        ):
            findings.duplicate = original
            findings.duplicate_reason = candidate["reason"]
            continue
        if not suggested and (
            original["state"] == "open" or original.get("state_reason") == "completed"
        ):
            github.suggest_duplicate(
                issue, original, plain(candidate["reason"], config)
            )
            suggested = True
        related.append((original["number"], "issue", candidate["reason"]))
    for item in answer["related"]:
        if item["number"] in findings.numbers or item["number"] == number:
            continue
        if item["kind"] == "issue":
            exists = other_issue(github, item["number"], number) is not None
        else:
            exists = github.discussion_exists(item["number"])
        if exists:
            findings.numbers.add(item["number"])
            related.append((item["number"], item["kind"], item["reason"]))
    findings.numbers |= named_numbers(
        github,
        number,
        [
            answer["summary"],
            answer["analysis"],
            *answer["next_steps"],
            *answer["missing_information"],
            findings.duplicate_reason,
            *(reason for _, _, reason in related),
        ],
        findings.numbers,
    )
    findings.related = [
        f"#{entry}"
        + (f" ({say(language, 'discussion')})" if kind == "discussion" else "")
        + f": {sanitize(one_line(reason), config, findings.numbers)}"
        for entry, kind, reason in related
    ]
    findings.references = references(answer["references"], sha, language, config)
    return findings


def references(
    items: list[dict[str, Any]], sha: str, language: str, config: Config
) -> list[str]:
    tracked = tracked_files(config.root)
    links: dict[str, str] = {}
    for item in items:
        link = reference_link(item, tracked, sha, language, config)
        if link and link not in links:
            links[link] = sanitize(one_line(item["reason"]), config)
    return [f"{link}: {reason}" for link, reason in links.items()]


# What GitHub shows next to the labels of a possible security report, instead of
# the engine's words, which could describe the vulnerability in public.
SECURITY_RATIONALE = "Possible security report; see the security policy."


def label_rationale(answer: dict[str, Any], config: Config) -> str:
    """Why the labels fit, for the timeline of the issue."""
    if answer.get("security_report"):
        return SECURITY_RATIONALE
    return plain(answer["label_rationale"], config)


def apply_labels(
    github: GitHub,
    issue: dict[str, Any],
    answer: dict[str, Any],
    config: Config,
    keep: Collection[str] = (),
) -> list[str]:
    """Correct the kind while nobody triaged the issue and add areas and topics.

    The labels in `keep` stay as they are, added or removed: someone changed them.
    """
    current = label_names(issue)
    kind = answer["kind"]
    if NEEDS_TRIAGE in current:
        for name in config.group("type"):
            if name in current and name != kind and name not in keep:
                github.remove_label(issue["number"], name)
    wanted = [kind, *answer["areas"], *answer["topics"]]
    added = [
        name
        for name in dict.fromkeys(wanted)
        if name not in current and name not in keep
    ]
    github.add_explained_labels(issue, added, label_rationale(answer, config))
    return added


def earlier_post(
    mode: str, comments: list[dict[str, Any]], author: str
) -> dict[str, Any] | None:
    """The comment of an earlier run of the same event, which a run again repeats."""
    if mode == "triage":
        analyses = [comment for comment in comments if kind_of(comment) == "analysis"]
        return analyses[-1] if analyses else None
    conversation = [
        comment
        for comment in comments
        if kind_of(comment) in {"analysis", "follow-up"}
        or login(comment.get("user")) == author
    ]
    if conversation and kind_of(conversation[-1]) == "follow-up":
        return conversation[-1]
    return None


def complete_labels(
    github: GitHub,
    issue: dict[str, Any],
    answer: dict[str, Any],
    config: Config,
    comments: list[dict[str, Any]],
    posted: dict[str, Any],
) -> list[str]:
    """Add the labels that belong to a comment of an earlier run, and are missing.

    GitHub can stop a run after its comment: before `needs-info`, which comes
    after the question, or, in a run of an earlier version, before any label. A
    label that anyone added or removed since the comment stays as it is. Whether
    the issue waits for its reporter, or is a possible duplicate, follows the
    comment, which said so, not the new answer: the reporter can already have
    answered it.
    """
    number = issue["number"]
    since = created(posted)
    changed = {
        (event.get("label") or {}).get("name", "")
        for event in github.events(number)
        if event["event"] in {"labeled", "unlabeled"} and created(event) >= since
    }
    added = apply_labels(github, issue, answer, config, keep=changed)
    _, said = marker(posted) or ("", {})
    author = login(issue.get("user"))
    answered = any(
        login(comment.get("user")) == author and created(comment) > since
        for comment in comments
    )
    lifecycle = [
        name
        for name, wanted in (
            (NEEDS_INFO, said.get("questions") == "1" and not answered),
            (POSSIBLE_DUPLICATE, "duplicate" in said),
        )
        if wanted and name not in label_names(issue) and name not in changed
    ]
    github.add_labels(number, lifecycle)
    return added + lifecycle


def apply_answer(
    github: GitHub,
    mode: str,
    number: int,
    raw: str,
    config: Config,
    sha: str,
    *,
    repeat: bool = False,
    engine: str = DEFAULT_ENGINE,
) -> list[str]:
    """Check the engine's answer and post it; `repeat` allows another analysis by hand."""
    engine = engine_named(engine)
    answer = parse_answer(mode, raw, config)
    issue = github.issue(number)
    if "pull_request" in issue:
        raise AssistantError(f"#{number} is a pull request, not an issue.")
    if mode == "release-reply":
        return apply_release_reply(github, issue, answer, config)
    if issue["state"] != "open" or issue.get("locked"):
        return [f"#{number} is closed or locked; nothing posted."]
    author = login(issue.get("user"))
    if mode == "maintainer-reply":
        return apply_maintainer_reply(github, issue, answer, config)
    if not repeat:
        comments = github.comments(number)
        posted = earlier_post(mode, comments, author)
        if posted is not None:
            # An earlier run posted the comment: never a second one.
            added = []
            if answer["kind"] != "spam":
                added = complete_labels(github, issue, answer, config, comments, posted)
            if not added:
                return [f"#{number} already has this {mode}; nothing posted."]
            return [
                f"#{number} already has this {mode}; no second comment, "
                f"labels added: {', '.join(added)}."
            ]
    if answer["kind"] == "spam":
        github.add_explained_labels(issue, [INVALID], label_rationale(answer, config))
        return [f"#{number} looks like spam: marked invalid, no comment."]
    asks = [
        ask.strip()
        for ask in answer["missing_information"]
        if ask.strip() and answer["kind"] in config.asking_kinds()
    ]
    # The labels come before the comment, so that a run that GitHub stops leaves no
    # comment without its labels; the run again posts the comment. Only `needs-info`
    # comes after it: without the question, the sweep would remind the reporter of
    # one that nobody asked, and close the issue. A run again adds it, as the
    # comment says.
    if mode == "follow-up":
        body = render_follow_up(
            answer,
            author,
            references(answer["references"], sha, answer["language"], config),
            asks,
            config,
            engine,
            named_numbers(github, number, [answer["reply"], *asks], ()),
        )
        added = apply_labels(github, issue, answer, config)
        github.comment(number, body)
        if asks and NEEDS_INFO not in label_names(issue):
            github.add_labels(number, [NEEDS_INFO])
        return [f"Follow-up on #{number}; labels added: {', '.join(added) or 'none'}."]
    if answer["security_report"]:
        # Nothing about a possible vulnerability is discussed in public.
        asks = []
        findings = Findings()
    else:
        findings = check_findings(github, issue, answer, sha, config)
    planned = [
        name
        for name in dict.fromkeys([answer["kind"], *answer["areas"], *answer["topics"]])
        if name not in label_names(issue)
    ]
    apply_labels(github, issue, answer, config)
    # Without its notice, `possible-duplicate` closes nothing.
    duplicate = [POSSIBLE_DUPLICATE] if findings.duplicate else []
    github.add_labels(number, duplicate)
    github.comment(
        number,
        render_analysis(answer, author, findings, asks, planned, config, engine),
    )
    waiting = [NEEDS_INFO] if asks and NEEDS_INFO not in label_names(issue) else []
    github.add_labels(number, waiting)
    lifecycle = waiting + duplicate
    return [
        f"Analysis on #{number}; labels added: {', '.join(planned + lifecycle) or 'none'}."
    ]


def apply_maintainer_reply(
    github: GitHub, issue: dict[str, Any], answer: dict[str, Any], config: Config
) -> list[str]:
    number = issue["number"]
    reason = plain(answer["reason"], config)
    if not answer["waiting_for_reporter"]:
        return [f"#{number} does not wait for the reporter: {reason}"]
    if NEEDS_INFO in label_names(issue):
        return [f"#{number} already waits for the reporter."]
    author = login(issue.get("user"))
    comments = github.comments(number)
    asked = [
        created(comment)
        for comment in comments
        if is_maintainer(comment) and login(comment.get("user")) != author
    ]
    if asked and any(
        login(comment.get("user")) == author and created(comment) > asked[-1]
        for comment in comments
    ):
        return [f"The reporter of #{number} already answered."]
    github.add_labels(number, [NEEDS_INFO])
    return [f"#{number} waits for the reporter: {reason}"]


def apply_release_reply(
    github: GitHub, issue: dict[str, Any], answer: dict[str, Any], config: Config
) -> list[str]:
    """Reopen an issue that a release closed when its reporter says it isn't fixed."""
    number = issue["number"]
    reason = plain(answer["reason"], config)
    if issue["state"] == "open" or issue.get("locked"):
        return [f"#{number} is open or locked; nothing to reopen."]
    if not answer["problem_persists"]:
        return [f"#{number} stays closed: {reason}"]
    github.reopen(number)
    github.add_labels(number, [NEEDS_TRIAGE])
    github.comment(
        number, render_reopened_release(login(issue.get("user")), answer["language"])
    )
    return [f"#{number} reopened: {reason}"]


# The daily sweep


# Runs a step of the sweep, named for the log, and returns what it did.
Isolate = Callable[[str, Callable[[], list[str]]], list[str]]


def directly(name: str, step: Callable[[], list[str]]) -> list[str]:
    """Run the step; an error of GitHub stops everything."""
    return step()


def sweep(github: GitHub, now: datetime, releases: bool = True) -> list[str]:
    """The whole lifecycle; every run does all of it, so a missed run loses nothing.

    Every step and every issue runs on its own: one that GitHub fails becomes a
    warning, the others go on, and SweepIncomplete names it at the end. Each step
    can run twice, so the next sweep completes what one stopped.
    """
    failed: list[str] = []

    def isolate(name: str, step: Callable[[], list[str]]) -> list[str]:
        start = len(github.writes)
        try:
            return step()
        except GitHubError as error:
            failure = f"{name}: {error}"
            command("warning", failure)
            failed.append(failure)
            # What the step wrote before GitHub stopped it, and the write that failed.
            return github.writes[start:]

    done = []
    if releases:
        done += isolate(
            "Marking the fixed issues", partial(mark_fixed, github, now, isolate)
        )
        done += isolate(
            "Closing the released issues", partial(close_released, github, isolate)
        )
    for label, step in (
        (NEEDS_INFO, sweep_waiting),
        (POSSIBLE_DUPLICATE, sweep_duplicate),
    ):
        done += isolate(
            f"The issues labeled {label}",
            partial(sweep_labeled, github, label, step, now, isolate),
        )
    if failed:
        raise SweepIncomplete(done, failed)
    return done


# How far back the summary of the sweep counts the 👍 and 👎 that the footers of the
# analyses and follow-ups ask for.
FEEDBACK_PERIOD = timedelta(days=30)


def feedback(github: GitHub, now: datetime) -> str:
    """The 👍 and 👎 on the analyses and follow-ups of the last 30 days, for the
    summary of the sweep: numbers only, which tell the maintainers whether the
    assistant helps.

    GitHub counts the reactions of each comment in its list of the comments, so
    one list of the comments of the repository since then is enough. Counting is no
    step of the lifecycle: one that GitHub fails is a warning, and the sweep passes.
    """
    since = now - FEEDBACK_PERIOD
    try:
        comments = github.comments_since(since)
    except GitHubError as error:
        command("warning", f"Counting the feedback: {error}")
        return "Feedback not counted: GitHub failed."
    counts = {"analysis": [0, 0, 0], "follow-up": [0, 0, 0]}
    for comment in comments:
        tally = counts.get(kind_of(comment) or "")
        if tally is None or created(comment) < since:
            continue
        reactions = comment.get("reactions") or {}
        tally[0] += 1
        tally[1] += int(reactions.get("+1") or 0)
        tally[2] += int(reactions.get("-1") or 0)
    (analyses, up, down), (follow_ups, up_after, down_after) = counts.values()
    return (
        f"Feedback of the last {FEEDBACK_PERIOD.days} days: analyses {analyses}, "
        f"👍 {up}, 👎 {down}; follow-ups {follow_ups}, 👍 {up_after}, 👎 {down_after}."
    )


def sweep_labeled(
    github: GitHub,
    label: str,
    step: Callable[[GitHub, dict[str, Any], datetime], list[str]],
    now: datetime,
    isolate: Isolate,
) -> list[str]:
    done = []
    for issue in github.open_issues(label):
        if not issue.get("locked"):
            done += isolate(f"#{issue['number']}", partial(step, github, issue, now))
    return done


def posted_since(comments: list[dict[str, Any]], kind: str, since: datetime) -> bool:
    """Whether the assistant posted a comment of the kind since the moment.

    It did when GitHub stopped an earlier sweep after the comment; this one
    completes the step without a second comment.
    """
    return any(
        kind_of(comment) == kind and created(comment) >= since for comment in comments
    )


def first_release(
    github: GitHub,
    releases: list[dict[str, Any]],
    shas: list[str],
    since: datetime,
) -> dict[str, Any] | None:
    """The first release after `since` that contains every merge commit in `shas`."""
    for release in releases:
        if moment(release["published_at"]) <= since:
            continue
        if all(github.contains(sha, release["tag_name"]) for sha in shas):
            return release
    return None


def mark_fixed(github: GitHub, now: datetime, isolate: Isolate = directly) -> list[str]:
    """Label the open issues that a recently merged pull request fixes.

    The issue stays open until a release ships the fix. A maintainer who removes the
    label keeps it removed: the notice of a pull request is never repeated.
    """
    branch, pulls = github.merged_pulls(now - MERGE_LOOKBACK)
    # Read once, by the first issue to mark.
    releases = cache(github.releases)
    done = []
    for pull in sorted(pulls, key=lambda item: item["mergedAt"]):
        if pull["baseRefName"] != branch:
            continue
        for reference in pull["closingIssuesReferences"]["nodes"]:
            # Most issues that a merge names are closed by the time of a sweep, and
            # each one read would spend the requests that the token has in an
            # hour; the read of an open one decides.
            if not github.owns(reference) or reference["state"] == "CLOSED":
                continue
            number = reference["number"]
            done += isolate(
                f"#{number}",
                partial(mark_issue_fixed, github, number, pull, branch, releases),
            )
    return done


def mark_issue_fixed(
    github: GitHub,
    number: int,
    pull: dict[str, Any],
    branch: str,
    releases: Callable[[], list[dict[str, Any]]],
) -> list[str]:
    try:
        issue = github.issue(number)
    except GitHubError as error:
        # An issue that was deleted or moved to another repository.
        if not error.missing():
            raise
        return []
    if issue["state"] != "open" or issue.get("locked"):
        return []
    current = label_names(issue)
    comments = github.comments(number)
    # Nor is a fix announced again that a release has shipped and closed the issue
    # with since its merge, such as at once, without the notice: an issue reopened
    # after it says that the fix didn't help.
    if (
        FIXED in current
        or any(
            (found := marker(comment))
            and found[0] == "fixed"
            and found[1].get("pull") == str(pull["number"])
            for comment in comments
        )
        or posted_since(comments, "released", moment(pull["mergedAt"]))
    ):
        return []
    # The labels before anything else: the label closes the issue with the
    # release, and every later sweep skips an issue that has it or the notice. So
    # the issue stops waiting for its reporter first, or the sweep would remind the
    # reporter of a fixed issue and close it unanswered. The notice is the only
    # thing a run can lose, when GitHub fails it or the releases read before it;
    # the release closes the issue anyway.
    for name in (NEEDS_INFO, STALE):
        if name in current:
            github.remove_label(number, name)
    github.add_labels(number, [FIXED])
    # A sweep that sees the merge late, as the run of the merge failed, may find a
    # release that ships the fix already: then the issue closes with it at once,
    # without a notice that promises the next release.
    shipped = pull["mergeCommit"] and first_release(
        github, releases(), [pull["mergeCommit"]["oid"]], moment(pull["mergedAt"])
    )
    marked = {
        **issue,
        "labels": [
            {"name": name} for name in sorted(current - {NEEDS_INFO, STALE} | {FIXED})
        ],
    }
    # Unless another pull request that fixes the issue waits for its release.
    if shipped and (closed := close_issue_released(github, marked, releases())):
        return closed
    github.comment(
        number,
        render_fixed(pull["number"], branch, issue_language(issue, comments)),
    )
    return [f"#{number}: fixed by #{pull['number']}; closes with the next release."]


def close_released(github: GitHub, isolate: Isolate = directly) -> list[str]:
    """Close the fixed issues whose fix a published release ships, with its link."""
    issues = [issue for issue in github.open_issues(FIXED) if not issue.get("locked")]
    if not issues:
        return []
    releases = github.releases()
    done = []
    for issue in issues:
        done += isolate(
            f"#{issue['number']}",
            partial(close_issue_released, github, issue, releases),
        )
    return done


def close_issue_released(
    github: GitHub, issue: dict[str, Any], releases: list[dict[str, Any]]
) -> list[str]:
    number = issue["number"]
    events = github.events(number)
    label = max(
        (
            event
            for event in events
            if event["event"] == "labeled"
            and (event.get("label") or {}).get("name") == FIXED
        ),
        key=created,
        default=None,
    )
    labeled = created(label or issue)
    comments = cache(partial(github.comments, number))
    # A fix that a release closed the issue with before it was last reopened
    # didn't help. Any other reopening, such as of an issue that GitHub closed at
    # the merge, leaves the fixes as they are.
    reopened = max(
        (created(event) for event in events if event["event"] == "reopened"),
        default=None,
    )
    spent = reopened and max(
        (
            created(comment)
            for comment in comments()
            if kind_of(comment) == "released" and created(comment) <= reopened
        ),
        default=None,
    )
    # The fixes that the label waits for: merged after that release, and at most
    # MERGE_LOOKBACK before the label, the latest that a sweep sets it, with the
    # time of its run.
    pulls = [
        pull
        for pull in github.fixing_pulls(number)
        if not (spent and moment(pull["mergedAt"]) <= spent)
        and labeled - moment(pull["mergedAt"]) <= MERGE_LOOKBACK + LABEL_SLACK
    ]
    # The first release that ships them all, published after the last of them was
    # merged: also before the label, when a sweep set it late. Without one, the
    # label promises the next release after it, and so does a label that someone
    # set by hand, such as after a fix that fell short: never a release before it.
    since = max((moment(pull["mergedAt"]) for pull in pulls), default=labeled)
    if label and login(label.get("actor")) != BOT:
        since = max(since, labeled)
    release = first_release(
        github, releases, [pull["mergeCommit"]["oid"] for pull in pulls], since
    )
    if not release:
        return []
    if not posted_since(comments(), "released", since):
        github.comment(
            number,
            render_released(
                login(issue.get("user")),
                release,
                sorted(pull["number"] for pull in pulls),
                issue_language(issue, comments()),
            ),
        )
    # Closed first: with the label, the next sweep finds an issue that GitHub
    # didn't close.
    github.close(
        issue,
        "COMPLETED",
        f"Fixed and released in {release['tag_name']} on "
        f"{release['published_at'][:10]}.",
    )
    for name in (FIXED, NEEDS_TRIAGE, NEEDS_INFO, STALE):
        if name in label_names(issue):
            github.remove_label(number, name)
    try:
        github.set_milestone(number, release["tag_name"], release["published_at"])
    except GitHubError as error:
        # The milestone only files the issue; the release closed it in any case.
        command("notice", f"No milestone {release['tag_name']} on #{number}: {error}")
    return [f"#{number}: closed, released in {release['tag_name']}."]


def sweep_waiting(github: GitHub, issue: dict[str, Any], now: datetime) -> list[str]:
    """Remind the reporter after 15 days, close after 30 days without an answer."""
    number = issue["number"]
    author = login(issue.get("user"))
    comments = github.comments(number)
    labeled = [
        created(event)
        for event in github.events(number)
        if event["event"] == "labeled"
        and (event.get("label") or {}).get("name") == NEEDS_INFO
    ]
    asked = [
        created(comment)
        for comment in comments
        if (is_maintainer(comment) and login(comment.get("user")) != author)
        or kind_of(comment) in {"analysis", "follow-up"}
    ]
    since = max(labeled + asked or [created(issue)])
    current = label_names(issue)
    if any(
        login(comment.get("user")) == author and created(comment) > since
        for comment in comments
    ):
        # The event workflow missed the answer, for example while it was switched off.
        for name in (NEEDS_INFO, STALE):
            if name in current:
                github.remove_label(number, name)
        return [f"#{number}: the reporter answered; no longer waiting."]
    reminders = [
        created(comment)
        for comment in comments
        if kind_of(comment) == "reminder" and created(comment) >= since
    ]
    language = issue_language(issue, comments)
    if reminders:
        if now - since >= CLOSE_AFTER and now - reminders[-1] >= WARNING_PERIOD:
            if not posted_since(comments, "closed-unanswered", reminders[-1]):
                github.comment(number, render_closed_unanswered(author, language))
            github.close(
                issue,
                "NOT_PLANNED",
                f"No answer to the questions for {(now - since).days} days; "
                f"reminded on {reminders[-1]:%Y-%m-%d}.",
            )
            return [
                f"#{number}: closed after {(now - since).days} days without an answer."
            ]
        if STALE not in current:
            github.add_labels(number, [STALE])
        return []
    if STALE in current:
        # A new question started a new waiting period.
        github.remove_label(number, STALE)
    if now - since >= REMIND_AFTER:
        questions = questions_address(github, issue, comments)
        github.comment(
            number, render_reminder(author, language, now + WARNING_PERIOD, questions)
        )
        github.add_labels(number, [STALE])
        return [f"#{number}: reminded the reporter after {(now - since).days} days."]
    return []


def questions_address(
    github: GitHub, issue: dict[str, Any], comments: list[dict[str, Any]]
) -> str:
    """The address of the comment with the questions that the reporter is to answer.

    That is the last comment that asked, of a maintainer or the assistant's analysis
    or follow-up; empty when that one asked the reporter nothing, or GitHub gave it
    no address on this issue.
    """
    author = login(issue.get("user"))
    asking = [
        comment
        for comment in comments
        if (is_maintainer(comment) and login(comment.get("user")) != author)
        or kind_of(comment) in {"analysis", "follow-up"}
    ]
    if not asking:
        return ""
    found = marker(asking[-1])
    address = str(asking[-1].get("html_url") or "")
    expected = (
        rf"https://github\.com/{re.escape(github.repository)}"
        rf"/issues/{issue['number']}#issuecomment-\d+"
    )
    if (found and found[1].get("questions") != "1") or not re.fullmatch(
        expected, address, re.IGNORECASE
    ):
        return ""
    return address


def sweep_duplicate(github: GitHub, issue: dict[str, Any], now: datetime) -> list[str]:
    """Close a possible duplicate 3 days after the notice, unless someone objected."""
    number = issue["number"]
    author = login(issue.get("user"))
    comments = github.comments(number)
    notices = [
        comment
        for comment in comments
        if (found := marker(comment))
        and found[0] == "analysis"
        and "duplicate" in found[1]
    ]
    if not notices:
        return []
    notice = notices[-1]
    if now - created(notice) < DUPLICATE_GRACE:
        return []
    # A comment of anyone objects, and a thumbs down of the reporter or of anyone
    # who may push to the repository: in the repository of an organization, the
    # owner is the organization, which never reacts.
    objected = any(
        created(comment) > created(notice) and not is_bot(comment.get("user"))
        for comment in comments
    ) or any(
        reaction["content"] == "-1"
        and (
            login(reaction.get("user")) == author
            or github.may_push(login(reaction.get("user")))
        )
        for reaction in github.reactions(notice["id"])
    )
    original = other_issue(github, int(marker(notice)[1]["duplicate"]), number)  # type: ignore[index]
    # A notice from before an original had to be vouched for closes nothing.
    if (
        objected
        or not original
        or original["state"] != "open"
        or not vouched_for(original)
    ):
        github.remove_label(number, POSSIBLE_DUPLICATE)
        return [
            f"#{number}: stays open, the duplicate notice was objected to or is outdated."
        ]
    language = issue_language(issue, comments)
    if not posted_since(comments, "closed-duplicate", created(notice)):
        github.comment(number, render_closed_duplicate(original["number"], language))
    github.close(
        issue,
        "DUPLICATE",
        f"Duplicate of #{original['number']}, announced on "
        f"{created(notice):%Y-%m-%d} without objection.",
        duplicate_of=original,
    )
    github.remove_label(number, POSSIBLE_DUPLICATE)
    for name in (NEEDS_TRIAGE, NEEDS_INFO, STALE):
        if name in label_names(issue):
            github.remove_label(number, name)
    github.add_labels(number, [DUPLICATE])
    return [f"#{number}: closed as a duplicate of #{original['number']}."]


# Labels as code


def sync_labels(github: GitHub, config: Config) -> list[str]:
    """Create and update the labels of labels.toml; never delete one.

    GitHub ignores the case of label names, so a label that it has in another case,
    such as Bug for bug, is renamed; creating it would fail.
    """
    existing = {label["name"].casefold(): label for label in github.labels()}
    done = []
    for label in config.labels:
        current = existing.get(label.name.casefold())
        if current is None:
            github.create_label(label)
            done.append(f"created {label.name}")
        elif current["name"] != label.name:
            github.update_label(label, current["name"])
            done.append(f"renamed {current['name']} to {label.name}")
        elif (current.get("color") or "").lower() != label.color or (
            current.get("description") or ""
        ) != label.description:
            github.update_label(label)
            done.append(f"updated {label.name}")
    configured = {label.name.casefold() for label in config.labels}
    for name in sorted(
        label["name"] for key, label in existing.items() if key not in configured
    ):
        command("notice", f"Label {name} is not in {LABELS_FILE}; it stays.")
    return done


# Findings of code scanning


def watch_findings(github: GitHub, root: Path) -> tuple[list[str], int]:
    """Dismiss the accepted findings and count the others, which stay open.

    The summary of a public repository's run is public, while code scanning alerts
    are only shown to maintainers: the lines name an open alert by its number and
    link only, never its rule, file or message. An accepted finding is named, since
    .github/findings.toml names it anyway.
    """
    accepted = load_findings(root)
    alerts = github.open_alerts()
    if alerts is None:
        return ["Code scanning isn't set up; there is nothing to check."], 0
    done, left = [], []
    for alert in sorted(alerts, key=lambda item: item["number"]):
        acceptance = next((item for item in accepted if item.covers(alert)), None)
        if acceptance is None:
            left.append(alert)
            continue
        github.dismiss_alert(
            alert["number"],
            acceptance.reason,
            f"{acceptance.comment} (accepted in {FINDINGS_FILE})",
        )
        done.append(
            f"Alert #{alert['number']} ({acceptance.tool} {acceptance.rule}) "
            f"dismissed as {acceptance.reason}."
        )
    done += [f"Alert #{alert['number']} is open: {alert['html_url']}" for alert in left]
    if left:
        done.append(
            f"Fix these findings, or accept them with a reason in {FINDINGS_FILE}."
        )
    return done, len(left)


# Checking a repository's set-up


def load_yaml(path: Path, where: str = "") -> Any:
    """A YAML file as data, with PyYAML or with yq, which GitHub's runners have.

    A file that isn't YAML is a SettingsError that names it as `where`.
    """
    try:
        import yaml
    except ImportError:
        try:
            result = subprocess.run(
                ["yq", "-o=json", ".", str(path)],
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError:
            raise AssistantError(
                "Reading the workflows needs PyYAML or yq; install one of them, "
                "for example with: pip install pyyaml"
            ) from None
        except subprocess.CalledProcessError as error:
            reason = error.stderr.strip()
        else:
            return json.loads(result.stdout)
    else:
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError) as error:
            reason = str(error)
    raise SettingsError([f"{where or path} is no valid YAML: {reason}"])


def normalized(node: Any, problems: list[str], where: str) -> Any:
    """A workflow without the refs of its actions, which must be commit hashes.

    PyYAML reads the key `on` as True and yq as "on"; both become "on".
    """
    if isinstance(node, dict):
        result = {}
        for key, value in node.items():
            name = "on" if key is True else key
            if name == "uses" and isinstance(value, str):
                action, _, ref = value.partition("@")
                if not SHA.fullmatch(ref):
                    problems.append(
                        f"{where}: pin {action} to a commit hash, not '{ref}'"
                    )
                result[name] = action
            else:
                result[name] = normalized(value, problems, where)
        return result
    if isinstance(node, list):
        return [normalized(item, problems, where) for item in node]
    return node


def check_labels(config: Config) -> list[str]:
    problems = []
    # GitHub ignores the case of label names: bug and Bug are one label for it.
    spellings: dict[str, list[str]] = {}
    for label in config.labels:
        spellings.setdefault(label.name.casefold(), []).append(label.name)
    for names in sorted(spellings.values()):
        if len(set(names)) > 1:
            problems.append(
                f"{LABELS_FILE}: the labels {', '.join(dict.fromkeys(names))} differ "
                "only in case, which GitHub ignores"
            )
        elif len(names) > 1:
            problems.append(f"{LABELS_FILE}: the label {names[0]} is defined twice")
    for label in config.labels:
        if not 0 < len(label.name) <= MAX_LABEL_NAME:
            problems.append(
                f"{LABELS_FILE}: the name '{label.name}' has {len(label.name)} "
                f"characters; GitHub takes 1 to {MAX_LABEL_NAME}"
            )
        if not re.fullmatch(r"[0-9a-f]{6}", label.color):
            problems.append(f"{LABELS_FILE}: {label.name} needs a color like 'd73a4a'")
        if not 0 < len(label.description) <= 100:
            problems.append(
                f"{LABELS_FILE}: {label.name} needs a description of 1 to 100 characters"
            )
        if label.group not in GROUPS:
            problems.append(
                f"{LABELS_FILE}: {label.name} has the unknown group {label.group}"
            )
        if label.form_options and label.group not in {"type", "area", "topic"}:
            problems.append(
                f"{LABELS_FILE}: only kinds, areas and topics have form_options"
            )
    lifecycle = {label.name for label in config.labels if label.group == "lifecycle"}
    for name in LIFECYCLE:
        if name == FIXED and config.releases is False:
            continue
        if name not in lifecycle:
            problems.append(f"{LABELS_FILE}: the lifecycle label {name} is missing")
    if not config.group("type"):
        problems.append(f"{LABELS_FILE}: at least one label needs the group type")
    return problems


def check_forms(config: Config) -> list[str]:
    problems = []
    defined = {label.name for label in config.labels}
    options: list[str] = []
    for path in sorted((config.root / ".github/ISSUE_TEMPLATE").glob("*.y*ml")):
        if path.stem == "config":
            continue
        where = path.relative_to(config.root).as_posix()
        try:
            form = load_yaml(path, where)
        except SettingsError as error:
            problems += error.problems
            continue
        if not isinstance(form, dict):
            problems.append(f"{where} is no issue form")
            continue
        names = form.get("labels") or []
        # GitHub takes a list of names or one text of them separated by commas.
        if isinstance(names, str):
            names = [name.strip() for name in names.split(",") if name.strip()]
        for name in names:
            if name not in defined:
                problems.append(f"{where}: the label {name} is not in {LABELS_FILE}")
        for item in form.get("body") or []:
            attributes = item.get("attributes") or {}
            if (
                item.get("type") == "dropdown"
                and attributes.get("label") == config.area_field
            ):
                options += attributes.get("options") or []
    for option in dict.fromkeys(options):
        owners = [label.name for label in config.labels if option in label.form_options]
        if len(owners) > 1:
            problems.append(
                f"{LABELS_FILE}: the form option '{option}' belongs to {', '.join(owners)}"
            )
        if not owners and option not in config.unmapped_options:
            problems.append(f"{LABELS_FILE}: no label has the form option '{option}'")
    for label in config.labels:
        for option in label.form_options:
            if option not in options:
                problems.append(
                    f"{LABELS_FILE}: {label.name} names the option '{option}', which no form has"
                )
    return problems


def check_workflows(config: Config) -> list[str]:
    problems: list[str] = []
    for name in TEMPLATE_WORKFLOWS:
        template = TEMPLATES / WORKFLOWS / name
        path = config.root / WORKFLOWS / name
        where = str(WORKFLOWS / name)
        if not path.is_file():
            problems.append(f"{where} is missing; run the install command")
            continue
        expected = normalized(load_yaml(template), [], template.name)
        actual = normalized(load_yaml(path, where), problems, where)
        if actual != expected:
            problems.append(
                f"{where} differs from the template of the issue assistant; "
                "only the refs of its actions may differ"
            )
    firewall = config.root / FIREWALL
    if not firewall.is_file():
        problems.append(f"{FIREWALL} is missing; run the install command")
    else:
        policy = load_yaml(firewall, str(FIREWALL))
        needed = set(load_yaml(TEMPLATES / FIREWALL)["allow"])
        hosts = set(policy.get("allow") or [])
        if policy.get("mode") != "enforce":
            problems.append(f"{FIREWALL}: mode must be enforce")
        if policy.get("no-default-urls"):
            problems.append(f"{FIREWALL}: the assistant needs GitHub's default hosts")
        for host in sorted(needed - hosts):
            problems.append(f"{FIREWALL}: the engine needs the host {host}")
        for host in sorted(hosts):
            if "*" in host or "/" in host:
                problems.append(f"{FIREWALL}: name the host {host} in full")
    actionlint = config.root / ACTIONLINT
    if actionlint.is_file():
        labels = (
            (load_yaml(actionlint, str(ACTIONLINT)) or {}).get("self-hosted-runner")
            or {}
        ).get("labels") or []
        if FIREWALL_RUNNER not in labels:
            problems.append(f"{ACTIONLINT}: list the runner {FIREWALL_RUNNER}")
    ignore = config.root / ".gitignore"
    if not ignore.is_file() or f"/{CONTEXT}/" not in ignore.read_text("utf-8"):
        problems.append(f".gitignore: add /{CONTEXT}/")
    return problems


def check_config(config: Config) -> list[str]:
    problems = []
    if config.engine not in ENGINES:
        problems.append(
            f"{SETTINGS / 'config.toml'}: unknown engine {config.engine}; "
            f"known engines: {', '.join(sorted(ENGINES))}"
        )
    for host in sorted(config.hosts):
        if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", host):
            problems.append(f"{SETTINGS / 'config.toml'}: {host} is no host name")
    if config.model and not is_model_name(config.model):
        problems.append(model_problem(config.model))
    for key in LIMITS:
        try:
            limit(config, key)
        except AssistantError as error:
            problems.append(str(error))
    if config.engine in ENGINES:
        engine = ENGINES[config.engine]
        for name in config.notices:
            path = config.root / name
            if not path.is_file():
                problems.append(
                    f"{name} is missing; it tells reporters who reads their issue"
                )
                continue
            content = path.read_text(encoding="utf-8")
            if engine.name not in content or engine.vendor not in content:
                problems.append(
                    f"{name}: say that {engine.name} by {engine.vendor} reads the issues"
                )
    return problems


def check_accepted_findings(config: Config) -> list[str]:
    try:
        accepted = load_findings(config.root)
    except SettingsError as error:
        return error.problems
    problems = []
    for item in accepted:
        where = f"{FINDINGS_FILE}: {item.tool} {item.rule}"
        if not item.tool.strip() or not item.rule.strip():
            problems.append(f"{FINDINGS_FILE}: name the tool and the rule")
        if item.reason not in DISMISS_REASONS:
            problems.append(
                f"{where}: the reason must be one of {', '.join(DISMISS_REASONS)}"
            )
        if not 0 < len(item.comment.strip()) <= 200:
            problems.append(f"{where}: explain it in a comment of 1 to 200 characters")
    return problems


def check_project(config: Config) -> list[str]:
    """project.md must tell the AI what the project is, which it reads first."""
    where = SETTINGS / "project.md"
    starter = (TEMPLATES / where).read_text(encoding="utf-8").splitlines()[0]
    if starter in config.project or "ONE SENTENCE" in config.project:
        return [
            f"{where}: replace the starter's first line with what the project does "
            "and for whom; the AI reads this file before every task"
        ]
    if len(config.project) < MIN_PROJECT:
        return [
            f"{where}: describe the project in at least {MIN_PROJECT} characters: "
            "what it does, for whom, and where its code and documentation are"
        ]
    return []


def check(config: Config) -> list[str]:
    """Everything that keeps the repository's set-up safe and consistent.

    The problems of reading the settings come first; `load_config` found them.
    """
    return (
        list(config.problems)
        + check_project(config)
        + check_labels(config)
        + check_forms(config)
        + check_workflows(config)
        + check_config(config)
        + check_accepted_findings(config)
    )


# Setting up a repository


def install(root: Path, ref: str, version: str) -> list[str]:
    """Write the workflows and the firewall policy of the templates into a repository.

    Labels, project.md and config.toml are only written when they are missing.
    """
    if not SHA.fullmatch(ref):
        raise AssistantError("Give the commit hash of the issue assistant's release.")
    done = []
    for name in TEMPLATE_WORKFLOWS:
        target = root / WORKFLOWS / name
        target.parent.mkdir(parents=True, exist_ok=True)
        content = ACTION_PIN.sub(
            f"{ACTION}@{ref} # {version}",
            (TEMPLATES / WORKFLOWS / name).read_text(encoding="utf-8"),
        )
        target.write_text(content, encoding="utf-8", newline="\n")
        done.append(f"wrote {WORKFLOWS / name}")
    firewall = root / FIREWALL
    if firewall.is_file():
        done.append(f"kept {FIREWALL}; check that it allows the template's hosts")
    else:
        shutil.copyfile(TEMPLATES / FIREWALL, firewall)
        done.append(f"wrote {FIREWALL}")
    for starter in STARTERS:
        target = root / starter
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(TEMPLATES / starter, target)
            done.append(f"wrote {starter}; adapt it")
    ignore = root / ".gitignore"
    entry = f"/{CONTEXT}/"
    lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.is_file() else []
    if entry not in lines:
        ignore.write_text(
            "\n".join([*lines, entry]) + "\n", encoding="utf-8", newline="\n"
        )
        done.append(f"added {entry} to .gitignore")
    return done


# Command line


def output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        print(f"{name}={value}")
        return
    with open(path, "a", encoding="utf-8") as handle:
        if "\n" in value:
            delimiter = f"EOF_{os.urandom(8).hex()}"
            handle.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
        else:
            handle.write(f"{name}={value}\n")


def fence(text: str) -> str:
    """The backticks of a code fence that nothing in the text can close: more than
    in any run of backticks in it."""
    longest = max((len(run) for run in re.findall("`+", text)), default=0)
    return "`" * max(3, longest + 1)


def summary(
    title: str, lines: list[str], dry_run: bool, previews: Iterable[str] = ()
) -> None:
    text = f"### {title}{' (dry run)' if dry_run else ''}\n\n" + (
        bullets(lines) if lines else "Nothing to do."
    )
    for preview in previews:
        body = preview.rstrip()
        marks = fence(body)
        text += (
            "\n\n<details><summary>Comment</summary>\n\n"
            f"{marks}markdown\n{body}\n{marks}\n\n</details>"
        )
    # The lines and the comments hold the engine's text, which the runner must not
    # read as workflow commands; it reads them again after the random token. Only a
    # terminal outside a runner, such as a check on one's own computer, shows the
    # text alone: the runner reads every other output, also that of a container.
    if sys.stdout.isatty() and os.environ.get("GITHUB_ACTIONS") != "true":
        print(text)
    else:
        token = os.urandom(16).hex()
        print(f"::stop-commands::{token}\n{text}\n::{token}::")
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def choose_engine(config: Config, ready: str) -> tuple[str, bool, str]:
    """The engine of config.toml, whether its secret exists, and its model.

    `ready` lists the engines whose secret the workflow found, separated by commas.
    """
    key = engine_named(config.engine)
    model = config.model or ENGINES[key].model
    if not is_model_name(model):
        raise AssistantError(model_problem(model))
    available = key in {item.strip() for item in ready.split(",") if item.strip()}
    if not available:
        command(
            "notice",
            f"No credentials for {ENGINES[key].name} are configured; "
            "the issue assistant only sets labels and keeps the lifecycle.",
        )
    return key, available, model


def repository_of_run(*, needed: bool) -> str:
    """The repository the run looks after: GH_REPO, or else GITHUB_REPOSITORY.

    Only the commands that call GitHub need it; a name that is given must fit.
    """
    name = os.environ.get("GH_REPO") or os.environ.get("GITHUB_REPOSITORY", "")
    if (name or needed) and not REPOSITORY_NAME.fullmatch(name):
        raise AssistantError(
            "GH_REPO or GITHUB_REPOSITORY must name the repository as owner/name"
            + (f", not {name!r}." if name else ".")
        )
    return name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("event", help="handle the GitHub event of this run")
    commands.add_parser("engine", help="name the engine and whether it can run")
    for name, text_ in (
        ("context", "write the files the engine reads"),
        ("apply", "check the engine's answer in RESULT and apply it"),
    ):
        task = commands.add_parser(name, help=text_)
        task.add_argument("--issue", type=int, default=os.environ.get("ISSUE") or None)
        task.add_argument(
            "--mode", choices=MODES, default=os.environ.get("MODE") or None
        )
    commands.add_parser(
        "sweep",
        help="remind, close unanswered issues and duplicates, and fixed issues "
        "with the release",
    )
    commands.add_parser("sync-labels", help="create and update the labels")
    commands.add_parser(
        "findings",
        help="dismiss the accepted findings of code scanning and fail while others "
        "are open",
    )
    commands.add_parser("check", help="check the repository's set-up")
    setup = commands.add_parser("install", help="write the workflows into a repository")
    setup.add_argument("--ref", required=True, help="commit hash of the release")
    setup.add_argument("--version", required=True, help="release tag, such as v1.0.0")
    arguments = parser.parse_args(argv)

    # ISSUE_ASSISTANT_ROOT points the script at another checkout, as in the action's
    # own smoke test.
    root = Path(
        os.environ.get("ISSUE_ASSISTANT_ROOT")
        or os.environ.get("GITHUB_WORKSPACE")
        or os.getcwd()
    ).resolve()
    # A fixture stands in for the issue of context and apply, which then write
    # nothing; see Fixture.
    fixture_name = os.environ.get(FIXTURE_VARIABLE, "")
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true" or fixture_name != ""
    github: GitHub | None = None
    title = f"Issue assistant: {arguments.command}"
    try:
        # Every other command would work on the real repository.
        if fixture_name and arguments.command not in {"context", "apply"}:
            raise AssistantError(
                f"{FIXTURE_VARIABLE} is for context and apply; "
                f"unset it for {arguments.command}."
            )
        if arguments.command == "install":
            for line in install(root, arguments.ref, arguments.version):
                print(line)
            return 0
        # check and engine read only the checkout, so they run anywhere, also on a
        # computer without GH_REPO; the other commands call GitHub.
        repository = repository_of_run(
            needed=arguments.command not in {"check", "engine"}
        )
        if arguments.command == "check":
            problems = check(load_config(root, repository, lenient=True))
            for problem in problems:
                command("error", problem)
            summary("Issue assistant set-up", problems or ["Everything fits."], False)
            return 1 if problems else 0
        if arguments.command == "engine":
            key, available, model = choose_engine(
                load_config(root, repository), os.environ.get("READY_ENGINES", "")
            )
            output("engine", key)
            output("ready", str(available).lower())
            output("model", model)
            return 0
        github = GitHub(repository, dry_run=dry_run)
        if arguments.command == "findings":
            # Independent of the settings of the issues.
            lines, left = watch_findings(github, root)
            for line in lines:
                if line.startswith("Alert #") and " is open: " in line:
                    command("error", line)
            summary("Findings", lines or ["No open findings."], dry_run)
            return 1 if left else 0
        config = load_config(root, repository)
        if fixture_name:
            fixture = read_fixture(root, fixture_name)
            if arguments.issue not in {None, fixture.number}:
                raise AssistantError(
                    f"The fixture {fixture_name} holds issue #{fixture.number}, "
                    f"not #{arguments.issue}."
                )
            arguments.issue = fixture.number
            github.use_fixture(fixture)
            command(
                "notice",
                f"Issue #{fixture.number} comes from the fixture {fixture_name}; "
                "nothing is written.",
            )
        if arguments.command == "apply":
            title = f"Issue assistant: {arguments.mode} of #{arguments.issue}"
        if arguments.command in {"context", "apply"} and not (
            arguments.issue and arguments.mode
        ):
            raise AssistantError(f"{arguments.command} needs an issue and a mode.")
        if arguments.command == "context":
            written = write_context(github, arguments.issue, arguments.mode, config)
            output("prompt", written["prompt"])
            output("schema", written["schema"])
            return 0
        if arguments.command == "event":
            name = os.environ["GITHUB_EVENT_NAME"]
            payload = json.loads(
                Path(os.environ["GITHUB_EVENT_PATH"]).read_text("utf-8")
            )
            plan = handle_event(github, name, payload, config)
            output("issue", str(plan.issue or ""))
            output("mode", plan.mode)
            title = f"Issue assistant: #{plan.issue}, next task {plan.mode}"
            done = plan.notes + github.writes
        elif arguments.command == "apply":
            done = apply_answer(
                github,
                arguments.mode,
                arguments.issue,
                os.environ.get("RESULT", ""),
                config,
                os.environ.get("GITHUB_SHA", "HEAD"),
                repeat=os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch",
                engine=os.environ.get("ENGINE", ""),
            )
            done += github.writes
        elif arguments.command == "sweep":
            title = "Issue lifecycle"
            now = datetime.now(UTC)
            try:
                done = sweep(github, now, config.releases)
            except SweepIncomplete as incomplete:
                command("error", f"{incomplete}; the next sweep tries again.")
                summary(
                    title,
                    incomplete.done
                    + [f"Failed: {failure}" for failure in incomplete.failed]
                    + [feedback(github, now)],
                    dry_run,
                    github.previews,
                )
                return 1
            done.append(feedback(github, now))
        else:
            title = "Labels"
            done = sync_labels(github, config)
    except SettingsError as error:
        for problem in error.problems:
            command("error", problem)
        return 1
    except AssistantError as error:
        command("error", str(error))
        return 1
    except GitHubError as error:
        # One error, not a traceback, and what the run wrote before it, which a
        # run again doesn't repeat.
        command("error", f"GitHub failed, so the run stopped: {error}")
        # Only the commands that call GitHub can fail on it, and they have `github`.
        writes = github.writes if github else []
        summary(
            title,
            [*writes, "Stopped: GitHub failed; running it again completes the rest."],
            dry_run,
            github.previews if github else (),
        )
        return 1
    summary(title, done, dry_run, github.previews)
    return 0


if __name__ == "__main__":
    sys.exit(main())
