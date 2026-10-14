"""Builders for issues, comments and answers, and a fake of the GitHub API.

The fake answers the REST and GraphQL calls of the script, so the real request code
runs; only `gh` itself is replaced.
"""

import json
import re
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import issue_assistant as assistant
from oracle import problems

REPOSITORY = "owner/project"
# The settings that the property tests and the fuzzers clean with: a repository, a
# kind of issue and one site besides GitHub that links may lead to.
CONFIG = assistant.Config(
    root=Path("."),
    repository=REPOSITORY,
    labels=(assistant.Label("bug", "d73a4a", "Bug", "type"),),
    project="A project.",
    hosts=frozenset({"docs.example.com"}),
)
# The commit hash that the sample repository installs the assistant with.
REF = "0123456789abcdef0123456789abcdef01234567"
NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)
BOT_USER = {"login": "github-actions[bot]", "type": "Bot"}
BUG_BODY = """### What happened?

The live card stays empty after a throw.

### Area

Live card

### Version

1.9.0

### Last version that worked

_No response_
"""
# How the assistant read forms and HTML before its patterns took linear time, for
# the tests to compare with. Their quantifiers overlap, so that some texts took the
# form pattern hours and the HTML pattern seconds; give them short texts only.
EARLIER_FORM_FIELD = re.compile(
    r"^###[ \t]+(.+?)[ \t]*\n(.*?)(?=^###[ \t]|\Z)", re.MULTILINE | re.DOTALL
)
EARLIER_HTML = re.compile(r"<!--.*?-->|</?[A-Za-z][^>\n]*>", re.DOTALL)


def earlier_form_fields(body):
    fields = {}
    for match in EARLIER_FORM_FIELD.finditer(body):
        value = match[2].strip()
        fields[match[1].strip()] = "" if value == "_No response_" else value
    return fields


def at(days_ago: float = 0) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


API = f"https://api.github.com/repos/{REPOSITORY}"
# The repository on GitHub, where the addresses of its comments start.
WEB = f"https://github.com/{REPOSITORY}"


def changed_since(item, query):
    """Whether an issue or a comment changed at or after the `since` of the query,
    as GitHub filters its lists of issues and of comments."""
    if "since" not in query:
        return True
    changed = item.get("updated_at") or item["created_at"]
    return assistant.moment(changed) >= assistant.moment(query["since"][0])


def make_issue(
    number=12,
    *,
    author="reporter",
    association="NONE",
    labels=("bug", "needs-triage"),
    state="open",
    body=BUG_BODY,
    days_ago=1,
    **extra,
):
    return {
        "number": number,
        "node_id": f"I_{number}",
        "title": f"Issue {number}",
        "user": {"login": author, "type": "User"},
        "author_association": association,
        "labels": [{"name": name} for name in labels],
        "state": state,
        "state_reason": None,
        "locked": False,
        "body": body,
        "created_at": at(days_ago),
        "updated_at": at(days_ago),
        "closed_at": None,
        "comments": 0,
        **extra,
    }


def make_comment(comment_id, author, body, days_ago, association="NONE"):
    user = BOT_USER if author == "bot" else {"login": author, "type": "User"}
    return {
        "id": comment_id,
        "user": user,
        "body": body,
        "created_at": at(days_ago),
        "author_association": association,
    }


def make_answer(**changes):
    answer = {
        "language": "en",
        "kind": "bug",
        "areas": ["area: cards"],
        "topics": [],
        "label_rationale": "The live card shows nothing after a throw.",
        "summary": "The live card stays empty after a throw.",
        "analysis": "The card probably misses the `throw` event.",
        "next_steps": ["Reload the dashboard."],
        "missing_information": [],
        "duplicates": [],
        "related": [],
        "references": [],
        "sensitive_data": "none",
        "security_report": False,
    }
    answer.update(changes)
    return answer


def make_follow_up(**changes):
    answer = {
        "language": "en",
        "kind": "bug",
        "areas": [],
        "topics": [],
        "label_rationale": "Still a bug of the live card.",
        "reply": "The log shows that the event arrives.",
        "missing_information": [],
        "references": [],
        "sensitive_data": "none",
    }
    answer.update(changes)
    return answer


# The keywords of each type of schema that answer_of follows. additionalProperties
# needs nothing: its answers hold the properties of the schema alone.
FOLLOWED = {
    "object": {"type", "properties", "required", "additionalProperties"},
    "array": {"type", "items", "maxItems"},
    "string": {"type", "enum", "maxLength"},
    "integer": {"type", "minimum"},
    "boolean": {"type"},
}


