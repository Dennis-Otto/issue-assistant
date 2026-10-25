"""The names that keep each engine to reading, against the versions that are pinned.

The read-only boundary of each engine rests on names that its vendor owns: the inputs
of openai/codex-action that choose its sandbox, the keys of the settings that leave
the Gemini CLI only its tools that read, the flags that do so for the Copilot CLI, and
the outputs that hold the answer. GitHub only warns about an input that an action
lacks, the Gemini CLI ignores a key of its settings that it doesn't know, and so does
Codex with a key of its configuration: a release that renames one keeps the step
running, without its boundary. The tests check only our side of these names, and no
check of a pull request runs an engine. So the job "engine contracts" of
.github/workflows/action-test.yml runs this script without a secret, on a pull request
that changes the step of an engine or what this script reads, and every week:

    PR=123 GITHUB_REPOSITORY=owner/name python3 scripts/engine_contract.py changes
    python3 scripts/engine_contract.py actions
    python3 scripts/engine_contract.py clis

`changes` tells the job whether the pull request changes one of those files. `actions`
reads the action.yml of each engine's action at the commit that a workflow pins and
fails for an input that a step passes and the action lacks or deprecates, for one that
the action requires and the step leaves out, and for an output that a job reads and the
action lacks. `clis` installs each engine's command line at its pinned version as
`analyze` does, without the scripts of its packages, and fails for a flag that its
--help doesn't list or a value that the flag doesn't take; for a key of its settings
that the JSON schema of the settings of that version lacks, or for a missing schema;
and for a run without a key, in a home of its own as `analyze` runs it, that fails at
anything but the key, or that complains about its settings. Codex checks its key only
at OpenAI: it passes when it starts its turn with the arguments of its step and the
configuration that its action gives it, which --strict-config checks.

GH_TOKEN reads the files on GitHub; no command line of an engine sees it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import issue_assistant as assistant  # noqa: E402

WORKFLOWS = ROOT / assistant.WORKFLOWS
# The step that runs the issue assistant of its workflow's commit, such as the step
# of the Gemini CLI and the Copilot CLI, whose inputs are the action's own.
TOOL = f"./{assistant.CONTEXT}/action"
# Besides the workflows with the step of an engine, the files whose change the check
# runs for: what keeps the engines to reading, and the check itself.
WATCHED = (
    "issue_assistant.py",
    "action.yml",
    "scripts/engine_contract.py",
    ".github/workflows/action-test.yml",
    ".github/renovate.json5",
)
# An output of a step in an expression: steps.<id>.outputs.<name>, or with the name
# in brackets, as one with a dash may be written.
OUTPUT = r"steps\.{}\.outputs(?:\.([A-Za-z0-9_-]+)|\['([^']+)'\])"
# The start of an option in the help of a command line: its short and its long name,
# indented by at most eight blanks; the lines of its description are indented more.
OPTION = re.compile(r"^ {1,8}(-[A-Za-z0-9?])?(?:, )?(--[A-Za-z0-9][A-Za-z0-9-]*)?")
DESCRIPTION = " " * 9
# The values that an option takes, as clap ([possible values: a, b]) and yargs
# ([choices: "a", "b"]) list them.
CHOICES = re.compile(r"\[(?:possible values|choices): ([^\]]*)\]")
# The JSON schema of the settings of a command line with settings, in the repository
# on GitHub that its package names, at the tag of its version.
SETTINGS_SCHEMAS = {
    "@google/gemini-cli": ("v{version}", "schemas/settings.schema.json")
}
# The prompt of a run without a key, which no engine answers.
PROMPT = "Answer with the word ok."
TURNS = 3
# The most seconds that a command line takes for its help or for a run without a key.
SECONDS = 120


@dataclass(frozen=True)
class ActionCli:
    """The command line that an engine's action installs at the version of one of its
    inputs, and runs with a subcommand. `configured` names each input of the action
    that it hands to the command line as a key of its configuration, as the
    description of the input says: openai/codex-action selects the permission
    profile through `default_permissions`. `started` is the type of the JSON event
    with which the command line starts its turn, once it took its arguments and its
    configuration."""

    package: str
    command: str
    version_input: str
    subcommand: tuple[str, ...]
    configured: dict[str, str]
    started: str
    # The variable that names its home, and the further variables of its step, such
    # as RUST_LOG, which the workflow sets for Codex.
    home: str
    variables: dict[str, str]
    # The flags of the check alone: one that fails on a key of the configuration that
    # it doesn't know, and one that lets it run outside a Git repository.
    checking: tuple[str, ...]


ACTION_CLIS = {
    "openai/codex-action": ActionCli(
        package="@openai/codex",
        command="codex",
        version_input="codex-version",
        subcommand=("exec",),
        configured={"permission-profile": "default_permissions"},
        started="turn.started",
        home="CODEX_HOME",
        variables={"RUST_LOG": "off"},
        checking=("--strict-config", "--skip-git-repo-check"),
    )
}


@dataclass(frozen=True)
class EngineStep:
    """A step that runs an engine, whose id is the key of the engine, with the
    outputs of the step that its job reads."""

    workflow: str
    job: str
    step: dict[str, Any]
    outputs: frozenset[str]

    @property
    def key(self) -> str:
        return str(self.step["id"])

    @property
    def uses(self) -> str:
        return str(self.step.get("uses", ""))

    @property
    def inputs(self) -> dict[str, Any]:
        return dict(self.step.get("with") or {})

    @property
    def where(self) -> str:
        return f"{self.workflow}, job {self.job}, step {self.key}"


@dataclass(frozen=True)
class Pin:
    """A version of an engine's command line that a workflow pins."""

    key: str
    package: str
    version: str
    # The action that installs it, or empty for one that `analyze` runs.
    action: str = ""

    @property
    def name(self) -> str:
        return f"{self.package}@{self.version}"


