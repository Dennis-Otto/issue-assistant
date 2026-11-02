"""The engines that write the analysis, each with its command line and its limits."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .constants import EFFORTS
from .errors import AssistantError
from .paths import SETTINGS

# How an engine gets the JSON schema of its answer: it enforces the schema as it is;
# it enforces a strict form, in which every field is required and one that the
# answer may leave out may be null, as some APIs of structured outputs want; or the
# task asks for it, in a file of the context folder. apply checks every answer
# against the schema itself, whichever way the engine got it.
ENFORCED, STRICT, IN_PROMPT = "enforced", "strict", "in the prompt"
# The limits of [engine] that an engine can stop at on its own; at the others, only
# the time of the job stops it.
STOPS = ("max_turns", "max_budget_usd")


@dataclass(frozen=True)
class Cli:
    """The command line of an engine that `analyze` runs, for an engine without an
    action that keeps it to reading.

    `analyze` installs `package` at the version that the workflow pins, without the
    scripts of its packages, and runs `command` in a home of its own with the
    secret of the engine alone, no token of GitHub and no other variable of the job:
    the prompt after `prompt`, the model after `--model`, the effort after `effort`
    when the task has one, and `arguments`, which leave it only tools that read
    files of the checkout.
    """

    package: str
    command: str
    arguments: tuple[str, ...]
    prompt: str = "--prompt"
    effort: str = ""
    # Settings that come before those of the user and of the repository, in a file
    # that the variable `settings_variable` names, with the turns of the task at the
    # dotted key `turns`.
    settings: dict[str, Any] = field(default_factory=dict)
    settings_variable: str = ""
    turns: str = ""
    # Whether it takes that file only when root owns it and every folder around it,
    # and nobody else may write to them; `analyze` then writes it with sudo.
    root_settings: bool = False
    # Further variables, such as those that turn off telemetry and updates.
    variables: dict[str, str] = field(default_factory=dict)
    # The field of the JSON that it prints that holds the answer, next to a field
    # error with a message.
    answer: str = ""
    # Or the type of the JSON events, one on each line that it prints, whose
    # data.content holds the answer: the last of the main agent, with the other
    # parts of its call of the model; an event of the type `error_event` holds a
    # message in data.
    answer_event: str = ""
    error_event: str = ""
    # The status with which it stops at its turns; None for one that has none.
    at_turns: int | None = None


@dataclass(frozen=True)
class Engine:
    """An AI engine that can write the analysis, named in its comments."""

    name: str
    vendor: str
    model: str
    # The names of the secrets in the environment issue-assistant that the engine's
    # step reads, the one that `install --setup` asks for first. install and check
    # handle only names; `analyze` passes the value of the engine that it runs to its
    # command line, and no other.
    credentials: tuple[str, ...]
    schema: str = ENFORCED
    # The levels of effort that it takes; none for an engine without the setting.
    efforts: tuple[str, ...] = EFFORTS
    # Which of STOPS it stops at.
    stops_at: tuple[str, ...] = STOPS
    # The arguments that its action adds to the command line, as the context step
    # writes them for it, with "{rules}" for the rules as a string of TOML; none for
    # an engine that takes the rules otherwise.
    action_arguments: tuple[str, ...] = ()
    cli: Cli | None = None
    # For an engine with an action and a schema that it enforces, its command line
    # as `evaluate` runs it on your computer, with the arguments that keep it to
    # reading, as its action has them in the workflow; see answer_with_schema.
    local: tuple[str, ...] = ()


# The engines the workflow template has a step for; .github/issue-assistant/config.toml
# of a repository chooses one. docs/engines.md describes how to add one.
ENGINES = {
    "claude": Engine(
        "Claude",
        "Anthropic",
        "claude-opus-5-5",
        ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
        # As in the step of the workflow: --restricted removes every tool that runs
        # code or reaches the network and confines the file tools to the checkout,
        # and --tools leaves only those that read.
        local=(
            "claude",
            "-p",
            "--restricted",
            "--tools",
            "Read,Grep,Glob",
            "--permission-prompts",
            "none",
            "--output-format",
            "json",
        ),
    ),
    # The Codex action runs Codex in a sandbox that reads files but neither writes
    # them nor reaches the network, and enforces the strict schema. Its arguments
    # give it the rules as developer instructions, which come before the task as a
    # system prompt does, and turn off web search, which would read the web and
    # send what an issue asks for through OpenAI; Codex reads a value of --config
    # as TOML. The action prints what Codex prints into the public log, where the
    # runner reads a line that starts with :: as a workflow command; --json makes
    # Codex print JSON events alone, one per line, which no text of an issue, of a
    # file or of the AI can start.
    "codex": Engine(
        "Codex",
        "OpenAI",
        "gpt-6.1-sol",
        ("OPENAI_API_KEY",),
        schema=STRICT,
        stops_at=(),
        action_arguments=(
            "--json",
            "--config",
            'web_search="disabled"',
            "--config",
            "developer_instructions={rules}",
        ),
    ),
    # The Gemini CLI and the Copilot CLI take no system prompt, so the prompt has
    # the rules first, and "auto" lets each choose its model.
    "gemini": Engine(
        "Gemini",
        "Google",
        "auto",
        ("GEMINI_API_KEY",),
        schema=IN_PROMPT,
        efforts=(),
        stops_at=("max_turns",),
        cli=Cli(
            package="@google/gemini-cli",
            command="gemini",
            # Nobody can answer its question whether it trusts the workspace, which
            # would stop it; its system settings keep it to reading all the same.
            arguments=("--skip-trust", "--output-format", "json"),
            # The tools that read files of its workspace, the checkout, and no
            # other: no shell, no writing or editing files, no web, agents, MCP
            # servers, extensions, skills, hooks or tools that a command finds. The
            # context folder is in .gitignore, which its tools would follow.
            settings={
                "tools": {
                    "core": ["read_file", "list_directory", "glob", "grep_search"],
                    "exclude": [
                        "run_shell_command",
                        "write_file",
                        "replace",
                        "web_fetch",
                        "google_web_search",
                    ],
                    "discoveryCommand": "",
                    "callCommand": "",
                },
                "admin": {
                    "extensions": {"enabled": False},
                    "mcp": {"enabled": False},
                    "skills": {"enabled": False},
                },
                "experimental": {"enableAgents": False},
                "skills": {"enabled": False},
                "hooksConfig": {"enabled": False},
                "general": {"checkpointing": {"enabled": False}},
                "privacy": {"usageStatisticsEnabled": False},
                "telemetry": {"enabled": False},
                "context": {"fileFiltering": {"respectGitIgnore": False}},
            },
            settings_variable="GEMINI_CLI_SYSTEM_SETTINGS_PATH",
            # It skips system settings that another user than root could change,
            # with no more than a warning, and would run without them.
            root_settings=True,
            turns="model.maxSessionTurns",
            variables={"GEMINI_TELEMETRY_ENABLED": "false"},
            answer="response",
            at_turns=53,
        ),
    ),
    "copilot": Engine(
        "Copilot",
        "GitHub",
        "auto",
        ("COPILOT_GITHUB_TOKEN",),
        schema=IN_PROMPT,
        stops_at=(),
        cli=Cli(
            package="@github/copilot",
            command="copilot",
            # The tools that read files, the only ones the model gets, without the
            # built-in MCP server of GitHub, the temporary folder, questions,
            # updates and the sync of the session to the history of the account;
            # remote control takes a command or a flag of its own to start, and
            # since 1.0.103 no flag turns it off. Its output is JSON events, which
            # hold the model's text as it is; its text for people renders Markdown,
            # which would drop the backslashes of the JSON of the answer.
            arguments=(
                "--available-tools",
                "view,glob,grep",
                "--allow-tool",
                "read",
                "--disable-builtin-mcps",
                "--disallow-temp-dir",
                "--no-ask-user",
                "--no-auto-update",
                "--sync-session=off",
                "--no-color",
                "--stream",
                "off",
                "--output-format",
                "json",
            ),
            # The name of the flag in its help; --effort is a hidden alias of it.
            effort="--reasoning-effort",
            answer_event="assistant.message",
            error_event="session.error",
            variables={"COPILOT_AUTO_UPDATE": "false"},
        ),
    ),
}
DEFAULT_ENGINE = "claude"
# The workflow passes the model to the engine's command line without quotes. The
# names and IDs of models fit, also with a version after @ or :, a path or a suffix
# in brackets; a blank, a quote or a line break would add arguments, and so would a
# leading dash, which the engine's action reads as the next flag.
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@/\[\]-]*")


def is_model_name(model: object) -> bool:
    return isinstance(model, str) and bool(MODEL_NAME.fullmatch(model))


def model_problem(model: object, key: str = "model") -> str:
    return (
        f"{SETTINGS / 'config.toml'}: the {key} {model!r} is no model name, "
        f"such as {ENGINES[DEFAULT_ENGINE].model}"
    )


@dataclass(frozen=True)
class Run:
    """What one run of the engine for a task may use, from [engine] in config.toml.

    The workflow passes each value to the engine's command line without quotes, so
    each has been checked: a model name, a whole number of turns, a number of
    dollars and a level of effort, or empty for the engine's own.
    """

    model: str
    turns: int
    budget: str
    effort: str


def engine_named(name: str | None) -> str:
    """The key of a known engine; the default for an empty name."""
    key = name or DEFAULT_ENGINE
    if key not in ENGINES:
        raise AssistantError(
            f"Unknown engine {key}; known engines: {', '.join(sorted(ENGINES))}."
        )
    return key