def answer_of(schema: dict[str, Any], choose: Any, key: str = "answer") -> Any:
    """A value that fits the schema of the engine's answer, each choice made by
    `choose`.

    The property tests and the fuzzers build their answers from answer_schema itself,
    so that they try what the engine may answer, and nothing else. `choose` has a
    method for each choice, which gets the name of the field: `text` with its most
    characters, `count` of items with their most, `one_of` the values, `number`
    with its least, `boolean`, and `present` for a field that may be left out.

    A schema that it can't follow fails here, rather than in answers that the
    checks refuse, which the tests and the fuzzers would skip without a word.
    """
    kind = schema.get("type")
    if not isinstance(kind, str) or set(schema) - FOLLOWED.get(kind, set()):
        raise ValueError(f"answer_of can't build the {key} of {schema!r}")
    if kind == "object":
        return {
            name: answer_of(item, choose, name)
            for name, item in schema["properties"].items()
            if name in schema["required"] or choose.present(name)
        }
    if kind == "array":
        # An array without items, such as the areas of a project that has none.
        if "items" not in schema:
            return []
        return [
            answer_of(schema["items"], choose, key)
            for _ in range(choose.count(key, schema["maxItems"]))
        ]
    if kind == "string":
        if "enum" in schema:
            return choose.one_of(key, schema["enum"])
        return choose.text(key, schema["maxLength"])
    if kind == "integer":
        return choose.number(key, schema["minimum"])
    return choose.boolean(key)


def make_pull(
    number=40,
    fixes=(12,),
    *,
    days_ago=1,
    base="main",
    merged=True,
    repository=REPOSITORY,
    **extra,
):
    """A merged pull request with the issues that it fixes, last updated at its merge.

    `fixes` holds numbers of this repository's issues, or `owner/name#number`.
    """
    references = []
    for issue in fixes:
        where, _, number_ = str(issue).rpartition("#")
        references.append(
            {
                "number": int(number_),
                "repository": {"nameWithOwner": where or REPOSITORY},
            }
        )
    return {
        "number": number,
        "merged": merged,
        "mergedAt": at(days_ago),
        "updatedAt": at(days_ago),
        "baseRefName": base,
        "mergeCommit": {"oid": f"{number:040x}"},
        "repository": {"nameWithOwner": repository},
        "closingIssuesReferences": {"nodes": references},
        **extra,
    }


def make_release(tag="v1.10.0", days_ago=0.5, **extra):
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/{REPOSITORY}/releases/tag/{tag}",
        "published_at": at(days_ago),
        "draft": False,
        "prerelease": False,
        **extra,
    }


def release_history(*ages, **changes):
    """Final releases published the given days ago, oldest first, up to v1.10.0.

    `changes` change the release of a tag, such as `{"v1.9.0": {"prerelease": True}}`
    passed as `**{"v1.9.0": ...}`.
    """
    first = 11 - len(ages)
    releases = []
    for offset, days_ago in enumerate(ages):
        tag = f"v1.{first + offset}.0"
        releases.append(make_release(tag, days_ago, **changes.get(tag, {})))
    return releases


def make_alert(number=1, tool="Scorecard", rule="CodeReviewID", path="no file"):
    return {
        "number": number,
        "state": "open",
        "tool": {"name": tool},
        "rule": {"id": rule},
        "most_recent_instance": {"location": {"path": path}},
        "html_url": f"https://github.com/{REPOSITORY}/security/code-scanning/{number}",
    }


def not_found(message="Not Found"):
    """The error of gh for a thing that doesn't exist."""
    return assistant.GitHubError(f"gh api: {message} (HTTP 404)", status=404)


def rate_limited():
    """The error of gh when the token used up its requests of the hour."""
    return assistant.GitHubError(
        "gh api: API rate limit exceeded for installation ID 1. (HTTP 403)", status=403
    )


def server_error():
    """The error of gh when GitHub fails, as in an incident."""
    return assistant.GitHubError("gh api: HTTP 502", status=502)


def analysis(comment_id=1, days_ago=1, **attributes):
    marker = assistant.render_marker("analysis", lang="en", **attributes)
    return make_comment(comment_id, "bot", f"{marker}\nFirst analysis", days_ago)


def follow_up(comment_id=1, days_ago=1, author="bot"):
    marker = assistant.render_marker("follow-up", lang="en", questions=0)
    return make_comment(comment_id, author, f"{marker}\nThanks for the log", days_ago)


