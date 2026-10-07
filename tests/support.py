"""Builders for issues, comments and answers, and a fake of the GitHub API.

The fake answers the REST and GraphQL calls of the script, so the real request code
runs; only `gh` itself is replaced.
"""

import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, unquote, urlsplit

import issue_assistant as assistant

REPOSITORY = "owner/project"
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


def at(days_ago: float = 0) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


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


def make_pull(number=40, fixes=(12,), *, days_ago=1, base="main", merged=True, **extra):
    """A merged pull request with the issues that it fixes."""
    return {
        "number": number,
        "merged": merged,
        "mergedAt": at(days_ago),
        "baseRefName": base,
        "mergeCommit": {"oid": f"{number:040x}"},
        "closingIssuesReferences": {
            "nodes": [
                {"number": issue, "repository": {"nameWithOwner": REPOSITORY}}
                for issue in fixes
            ]
        },
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


def analysis(comment_id=1, days_ago=1, **attributes):
    marker = assistant.render_marker("analysis", lang="en", **attributes)
    return make_comment(comment_id, "bot", f"{marker}\nFirst analysis", days_ago)


class FakeGitHub(assistant.GitHub):
    """The REST and GraphQL endpoints the script uses, in memory."""

    def __init__(self, config, *issues, dry_run=False):
        super().__init__(REPOSITORY, dry_run=dry_run)
        self.store = {issue["number"]: deepcopy(issue) for issue in issues}
        self.comment_store: dict[int, list] = {}
        self.event_store: dict[int, list] = {}
        self.reaction_store: dict[int, list] = {}
        self.discussion_store: list[dict] | None = []
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
        self.release_store: list[dict] = []
        # Pairs of a merge commit and the tag of a release that contains it.
        self.contained: set[tuple[str, str]] = set()
        self.calls: list[tuple] = []
        self.suggestions: list[dict] = []
        self.rationales: dict[tuple[int, str], str] = {}
        self.graphql_error: str | None = None

    def _issue(self, number):
        if number not in self.store:
            raise assistant.GitHubError("gh api: Not Found (HTTP 404)")
        return self.store[number]

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
                        "created_at": at(),
                    }
                )

    def rest(self, path, *, method="GET", data=None, pages=False):
        self.calls.append((method, path, deepcopy(data)))
        parts = urlsplit(path)
        route, query = parts.path, parse_qs(parts.query)
        if method == "GET":
            if match := re.fullmatch(r"issues/(\d+)", route):
                return deepcopy(self._issue(int(match[1])))
            if match := re.fullmatch(r"issues/(\d+)/comments", route):
                return deepcopy(self.comment_store.get(int(match[1]), []))
            if match := re.fullmatch(r"issues/(\d+)/events", route):
                return deepcopy(self.event_store.get(int(match[1]), []))
            if match := re.fullmatch(r"issues/comments/(\d+)/reactions", route):
                return deepcopy(self.reaction_store.get(int(match[1]), []))
            if route == "issues":
                items = [
                    issue
                    for issue in self.store.values()
                    if query["state"][0] in ("all", issue["state"])
                    and (
                        "labels" not in query
                        or query["labels"][0] in assistant.label_names(issue)
                    )
                ]
                return deepcopy(sorted(items, key=lambda item: -item["number"]))
            if route == "labels":
                return deepcopy(self.label_store)
            if route == "releases/latest":
                if not self.release:
                    raise assistant.GitHubError("gh api: Not Found (HTTP 404)")
                return {"tag_name": self.release}
            if route == "releases":
                return deepcopy(self.release_store)
            if match := re.fullmatch(r"compare/([0-9a-f]+)\.\.\.(.+)", route):
                tag = unquote(match[2])
                if tag not in {item["tag_name"] for item in self.release_store}:
                    raise assistant.GitHubError("gh api: Not Found (HTTP 404)")
                inside = (match[1], tag) in self.contained
                return {"status": "ahead" if inside else "diverged"}
        if method == "POST":
            if match := re.fullmatch(r"issues/(\d+)/comments", route):
                comment = make_comment(1000 + len(self.calls), "bot", data["body"], 0)
                self.comment_store.setdefault(int(match[1]), []).append(comment)
                return comment
            if match := re.fullmatch(r"issues/(\d+)/labels", route):
                self._add(int(match[1]), data["labels"])
                return []
            if route == "labels":
                self.label_store.append({**data, "node_id": f"L_{data['name']}"})
                return data
        if method == "DELETE" and (
            match := re.fullmatch(r"issues/(\d+)/labels/(.+)", route)
        ):
            issue = self._issue(int(match[1]))
            name = unquote(match[2])
            if name not in assistant.label_names(issue):
                raise assistant.GitHubError("gh api: Label does not exist (HTTP 404)")
            issue["labels"] = [
                label for label in issue["labels"] if label["name"] != name
            ]
            return None
        if method == "PATCH":
            if match := re.fullmatch(r"issues/(\d+)", route):
                issue = self._issue(int(match[1]))
                issue.update(data)
                if data["state"] == "open":
                    issue["state_reason"] = "reopened"
                return issue
            if match := re.fullmatch(r"labels/(.+)", route):
                label = next(
                    item
                    for item in self.label_store
                    if item["name"] == unquote(match[1])
                )
                label.update(data)
                return label
        raise AssertionError(f"unexpected call {method} {path}")

    def graphql(self, query, variables):
        self.calls.append(("GRAPHQL", query, deepcopy(variables)))
        if self.graphql_error:
            raise assistant.GitHubError(self.graphql_error)
        if "discussions(" in query:
            if self.discussion_store is None:
                raise assistant.GitHubError("Discussions are not enabled")
            return {"repository": {"discussions": {"nodes": self.discussion_store}}}
        if "discussion(number" in query:
            found = [
                item
                for item in self.discussion_store or []
                if item["number"] == variables["number"]
            ]
            return {"repository": {"discussion": found[0] if found else None}}
        if "pullRequests(states: MERGED" in query:
            return {
                "repository": {
                    "defaultBranchRef": {"name": self.branch},
                    "pullRequests": {"nodes": deepcopy(self.pull_store)},
                }
            }
        if "closedByPullRequestsReferences" in query:
            nodes = [
                {
                    key: pull[key]
                    for key in ("number", "merged", "mergedAt", "baseRefName")
                }
                | {"mergeCommit": pull["mergeCommit"] if pull["merged"] else None}
                for pull in self.pull_store
                if variables["number"]
                in {ref["number"] for ref in pull["closingIssuesReferences"]["nodes"]}
            ]
            return {
                "repository": {
                    "defaultBranchRef": {"name": self.branch},
                    "issue": {"closedByPullRequestsReferences": {"nodes": nodes}},
                }
            }
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
            issue = self._issue(number)
            issue["state"] = "closed"
            issue["state_reason"] = variables["reason"].lower()
            issue["duplicate_of"] = variables["duplicate"]
            issue["rationale"] = variables["rationale"]
            self.event_store.setdefault(number, []).append(
                {"event": "closed", "actor": BOT_USER, "created_at": at()}
            )
            return {"closeIssue": {"issue": {"number": number}}}
        raise AssertionError(f"unexpected query {query}")

    # Helpers for the tests

    def labels_of(self, number):
        return assistant.label_names(self.store[number])

    def posted(self, number):
        return [
            comment["body"]
            for comment in self.comment_store.get(number, [])
            if comment["user"] == BOT_USER and comment["created_at"] == at()
        ]

    def writes_made(self):
        return [
            call
            for call in self.calls
            if call[0] != "GET"
            and not (call[0] == "GRAPHQL" and "mutation" not in call[1])
        ]


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
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    if github is not None:
        monkeypatch.setattr(assistant, "GitHub", lambda repository, dry_run: github)
    code = assistant.main(list(argv))
    text = outputs.read_text("utf-8") if outputs.exists() else ""
    return code, text, summary.read_text("utf-8") if summary.exists() else ""
