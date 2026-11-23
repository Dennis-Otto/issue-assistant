"""The renderings of GitHub, read by their numbers in a second step (#521).

`read_renderings` runs here against GitHubOfRenderings, which answers the requests
as GitHub does, from the texts that it holds, and fails them in each shape that the
measurement of #521 saw, on a clock of its own; the GraphQL contract test checks the
documents against GitHub's schema, and the tests of GitHub.renderings run gh's
stand-in on the answers that GitHub gave to two real requests.
"""

import itertools
import json
import re
import string
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest

import issue_assistant as assistant
from support import fake_gh

FIXTURES = Path(__file__).parent / "fixtures"
RECORDED = json.loads((FIXTURES / "renderings-by-number.json").read_text("utf-8"))
REPOSITORY = "owner/project"
OTHER = "owner/other"
LIMITS = assistant.CONTEXT_RENDERING_LIMITS
FOOTNOTES = "[^1]" * 2200

ISSUE_ALIAS = re.compile(r"(\w+): issue\(number: (\d+)\) \{ body bodyHTML \}")
DISCUSSION_ALIAS = re.compile(r"(\w+): discussion\(number: (\d+)\) \{ body bodyHTML \}")
ANSWER_ALIAS = re.compile(r"(\w+): discussion\(number: (\d+)\) \{ answer \{")
WINDOW_ALIAS = re.compile(r"(w\d+): comments\(first: (\d+)(?:, after: \$(w\d+))?\)")
# The issue of the comments, or one of its windows, in the order of the document.
COMMENTS_PART = re.compile(r"(c\d+): issue\(number: (\d+)\)|" + WINDOW_ALIAS.pattern)


def html_of(body: str) -> str:
    return f"<p>{body}</p>"


def done(stdout: object = "", stderr: str = "", status: int = 0) -> Any:
    text = stdout if isinstance(stdout, str) else json.dumps(stdout)
    return subprocess.CompletedProcess(["gh"], status, text, stderr)


# The shapes of a failed request: what gh gives for each, as the measurement saw them
# or as GitHub documents them.
SHAPES = {
    "a 504 with an HTML page": done(
        "<!DOCTYPE html><html>Unicorn</html>", "gh: HTTP 504", 1
    ),
    "a body cut off": done("", "unexpected end of JSON input", 1),
    "exit 0 and no answer": done(),
    "errors and no data": done(
        {"errors": [{"message": "Something went wrong while executing your query."}]},
        "gh: Something went wrong while executing your query.",
        1,
    ),
    "data null": done(
        {"data": None, "errors": [{"message": "timedout"}]}, "gh: timedout", 1
    ),
    "data that isn't an object": done({"data": []}),
    "a repository that isn't there without a reason": done(
        {"data": {"repository": None}}
    ),
}
SECONDARY = done(
    {"message": "You have exceeded a secondary rate limit."},
    "gh: You have exceeded a secondary rate limit. (HTTP 403)",
    1,
)
PRIMARY = done(
    {"errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded."}]},
    "gh: API rate limit exceeded.",
    1,
)


class GitHubOfRenderings:
    """GitHub as a request of renderings sees it: the texts that it holds, by their
    numbers, and a clock that each request moves on by `seconds`.

    `failing` gives the answer in place of GitHub's for a request whose aliases it
    picks, such as a 504 for every request that holds `i5`; `edit` changes what an
    answer holds without changing the texts of the first step."""

    def __init__(self, seconds: float = 0.5) -> None:
        self.issues: dict[tuple[str, int], str] = {}
        self.discussions: dict[tuple[str, int], str] = {}
        self.answers: dict[tuple[str, int], str] = {}
        self.comments: dict[tuple[str, int], list[tuple[str, str]]] = {}
        self.html: dict[str, str] = {}
        # The issues deleted since the first step, whose comments are gone too.
        self.gone: set[int] = set()
        self.seconds: float | Callable[[dict[str, Any]], float] = seconds
        self.failing: Callable[[set[str], dict[str, Any]], Any] = (
            lambda aliases, request: None
        )
        self.remaining = 5000
        self.now = 0.0
        self.requests: list[dict[str, Any]] = []

    def clock(self) -> float:
        return self.now

    def send(self, request: dict[str, Any], timeout: float) -> Any:
        self.requests.append({**request, "timeout": timeout})
        seconds = self.seconds(request) if callable(self.seconds) else self.seconds
        if seconds > timeout:
            self.now += timeout
            raise subprocess.TimeoutExpired(["gh"], timeout)
        self.now += seconds
        aliases = set(
            re.findall(r"(\w+): (?:issue|discussion|comments)\(", request["query"])
        )
        failure = self.failing(aliases, request)
        if failure is not None:
            return failure
        self.remaining -= 1
        rate = {"cost": 1, "remaining": self.remaining}
        variables = request["variables"]
        repository = f"{variables['owner']}/{variables['name']}"
        if request["query"] == assistant.CURSORS:
            return done(
                {
                    "data": {
                        "repository": self.cursors(repository, variables),
                        "rateLimit": rate,
                    }
                }
            )
        return self.answered(repository, request, rate)

    def cursors(self, repository: str, variables: dict[str, Any]) -> dict[str, Any]:
        comments = self.comments[(repository, variables["number"])]
        ids = [comment for comment, _ in comments]
        start = (
            0 if variables["after"] is None else ids.index(variables["after"][7:]) + 1
        )
        page = ids[start : start + 100]
        return {
            "issue": {
                "comments": {
                    "pageInfo": {
                        "hasNextPage": start + 100 < len(ids),
                        "endCursor": f"cursor:{page[-1]}" if page else None,
                    },
                    "edges": [
                        {"cursor": f"cursor:{id}", "node": {"fullDatabaseId": id}}
                        for id in page
                    ],
                }
            }
        }

    def entry(self, body: str) -> dict[str, str]:
        return {"body": body, "bodyHTML": self.html.get(body, html_of(body))}

    def answered(
        self, repository: str, request: dict[str, Any], rate: dict[str, Any]
    ) -> Any:
        query, variables = request["query"], request["variables"]
        found: dict[str, Any] = {}
        errors = []

        def absent(path: list[str], number: int) -> None:
            errors.append(
                {"type": "NOT_FOUND", "path": path, "message": f"no {number}"}
            )

        for alias, number in ISSUE_ALIAS.findall(query):
            body = self.issues.get((repository, int(number)))
            found[alias] = None if body is None else self.entry(body)
            if body is None:
                absent(["repository", alias], int(number))
        for alias, number in DISCUSSION_ALIAS.findall(query):
            found[alias] = self.entry(self.discussions[(repository, int(number))])
        for alias, number in ANSWER_ALIAS.findall(query):
            body = self.answers.get((repository, int(number)))
            found[alias] = {"answer": None if body is None else self.entry(body)}
        # Each window belongs to the issue whose alias came last before it.
        holder: dict[str, Any] | None = None
        comments: list[tuple[str, str]] = []
        for part in COMMENTS_PART.finditer(query):
            alias, number, window, first, after = part.groups()
            if alias is not None:
                holder = None if int(number) in self.gone else {}
                found[alias] = holder
                if holder is None:
                    absent(["repository", alias], int(number))
                else:
                    comments = self.comments[(repository, int(number))]
            elif holder is not None:
                ids = [comment for comment, _ in comments]
                start = 0 if not after else ids.index(variables[after][7:]) + 1
                holder[window] = {
                    "nodes": [
                        {"fullDatabaseId": comment, **self.entry(body)}
                        for comment, body in comments[start : start + int(first)]
                    ]
                }
        answer: dict[str, Any] = {"data": {"repository": found, "rateLimit": rate}}
        if errors:
            answer["errors"] = errors
        return done(answer, "gh: no such issue" if errors else "", 1 if errors else 0)

    def texts_asked(self, request: dict[str, Any]) -> int:
        query = request["query"]
        return (
            len(ISSUE_ALIAS.findall(query))
            + len(DISCUSSION_ALIAS.findall(query))
            + len(ANSWER_ALIAS.findall(query))
            + sum(int(first) for _, first, _ in WINDOW_ALIAS.findall(query))
        )