def page_of(items, variables):
    """The page of a GraphQL connection after the cursor, which is the place in the
    list, with its pageInfo."""
    assert 1 <= variables["first"] <= 100, "GitHub gives 1 to 100 nodes a page"
    start = int(variables["cursor"] or 0)
    end = start + variables["first"]
    info = {"hasNextPage": end < len(items), "endCursor": str(end)}
    return deepcopy(items[start:end]), info


def as_graphql(issue):
    """An issue of the fake as GitHub's GraphQL gives it. Its `closer`, if it has
    one, is that of its last closing: {"number": 31} for a pull request, {} for a
    commit and None for a person. Its `fixes` are the pull requests that name it,
    as GraphQL gives them."""
    closings = [{"closer": issue["closer"]}] if "closer" in issue else []
    return {
        "number": issue["number"],
        "title": issue["title"],
        "body": issue["body"],
        "state": issue["state"].upper(),
        "stateReason": (issue["state_reason"] or "").upper() or None,
        "createdAt": issue["created_at"],
        "closedAt": issue["closed_at"],
        "labels": {"nodes": [{"name": label["name"]} for label in issue["labels"]]},
        "comments": {"totalCount": issue["comments"]},
        "closedByPullRequestsReferences": {"nodes": issue.get("fixes", [])},
        "timelineItems": {"nodes": closings},
    }