def engine_steps(folder: Path = WORKFLOWS) -> list[EngineStep]:
    """The steps of the engines in the workflows of the folder."""
    found = []
    for path in sorted(folder.glob("*.yml")):
        workflow = assistant.load_yaml(path) or {}
        for job_name, job in (workflow.get("jobs") or {}).items():
            text = json.dumps(job)
            for step in job.get("steps", []):
                key = step.get("id")
                if key not in assistant.ENGINES:
                    continue
                reads = re.findall(OUTPUT.format(re.escape(key)), text)
                found.append(
                    EngineStep(
                        str(assistant.WORKFLOWS / path.name),
                        job_name,
                        step,
                        frozenset(dotted or bracketed for dotted, bracketed in reads),
                    )
                )
    return found


def missing_steps(steps: Iterable[EngineStep]) -> list[str]:
    """An engine without a step would leave its names unchecked."""
    keys = {step.key for step in steps}
    return [
        f"No workflow in {assistant.WORKFLOWS} has a step with the id {key}, the "
        f"engine {assistant.ENGINES[key].name}, so its names go unchecked."
        for key in assistant.ENGINES
        if key not in keys
    ]


def run_gh(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["gh", *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def github_file(repository: str, path: str, ref: str) -> str | None:
    """A file of a repository on GitHub at a commit or a tag; None when it has none."""
    result = run_gh(
        [
            "api",
            "-H",
            "Accept: application/vnd.github.raw",
            f"repos/{repository}/contents/{path}?ref={ref}",
        ]
    )
    if not result.returncode:
        return result.stdout
    if "HTTP 404" in result.stderr:
        return None
    raise assistant.AssistantError(
        f"GitHub gave no {path} of {repository} at {ref}: "
        f"{assistant.one_line(result.stderr)[-300:] or 'no message'}"
    )


def action_file(uses: str) -> Any:
    """The action.yml of the action that `uses` names, at its commit."""
    name, _, ref = uses.partition("@")
    owner, repository, *folder = name.split("/")
    for file in ("action.yml", "action.yaml"):
        path = "/".join([*folder, file])
        text = github_file(f"{owner}/{repository}", path, ref)
        if text is not None:
            return assistant.yaml_of(text)
    return None


def pinned(uses: str) -> str:
    """An action as `uses` names it, with the start of its commit."""
    name, _, ref = uses.partition("@")
    return f"{name} at {ref[:7]}"


def action_problems(step: EngineStep, action: Any) -> list[str]:
    """What the step passes to its action and reads of it that the action lacks."""
    name = pinned(step.uses)
    if not isinstance(action, dict):
        return [f"{step.where}: {name} has no action.yml that can be read"]
    inputs = action.get("inputs") or {}
    outputs = action.get("outputs") or {}
    passed = step.inputs
    problems = []
    for key in passed:
        spec = inputs.get(key)
        if key not in inputs:
            problems.append(
                f"{step.where}: {name} has no input {key}; GitHub only warns about "
                "it, and the step runs without it"
            )
        elif isinstance(spec, dict) and spec.get("deprecationMessage"):
            problems.append(
                f"{step.where}: {name} deprecates the input {key}: "
                f"{assistant.one_line(str(spec['deprecationMessage']))}"
            )
    for key, spec in inputs.items():
        if (
            isinstance(spec, dict)
            and spec.get("required") is True
            and "default" not in spec
            and key not in passed
        ):
            problems.append(
                f"{step.where}: {name} requires the input {key}, which the step "
                "doesn't pass"
            )
    for key in sorted(step.outputs):
        if key not in outputs:
            problems.append(
                f"{step.where}: {name} has no output {key}, which the job reads; "
                "the answer would be empty"
            )
    cli = ACTION_CLIS.get(step.uses.partition("@")[0])
    for key, setting in cli.configured.items() if cli else ():
        spec = inputs.get(key)
        described = spec.get("description", "") if isinstance(spec, dict) else ""
        if key in passed and key in inputs and setting not in str(described):
            problems.append(
                f"{step.where}: {name} no longer says that it hands the input {key} "
                f"to its command line as {setting}; find out how it does, and "
                "change ACTION_CLIS in scripts/engine_contract.py"
            )
    return problems


def actions_command() -> int:
    steps = engine_steps()
    problems = missing_steps(steps)
    lines = []
    files: dict[str, Any] = {}
    for step in steps:
        if step.uses.startswith("./"):
            continue
        if step.uses not in files:
            files[step.uses] = action_file(step.uses)
        found = action_problems(step, files[step.uses])
        problems += found
        lines.append(
            f"{step.where}: {len(step.inputs)} input(s) and {len(step.outputs)} "
            f"output(s) of {pinned(step.uses)}"
            + (f", {len(found)} problem(s)" if found else "")
        )
    return report("The inputs and outputs of the engines' actions", lines, problems)


def cli_pins(steps: Iterable[EngineStep]) -> list[Pin]:
    """The versions of the engines' command lines that the steps pin: the version of
    an engine that `analyze` runs, and the one that an engine's action installs."""
    pins = set()
    for step in steps:
        cli = assistant.ENGINES[step.key].cli
        action = ACTION_CLIS.get(step.uses.partition("@")[0])
        if cli and step.uses == TOOL:
            version = step.inputs.get("engine-version")
            pins.add(Pin(step.key, cli.package, str(version)))
        elif action:
            version = step.inputs.get(action.version_input)
            pins.add(
                Pin(step.key, action.package, str(version), step.uses.partition("@")[0])
            )
    return sorted(pins, key=lambda pin: (pin.key, pin.version))


def passed_flags(arguments: Sequence[str]) -> dict[str, set[str]]:
    """The flags of a command line, each with the values that follow it."""
    flags: dict[str, set[str]] = {}
    flag = None
    for argument in arguments:
        if argument.startswith("-"):
            flag, _, value = argument.partition("=")
            flags.setdefault(flag, set())
            if value:
                flags[flag].add(value)
                flag = None
        elif flag is not None:
            flags[flag].add(argument)
            flag = None
    return flags


def help_options(text: str) -> dict[str, frozenset[str] | None]:
    """The options that the help of a command line lists, each with the values that
    it takes, or None for any value."""
    entries: list[tuple[list[str], list[str]]] = []
    for line in text.splitlines():
        match = OPTION.match(line)
        names = [name for name in match.groups() if name] if match else []
        if names:
            entries.append((names, [line]))
        elif entries and (not line.strip() or line.startswith(DESCRIPTION)):
            entries[-1][1].append(line)
        else:
            entries.append(([], []))
    options = {}
    for names, lines in entries:
        found = CHOICES.search("\n".join(lines))
        values = (
            frozenset(value.strip().strip('"') for value in found[1].split(","))
            if found
            else None
        )
        for name in names:
            options[name] = values
    return options


def flag_problems(
    name: str, flags: dict[str, set[str]], options: dict[str, frozenset[str] | None]
) -> list[str]:
    """The flags that the help doesn't list, and the values that a flag doesn't take."""
    problems = []
    for flag, values in sorted(flags.items()):
        if flag not in options:
            problems.append(
                f"{name}: its --help lists no {flag}; a flag that it doesn't list "
                "may go without notice in its next release"
            )
            continue
        takes = options[flag]
        wrong = sorted(values - takes) if takes is not None else []
        if wrong:
            problems.append(
                f"{name} takes no {', '.join(wrong)} for {flag}, only "
                f"{', '.join(sorted(takes or ()))}"
            )
    return problems


def resolved(schema: Any, root: dict[str, Any]) -> Any:
    """The schema that a $ref of the same document points to, with the keys beside
    the $ref; a $ref to another document allows anything."""
    for _ in range(10):
        if not isinstance(schema, dict) or "$ref" not in schema:
            return schema
        ref = str(schema["$ref"])
        if not ref.startswith("#/"):
            return True
        target: Any = root
        for part in ref[2:].split("/"):
            target = target.get(part) if isinstance(target, dict) else None
        rest = {key: value for key, value in schema.items() if key != "$ref"}
        schema = {**target, **rest} if isinstance(target, dict) else rest
    return schema


def fits(value: Any, kind: str) -> bool:
    if kind in ("number", "integer"):
        return (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and (kind == "number" or float(value).is_integer())
        )
    types: dict[str, type | tuple[type, ...]] = {
        "object": dict,
        "array": list,
        "string": str,
        "boolean": bool,
        "null": type(None),
    }
    return isinstance(value, types.get(kind, object))


def settings_problems(
    value: Any, schema: Any, root: dict[str, Any], path: str = ""
) -> list[str]:
    """The keys of the settings that the JSON schema lacks, and the values that don't
    fit it, by their dotted path. A key that an object of the schema doesn't name is
    unknown unless the object takes other keys: the command line would ignore it."""
    schema = resolved(schema, root)
    if not isinstance(schema, dict):
        return []
    for combined in ("anyOf", "oneOf"):
        if combined in schema:
            branches = [
                settings_problems(value, branch, root, path)
                for branch in schema[combined]
            ]
            return [] if any(not found for found in branches) else branches[0]
    kinds = schema.get("type")
    if kinds is not None:
        kinds = [kinds] if isinstance(kinds, str) else list(kinds)
        if not any(fits(value, kind) for kind in kinds):
            return [f"{path} is no {' or '.join(kinds)}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{path} is none of {', '.join(map(json.dumps, schema['enum']))}"]
    problems = []
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        others = schema.get("additionalProperties", not properties)
        for key, item in value.items():
            where = f"{path}.{key}" if path else key
            if key in properties:
                problems += settings_problems(item, properties[key], root, where)
            elif others is False:
                problems.append(f"{where} is no key of the settings")
            else:
                problems += settings_problems(item, others, root, where)
    elif isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            problems += settings_problems(
                item, schema["items"], root, f"{path}[{index}]"
            )
    return problems


def settings_schema(pin: Pin, command_path: str) -> dict[str, Any] | None:
    """The JSON schema of the settings of the pinned version, from the repository on
    GitHub that its package names; None when it has none there."""
    tag, path = SETTINGS_SCHEMAS.get(pin.package, ("", ""))
    package = Path(command_path).parent.parent / pin.package / "package.json"
    manifest = json.loads(package.read_text(encoding="utf-8"))
    repository = manifest.get("repository")
    url = repository.get("url", "") if isinstance(repository, dict) else repository
    found = re.search(r"github\.com[/:]([\w.-]+/[\w.-]+?)(?:\.git)?$", str(url or ""))
    if not path or not found:
        return None
    text = github_file(found[1], path, tag.format(version=pin.version))
    schema = json.loads(text) if text is not None else None
    return schema if isinstance(schema, dict) else None


def keys_of(settings: Any) -> int:
    """The keys of the settings, also those inside another."""
    if not isinstance(settings, dict):
        return 0
    return sum(1 + keys_of(value) for value in settings.values())


def environment(home: Path) -> dict[str, str]:
    """The variables of a run of a command line for its help or without a key: a home
    of its own, and no variable of the job but the path."""
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "NO_COLOR": "1",
    }


def help_of(command: Sequence[str], home: Path) -> str:
    home.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [*command, "--help"],
        env=environment(home),
        cwd=home,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=SECONDS,
        check=False,
    )
    if result.returncode:
        raise assistant.AssistantError(
            f"{' '.join([Path(command[0]).name, *command[1:]])} --help failed: "
            f"{assistant.one_line(result.stderr or result.stdout)[-300:]}"
        )
    return f"{result.stdout}\n{result.stderr}"


