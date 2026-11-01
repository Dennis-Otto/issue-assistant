"""Running the engine: choosing it, installing its command line, starting it and reporting
what it used."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path, PurePath, PurePosixPath
from typing import Any

from .cleaning import one_line
from .comments import bullets
from .engines import ENGINES, Cli, Engine, Run, engine_named
from .errors import AssistantError
from .paths import CONTEXT, ENGINE_LOCKS, SETTINGS
from .settings import Config, task_run
from .values import command, fence


def output(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        print(f"{name}={value}")
        return
    with open(path, "a", encoding="utf-8") as handle:
        if "\n" in value:
            delimiter = f"EOF_{os.urandom(8).hex()}"
            handle.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
        else:
            handle.write(f"{name}={value}\n")


def summary(
    title: str,
    lines: list[str],
    dry_run: bool,
    previews: Iterable[str] = (),
    sections: str = "",
) -> None:
    """Print the lines of the run, the comments of a dry run and the sections after
    them, such as the digest of the sweep, and add them to the summary of the job."""
    text = f"### {title}{' (dry run)' if dry_run else ''}\n\n" + (
        bullets(lines) if lines else "Nothing to do."
    )
    for preview in previews:
        body = preview.rstrip()
        marks = fence(body)
        text += (
            "\n\n<details><summary>Comment</summary>\n\n"
            f"{marks}markdown\n{body}\n{marks}\n\n</details>"
        )
    if sections:
        text += f"\n\n{sections}"
    # The lines and the comments hold the engine's text, which the runner must not
    # read as workflow commands; it reads them again after the random token. Only a
    # terminal outside a runner, such as a check on one's own computer, shows the
    # text alone: the runner reads every other output, also that of a container.
    if sys.stdout.isatty() and os.environ.get("GITHUB_ACTIONS") != "true":
        print(text)
    else:
        token = os.urandom(16).hex()
        print(f"::stop-commands::{token}\n{text}\n::{token}::")
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def choose_engine(config: Config, ready: str) -> tuple[str, bool]:
    """The engine of config.toml and whether its secret exists.

    `ready` lists the engines whose secret the workflow found, separated by commas.
    """
    key = engine_named(config.engine)
    available = key in {item.strip() for item in ready.split(",") if item.strip()}
    if not available:
        command(
            "notice",
            f"No credentials for {ENGINES[key].name} are configured; "
            "the issue assistant only sets labels and keeps the lifecycle.",
        )
    return key, available


# The engines that `analyze` runs, those with a Cli in ENGINES.

# A version of an engine's command line as its lock file pins it, such as 1.2.3.
CLI_VERSION = re.compile(r"\d+\.\d+\.\d+")
# The files of the folder of an engine in ENGINE_LOCKS, which npm ci installs.
LOCK_FILES = ("package.json", "package-lock.json")
# The most minutes that an engine's command line runs; the job stops at 20.
ENGINE_MINUTES = 15
# The file of the settings of an engine's command line, in the folder of its run.
CLI_SETTINGS = "settings.json"
# Where a command line that takes its settings only from root finds those of a run:
# in a folder of the run below this one, which root owns and nobody else may write to.
ROOT_SETTINGS = PurePosixPath("/etc/issue-assistant")


def cli_settings(cli: Cli, run: Run) -> dict[str, Any]:
    """The settings of the command line for the run of a task: its turns at the key
    `turns`."""
    settings = deepcopy(cli.settings)
    if cli.turns:
        *path, last = cli.turns.split(".")
        place = settings
        for key in path:
            place = place.setdefault(key, {})
        place[last] = run.turns
    return settings


def cli_command(cli: Cli, command_path: str, run: Run, prompt: str) -> list[str]:
    """The command line of an engine that `analyze` runs, for the prompt of a task."""
    effort = [cli.effort, run.effort] if cli.effort and run.effort else []
    return [command_path, cli.prompt, prompt, "--model", run.model, *effort] + list(
        cli.arguments
    )


def cli_environment(
    engine: Engine, cli: Cli, home: Path, settings: PurePath
) -> dict[str, str]:
    """The variables of an engine's command line: a home of its own, outside the
    checkout, its settings, and of the job's variables only the engine's secret."""
    variables = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "NO_COLOR": "1",
        **cli.variables,
    }
    if cli.settings_variable:
        variables[cli.settings_variable] = str(settings)
    variables |= {
        name: os.environ[name] for name in engine.credentials if os.environ.get(name)
    }
    return variables


def locked_version(key: str, locks: Path = ENGINE_LOCKS) -> str:
    """The version of the command line of the engine `key` in its lock file."""
    cli = ENGINES[key].cli
    assert cli is not None
    path = locks / key / LOCK_FILES[1]
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        lock = None
    packages = lock.get("packages") if isinstance(lock, dict) else None
    entry = (
        packages.get(f"node_modules/{cli.package}")
        if isinstance(packages, dict)
        else None
    )
    version = entry.get("version") if isinstance(entry, dict) else None
    if not isinstance(version, str) or not CLI_VERSION.fullmatch(version):
        raise AssistantError(
            f"The issue assistant has no lock file of {cli.package} with its version "
            f"at {path}: its step needs the folder {ENGINE_LOCKS.name}/ of its checkout."
        )
    return version


# What npm never sees: the secrets of the engines and the tokens of GitHub. It
# installs with the scripts of no package, so no code of a package runs then.
NPM_HIDDEN = frozenset(
    {name for engine in ENGINES.values() for name in engine.credentials}
    | {"GH_TOKEN", "GITHUB_TOKEN"}
)


def install_cli(key: str, folder: Path, locks: Path = ENGINE_LOCKS) -> str:
    """Install the command line of the engine `key` into `folder` from its lock file
    in `locks` and return the path of its command.

    npm ci installs each package at the version of the lock file and refuses one
    whose hash isn't its integrity there, so `analyze` runs exactly what the check
    engine contracts verified, with the signatures of npm's registry, when the lock
    file changed; it never resolves a version of its own.
    """
    cli = ENGINES[key].cli
    assert cli is not None
    version = locked_version(key, locks)
    npm = shutil.which("npm")
    if npm is None:
        raise AssistantError("analyze needs npm, which GitHub's runners have.")
    folder.mkdir(parents=True, exist_ok=True)
    for name in LOCK_FILES:
        shutil.copyfile(locks / key / name, folder / name)
    # In the folder, so that npm reads no .npmrc of the checkout.
    result = subprocess.run(
        [npm, "ci", "--prefix", str(folder), "--ignore-scripts"]
        + ["--no-audit", "--no-fund", "--loglevel", "error"],
        cwd=folder,
        env={
            name: value for name, value in os.environ.items() if name not in NPM_HIDDEN
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        code = re.search(r"(?m)^npm (?:error|ERR!) code (\S+)", result.stderr)
        reason = (
            "the registry sent a package whose hash isn't the integrity of the lock "
            "file, so nothing of it runs"
            if code and code[1] == "EINTEGRITY"
            else one_line(result.stderr)[-300:] or "no message"
        )
        raise AssistantError(
            f"npm installed no {cli.package}@{version} from "
            f"{ENGINE_LOCKS.name}/{key}/{LOCK_FILES[1]}"
            + (f" ({code[1]})" if code else "")
            + f": {reason}"
        )
    return str(folder / "node_modules" / ".bin" / cli.command)


def cli_answer(
    engine: Engine, cli: Cli, result: subprocess.CompletedProcess[str]
) -> str:
    """The answer in the output of an engine's command line."""
    if cli.at_turns is not None and result.returncode == cli.at_turns:
        raise AssistantError(
            f"{engine.name} stopped at the turns of the task, before it answered; "
            f"max_turns under [engine] in {SETTINGS / 'config.toml'} gives it more."
        )
    reason: object = None
    answer: object = None
    if cli.answer:
        try:
            printed = json.loads(result.stdout)
        except ValueError:
            printed = None
        report = printed if isinstance(printed, dict) else {}
        error = report.get("error")
        reason = error.get("message") if isinstance(error, dict) else None
        answer = report.get(cli.answer)
    else:
        answer, reason = event_answer(cli, result.stdout)
    if result.returncode or not isinstance(answer, str) or not answer.strip():
        message = one_line(str(reason or result.stderr))[-300:] or "no answer"
        raise AssistantError(f"{engine.name} failed: {message}")
    return answer