class FakeGitHub(assistant.GitHub):
    """The REST and GraphQL endpoints the script uses, in memory."""

    def __init__(self, config, *issues, dry_run=False):
        super().__init__(REPOSITORY, dry_run=dry_run)
        self.store = {issue["number"]: deepcopy(issue) for issue in issues}
        self.comment_store: dict[int, list] = {}
        self.event_store: dict[int, list] = {}
        self.reaction_store: dict[int, list] = {}
        # The permission and the role of the people who may do more than read, as
        # GitHub names them: permission is admin, write, read or none, the role also
        # maintain, triage or the name of a custom role.
        self.permission_store: dict[str, tuple[str, str]] = {
            "owner": ("admin", "admin")
        }
        self.discussion_store: list[dict] | None = []
        # The pages of discussions and, by their state, of issues read.
        self.discussion_pages = 0
        self.issue_pages: Counter[str] = Counter()
        # What GitHub's search finds, by the query; nothing for any other query.
        self.search_store: dict[str, list[dict]] = {}
        self.label_store = [
            {
                "name": label.name,
                "color": label.color,
                "description": label.description,
                "node_id": f"L_{label.name}",
            }
            for label in config.labels
        ]
        self.release = "v1.9.0"
        self.branch = "main"
        self.pull_store: list[dict] = []
        # The pages of merged pull requests read, which no test needs many of.
        self.pages = 0
        self.release_store: list[dict] = []
        # Pairs of a merge commit and the tag of a release that contains it.
        self.contained: set[tuple[str, str]] = set()
        self.milestone_store: list[dict] = []
        # The open code scanning alerts; None where code scanning isn't set up.
        self.alert_store: list[dict] | None = []
        self.alert_error: str | None = None
        self.calls: list[tuple] = []
        self.suggestions: list[dict] = []
        self.rationales: dict[tuple[int, str], str] = {}
        self.graphql_error: str | None = None
        # Patterns of the calls that fail, such as "^GET issues/7$", with their error.
        self.failures: list[tuple[str, assistant.GitHubError]] = []
        # Who changed the text of an issue last, and when, by its number, as
        # GitHub's lastEditedAt and editor.
        self.edit_store: dict[int, dict] = {}
        # What the fake writes happens at this moment, which a test can move on.
        self.clock = NOW

    def time(self):
        """The moment of the clock, as GitHub writes it."""
        return self.clock.isoformat().replace("+00:00", "Z")

    def edit(self, number, editor, **changes):
        """Change the title or the text of an issue, now, as `editor`, and return the
        `changes` of GitHub's `edited` event. GitHub keeps the history of the text
        alone: a new title is no edit."""
        issue = self._issue(number)
        found = {key: {"from": issue[key]} for key in changes}
        issue.update(changes, updated_at=self.time())
        if "body" in changes:
            self.edit_store[number] = {
                "lastEditedAt": self.time(),
                "editor": {"login": editor},
            }
        return found

    def fail(self, pattern, error=None):
        """Let every call whose method and path or query match the pattern fail."""
        self.failures.append((pattern, error or rate_limited()))

    def _failing(self, call):
        for pattern, error in self.failures:
            if re.search(pattern, call):
                raise error

    def _issue(self, number):
        if number not in self.store:
            raise not_found()
        return self.store[number]

    def _label(self, name):
        """The label of that name, in any case, as GitHub finds it; None without one."""
        return next(
            (
                label
                for label in self.label_store
                if label["name"].casefold() == name.casefold()
            ),
            None,
        )

    def _add(self, number, names):
        issue = self._issue(number)
        for name in names:
            if name not in assistant.label_names(issue):
                issue["labels"].append({"name": name})
                self.event_store.setdefault(number, []).append(
                    {
                        "event": "labeled",
                        "label": {"name": name},
                        "actor": BOT_USER,
                        "created_at": self.time(),
                    }
                )

    def _gh(self, args, data=None, *, read=True):
        """GitHub's search of issues, the one call that the script sends to gh
        outside the repository: is:issue, is:closed, is:unlocked, closed:<DAY,
        -label:"NAME" and no:assignee, the oldest first."""
        parts = urlsplit(args[1])
        assert parts.path == "search/issues", args
        query = parse_qs(parts.query)
        assert (query["sort"], query["order"]) == (["created"], ["asc"])
        self.calls.append(("SEARCH", query["q"][0], None))
        self._failing(f"SEARCH {query['q'][0]}")
        words = re.findall(r'(?:[^\s"]+|"[^"]*")+', query["q"][0])
        assert words[:2] == [f"repo:{REPOSITORY}", "is:issue"], words
        found = []
        for issue in sorted(self.store.values(), key=lambda item: item["number"]):
            fits = True
            for word in words[2:]:
                if word == "is:closed":
                    fits &= issue["state"] == "closed"
                elif word == "is:unlocked":
                    fits &= not issue["locked"]
                elif word == "no:assignee":
                    fits &= not issue.get("assignees")
                elif word.startswith("closed:<"):
                    day = datetime.fromisoformat(word.removeprefix("closed:<"))
                    fits &= bool(issue["closed_at"]) and assistant.moment(
                        issue["closed_at"]
                    ) < day.replace(tzinfo=UTC)
                else:
                    name = re.fullmatch(r'-label:"(.+)"', word)
                    assert name, word
                    fits &= name[1].casefold() not in {
                        label.casefold() for label in assistant.label_names(issue)
                    }
            if fits:
                found.append(deepcopy(issue))
        page = int(query["page"][0])
        return {
            "total_count": len(found),
            "items": found[(page - 1) * 100 : page * 100],
        }

    def rest(self, path, *, method="GET", data=None, pages=False):
        self.calls.append((method, path, deepcopy(data)))
        self._failing(f"{method} {path}")
        parts = urlsplit(path)
        route, query = parts.path, parse_qs(parts.query)
        if method == "GET":
            if match := re.fullmatch(r"issues/(\d+)", route):
                return deepcopy(self._issue(int(match[1])))
            if match := re.fullmatch(r"issues/(\d+)/comments", route):
                # With the address that GitHub gives each comment.
                return [
                    {"html_url": f"{WEB}/issues/{match[1]}#issuecomment-{item['id']}"}
                    | deepcopy(item)
                    for item in self.comment_store.get(int(match[1]), [])
                ]
            if match := re.fullmatch(r"issues/(\d+)/events", route):
                return deepcopy(self.event_store.get(int(match[1]), []))
            if match := re.fullmatch(r"issues/comments/(\d+)/reactions", route):
                return deepcopy(self.reaction_store.get(int(match[1]), []))
            if match := re.fullmatch(r"collaborators/([^/]+)/permission", route):
                user = unquote(match[1])
                # Anyone else can read a public repository.
                permission, role = self.permission_store.get(user, ("read", "read"))
                return {
                    "permission": permission,
                    "role_name": role,
                    "user": {"login": user, "type": "User"},
                }
            if route == "issues":
                items = [
                    issue
                    for issue in self.store.values()
                    if query["state"][0] in ("all", issue["state"])
                    and (
                        "labels" not in query
                        or query["labels"][0] in assistant.label_names(issue)
                    )
                    and changed_since(issue, query)
                ]
                return deepcopy(sorted(items, key=lambda item: -item["number"]))
            if route == "issues/comments":
                # Every comment of the repository, with the address of its issue.
                return [
                    {**deepcopy(comment), "issue_url": f"{API}/issues/{number}"}
                    for number, comments in sorted(self.comment_store.items())
                    for comment in comments
                    if changed_since(comment, query)
                ]
            if route == "labels":
                return deepcopy(self.label_store)
            if route == "releases/latest":
                if not self.release:
                    raise not_found()
                return {"tag_name": self.release}
            if route == "releases":
                # GitHub lists the newest first, and a page of them.
                newest = sorted(
                    self.release_store,
                    key=lambda item: item["published_at"],
                    reverse=True,
                )
                return deepcopy(newest[: int(query["per_page"][0])])
            if route == "milestones":
                return deepcopy(self.milestone_store)
            if route == "code-scanning/alerts":
                if self.alert_error:
                    raise assistant.GitHubError(self.alert_error)
                if self.alert_store is None:
                    raise not_found("no analysis found")
                return deepcopy(self.alert_store)
            if match := re.fullmatch(r"compare/([0-9a-f]+)\.\.\.(.+)", route):
                tag = unquote(match[2])
                if tag not in {item["tag_name"] for item in self.release_store}:
                    raise not_found()
                inside = (match[1], tag) in self.contained
                return {"status": "ahead" if inside else "diverged"}
        if method == "POST":
            if match := re.fullmatch(r"issues/(\d+)/comments", route):
                comment = make_comment(1000 + len(self.calls), "bot", data["body"], 0)
                comment["created_at"] = self.time()
                self.comment_store.setdefault(int(match[1]), []).append(comment)
                return comment
            if match := re.fullmatch(r"issues/(\d+)/labels", route):
                self._add(int(match[1]), data["labels"])
                return []
            if match := re.fullmatch(r"issues/comments/(\d+)/reactions", route):
                # The same reaction of the same person again changes nothing.
                reaction = {"content": data["content"], "user": BOT_USER}
                reactions = self.reaction_store.setdefault(int(match[1]), [])
                if reaction not in reactions:
                    reactions.append(reaction)
                return reaction
            if route == "labels":
                # GitHub ignores the case of label names.
                if self._label(data["name"]):
                    raise assistant.GitHubError(
                        "gh api: Validation Failed: already_exists (HTTP 422)",
                        status=422,
                    )
                self.label_store.append({**data, "node_id": f"L_{data['name']}"})
                return data
            if route == "milestones":
                milestone = {**data, "number": len(self.milestone_store) + 1}
                self.milestone_store.append(milestone)
                return milestone
        if method == "PUT" and (match := re.fullmatch(r"issues/(\d+)/lock", route)):
            issue = self._issue(int(match[1]))
            assert data == {"lock_reason": "resolved"}
            issue.update(locked=True, active_lock_reason="resolved")
            self.event_store.setdefault(int(match[1]), []).append(
                {"event": "locked", "actor": BOT_USER, "created_at": self.time()}
            )
            return None
        if method == "DELETE" and (
            match := re.fullmatch(r"issues/(\d+)/labels/(.+)", route)
        ):
            issue = self._issue(int(match[1]))
            # A name is one segment of the path: GitHub reads an unencoded slash
            # as the start of another.
            if "/" in match[2]:
                raise not_found()
            name = unquote(match[2])
            if name not in assistant.label_names(issue):
                raise not_found("Label does not exist")
            issue["labels"] = [
                label for label in issue["labels"] if label["name"] != name
            ]
            return None
        if method == "PATCH":
            if match := re.fullmatch(r"issues/(\d+)", route):
                issue = self._issue(int(match[1]))
                issue.update(data)
                if data.get("state") == "closed":
                    issue["closed_at"] = self.time()
                if data.get("state") == "open":
                    issue["state_reason"] = "reopened"
                    self.event_store.setdefault(int(match[1]), []).append(
                        {
                            "event": "reopened",
                            "actor": BOT_USER,
                            "created_at": self.time(),
                        }
                    )
                return issue
            if match := re.fullmatch(r"issues/comments/(\d+)", route):
                comment = next(
                    item
                    for comments in self.comment_store.values()
                    for item in comments
                    if item["id"] == int(match[1])
                )
                comment.update(data, updated_at=self.time())
                return comment
            if match := re.fullmatch(r"code-scanning/alerts/(\d+)", route):
                alert = next(
                    item
                    for item in self.alert_store or []
                    if item["number"] == int(match[1])
                )
                alert.update(data)
                return alert
            if match := re.fullmatch(r"labels/(.+)", route):
                label = self._label(unquote(match[1]))
                if "/" in match[1] or label is None:
                    raise not_found()
                changes = dict(data)
                if "new_name" in changes:
                    changes["name"] = changes.pop("new_name")
                label.update(changes)
                return label
        raise AssertionError(f"unexpected call {method} {path}")

    def graphql(self, query, variables):
        self.calls.append(("GRAPHQL", query, deepcopy(variables)))
        self._failing(f"GRAPHQL {query}")
        if self.graphql_error:
            raise assistant.GitHubError(self.graphql_error)
        if "discussions(" in query:
            # A repository without discussions has none, as GitHub answers; a page
            # of them after the cursor, by their last update.
            assert "orderBy: {field: UPDATED_AT, direction: DESC}" in query
            self.discussion_pages += 1
            assert self.discussion_pages <= 50, "endless paging of the discussions"
            nodes, info = page_of(self.discussion_store or [], variables)
            return {"repository": {"discussions": {"pageInfo": info, "nodes": nodes}}}
        if "issues(states:" in query:
            # The issues alone, never a pull request, by their last update.
            assert "orderBy: {field: UPDATED_AT, direction: DESC}" in query
            (state,) = variables["states"]
            self.issue_pages[state] += 1
            assert self.issue_pages[state] <= 50, "endless paging of the issues"
            issues = sorted(
                (
                    issue
                    for issue in self.store.values()
                    if "pull_request" not in issue and issue["state"].upper() == state
                ),
                key=lambda issue: issue["updated_at"],
                reverse=True,
            )
            nodes, info = page_of([as_graphql(issue) for issue in issues], variables)
            return {"repository": {"issues": {"pageInfo": info, "nodes": nodes}}}
        if "discussion(number" in query:
            found = [
                item
                for item in self.discussion_store or []
                if item["number"] == variables["number"]
            ]
            if not found:
                raise assistant.GitHubError(
                    "Could not resolve to a Discussion with the number of "
                    f"{variables['number']}.",
                    types={"NOT_FOUND"},
                )
            return {"repository": {"discussion": found[0]}}
        if "pullRequests(states: MERGED" in query:
            # This repository's merged pull requests, by their last update, a page of
            # them after the cursor, which is the place in the list.
            assert "orderBy: {field: UPDATED_AT, direction: DESC}" in query
            # Paging that never ends fails the test instead of filling the memory.
            self.pages += 1
            assert self.pages <= 50, "endless paging of the merged pull requests"
            size = int(re.search(r"pullRequests\([^)]*first: (\d+)", query)[1])
            pulls = sorted(
                (
                    pull
                    for pull in self.pull_store
                    if pull["merged"]
                    and pull["repository"]["nameWithOwner"] == REPOSITORY
                ),
                key=lambda pull: pull["updatedAt"],
                reverse=True,
            )
            start = int(variables["cursor"] or 0)
            end = start + size
            nodes = deepcopy(pulls[start:end])
            # The state of each issue at the time of the query, unless a test pins
            # the one that GitHub reported before the issue changed.
            for pull in nodes:
                for ref in pull["closingIssuesReferences"]["nodes"]:
                    here = ref["repository"]["nameWithOwner"] == REPOSITORY
                    issue = self.store.get(ref["number"]) if here else None
                    ref.setdefault(
                        "state", (issue or {"state": "open"})["state"].upper()
                    )
            return {
                "repository": {
                    "defaultBranchRef": {"name": self.branch},
                    "pullRequests": {
                        "pageInfo": {
                            "hasNextPage": end < len(pulls),
                            "endCursor": str(end),
                        },
                        "nodes": nodes,
                    },
                }
            }
        if "closedByPullRequestsReferences" in query:
            # Every pull request that names the issue, of any repository.
            nodes = [
                {
                    key: pull[key]
                    for key in (
                        "number",
                        "merged",
                        "mergedAt",
                        "baseRefName",
                        "repository",
                    )
                }
                | {"mergeCommit": pull["mergeCommit"] if pull["merged"] else None}
                for pull in self.pull_store
                if (variables["number"], REPOSITORY)
                in {
                    (ref["number"], ref["repository"]["nameWithOwner"])
                    for ref in pull["closingIssuesReferences"]["nodes"]
                }
            ]
            return {
                "repository": {
                    "defaultBranchRef": {"name": self.branch},
                    "issue": {"closedByPullRequestsReferences": {"nodes": nodes}},
                }
            }
        if "lastEditedAt" in query:
            self._issue(variables["number"])
            edit = self.edit_store.get(
                variables["number"], {"lastEditedAt": None, "editor": None}
            )
            return {"repository": {"issue": deepcopy(edit)}}
        number = int(variables["issue"].removeprefix("I_"))
        if "addLabelsToLabelable" in query:
            names = [item["labelId"].removeprefix("L_") for item in variables["labels"]]
            self._add(number, names)
            for item in variables["labels"]:
                self.rationales[(number, item["labelId"])] = item["rationale"]
            return {"addLabelsToLabelable": {"clientMutationId": None}}
        if "isSuggestion: true" in query:
            self.suggestions.append(variables)
            return {"closeIssue": {"issue": {"number": number}}}
        if "closeIssue" in query:
            # The values of GitHub's IssueClosedStateReason.
            assert variables["reason"] in {"COMPLETED", "NOT_PLANNED", "DUPLICATE"}
            issue = self._issue(number)
            issue["state"] = "closed"
            issue["closed_at"] = self.time()
            issue["state_reason"] = variables["reason"].lower()
            issue["duplicate_of"] = variables["duplicate"]
            issue["rationale"] = variables["rationale"]
            self.event_store.setdefault(number, []).append(
                {"event": "closed", "actor": BOT_USER, "created_at": self.time()}
            )
            return {"closeIssue": {"issue": {"number": number}}}
        raise AssertionError(f"unexpected query {query}")

    def search(self, query):
        self.calls.append(("SEARCH", query, None))
        self._failing(f"SEARCH {query}")
        scope = f"repo:{REPOSITORY} is:issue ("
        assert query.startswith(scope) and query.endswith(")"), query
        words = query.removeprefix(scope).removesuffix(")").split(" OR ")
        # GitHub's search takes at most five operators and 256 characters.
        assert len(words) <= assistant.SEARCH_TERMS, query
        assert len(query) <= 256, query
        found = deepcopy(self.search_store.get(query, [])[: assistant.SEARCHED])
        for item in found:
            item.setdefault(
                "repository_url", f"https://api.github.com/repos/{REPOSITORY}"
            )
        return found

    # Helpers for the tests

    def labels_of(self, number):
        return assistant.label_names(self.store[number])

    def posted(self, number):
        return [
            comment["body"]
            for comment in self.comment_store.get(number, [])
            if comment["user"] == BOT_USER and comment["created_at"] == self.time()
        ]

    def writes_made(self):
        return [
            call
            for call in self.calls
            if call[0] not in {"GET", "SEARCH"}
            and not (call[0] == "GRAPHQL" and "mutation" not in call[1])
        ]


