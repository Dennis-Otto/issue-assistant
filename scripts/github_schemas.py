"""GitHub's own descriptions of its API, and the copies of them in tests/github/.

The tests check what the script sends against these copies, so that a misspelt
parameter or an endpoint that GitHub deprecates fails before a release:

- tests/github/schema.docs.graphql, GitHub's GraphQL schema as it is, for
  tests/test_graphql_contract.py.
- tests/github/api.github.com.json, for tests/test_rest_contract.py: the part of
  GitHub's OpenAPI description of the version that the script asks for, which a request
  can break. That is every operation with its path, whether GitHub deprecates it, and
  its query parameters. GitHub's whole file is 14 MB of texts, examples and answers,
  which every repository that uses the action would download with it, on every run.

`download` writes both as GitHub publishes them today into a folder. `refresh` brings
the two files of such a folder to the default branch: with one pull request from the
branch bot/github-schemas, whose one commit sits on the newest commit of the default
branch, and none when the copies are current, which also closes one still open. The
job schema of the Action test runs it, once the contract tests have passed against the
files of that folder.

    python3 scripts/github_schemas.py download tests/github
    GH_REPO=owner/name APP_SLUG=app RUN_URL=https://... \\
        python3 scripts/github_schemas.py refresh FOLDER
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import issue_assistant as assistant  # noqa: E402

SCHEMA = "schema.docs.graphql"
DESCRIPTION = "api.github.com.json"
COPIES = ROOT / "tests" / "github"
SCHEMA_URL = "https://docs.github.com/public/fpt/schema.docs.graphql"
DESCRIPTION_URL = (
    "https://raw.githubusercontent.com/github/rest-api-description/main/"
    f"descriptions/api.github.com/api.github.com.{assistant.API_VERSION}.json"
)
# The tries of a download, and the seconds between them.
TRIES = 3
PAUSE = 5

BRANCH = "bot/github-schemas"
TITLE = "test: refresh the copies of GitHub's schemas"

METHODS = ("get", "post", "put", "patch", "delete")
# The placeholders of a path whose value isn't any one segment, as a pattern.
PLACEHOLDERS = {"integer": "[0-9]+", "multi-segment": ".+"}


def resolved(full: Mapping[str, Any], node: Mapping[str, Any]) -> Mapping[str, Any]:
    """The part of the description that a `$ref` points to, or the node itself."""
    while "$ref" in node:
        target = full
        for key in node["$ref"].removeprefix("#/").split("/"):
            target = target[key]
        node = target
    return node


def reduce(full: Mapping[str, Any]) -> dict[str, Any]:
    """The part of GitHub's OpenAPI description that a request can break."""
    paths: dict[str, Any] = {}
    for template, item in full["paths"].items():
        entry: dict[str, Any] = {}
        placeholders: dict[str, str] = {}
        for method in METHODS:
            if method not in item:
                continue
            query: dict[str, Any] = {}
            for reference in item[method].get("parameters", ()):
                parameter = resolved(full, reference)
                schema = resolved(full, parameter.get("schema", {}))
                if parameter["in"] == "query":
                    query[parameter["name"]] = {
                        key: schema[key] for key in ("type", "enum") if key in schema
                    } | ({"deprecated": True} if parameter.get("deprecated") else {})
                elif parameter["in"] == "path":
                    kind = (
                        "multi-segment"
                        if parameter.get("x-multi-segment")
                        else schema.get("type")
                    )
                    if kind in PLACEHOLDERS:
                        placeholders[parameter["name"]] = kind
            entry[method] = {"query": query} | (
                {"deprecated": True} if item[method].get("deprecated") else {}
            )
        if placeholders:
            entry["placeholders"] = placeholders
        paths[template] = entry
    return {"api_version": assistant.API_VERSION, "paths": paths}


