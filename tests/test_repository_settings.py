"""The settings as code of this repository: .github/repository.project.toml on top of
.github/repository.toml, which the blueprint keeps current. The settings bot makes
GitHub match them after every push to main.

With allowed_actions = "selected", GitHub runs only its own actions and those that
[actions.selected] lists, so a workflow that runs another action would stop. While one
does, the bot keeps the gate open and fails on main, where only bot-health notices it.
These tests find such an action in the pull request that adds it instead: every action
and reusable workflow that a workflow, an action of .github/actions or action.yml of
this repository names. What those run in turn only the bot can read, from GitHub.
"""

import fnmatch
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROJECT_SETTINGS = ".github/repository.project.toml"
# A step or a job that names an action or a reusable workflow, as blueprint.py reads it.
USES = re.compile(r"""^[ \t]*(?:-[ \t]+)?uses:[ \t]*["']?([\w.:/@+-]+)""", re.MULTILINE)
# The owners of the actions that GitHub makes, which github_owned_allowed allows.
GITHUB_OWNERS = ("actions", "github")


def merged(base, extra):
    """The settings of the project on top of those of the blueprint, as the header of
    repository.project.toml says: tables merge, lists gain their new items, and values
    replace those of repository.toml."""
    result = dict(base)
    for key, value in extra.items():
        have = result.get(key)
        if isinstance(value, dict) and isinstance(have, dict):
            result[key] = merged(have, value)
        elif isinstance(value, list) and isinstance(have, list):
            result[key] = have + [item for item in value if item not in have]
        else:
            result[key] = value
    return result


def actions_settings():
    def load(path):
        return tomllib.loads((ROOT / path).read_text(encoding="utf-8"))

    settings = merged(load(".github/repository.toml"), load(PROJECT_SETTINGS))
    return settings.get("actions", {})


def named_actions():
    """Every action and reusable workflow that the repository names, with the first
    file that names it: not its own, which every policy allows, nor an image of
    Docker, which no pattern names."""
    files = sorted(
        [
            *(ROOT / ".github/workflows").glob("*.y*ml"),
            *(ROOT / ".github/actions").rglob("action.y*ml"),
            *ROOT.glob("action.y*ml"),
        ]
    )
    found = {}
    for path in files:
        for reference in USES.findall(path.read_text(encoding="utf-8")):
            if not reference.startswith(("./", "docker://")):
                found.setdefault(reference, path.relative_to(ROOT).as_posix())
    return found


def matches(pattern, reference):
    # In a pattern of GitHub, * stands for any text, slashes too, and the case of
    # owners and repositories doesn't count.
    return fnmatch.fnmatchcase(reference.casefold(), pattern.casefold())


def allows(actions, reference):
    """Whether GitHub lets a workflow run the action or reusable workflow: a pattern
    with ! in front blocks what it names, also an action of GitHub."""
    policy = actions.get("allowed_actions", "all")
    if policy == "all":
        return True
    if policy != "selected":
        # local_only: the actions of the repository alone.
        return False
    selected = actions.get("selected", {})
    patterns = [str(pattern) for pattern in selected.get("patterns_allowed", [])]
    if any(
        matches(pattern[1:], reference) for pattern in patterns if pattern[:1] == "!"
    ):
        return False
    owner = reference.casefold().partition("/")[0]
    if selected.get("github_owned_allowed") and owner in GITHUB_OWNERS:
        return True
    return any(matches(pattern, reference) for pattern in patterns)


def test_the_settings_allow_every_action_that_a_workflow_runs():
    actions = actions_settings()
    lacking = {}
    for reference, where in sorted(named_actions().items()):
        if not allows(actions, reference):
            lacking.setdefault(reference.partition("@")[0] + "@*", where)
    assert not lacking, "\n".join(
        f"{where} runs {pattern.removesuffix('@*')}, which [actions.selected] lacks:"
        f" add {pattern!r} to patterns_allowed of [actions.selected] in"
        f" {PROJECT_SETTINGS}"
        for pattern, where in lacking.items()
    )


def test_the_actions_of_both_engines_are_found():
    # The check above means something only while the reader finds what the
    # workflows run, the actions of the engines among them.
    names = {reference.partition("@")[0] for reference in named_actions()}
    assert {"anthropics/claude-code-action", "openai/codex-action"} <= names


def test_the_project_lists_only_actions_that_its_workflows_run():
    # A pattern that nothing runs any more lets that action run in a pull request.
    project = tomllib.loads((ROOT / PROJECT_SETTINGS).read_text(encoding="utf-8"))
    patterns = (
        project.get("actions", {}).get("selected", {}).get("patterns_allowed", [])
    )
    named = list(named_actions())
    unused = [
        pattern
        for pattern in patterns
        if not any(matches(pattern, reference) for reference in named)
    ]
    assert not unused, (
        f"{PROJECT_SETTINGS} allows actions that no workflow runs: {unused}"
    )


SELECTED = {
    "allowed_actions": "selected",
    "selected": {
        "github_owned_allowed": True,
        "patterns_allowed": ["openai/codex-action@*", "!actions/cache@*"],
    },
}


@pytest.mark.parametrize(
    ("actions", "reference", "allowed"),
    [
        (SELECTED, "actions/checkout@0123abc", True),
        (SELECTED, "Openai/Codex-Action@0123abc", True),
        (SELECTED, "anthropics/claude-code-action@0123abc", False),
        # A pattern with ! blocks an action of GitHub as well.
        (SELECTED, "actions/cache@0123abc", False),
        (
            {**SELECTED, "selected": {"patterns_allowed": []}},
            "actions/checkout@0123abc",
            False,
        ),
        (
            {"allowed_actions": "selected", "selected": {"patterns_allowed": ["o/*"]}},
            "o/action/.github/workflows/reusable.yml@v1",
            True,
        ),
        ({"allowed_actions": "all"}, "anyone/action@v1", True),
        ({"allowed_actions": "local_only"}, "actions/checkout@v4", False),
    ],
)
def test_the_policy_is_read_as_github_reads_it(actions, reference, allowed):
    assert allows(actions, reference) is allowed


def test_the_project_s_lists_gain_their_new_items():
    base = {"selected": {"patterns_allowed": ["a@*", "b@*"], "verified_allowed": False}}
    extra = {"selected": {"patterns_allowed": ["b@*", "c@*"], "verified_allowed": True}}
    assert merged(base, extra) == {
        "selected": {
            "patterns_allowed": ["a@*", "b@*", "c@*"],
            "verified_allowed": True,
        }
    }