# The issues of the repository in which the tests and the fuzzers post whole
# comments: open, triaged and older or newer than #12, the issue they comment on.
ISSUES = range(1, 21)


def posted_comments(config, mode, answer, sha=REF):
    """What the assistant posts on #12 for the answer, and the issue numbers that it
    may reference there: the other issues that it looked up and found, as duplicates,
    related issues or numbers that its texts name. Nothing for an answer that the
    checks refuse as a whole.

    A follow-up answers the reporter, who answered the questions of the analysis,
    and an answer the question of a maintainer, comment 2.
    """
    github = FakeGitHub(
        config,
        *(make_issue(number, labels=("bug",)) for number in ISSUES if number != 12),
        make_issue(),
    )
    if mode == "follow-up":
        github.comment_store[12] = [
            analysis(questions=1, days_ago=1),
            make_comment(2, "reporter", "Version 1.9.0.", 0.5),
        ]
    if mode == "maintainer-question":
        github.comment_store[12] = [
            make_comment(2, "maintainer", "/assistant ask Why?", 0.5, "OWNER")
        ]
    try:
        assistant.apply_answer(github, mode, 12, json.dumps(answer), config, sha)
    except assistant.AssistantError:
        return [], set()
    looked_up = {
        int(match[1])
        for method, path, _ in github.calls
        if method == "GET" and (match := re.fullmatch(r"issues/(\d+)", path))
    }
    return github.posted(12), (looked_up & set(ISSUES)) - {12}