def issue(
    github: GitHubOfRenderings,
    number: int,
    body: str = "",
    repository: str = REPOSITORY,
) -> Any:
    body = body or f"Issue {number}."
    github.issues[(repository, number)] = body
    return assistant.Text(repository, assistant.ISSUE, number, body)


def comments(
    github: GitHubOfRenderings,
    number: int,
    bodies: list[str],
    hidden: frozenset[int] = frozenset(),
) -> list[Any]:
    held = [
        (str(7_000_000_000 + number * 1000 + index), body)
        for index, body in enumerate(bodies)
    ]
    github.comments[(REPOSITORY, number)] = held
    return [
        assistant.Text(
            REPOSITORY, assistant.COMMENT, number, body, comment, index in hidden
        )
        for index, (comment, body) in enumerate(held)
    ]


def read(github: GitHubOfRenderings, texts: list[Any], limits: Any = LIMITS) -> Any:
    return assistant.read_renderings(texts, limits, github.send, github.clock)


def rendered(renderings: Any, texts: list[Any]) -> list[str | None]:
    return [renderings.of(text) for text in texts]


def asked(github: GitHubOfRenderings) -> list[int]:
    """How many texts each request of renderings asked for, in their order."""
    return [
        github.texts_asked(request)
        for request in github.requests
        if request["query"] != assistant.CURSORS
    ]


def test_the_renderings_of_issues_comments_and_discussions_are_read():
    github = GitHubOfRenderings()
    texts = [
        issue(github, 12),
        *comments(github, 12, ["First.", "Second."]),
        issue(github, 7),
    ]
    github.discussions[(REPOSITORY, 3)] = "A discussion."
    github.answers[(REPOSITORY, 3)] = "Its answer."
    texts += [
        assistant.Text(REPOSITORY, assistant.DISCUSSION, 3, "A discussion."),
        assistant.Text(REPOSITORY, assistant.ANSWER, 3, "Its answer."),
    ]
    renderings = read(github, texts)
    assert rendered(renderings, texts) == [html_of(text.body) for text in texts]
    # One request, and no cursors: the comments are one window from the first.
    assert len(github.requests) == 1
    assert (
        renderings.requests == 1
        and renderings.points == 1
        and renderings.lowest == 4999
    )
    assert not renderings.fallbacks


def pattern_of(template: str, **parts: str) -> str:
    """A pattern of the documents that a template of the module makes."""
    pattern = ""
    for literal, name, _, _ in string.Formatter().parse(template):
        pattern += re.escape(literal)
        if name is not None:
            pattern += parts[name]
    return pattern


WINDOW = pattern_of(
    assistant.WINDOW_FIELD, alias=r"w\d+", first=r"\d+", after=r"(?:, after: \$w\d+)?"
)
# Each field under an alias of the number that it reads.
FIELDS = [
    pattern_of(assistant.ISSUE_FIELD, alias=r"i(\d+)", number=r"\1"),
    pattern_of(assistant.DISCUSSION_FIELD, alias=r"d(\d+)", number=r"\1"),
    pattern_of(assistant.ANSWER_FIELD, alias=r"a(\d+)", number=r"\1"),
    pattern_of(
        assistant.COMMENTS_FIELD,
        alias=r"c(\d+)",
        number=r"\1",
        windows=f"{WINDOW}(?: {WINDOW})*",
    ),
]
DOCUMENT = pattern_of(
    assistant.RENDERINGS, variables=r"(?:, \$w\d+: String!)*", fields="(.+)"
)


def test_renderings_are_read_by_number():
    """Every request is built from the closed list of documents, each of which reads
    its texts by the number of their issue or discussion, under an alias of that
    number, and asks for nothing else."""
    github = GitHubOfRenderings()
    texts = [
        issue(github, 12),
        *comments(github, 12, ["a", "b", "c"], hidden=frozenset({1})),
    ]
    github.discussions[(REPOSITORY, 3)] = "A discussion."
    github.answers[(REPOSITORY, 3)] = "Its answer."
    texts += [
        assistant.Text(REPOSITORY, assistant.DISCUSSION, 3, "A discussion."),
        assistant.Text(REPOSITORY, assistant.ANSWER, 3, "Its answer."),
        issue(github, 5, repository=OTHER),
    ]
    renderings = read(github, texts)
    assert len(renderings.html) == len(texts) - 1
    assert github.requests[0]["query"] == assistant.CURSORS
    assert set(github.requests[0]["variables"]) == {"owner", "name", "number", "after"}
    for request in github.requests[1:]:
        document = re.fullmatch(DOCUMENT, request["query"])
        assert document, request["query"]
        for part in re.split(r" (?=[icda]\d+: )", document[1]):
            assert any(re.fullmatch(field, part) for field in FIELDS), part
        assert set(request["variables"]) - {"owner", "name"} <= {
            f"w{n}" for n in range(100)
        }
    assert [request["variables"]["name"] for request in github.requests] == [
        "project",
        "project",
        "other",
    ]


