"""GitHub: every request of the assistant, through gh, and GraphQL's answers as REST's."""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable, Iterator
from copy import deepcopy
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

from .constants import BOT
from .errors import GitHubError
from .fixture import Fixture
from .settings import Label
from .values import command, login, moment, stamp

# The version of GitHub's REST API that every call of gh asks for, so that an answer
# doesn't follow whatever version GitHub serves by default. tests/test_rest_contract.py
# checks every request against GitHub's description of this version.
API_VERSION = "2022-11-28"
API_VERSION_HEADER = f"X-GitHub-Api-Version: {API_VERSION}"

# GitHub's search returns at most 1000 results, 10 pages of 100.
SEARCH_PAGES = 10

# A first analysis also reads the issues that GitHub's search finds for the words of
# the new issue, so that an older duplicate is found beyond the closed issues above.
SEARCHED = 30

EVALUATION_PAGES = 10


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
        # The comments and events read in this run, by their path, once remember()
        # turned it on: a sweep reads them for its steps and again for its digest.
        self._memo: dict[str, list[dict[str, Any]]] | None = None
        # The issue that a file holds in place of GitHub.
        self.fixture: Fixture | None = None
        # The related repositories read in this run, by their name; see sibling().
        self._siblings: dict[str, GitHub | None] = {}

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
                ["gh", *args, "--header", API_VERSION_HEADER],
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

    def default_branch(self) -> str:
        """The default branch of the repository, which `install --setup` reads."""
        repository = self._gh(["api", f"repos/{self.repository}"])
        return str(repository["default_branch"])

    def public(self) -> bool:
        """Whether the repository is public; not one that the token can't see."""
        try:
            repository = self._gh(["api", f"repos/{self.repository}"])
        except GitHubError as error:
            if not error.missing():
                raise
            return False
        return bool(repository.get("visibility") == "public")

    def sibling(self, repository: str) -> GitHub | None:
        """The related repository of [related] with that name, to read and never to
        write, once it is public; None for one that isn't, or doesn't exist.

        Its issues become links in a comment here and their texts part of the log
        of the run, which can be public, so the issues of a private one stay out.
        """
        if repository not in self._siblings:
            other = GitHub(repository, dry_run=True)
            self._siblings[repository] = other if other.public() else None
        return self._siblings[repository]

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

    def remember(self) -> None:
        """Read the comments and the events of an issue once, until a write of this
        run changes the issue: for a sweep, whose digest reads what its steps read."""
        self._memo = {}

    def _remembered(self, path: str) -> list[dict[str, Any]]:
        if self._memo is None:
            found: list[dict[str, Any]] = self.rest(path, pages=True)
            return found
        if path not in self._memo:
            self._memo[path] = self.rest(path, pages=True)
        return deepcopy(self._memo[path])

    def _forget(self, description: str) -> None:
        """Forget what was read of the issues that a write names; of all, when it
        names none, as an edit of a comment."""
        if self._memo is None:
            return
        numbers = re.findall(r"#(\d+)", description)
        if not numbers:
            self._memo.clear()
        for number in numbers:
            for kind in ("comments", "events"):
                self._memo.pop(f"issues/{number}/{kind}?per_page=100", None)

    def _write(self, description: str, action: Callable[[], object]) -> None:
        print(("Would " if self.dry_run else "") + description)
        if not self.dry_run:
            self._forget(description)
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
        return self._remembered(f"issues/{number}/comments?per_page=100")

    def events(self, number: int) -> list[dict[str, Any]]:
        return self._remembered(f"issues/{number}/events?per_page=100")

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

    def recent_issues(self, closed: int) -> list[dict[str, Any]]:
        """Every open issue and the `closed` closed ones that changed last, in the
        form of the REST API, with the pull request that closed each, if one did.

        GraphQL's issues are issues alone, while REST lists pull requests among them,
        and its pages are as many as the repository has issues and pull requests.
        """
        found: list[dict[str, Any]] = []
        for state, most in (("OPEN", None), ("CLOSED", closed)):
            cursor = None
            taken = 0
            while most is None or taken < most:
                data = self.graphql(
                    """
                    query($owner: String!, $name: String!, $states: [IssueState!],
                          $first: Int!, $cursor: String) {
                      repository(owner: $owner, name: $name) {
                        issues(states: $states, first: $first, after: $cursor,
                               orderBy: {field: UPDATED_AT, direction: DESC}) {
                          pageInfo { hasNextPage endCursor }
                          nodes {
                            number title body state stateReason(enableDuplicate: true)
                            createdAt closedAt
                            author { __typename login }
                            labels(first: 20) { nodes { name } }
                            comments { totalCount }
                            closedByPullRequestsReferences(first: 5, includeClosedPrs: true) {
                              nodes { number merged repository { nameWithOwner } }
                            }
                            timelineItems(itemTypes: [CLOSED_EVENT], last: 1) {
                              nodes {
                                ... on ClosedEvent {
                                  closer { ... on PullRequest { number } }
                                }
                              }
                            }
                          }
                        }
                      }
                    }
                    """,
                    {
                        "owner": self.owner,
                        "name": self.name,
                        "states": [state],
                        "first": 100 if most is None else min(100, most - taken),
                        "cursor": cursor,
                    },
                )
                page = data["repository"]["issues"]
                found += [as_rest(node, self.owns) for node in page["nodes"]]
                taken += len(page["nodes"])
                if not page["pageInfo"]["hasNextPage"]:
                    break
                cursor = page["pageInfo"]["endCursor"]
        return found

    def closed_issues(self) -> Iterator[dict[str, Any]]:
        """The closed issues, the newest first, in the form of the REST API with
        `duplicate_of`: the issue of this repository that each was closed or marked
        as a duplicate of, with its number and when it was opened, or None.

        The pages come one by one as `evaluate` reads on, up to EVALUATION_PAGES.
        """
        cursor = None
        for _ in range(EVALUATION_PAGES):
            data = self.graphql(
                """
                query($owner: String!, $name: String!, $cursor: String) {
                  repository(owner: $owner, name: $name) {
                    issues(first: 100, after: $cursor, states: [CLOSED],
                           orderBy: {field: CREATED_AT, direction: DESC}) {
                      pageInfo { hasNextPage endCursor }
                      nodes {
                        number title body createdAt closedAt
                        stateReason(enableDuplicate: true) authorAssociation
                        author { __typename login }
                        labels(first: 20) { nodes { name } }
                        duplicateOf {
                          number createdAt repository { nameWithOwner }
                        }
                        timelineItems(
                          itemTypes: [MARKED_AS_DUPLICATE_EVENT,
                                      UNMARKED_AS_DUPLICATE_EVENT],
                          last: 1
                        ) {
                          nodes {
                            ... on MarkedAsDuplicateEvent {
                              canonical {
                                ... on Issue {
                                  number createdAt repository { nameWithOwner }
                                }
                              }
                            }
                          }
                        }
                      }
                    }
                  }
                }
                """,
                {"owner": self.owner, "name": self.name, "cursor": cursor},
            )
            page = data["repository"]["issues"]
            yield from (closed_as_rest(node, self.owns) for node in page["nodes"])
            if not page["pageInfo"]["hasNextPage"]:
                return
            cursor = page["pageInfo"]["endCursor"]

    def search_issues(self, terms: list[str]) -> list[dict[str, Any]]:
        """The issues of the repository that GitHub's search finds best for any of
        the words, at most SEARCHED."""
        # The parentheses keep `repo:` on every word, also where AND binds closer
        # than OR, as in GitHub's advanced search.
        found = self.search(f"repo:{self.repository} is:issue ({' OR '.join(terms)})")
        own = f"/repos/{self.repository}".lower()
        return [
            item
            for item in found
            if "pull_request" not in item
            and str(item.get("repository_url", "")).lower().endswith(own)
        ]

    def search(self, query: str) -> list[dict[str, Any]]:
        """The SEARCHED issues and pull requests that GitHub's search finds best for
        the query, in the form of the REST API."""
        found = self._gh(
            [
                "api",
                "--method",
                "GET",
                "search/issues",
                "-f",
                f"q={query}",
                "-F",
                f"per_page={SEARCHED}",
            ]
        )
        items: list[dict[str, Any]] = found["items"]
        return items

    def search_issues_where(self, qualifiers: str) -> Iterator[dict[str, Any]]:
        """The issues of the repository, no pull requests, that GitHub's search
        finds with the qualifiers, such as is:closed, the oldest first.

        The pages come one by one as the caller reads on, up to the 1000 results
        that the search returns at most: issues that the caller skips, such as
        those that someone unlocked, never hide the ones after them.
        """
        query = f"repo:{self.repository} is:issue {qualifiers}"
        for page in range(1, SEARCH_PAGES + 1):
            found = self._gh(
                [
                    "api",
                    "search/issues?"
                    + urlencode({"q": query, "sort": "created", "order": "asc"})
                    + f"&per_page=100&page={page}",
                ]
            )
            items: list[dict[str, Any]] = found["items"]
            yield from items
            if len(items) < 100:
                return

    def issues_since(self, since: datetime) -> list[dict[str, Any]]:
        """The issues opened or changed at or after the moment."""
        items = self.rest(
            f"issues?state=all&since={stamp(since)}&per_page=100", pages=True
        )
        return [item for item in items if "pull_request" not in item]

    def issues_of_the_assistant(self) -> list[dict[str, Any]]:
        """Every issue that the assistant's account opened, also a closed one."""
        items: list[dict[str, Any]] = self.rest(
            f"issues?state=all&creator={quote(BOT, safe='')}&per_page=100", pages=True
        )
        return items

    def comments_since(self, since: datetime) -> list[dict[str, Any]]:
        """The comments on issues and pull requests written or changed at or after
        the moment."""
        comments: list[dict[str, Any]] = self.rest(
            f"issues/comments?since={stamp(since)}&per_page=100", pages=True
        )
        return comments

    def latest_comments(self, pages: int) -> list[dict[str, Any]]:
        """The comments on issues and pull requests written last, the newest first:
        at most `pages` pages of a hundred, so that a busy repository costs no more
        calls than a quiet one."""
        found: list[dict[str, Any]] = []
        for page in range(1, pages + 1):
            items: list[dict[str, Any]] = self.rest(
                f"issues/comments?sort=created&direction=desc&per_page=100&page={page}"
            )
            found += items
            if len(items) < 100:
                break
        return found

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

    def discussions(self, most: int) -> list[dict[str, Any]]:
        """The `most` discussions that changed last, with the moments when each
        and its answer were written, for `evaluate`."""
        nodes: list[dict[str, Any]] = []
        cursor = None
        while len(nodes) < most:
            try:
                data = self.graphql(
                    """
                    query($owner: String!, $name: String!, $first: Int!,
                          $cursor: String) {
                      repository(owner: $owner, name: $name) {
                        discussions(first: $first, after: $cursor,
                                    orderBy: {field: UPDATED_AT, direction: DESC}) {
                          pageInfo { hasNextPage endCursor }
                          nodes {
                            number title closed isAnswered body createdAt
                            category { name }
                            answer { body createdAt }
                          }
                        }
                      }
                    }
                    """,
                    {
                        "owner": self.owner,
                        "name": self.name,
                        "first": min(100, most - len(nodes)),
                        "cursor": cursor,
                    },
                )
            except GitHubError as error:
                # A repository without discussions has none; GitHub says so only
                # when the repository itself is gone.
                if not error.missing():
                    raise
                return []
            page = data["repository"]["discussions"]
            nodes += page["nodes"]
            if not page["pageInfo"]["hasNextPage"]:
                break
            cursor = page["pageInfo"]["endCursor"]
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

    def discussion(self, number: int) -> dict[str, Any] | None:
        """The discussion with its comments and their replies, in the form of the
        REST API, as the assistant reads issues; None when there is none.

        The first 50 comments, each with its first 20 replies, and the number of
        all: the first answer comes within minutes of the question, among the
        first comments.
        """
        try:
            data = self.graphql(
                """
                query($owner: String!, $name: String!, $number: Int!) {
                  repository(owner: $owner, name: $name) {
                    discussion(number: $number) {
                      id number title body createdAt closed locked isAnswered
                      authorAssociation
                      author { __typename login }
                      category { name isAnswerable }
                      comments(first: 50) {
                        totalCount
                        nodes {
                          body createdAt authorAssociation
                          author { __typename login }
                          replies(first: 20) {
                            nodes {
                              body createdAt authorAssociation
                              author { __typename login }
                            }
                          }
                        }
                      }
                    }
                  }
                }
                """,
                {"owner": self.owner, "name": self.name, "number": number},
            )
        except GitHubError as error:
            if not error.missing():
                raise
            return None
        node = data["repository"]["discussion"]
        return discussion_as_rest(node) if node else None

    def discussions_since(self, since: datetime) -> list[dict[str, Any]]:
        """The discussions opened at or after the moment, the newest first, with
        their author, its role and their category, in the form of the REST API."""
        found: list[dict[str, Any]] = []
        cursor = None
        while True:
            try:
                data = self.graphql(
                    """
                    query($owner: String!, $name: String!, $first: Int!,
                          $cursor: String) {
                      repository(owner: $owner, name: $name) {
                        discussions(first: $first, after: $cursor,
                                    orderBy: {field: CREATED_AT, direction: DESC}) {
                          pageInfo { hasNextPage endCursor }
                          nodes {
                            number createdAt authorAssociation
                            author { __typename login }
                            category { name }
                          }
                        }
                      }
                    }
                    """,
                    {
                        "owner": self.owner,
                        "name": self.name,
                        "first": 100,
                        "cursor": cursor,
                    },
                )
            except GitHubError as error:
                # A repository without discussions has none, as in discussions().
                if not error.missing():
                    raise
                return found
            page = data["repository"]["discussions"]
            for node in page["nodes"]:
                if moment(node["createdAt"]) < since:
                    return found
                found.append(
                    {
                        "number": node["number"],
                        "category": {"name": node["category"]["name"]},
                        **post_as_rest(node),
                    }
                )
            if not page["pageInfo"]["hasNextPage"]:
                return found
            cursor = page["pageInfo"]["endCursor"]

    def last_edit(self, number: int) -> tuple[str, datetime] | None:
        """Who changed the text of the issue last, and when; None while nobody did.

        GitHub keeps the history of the text alone, so a new title is no edit. The
        issue of a fixture has no history.
        """
        if self.fixture and number == self.fixture.number:
            return None
        data = self.graphql(
            """
            query($owner: String!, $name: String!, $number: Int!) {
              repository(owner: $owner, name: $name) {
                issue(number: $number) { lastEditedAt editor { login } }
              }
            }
            """,
            {"owner": self.owner, "name": self.name, "number": number},
        )
        issue = data["repository"]["issue"]
        if not issue["lastEditedAt"]:
            return None
        return login(issue["editor"]), moment(issue["lastEditedAt"])

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

    def answer_discussion(self, discussion: dict[str, Any], body: str) -> None:
        """Post a comment on the discussion, at its top level; it never marks the
        comment as the answer, which is the asker's choice."""
        if self.dry_run:
            self.previews.append(body)
        self._write(
            f"comment on discussion #{discussion['number']}",
            lambda: self.graphql(
                """
                mutation($discussion: ID!, $body: String!) {
                  addDiscussionComment(
                    input: {discussionId: $discussion, body: $body}
                  ) {
                    comment { id }
                  }
                }
                """,
                {"discussion": discussion["node_id"], "body": body},
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

    def create_issue(self, title: str, body: str, labels: list[str]) -> int:
        """Open an issue of the assistant; its number, 0 in a dry run, which opens
        none."""
        if self.dry_run:
            self.previews.append(body)
        number = 0

        def create() -> None:
            nonlocal number
            opened = self.rest(
                "issues",
                method="POST",
                data={"title": title, "body": body, "labels": labels},
            )
            number = opened["number"]

        self._write("open the digest issue", create)
        return number

    def edit_issue(self, number: int, title: str, body: str) -> None:
        """Change the title and the text of an issue of the assistant; an edit
        notifies nobody."""
        self._write(
            f"edit #{number}",
            lambda: self.rest(
                f"issues/{number}",
                method="PATCH",
                data={"title": title, "body": body},
            ),
        )

    def react(self, comment_id: int, content: str) -> None:
        """React to a comment, such as with eyes; the same reaction again changes
        nothing, and neither does a comment that was deleted meanwhile."""

        def react() -> None:
            try:
                self.rest(
                    f"issues/comments/{comment_id}/reactions",
                    method="POST",
                    data={"content": content},
                )
            except GitHubError as error:
                # The command was carried out; only its sign has nowhere to go.
                if not error.missing():
                    raise

        self._write(f"react with {content} to comment {comment_id}", react)

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

    def assign(self, number: int, logins: list[str]) -> None:
        """Assign people, at least one, to the issue; GitHub leaves out those it
        can't assign."""
        self._write(
            f"assign #{number} to {', '.join(logins)}",
            lambda: self.rest(
                f"issues/{number}/assignees",
                method="POST",
                data={"assignees": logins},
            ),
        )

    def assignable(self, user: str) -> bool:
        """Whether issues of the repository can be assigned to the person."""
        try:
            self.rest(f"assignees/{quote(user, safe='')}")
        except GitHubError as error:
            if not error.missing():
                raise
            return False
        return True

    def issue_types(self) -> list[str] | None:
        """The names of the issue types that the repository's issues can have; None
        where it has none, as a repository of a person."""
        data = self.graphql(
            """
            query($owner: String!, $name: String!) {
              repository(owner: $owner, name: $name) {
                issueTypes(first: 100) { nodes { name isEnabled } }
              }
            }
            """,
            {"owner": self.owner, "name": self.name},
        )
        found = (data["repository"] or {}).get("issueTypes")
        if found is None:
            return None
        return [node["name"] for node in found["nodes"] if node["isEnabled"]]

    def issue_type_id(self, name: str) -> str | None:
        """The ID of the repository's issue type of that name; None where the
        repository has no such type, or has it turned off."""
        data = self.graphql(
            """
            query($owner: String!, $name: String!, $type: String!) {
              repository(owner: $owner, name: $name) {
                issueType(name: $type) { id isEnabled }
              }
            }
            """,
            {"owner": self.owner, "name": self.name, "type": name},
        )
        found = (data["repository"] or {}).get("issueType")
        return str(found["id"]) if found and found["isEnabled"] else None

    def set_issue_type(
        self, issue: dict[str, Any], name: str | None, type_id: str | None
    ) -> None:
        """Set the issue type of the issue; None takes its type away."""
        what = f"set the issue type of #{issue['number']} to {name}"
        if type_id is None:
            what = f"remove the issue type of #{issue['number']}"
        self._write(
            what,
            lambda: self.graphql(
                """
                mutation($issue: ID!, $type: ID) {
                  updateIssueIssueType(input: {issueId: $issue, issueTypeId: $type}) {
                    clientMutationId
                  }
                }
                """,
                {"issue": issue["node_id"], "type": type_id},
            ),
        )

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

    def lock(self, number: int) -> None:
        """Lock the conversation of an issue as resolved; only those who may push
        can comment then."""
        self._write(
            f"lock #{number} (resolved)",
            lambda: self.rest(
                f"issues/{number}/lock",
                method="PUT",
                data={"lock_reason": "resolved"},
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

    def create_label(self, label: Label) -> bool:
        """Create a label, and say whether this call created it.

        Another workflow may have created it after the labels were read, such as the
        settings bot of the blueprint, which a push that changes labels.toml starts
        as well. GitHub refuses to create it again, so the label then gets its color
        and its description as one that existed, and the run goes on.
        """
        exists = False

        def create() -> None:
            nonlocal exists
            try:
                self.rest(
                    "labels",
                    method="POST",
                    data={
                        "name": label.name,
                        "color": label.color,
                        "description": label.description,
                    },
                )
            except GitHubError as error:
                if not error.already_exists():
                    raise
                exists = True

        self._write(f"create label {label.name}", create)
        # The labels read before lack it.
        self._labels = None
        if exists:
            # GitHub created nothing, so the summary does not say so.
            self.writes[-1] = f"found label {label.name}, created a moment before"
            # The other workflow may have created it in another case.
            self.update_label(label, rename=True)
        return not exists

    def update_label(
        self, label: Label, current: str | None = None, *, rename: bool = False
    ) -> None:
        """Set the color and the description of a label, and its name where GitHub
        has it as `current`, in another case, or may have it so (`rename`)."""
        name = current or label.name
        data = {"color": label.color, "description": label.description}
        if rename or name != label.name:
            data["new_name"] = label.name
        self._write(
            f"update label {name}",
            lambda: self.rest(
                f"labels/{quote(name, safe='')}", method="PATCH", data=data
            ),
        )


def as_rest(
    node: dict[str, Any], owns: Callable[[dict[str, Any]], bool]
) -> dict[str, Any]:
    """An issue of GraphQL in the form of the REST API, which the assistant reads,
    with `closed_by`, the number of the pull request that fixed it, if one did.

    That is the last merged pull request of this repository that names the issue,
    such as `Fixes #12`, else the pull request that closed it last. A release
    closes the issues that its pull requests fix (`close_with_release`), so the
    closing is often the bot's and not the pull request's."""
    closer = None
    if node["state"] == "CLOSED":
        fixes = [
            pull["number"]
            for pull in node["closedByPullRequestsReferences"]["nodes"]
            if pull["merged"] and owns(pull)
        ]
        events = node["timelineItems"]["nodes"]
        closed = ((events[-1] if events else {}).get("closer") or {}).get("number")
        closer = fixes[-1] if fixes else closed
    return {
        "number": node["number"],
        "title": node["title"],
        "body": node["body"],
        "user": actor(node["author"]),
        "state": node["state"].lower(),
        "state_reason": (node["stateReason"] or "").lower() or None,
        "labels": (node["labels"] or {"nodes": []})["nodes"],
        "created_at": node["createdAt"],
        "closed_at": node["closedAt"],
        "comments": node["comments"]["totalCount"],
        "closed_by": closer,
    }


def closed_as_rest(
    node: dict[str, Any], owns: Callable[[dict[str, Any]], bool]
) -> dict[str, Any]:
    """A closed issue of GraphQL for `evaluate`, in the form of the REST API, with
    `duplicate_of`: the issue of this repository that it was closed as a duplicate
    of, else the one it was marked as a duplicate of last, unless someone unmarked
    it, with its number and when it was opened; None for any other issue."""
    events = node["timelineItems"]["nodes"]
    marked = (events[-1] if events else {}).get("canonical") or {}
    original = node["duplicateOf"] or (marked if "number" in marked else None)
    author = node["author"] or {}
    return {
        "number": node["number"],
        "title": node["title"],
        "body": node["body"],
        # A deleted account is GitHub's ghost.
        "user": {
            "login": author.get("login") or "ghost",
            "type": "Bot" if author.get("__typename") == "Bot" else "User",
        },
        "author_association": node["authorAssociation"],
        "state": "closed",
        "state_reason": (node["stateReason"] or "").lower() or None,
        "labels": (node["labels"] or {"nodes": []})["nodes"],
        "created_at": node["createdAt"],
        "closed_at": node["closedAt"],
        "duplicate_of": {
            "number": original["number"],
            "created_at": original["createdAt"],
        }
        if original and owns(original)
        else None,
    }


def actor(node: dict[str, Any] | None) -> dict[str, str]:
    """An author of GraphQL as the REST API names it: a bot by its login and [bot],
    as github-actions[bot], of the type Bot, and a deleted account as ghost."""
    if not node:
        return {"login": "ghost", "type": "User"}
    if node.get("__typename") == "Bot":
        return {"login": f"{node['login']}[bot]", "type": "Bot"}
    return {"login": node["login"], "type": "User"}


def post_as_rest(node: dict[str, Any]) -> dict[str, Any]:
    """A discussion, a comment or a reply of GraphQL: who wrote it, in which role,
    when, and its text, as the REST API names them."""
    return {
        "user": actor(node["author"]),
        "author_association": node["authorAssociation"],
        "created_at": node["createdAt"],
        "body": node.get("body"),
    }


def discussion_as_rest(node: dict[str, Any]) -> dict[str, Any]:
    """A discussion of GitHub.discussion() in the form of the REST API, with its
    comments, each with its replies, and their number."""
    comments = node["comments"]
    return {
        "node_id": node["id"],
        "number": node["number"],
        "title": node["title"],
        **post_as_rest(node),
        "state": "closed" if node["closed"] else "open",
        "locked": node["locked"],
        "answered": bool(node["isAnswered"]),
        "category": {
            "name": node["category"]["name"],
            "is_answerable": node["category"]["isAnswerable"],
        },
        "comment_count": comments["totalCount"],
        "comments": [
            {
                **post_as_rest(comment),
                "replies": [
                    post_as_rest(reply) for reply in comment["replies"]["nodes"]
                ],
            }
            for comment in comments["nodes"]
        ],
    }