def own_details(body):
    """The HTML that folds the links of a comment, as the assistant writes it."""
    folds = "|".join(
        re.escape(texts["links"]).replace(r"\{count\}", r"\d+")
        for texts in assistant.TEXT.values()
    )
    opening = re.search(rf"^<details><summary>(?:{folds})</summary>$", body, re.M)
    return [f"HTML {opening[0]!r}", "HTML '</details>'"] if opening else []


def stray_problems(body, numbers, config, author="reporter", *, security=False):
    """What the oracle finds in a comment of the assistant beyond its own words: its
    marker, the thanks that mention the reporter, the HTML of its footer and of the
    fold of its links and, in the comment on a possible security report, the link to
    the security policy, and in an answer, the link to the question of #12 that it
    answers, each once at most."""
    marker = body.partition("\n")[0]
    question = re.match(r"<!-- issue-assistant:answer .*?\bto=(\d+)", marker)
    own = Counter(
        [
            f"HTML {marker!r}",
            f"mention @{author}",
            "HTML '<sub>'",
            "HTML '</sub>'",
            *own_details(body),
            *([f"link to {config.policy}"] if security else []),
            *(
                [f"link to {WEB}/issues/12#issuecomment-{question[1]}"]
                if question
                else []
            ),
        ]
    )
    return list((Counter(problems(body, numbers, config)) - own).elements())