def test_renderings_come_in_requests_of_the_budget():
    github = GitHubOfRenderings()
    # About 0.95 MB of estimate each: 16 of them fill the bytes of a request.
    footnotes = "[^1]" * 2200
    by_bytes = [
        issue(github, number, f"{number} {footnotes}") for number in range(1, 21)
    ]
    read(github, by_bytes)
    assert asked(github) == [16, 4]
    # 84,000 padded cells each: four fill the cells of a request.
    github = GitHubOfRenderings()
    table = "|" + "h|" * 280 + "\n|" + "-|" * 280 + "\n" + "|x\n" * 299
    assert assistant.rendering_estimate(table, REPOSITORY).cells == 84_000
    by_cells = [issue(github, number, f"{number}\n\n{table}") for number in range(1, 7)]
    read(github, by_cells)
    assert asked(github) == [4, 2]
    # A hundred texts at most, and the texts of one repository in a request.
    github = GitHubOfRenderings()
    many = [issue(github, number) for number in range(1, 102)]
    others = [issue(github, number, repository=OTHER) for number in (1, 2)]
    renderings = read(github, [*many[:50], *others, *many[50:]])
    assert asked(github) == [50, 2, 51]
    assert renderings.of(others[0]) == html_of("Issue 1.")
    github = GitHubOfRenderings()
    read(github, many)
    assert asked(github) == [100, 1]


def test_a_window_ends_before_a_text_left_out():
    """A collapsed comment, an empty one and one above the cap aren't asked for; the
    windows end before each and start after it, at the cursor of the comment
    before them, which is read once."""
    github = GitHubOfRenderings()
    too_large = "[^1]" * 3000
    texts = comments(
        github, 12, ["a", "b", "c", too_large, "d", "", "e"], hidden=frozenset({1})
    )
    renderings = read(github, texts)
    assert rendered(renderings, texts) == [
        html_of("a"),
        None,
        html_of("c"),
        None,
        html_of("d"),
        None,
        html_of("e"),
    ]
    cursors, request = github.requests
    assert (
        cursors["query"] == assistant.CURSORS and cursors["variables"]["after"] is None
    )
    assert re.findall(
        r"w\d+: comments\(first: \d+(?:, after: \$w\d+)?\)", request["query"]
    ) == [
        "w0: comments(first: 1)",
        "w1: comments(first: 1, after: $w1)",
        "w2: comments(first: 1, after: $w2)",
        "w3: comments(first: 1, after: $w3)",
    ]
    ids = [text.comment for text in texts]
    assert [request["variables"][f"w{index}"] for index in (1, 2, 3)] == [
        f"cursor:{ids[1]}",
        f"cursor:{ids[3]}",
        f"cursor:{ids[5]}",
    ]
    assert renderings.fallbacks == {assistant.ESTIMATED_TOO_LARGE: 1}
    assert renderings.requests == 2


def test_the_cursors_are_read_only_when_a_window_needs_them():
    github = GitHubOfRenderings()
    texts = comments(github, 12, ["a", "b"], hidden=frozenset({1}))
    read(github, texts)
    assert [request["query"] for request in github.requests] != [assistant.CURSORS]
    assert all(request["query"] != assistant.CURSORS for request in github.requests)
    # More than a request of comments: the second window starts after the cursor of
    # the 100th comment, which the first page of the cursors holds.
    github = GitHubOfRenderings()
    texts = comments(github, 12, [f"c{index}" for index in range(250)])
    renderings = read(github, texts)
    assert asked(github) == [100, 100, 50]
    cursors = [
        request for request in github.requests if request["query"] == assistant.CURSORS
    ]
    assert [request["variables"]["after"] for request in cursors] == [
        None,
        f"cursor:{texts[99].comment}",
    ]
    assert all(renderings.of(text) == html_of(text.body) for text in texts)


def test_the_cursors_are_read_after_a_window_failed():
    github = GitHubOfRenderings()
    texts = comments(github, 12, ["a", "b", "c", "d"])
    github.failing = lambda aliases, request: (
        SHAPES["a 504 with an HTML page"] if github.texts_asked(request) == 4 else None
    )
    renderings = read(github, texts)
    # The window of four failed whole, before its cursors were known; its second
    # half needs the cursor of the second comment, read when it goes.
    queries = [request["query"] == assistant.CURSORS for request in github.requests]
    assert queries == [False, False, True, False]
    assert asked(github) == [4, 2, 2]
    assert rendered(renderings, texts) == [html_of(body) for body in "abcd"]
    assert renderings.failed == 1 and not renderings.fallbacks


def test_a_failed_cursor_read_leaves_its_windows_to_their_cleaned_source():
    github = GitHubOfRenderings()
    texts = [
        issue(github, 12),
        *comments(github, 12, ["a", "b", "c"], hidden=frozenset({1})),
    ]
    github.failing = lambda aliases, request: (
        SHAPES["a body cut off"] if request["query"] == assistant.CURSORS else None
    )
    renderings = read(github, texts)
    assert rendered(renderings, texts) == [
        html_of("Issue 12."),
        html_of("a"),
        None,
        None,
    ]
    assert renderings.fallbacks == {assistant.FAILED: 1}
    assert renderings.failed == 1 and renderings.points == 1 + assistant.FAILED_POINTS


def test_a_comment_that_its_cursors_do_not_know_changed():
    github = GitHubOfRenderings()
    texts = comments(github, 12, ["a", "b", "c"], hidden=frozenset({1}))
    # The comment before the window was deleted between the two steps.
    del github.comments[(REPOSITORY, 12)][1]
    renderings = read(github, texts)
    assert rendered(renderings, texts) == [html_of("a"), None, None]
    assert renderings.fallbacks == {assistant.CHANGED: 1}


