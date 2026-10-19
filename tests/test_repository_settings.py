"""The settings as code of this repository: .github/repository.project.toml on top of
.github/repository.toml, which the blueprint keeps current. The settings bot makes
GitHub match them after every push to main.

With allowed_actions = "selected", GitHub runs only its own actions and those that
[actions.selected] lists, so a workflow that runs another action would stop. While one
does, the bot keeps the gate open and fails on main, where only bot-health notices it.
These tests find such an action in the pull request that adds it instead, with the
bot's own code: every action and reusable workflow that a workflow, an action of
.github/actions or action.yml of this repository names. What those run in turn only
the bot can read, from GitHub; such an action goes into RUN_IN_TURN below as well.
"""

import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROJECT_SETTINGS = ".github/repository.project.toml"
# The patterns of the project for actions that no workflow names but that an action
# of a workflow runs in turn, with that action, as the bot's message asks for them.
# Today codex-action runs only GitHub's own actions, which github_owned_allowed allows.
RUN_IN_TURN: dict[str, str] = {}


@pytest.fixture
def bot(monkeypatch):
    """The settings module of the bot, which reads the settings as it does on main."""
    monkeypatch.syspath_prepend(str(ROOT / ".github"))
    from blueprint import settings

    return settings


@pytest.fixture
def settings(bot):
    return bot.Settings.load(ROOT / ".github" / "repository.toml")


def test_the_settings_allow_every_action_that_a_workflow_runs(bot, settings):
    selected = settings.actions.get("selected", {})
    lacking = {
        bot.pattern_of(reference): where
        for reference, where in sorted(settings.workflows.items())
        if not bot.allows(selected, reference)
    }
    assert not lacking, "\n".join(
        f"{where} runs {pattern.removesuffix('@*')}, which [actions.selected] lacks:"
        f" add {pattern!r} to patterns_allowed of [actions.selected] in"
        f" {PROJECT_SETTINGS}"
        for pattern, where in lacking.items()
    )


def test_the_actions_of_both_engines_are_found(settings):
    # The check above means something only while the reader finds what the
    # workflows run, the actions of the engines among them.
    names = {reference.partition("@")[0] for reference in settings.workflows}
    assert {"anthropics/claude-code-action", "openai/codex-action"} <= names


def test_the_project_lists_only_actions_that_its_workflows_run(bot, settings):
    # A pattern that nothing runs any more lets that action run in a pull request.
    project = tomllib.loads((ROOT / PROJECT_SETTINGS).read_text(encoding="utf-8"))
    patterns = (
        project.get("actions", {}).get("selected", {}).get("patterns_allowed", [])
    )
    unused = [
        pattern
        for pattern in patterns
        if pattern not in RUN_IN_TURN
        and not any(bot.matches(pattern, reference) for reference in settings.workflows)
    ]
    assert not unused, (
        f"{PROJECT_SETTINGS} allows actions that no workflow runs: {unused}. Remove"
        " them, or, for an action that another action runs in turn, add its pattern"
        " with that action to RUN_IN_TURN in tests/test_repository_settings.py."
    )


def test_every_action_run_in_turn_is_listed():
    project = tomllib.loads((ROOT / PROJECT_SETTINGS).read_text(encoding="utf-8"))
    patterns = (
        project.get("actions", {}).get("selected", {}).get("patterns_allowed", [])
    )
    assert set(RUN_IN_TURN) <= set(patterns)
