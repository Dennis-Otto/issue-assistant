"""Every REST request of the script, checked against GitHub's OpenAPI description.

The fake of the API in support.py answers what a test sets up, so a misspelt query
parameter or an endpoint that GitHub deprecates passes every test that only asks the
fake. Here each call of the script is read from its source, with its method, its path
and its query, and checked against tests/github/api.github.com.json, the part of
GitHub's description of the version that the script asks for which a request can
break. The fake and the stand-in of gh check what each test sends as well, with the
values that only a run knows; see described() in support.py.

The Action test workflow checks the requests every week against the description of
that day, which REST_DESCRIPTION names. scripts/github_schemas.py writes the copy, and
the workflow brings a new one in a pull request of its own.
"""

import ast
import io
import json
import runpy
import sys
from collections.abc import Iterator
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode

import pytest
import yaml

import issue_assistant as assistant
from support import CONFIG, REPOSITORY, FakeGitHub, fake_gh, make_issue, schemas

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "issue_assistant.py"
WORKFLOW = ROOT / ".github" / "workflows" / "action-test.yml"
# The methods that send a request, by the path that their callers name.
SENDERS = {"rest", "_remembered", "_gh"}

# A description in GitHub's form, with each kind of part that the reduction reads.
FULL = {
    "paths": {
        "/repos/{owner}/{repo}/issues": {
            "get": {
                "parameters": [
                    {"$ref": "#/components/parameters/owner"},
                    {"$ref": "#/components/parameters/per-page"},
                    {
                        "name": "state",
                        "in": "query",
                        "schema": {"type": "string", "enum": ["open", "all"]},
                    },
                    {"$ref": "#/components/parameters/alert-state"},
                    {"name": "old", "in": "query", "deprecated": True},
                    {"name": "X-Header", "in": "header", "schema": {"type": "string"}},
                ],
            },
            "post": {"deprecated": True},
            "description": "not a method",
        },
        "/repos/{owner}/{repo}/issues/comments": {"get": {}},
        "/repos/{owner}/{repo}/issues/{issue_number}": {
            "get": {
                "parameters": [
                    {"$ref": "#/components/parameters/owner"},
                    {"$ref": "#/components/parameters/issue-number"},
                ]
            },
        },
        "/repos/{owner}/{repo}/compare/{basehead}": {
            "get": {
                "parameters": [
                    {
                        "name": "basehead",
                        "in": "path",
                        "schema": {"type": "string"},
                        "x-multi-segment": True,
                    },
                    {
                        "name": "ref",
                        "in": "query",
                        "schema": {"type": "string"},
                        "x-multi-segment": True,
                    },
                ]
            },
        },
    },
    "components": {
        "parameters": {
            "owner": {"name": "owner", "in": "path", "schema": {"type": "string"}},
            "per-page": {
                "name": "per_page",
                "in": "query",
                "schema": {"type": "integer"},
            },
            "issue-number": {
                "name": "issue_number",
                "in": "path",
                "schema": {"$ref": "#/components/schemas/number"},
            },
            "alert-state": {
                "name": "alert",
                "in": "query",
                "schema": {"$ref": "#/components/schemas/state"},
            },
        },
        "schemas": {
            "number": {"type": "integer"},
            "state": {"type": "string", "enum": ["open"], "description": "unread"},
        },
    },
}
REDUCED = {
    "api_version": assistant.API_VERSION,
    "paths": {
        "/repos/{owner}/{repo}/issues": {
            "get": {
                "query": {
                    "per_page": {"type": "integer"},
                    "state": {"type": "string", "enum": ["open", "all"]},
                    "alert": {"type": "string", "enum": ["open"]},
                    "old": {"deprecated": True},
                }
            },
            "post": {"deprecated": True, "query": {}},
        },
        "/repos/{owner}/{repo}/issues/comments": {"get": {"query": {}}},
        "/repos/{owner}/{repo}/issues/{issue_number}": {
            "get": {"query": {}},
            "placeholders": {"issue_number": "integer"},
        },
        "/repos/{owner}/{repo}/compare/{basehead}": {
            "get": {"query": {"ref": {"type": "string"}}},
            "placeholders": {"basehead": "multi-segment"},
        },
    },
}
ISSUES = "/repos/{owner}/{repo}/issues"


def test_the_reduction_keeps_what_a_request_can_break():
    assert schemas.reduce(FULL) == REDUCED