def test_a_text_estimated_too_large_is_read_from_its_cleaned_source():
    github = GitHubOfRenderings()
    small, large = issue(github, 1), issue(github, 2, "[^1]" * 3000)
    arriving = issue(github, 3, "Short, but rendered large.")
    github.html["Short, but rendered large."] = "<p>" + "x" * assistant.CAP + "</p>"
    renderings = read(github, [small, large, arriving])
    assert rendered(renderings, [small, large, arriving]) == [
        html_of("Issue 1."),
        None,
        None,
    ]
    assert asked(github) == [2]
    assert renderings.fallbacks == {
        assistant.ESTIMATED_TOO_LARGE: 1,
        assistant.ARRIVED_TOO_LARGE: 1,
    }


@pytest.mark.parametrize("shape", SHAPES)
def test_a_failed_request_is_split_in_halves(shape):
    """One text that fails in a request of eight: the request and the halves that
    hold it fail, each sent once, and never whole again."""
    github = GitHubOfRenderings()
    texts = [issue(github, number) for number in range(1, 9)]
    github.failing = lambda aliases, request: SHAPES[shape] if "i6" in aliases else None
    renderings = read(github, texts)
    assert asked(github) == [8, 4, 4, 2, 1, 1, 2]
    assert rendered(renderings, texts) == [
        html_of(text.body) if text.number != 6 else None for text in texts
    ]
    assert renderings.fallbacks == {assistant.FAILED: 1}
    assert renderings.failed == 4
    assert renderings.points == 3 + 4 * assistant.FAILED_POINTS


def test_a_call_that_runs_past_its_time_is_split_too():
    github = GitHubOfRenderings()
    texts = [issue(github, number) for number in range(1, 3)]
    github.seconds = lambda request: 40.0 if "i2: issue" in request["query"] else 0.5
    renderings = read(github, texts)
    assert [request["timeout"] for request in github.requests] == [30.0, 30.0, 30.0]
    assert rendered(renderings, texts) == [html_of("Issue 1."), None]
    assert renderings.fallbacks == {assistant.FAILED: 1}


def test_a_window_is_split_at_its_middle_comment():
    github = GitHubOfRenderings()
    texts = comments(github, 12, ["a", "b", "c", "bad", "e"])
    github.failing = lambda aliases, request: (
        SHAPES["a 504 with an HTML page"]
        if (
            request["query"] != assistant.CURSORS
            and any("bad" in body for body in [str(request)])
        )
        else None
    )

    def failing(aliases, request):
        if request["query"] == assistant.CURSORS:
            return None
        nodes = github.answered(REPOSITORY, request, {"cost": 1, "remaining": 5000})
        return SHAPES["a body cut off"] if '"bad"' in nodes.stdout else None

    github.failing = failing
    renderings = read(github, texts)
    assert asked(github) == [5, 3, 2, 1, 1]
    assert rendered(renderings, texts) == [
        html_of("a"),
        html_of("b"),
        html_of("c"),
        None,
        html_of("e"),
    ]


def test_a_text_that_fails_alone_is_read_from_its_cleaned_source():
    github = GitHubOfRenderings()
    text = issue(github, 1)
    github.failing = lambda aliases, request: SHAPES["errors and no data"]
    renderings = read(github, [text])
    assert renderings.of(text) is None and len(github.requests) == 1
    assert renderings.fallbacks == {assistant.FAILED: 1}


def test_a_partial_answer_keeps_the_other_renderings():
    """An alias or a repository that GitHub answers null, with NOT_FOUND or FORBIDDEN
    on its path, leaves only its texts to their cleaned source."""
    github = GitHubOfRenderings()
    texts = [
        issue(github, 1),
        assistant.Text(REPOSITORY, assistant.ISSUE, 2, "Gone."),
        issue(github, 3),
    ]
    renderings = read(github, texts)
    assert rendered(renderings, texts) == [
        html_of("Issue 1."),
        None,
        html_of("Issue 3."),
    ]
    assert (
        renderings.fallbacks == {assistant.NULL_ANSWER: 1} and len(github.requests) == 1
    )
    private = [issue(github, number, repository=OTHER) for number in (1, 2)]
    github.failing = lambda aliases, request: (
        done(
            {
                "data": {"repository": None},
                "errors": [{"type": "FORBIDDEN", "path": ["repository"]}],
            },
            "gh: forbidden",
            1,
        )
        if request["variables"]["name"] == "other"
        else None
    )
    renderings = read(github, [*private, texts[0]])
    assert rendered(renderings, [*private, texts[0]]) == [
        None,
        None,
        html_of("Issue 1."),
    ]
    assert renderings.fallbacks == {assistant.NULL_ANSWER: 2} and renderings.failed == 0


def test_a_null_without_a_reason_or_a_missing_alias_fails_the_request():
    github = GitHubOfRenderings()
    texts = [issue(github, 1), issue(github, 2)]

    def without(alias, value):
        def failing(aliases, request):
            answer = json.loads(
                github.answered(
                    REPOSITORY, request, {"cost": 1, "remaining": 5000}
                ).stdout
            )
            repository = answer["data"]["repository"]
            if alias in repository and len(repository) > 1:
                if value is ...:
                    del repository[alias]
                else:
                    repository[alias] = value
            return done(answer)

        return failing

    for alias, value in [("i2", ...), ("i2", None), ("i2", {"body": "Issue 2."})]:
        github.requests.clear()
        github.failing = without(alias, value)
        renderings = read(github, texts)
        assert asked(github) == [2, 1, 1]
        assert rendered(renderings, texts) == [html_of("Issue 1."), html_of("Issue 2.")]


def test_an_answer_is_taken_by_its_alias_or_its_comment():
    github = GitHubOfRenderings()
    texts = [issue(github, 2), issue(github, 1), *comments(github, 1, ["x", "y"])]

    def shuffled(aliases, request):
        answer = json.loads(
            github.answered(REPOSITORY, request, {"cost": 1, "remaining": 5000}).stdout
        )
        repository = answer["data"]["repository"]
        answer["data"]["repository"] = dict(reversed(list(repository.items())))
        repository["c1"]["w0"]["nodes"].reverse()
        return done(answer)

    github.failing = shuffled
    renderings = read(github, texts)
    assert rendered(renderings, texts) == [html_of(text.body) for text in texts]


