"""Issue assistant: labels, a first analysis, duplicates and unanswered questions.

The GitHub Action in action.yml runs this package, with `python3 -m issue_assistant`,
in the repository it looks after. That repository keeps its labels in
.github/labels.toml, a description of the project and its settings in
.github/issue-assistant/, and copies of the four workflows and the firewall policy,
which install writes from templates/.github/; see README.md.

An AI engine from ENGINES writes the analysis. It only reads: the checkout and a
context folder that `context` writes, with the prompt and the JSON schema of its
answer. The answer reaches GitHub only through `apply`, which checks it against the
schema, the labels and the issues that exist, and which runs in a job without the
engine. Reminders, closing, reopening and the labels of the issue forms work
without one.

docs/architecture/modules.md has the map of the modules. Each imports only from
those above it in the list below, so the list reads from the ground up.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from .paths import FIREWALL, SETTINGS, TOOL
from .constants import (
    DISCUSSION_MODES,
    EVALUATED,
    FIREWALL_RUNNER,
    MODES,
    MOST_EVALUATED,
    REPOSITORY_NAME,
    RUNNER_VARIABLE,
)
from .values import command
from .errors import AssistantError, GitHubError, SettingsError, SweepIncomplete
from .engines import ENGINES
from .settings import Config, load_config, task_run
from .fixture import FIXTURE_VARIABLE, read_fixture
from .github import GitHub
from .issues import command_of
from .cleaning import code_span
from .applying import apply_answer
from .digests import report, share
from .context import write_context
from .events import handle_event
from .sweeps import sweep
from .labels import sync_labels
from .code_scanning import watch_findings
from .setup_check import check, firewall_endpoints, runner_problems, runner_trade_off
from .installation import (
    install,
    release_of,
    repository_of_checkout,
    repository_to_name,
    set_up_github,
)
from .running import (
    analyze,
    choose_engine,
    output,
    report_usage,
    runner_folder,
    summary,
)
from .evaluation import (
    LOCAL_ENGINES,
    MEASURES,
    MEASURES_HEAD,
    evaluate,
    evaluated_config,
    local_engine,
)
from .paths import *  # noqa: F403
from .constants import *  # noqa: F403
from .patterns import *  # noqa: F403
from .values import *  # noqa: F403
from .errors import *  # noqa: F403
from .languages import *  # noqa: F403
from .engines import *  # noqa: F403
from .settings import *  # noqa: F403
from .fixture import *  # noqa: F403
from .github import *  # noqa: F403
from .issues import *  # noqa: F403
from .cleaning import *  # noqa: F403
from .answer import *  # noqa: F403
from .comments import *  # noqa: F403
from .applying import *  # noqa: F403
from .learning import *  # noqa: F403
from .digests import *  # noqa: F403
from .context import *  # noqa: F403
from .commands import *  # noqa: F403
from .events import *  # noqa: F403
from .sweeps import *  # noqa: F403
from .labels import *  # noqa: F403
from .code_scanning import *  # noqa: F403
from .actionlint import *  # noqa: F403
from .setup_check import *  # noqa: F403
from .installation import *  # noqa: F403
from .running import *  # noqa: F403
from .evaluation import *  # noqa: F403

# The numbers of the accuracy page, from a report of `evaluate`. The job publish of
# .github/workflows/evaluate.yml takes a report that a job with an AI wrote into a job
# that writes, so the report counts as untrusted: only what passes the checks below
# reaches the page, which are numbers and the names of a measure, the engine, the model
# and the repository. The lines of the issues stay in the report.
ACCURACY_PAGE = PurePosixPath("docs/accuracy.md")
ACCURACY_BLOCK = re.compile(
    r"^<!-- accuracy numbers: start -->\n"
    r"(?P<sections>.*?)"
    r"^<!-- accuracy numbers: end -->$",
    re.DOTALL | re.MULTILINE,
)
# What comes before the lines of the issues in a report, as render_evaluation writes
# it. A number has at most nine digits, which int() always takes.
REPORT = re.compile(
    r"# How accurate the issue assistant is on (?P<repository>\S+)\n"
    r"\n"
    r"Measured on (?P<day>\d{4}-\d{2}-\d{2}) with (?P<engine>[A-Za-z]+), "
    r"`(?P<model>[\w.:/@-]+)`, on the (?P<replayed>\d{1,9}) newest of "
    r"(?P<read>\d{1,9}) closed issues read [^\n]*\n"
    r"\n"
    + "".join(rf"{re.escape(line)}\n" for line in MEASURES_HEAD)
    + r"(?P<rows>(?:\|[^\n]*\n)+)"
    r"\n"
    r"\*Precision\*[^\n]*\n"
    r"(?:\n(?P<failed>\d{1,9}) of the \d{1,9} analyses failed; they count in no "
    r"measure\.\n)?"
    r"(?:\nThe analyses cost \$(?P<cost>\d{1,9}\.\d\d) at the prices of the API\.\n)?"
    r"\n## Issue by issue\n",
    re.ASCII,
)
REPORT_ROW = re.compile(
    r"\| (?P<measure>[^|]+) \| (?P<precision>[^|]+) \| (?P<recall>[^|]+) \| "
    r"(?P<issues>\d{1,9}) \|",
    re.ASCII,
)
REPORT_SHARE = re.compile(r"\d{1,3} % \((\d{1,9}) of (\d{1,9})\)", re.ASCII)


@dataclass(frozen=True)
class Measurement:
    """The numbers of a report of `evaluate`, and what they were measured on."""

    repository: str
    day: date
    engine: str
    model: str
    replayed: int
    read: int
    # The lines of the table of measures.
    rows: tuple[str, ...]
    # How many analyses failed, and what they cost in dollars, if the report says.
    failed: int
    cost: str | None


def fits_share(cell: str) -> bool:
    """Whether a cell of the table of measures is what `share` writes for its numbers,
    with no more of the part than of the whole."""
    if cell == share(0, 0):
        return True
    found = REPORT_SHARE.fullmatch(cell)
    if found is None:
        return False
    part, whole = int(found[1]), int(found[2])
    return part <= whole and cell == share(part, whole)


def fits_row(line: str) -> bool:
    """Whether a line of the table of measures has a measure and its numbers."""
    row = REPORT_ROW.fullmatch(line)
    return (
        row is not None
        and row["measure"] in MEASURES
        and fits_share(row["precision"])
        and fits_share(row["recall"])
    )


def measurement_of(report_text: str) -> Measurement:
    """The numbers of a report of `evaluate`, or an error when anything about them
    isn't what `evaluate` writes."""
    found = REPORT.match(report_text)
    if found is None:
        raise AssistantError(
            "That is no report of evaluate: its head doesn't have the form of one."
        )
    rows = tuple(found["rows"].splitlines())
    bad = next((row for row in rows if not fits_row(row)), None)
    if bad is not None:
        raise AssistantError(
            f"That is no report of evaluate: the line {bad!r} of its table has no "
            "measure with its numbers."
        )
    if not REPOSITORY_NAME.fullmatch(found["repository"]):
        raise AssistantError(
            "That is no report of evaluate: it names no repository as owner/name."
        )
    if found["engine"] not in {engine.name for engine in ENGINES.values()}:
        raise AssistantError(
            f"That is no report of evaluate: it names the engine {found['engine']}."
        )
    try:
        day = date.fromisoformat(found["day"])
    except ValueError:
        raise AssistantError(
            f"That is no report of evaluate: {found['day']} is no day."
        ) from None
    failed, replayed = int(found["failed"] or 0), int(found["replayed"])
    if failed >= replayed:
        raise AssistantError(
            "That is no report of evaluate with numbers: no analysis gave an answer, "
            f"as {failed} of the {replayed} failed."
        )
    return Measurement(
        found["repository"],
        day,
        found["engine"],
        found["model"],
        replayed,
        int(found["read"]),
        rows,
        failed,
        found["cost"],
    )