def run_main(monkeypatch, tmp_path, root, github, *argv, **env):
    outputs = tmp_path / "output"
    summary = tmp_path / "summary"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_WORKSPACE", str(root))
    monkeypatch.setenv("GH_REPO", REPOSITORY)
    for key in (
        "DRY_RUN",
        "ISSUE",
        "MODE",
        "RESULT",
        "ENGINE",
        "READY_ENGINES",
        "ISSUE_ASSISTANT_ROOT",
        "ISSUE_ASSISTANT_FIXTURE",
        "GITHUB_EVENT_NAME",
        # The event of the CI run that runs the tests, which names no issue here.
        "GITHUB_EVENT_PATH",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    if github is not None:
        monkeypatch.setattr(assistant, "GitHub", lambda repository, dry_run: github)
    code = assistant.main(list(argv))
    text = outputs.read_text("utf-8") if outputs.exists() else ""
    return code, text, summary.read_text("utf-8") if summary.exists() else ""


# Texts of the engine that once got past the cleaning, with the issue numbers that
# were checked: found in review, in GitHub's rendering and by the fuzzer, whose
# finding of 2026-10-08 is the first of them. The tests judge each where the
# comment puts it.
FUZZ_FINDING = "\n".join(
    [
        "\0\0" + "\x1d" * 14 + "\0~~~",
        "~~\x01~[~~",
        "~~~~~~",
        "~~8\x18~~",
        "~~8[~\x7f\x7fA&&(~\x16~~~~~~" + ">" * 30,
        "\t_@sI\x01,",
        "*",
        "\x02A_:\x01\x0f",
        "*8",
        "*\t)_:,~",
        "~~~",
    ]
)
ESCAPES = [
    (FUZZ_FINDING, set()),
    (FUZZ_FINDING, {1}),
    ("\x1d~~~\nx\n~~~~\n@octocat\n~~~~", set()),
    ("`a`\n#\n```\n@octocat\n````", set()),
    ("`a`<!-- b\n```\n@octocat\n```", set()),
    ("```\n@octocat\n````", set()),
    ("```python\n@dataclass\n```", set()),
    ("```\n@octocat <img src=https://evil.example/p.png>\n```", set()),
    ("```\n\t```\n@octocat\n```", set()),
    ("- item\n  ```\n@octocat https://evil.example/x\n```", set()),
    ("<b></b>    ~~~\nx\n~~~\n@octocat\n~~~", set()),
    ("foo/bar<<x>y>#12", {12}),
    ("see #1<<b>i>337 here", {1}),
    ("!<<x>y>[pic](https://docs.example.com/page.png)", set()),
    ("x_www.evil.example y", set()),
    ("A _www.evil.example/@octocat y", set()),
    # Found on 2026-10-13 by the oracle of tests/oracle.py, which models GitHub's
    # tables and autolinks, and by fuzz/fuzz_render.py, which fuzzes whole comments.
    ("f | g\n--|--\n`x|@octocat https://evil.example/x` | y", set()),
    ("a\n:-:\n`https://evil.example/x|y`", set()),
    ("see www. here", set()),
    ("~~~~~~\n@octocat\n~~~", set()),
    ("#0@GH-5", {5}),
    ("[x](https://github.com/owner/project/blob/(a)/../../../../evil/repo)", set()),
    ("[x](https://github.com/owner/project/blob/&#46;&#46;/&#46;&#46;/e/r)", set()),
]