def test_the_copy_is_a_reduced_description_of_the_version_of_the_script():
    path = schemas.COPIES / schemas.DESCRIPTION
    text = path.read_text(encoding="utf-8")
    assert schemas.as_text(json.loads(text)) == text
    assert json.loads(text)["api_version"] == assistant.API_VERSION
    assert f"api.github.com.{assistant.API_VERSION}.json" in schemas.DESCRIPTION_URL
    assert assistant.API_VERSION in assistant.API_VERSION_HEADER


def test_the_weekly_run_names_the_description_of_that_day(monkeypatch, tmp_path):
    day = tmp_path / "day.json"
    day.write_text(json.dumps(REDUCED), encoding="utf-8")
    monkeypatch.setenv("REST_DESCRIPTION", str(day))
    schemas.pinned.cache_clear()
    try:
        assert schemas.pinned().problems("GET", "repos/o/n/nowhere")
        assert not schemas.pinned().problems("GET", "repos/o/n/issues")
    finally:
        schemas.pinned.cache_clear()


@pytest.mark.parametrize(
    ("method", "request_", "problems"),
    [
        ("GET", "repos/o/n/issues?state=all&per_page=100", []),
        ("GET", "repos/o/n/issues/comments", []),
        ("GET", "repos/o/n/issues/12", []),
        ("GET", "repos/o/n/compare/1a...release/1.0?ref=x", []),
        (
            "GET",
            "repos/o/n/labels",
            ["GitHub's description has no path /repos/o/n/labels."],
        ),
        # No number where GitHub takes a number, so no issue either.
        (
            "GET",
            "repos/o/n/issues/commentz",
            ["GitHub's description has no path /repos/o/n/issues/commentz."],
        ),
        ("PUT", "repos/o/n/issues", [f"GitHub's description has no PUT {ISSUES}."]),
        ("POST", "repos/o/n/issues", [f"GitHub deprecates POST {ISSUES}."]),
        (
            "GET",
            "repos/o/n/issues?per_pag=100",
            [f"GET {ISSUES} has no query parameter per_pag."],
        ),
        (
            "GET",
            "repos/o/n/issues?old=1",
            [f"GitHub deprecates the parameter old of GET {ISSUES}."],
        ),
        (
            "GET",
            "repos/o/n/issues?state=closed",
            [f"The parameter state of GET {ISSUES} doesn't take closed."],
        ),
        (
            "GET",
            "repos/o/n/issues?per_page=many&state=all",
            [f"The parameter per_page of GET {ISSUES} is a number, not many."],
        ),
    ],
)
def test_the_description_says_what_is_wrong_with_a_request(method, request_, problems):
    assert schemas.Description(REDUCED).problems(method, request_) == problems


@pytest.mark.parametrize(
    ("arguments", "request_"),
    [
        (["repos/o/n/issues/1"], ("GET", "repos/o/n/issues/1")),
        (
            ["repos/o/n/labels?per_page=100", "--paginate", "--slurp"],
            ("GET", "repos/o/n/labels?per_page=100"),
        ),
        (
            ["repos/o/n/labels", "--method", "PATCH", "--input", "-"],
            ("PATCH", "repos/o/n/labels"),
        ),
        (["repos/o/n/labels", "--input", "-"], ("POST", "repos/o/n/labels")),
        (["repos/o/n/labels", "-f", "name=bug"], ("POST", "repos/o/n/labels")),
        (
            ["--method", "GET", "search/issues", "-f", "q=a&b c", "-F", "per_page=3"],
            ("GET", "search/issues?q=a%26b+c&per_page=3"),
        ),
        (
            ["--method", "GET", "search/issues?page=2", "-F", "per_page=3"],
            ("GET", "search/issues?page=2&per_page=3"),
        ),
        (
            ["-H", "Accept: raw", "repos/o/n", "--jq", ".name", "--header", "A: b"],
            ("GET", "repos/o/n"),
        ),
        (["graphql", "--input", "-"], None),
    ],
)
def test_the_method_and_the_path_of_a_command_of_gh(arguments, request_):
    assert schemas.request_of(arguments) == request_


# The requests of the script, from its source