def event_answer(cli: Cli, printed: str) -> tuple[str | None, object]:
    """The answer and the last message of an error in the JSON events that an
    engine's command line printed, one on each line, of its main agent alone."""
    events = []
    for line in printed.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        data = event.get("data") if isinstance(event, dict) else None
        if isinstance(data, dict) and "agentId" not in event:
            events.append((event.get("type"), data))
    errors = [data.get("message") for kind, data in events if kind == cli.error_event]
    messages = [
        data
        for kind, data in events
        if kind == cli.answer_event and isinstance(data.get("content"), str)
    ]
    reason = errors[-1] if errors else None
    if not messages:
        return None, reason
    # A call of the model may answer in parts, split where it reasoned.
    call = messages[-1].get("apiCallId")
    parts = [data for data in messages if call and data.get("apiCallId") == call]
    return "\n".join(data["content"] for data in parts or messages[-1:]), reason


def analyze(config: Config, mode: str | None, key: str, work: Path) -> str:
    """Run the engine of config.toml that has no action of its own on the task of
    the context folder, with tools that only read the checkout, and return its
    answer, which `apply` checks like any other."""
    runs = {name: cli for name, engine in ENGINES.items() if (cli := engine.cli)}
    if key not in runs:
        raise AssistantError(
            f"analyze runs the engines {', '.join(runs)}, not {key or 'none'}."
        )
    if key != engine_named(config.engine):
        raise AssistantError(
            f"The step of {key} ran, but config.toml names the engine {config.engine}."
        )
    engine, cli = ENGINES[key], runs[key]
    run = task_run(config, mode)
    folder = config.root / CONTEXT
    prompt = "\n\n".join(
        (folder / name).read_text(encoding="utf-8")
        for name in ("rules.md", "prompt.md")
    )
    command_path = install_cli(key, work / "cli")
    result = run_cli(engine, cli, command_path, run, prompt, config.root, work)
    return cli_answer(engine, cli, result)