def accuracy_section(found: Measurement) -> str:
    """The part of the accuracy page for one repository."""
    failed = (
        f" {found.failed} of the {found.replayed} analyses failed; they count in no "
        "measure."
        if found.failed
        else ""
    )
    cost = (
        f" The analyses cost ${found.cost} at the prices of the API."
        if found.cost
        else ""
    )
    return "\n".join(
        (
            f"### [{found.repository}](https://github.com/{found.repository})",
            "",
            f"Measured on {found.day.isoformat()} with {found.engine}, "
            f"{code_span(found.model)}, on the {found.replayed} newest of {found.read} "
            "closed issues read that people labelled or closed as a duplicate of an "
            f"older issue, and that the assistant never analyzed.{failed}{cost} Small "
            "numbers vary: with few issues, one more found or missed moves a share a "
            "lot, so each share names its counts.",
            "",
            *MEASURES_HEAD,
            *found.rows,
        )
    )


def page_with_numbers(page: str, found: Measurement) -> str:
    """The accuracy page with the numbers in its block: a part for each repository,
    in the order of their names, whose newest numbers replace its older ones. The text
    that stands there before the first numbers goes."""
    block = ACCURACY_BLOCK.search(page)
    if block is None:
        raise AssistantError(
            f"{ACCURACY_PAGE} has no block of numbers between the lines "
            "<!-- accuracy numbers: start --> and <!-- accuracy numbers: end -->."
        )
    kept = {
        section[len("### [") : section.index("]")]: section.strip()
        for section in re.split(r"(?m)^(?=### \[)", block["sections"])[1:]
    }
    kept[found.repository] = accuracy_section(found)
    sections = "\n\n".join(kept[name] for name in sorted(kept, key=str.casefold))
    start, end = block.span("sections")
    return f"{page[:start]}\n{sections}\n\n{page[end:]}"