def text_of(node: ast.expr, known: dict[str, str]) -> str:
    """The text of an expression, with 1 for each value that only a run knows, which
    every placeholder and every parameter without values of its own takes."""
    match node:
        case ast.Constant(value=str() as text):
            return text
        case ast.Name(id=name) if name in known:
            return known[name]
        case ast.JoinedStr(values=values):
            return "".join(text_of(value, known) for value in values)
        case ast.FormattedValue(value=ast.Attribute(attr="repository")):
            return REPOSITORY
        case ast.FormattedValue(value=ast.Name(id=name)) if name in known:
            return known[name]
        case ast.FormattedValue():
            return "1"
        case ast.BinOp(op=ast.Add(), left=left, right=right):
            return text_of(left, known) + text_of(right, known)
        case ast.Call(func=ast.Name(id="urlencode"), args=[ast.Dict() as fields]):
            return urlencode(
                {
                    key.value: value.value if isinstance(value, ast.Constant) else "1"
                    for key, value in zip(fields.keys, fields.values, strict=True)
                    if isinstance(key, ast.Constant)
                }
            )
    raise AssertionError(f"line {node.lineno}: a request that this test can't read")


def functions_of(tree: ast.Module) -> Iterator[ast.FunctionDef]:
    """The functions and the methods; a nested function belongs to the one around it."""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            yield node
        elif isinstance(node, ast.ClassDef):
            yield from (item for item in node.body if isinstance(item, ast.FunctionDef))


def method_of(call: ast.Call) -> str:
    for keyword in call.keywords:
        if keyword.arg == "method":
            assert isinstance(keyword.value, ast.Constant), f"line {call.lineno}"
            return str(keyword.value.value)
    return "GET"


def requests_in(source: str) -> Iterator[tuple[int, str, str]]:
    """Each REST request of the source with its line, its method and its path and
    query. A method that only hands on what its callers name has its callers read."""
    for function in functions_of(ast.parse(source)):
        if function.name in SENDERS:
            continue
        known: dict[str, str] = {}
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.JoinedStr)
                and isinstance(node.targets[0], ast.Name)
            ):
                known[node.targets[0].id] = text_of(node.value, known)
        for node in ast.walk(function):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Attribute)
                or node.func.attr not in SENDERS
            ):
                continue
            first = node.args[0]
            if node.func.attr != "_gh":
                path = f"repos/{REPOSITORY}/{text_of(first, known)}"
                yield node.lineno, method_of(node), path
                continue
            assert isinstance(first, ast.List), f"line {node.lineno}"
            words = [text_of(word, known) for word in first.elts]
            assert words[0] == "api", f"line {node.lineno}"
            request = schemas.request_of(words[1:])
            if request:
                yield node.lineno, *request


def problems_in(source: str) -> list[str]:
    return [
        f"line {line}: {problem}"
        for line, method, request in requests_in(source)
        for problem in schemas.pinned().problems(method, request)
    ]


@pytest.mark.mutation_unsafe
def test_every_rest_request_of_the_script_is_in_github_s_description():
    source = SCRIPT.read_text(encoding="utf-8")
    assert problems_in(source) == []
    requests = {request for _, _, request in requests_in(source)}
    # The reading finds each kind of call, also those that build their path in steps.
    own = f"repos/{REPOSITORY}"
    assert {
        own,
        f"{own}/labels?per_page=100",
        f"{own}/milestones?state=all&per_page=100",
        f"{own}/releases/latest",
        f"{own}/collaborators/1/permission",
        f"{own}/compare/1...1?per_page=1",
        f"{own}/assignees/1",
        f"{own}/issues/1/comments?per_page=100",
        f"{own}/environments/1/secrets?per_page=100",
        f"{own}/environments/1/deployment-branch-policies",
        "search/issues?q=1&per_page=1",
        "search/issues?q=1&sort=created&order=asc&per_page=100&page=1",
    } <= requests
    assert len(requests) > 30


@pytest.mark.parametrize(
    ("code", "problem"),
    [
        (
            'self.rest("labels?per_pag=100", pages=True)',
            "has no query parameter per_pag",
        ),
        ('self.rest(f"labelz/{name}")', "no path"),
        ('github.rest("labels", method="PUT")', "no PUT"),
        ('self._remembered(f"issues/{number}/events?page_size=100")', "page_size"),
        ('self._gh(["api", "search/issuez"])', "no path"),
        (
            'self._gh(["api", "--method", "GET", "search/issues", "-f", "x=1"])',
            "no query",
        ),
    ],
)
def test_a_wrong_request_in_the_source_fails(code, problem):
    source = (
        f"class GitHub:\n    def read(self, github, name, number):\n        {code}\n"
    )
    (found,) = problems_in(source)
    assert found.startswith("line 3: ") and problem in found