def run_problems(
    pin: Pin, result: subprocess.CompletedProcess[str], settings: Path
) -> list[str]:
    """A run without a key must fail asking for the key, and say nothing of its
    settings: a command line names the file of its settings only to refuse it or to
    say that it skips it."""
    printed = f"{result.stdout}\n{result.stderr}"
    secret = assistant.ENGINES[pin.key].credentials[0]
    problems = [
        f"{pin.name} refused or skipped its settings: {assistant.one_line(line)[:300]}"
        for line in printed.splitlines()
        if str(settings) in line
    ]
    if not result.returncode or secret not in printed:
        problems.append(
            f"{pin.name} ran without a key and didn't stop asking for {secret}, so "
            "an argument or a setting may have stopped it first: "
            f"{assistant.one_line(printed)[-300:] or 'no output'}"
        )
    return problems


def check_cli(pin: Pin, work: Path) -> tuple[str, list[str]]:
    """The flags, the settings and a run without a key of an engine that `analyze`
    runs, as it runs it."""
    engine = assistant.ENGINES[pin.key]
    cli = engine.cli
    assert cli is not None
    command_path = assistant.install_cli(cli, pin.version, work / "cli")
    runs = [
        assistant.Run(engine.model, TURNS, "1", effort)
        for effort in engine.efforts or ("",)
    ]
    flags: dict[str, set[str]] = {}
    for run in runs:
        for flag, values in passed_flags(
            assistant.cli_command(cli, command_path, run, PROMPT)[1:]
        ).items():
            flags.setdefault(flag, set()).update(values)
    options = help_options(help_of([command_path], work / "help"))
    problems = flag_problems(pin.name, flags, options)
    checked = [f"{len(flags)} flag(s)"]
    if cli.settings:
        settings = assistant.cli_settings(cli, runs[0])
        schema = settings_schema(pin, command_path)
        if schema is None:
            problems.append(
                f"{pin.name}: no JSON schema of its settings could be found, so a "
                "key of them that it doesn't know would go unnoticed; name where "
                "it is in SETTINGS_SCHEMAS of scripts/engine_contract.py"
            )
        else:
            problems += [
                f"{pin.name}: {problem}"
                for problem in settings_problems(settings, schema, schema)
            ]
        checked.append(f"{keys_of(settings)} key(s) of its settings")
    checkout = work / "checkout"
    checkout.mkdir(parents=True)
    result = assistant.run_cli(
        engine, cli, command_path, runs[0], PROMPT, checkout, work
    )
    problems += run_problems(pin, result, work / assistant.CLI_SETTINGS)
    checked.append(f"a run without {engine.credentials[0]}")
    return ", ".join(checked), problems