def event_comment(number: int) -> int | None:
    """The id of the comment on the issue whose event started this run; None for any
    other event, such as a run by hand, and outside of GitHub Actions.

    The jobs of a run share its event, so the analysis and the apply job of a reply
    find the same comment, also when newer ones came in while the run waited.
    """
    identifier = (event_payload(number).get("comment") or {}).get("id")
    return identifier if isinstance(identifier, int) else None


def event_payload(number: int) -> dict[str, Any]:
    """The event that started this run when it is one of the issue; else empty."""
    path = os.environ.get("GITHUB_EVENT_PATH")
    payload = json.loads(Path(path).read_text("utf-8")) if path else {}
    if (payload.get("issue") or {}).get("number") != number:
        return {}
    found: dict[str, Any] = payload
    return found


def asked_again(number: int) -> bool:
    """Whether the run is one that a maintainer or the reporter started with
    /assistant again on the issue, which the event job let start: its analysis
    is posted also after an earlier one, as by hand."""
    payload = event_payload(number)
    comment = payload.get("comment")
    given = command_of(payload["issue"], comment) if comment else None
    return given is not None and given.name == "again"


def repository_of_run(*, needed: bool) -> str:
    """The repository the run looks after: GH_REPO, or else GITHUB_REPOSITORY.

    Only the commands that call GitHub need it; a name that is given must fit.
    """
    name = os.environ.get("GH_REPO") or os.environ.get("GITHUB_REPOSITORY", "")
    if (name or needed) and not REPOSITORY_NAME.fullmatch(name):
        raise AssistantError(
            "GH_REPO or GITHUB_REPOSITORY must name the repository as owner/name"
            + (f", not {name!r}." if name else ".")
        )
    return name