def run_cli(
    engine: Engine,
    cli: Cli,
    command_path: str,
    run: Run,
    prompt: str,
    root: Path,
    work: Path,
) -> subprocess.CompletedProcess[str]:
    """Run the engine's command line on the prompt in the checkout `root`, with a
    home and the settings of the run in `work`, the folder of the run.

    scripts/engine_contract.py runs it so too, without the secret, so that the check
    of the settings and the flags runs them where `analyze` does.
    """
    home = work / "home"
    home.mkdir(parents=True, exist_ok=True)
    with placed_settings(cli, json.dumps(cli_settings(cli, run)), work) as settings:
        try:
            return subprocess.run(
                cli_command(cli, command_path, run, prompt),
                cwd=root,
                env=cli_environment(engine, cli, home, settings),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=ENGINE_MINUTES * 60,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise AssistantError(
                f"{engine.name} gave no answer within {ENGINE_MINUTES} minutes."
            ) from None


@contextmanager
def placed_settings(cli: Cli, text: str, work: Path) -> Iterator[PurePath]:
    """Write the settings `text` of a run in the folder `work` to where the command
    line reads them, and yield that path; for a command line that takes them only
    from root, into a folder of root's, which goes again when the run ends, also
    after a copy that failed half way.

    `analyze` and `evaluate` place them so, and scripts/engine_contract.py too.
    """
    written = work / CLI_SETTINGS
    written.write_text(text, encoding="utf-8")
    settings = settings_path(cli, work)
    try:
        if cli.root_settings:
            install_as_root(written, settings)
        yield settings
    finally:
        if cli.root_settings:
            as_root(["rm", "-rf", str(settings.parent)])


def settings_path(cli: Cli, work: Path) -> PurePath:
    """Where the command line reads the settings of the run in the folder `work`."""
    if cli.root_settings:
        return ROOT_SETTINGS / work.name / CLI_SETTINGS
    return work / CLI_SETTINGS


def as_root(command: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a command as root: with sudo, which GitHub's runners have, unless the job
    runs as root already."""
    root = getattr(os, "geteuid", lambda: -1)() == 0
    command = command if root else ["sudo", "--non-interactive", *command]
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(command, 127, "", f"no {command[0]}")


def install_as_root(source: Path, target: PurePath) -> None:
    """Copy the settings to `target`, in a new folder of root's that nobody else may
    write to, as the folder around it; a command line that takes its settings only
    so would otherwise run without them."""
    folder = target.parent
    for arguments in (
        # Root's numbers, as macOS has no group named root.
        ["install", "-d", "-m", "755", "-o", "0", "-g", "0"]
        + [str(folder.parent), str(folder)],
        ["install", "-m", "644", "-o", "0", "-g", "0", str(source), str(target)],
    ):
        result = as_root(arguments)
        if result.returncode:
            raise AssistantError(
                f"The settings that keep the engine's command line to reading must "
                f"belong to root, and {' '.join(arguments[:2])} as root failed: "
                f"{one_line(result.stderr)[-300:] or 'no message'}. It needs sudo "
                "without a password prompt: GitHub's runners have it; on your "
                "computer, run sudo -v first."
            )


def runner_folder() -> Path:
    """A new folder for a run of an engine's command line, in the temporary folder
    of the runner, that only the user of the runner may enter.

    scripts/engine_contract.py runs the engines' command lines in such a folder too,
    as `analyze` does.
    """
    return Path(
        tempfile.mkdtemp(
            prefix="issue-assistant-", dir=os.environ.get("RUNNER_TEMP") or None
        )
    )


# How a run of the engine ended, by the subtype of the result in its execution file;
# any other subtype, or an error, is a failure.
OUTCOMES = {
    "success": "answered",
    "error_max_turns": "stopped at max_turns",
    "error_max_budget_usd": "stopped at max_budget_usd",
}


def engine_result(path: str) -> dict[str, Any] | None:
    """The result of the engine's run: the last message of the type result in the
    execution file of its action, a JSON list of the messages of the run; None
    without the file or such a message."""
    try:
        messages = json.loads(Path(path).read_text("utf-8")) if path else None
    except (OSError, ValueError):
        return None
    results = [
        message
        for message in (messages if isinstance(messages, list) else [])
        if isinstance(message, dict) and message.get("type") == "result"
    ]
    return results[-1] if results else None


def number_of(value: object) -> float | None:
    """A number of the result, 0 or more; None for anything else."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    # Neither nan nor inf is a count.
    return float(value) if 0 <= value < float("inf") else None


def usage_line(result: dict[str, Any], run: Run) -> str:
    """What the engine's run took, in one line of numbers: its turns, time, cost and
    tokens, against the limits of the task.

    The execution file holds the whole run, with the texts of the issue; nothing but
    these numbers and fixed words leaves it.
    """
    subtype = str(result.get("subtype"))
    # A run that stopped at a limit is an error too; a success with one is none.
    failed = subtype == "success" and result.get("is_error") is True
    outcome = "failed" if failed else OUTCOMES.get(subtype, "failed")
    spent = []
    turns = number_of(result.get("num_turns"))
    if turns is not None:
        spent.append(f"{turns:.0f} of {run.turns} turns")
    milliseconds = number_of(result.get("duration_ms"))
    if milliseconds is not None:
        minutes, seconds = divmod(round(milliseconds / 1000), 60)
        spent.append(f"{minutes} min {seconds} s" if minutes else f"{seconds} s")
    line = f"{run.model} {outcome}" + (f" after {' and '.join(spent)}" if spent else "")
    cost = number_of(result.get("total_cost_usd"))
    if cost is not None:
        line += f", ${cost:.3f} of ${float(run.budget):.2f}"
    usage = result.get("usage")
    tokens = usage if isinstance(usage, dict) else {}
    counts = {
        name: number_of(tokens.get(f"{name}_tokens")) or 0.0
        for name in ("input", "cache_creation_input", "cache_read_input", "output")
    }
    read = counts["input"] + counts["cache_creation_input"] + counts["cache_read_input"]
    if read or counts["output"]:
        line += (
            f", {read:,.0f} tokens in ({counts['cache_read_input']:,.0f} of them from "
            f"the cache) and {counts['output']:,.0f} out"
        )
    return line


def report_usage(path: str, run: Run, mode: str | None) -> list[str]:
    """The line of usage_line for the summary of the job, and a warning that names
    the setting when the run stopped at a limit before it answered."""
    result = engine_result(path)
    if result is None:
        command(
            "notice",
            "The engine left no result in its execution file, so what its run cost "
            "is unknown.",
        )
        return ["No result of the engine; what its run cost is unknown."]
    task = f"the task {mode}" if mode else "the analysis"
    where = SETTINGS / "config.toml"
    stopped = {
        "error_max_turns": (
            f"The engine stopped after the {run.turns} turns of {task}, before it "
            f"answered; max_turns under [engine] in {where} gives it more."
        ),
        "error_max_budget_usd": (
            f"The engine stopped at max_budget_usd = {run.budget} dollars, before it "
            f"answered; raise it under [engine] in {where}, or choose a cheaper "
            "model or effort there."
        ),
    }.get(str(result.get("subtype")))
    if stopped:
        command("warning", stopped)
    return [usage_line(result, run)]
