"""No test names a value that Renovate moves.

Renovate keeps the commits of the actions, the images and the versions below a line
"# renovate:" current in the workflows, action.yml, the scripts and the Dockerfiles, on
a branch that only Renovate pushes to. A test that names such a value fails on that
branch, where nobody can fix it, and the update never merges by itself, as the update
of the Codex action did in #561 (#566). A test reads the value from its file instead,
or makes one up, such as SHA in test_engine_contract.py.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The files that Renovate's managers read, as config:recommended and the rules of
# .github/renovate-blueprint.json5 have them: the actions of the workflows of each
# folder .github, templates/ too, and of action.yml (github-actions); the images by
# their digest and the versions below a line "# renovate:" in the workflows and the
# scripts (the custom managers of the blueprint); and the images of the Dockerfiles
# (dockerfile).
READ = re.compile(
    r"(^|/)\.github/(workflows|actions)/.+\.ya?ml$"
    r"|(^|/)action\.ya?ml$"
    r"|(^|/)[^/]+\.sh$"
    r"|(^|/)([Dd]ocker|[Cc]ontainer)file[^/]*$"
)
# config:recommended leaves out the tests, examples and dependencies of others.
IGNORED = re.compile(
    r"(^|/)(node_modules|bower_components|vendor|examples|tests?|__tests__"
    r"|__fixtures__)/"
)
ACTION = re.compile(r"uses:\s*['\"]?(?P<name>[^\s'\"@]+)@(?P<commit>[0-9a-f]{40})\b")
# The image of a script or a workflow by its tag and digest, as the custom manager of
# .github/renovate-blueprint.json5 reads it, and the image of a Dockerfile.
IMAGE = re.compile(
    r"(?:^|[\s\"'=])(?P<name>[a-z0-9][a-z0-9._-]*(?:/[a-z0-9._-]+)+)"
    r":(?P<tag>v?\d[a-zA-Z0-9._-]*)@sha256:(?P<digest>[a-f0-9]{64})"
)
FROM = re.compile(
    r"^FROM\s+(?:--\S+\s+)*(?P<name>[^\s:@]+):(?P<tag>[^\s@]+)"
    r"@sha256:(?P<digest>[a-f0-9]{64})",
    re.M | re.I,
)
VERSION = re.compile(
    r"# renovate: datasource=\S+ depName=(?P<name>\S+)(?: extractVersion=\S+)?\n"
    r"\s*[\w-]+(?:: |=)\"?(?P<version>[^\"\s@]+)"
    r"(?:@sha256:(?P<digest>[a-f0-9]{64}))?"
)
# Renovate leaves the pins of the issue assistant alone: in templates/ they are
# placeholders that install replaces (the first rule of .github/renovate.json5).
ITSELF = "Dennis-Otto/issue-assistant"


def renovated(root, names):
    """Each value that Renovate moves in the files of `names` below `root`, with what
    it is and where it is."""
    values = {}
    for name in names:
        if not READ.search(name) or IGNORED.search(name):
            continue
        text = (root / name).read_text(encoding="utf-8")
        for match in ACTION.finditer(text):
            if not match["name"].startswith(ITSELF):
                what = f"the commit of {match['name']} in {name}"
                values[match["commit"]] = what
                values[match["commit"][:7]] = what
        for match in [*IMAGE.finditer(text), *FROM.finditer(text)]:
            what = f"the image {match['name']} in {name}"
            values[f"{match['name']}:{match['tag']}"] = what
            values[match["digest"]] = what
        for match in VERSION.finditer(text):
            what = f"the version of {match['name']} in {name}"
            values[match["version"]] = what
            if match["digest"]:
                values[match["digest"]] = what
    return values


def named(values, tests):
    """Each line of the tests that names a value of `values` as a whole token: not a
    part of a longer commit, version or name, and a version with or without its v. A
    file name such as codex-exec-0.162.1.help names the version of a recording, which
    nothing compares with the pin."""
    bare = {value.removeprefix("v"): value for value in values}
    # The longest first, so that a whole commit wins over its start.
    tokens = re.compile(
        r"(?<![\w.])v?(?P<value>"
        + "|".join(map(re.escape, sorted(bare, key=len, reverse=True)))
        + r")(?![\w.])"
    )
    found = []
    for path in tests:
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, 1):
            found += [
                f"{path.name}:{number} names {bare[match['value']]}, "
                f"{values[bare[match['value']]]}"
                for match in tokens.finditer(line)
            ]
    return found


def tracked():
    """The files of the repository, as Renovate sees them on its branch."""
    return subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split("\0")


def test_no_test_names_a_value_that_renovate_moves():
    """The tests in tests/*.py only; the recordings and goldens below tests/ quote
    the files as they were and are compared with nothing that Renovate moves."""
    values = renovated(ROOT, filter(None, tracked()))
    tests = sorted(
        path
        for path in (ROOT / "tests").glob("*.py")
        if path.name != Path(__file__).name
    )
    found = named(values, tests)
    assert found == [], (
        "A test names a value that Renovate moves, so Renovate's update fails on its "
        "own branch: read the value from its file, or make one up.\n" + "\n".join(found)
    )


def test_the_guard_sees_every_value_that_renovate_moves():
    """The values of today: an action of a workflow and one of templates/, an image of
    a script and one of a Dockerfile, and a version below "# renovate:"."""
    for name, kind in (
        (".github/workflows/lint.yml", "the commit of actions/checkout"),
        (".github/workflows/lint.yml", "the image rhysd/actionlint"),
        (
            "templates/variants/.github/workflows/issue-assistant.yml",
            "the commit of step-security/harden-runner",
        ),
        ("scripts/markdownlint.sh", "the image"),
        (".devcontainer/Dockerfile", "the image"),
        (
            ".github/workflows/reusable-issue-assistant.yml",
            "the version of @openai/codex",
        ),
    ):
        assert any(
            what.startswith(kind) and what.endswith(f" in {name}")
            for what in renovated(ROOT, [name]).values()
        ), (name, kind)
    whats = renovated(ROOT, filter(None, tracked())).values()
    assert not any(ITSELF in what for what in whats)


def test_the_guard_names_the_file_the_line_and_the_value(tmp_path):
    commit = "fedcba9876543210fedcba9876543210fedcba98"
    digest = "ab" * 32
    files = {
        ".github/workflows/ci.yml": (
            f"      - uses: o/r@{commit} # v1.2.3\n"
            f"      - uses: {ITSELF}@{'1' * 40}\n"
            "      # renovate: datasource=npm depName=tool\n"
            "      TOOL_VERSION: 7.8.9\n"
        ),
        "templates/.github/workflows/a.yml": f"    - uses: t/a@{'c' * 40}\n",
        "scripts/run.sh": (
            f'IMAGE="o/img:2.0@sha256:{digest}"\n'
            "# renovate: datasource=docker depName=o/tool\n"
            f"TOOL=o/tool:4.5@sha256:{'d' * 64}\n"
        ),
        "app/Dockerfile": f"FROM --platform=linux/amd64 py:3.14@sha256:{'e' * 64}\n",
        "tests/fixtures/.github/workflows/old.yml": f"- uses: x/y@{'f' * 40}\n",
        "docs/page.md": f"uses: d/d@{'0' * 40}\n",
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    values = renovated(tmp_path, files)
    assert values == {
        commit: "the commit of o/r in .github/workflows/ci.yml",
        commit[:7]: "the commit of o/r in .github/workflows/ci.yml",
        "7.8.9": "the version of tool in .github/workflows/ci.yml",
        "c" * 40: "the commit of t/a in templates/.github/workflows/a.yml",
        "c" * 7: "the commit of t/a in templates/.github/workflows/a.yml",
        "o/img:2.0": "the image o/img in scripts/run.sh",
        digest: "the image o/img in scripts/run.sh",
        "o/tool:4.5": "the version of o/tool in scripts/run.sh",
        "d" * 64: "the version of o/tool in scripts/run.sh",
        "py:3.14": "the image py in app/Dockerfile",
        "e" * 64: "the image py in app/Dockerfile",
    }
    test = tmp_path / "test_x.py"
    test.write_text(
        f'PIN = "{commit[:7]}"\n'
        f'LONGER = "{commit[:7]}0"\n'
        'VERSION = "v7.8.9"\n'
        'NEWER = "7.8.9.1"\n'
        'RECORDING = "tool-7.8.9.help"\n'
        'IMAGE = "docker run o/img:2.0 lint"\n'
        f'DIGEST = "sha256:{digest}"\n',
        encoding="utf-8",
    )
    assert named(values, [test]) == [
        f"test_x.py:1 names {commit[:7]}, the commit of o/r in .github/workflows/ci.yml",
        "test_x.py:3 names 7.8.9, the version of tool in .github/workflows/ci.yml",
        "test_x.py:6 names o/img:2.0, the image o/img in scripts/run.sh",
        f"test_x.py:7 names {digest}, the image o/img in scripts/run.sh",
    ]