# The commands of the script with their help, in the order of --help, and below the
# options of some of them. They are constants, which mutmut leaves alone: its
# mutants change what the commands do, which the tests check, and not the texts of
# the help, which no test needs to check.
SCRIPT_COMMANDS = {
    "event": "handle the GitHub event of this run",
    "endpoints": "check the runner that ISSUE_ASSISTANT_AI_RUNNER names for the AI, "
    "and name the hosts of the firewall policy for Harden-Runner there",
    "engine": "name the engine, whether it can run, and the model, turns, budget and "
    "effort of the task",
    "context": "write the files the engine reads",
    "analyze": "run the command line of the engine of config.toml, for an engine "
    "without an action of its own, with tools that only read",
    "apply": "check the engine's answer in RESULT and apply it",
    "usage": "report the turns, time, cost and tokens of the engine's run in "
    "EXECUTION_FILE",
    "sweep": "remind, close unanswered issues and duplicates, and fixed issues with "
    "the release",
    "sync-labels": "create and update the labels",
    "findings": "dismiss the accepted findings of code scanning and fail while others "
    "are open",
    "check": "check the repository's set-up",
    "evaluate": "measure how often a first analysis chooses the labels, duplicates and "
    "questions of the maintainers, by replaying closed issues with the engine's "
    "command line on this computer",
    "accuracy-page": "write the numbers of a report of evaluate into "
    "docs/accuracy.md of the checkout",
    "install": "write the workflows and the starters into a repository",
}
# The commands of one task of the engine, which take --issue and --mode, by default
# from ISSUE and MODE.
TASK_COMMANDS = ("engine", "context", "analyze", "apply", "usage")
# The commands that read only the checkout, so they run anywhere, also on a computer
# without GH_REPO; the others, but install and accuracy-page, call GitHub.
OFFLINE_COMMANDS = ("check", "endpoints", "engine", "analyze", "usage")
SCRIPT_OPTIONS: dict[str, dict[str, dict[str, Any]]] = {
    "evaluate": {
        "--issues": {
            "type": int,
            "default": EVALUATED,
            "help": f"how many closed issues to replay, 1 to {MOST_EVALUATED}; "
            f"{EVALUATED} by default",
        },
        "--engine": {
            "choices": LOCAL_ENGINES,
            "help": "the engine whose command line answers; by default that of "
            "config.toml",
        },
        "--model": {"help": "the model; by default that of config.toml for its engine"},
        "--report": {
            "type": Path,
            "help": "the Markdown file of the report, outside the checkout; by default "
            "NAME-accuracy.md next to it",
        },
    },
    "accuracy-page": {
        "--report": {
            "type": Path,
            "required": True,
            "help": "the Markdown file of the report of evaluate",
        },
        "--repository": {
            "required": True,
            "help": "the repository that the run measured, as owner/name; a report "
            "that names another one is refused",
        },
    },
    "install": {
        "--ref": {"help": "commit hash of the release; by default the checkout's"},
        "--version": {"help": "release tag, such as v1.0.0; by default the checkout's"},
        "--setup": {
            "action": "store_true",
            "help": "also create the environment issue-assistant on GitHub and set the "
            "engine's secret",
        },
    },
}

DESCRIPTION = (
    "Issue assistant: labels, a first analysis, duplicates and unanswered questions."
)


def command_line() -> argparse.ArgumentParser:
    """The parser of SCRIPT_COMMANDS and their options."""
    parser = argparse.ArgumentParser(prog="issue_assistant", description=DESCRIPTION)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text_ in SCRIPT_COMMANDS.items():
        given = commands.add_parser(name, help=text_)
        if name in TASK_COMMANDS:
            given.add_argument(
                "--issue", type=int, default=os.environ.get("ISSUE") or None
            )
            given.add_argument(
                "--mode", choices=MODES, default=os.environ.get("MODE") or None
            )
        for option, settings in SCRIPT_OPTIONS.get(name, {}).items():
            given.add_argument(option, **settings)
    return parser


@dataclass
class CommandRun:
    """What the summary of a command that calls GitHub reports, also when GitHub
    fails: the title, which the command may change as it learns more, whether the run
    is a dry run, and its GitHub, once there is one."""

    title: str
    dry_run: bool
    github: GitHub | None = None