def test_a_text_edited_between_the_two_readings_is_read_from_its_cleaned_source():
    github = GitHubOfRenderings()
    texts = [issue(github, 1), *comments(github, 1, ["x", "y"])]
    github.discussions[(REPOSITORY, 4)] = "A discussion."
    texts += [
        assistant.Text(REPOSITORY, assistant.DISCUSSION, 4, "An earlier discussion."),
        assistant.Text(REPOSITORY, assistant.ANSWER, 4, "An answer that went."),
    ]
    github.issues[(REPOSITORY, 1)] = "Issue 1, edited."
    held = github.comments[(REPOSITORY, 1)]
    held[1] = ("1", held[1][1])
    renderings = read(github, texts)
    assert rendered(renderings, texts) == [None, html_of("x"), None, None, None]
    assert renderings.fallbacks == {assistant.CHANGED: 4}


def test_a_rendering_failure_never_fails_the_run(monkeypatch, tmp_path):
    """GitHub.renderings raises on no shape that gh gives, and runs gh itself."""
    texts = [assistant.Text(REPOSITORY, assistant.ISSUE, 1, "Issue 1.")]
    for answer in SHAPES.values():
        calls = fake_gh(
            monkeypatch,
            tmp_path,
            {
                "stdout": answer.stdout,
                "stderr": answer.stderr,
                "status": answer.returncode,
            },
        )
        renderings = assistant.GitHub(REPOSITORY).renderings(texts, LIMITS)
        assert renderings.of(texts[0]) is None and renderings.fallbacks == {
            assistant.FAILED: 1
        }
        assert [command for command, _ in calls] == [
            ["gh", "api", "graphql", "--input", "-"]
        ]


def test_github_s_answers_to_requests_by_number(monkeypatch, tmp_path):
    """The answers that GitHub gave to two requests of the test repository of #521:
    the cursors of an issue's comments, then two issues, an answer and two windows
    of comments, with gh exiting 1 for the issue that doesn't exist."""
    repository = RECORDED["repository"]
    cursors, answer = RECORDED["exchanges"]
    data = json.loads(answer["stdout"])["data"]["repository"]
    edges = json.loads(cursors["stdout"])["data"]["repository"]["issue"]["comments"][
        "edges"
    ]
    shown = {
        node["fullDatabaseId"]: node["body"]
        for window in data["c6"].values()
        for node in window["nodes"]
    }
    texts = [
        assistant.Text(repository, assistant.ISSUE, 1, data["i1"]["body"]),
        *(
            assistant.Text(
                repository,
                assistant.COMMENT,
                6,
                shown.get(edge["node"]["fullDatabaseId"], ""),
                edge["node"]["fullDatabaseId"],
                edge["node"]["fullDatabaseId"] not in shown,
            )
            for edge in edges
        ),
        assistant.Text(repository, assistant.ISSUE, 35, "Never there."),
        assistant.Text(repository, assistant.ANSWER, 38, "Never given."),
    ]
    calls = fake_gh(
        monkeypatch,
        tmp_path,
        *(
            {"stdout": e["stdout"], "stderr": e["stderr"], "status": e["status"]}
            for e in (cursors, answer)
        ),
    )
    renderings = assistant.GitHub(repository).renderings(texts, LIMITS)
    assert [json.loads(sent) for _, sent in calls] == [
        {"query": exchange["query"], "variables": exchange["variables"]}
        for exchange in (cursors, answer)
    ]
    assert (
        renderings.of(texts[0]) == data["i1"]["bodyHTML"] and len(renderings.html) == 3
    )
    assert renderings.fallbacks == {assistant.NULL_ANSWER: 1, assistant.CHANGED: 1}
    assert renderings.lowest == 4879 and renderings.failed == 0


def test_a_call_of_gh_ends_after_its_seconds(monkeypatch):
    """gh runs with a timeout of CALL_SECONDS, or less when the command has less
    left; a call that runs past it is a failed request."""
    timeouts = []

    def run(command, **options):
        timeouts.append(options["timeout"])
        raise subprocess.TimeoutExpired(command, options["timeout"])

    monkeypatch.setattr(assistant.subprocess, "run", run)
    texts = [assistant.Text(REPOSITORY, assistant.ISSUE, 1, "x")]
    renderings = assistant.GitHub(REPOSITORY).renderings(texts, LIMITS)
    assert timeouts == [assistant.CALL_SECONDS]
    assert renderings.fallbacks == {assistant.FAILED: 1} and renderings.failed == 1
    renderings = assistant.GitHub(REPOSITORY).renderings(texts, assistant.Limits(0.0))
    assert len(timeouts) == 1 and renderings.fallbacks == {assistant.OUT_OF_TIME: 1}


def test_a_request_that_the_time_spent_keeps_back_is_not_counted():
    """The time can run out between the look at the limits and the request, which
    then doesn't go."""
    github = GitHubOfRenderings()
    # The start, the two looks at the limits, then the time left to the request.
    times = iter([0.0, 0.0, 0.0, 200.0])
    texts = [issue(github, 1)]
    renderings = assistant.read_renderings(
        texts, LIMITS, github.send, lambda: next(times, 200.0)
    )
    assert not github.requests and renderings.requests == 0
    assert renderings.fallbacks == {assistant.OUT_OF_TIME: 1}


def test_the_time_of_the_command_ends_between_two_requests():
    github = GitHubOfRenderings(seconds=2.0)
    texts = [issue(github, number, FOOTNOTES) for number in range(1, 40)]
    renderings = read(github, texts, assistant.Limits(4.0))
    assert renderings.requests == 2 and renderings.fallbacks == {
        assistant.OUT_OF_TIME: 39 - 32
    }


def test_the_limits_can_end_while_the_cursors_are_read():
    """The comment before a window lies on the second page of the cursors, and the
    limit of requests ends the reading after the first."""
    github = GitHubOfRenderings()
    texts = comments(
        github, 12, [f"c{n}" for n in range(150)], hidden=frozenset(range(120))
    )
    renderings = read(github, texts, assistant.Limits(60.0, requests=1))
    assert [request["query"] for request in github.requests] == [assistant.CURSORS]
    assert renderings.fallbacks == {assistant.OTHER_LIMIT: 30}


def test_a_limit_while_the_cursors_are_read_counts_each_text_once():
    """The cursors of the first issue fail and its window falls back; the limit of
    requests then ends the cursors of the second: only its window is left."""
    github = GitHubOfRenderings()
    texts = [
        *comments(github, 12, ["a", "b"], hidden=frozenset({0})),
        *comments(github, 13, ["c", "d"], hidden=frozenset({0})),
    ]
    github.failing = lambda aliases, request: SHAPES["a 504 with an HTML page"]
    renderings = read(github, texts, assistant.Limits(60.0, requests=2))
    assert renderings.fallbacks == {assistant.FAILED: 1, assistant.OTHER_LIMIT: 1}