@pytest.mark.parametrize(
    "code",
    [
        'self.rest(path, method="PUT")',
        'self.rest("labels", method=method)',
        "self._gh(arguments)",
        'self._gh(["repo", "view"])',
        'self.rest("labels" + name)',
        'self.rest(f"labels/{self.owner}" * 2)',
    ],
)
def test_a_request_that_the_reading_can_t_read_fails(code):
    source = f"def read(self, name):\n    {code}\n"
    with pytest.raises(AssertionError, match="line 2"):
        list(requests_in(source))


def test_the_reading_follows_a_path_that_a_function_builds_in_steps():
    source = (
        "def read(self, github, name):\n"
        '    path = f"environments/{name}"\n'
        '    github.rest(path, method="PUT", data={})\n'
        '    github.rest(f"{path}/secrets?per_page=100")\n'
        '    self._gh(["api", "graphql", "--input", "-"], {})\n'
        "    self.rest(\"x?\" + urlencode({'a': 'b', 'c': d}))\n"
        "def rest(self, path):\n"
        "    return self.now.rest(path)\n"
    )
    assert list(requests_in(source)) == [
        (3, "PUT", f"repos/{REPOSITORY}/environments/1"),
        (4, "GET", f"repos/{REPOSITORY}/environments/1/secrets?per_page=100"),
        (6, "GET", f"repos/{REPOSITORY}/x?a=b&c=1"),
    ]


# What the tests send


@pytest.mark.parametrize(
    ("path", "problem"),
    [("issues/12?per_pag=100", "per_pag"), ("issuez", "no path")],
)
def test_the_fake_of_github_refuses_a_request_outside_the_description(path, problem):
    github = FakeGitHub(CONFIG, make_issue(12))
    with pytest.raises(AssertionError, match=problem):
        github.rest(path)
    assert github.calls == []


def test_the_fake_of_github_checks_the_search_too():
    with pytest.raises(AssertionError, match="no query parameter bad"):
        FakeGitHub(CONFIG)._gh(["api", "search/issues?bad=1"])


def test_the_stand_in_of_gh_refuses_a_request_outside_the_description(
    monkeypatch, tmp_path
):
    fake_gh(monkeypatch, tmp_path, {"stdout": "{}"})
    with pytest.raises(AssertionError, match="no path"):
        assistant.GitHub(REPOSITORY).rest("labelz")


def test_the_stand_in_of_gh_refuses_a_call_without_the_version(monkeypatch, tmp_path):
    fake_gh(monkeypatch, tmp_path, {"stdout": "{}"})
    with pytest.raises(AssertionError, match="repos/owner/project"):
        assistant.subprocess.run(["gh", "api", "repos/owner/project"], check=False)


@pytest.mark.parametrize(
    "send",
    [
        lambda github: github.rest("issues/1"),
        lambda github: github.rest("labels?per_page=100", pages=True),
        lambda github: github.rest("issues/1", method="PATCH", data={"state": "open"}),
        lambda github: github.default_branch(),
        lambda github: github.public(),
        lambda github: github.graphql("query { viewer { login } }", {}),
        lambda github: github.search("repo:owner/project is:issue (bug)"),
        lambda github: next(github.search_issues_where("is:closed")),
    ],
)
def test_every_kind_of_call_asks_for_the_version_of_the_api(
    monkeypatch, tmp_path, send
):
    answer = '{"default_branch": "main", "items": [{}], "data": {}}'
    fake_gh(monkeypatch, tmp_path, {"stdout": answer})
    send(assistant.GitHub(REPOSITORY))
    (line,) = (tmp_path / "gh-calls.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(line)["arguments"][-2:] == [
        "--header",
        f"X-GitHub-Api-Version: {assistant.API_VERSION}",
    ]


@pytest.mark.mutation_unsafe
def test_every_call_of_gh_api_goes_through_the_one_method_that_adds_the_version():
    commands = [
        node.elts
        for node in ast.walk(ast.parse(SCRIPT.read_text(encoding="utf-8")))
        if isinstance(node, ast.List)
        and node.elts
        and isinstance(node.elts[0], ast.Constant)
        and node.elts[0].value == "gh"
    ]
    assert len(commands) > 1
    for words in commands:
        assert not (isinstance(words[1], ast.Constant) and words[1].value == "api")