class Description:
    """A reduced description, which says what is wrong with a request."""

    def __init__(self, reduced: Mapping[str, Any]) -> None:
        self.paths: Mapping[str, Mapping[str, Any]] = reduced["paths"]
        # With the fewest placeholders first, so that issues/comments isn't taken for
        # issues/{issue_number}.
        self.routes: list[tuple[re.Pattern[str], str]] = [
            (self._pattern(template), template)
            for template in sorted(self.paths, key=lambda text: (text.count("{"), text))
        ]
        # The fake of GitHub asks for each of its calls, and most are the same path.
        self.route = lru_cache(maxsize=4096)(self._route)

    def _pattern(self, template: str) -> re.Pattern[str]:
        kinds = self.paths[template].get("placeholders", {})
        parts = re.split(r"\{([^}]+)\}", template)
        parts[::2] = [re.escape(part) for part in parts[::2]]
        parts[1::2] = [
            PLACEHOLDERS.get(kinds.get(name), "[^/]+") for name in parts[1::2]
        ]
        return re.compile("".join(parts))

    def _route(self, path: str) -> str | None:
        for pattern, template in self.routes:
            if pattern.fullmatch(path):
                return template
        return None

    def problems(self, method: str, request: str) -> list[str]:
        """What GitHub's description has against the method and the path with its
        query, from the first segment after the host, such as repos/o/n/labels?page=2."""
        parts = urlsplit(request)
        template = self.route(f"/{parts.path}")
        if template is None:
            return [f"GitHub's description has no path /{parts.path}."]
        name = f"{method} {template}"
        operation = self.paths[template].get(method.lower())
        if operation is None:
            return [f"GitHub's description has no {name}."]
        found = [f"GitHub deprecates {name}."] if operation.get("deprecated") else []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            parameter = operation["query"].get(key)
            if parameter is None:
                found.append(f"{name} has no query parameter {key}.")
            elif parameter.get("deprecated"):
                found.append(f"GitHub deprecates the parameter {key} of {name}.")
            elif value not in parameter.get("enum", (value,)):
                found.append(f"The parameter {key} of {name} doesn't take {value}.")
            elif parameter.get("type") == "integer" and not value.isdecimal():
                found.append(f"The parameter {key} of {name} is a number, not {value}.")
        return found


@lru_cache(maxsize=1)
def pinned() -> Description:
    """The copy in tests/github/, or the file that REST_DESCRIPTION names: the weekly
    run checks the requests against the description of that day."""
    path = Path(os.environ.get("REST_DESCRIPTION") or COPIES / DESCRIPTION)
    return Description(json.loads(path.read_text(encoding="utf-8")))


def request_of(arguments: Sequence[str]) -> tuple[str, str] | None:
    """The method and the path with its query of the arguments of `gh api`; None for
    GraphQL, which tests/test_graphql_contract.py checks."""
    words = iter(arguments)
    method = None
    path = ""
    body = False
    fields: list[tuple[str, str]] = []
    for word in words:
        if word == "--method":
            method = next(words)
        elif word in {"-f", "-F"}:
            key, _, value = next(words).partition("=")
            fields.append((key, value))
        elif word == "--input":
            next(words)
            body = True
        elif word in {"--header", "-H", "--jq"}:
            next(words)
        elif not word.startswith("-"):
            path = word
    if path == "graphql":
        return None
    # As gh decides: fields or an input make a POST, unless the command names another.
    method = method or ("POST" if fields or body else "GET")
    if method == "GET" and fields:
        path += ("&" if "?" in path else "?") + urlencode(fields)
    return method, path


def fetch(url: str) -> bytes:
    """The file at the address; a moment of failure of the host is tried again."""
    tries = 1
    while True:
        try:
            request = Request(url, headers={"User-Agent": "issue-assistant"})
            with urlopen(request, timeout=60) as response:
                body: bytes = response.read()
                return body
        except OSError as error:
            if tries == TRIES:
                raise assistant.AssistantError(
                    f"GitHub gave no {url}: {error}"
                ) from error
            print(f"::notice::{url} failed: {error}; trying again in {PAUSE} s")
            time.sleep(PAUSE)
            tries += 1