def turn_started(
    command: Sequence[str], env: dict[str, str], cwd: Path, started: str
) -> tuple[bool, str]:
    """Whether the command line started its turn, by the type of its JSON event, and
    what it printed until then; it stops there, or after SECONDS."""
    printed = []
    with subprocess.Popen(
        list(command),
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as process:
        timer = threading.Timer(SECONDS, process.kill)
        timer.start()
        try:
            for line in process.stdout or ():
                printed.append(line)
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("type") == started:
                    return True, "".join(printed)
        finally:
            timer.cancel()
            process.kill()
    return False, "".join(printed)


def check_action_cli(
    pin: Pin, steps: Iterable[EngineStep], work: Path
) -> tuple[str, list[str]]:
    """The flags and the configuration that an engine's action hands to its command
    line, and that it starts its turn with them, without a key."""
    engine = assistant.ENGINES[pin.key]
    action = ACTION_CLIS[pin.action]
    cli = assistant.Cli(package=action.package, command=action.command, arguments=())
    command_path = assistant.install_cli(cli, pin.version, work / "cli")
    arguments = json.loads(assistant.engine_arguments(engine, "The rules."))
    passed: dict[str, str] = {}
    for step in steps:
        if step.uses.partition("@")[0] == pin.action:
            passed |= {key: str(value) for key, value in step.inputs.items()}
    for key, setting in action.configured.items():
        if key in passed:
            arguments += ["--config", f"{setting}={json.dumps(passed[key])}"]
    command = [command_path, *action.subcommand]
    flags = passed_flags([*action.checking, *arguments])
    options = help_options(help_of(command, work / "help"))
    problems = flag_problems(
        f"{pin.name} {' '.join(action.subcommand)}", flags, options
    )
    home = work / "home"
    (home / f".{action.command}").mkdir(parents=True)
    checkout = work / "checkout"
    checkout.mkdir(parents=True)
    env = environment(home) | action.variables
    env[action.home] = str(home / f".{action.command}")
    started, printed = turn_started(
        [*command, *action.checking, *arguments, PROMPT], env, checkout, action.started
    )
    if not started:
        problems.append(
            f"{pin.name} didn't start its turn with the arguments of its step and the "
            f"configuration of its action: {assistant.one_line(printed)[-300:]}"
        )
    keys = len(flags.get("--config", ()))
    return f"{len(flags)} flag(s), {keys} key(s) of its configuration", problems


def clis_command() -> int:
    held = [
        name
        for engine in assistant.ENGINES.values()
        for name in engine.credentials
        if os.environ.get(name)
    ]
    if held:
        raise assistant.AssistantError(
            f"The check runs the engines without a key; unset {', '.join(held)}."
        )
    steps = engine_steps()
    problems = missing_steps(steps)
    lines = []
    for pin in cli_pins(steps):
        work = assistant.runner_folder()
        try:
            checked, found = (
                check_action_cli(pin, steps, work)
                if pin.action
                else check_cli(pin, work)
            )
        except (assistant.AssistantError, subprocess.TimeoutExpired) as error:
            checked, found = "stopped", [f"{pin.name}: {error}"]
        finally:
            shutil.rmtree(work, ignore_errors=True)
        problems += found
        lines.append(
            f"{assistant.ENGINES[pin.key].name}, {pin.name}: {checked}"
            + (f", {len(found)} problem(s)" if found else "")
        )
    return report(
        "The flags and settings of the engines' command lines", lines, problems
    )


def watched(steps: Iterable[EngineStep]) -> set[str]:
    return {step.workflow for step in steps} | set(WATCHED)


def changes_command() -> int:
    """Whether the pull request changes a file that the check reads."""
    repository, number = os.environ["GITHUB_REPOSITORY"], os.environ["PR"]
    result = run_gh(
        [
            "api",
            "--paginate",
            f"repos/{repository}/pulls/{number}/files",
            "--jq",
            ".[].filename",
        ]
    )
    if result.returncode:
        raise assistant.AssistantError(
            f"GitHub listed no files of the pull request #{number}: "
            f"{assistant.one_line(result.stderr)[-300:] or 'no message'}"
        )
    files = sorted(set(result.stdout.split()) & watched(engine_steps()))
    assistant.output("engines", "true" if files else "false")
    print(
        f"The pull request changes {', '.join(files)}, so the check runs."
        if files
        else "The pull request changes no step of an engine and nothing that the "
        "check reads, so the check passes without running."
    )
    return 0


def report(title: str, lines: list[str], problems: list[str]) -> int:
    for problem in problems:
        assistant.command("error", problem)
    assistant.summary(f"Engine contracts: {title}", lines + problems, False)
    return 1 if problems else 0


COMMANDS: dict[str, Callable[[], int]] = {
    "changes": changes_command,
    "actions": actions_command,
    "clis": clis_command,
}


def main(arguments: Sequence[str]) -> int:
    if len(arguments) != 1 or arguments[0] not in COMMANDS:
        print(f"usage: engine_contract.py {{{','.join(COMMANDS)}}}", file=sys.stderr)
        return 2
    try:
        return COMMANDS[arguments[0]]()
    except assistant.AssistantError as error:
        assistant.command("error", str(error))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
