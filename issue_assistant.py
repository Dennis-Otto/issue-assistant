"""Issue assistant: labels, a first analysis, duplicates and unanswered questions.

The GitHub Action in action.yml runs this script in the repository it looks after.
That repository keeps its labels in .github/labels.toml, a description of the
project and its settings in .github/issue-assistant/, and copies of the three
workflows with which this repository looks after its own issues; see README.md.

An AI engine from ENGINES writes the analysis. It only reads: the checkout and a
context folder that `context` writes, with the prompt and the JSON schema of its
answer. The answer reaches GitHub only through `apply`, which checks it against the
schema, the labels and the issues that exist, and which runs in a job without the
engine. Reminders, closing, reopening and the labels of the issue forms work
without one.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

TOOL = Path(__file__).resolve().parent
PROMPTS = TOOL / "prompts"
# The starter files a repository gets, in the places they go.
TEMPLATES = TOOL / "templates"
# Files of the repository the assistant looks after, relative to its root.
LABELS_FILE = Path(".github/labels.toml")
SETTINGS = Path(".github/issue-assistant")
CONTEXT = Path(".issue-assistant")
WORKFLOWS = Path(".github/workflows")
FIREWALL = Path(".github/egress-firewall.yaml")
ACTIONLINT = Path(".github/actionlint.yaml")
FINDINGS_FILE = Path(".github/findings.toml")
# Files that install writes only when a repository doesn't have them yet.
STARTERS = (
    LABELS_FILE,
    SETTINGS / "project.md",
    SETTINGS / "config.toml",
    FINDINGS_FILE,
)
FIREWALL_RUNNER = "ubuntu-24.04-firewall"
# The workflows with which this repository looks after its own issues are the
# templates: install copies them, with the commit hash of the release in the pin of
# the action, and check compares a repository's workflows with them.
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
# How long a comment of the reporter can reopen an issue that a release closed.
REOPEN_AFTER_RELEASE = timedelta(days=30)

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

MODES = ("triage", "follow-up", "maintainer-reply", "release-reply")

MARKER = re.compile(r"<!-- issue-assistant:([a-z-]+)((?: [a-z]+=[\w.+/-]+)*) -->")
# Code that Markdown shows as it is, by the rules of CommonMark: a fenced block from
# a fence line to a closing fence line of the same character and at least the same
# length, and a code span within one line between backtick runs of the same length
# that no other backtick touches. A span over several lines can be cut by a fence
# or a quote on the next line, so it, like anything else that doesn't fit these
# rules exactly, counts as text: the safe side.
CODE = re.compile(
    r"^ {0,3}(?P<fence>(?P<char>[`~])(?P=char){2,})[^`\n]*\n.*?"
    r"^ {0,3}(?P=fence)(?P=char)*[ \t]*$"
    r"|(?<![`\\])(?P<run>`+)(?!`)[^\n]*?(?<!`)(?P=run)(?!`)",
    re.MULTILINE | re.DOTALL,
)
# Also a link without a target, [text](), which points to the page itself.
MARKDOWN_LINK = re.compile(
    r"(?<!!)\[([^\]\n]*)\]\(\s*<?([^)\s>\0]*)>?(?:\s+\"[^\"]*\")?\s*\)"
)
LINK_DEFINITION = re.compile(r"^ {0,3}\[[^\]\n]*\]:.*$", re.MULTILINE)
IMAGE = re.compile(r"!\[([^\]\n]*)\]\([^)\n]*\)")
BARE_URL = re.compile(r"\bhttps?://[^\s)<>\]`\0]+")
WWW_URL = re.compile(r"(?<![\w.-])www\.[^\s<>`\0]+", re.IGNORECASE)
# GitHub links a bare address only after these characters or at the start.
AUTOLINK_AFTER = frozenset(" \t\n*_~(")
# GitHub turns these into a mention or a link at the start of a piece of text and
# after any character but a letter or digit; an underscore can start or end an
# emphasis, so a reference counts whatever follows its number.
MENTION = re.compile(r"(?<![A-Za-z0-9])@([A-Za-z0-9][A-Za-z0-9-]{0,38}(?:/[\w-]+)?)")
CROSS_REFERENCE = re.compile(r"(?<![A-Za-z0-9])([\w.-]+/[\w.-]+#\d+)")
ISSUE_REFERENCE = re.compile(r"(?<![A-Za-z0-9])(?:#|GH-)(\d+)", re.IGNORECASE)
HTML = re.compile(r"<!--.*?-->|</?[A-Za-z][^>\n]*>", re.DOTALL)
HEADING_LINE = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
SECRET = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|sk-ant-[A-Za-z0-9_-]{8,}"
    r"|sk-(?:proj-)?[A-Za-z0-9_-]{20,}"
    r"|xox[abposr]-[A-Za-z0-9-]{10,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
LONG_TOKEN = re.compile(r"(?<![\w+/=-])[\w+/=-]{40,}(?![\w+/=-])")
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


class GitHubError(Exception):
    """A gh call failed."""


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
    # Only alerts in files under this path; empty for every file.
    path: str = ""

    def covers(self, alert: dict[str, Any]) -> bool:
        location = (alert.get("most_recent_instance") or {}).get("location") or {}
        return (
            (alert.get("tool") or {}).get("name", "").casefold() == self.tool.casefold()
            and (alert.get("rule") or {}).get("id") == self.rule
            and str(location.get("path") or "").startswith(self.path)
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


def load_labels(path: Path) -> tuple[Label, ...]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return tuple(
        Label(
            name=item["name"],
            color=item["color"].lower(),
            description=item["description"],
            group=item["group"],
            form_options=tuple(item.get("form_options", ())),
            ask=item.get("ask", True),
        )
        for item in data["label"]
    )


def load_config(root: Path, repository: str) -> Config:
    """The settings of the repository in `root`; config.toml is optional."""
    project_file = root / SETTINGS / "project.md"
    if not project_file.is_file() or not (root / LABELS_FILE).is_file():
        raise AssistantError(
            f"{LABELS_FILE} and {SETTINGS / 'project.md'} are needed; "
            "see https://github.com/Dennis-Otto/issue-assistant#set-up-a-repository."
        )
    settings_file = root / SETTINGS / "config.toml"
    settings = (
        tomllib.loads(settings_file.read_text(encoding="utf-8"))
        if settings_file.is_file()
        else {}
    )
    engine = settings.get("engine", {})
    forms = settings.get("forms", {})
    return Config(
        root=root,
        repository=repository,
        labels=load_labels(root / LABELS_FILE),
        project=project_file.read_text(encoding="utf-8").strip(),
        engine=engine.get("name", DEFAULT_ENGINE),
        model=engine.get("model", ""),
        hosts=frozenset(settings.get("links", {}).get("hosts", ())),
        area_field=forms.get("area_field", "Area"),
        unmapped_options=tuple(forms.get("unmapped_options", ("Other",))),
        notices=tuple(settings.get("transparency", {}).get("files", ("SUPPORT.md",))),
        releases=settings.get("releases", {}).get("close_with_release", True),
    )


def load_findings(root: Path) -> tuple[Acceptance, ...]:
    """The accepted findings of .github/findings.toml; none without the file.

    Only the findings command and the check read it, so a broken file never stops
    the work on issues.
    """
    path = root / FINDINGS_FILE
    if not path.is_file():
        return ()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return tuple(
            Acceptance(
                tool=str(item["tool"]),
                rule=str(item["rule"]),
                reason=str(item["reason"]),
                comment=str(item["comment"]),
                path=str(item.get("path", "")),
            )
            for item in data.get("accept", [])
        )
    except (tomllib.TOMLDecodeError, KeyError, TypeError) as error:
        raise AssistantError(
            f"{FINDINGS_FILE}: every [[accept]] needs tool, rule, reason and comment "
            f"({type(error).__name__}: {error})"
        ) from error


def label_names(issue: dict[str, Any]) -> set[str]:
    return {label["name"] for label in issue.get("labels") or []}


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

    def _gh(self, args: list[str], data: object = None) -> Any:
        result = subprocess.run(
            ["gh", *args],
            input=None if data is None else json.dumps(data),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise GitHubError(
                f"gh {args[1] if len(args) > 1 else ''}: "
                f"{(result.stderr or result.stdout).strip()}"
            )
        return json.loads(result.stdout) if result.stdout.strip() else None

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
        response = self._gh(args, data)
        if pages:
            return [item for page in response for item in page]
        return response

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        response = self._gh(
            ["api", "graphql", "--input", "-"],
            {"query": query, "variables": variables},
        )
        if response.get("errors"):
            raise GitHubError(
                "; ".join(error["message"] for error in response["errors"])
            )
        data: dict[str, Any] = response["data"]
        return data

    def _write(self, description: str, action: Callable[[], object]) -> None:
        self.writes.append(description)
        print(("Would " if self.dry_run else "") + description)
        if not self.dry_run:
            action()

    # Reads

    def issue(self, number: int) -> dict[str, Any]:
        issue: dict[str, Any] = self.rest(f"issues/{number}")
        return issue

    def comments(self, number: int) -> list[dict[str, Any]]:
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

    def open_issues(self, label: str) -> list[dict[str, Any]]:
        items = self.rest(
            f"issues?state=open&labels={quote(label)}&per_page=100", pages=True
        )
        return [item for item in items if "pull_request" not in item]

    def all_issues(self) -> list[dict[str, Any]]:
        items = self.rest("issues?state=all&per_page=100", pages=True)
        return [item for item in items if "pull_request" not in item]

    def labels(self) -> list[dict[str, Any]]:
        if self._labels is None:
            self._labels = self.rest("labels?per_page=100", pages=True)
        return self._labels

    def latest_release(self) -> str | None:
        try:
            release = self.rest("releases/latest")
        except GitHubError:
            return None
        tag: str = release["tag_name"]
        return tag

    def releases(self) -> list[dict[str, Any]]:
        """The published final releases, oldest first; no drafts or pre-releases."""
        items = self.rest("releases?per_page=30")
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
        except GitHubError:
            return False
        return compared["status"] in {"ahead", "identical"}

    def merged_pulls(self) -> tuple[str, list[dict[str, Any]]]:
        """The default branch and its latest merged pull requests with the issues they fix."""
        data = self.graphql(
            """
            query($owner: String!, $name: String!) {
              repository(owner: $owner, name: $name) {
                defaultBranchRef { name }
                pullRequests(states: MERGED, first: 30,
                             orderBy: {field: UPDATED_AT, direction: DESC}) {
                  nodes {
                    number mergedAt baseRefName
                    mergeCommit { oid }
                    closingIssuesReferences(first: 10) {
                      nodes { number repository { nameWithOwner } }
                    }
                  }
                }
              }
            }
            """,
            {"owner": self.owner, "name": self.name},
        )
        repository = data["repository"]
        pulls: list[dict[str, Any]] = repository["pullRequests"]["nodes"]
        return repository["defaultBranchRef"]["name"], pulls

    def fixing_pulls(self, number: int) -> list[dict[str, Any]]:
        """The merged pull requests into the default branch that fix the issue."""
        data = self.graphql(
            """
            query($owner: String!, $name: String!, $number: Int!) {
              repository(owner: $owner, name: $name) {
                defaultBranchRef { name }
                issue(number: $number) {
                  closedByPullRequestsReferences(first: 10, includeClosedPrs: true) {
                    nodes { number merged mergedAt baseRefName mergeCommit { oid } }
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
            if pull["merged"] and pull["baseRefName"] == branch and pull["mergeCommit"]
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
        except GitHubError:
            # A repository without discussions.
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
        except GitHubError:
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
                print(
                    f"::notice::Labels without rationale on #{issue['number']}: {error}"
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
                self.rest(f"issues/{number}/labels/{quote(name)}", method="DELETE")
            except GitHubError as error:
                if "Label does not exist" not in str(error):
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
                print(f"::notice::Closing #{issue['number']} through REST: {error}")
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
                print(
                    f"::notice::No duplicate suggestion on #{issue['number']}: {error}"
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

    def update_label(self, label: Label) -> None:
        self._write(
            f"update label {label.name}",
            lambda: self.rest(
                f"labels/{quote(label.name)}",
                method="PATCH",
                data={"color": label.color, "description": label.description},
            ),
        )


# Comments and issues


def login(item: dict[str, Any] | None) -> str:
    return str((item or {}).get("login") or "")


def is_bot(user: dict[str, Any] | None) -> bool:
    return (user or {}).get("type") == "Bot" or login(user).endswith("[bot]")


def is_maintainer(item: dict[str, Any]) -> bool:
    return item.get("author_association") in MAINTAINERS


def moment(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def created(item: dict[str, Any]) -> datetime:
    return moment(item["created_at"])


def marker(comment: dict[str, Any]) -> tuple[str, dict[str, str]] | None:
    """The kind and attributes of an assistant comment; None for every other comment."""
    if login(comment.get("user")) != BOT:
        return None
    match = MARKER.search(comment.get("body") or "")
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
    """The answers of an issue form, by the label of their field."""
    fields = {}
    for match in re.finditer(
        r"^###[ \t]+(.+?)[ \t]*\n(.*?)(?=^###[ \t]|\Z)", body or "", re.M | re.S
    ):
        value = match[2].strip()
        fields[match[1].strip()] = "" if value == "_No response_" else value
    return fields


def form_labels(issue: dict[str, Any], config: Config) -> list[str]:
    area = form_fields(issue.get("body") or "").get(config.area_field, "")
    return [
        label.name for label in config.labels if area and area in label.form_options
    ]


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
    answers = " ".join(form_fields(issue.get("body") or "").values()) or (
        issue.get("body") or ""
    )
    texts = [answers] + [
        comment.get("body") or ""
        for comment in comments
        if login(comment.get("user")) == author
    ]
    return detect_language(" ".join(texts))


def format_date(moment: datetime, language: str) -> str:
    if language == "de":
        return f"{moment.day}. {MONTHS_DE[moment.month - 1]} {moment.year}"
    return f"{moment:%B} {moment.day}, {moment.year}"


def follow_up_due(comments: list[dict[str, Any]], author: str) -> bool:
    """Whether the reporter answered questions that the assistant asked last."""
    asks = [
        (index, comment)
        for index, comment in enumerate(comments)
        if kind_of(comment) in {"analysis", "follow-up"}
    ]
    if not asks:
        return False
    index, last = asks[-1]
    found = marker(last)
    if not found or found[1].get("questions") != "1":
        return False
    if sum(kind_of(comment) == "follow-up" for comment in comments) >= MAX_FOLLOW_UPS:
        return False
    # Once the maintainer joined the conversation, the assistant stays out of it.
    return not any(
        is_maintainer(comment) and login(comment.get("user")) != author
        for comment in comments[index + 1 :]
    )


# Events


@dataclass
class Plan:
    """What the event job did, and which task the engine gets next."""

    issue: int | None = None
    mode: str = "none"
    notes: list[str] = field(default_factory=list)


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
        return Plan(number, "triage")
    if name == "issues" and action == "edited":
        if (
            issue["state"] == "open"
            and login(payload.get("sender")) == author
            and current & {NEEDS_INFO, STALE}
        ):
            return answered(github, issue)
        return Plan(number, notes=["Edit without a pending question."])
    if name == "issue_comment" and action == "created":
        comment = payload["comment"]
        if issue["state"] != "open":
            return reopen_if_answered(github, issue, comment)
        plan = Plan(number)
        if POSSIBLE_DUPLICATE in current:
            # Every human comment after the notice stops the automatic closing.
            github.remove_label(number, POSSIBLE_DUPLICATE)
            plan.notes.append("A comment stopped the duplicate closing.")
        if login(comment.get("user")) == author:
            if current & {NEEDS_INFO, STALE}:
                return answered(github, issue, plan)
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
    if follow_up_due(github.comments(number), login(issue.get("user"))):
        plan.mode = "follow-up"
    return plan


def reopen_if_answered(
    github: GitHub, issue: dict[str, Any], comment: dict[str, Any]
) -> Plan:
    """Reopen an issue the assistant closed once its reporter answers.

    An issue closed unanswered reopens at once; for one that a release closed, the
    engine decides whether the comment says that the problem persists.
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
        if created(comment) - created(closings[-1]) > REOPEN_AFTER_RELEASE:
            return Plan(number, notes=["The release closed this issue long ago."])
        return Plan(number, "release-reply", ["The reporter wrote after the release."])
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
    if follow_up_due(comments, author):
        plan.mode = "follow-up"
    return plan


# Context for the engine


def excerpt(text: str, limit: int) -> str:
    """The text of an issue in short: form answers as lines, without empty answers."""
    fields = form_fields(text)
    if fields:
        text = "\n".join(f"{key}: {value}" for key, value in fields.items() if value)
    text = re.sub(r"<!--.*?-->", "", text or "", flags=re.DOTALL)
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


def render_issue(issue: dict[str, Any], comments: list[dict[str, Any]]) -> str:
    reporter = login(issue.get("user"))
    lines = [
        f"# Issue #{issue['number']}: {issue['title']}",
        "",
        f"- Reporter: {describe_author(issue, reporter)}",
        f"- State: {issue['state']}",
        f"- Labels: {', '.join(sorted(label_names(issue))) or 'none'}",
        f"- Opened: {issue['created_at'][:10]}",
        "",
        "## Text of the issue",
        "",
        HTML.sub("", issue.get("body") or "").strip() or "(empty)",
    ]
    for index, comment in enumerate(comments, start=1):
        lines += [
            "",
            f"## Comment {index} by {describe_author(comment, reporter)}, "
            f"{comment['created_at'][:10]}",
            "",
            HTML.sub("", comment.get("body") or "").strip(),
        ]
    return "\n".join(lines) + "\n"


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
    (folder / "issue.md").write_text(render_issue(issue, comments), encoding="utf-8")
    (folder / "labels.md").write_text(render_labels(config), encoding="utf-8")
    others = [
        {
            "number": item["number"],
            "title": item["title"],
            "state": item["state"],
            "state_reason": item.get("state_reason"),
            "labels": sorted(label_names(item)),
            "opened": item["created_at"][:10],
            "closed": (item.get("closed_at") or "")[:10] or None,
            "comments": item.get("comments", 0),
            "excerpt": excerpt(item.get("body") or "", 1500),
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
            "title": item["title"],
            "category": (item.get("category") or {}).get("name"),
            "answered": item.get("isAnswered"),
            "closed": item.get("closed"),
            "excerpt": excerpt(item.get("body") or "", 1000),
            "answer": excerpt((item.get("answer") or {}).get("body") or "", 1000)
            or None,
        }
        for item in github.discussions()
    ]
    (folder / "discussions.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in discussions),
        encoding="utf-8",
    )
    values = {
        "repository": config.repository,
        "issue": str(number),
        "context": CONTEXT.as_posix(),
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
    schema = json.dumps(answer_schema(mode, config), separators=(",", ":"))
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
            f"{path}.{key} is not expected"
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


def looks_secret(text: str) -> bool:
    if SECRET.search(text):
        return True
    return any(
        re.search("[a-z]", token)
        and re.search("[A-Z]", token)
        and re.search("[0-9]", token)
        for token in LONG_TOKEN.findall(text)
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
    problems = validate(answer_schema(mode, config), answer)
    if problems:
        raise AssistantError(
            "The engine's answer breaks the schema: " + "; ".join(problems)
        )
    if any(looks_secret(item) for item in all_text(answer)):
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
    if hostname == "github.com":
        return bool(
            re.match(
                rf"^/{re.escape(config.repository)}/(?:blob|tree|releases)/", parts.path
            )
        )
    return hostname in config.hosts


def clean_prose(text: str, numbers: set[int], config: Config) -> str:
    """Text outside code without anything that GitHub turns into a mention, a
    reference, a foreign link, an image or HTML.

    What the cleaning keeps or writes goes behind a placeholder, so that no later
    rule changes it again. The rules repeat until nothing matches, because a removal
    can join the characters around it into something new. Last, the characters that
    could start code, a link or an image, escape the code written here or form an
    entity are escaped.
    """
    kept: list[str] = []

    def keep(value: str) -> str:
        kept.append(value)
        return f"\0{len(kept) - 1}\0"

    def code(value: str) -> str:
        return keep(f"`{value}`")

    def link(match: re.Match[str]) -> str:
        if not allowed_url(match[2], config):
            return match[1]
        # The link's text stays in the text, so the next rounds clean it too.
        return keep("[") + match[1] + keep(f"]({match[2]})")

    def address(url: str, match: re.Match[str]) -> str:
        start = match.start()
        linked = start == 0 or match.string[start - 1] in AUTOLINK_AFTER
        # GitHub doesn't link an address glued to other text, such as @https://….
        return keep(match[0]) if linked and allowed_url(url, config) else code(match[0])

    previous = None
    while text != previous:
        previous = text
        text = HTML.sub("", text)
        text = LINK_DEFINITION.sub("", text)
        text = IMAGE.sub(r"\1", text)
        text = MARKDOWN_LINK.sub(link, text)
        text = BARE_URL.sub(lambda m: address(m[0], m), text)
        text = WWW_URL.sub(lambda m: address(f"https://{m[0]}", m), text)
        text = MENTION.sub(lambda m: code(m[0]), text)
        text = CROSS_REFERENCE.sub(lambda m: code(m[1]), text)
        text = ISSUE_REFERENCE.sub(
            lambda m: keep(m[0]) if int(m[1]) in numbers else code(m[0]), text
        )
    text = HEADING_LINE.sub("", text)
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
    pieces = re.split(r"\0(\d+)\0", text)
    return joined(
        kept[int(piece)] if index % 2 else piece for index, piece in enumerate(pieces)
    )


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
    text = text.replace("\r", "").replace("\0", "")
    parts, last = [], 0
    for match in CODE.finditer(text):
        parts += [clean_prose(text[last : match.start()], checked, config), match[0]]
        last = match.end()
    parts.append(clean_prose(text[last:], checked, config))
    return joined(parts).strip()


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


def reference_link(
    reference: dict[str, Any],
    tracked: set[str],
    sha: str,
    language: str,
    config: Config,
) -> str | None:
    """A checked link to a file of the repository, or None for a path that doesn't fit.

    Code lines link to the analyzed commit, so they stay right when the file changes;
    files and headings link to the default branch.
    """
    path = reference["path"].strip().removeprefix("./")
    if path not in tracked or ".." in path.split("/"):
        return None
    file = config.root / path
    line = reference.get("line")
    anchor = (reference.get("anchor") or "").strip().lstrip("#").lower()
    if line:
        if line > len(file.read_text(encoding="utf-8", errors="replace").splitlines()):
            return None
        word = "Zeile" if language == "de" else "line"
        return f"[`{path}`, {word} {line}]({config.blob}/{sha}/{path}#L{line})"
    if anchor and path.endswith(".md"):
        titles = headings(file)
        if anchor in titles:
            return f"[{titles[anchor]}]({config.blob}/HEAD/{path}#{anchor}) in `{path}`"
    return f"[`{path}`]({config.blob}/HEAD/{path})"


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
        "The maintainer reads every issue as well.",
        "summary": "Summary",
        "analysis": "First analysis",
        "next_steps": "Worth trying",
        "missing": "Information needed",
        "missing.intro": "To continue, please add the following, in a comment or by "
        "editing the issue:",
        "missing.outro": "Without an answer, a reminder follows after 15 days and the "
        "issue closes after 30 days; an answer reopens it.",
        "duplicate": "Possible duplicate",
        "duplicate.text": "This looks like a duplicate of #{number}: {reason}",
        "duplicate.close": "If that's right, there's nothing to do: the issue closes as "
        "a duplicate of #{number} in 3 days, so the conversation stays in one place. A "
        "👍 on #{number} shows that more people are affected. If it's something "
        "different, write a comment or react to this comment with 👎, and it stays open.",
        "related": "Related",
        "discussion": "discussion",
        "references": "Where to look",
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
        "documentation and earlier issues. It can be wrong; the maintainer "
        "decides.{labels}</sub>",
        "labels": " Labels: {labels}.",
        "follow-up.thanks": "Thanks for your answer, @{author}!",
        "follow-up.missing": "Still needed",
        "follow-up.missing.intro": "Please also add:",
        "follow-up.footer": "<sub>🤖 Automated follow-up by {engine}. It can be wrong; "
        "the maintainer decides.</sub>",
        "reminder": "@{author}, a friendly reminder: this issue is still waiting for "
        "your answer to the questions above. Without an answer, it closes automatically "
        "on {date}. A short comment keeps it open, for example if you need more time.",
        "closed-unanswered": "I'm closing this issue because the information asked for "
        "above has been missing for 30 days. @{author}, as soon as you answer here, it "
        "reopens automatically. Thanks for reporting!",
        "closed-duplicate": "Closed as a duplicate of #{number}, as announced three days "
        "ago. Please continue there; a 👍 on #{number} shows that more people are "
        "affected. If this was a mistake, write a comment and the maintainer will "
        "reopen it.",
        "reopened": "Thanks for your answer, @{author}! I reopened the issue; the "
        "maintainer will take another look.",
        "fixed": "Fixed by #{pull}, which is now on `{branch}`. The fix ships with the "
        "next release; this issue closes then, with a link to it.",
        "released": "🎉 Released in [{tag}]({url}){fixes}. @{author}, please update to "
        "this version. If the problem persists, write a comment here and the issue "
        "reopens, or open a new issue.",
        "released.fixes": " with the fix from {pulls}",
        "reopened-release": "Thanks for letting us know, @{author}, and sorry that it "
        "isn't solved yet. I reopened the issue; the maintainer will take another look.",
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
        "angesehen. Der Maintainer liest jedes Issue ebenfalls.",
        "summary": "Zusammenfassung",
        "analysis": "Erste Analyse",
        "next_steps": "Das kannst du ausprobieren",
        "missing": "Fehlende Informationen",
        "missing.intro": "Damit es weitergehen kann, ergänze bitte Folgendes, als "
        "Kommentar oder indem du das Issue bearbeitest:",
        "missing.outro": "Ohne Antwort folgt nach 15 Tagen eine Erinnerung, nach 30 "
        "Tagen wird das Issue geschlossen; eine Antwort öffnet es wieder.",
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
        "der Dokumentation und früherer Issues. Sie kann falsch sein; der Maintainer "
        "entscheidet.{labels}</sub>",
        "labels": " Labels: {labels}.",
        "follow-up.thanks": "Danke für deine Antwort, @{author}!",
        "follow-up.missing": "Noch offen",
        "follow-up.missing.intro": "Bitte ergänze noch:",
        "follow-up.footer": "<sub>🤖 Automatische Rückmeldung von {engine}. Sie kann "
        "falsch sein; der Maintainer entscheidet.</sub>",
        "reminder": "@{author}, eine freundliche Erinnerung: Dieses Issue wartet noch "
        "auf deine Antwort auf die Fragen oben. Ohne Antwort wird es am {date} "
        "automatisch geschlossen. Ein kurzer Kommentar genügt, damit es offen bleibt, "
        "etwa wenn du mehr Zeit brauchst.",
        "closed-unanswered": "Ich schließe dieses Issue, weil die erbetenen "
        "Informationen seit 30 Tagen fehlen. @{author}, sobald du hier antwortest, wird "
        "es automatisch wieder geöffnet. Danke für deine Meldung!",
        "closed-duplicate": "Als Duplikat von #{number} geschlossen, wie vor drei Tagen "
        "angekündigt. Bitte mach dort weiter; ein 👍 bei #{number} zeigt, dass mehr "
        "Leute betroffen sind. Falls das ein Irrtum war, schreib einen Kommentar, dann "
        "öffnet der Maintainer es wieder.",
        "reopened": "Danke für deine Antwort, @{author}! Ich habe das Issue wieder "
        "geöffnet; der Maintainer sieht es sich erneut an.",
        "fixed": "Behoben durch #{pull}, jetzt auf `{branch}`. Der Fix kommt mit dem "
        "nächsten Release; dann wird dieses Issue mit einem Link darauf geschlossen.",
        "released": "🎉 Veröffentlicht in [{tag}]({url}){fixes}. @{author}, bitte "
        "aktualisiere auf diese Version. Besteht das Problem weiter, schreib hier einen "
        "Kommentar, dann wird das Issue wieder geöffnet, oder eröffne ein neues Issue.",
        "released.fixes": " mit dem Fix aus {pulls}",
        "reopened-release": "Danke für die Rückmeldung, @{author}, und schade, dass es "
        "noch nicht gelöst ist. Ich habe das Issue wieder geöffnet; der Maintainer sieht "
        "es sich erneut an.",
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
    return "\n".join(f"- {item}" for item in items)


@dataclass
class Findings:
    """The engine's answer after the checks, ready to post."""

    duplicate: dict[str, Any] | None = None
    duplicate_reason: str = ""
    related: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    numbers: set[int] = field(default_factory=set)


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
                    reason=clean(findings.duplicate_reason),
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
        if findings.related:
            parts.append(
                f"### {say(language, 'related')}\n\n{bullets(findings.related)}"
            )
        if findings.references:
            parts.append(
                f"### {say(language, 'references')}\n\n{bullets(findings.references)}"
            )
    shown = ", ".join(f"`{name}`" for name in labels)
    parts.append(
        say(
            language,
            "footer",
            labels=say(language, "labels", labels=shown) if labels else "",
            engine=ENGINES[engine].name,
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
) -> str:
    language = answer["language"]
    parts = [
        render_marker(
            "follow-up", lang=language, questions=int(bool(asks)), engine=engine
        ),
        say(language, "follow-up.thanks", author=author),
    ]
    if answer["sensitive_data"] != "none":
        parts.append(say(language, f"sensitive.{answer['sensitive_data']}"))
    parts.append(sanitize(answer["reply"], config))
    if asks:
        parts.append(
            f"### {say(language, 'follow-up.missing')}\n\n"
            f"{say(language, 'follow-up.missing.intro')}\n\n"
            f"{bullets(sanitize(ask, config) for ask in asks)}\n\n"
            f"{say(language, 'missing.outro')}"
        )
    if references:
        parts.append(f"### {say(language, 'references')}\n\n{bullets(references)}")
    parts.append(say(language, "follow-up.footer", engine=ENGINES[engine].name))
    return "\n\n".join(parts) + "\n"


def render_reminder(author: str, language: str, closes: datetime) -> str:
    date = format_date(closes, language)
    return (
        f"{render_marker('reminder')}\n"
        f"{say(language, 'reminder', author=author, date=date)}\n"
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
    except GitHubError:
        return None
    return None if "pull_request" in issue else issue


def check_findings(
    github: GitHub,
    issue: dict[str, Any],
    answer: dict[str, Any],
    sha: str,
    config: Config,
) -> Findings:
    """Keep the duplicates and related issues that exist, and the files that fit.

    A sure duplicate of an open, older issue gets the notice and closes later; the
    first other candidate becomes a suggestion in GitHub's panel for the maintainer.
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
            and not findings.duplicate
        ):
            findings.duplicate = original
            findings.duplicate_reason = candidate["reason"]
            continue
        if not suggested and (
            original["state"] == "open" or original.get("state_reason") == "completed"
        ):
            github.suggest_duplicate(issue, original, candidate["reason"])
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
    findings.related = [
        f"#{entry}"
        + (f" ({say(language, 'discussion')})" if kind == "discussion" else "")
        + f": {sanitize(reason, config, findings.numbers)}"
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
            links[link] = sanitize(item["reason"], config)
    return [f"{link}: {reason}" for link, reason in links.items()]


def apply_labels(
    github: GitHub, issue: dict[str, Any], answer: dict[str, Any], config: Config
) -> list[str]:
    """Correct the kind while nobody triaged the issue and add areas and topics."""
    current = label_names(issue)
    kind = answer["kind"]
    if NEEDS_TRIAGE in current:
        for name in config.group("type"):
            if name in current and name != kind:
                github.remove_label(issue["number"], name)
    wanted = [kind, *answer["areas"], *answer["topics"]]
    added = [name for name in dict.fromkeys(wanted) if name not in current]
    github.add_explained_labels(issue, added, answer["label_rationale"])
    return added


def already_done(mode: str, comments: list[dict[str, Any]], author: str) -> bool:
    """Whether a repeated run of the same event would post the same comment again."""
    if mode == "triage":
        return any(kind_of(comment) == "analysis" for comment in comments)
    conversation = [
        comment
        for comment in comments
        if kind_of(comment) in {"analysis", "follow-up"}
        or login(comment.get("user")) == author
    ]
    return bool(conversation) and kind_of(conversation[-1]) == "follow-up"


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
        return apply_release_reply(github, issue, answer)
    if issue["state"] != "open" or issue.get("locked"):
        return [f"#{number} is closed or locked; nothing posted."]
    author = login(issue.get("user"))
    if mode == "maintainer-reply":
        return apply_maintainer_reply(github, issue, answer)
    if not repeat and already_done(mode, github.comments(number), author):
        return [f"#{number} already has this {mode}; nothing posted."]
    if answer["kind"] == "spam":
        github.add_explained_labels(issue, [INVALID], answer["label_rationale"])
        return [f"#{number} looks like spam: marked invalid, no comment."]
    asks = [
        ask.strip()
        for ask in answer["missing_information"]
        if ask.strip() and answer["kind"] in config.asking_kinds()
    ]
    if mode == "follow-up":
        body = render_follow_up(
            answer,
            author,
            references(answer["references"], sha, answer["language"], config),
            asks,
            config,
            engine,
        )
        github.comment(number, body)
        added = apply_labels(github, issue, answer, config)
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
    github.comment(
        number,
        render_analysis(answer, author, findings, asks, planned, config, engine),
    )
    apply_labels(github, issue, answer, config)
    lifecycle = []
    if asks and NEEDS_INFO not in label_names(issue):
        lifecycle.append(NEEDS_INFO)
    if findings.duplicate:
        lifecycle.append(POSSIBLE_DUPLICATE)
    github.add_labels(number, lifecycle)
    return [
        f"Analysis on #{number}; labels added: {', '.join(planned + lifecycle) or 'none'}."
    ]


def apply_maintainer_reply(
    github: GitHub, issue: dict[str, Any], answer: dict[str, Any]
) -> list[str]:
    number = issue["number"]
    if not answer["waiting_for_reporter"]:
        return [f"#{number} does not wait for the reporter: {answer['reason']}"]
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
    return [f"#{number} waits for the reporter: {answer['reason']}"]


def apply_release_reply(
    github: GitHub, issue: dict[str, Any], answer: dict[str, Any]
) -> list[str]:
    """Reopen an issue that a release closed when its reporter says it isn't fixed."""
    number = issue["number"]
    if issue["state"] == "open" or issue.get("locked"):
        return [f"#{number} is open or locked; nothing to reopen."]
    if not answer["problem_persists"]:
        return [f"#{number} stays closed: {answer['reason']}"]
    github.reopen(number)
    github.add_labels(number, [NEEDS_TRIAGE])
    github.comment(
        number, render_reopened_release(login(issue.get("user")), answer["language"])
    )
    return [f"#{number} reopened: {answer['reason']}"]


# The daily sweep


def sweep(github: GitHub, now: datetime, releases: bool = True) -> list[str]:
    """The whole lifecycle; every run does all of it, so a missed run loses nothing."""
    done = []
    if releases:
        done += mark_fixed(github, now)
        done += close_released(github)
    for issue in github.open_issues(NEEDS_INFO):
        if not issue.get("locked"):
            done += sweep_waiting(github, issue, now)
    for issue in github.open_issues(POSSIBLE_DUPLICATE):
        if not issue.get("locked"):
            done += sweep_duplicate(github, issue, now)
    return done


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


def mark_fixed(github: GitHub, now: datetime) -> list[str]:
    """Label the open issues that a recently merged pull request fixes.

    The issue stays open until a release ships the fix. A maintainer who removes the
    label keeps it removed: the notice of a pull request is never repeated.
    """
    branch, pulls = github.merged_pulls()
    done = []
    for pull in sorted(pulls, key=lambda item: item["mergedAt"]):
        merged = moment(pull["mergedAt"])
        if pull["baseRefName"] != branch or now - merged > MERGE_LOOKBACK:
            continue
        for reference in pull["closingIssuesReferences"]["nodes"]:
            same = reference["repository"]["nameWithOwner"].lower()
            if same != github.repository.lower():
                continue
            number = reference["number"]
            issue = github.issue(number)
            if issue["state"] != "open" or issue.get("locked"):
                continue
            current = label_names(issue)
            comments = github.comments(number)
            if FIXED in current or any(
                (found := marker(comment))
                and found[0] == "fixed"
                and found[1].get("pull") == str(pull["number"])
                for comment in comments
            ):
                continue
            github.comment(
                number,
                render_fixed(pull["number"], branch, issue_language(issue, comments)),
            )
            github.add_labels(number, [FIXED])
            for name in (NEEDS_INFO, STALE):
                if name in current:
                    github.remove_label(number, name)
            done.append(
                f"#{number}: fixed by #{pull['number']}; closes with the next release."
            )
    return done


def close_released(github: GitHub) -> list[str]:
    """Close the fixed issues whose fix a published release ships, with its link."""
    issues = [issue for issue in github.open_issues(FIXED) if not issue.get("locked")]
    if not issues:
        return []
    releases = github.releases()
    done = []
    for issue in issues:
        number = issue["number"]
        labeled = [
            created(event)
            for event in github.events(number)
            if event["event"] == "labeled"
            and (event.get("label") or {}).get("name") == FIXED
        ]
        pulls = github.fixing_pulls(number)
        shas = [pull["mergeCommit"]["oid"] for pull in pulls]
        # The label promises the next release: one published after it, with every
        # fixing pull request merged.
        since = max(labeled or [created(issue)])
        release = first_release(github, releases, shas, since)
        if not release:
            continue
        comments = github.comments(number)
        author = login(issue.get("user"))
        github.comment(
            number,
            render_released(
                author,
                release,
                sorted(pull["number"] for pull in pulls),
                issue_language(issue, comments),
            ),
        )
        for name in (FIXED, NEEDS_TRIAGE, NEEDS_INFO, STALE):
            if name in label_names(issue):
                github.remove_label(number, name)
        github.close(
            issue,
            "COMPLETED",
            f"Fixed and released in {release['tag_name']} on "
            f"{release['published_at'][:10]}.",
        )
        try:
            github.set_milestone(number, release["tag_name"], release["published_at"])
        except GitHubError as error:
            # The milestone only files the issue; the release closed it in any case.
            print(f"::notice::No milestone {release['tag_name']} on #{number}: {error}")
        done.append(f"#{number}: closed, released in {release['tag_name']}.")
    return done


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
        github.comment(number, render_reminder(author, language, now + WARNING_PERIOD))
        github.add_labels(number, [STALE])
        return [f"#{number}: reminded the reporter after {(now - since).days} days."]
    return []


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
    objections = [
        comment
        for comment in comments
        if created(comment) > created(notice) and not is_bot(comment.get("user"))
    ]
    objections += [
        reaction
        for reaction in github.reactions(notice["id"])
        if reaction["content"] == "-1"
        and login(reaction.get("user")) in {author, github.owner}
    ]
    original = other_issue(github, int(marker(notice)[1]["duplicate"]), number)  # type: ignore[index]
    if objections or not original or original["state"] != "open":
        github.remove_label(number, POSSIBLE_DUPLICATE)
        return [
            f"#{number}: stays open, the duplicate notice was objected to or is outdated."
        ]
    language = issue_language(issue, comments)
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
    existing = {label["name"]: label for label in github.labels()}
    done = []
    for label in config.labels:
        current = existing.get(label.name)
        if current is None:
            github.create_label(label)
            done.append(f"created {label.name}")
        elif (current.get("color") or "").lower() != label.color or (
            current.get("description") or ""
        ) != label.description:
            github.update_label(label)
            done.append(f"updated {label.name}")
    configured = {label.name for label in config.labels}
    for name in sorted(set(existing) - configured):
        print(f"::notice::Label {name} is not in {LABELS_FILE}; it stays.")
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
            f"{acceptance.comment} (accepted in {FINDINGS_FILE.as_posix()})",
        )
        done.append(
            f"Alert #{alert['number']} ({acceptance.tool} {acceptance.rule}) "
            f"dismissed as {acceptance.reason}."
        )
    done += [f"Alert #{alert['number']} is open: {alert['html_url']}" for alert in left]
    if left:
        done.append(
            f"Fix these findings, or accept them with a reason in "
            f"{FINDINGS_FILE.as_posix()}."
        )
    return done, len(left)


# Checking a repository's set-up


def load_yaml(path: Path) -> Any:
    """A YAML file as data, with PyYAML or with yq, which GitHub's runners have."""
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
        return json.loads(result.stdout)
    return yaml.safe_load(path.read_text(encoding="utf-8"))


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
    names = [label.name for label in config.labels]
    for name in sorted({name for name in names if names.count(name) > 1}):
        problems.append(f"{LABELS_FILE}: the label {name} is defined twice")
    for label in config.labels:
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
        form = load_yaml(path)
        where = path.relative_to(config.root).as_posix()
        for name in form.get("labels") or []:
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
        template = TOOL / WORKFLOWS / name
        path = config.root / WORKFLOWS / name
        where = (WORKFLOWS / name).as_posix()
        if not path.is_file():
            problems.append(f"{where} is missing; run the install command")
            continue
        expected = normalized(load_yaml(template), [], template.name)
        actual = normalized(load_yaml(path), problems, where)
        if actual != expected:
            problems.append(
                f"{where} differs from the template of the issue assistant; "
                "only the refs of its actions may differ"
            )
    firewall = config.root / FIREWALL
    if not firewall.is_file():
        problems.append(f"{FIREWALL} is missing; run the install command")
    else:
        policy = load_yaml(firewall)
        needed = set(load_yaml(TOOL / FIREWALL)["allow"])
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
        labels = ((load_yaml(actionlint) or {}).get("self-hosted-runner") or {}).get(
            "labels"
        ) or []
        if FIREWALL_RUNNER not in labels:
            problems.append(f"{ACTIONLINT}: list the runner {FIREWALL_RUNNER}")
    ignore = config.root / ".gitignore"
    if not ignore.is_file() or f"/{CONTEXT.as_posix()}/" not in ignore.read_text(
        "utf-8"
    ):
        problems.append(f".gitignore: add /{CONTEXT.as_posix()}/")
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
    if not isinstance(config.releases, bool):
        problems.append(
            f"{SETTINGS / 'config.toml'}: close_with_release must be true or false"
        )
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
    except AssistantError as error:
        return [str(error)]
    problems = []
    for item in accepted:
        where = f"{FINDINGS_FILE.as_posix()}: {item.tool} {item.rule}"
        if not item.tool.strip() or not item.rule.strip():
            problems.append(f"{FINDINGS_FILE.as_posix()}: name the tool and the rule")
        if item.reason not in DISMISS_REASONS:
            problems.append(
                f"{where}: the reason must be one of {', '.join(DISMISS_REASONS)}"
            )
        if not 0 < len(item.comment.strip()) <= 200:
            problems.append(f"{where}: explain it in a comment of 1 to 200 characters")
    return problems


def check(config: Config) -> list[str]:
    """Everything that keeps the repository's set-up safe and consistent."""
    return (
        check_labels(config)
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
            (TOOL / WORKFLOWS / name).read_text(encoding="utf-8"),
        )
        target.write_text(content, encoding="utf-8", newline="\n")
        done.append(f"wrote {target.relative_to(root).as_posix()}")
    firewall = root / FIREWALL
    if firewall.is_file():
        done.append(
            f"kept {FIREWALL.as_posix()}; check that it allows the template's hosts"
        )
    else:
        shutil.copyfile(TOOL / FIREWALL, firewall)
        done.append(f"wrote {FIREWALL.as_posix()}")
    for starter in STARTERS:
        target = root / starter
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(TEMPLATES / starter, target)
            done.append(f"wrote {starter.as_posix()}; adapt it")
    ignore = root / ".gitignore"
    entry = f"/{CONTEXT.as_posix()}/"
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


def summary(
    title: str, lines: list[str], dry_run: bool, previews: Iterable[str] = ()
) -> None:
    text = f"### {title}{' (dry run)' if dry_run else ''}\n\n" + (
        bullets(lines) if lines else "Nothing to do."
    )
    for preview in previews:
        text += (
            "\n\n<details><summary>Comment</summary>\n\n"
            f"````markdown\n{preview.rstrip()}\n````\n\n</details>"
        )
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def choose_engine(config: Config, ready: str) -> tuple[str, bool, str]:
    """The engine of config.toml, whether its secret exists, and its model.

    `ready` lists the engines whose secret the workflow found, separated by commas.
    """
    key = engine_named(config.engine)
    available = key in {item.strip() for item in ready.split(",") if item.strip()}
    if not available:
        print(
            f"::notice::No credentials for {ENGINES[key].name} are configured; "
            "the issue assistant only sets labels and keeps the lifecycle."
        )
    return key, available, config.model or ENGINES[key].model


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("event", help="handle the GitHub event of this run")
    commands.add_parser("engine", help="name the engine and whether it can run")
    for name, text_ in (
        ("context", "write the files the engine reads"),
        ("apply", "check the engine's answer in RESULT and apply it"),
    ):
        command = commands.add_parser(name, help=text_)
        command.add_argument(
            "--issue", type=int, default=os.environ.get("ISSUE") or None
        )
        command.add_argument(
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
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"
    try:
        if arguments.command == "install":
            for line in install(root, arguments.ref, arguments.version):
                print(line)
            return 0
        repository = os.environ.get("GH_REPO") or os.environ.get(
            "GITHUB_REPOSITORY", ""
        )
        github = GitHub(repository, dry_run=dry_run)
        if arguments.command == "findings":
            # Independent of the settings of the issues.
            lines, left = watch_findings(github, root)
            for line in lines:
                if line.startswith("Alert #") and " is open: " in line:
                    print(f"::error::{line}")
            summary("Findings", lines or ["No open findings."], dry_run)
            return 1 if left else 0
        config = load_config(root, repository)
        if arguments.command == "check":
            problems = check(config)
            for problem in problems:
                print(f"::error::{problem}")
            summary("Issue assistant set-up", problems or ["Everything fits."], False)
            return 1 if problems else 0
        if arguments.command in {"context", "apply"} and not (
            arguments.issue and arguments.mode
        ):
            raise AssistantError(f"{arguments.command} needs an issue and a mode.")
        if arguments.command == "engine":
            key, available, model = choose_engine(
                config, os.environ.get("READY_ENGINES", "")
            )
            output("engine", key)
            output("ready", str(available).lower())
            output("model", model)
            return 0
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
            title = f"Issue assistant: {arguments.mode} of #{arguments.issue}"
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
            done = sweep(github, datetime.now(UTC), config.releases)
        else:
            title = "Labels"
            done = sync_labels(github, config)
    except AssistantError as error:
        print(f"::error::{error}")
        return 1
    summary(title, done, dry_run, github.previews)
    return 0


if __name__ == "__main__":
    sys.exit(main())