# The weekly refresh of the copies


def ok(body):
    return {"stdout": json.dumps(body)}


MISSING = {"stderr": "gh: Not Found (HTTP 404)", "status": 1}
READ_MAIN = [
    ok({"default_branch": "main"}),
    ok({"object": {"sha": "head"}}),
    ok({"tree": {"sha": "base"}}),
    ok({"sha": "blob-1"}),
    ok({"sha": "blob-2"}),
]
SIGN_OFF = "app[bot] <7+app[bot]@users.noreply.github.com>"


@pytest.fixture
def folder(tmp_path):
    copies = tmp_path / "copies"
    copies.mkdir()
    (copies / schemas.SCHEMA).write_text("type Query\n", encoding="utf-8")
    (copies / schemas.DESCRIPTION).write_text("{}\n", encoding="utf-8")
    return copies


def refreshed(monkeypatch, tmp_path, folder, *answers):
    calls = fake_gh(monkeypatch, tmp_path, *READ_MAIN, *answers)
    github = assistant.GitHub(REPOSITORY)
    number = schemas.refresh(github, folder, SIGN_OFF, "https://run")
    return number, [
        (command[2:], json.loads(text) if text else None) for command, text in calls
    ]


def writes(calls):
    return [
        (arguments[0], arguments[2], body)
        for arguments, body in calls
        if "--method" in arguments and "git/blobs" not in arguments[0]
    ]


OWN = f"repos/{REPOSITORY}"
PULLS = f"{OWN}/pulls?state=open&head=owner%3Abot%2Fgithub-schemas&per_page=100"


def test_copies_that_are_current_open_no_pull_request(monkeypatch, tmp_path, folder):
    number, calls = refreshed(monkeypatch, tmp_path, folder, ok({"sha": "base"}))
    assert number is None
    assert [call[0][0] for call in calls][1:3] == [
        f"{OWN}/git/ref/heads/main",
        f"{OWN}/git/commits/head",
    ]
    assert writes(calls) == [
        (
            f"{OWN}/git/trees",
            "POST",
            {
                "base_tree": "base",
                "tree": [
                    {
                        "path": f"tests/github/{name}",
                        "mode": "100644",
                        "type": "blob",
                        "sha": sha,
                    }
                    for name, sha in (
                        (schemas.SCHEMA, "blob-1"),
                        (schemas.DESCRIPTION, "blob-2"),
                    )
                ],
            },
        )
    ]
    assert calls[3][1] == {"content": "type Query\n", "encoding": "utf-8"}
    assert calls[4][1] == {"content": "{}\n", "encoding": "utf-8"}


def test_new_copies_open_the_pull_request(monkeypatch, tmp_path, folder):
    number, calls = refreshed(
        monkeypatch,
        tmp_path,
        folder,
        ok({"sha": "tree"}),
        MISSING,
        ok({"sha": "commit"}),
        ok({}),
        ok([]),
        ok({"number": 41}),
    )
    assert number == 41
    (_, commit, reference, pull) = writes(calls)
    assert commit == (
        f"{OWN}/git/commits",
        "POST",
        {
            "message": f"{schemas.TITLE}\n\nSigned-off-by: {SIGN_OFF}\n",
            "tree": "tree",
            "parents": ["head"],
        },
    )
    assert reference == (
        f"{OWN}/git/refs",
        "POST",
        {"ref": "refs/heads/bot/github-schemas", "sha": "commit"},
    )
    assert pull[:2] == (f"{OWN}/pulls", "POST")
    assert pull[2]["title"] == schemas.TITLE
    assert (pull[2]["head"], pull[2]["base"]) == ("bot/github-schemas", "main")
    assert "https://run" in pull[2]["body"]
    assert calls[-2][0][0] == PULLS


def test_newer_copies_update_the_one_pull_request(monkeypatch, tmp_path, folder):
    number, calls = refreshed(
        monkeypatch,
        tmp_path,
        folder,
        ok({"sha": "tree"}),
        ok({"object": {"sha": "old"}}),
        ok({"tree": {"sha": "older"}}),
        ok({"sha": "commit"}),
        ok({}),
        ok([{"number": 41}]),
    )
    assert number == 41
    (_, commit, reference) = writes(calls)
    assert commit[2]["parents"] == ["head"]
    assert reference == (
        f"{OWN}/git/refs/heads/bot/github-schemas",
        "PATCH",
        {"sha": "commit", "force": True},
    )