def test_a_request_of_windows_that_all_lack_their_cursors_is_not_sent():
    github = GitHubOfRenderings()
    texts = comments(github, 12, ["a", "b"], hidden=frozenset({0}))
    github.failing = lambda aliases, request: SHAPES["a body cut off"]
    renderings = read(github, texts)
    assert [request["query"] for request in github.requests] == [assistant.CURSORS]
    assert renderings.fallbacks == {assistant.FAILED: 1}


def test_a_text_given_twice_is_asked_for_once():
    github = GitHubOfRenderings()
    text = issue(github, 1)
    renderings = read(github, [text, text])
    assert asked(github) == [1] and renderings.of(text) == html_of("Issue 1.")


def test_the_comments_of_an_issue_that_went_are_read_from_their_cleaned_source():
    github = GitHubOfRenderings()
    texts = [issue(github, 1), *comments(github, 12, ["a", "b"])]
    github.gone.add(12)
    renderings = read(github, texts)
    assert rendered(renderings, texts) == [html_of("Issue 1."), None, None]
    assert renderings.fallbacks == {assistant.NULL_ANSWER: 2}


@pytest.mark.parametrize(
    "change",
    [
        lambda window: window.clear(),
        lambda window: window.update(nodes=None),
        lambda window: window["nodes"][0].pop("fullDatabaseId"),
        lambda window: window["nodes"][0].pop("bodyHTML"),
    ],
)
def test_a_window_of_another_shape_fails_its_request(change):
    github = GitHubOfRenderings()
    texts = comments(github, 12, ["a", "b"])

    def failing(aliases, request):
        if request["query"] == assistant.CURSORS:
            return None
        answer = json.loads(
            github.answered(REPOSITORY, request, {"cost": 1, "remaining": 5000}).stdout
        )
        change(answer["data"]["repository"]["c12"]["w0"])
        return done(answer)

    github.failing = failing
    renderings = read(github, texts)
    assert renderings.fallbacks == {assistant.FAILED: 2} and renderings.failed == 3


@pytest.mark.parametrize(
    "page",
    [
        {"pageInfo": None, "edges": []},
        {"pageInfo": {"hasNextPage": "yes", "endCursor": "x"}, "edges": []},
        {"pageInfo": {"hasNextPage": True, "endCursor": None}, "edges": []},
        {
            "pageInfo": {"hasNextPage": False},
            "edges": [{"node": {"fullDatabaseId": "1"}}],
        },
        {"pageInfo": {"hasNextPage": False}, "edges": [{"cursor": "x", "node": None}]},
        {"pageInfo": {"hasNextPage": False}, "edges": ["x"]},
    ],
)
def test_a_page_of_cursors_of_another_shape_is_a_failed_read(page):
    body = {"data": {"repository": {"issue": {"comments": page}}}}
    assert assistant.page_of(body) is None
    assert assistant.page_of(None) is None
    assert assistant.page_of({"data": {"repository": None}}) is None


def test_the_renderings_stop_at_the_limits_of_the_command():
    """Each limit ends the renderings of the command; the issue and its comments
    come first, so they keep theirs."""

    def run(limits=LIMITS, seconds=0.5, failing=None, remaining=5000):
        github = GitHubOfRenderings(seconds)
        github.remaining = remaining
        texts = [issue(github, 1), *comments(github, 1, ["x"])]
        texts += [issue(github, number, FOOTNOTES) for number in range(2, 80)]
        if failing:
            github.failing = failing
        renderings = read(github, texts, limits)
        assert renderings.of(texts[0]) and renderings.of(texts[1])
        assert len(renderings.html) + sum(renderings.fallbacks.values()) == 80
        return renderings

    # The first request holds the issue, its comment and 16 other issues, each
    # later one 16 others.
    time = run(assistant.Limits(5.0), seconds=2.0)
    assert time.requests == 3 and time.fallbacks == {assistant.OUT_OF_TIME: 80 - 34}
    requests = run(assistant.Limits(60.0, requests=2))
    assert requests.requests == 2
    assert requests.fallbacks == {assistant.OTHER_LIMIT: 80 - 34}
    points = run(assistant.Limits(60.0, points=24), seconds=9.5)
    assert points.points == 24 and points.requests == 2
    assert points.fallbacks == {assistant.OTHER_LIMIT: 80 - 34}
    low = run(remaining=assistant.RESERVE + 1)
    assert low.lowest == assistant.RESERVE - 1 and low.requests == 2
    assert low.fallbacks == {assistant.OTHER_LIMIT: 80 - 34}
    failures = run(
        assistant.Limits(60.0, failures=2),
        failing=lambda aliases, request: (
            SHAPES["a body cut off"] if "i1" not in aliases else None
        ),
    )
    assert failures.failed == 2 and failures.requests == 3
    assert failures.fallbacks == {assistant.OTHER_LIMIT: 80 - 18}
    for limit in (SECONDARY, PRIMARY):
        limited = run(
            failing=lambda aliases, request, limit=limit: (
                limit if "i1" not in aliases else None
            )
        )
        assert limited.requests == 2 and limited.failed == 1
        assert limited.fallbacks == {assistant.OTHER_LIMIT: 80 - 18}


def test_the_time_of_a_call_ends_with_the_time_of_the_command():
    github = GitHubOfRenderings(seconds=8.0)
    texts = [issue(github, number, FOOTNOTES) for number in range(1, 40)]
    renderings = read(github, texts, assistant.Limits(20.0))
    assert [request["timeout"] for request in github.requests] == [20.0, 12.0, 4.0]
    assert renderings.requests == 3
    assert renderings.fallbacks == {assistant.OUT_OF_TIME: 39 - 32}


def test_the_limits_of_evaluate_grow_with_its_samples():
    assert assistant.Limits.of_evaluation(200) == assistant.Limits(2180.0, 260, 10, 550)
    assert assistant.Limits(60.0, 60, 10, 150) == assistant.ATTACHMENTS_RENDERING_LIMITS