def as_text(reduced: Mapping[str, Any]) -> str:
    """The file of a reduced description; the same text for the same description."""
    return json.dumps(reduced, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def download(folder: Path) -> None:
    """Write GitHub's schema and description of today into the folder."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / SCHEMA).write_bytes(fetch(SCHEMA_URL))
    full = json.loads(fetch(DESCRIPTION_URL))
    (folder / DESCRIPTION).write_bytes(as_text(reduce(full)).encode("utf-8"))


def open_pulls(github: assistant.GitHub) -> list[dict[str, Any]]:
    """The open pull requests from the branch of the bot, of this repository alone: a
    fork may have a branch of this name."""
    head = quote(f"{github.owner}:{BRANCH}", safe="")
    pulls: list[dict[str, Any]] = github.rest(
        f"pulls?state=open&head={head}&per_page=100"
    )
    return pulls


def close_refresh(github: assistant.GitHub) -> None:
    """Close a pull request that the default branch has overtaken, such as when a
    person merged the copies by hand, with its branch: its automatic merge is still on.
    """
    pulls = open_pulls(github)
    for pull in pulls:
        github.rest(f"pulls/{pull['number']}", method="PATCH", data={"state": "closed"})
    if pulls:
        github.rest(f"git/refs/heads/{BRANCH}", method="DELETE")


def refresh(
    github: assistant.GitHub, folder: Path, sign_off: str, run_url: str
) -> int | None:
    """The one pull request with the files of the folder, opened or brought up to
    date; None when the copies on the default branch already are the same, and then
    no such pull request stays open."""
    main = github.default_branch()
    head = github.rest(f"git/ref/heads/{main}")["object"]["sha"]
    base = github.rest(f"git/commits/{head}")["tree"]["sha"]
    entries = [
        {
            "path": f"tests/github/{name}",
            "mode": "100644",
            "type": "blob",
            "sha": github.rest(
                "git/blobs",
                method="POST",
                data={
                    "content": (folder / name).read_text(encoding="utf-8"),
                    "encoding": "utf-8",
                },
            )["sha"],
        }
        for name in (SCHEMA, DESCRIPTION)
    ]
    tree = github.rest(
        "git/trees", method="POST", data={"base_tree": base, "tree": entries}
    )["sha"]
    if tree == base:
        close_refresh(github)
        return None
    # The commit goes through GitHub's API: GitHub signs it, as the release app's, and
    # it starts the checks. It always sits on the newest commit, which main requires.
    try:
        branch = github.rest(f"git/ref/heads/{BRANCH}")["object"]["sha"]
    except assistant.GitHubError as error:
        if not error.missing():
            raise
        branch = None
    if branch is None or github.rest(f"git/commits/{branch}")["tree"]["sha"] != tree:
        commit = github.rest(
            "git/commits",
            method="POST",
            data={
                "message": f"{TITLE}\n\nSigned-off-by: {sign_off}\n",
                "tree": tree,
                "parents": [head],
            },
        )["sha"]
        if branch is None:
            github.rest(
                "git/refs",
                method="POST",
                data={"ref": f"refs/heads/{BRANCH}", "sha": commit},
            )
        else:
            github.rest(
                f"git/refs/heads/{BRANCH}",
                method="PATCH",
                data={"sha": commit, "force": True},
            )
    pulls = open_pulls(github)
    if pulls:
        number: int = pulls[0]["number"]
        return number
    opened: int = github.rest(
        "pulls",
        method="POST",
        data={
            "title": TITLE,
            "head": BRANCH,
            "base": main,
            "body": (
                "GitHub changed its GraphQL schema or its OpenAPI description, and "
                f"[the contract tests passed against them]({run_url}), so the copies "
                "in `tests/github/` follow. Nothing but the two copies changes, and "
                "the pull request merges itself once its checks pass."
            ),
        },
    )["number"]
    return opened


def refresh_command(folder: str) -> int:
    repository = os.environ["GH_REPO"]
    github = assistant.GitHub(repository)
    # The sign-off names the account of the app, the author of the commit.
    bot = f"{os.environ['APP_SLUG']}[bot]"
    user = github._gh(["api", f"users/{quote(bot, safe='')}"])["id"]
    number = refresh(
        github,
        Path(folder),
        f"{bot} <{user}+{bot}@users.noreply.github.com>",
        os.environ["RUN_URL"],
    )
    if number is None:
        write("GITHUB_STEP_SUMMARY", "The copies in tests/github/ are current.\n")
        return 0
    write("GITHUB_STEP_SUMMARY", f"The copies follow in the pull request #{number}.\n")
    write("GITHUB_OUTPUT", f"number={number}\n")
    return 0


def write(variable: str, text: str) -> None:
    """Append to the file that the variable of the runner names, if any."""
    path = os.environ.get(variable)
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def main() -> int:
    try:
        match sys.argv[1:]:
            case ["download", folder]:
                download(Path(folder))
                return 0
            case ["refresh", folder]:
                return refresh_command(folder)
    except (assistant.AssistantError, assistant.GitHubError) as error:
        print(f"::error::{error}")
        return 1
    print("Usage: github_schemas.py download FOLDER | refresh FOLDER", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