def test_a_pull_request_that_has_these_copies_stays_as_it_is(
    monkeypatch, tmp_path, folder
):
    number, calls = refreshed(
        monkeypatch,
        tmp_path,
        folder,
        ok({"sha": "tree"}),
        ok({"object": {"sha": "old"}}),
        ok({"tree": {"sha": "tree"}}),
        ok([{"number": 41}]),
    )
    assert number == 41
    assert len(writes(calls)) == 1


def test_a_branch_that_a_closed_pull_request_left_behind_gets_a_new_one(
    monkeypatch, tmp_path, folder
):
    number, calls = refreshed(
        monkeypatch,
        tmp_path,
        folder,
        ok({"sha": "tree"}),
        ok({"object": {"sha": "old"}}),
        ok({"tree": {"sha": "older"}}),
        ok({"sha": "commit"}),
        ok({}),
        ok([]),
        ok({"number": 42}),
    )
    assert number == 42
    assert [write[:2] for write in writes(calls)][-2:] == [
        (f"{OWN}/git/refs/heads/bot/github-schemas", "PATCH"),
        (f"{OWN}/pulls", "POST"),
    ]


def test_an_error_of_github_about_the_branch_stops_the_refresh(
    monkeypatch, tmp_path, folder
):
    with pytest.raises(assistant.GitHubError):
        refreshed(
            monkeypatch,
            tmp_path,
            folder,
            ok({"sha": "tree"}),
            {"stderr": "gh: Forbidden (HTTP 403)", "status": 1},
        )


def in_a_run(monkeypatch, tmp_path, *arguments):
    """The command of the job, with its variables and the files of the runner."""
    monkeypatch.setenv("GH_REPO", REPOSITORY)
    monkeypatch.setenv("APP_SLUG", "app")
    monkeypatch.setenv("RUN_URL", "https://run")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary"))
    monkeypatch.setattr(sys, "argv", ["github_schemas.py", *arguments])
    return schemas.main()


def read(path):
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_the_job_gets_the_number_of_the_pull_request(monkeypatch, tmp_path, folder):
    calls = fake_gh(
        monkeypatch,
        tmp_path,
        ok({"id": 7}),
        *READ_MAIN,
        ok({"sha": "tree"}),
        ok({"object": {"sha": "old"}}),
        ok({"tree": {"sha": "tree"}}),
        ok([{"number": 41}]),
    )
    assert in_a_run(monkeypatch, tmp_path, "refresh", str(folder)) == 0
    assert read(tmp_path / "output") == "number=41\n"
    assert "#41" in read(tmp_path / "summary")
    assert calls[0][0] == ["gh", "api", "users/app%5Bbot%5D"]


def test_the_job_has_nothing_to_merge_when_the_copies_are_current(
    monkeypatch, tmp_path, folder
):
    fake_gh(monkeypatch, tmp_path, ok({"id": 7}), *READ_MAIN, ok({"sha": "base"}))
    assert in_a_run(monkeypatch, tmp_path, "refresh", str(folder)) == 0
    assert read(tmp_path / "output") == ""
    assert "current" in read(tmp_path / "summary")


def test_the_job_writes_nowhere_without_the_files_of_the_runner(
    monkeypatch, tmp_path, folder
):
    fake_gh(monkeypatch, tmp_path, ok({"id": 7}), *READ_MAIN, ok({"sha": "base"}))
    monkeypatch.setenv("GH_REPO", REPOSITORY)
    monkeypatch.setenv("APP_SLUG", "app")
    monkeypatch.setenv("RUN_URL", "https://run")
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.setattr(sys, "argv", ["github_schemas.py", "refresh", str(folder)])
    assert schemas.main() == 0


def test_a_refusal_of_github_fails_the_job_with_its_message(
    monkeypatch, tmp_path, folder, capsys
):
    fake_gh(monkeypatch, tmp_path, {"stderr": "gh: Forbidden (HTTP 403)", "status": 1})
    assert in_a_run(monkeypatch, tmp_path, "refresh", str(folder)) == 1
    assert "::error::" in capsys.readouterr().out