def test_the_notice_counts_each_reason(capsys):
    renderings = assistant.Renderings()
    renderings.notice()
    assert capsys.readouterr().out == ""
    github = GitHubOfRenderings()
    texts = [
        issue(github, 1),
        issue(github, 2, "[^1]" * 3000),
        assistant.Text(REPOSITORY, assistant.ISSUE, 3, "Gone."),
    ]
    read(github, texts).notice()
    assert capsys.readouterr().out == (
        "::notice::Renderings of GitHub: 1 read; requests: 1, failed: 0; counted "
        "points: 1; seconds: 0.5; lowest remaining points: 4999. Texts read from "
        "their cleaned source: 2 (its estimate is above the cap: 1; GitHub answered "
        "null for it: 1).\n"
    )
    read(github, texts[:1]).notice()
    assert capsys.readouterr().out.endswith("their cleaned source: 0.\n")
    # A text above the cap alone sends no request, so GitHub reports no points.
    read(GitHubOfRenderings(), texts[1:2]).notice()
    assert "lowest remaining points: none reported." in capsys.readouterr().out


# The estimate: what a text may render to, held against what GitHub rendered.


def planted(head: str, pieces: Iterable[str], tail: str, length: int = 65_536) -> str:
    """A text in the shape of a planted text of #521: a head, as many of the pieces as
    fit, blanks and a tail, of the length of the planted one."""
    text = head
    for piece in pieces:
        if len(text) + len(piece) + len(tail) > length:
            break
        text += piece
    return text + " " * (length - len(text) - len(tail)) + tail


GREEK = [
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "zeta",
    "eta",
    "theta",
    "iota",
    "kappa",
    "lambda",
    "mu",
    "nu",
    "xi",
    "omicron",
    "pi",
    "rho",
    "sigma",
    "tau",
    "upsilon",
    "phi",
    "chi",
    "psi",
    "omega",
]
KINDS = ["NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION"]
FUNCTION = (
    "// zq comment line about the filler values\nfunction zqStep(count, label) {\n"
    '  const items = ["alpha", "beta", \'gamma\'];\n  let total = 0;\n'
    "  for (let i = 0; i < count; i++) {\n    total += items.length * i + 3.5;\n  }\n"
    "  return `${label}: ${total}`;\n}\n"
)


def tables(columns: int, count: int, rows: int) -> list[str]:
    table = "|" + "h|" * columns + "\n|" + "-|" * columns + "\n" + "|Zq\n" * rows
    return [table + "\n"] * count


def alert(number: int, words: int = 0) -> str:
    text = f"> [!{KINDS[number % 5]}]\n> Zq{number % 10}"
    if words:
        text += " " + " ".join((GREEK * 3)[number % 3 : number % 3 + words])
    return text + "\n\n"


# The planted texts of the measurement of #521, rebuilt in their shape, each with the
# bytes of GitHub's rendering of the planted one.
PLANTED = {
    "footnotes, one definition": (
        planted(
            "Zqfoot1 footnote references.\n\n",
            itertools.repeat("[^1]"),
            "\n\n[^1]: Zqfoot1note alpha beta gamma",
        ),
        6_289_910,
    ),
    "footnotes, distinct": (
        planted(
            "Zqfoot5 distinct footnotes.\n\n",
            [*(f"[^{n}]" for n in range(1, 2993)), "\n\n"],
            "\n".join(f"[^{n}]: Zq{n}" for n in range(1, 2993)),
        ),
        1_377_755,
    ),
    "alerts, short": (
        planted("Zqalert1 alerts.\n\n", map(alert, itertools.count()), ""),
        2_475_137,
    ),
    "alerts, long": (
        planted(
            "Zqalert5 alerts with longer text.\n\n",
            (alert(number, 40) for number in itertools.count()),
            "",
        ),
        268_147,
    ),
    "js, dense": (
        planted(
            "Zqcode1 dense code.\n\n```js\n",
            itertools.repeat("v=" + "+".join("1" * 30) + ";\n"),
            "\n```\n",
        ),
        1_871_545,
    ),
    "js, ordinary": (
        planted(
            "Zqcode5 ordinary code.\n\n```js\n", itertools.repeat(FUNCTION), "\n```\n"
        ),
        594_083,
    ),
    "100-column tables": (
        planted("Zqtable1 padded tables.\n\n", tables(100, 4, 897), "", 16_004),
        3_639_621,
    ),
    "200-column tables": (
        planted("Zqwide1 padded tables.\n\n", tables(200, 16, 310), "", 32_768),
        10_021_864,
    ),
}

# Small cases rendered once through `gh api -X POST markdown`, mode gfm, with the
# test repository of #521 as their context, which wraps each table as bodyHTML does.
ROWS = "|" + "h|" * 20 + "\n|" + "-|" * 20 + "\n" + "|Zq\n" * 20
MEASURED = {
    "a table of 20 columns and 20 rows": (ROWS, 4_613),
    "the same, aligned to the center": (ROWS.replace("-|", ":-:|"), 10_913),
    "the same in a quote": ("".join(f"> {row}\n" for row in ROWS.splitlines()), 4_640),
    "the same in an alert": (
        "> [!NOTE]\n" + "".join(f"> {row}\n" for row in ROWS.splitlines()),
        5_083,
    ),
    "the same in a list": (
        "- item\n\n" + "".join(f"  {row}\n" for row in ROWS.splitlines()),
        4_669,
    ),
    "the same without pipes at the margins": (
        "|".join(["h"] * 20) + "\n" + "|".join(["-"] * 20) + "\n" + "Zq|\n" * 20,
        4_613,
    ),
    "the same with escaped pipes in its cells": (ROWS.replace("|Zq", "|a\\|b"), 4_633),
    "fifty tables of one header cell and no rows": ("|a|\n|-|\n\n" * 50, 6_399),
    "fifty references": ("#6 " * 50, 19_267),
    "fifty words of hex digits": (" ".join(f"{n:07x}abc" for n in range(50)), 567),
    "emoji and shortcodes": (
        "\u26a0\ufe0f \U0001f389 :warning: :tada: :+1: " * 20,
        2_637,
    ),
}
MEASURED_IN = "Dennis-Otto/reading-measurements"
# A body of 3,450 B with three permalinks of 32 lines each rendered to 41,722 B on
# GitHub (rust-lang/rust#165478); this one has the same length and ranges.
PERMALINK = (
    "See https://github.com/o/r/blob/0123456789abcdef0123456789abcdef01234567/"
    "src/compile.rs#L2359-L2390 here.\n"
)
PERMALINKS = planted("", [PERMALINK] * 3, "", 3_450)