def main(argv: list[str] | None = None) -> int:
    arguments = command_line().parse_args(argv)
    # ISSUE_ASSISTANT_ROOT points the script at another checkout, as in the action's
    # own smoke test.
    root = Path(
        os.environ.get("ISSUE_ASSISTANT_ROOT")
        or os.environ.get("GITHUB_WORKSPACE")
        or os.getcwd()
    ).resolve()
    # A fixture stands in for the issue of context and apply, which then write
    # nothing; see Fixture.
    fixture_name = os.environ.get(FIXTURE_VARIABLE, "")
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true" or fixture_name != ""
    run = CommandRun(f"Issue assistant: {arguments.command}", dry_run)
    try:
        # Every other command would work on the real repository.
        if fixture_name and arguments.command not in {"context", "apply"}:
            raise AssistantError(
                f"{FIXTURE_VARIABLE} is for context and apply; "
                f"unset it for {arguments.command}."
            )
        if arguments.command == "install":
            return install_command(root, arguments)
        if arguments.command == "accuracy-page":
            return accuracy_page_command(root, arguments.report, arguments.repository)
        repository = repository_of_run(needed=arguments.command not in OFFLINE_COMMANDS)
        if arguments.command == "check":
            return check_command(root, repository)
        if arguments.command == "endpoints":
            return endpoints_command(root)
        if arguments.command == "engine":
            return engine_command(root, repository, arguments.mode)
        if arguments.command == "analyze":
            return analyze_command(root, repository, arguments.mode)
        if arguments.command == "usage":
            return usage_command(root, repository, arguments)
        # evaluate only reads; its engine writes into the checkout alone.
        github = run.github = GitHub(
            repository, dry_run=dry_run or arguments.command == "evaluate"
        )
        if arguments.command == "findings":
            # Independent of the settings of the issues.
            return findings_command(github, root, dry_run)
        config = load_config(root, repository)
        if arguments.command == "evaluate":
            return evaluate_command(github, config, root, repository, arguments)
        return issue_command(run, github, config, root, arguments, fixture_name)
    except SettingsError as error:
        for problem in error.problems:
            command("error", problem)
        return 1
    except AssistantError as error:
        command("error", str(error))
        return 1
    except GitHubError as error:
        # One error, not a traceback, and what the run wrote before it, which a
        # run again doesn't repeat.
        command("error", f"GitHub failed, so the run stopped: {error}")
        # Only the commands that call GitHub can fail on it, and they have `github`.
        failed = run.github
        summary(
            run.title,
            [
                *(failed.writes if failed else []),
                "Stopped: GitHub failed; running it again completes the rest.",
            ],
            dry_run,
            failed.previews if failed else (),
        )
        return 1


def install_command(root: Path, arguments: argparse.Namespace) -> int:
    ref, version = release_of(TOOL, arguments.ref, arguments.version)
    for line in install(root, ref, version, repository_to_name(root)):
        print(line)
    if arguments.setup:
        if shutil.which("gh") is None:
            raise AssistantError(
                "--setup needs the GitHub CLI, gh: https://cli.github.com"
            )
        repository = repository_of_run(needed=False) or repository_of_checkout(root)
        print(f"set up {repository} on GitHub:")
        # It writes, as install writes the files, also in a dry run.
        github = GitHub(repository, dry_run=False)
        config = load_config(root, repository, lenient=True)
        for line in set_up_github(github, config, ask=sys.stdin.isatty()):
            print(line)
    print(
        f"next: describe the project in {SETTINGS / 'project.md'}, run "
        f"PYTHONPATH={TOOL.as_posix()} python3 -P -m issue_assistant check, commit, "
        "push, and try it under Actions > Issue assistant > Run workflow"
    )
    return 0


def check_command(root: Path, repository: str) -> int:
    # The variable lives on GitHub, not in the checkout: a step of the CI passes it.
    runner = os.environ.get(RUNNER_VARIABLE, "")
    problems = check(load_config(root, repository, lenient=True)) + runner_problems(
        runner
    )
    for problem in problems:
        command("error", problem)
    if not problems and runner not in ("", FIREWALL_RUNNER):
        command("notice", runner_trade_off(runner))
    summary("Issue assistant set-up", problems or ["Everything fits."], False)
    return 1 if problems else 0