def test_the_download_writes_both_copies(monkeypatch, tmp_path):
    def answer(request, timeout):
        assert request.get_header("User-agent") == "issue-assistant" and timeout == 60
        if request.full_url == schemas.SCHEMA_URL:
            return io.BytesIO(b"type Query\r\n")
        assert request.full_url == schemas.DESCRIPTION_URL
        return io.BytesIO(json.dumps(FULL).encode("utf-8"))

    monkeypatch.setattr(schemas, "urlopen", answer)
    target = tmp_path / "new" / "copies"
    assert in_a_run(monkeypatch, tmp_path, "download", str(target)) == 0
    # The schema as GitHub publishes it, byte by byte, for the comparison with the copy.
    assert (target / schemas.SCHEMA).read_bytes() == b"type Query\r\n"
    written = (target / schemas.DESCRIPTION).read_bytes()
    assert json.loads(written) == REDUCED and written.endswith(b"}\n")
    assert b"\r" not in written


def test_a_moment_of_failure_of_the_host_is_tried_again(monkeypatch, capsys):
    answers = [URLError("down"), TimeoutError("slow"), io.BytesIO(b"body")]
    pauses = []

    def answer(request, timeout):
        found = answers.pop(0)
        if isinstance(found, Exception):
            raise found
        return found

    monkeypatch.setattr(schemas, "urlopen", answer)
    monkeypatch.setattr(schemas.time, "sleep", pauses.append)
    assert schemas.fetch("https://host/file") == b"body"
    assert pauses == [schemas.PAUSE, schemas.PAUSE]
    assert capsys.readouterr().out.count("::notice::") == 2


def test_a_host_that_stays_down_fails_the_download(monkeypatch, tmp_path, capsys):
    def answer(request, timeout):
        raise URLError("down")

    monkeypatch.setattr(schemas, "urlopen", answer)
    monkeypatch.setattr(schemas.time, "sleep", lambda seconds: None)
    assert in_a_run(monkeypatch, tmp_path, "download", str(tmp_path / "copies")) == 1
    assert f"::error::GitHub gave no {schemas.SCHEMA_URL}" in capsys.readouterr().out


def test_the_script_names_its_commands(monkeypatch, tmp_path, capsys):
    assert in_a_run(monkeypatch, tmp_path, "other") == 2
    assert "Usage" in capsys.readouterr().err


def test_the_script_runs_as_a_program(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["github_schemas.py"])
    with pytest.raises(SystemExit) as stop:
        runpy.run_path(str(ROOT / "scripts" / "github_schemas.py"), run_name="__main__")
    assert stop.value.code == 2
    assert "Usage" in capsys.readouterr().err


# The job that runs it


def jobs():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def step(job, name):
    (found,) = [item for item in job["steps"] if item.get("name") == name]
    return found


def test_the_weekly_job_checks_the_requests_against_what_github_publishes_that_day():
    job = jobs()["schema"]
    download = step(job, "Download GitHub's schema and description of today")
    assert download["run"].startswith("python scripts/github_schemas.py download ")
    check = step(job, "Check the requests against them")
    assert "tests/test_graphql_contract.py tests/test_rest_contract.py" in check["run"]
    assert check["env"] == {
        "GRAPHQL_SCHEMA": f"${{{{ runner.temp }}}}/github/{schemas.SCHEMA}",
        "REST_DESCRIPTION": f"${{{{ runner.temp }}}}/github/{schemas.DESCRIPTION}",
    }
    keep = step(job, "Keep them for the pull request")
    assert keep["with"]["name"] == "github-schemas"


def test_only_copies_that_passed_reach_the_pull_request():
    job = jobs()["schema-refresh"]
    # A condition without a status function needs the job before to have passed.
    assert job["needs"] == "schema"
    assert "needs.schema.outputs.changed == 'true'" in job["if"]
    for word in ("always", "failure", "cancelled"):
        assert word not in job["if"]
    assert job["environment"] == {"name": "release", "deployment": False}
    assert job["permissions"] == {"contents": "read"}
    assert job["concurrency"]["cancel-in-progress"] is False


def test_the_token_of_the_pull_request_can_do_only_what_it_needs():
    job = jobs()["schema-refresh"]
    token = step(job, "Create a token of the release app")["with"]
    assert {key for key in token if key.startswith("permission-")} == {
        "permission-contents",
        "permission-pull-requests",
    }
    refresh = step(job, "Open or update the one pull request")
    assert refresh["run"].startswith("python3 scripts/github_schemas.py refresh ")
    merge = step(job, "Let the pull request merge itself")
    assert merge["if"] == "steps.refresh.outputs.number != ''"
    assert "gh pr merge --auto --squash" in merge["run"]