@pytest.mark.parametrize("name", PLANTED)
def test_the_estimate_is_above_each_planted_rendering(name):
    source, size = PLANTED[name]
    assert len(source) in (65_536, 16_004, 32_768)
    assert assistant.rendering_estimate(source, MEASURED_IN).size > size


def test_the_estimate_is_above_each_recorded_rendering():
    recorded = json.loads((FIXTURES / "renderings.json").read_text("utf-8"))["texts"]
    for text in recorded:
        repository = (
            "/".join(text["address"].split("/")[3:5]) or "Dennis-Otto/issue-assistant"
        )
        estimate = assistant.rendering_estimate(text["source"], repository)
        assert estimate.size > len(text["html"].encode()), text["name"]
    for name, (source, size) in MEASURED.items():
        assert assistant.rendering_estimate(source, MEASURED_IN).size > size, name
    assert assistant.rendering_estimate(PERMALINKS, "o/r").size > 41_722
    control = json.loads(RECORDED["exchanges"][1]["stdout"])["data"]["repository"]["i1"]
    assert assistant.rendering_estimate(control["body"], MEASURED_IN).size > len(
        control["bodyHTML"].encode()
    )


@pytest.mark.parametrize(
    ("source", "cells"),
    [
        # Quotes, alerts and lists hide no table, nor do missing pipes at the margins.
        ("> a | b\n> --- | ---\n> 1 | 2\n> 3 | 4\n", 6),
        ("> [!NOTE]\n> |a|b|c|\n> |-|-|-|\n> |1|\n", 6),
        ("- item\n\n  |a|b|\n  |-|-|\n  |1|\n", 4),
        # The larger reading of a header counts: with its list marker, 3 cells.
        ("1. |a|b|\n   |-|-|\n   |1|\n", 6),
        # The columns are the larger of the header and the delimiter row.
        ("|a|\n|-|-|-|\n|1|\n", 6),
        ("|a|b|c|d|\n|-|-|\n", 4),
        # An escaped pipe separates no cells.
        ("|a\\|b|\n|-|\n|1\\|2|\n", 2),
        # A blank row ends a table, a row in a quote ends at an empty quote.
        ("|a|b|\n|-|-|\n|1|\n\nlater\n", 4),
        ("|a|b|\n|-|-|\n|1|\n```\n|2|\n```\n", 4),
        ("> |a|b|\n> |-|-|\n> |1|\n>\n> later\n", 4),
        # No table: no header, a blank header, no pipe, a cell that isn't of `-`, a fence.
        ("|-|-|\n", 0),
        ("\n|-|-|\n", 0),
        ("a\n---\n", 0),
        ("|a|b|\n|-|x|\n", 0),
        ("|a|b|\n|\n", 0),
        ("```\n|a|b|\n|-|-|\n```\n", 0),
        # A delimiter row whose list marker is all it starts with.
        ("a | b\n- | -\n", 2),
    ],
)
def test_the_estimate_counts_the_cells_of_each_table(source, cells):
    assert assistant.rendering_estimate(source, REPOSITORY).cells == cells


def test_aligned_cells_and_tables_weigh_more():
    plain = assistant.rendering_estimate("|a|b|\n|-|-|\n", REPOSITORY)
    aligned = assistant.rendering_estimate("|a|b|\n|:-|-:|\n", REPOSITORY)
    assert plain.size == 8 * 12 + assistant.TABLE_BYTES + 2 * assistant.CELL_BYTES
    assert (
        aligned.size
        == 8 * 14
        + assistant.TABLE_BYTES
        + 2 * assistant.ALIGNED_CELL_BYTES
        + 2 * assistant.COLON_BYTES
    )


@pytest.mark.parametrize(
    ("source", "weight"),
    [
        ("[^1]", assistant.FOOTNOTE_BYTES),
        ("[!", assistant.ALERT_BYTES),
        ("#6", assistant.REFERENCE_BYTES + 3 * len(REPOSITORY)),
        ("GH-6", assistant.REFERENCE_BYTES + 3 * len(REPOSITORY)),
        ("deadbeef", assistant.REFERENCE_BYTES + 3 * len(REPOSITORY)),
        ("@a", assistant.MENTION_BYTES),
        ("$", assistant.DOLLAR_BYTES),
        ("[x]", assistant.TASK_BYTES),
        ("www.", assistant.ADDRESS_BYTES),
        ("🎉", assistant.EMOJI_BYTES),
        ("user-attachments/assets/", assistant.PICTURE_BYTES),
        (
            "/blob/main/a.py#L3-L5",
            assistant.PERMALINK_BYTES + 3 * assistant.PERMALINK_LINE_BYTES,
        ),
        (
            "/blob/main/a.py#L5-L3",
            assistant.PERMALINK_BYTES + assistant.PERMALINK_LINE_BYTES,
        ),
        (
            "/blob/main/a.py#L5",
            assistant.PERMALINK_BYTES + assistant.PERMALINK_LINE_BYTES,
        ),
    ],
)
def test_each_mark_weighs_what_it_renders_to(source, weight):
    size = len(source.encode()) * assistant.TEXT_BYTE
    assert assistant.rendering_estimate(source, REPOSITORY).size == size + weight


def test_nothing_in_a_fence_is_a_mark():
    for fence, weight in (
        ("```", assistant.FENCED_BYTE),
        ("~~~~ js", assistant.HIGHLIGHTED_BYTE),
        ("> ```py", 40),
    ):
        source = f"{fence}\n#1 [^1]\n{re.match('[`~]+', fence.lstrip('> '))[0]}\nafter"
        inside = len("#1 [^1]") + 1
        expected = 8 * len(source.encode()) + (weight - 8) * inside
        assert assistant.rendering_estimate(source, REPOSITORY).size == expected, fence
    # A row of backticks with a backtick in its info string opens no fence.
    assert assistant.rendering_estimate("``` a`b\n#1", REPOSITORY).size > 8 * 10 + 400
    # A fence that never closes runs to the end of the text.
    assert (
        assistant.rendering_estimate("```\n#1\n", REPOSITORY).size
        == 8 * 7 + 4 * 3 + 4 * 1
    )