def endpoints_command(root: Path) -> int:
    """For the job event: the endpoints of Harden-Runner for the job analyze on the
    runner of ISSUE_ASSISTANT_AI_RUNNER, empty on GitHub's egress-firewall runner,
    whose firewall reads the policy itself."""
    runner = os.environ.get(RUNNER_VARIABLE, "")
    problems = runner_problems(runner)
    if problems:
        raise SettingsError(problems)
    if runner in ("", FIREWALL_RUNNER):
        output("endpoints", "")
        print(
            f"The AI runs on {FIREWALL_RUNNER}, whose firewall blocks every host but "
            f"those of {FIREWALL}."
        )
        return 0
    output("endpoints", firewall_endpoints(root))
    command("notice", runner_trade_off(runner))
    return 0


def engine_command(root: Path, repository: str, mode: str | None) -> int:
    config = load_config(root, repository)
    key, available = choose_engine(config, os.environ.get("READY_ENGINES", ""))
    run = task_run(config, mode)
    output("engine", key)
    output("ready", str(available).lower())
    output("model", run.model)
    output("turns", str(run.turns))
    output("budget", run.budget)
    output("effort", run.effort)
    return 0


def analyze_command(root: Path, repository: str, mode: str | None) -> int:
    key = os.environ.get("ENGINE", "")
    work = runner_folder()
    answer = analyze(load_config(root, repository), mode, key, work)
    output("answer", answer)
    command(
        "notice",
        f"{ENGINES[key].name} answered; the job apply checks the answer "
        "before anything is written.",
    )
    return 0


def usage_command(root: Path, repository: str, arguments: argparse.Namespace) -> int:
    run = task_run(load_config(root, repository), arguments.mode)
    lines = report_usage(os.environ.get("EXECUTION_FILE", ""), run, arguments.mode)
    of = f" of #{arguments.issue}" if arguments.issue else ""
    task_name = arguments.mode or "analysis"
    summary(f"Issue assistant: cost of the {task_name}{of}", lines, False)
    return 0


def findings_command(github: GitHub, root: Path, dry_run: bool) -> int:
    lines, left = watch_findings(github, root)
    for line in lines:
        if line.startswith("Alert #") and " is open: " in line:
            command("error", line)
    summary("Findings", lines or ["No open findings."], dry_run)
    return 1 if left else 0


def evaluate_command(
    github: GitHub,
    config: Config,
    root: Path,
    repository: str,
    arguments: argparse.Namespace,
) -> int:
    config = evaluated_config(config, arguments.engine, arguments.model)
    written = (arguments.report or root.parent / f"{github.name}-accuracy.md").resolve()
    if written.is_relative_to(root):
        raise AssistantError(
            f"The report {written} must be outside the checkout, where the "
            "engine would read it in the next evaluation."
        )
    # Checked before the replays, whose answers cost, so that none is lost.
    if not written.parent.is_dir() or not os.access(written.parent, os.W_OK):
        raise AssistantError(
            f"The report {written} needs a folder that exists and that you "
            "may write to."
        )
    work = Path(tempfile.mkdtemp(prefix="issue-assistant-"))
    try:
        lines = evaluate(
            github,
            config,
            arguments.issues,
            written,
            local_engine(config, work),
            datetime.now(UTC).date(),
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    summary(f"Accuracy on {repository}", lines, False)
    return 0


def accuracy_page_command(root: Path, report_file: Path, repository: str) -> int:
    page = root / ACCURACY_PAGE
    try:
        report_text = report_file.read_text(encoding="utf-8")
        before = page.read_text(encoding="utf-8")
    except (OSError, ValueError) as error:
        raise AssistantError(f"accuracy-page can't read a file: {error}") from None
    found = measurement_of(report_text)
    if found.repository != repository:
        raise AssistantError(
            f"This run measured {repository}, and the report names {found.repository}."
        )
    page.write_text(page_with_numbers(before, found), encoding="utf-8")
    summary(
        f"Accuracy on {found.repository}",
        [f"The numbers of {found.day.isoformat()} are in {ACCURACY_PAGE}."],
        False,
    )
    return 0


def issue_command(
    run: CommandRun,
    github: GitHub,
    config: Config,
    root: Path,
    arguments: argparse.Namespace,
    fixture_name: str,
) -> int:
    """The commands of an event and of the issues: event, context, apply, sweep and
    sync-labels."""
    if fixture_name and arguments.mode in DISCUSSION_MODES:
        raise AssistantError(
            f"A fixture holds an issue; {arguments.mode} reads a discussion of "
            "GitHub, so unset it."
        )
    if fixture_name:
        fixture = read_fixture(root, fixture_name)
        if arguments.issue not in {None, fixture.number}:
            raise AssistantError(
                f"The fixture {fixture_name} holds issue #{fixture.number}, "
                f"not #{arguments.issue}."
            )
        arguments.issue = fixture.number
        github.use_fixture(fixture)
        command(
            "notice",
            f"Issue #{fixture.number} comes from the fixture {fixture_name}; "
            "nothing is written.",
        )
    if arguments.command in {"context", "apply"} and not (
        arguments.issue and arguments.mode
    ):
        raise AssistantError(f"{arguments.command} needs an issue and a mode.")
    if arguments.command == "context":
        written = write_context(
            github,
            arguments.issue,
            arguments.mode,
            config,
            event_comment(arguments.issue),
        )
        output("prompt", written["prompt"])
        output("task", written["task"])
        output("schema", written["schema"])
        output("engine-args", written["engine-args"])
        return 0
    if arguments.command == "event":
        return event_command(run, github, config)
    if arguments.command == "apply":
        return apply_command(run, github, config, arguments)
    if arguments.command == "sweep":
        return sweep_command(run, github, config)
    run.title = "Labels"
    summary(run.title, sync_labels(github, config), run.dry_run, github.previews)
    return 0


def event_command(run: CommandRun, github: GitHub, config: Config) -> int:
    name = os.environ["GITHUB_EVENT_NAME"]
    payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text("utf-8"))
    plan = handle_event(github, name, payload, config)
    output("issue", str(plan.issue or ""))
    output("mode", plan.mode)
    run.title = f"Issue assistant: #{plan.issue}, next task {plan.mode}"
    summary(run.title, plan.notes + github.writes, run.dry_run, github.previews)
    return 0


def apply_command(
    run: CommandRun, github: GitHub, config: Config, arguments: argparse.Namespace
) -> int:
    run.title = f"Issue assistant: {arguments.mode} of #{arguments.issue}"
    done = apply_answer(
        github,
        arguments.mode,
        arguments.issue,
        os.environ.get("RESULT", ""),
        config,
        os.environ.get("GITHUB_SHA", "HEAD"),
        repeat=os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"
        or asked_again(arguments.issue),
        engine=os.environ.get("ENGINE", ""),
        comment_id=event_comment(arguments.issue),
    )
    summary(run.title, done + github.writes, run.dry_run, github.previews)
    return 0


def sweep_command(run: CommandRun, github: GitHub, config: Config) -> int:
    run.title = "Issue lifecycle"
    now = datetime.now(UTC)
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    github.remember()
    try:
        done = sweep(github, now, config.releases, config.lifecycle)
    except SweepIncomplete as incomplete:
        command("error", f"{incomplete}; the next sweep tries again.")
        counted, sections = report(github, config, now, event)
        summary(
            run.title,
            incomplete.done
            + [f"Failed: {failure}" for failure in incomplete.failed]
            + counted,
            run.dry_run,
            github.previews,
            sections,
        )
        return 1
    counted, sections = report(github, config, now, event)
    summary(run.title, done + counted, run.dry_run, github.previews, sections)
    return 0
