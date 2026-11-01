"""`evaluate`: replaying closed issues through the engine and scoring its answers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Iterable
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .answer import parse_answer
from .applying import other_issue, questions, vouched_for
from .cleaning import code_span, one_line
from .constants import MOST_EVALUATED, NEEDS_INFO, NO_PRIORITY
from .context import write_context
from .digests import share
from .engines import ENGINES, Engine, Run, engine_named
from .errors import AssistantError, GitHubError
from .github import GitHub
from .issues import is_bot, is_maintainer, kind_of, opted_out, text_of
from .learning import CHOSEN_GROUPS, plural
from .paths import CONTEXT
from .patterns import CODE, HTML_COMMENT
from .running import (
    ENGINE_MINUTES,
    OUTCOMES,
    cli_answer,
    cli_command,
    cli_settings,
    number_of,
    placed_settings,
)
from .settings import Config, label_names, task_run
from .values import created, login, moment, stamp

# The moment that it replays: a minute after the issue was opened, when it had the
# labels of its form and the analysis would have started.
OPENING = timedelta(minutes=1)

# Evaluation: how often a first analysis chooses what the maintainers chose

# The groups of labels whose choices `evaluate` scores, as the report names them.
SCORED_GROUPS = dict(
    zip(CHOSEN_GROUPS, ("Type", "Areas", "Topics", "Priority"), strict=True)
)
# The engines whose command line `evaluate` runs on this computer.
LOCAL_ENGINES = tuple(
    key for key, engine in ENGINES.items() if engine.cli or engine.local
)
# The measures of a report, in its order, and the head of their table.
MEASURES = (
    *SCORED_GROUPS.values(),
    "Duplicates",
    "Sure duplicates",
    "Asks for information",
)
MEASURES_HEAD = (
    "| Measure | Precision | Recall | Issues |",
    "| --- | ---: | ---: | ---: |",
)


@dataclass(frozen=True)
class Sample:
    """A closed issue that `evaluate` replays, and what people decided about it."""

    # The issue as it is now, closed, and as it was at the moment of the replay.
    issue: dict[str, Any]
    moment: datetime
    then: dict[str, Any]
    # The labels of each group of SCORED_GROUPS that it has, but those of bots.
    labels: dict[str, frozenset[str]]
    # The older issue that it was closed as a duplicate of.
    original: int | None
    # Whether a maintainer asked its reporter for more information.
    asked: bool

    @property
    def number(self) -> int:
        number: int = self.issue["number"]
        return number


def labels_at(
    issue: dict[str, Any], events: list[dict[str, Any]], when: datetime
) -> set[str]:
    """The labels that the issue had at the moment: those of now, with every change
    of a label after it undone, the latest first."""
    names = label_names(issue)
    for event in sorted(events, key=created, reverse=True):
        if created(event) <= when:
            break
        name = str((event.get("label") or {}).get("name", ""))
        if event["event"] == "labeled":
            names.discard(name)
        elif event["event"] == "unlabeled":
            names.add(name)
    return names


def labels_by_people(
    config: Config,
    issue: dict[str, Any],
    events: list[dict[str, Any]],
    when: datetime,
) -> dict[str, frozenset[str]]:
    """The labels of each scored group that the issue has and that a person added
    last, after the moment: what the maintainers decided.

    A label that it had at the moment, such as that of its form, which GitHub
    records as added by the reporter, is no decision: the replay sees it. Nor is
    one that a bot added last, such as the assistant or a labeler of the
    repository."""
    last = {
        str((event.get("label") or {}).get("name", "")): event
        for event in sorted(events, key=created)
        if event["event"] == "labeled"
    }
    names = {
        name
        for name in label_names(issue)
        if name in last
        and created(last[name]) > when
        and not is_bot(last[name].get("actor"))
    }
    return {
        group: frozenset(names.intersection(config.group(group)))
        for group in SCORED_GROUPS
    }


def maintainers_asked(
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
    events: list[dict[str, Any]],
    when: datetime,
) -> bool:
    """Whether a maintainer asked the reporter for more information after the
    moment: with a question in their first comment, outside of code, or with the
    label needs-info."""
    reporter = login(issue.get("user"))
    first = next(
        (
            comment
            for comment in comments
            if created(comment) > when
            and is_maintainer(comment)
            and not is_bot(comment.get("user"))
            and login(comment.get("user")) != reporter
        ),
        None,
    )
    question = first is not None and "?" in CODE.sub(
        "", HTML_COMMENT.sub("", text_of(first))
    )
    return question or any(
        event["event"] == "labeled"
        and (event.get("label") or {}).get("name") == NEEDS_INFO
        and not is_bot(event.get("actor"))
        and created(event) > when
        for event in events
    )


def sample_of(github: GitHub, config: Config, issue: dict[str, Any]) -> Sample | None:
    """The sample of a closed issue that people labelled with a label of a scored
    group or closed as a duplicate of an older issue, and that the assistant never
    analyzed; None for any other issue, such as one that a bot opened or whose
    reporter keeps the AI out of it."""
    if is_bot(issue["user"]) or opted_out(issue, config):
        return None
    duplicate = issue["duplicate_of"]
    original = (
        duplicate["number"]
        if duplicate and moment(duplicate["created_at"]) < created(issue)
        else None
    )
    scored = {name for group in SCORED_GROUPS for name in config.group(group)}
    if original is None and not label_names(issue) & scored:
        return None
    number = issue["number"]
    comments = github.comments(number)
    if any(kind_of(comment) == "analysis" for comment in comments):
        return None
    events = github.events(number)
    when = created(issue) + OPENING
    labels = labels_by_people(config, issue, events, when)
    if original is None and not any(labels.values()):
        return None
    then = issue | {
        "state": "open",
        "state_reason": None,
        "closed_at": None,
        "duplicate_of": None,
        "labels": [{"name": name} for name in sorted(labels_at(issue, events, when))],
    }
    asked = maintainers_asked(issue, comments, events, when)
    return Sample(issue, when, then, labels, original, asked)


def choose_samples(
    github: GitHub, config: Config, count: int
) -> tuple[list[Sample], int]:
    """The samples of the `count` closed issues opened last that have one, and how
    many closed issues it read for them."""
    chosen: list[Sample] = []
    read = 0
    for issue in github.closed_issues():
        read += 1
        sample = sample_of(github, config, issue)
        if sample:
            chosen.append(sample)
            if len(chosen) == count:
                break
    return chosen, read


class Then(GitHub):
    """GitHub as it was at the moment of a sample, for the context of its replay:
    the issue as it was, and of everything else only what existed then.

    An issue closed since is open, and a discussion answered since has no answer;
    the texts and the labels of the other issues are those of now. It reads through
    `github`, writes nothing and shares `memo` with the replays of the other
    samples, so that the window of the issues, the discussions, the releases and the
    latest comments are read once. A read that it doesn't change, such as whether
    someone may push, goes to GitHub as it is.
    """

    def __init__(self, github: GitHub, sample: Sample, memo: dict[str, Any]) -> None:
        super().__init__(github.repository, dry_run=True)
        self.now = github
        self.sample = sample
        self.memo = memo

    def rest(
        self,
        path: str,
        *,
        method: str = "GET",
        data: object = None,
        pages: bool = False,
    ) -> Any:
        return self.now.rest(path, method=method, data=data, pages=pages)

    def _once(self, key: str, read: Callable[[], Any]) -> Any:
        if key not in self.memo:
            self.memo[key] = read()
        return deepcopy(self.memo[key])

    def _before(
        self, items: Iterable[dict[str, Any]], key: str = "created_at"
    ) -> list[dict[str, Any]]:
        return [item for item in items if moment(item[key]) <= self.sample.moment]

    def _as_then(self, issue: dict[str, Any]) -> dict[str, Any]:
        closed = issue.get("closed_at")
        if closed and moment(closed) > self.sample.moment:
            return issue | {
                "state": "open",
                "state_reason": None,
                "closed_at": None,
                "closed_by": None,
            }
        return issue

    def issue(self, number: int) -> dict[str, Any]:
        if number == self.sample.number:
            return deepcopy(self.sample.then)
        return self._as_then(self.now.issue(number))

    def comments(self, number: int) -> list[dict[str, Any]]:
        return self._before(self.now.comments(number))

    def events(self, number: int) -> list[dict[str, Any]]:
        return self._before(self.now.events(number))

    def reactions(self, comment_id: int) -> list[dict[str, Any]]:
        return self._before(self.now.reactions(comment_id))

    def latest_comments(self, pages: int) -> list[dict[str, Any]]:
        return self._before(
            self._once(f"comments {pages}", lambda: self.now.latest_comments(pages))
        )

    def recent_issues(self, closed: int) -> list[dict[str, Any]]:
        found = self._once(f"issues {closed}", lambda: self.now.recent_issues(closed))
        return [self._as_then(item) for item in self._before(found)]

    def search(self, query: str) -> list[dict[str, Any]]:
        # Only issues of then, so that later ones push none of them out of the results.
        return self.now.search(f"{query} created:<={stamp(self.sample.moment)}")

    def sibling(self, repository: str) -> GitHub | None:
        # A related repository as it was at the moment too, so that its search finds
        # no issue opened later.
        other = self.now.sibling(repository)
        return None if other is None else Then(other, self.sample, {})

    def search_issues(self, terms: list[str]) -> list[dict[str, Any]]:
        return [
            self._as_then(item) for item in self._before(super().search_issues(terms))
        ]

    def discussions(self, most: int) -> list[dict[str, Any]]:
        found = self._once(f"discussions {most}", lambda: self.now.discussions(most))
        return [
            item | {"answer": None, "isAnswered": False}
            if item.get("answer")
            and moment(item["answer"]["createdAt"]) > self.sample.moment
            else item
            for item in self._before(found, "createdAt")
        ]

    def latest_release(self) -> str | None:
        found = self._before(self._once("releases", self.now.releases), "published_at")
        return str(found[-1]["tag_name"]) if found else None


@dataclass(frozen=True)
class Answered:
    """The answer of an engine's command line, and what its run cost, if it says."""

    text: str
    cost: float | None = None


@dataclass(frozen=True)
class Outcome:
    """What the replay of a sample chose, or why it failed."""

    sample: Sample
    labels: dict[str, frozenset[str]] = field(default_factory=dict)
    # The older issues that it named as duplicates, as the analysis keeps them, and
    # the one that gets the notice that closes the issue, if any.
    duplicates: tuple[int, ...] = ()
    sure: int | None = None
    asks: bool = False
    cost: float | None = None
    error: str = ""


def chosen_labels(answer: dict[str, Any], config: Config) -> dict[str, frozenset[str]]:
    """The labels of each scored group that an answer gives: its kind, unless it is
    spam, its areas and topics, and its priority, unless it chose none."""
    return {
        "type": frozenset({answer["kind"]}.intersection(config.group("type"))),
        "area": frozenset(answer["areas"]),
        "topic": frozenset(answer["topics"]),
        "priority": frozenset({answer.get("priority", NO_PRIORITY)} - {NO_PRIORITY}),
    }


def named_duplicates(
    then: GitHub, number: int, answer: dict[str, Any]
) -> tuple[tuple[int, ...], int | None]:
    """The duplicates of an answer as check_findings keeps them: each older issue
    once, and the first sure one whose original was open and vouched for, which
    gets the notice that closes the issue."""
    named: list[int] = []
    sure = None
    for candidate in answer["duplicates"]:
        original = other_issue(then, candidate["number"], number)
        if not original or original["number"] > number or original["number"] in named:
            continue
        named.append(original["number"])
        if (
            sure is None
            and candidate["confidence"] == "high"
            and original["state"] == "open"
            and vouched_for(original)
        ):
            sure = original["number"]
    return tuple(named), sure


def replay(
    github: GitHub,
    config: Config,
    samples: list[Sample],
    answer: Callable[[], Answered],
) -> list[Outcome]:
    """The first analysis of each sample as at its moment: its context written by
    Then, the engine's answer from `answer`, and what that answer chose.

    The engine reads the context folder of the checkout, as in the workflow. A
    sample whose context, engine or answer fails is an outcome with its error, and
    the others go on.
    """
    memo: dict[str, Any] = {}
    outcomes = []
    for index, sample in enumerate(samples, start=1):
        place = f"{index} of {len(samples)}, #{sample.number}"
        then = Then(github, sample, memo)
        try:
            write_context(then, sample.number, "triage", config)
            answered = answer()
            chosen = parse_answer("triage", answered.text, config)
            named, sure = named_duplicates(then, sample.number, chosen)
        except (AssistantError, GitHubError) as error:
            reason = one_line(str(error))
            print(f"{place}: failed, {reason}")
            outcomes.append(Outcome(sample, error=reason))
            continue
        print(f"{place}: answered")
        outcomes.append(
            Outcome(
                sample,
                chosen_labels(chosen, config),
                named,
                sure,
                bool(questions(chosen, config)),
                answered.cost,
            )
        )
    return outcomes


@dataclass(frozen=True)
class Score:
    """A measure of the replays: over how many issues it counts, how many choices
    the assistant made, how many the maintainers made, and how many of them agree."""

    name: str
    issues: int
    chosen: int
    actual: int
    right: int


def scores(outcomes: list[Outcome], config: Config) -> list[Score]:
    """The precision and recall of each scored group of labels that labels.toml
    has, of the duplicates, of the sure ones alone, and of the questions for more
    information.

    A group counts the issues that people gave a label of it, since an issue
    without an area says nothing about the areas; the duplicates count the issues
    closed as a duplicate of an older one, and a duplicate named for any other is a
    wrong one; the questions count every issue.
    """
    answered = [outcome for outcome in outcomes if not outcome.error]
    found = []
    for group, name in SCORED_GROUPS.items():
        if not config.group(group):
            continue
        judged = [outcome for outcome in answered if outcome.sample.labels[group]]
        found.append(
            Score(
                name,
                len(judged),
                sum(len(outcome.labels[group]) for outcome in judged),
                sum(len(outcome.sample.labels[group]) for outcome in judged),
                sum(
                    len(outcome.labels[group] & outcome.sample.labels[group])
                    for outcome in judged
                ),
            )
        )
    originals = sum(outcome.sample.original is not None for outcome in answered)
    for name, sure in ((MEASURES[-3], False), (MEASURES[-2], True)):
        named = [
            (outcome, number)
            for outcome in answered
            for number in (
                ((outcome.sure,) if outcome.sure else ())
                if sure
                else outcome.duplicates
            )
        ]
        right = sum(number == outcome.sample.original for outcome, number in named)
        found.append(Score(name, originals, len(named), originals, right))
    found.append(
        Score(
            MEASURES[-1],
            len(answered),
            sum(outcome.asks for outcome in answered),
            sum(outcome.sample.asked for outcome in answered),
            sum(outcome.asks and outcome.sample.asked for outcome in answered),
        )
    )
    return found


def table_cell(text: str) -> str:
    """Text for a cell of a Markdown table: one line, whose | ends no cell."""
    return one_line(text).replace("|", "\\|")


# What the report says of the question for more information, by whether the
# assistant asked and whether the maintainers did.
ASKED = {
    (True, True): "asked, as the maintainers did",
    (True, False): "asked, the maintainers didn't",
    (False, True): "missed the maintainers' question",
    (False, False): "",
}


def outcome_row(outcome: Outcome, repository: str) -> str:
    """The line of the report's table for one replay."""
    sample = outcome.sample
    link = f"[#{sample.number}](https://github.com/{repository}/issues/{sample.number})"
    if outcome.error:
        return f"| {link} | failed: {table_cell(outcome.error)} | | | | |"

    def names(labels: Iterable[str]) -> str:
        return ", ".join(table_cell(code_span(name)) for name in sorted(labels))

    # The groups that count for the issue, as in scores.
    groups = [group for group in SCORED_GROUPS if sample.labels[group]]
    right = [name for g in groups for name in outcome.labels[g] & sample.labels[g]]
    wrong = [name for g in groups for name in outcome.labels[g] - sample.labels[g]]
    missed = [name for g in groups for name in sample.labels[g] - outcome.labels[g]]
    numbers = list(outcome.duplicates)
    named = ", ".join(f"#{number}" for number in numbers)
    if sample.original is None:
        duplicate = f"named {named}, of none" if numbers else ""
    elif sample.original in numbers:
        duplicate = f"found #{sample.original}"
    else:
        duplicate = f"missed #{sample.original}" + (f", named {named}" if named else "")
    return (
        f"| {link} | {names(right)} | {names(wrong)} | {names(missed)} | "
        f"{duplicate} | {ASKED[(outcome.asks, sample.asked)]} |"
    )


def render_evaluation(
    config: Config,
    engine: Engine,
    run: Run,
    day: date,
    outcomes: list[Outcome],
    read: int,
) -> str:
    """The report of `evaluate`: the precision and recall of each measure, what it
    replayed and how, and a line for each issue."""
    repository = config.repository
    failed = sum(bool(outcome.error) for outcome in outcomes)
    costs = [outcome.cost for outcome in outcomes if outcome.cost is not None]
    lines = [
        f"# How accurate the issue assistant is on {repository}",
        "",
        f"Measured on {day.isoformat()} with {engine.name}, {code_span(run.model)}, on "
        f"the {len(outcomes)} newest of {read} closed issues read that people "
        "labelled or closed as a duplicate of an older issue, and that the assistant "
        "never analyzed. It analyzed each as it was a minute after it was opened, "
        "with the issues, discussions and releases of that moment, and compared its "
        "choices with what the maintainers decided.",
        "",
        *MEASURES_HEAD,
        *(
            f"| {score.name} | {share(score.right, score.chosen)} | "
            f"{share(score.right, score.actual)} | {score.issues} |"
            for score in scores(outcomes, config)
        ),
        "",
        "*Precision* is the share of the assistant's choices that the maintainers "
        "made too, and *recall* the share of the maintainers' choices that the "
        "assistant made. A group of labels counts the issues that people gave a "
        "label of the group after they were opened, and a label that an issue had "
        "then, such as that of its form, counts for neither; the duplicates count the issues closed as a duplicate "
        "of an older one, and the sure ones only the duplicates that the assistant "
        "was sure of, which get the notice that closes them; the questions count "
        "every issue, and the maintainers asked when the first comment of one of "
        "them asked a question or someone added `needs-info`.",
    ]
    if failed:
        lines += [
            "",
            f"{failed} of the {len(outcomes)} analyses failed; they count in no "
            "measure.",
        ]
    if costs:
        lines += ["", f"The analyses cost ${sum(costs):.2f} at the prices of the API."]
    lines += [
        "",
        "## Issue by issue",
        "",
        "| Issue | Right | Wrong | Missed | Duplicate | Information |",
        "| --- | --- | --- | --- | --- | --- |",
        *(outcome_row(outcome, repository) for outcome in outcomes),
    ]
    return "\n".join(lines) + "\n"


def answer_with_schema(
    engine: Engine, command_line: list[str], run: Run, folder: Path, root: Path
) -> Answered:
    """The answer of an engine's command line that enforces the schema, as `local`
    in ENGINES names it, to the task of the context folder, with the arguments that
    its action gets in the workflow: the rules as the system prompt, the model, the
    limits of the task and the schema of the answer. The task goes in on standard
    input, which takes a text of any length on every system."""
    effort = ["--effort", run.effort] if run.effort else []
    result = subprocess.run(
        [
            *command_line,
            "--append-system-prompt-file",
            str(CONTEXT / "rules.md"),
            "--model",
            run.model,
            "--max-turns",
            str(run.turns),
            "--max-budget-usd",
            run.budget,
            *effort,
            "--json-schema",
            (folder / "schema.json").read_text(encoding="utf-8"),
        ],
        input=(folder / "prompt.md").read_text(encoding="utf-8"),
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=ENGINE_MINUTES * 60,
        check=False,
    )
    try:
        printed = json.loads(result.stdout)
    except ValueError:
        printed = None
    # The result, as the execution file of the action holds it last.
    report = printed if isinstance(printed, dict) else {}
    answer = report.get("structured_output")
    if result.returncode or not isinstance(answer, dict):
        subtype = str(report.get("subtype"))
        # A run that stopped at a limit of its task.
        if subtype in OUTCOMES and subtype != "success":
            raise AssistantError(
                f"{engine.name} {OUTCOMES[subtype]}, before it answered."
            )
        message = one_line(str(report.get("result") or result.stderr))[-300:]
        raise AssistantError(f"{engine.name} failed: {message or 'no answer'}")
    return Answered(json.dumps(answer), number_of(report.get("total_cost_usd")))


def evaluated_config(config: Config, engine: str | None, model: str | None) -> Config:
    """The settings of an evaluation: those of config.toml, with the engine and the
    model that `evaluate` names. Another engine than that of config.toml runs its
    own default model and effort, unless a model is named."""
    if engine and engine != engine_named(config.engine):
        config = replace(config, engine=engine, model="", effort="")
    return replace(config, model=model) if model else config


def local_engine(config: Config, work: Path) -> Callable[[], Answered]:
    """What answers the task of the context folder in `evaluate`: the command line
    of the engine of `config` on this computer, as you would run it, logged in as
    you, with only its tools that read the checkout."""
    key = engine_named(config.engine)
    if key not in LOCAL_ENGINES:
        raise AssistantError(
            f"evaluate runs the engines {', '.join(LOCAL_ENGINES)}, not {key}."
        )
    engine = ENGINES[key]
    run = task_run(config, "triage")
    folder = config.root / CONTEXT
    cli = engine.cli
    name = cli.command if cli else engine.local[0]
    command_path = shutil.which(name)
    if command_path is None:
        raise AssistantError(
            f"evaluate runs the command line of {engine.name}, {name}, which isn't "
            "on the PATH of this computer."
        )
    found: str = command_path
    if os.name == "nt" and Path(found).suffix.lower() in {".cmd", ".bat"}:
        # cmd.exe cuts an argument at its first line break and refuses a command
        # line beyond 8191 characters, and the task is longer.
        raise AssistantError(
            f"evaluate can't run {found}: Windows passes the task to a .cmd or .bat "
            f"file through cmd.exe, which cuts it. Install {engine.name} as a "
            "program of its own, or run evaluate in WSL."
        )
    if cli and cli.root_settings and not hasattr(os, "geteuid"):
        # Without root, the command line would run without its settings.
        raise AssistantError(
            f"evaluate runs {engine.name} only on Linux, macOS or WSL: it takes the "
            "settings that keep it to reading only from a file of root's."
        )

    def answer() -> Answered:
        try:
            if cli is None:
                return answer_with_schema(
                    engine, [found, *engine.local[1:]], run, folder, config.root
                )
            prompt = "\n\n".join(
                (folder / file).read_text(encoding="utf-8")
                for file in ("rules.md", "prompt.md")
            )
            variables = os.environ | cli.variables
            with ExitStack() as stack:
                if cli.settings_variable:
                    settings = stack.enter_context(
                        placed_settings(cli, json.dumps(cli_settings(cli, run)), work)
                    )
                    variables[cli.settings_variable] = str(settings)
                result = subprocess.run(
                    cli_command(cli, found, run, prompt),
                    cwd=config.root,
                    env=variables,
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
        except OSError as error:
            raise AssistantError(f"{engine.name} didn't start: {error}") from None
        return Answered(cli_answer(engine, cli, result))

    return answer


def evaluate(
    github: GitHub,
    config: Config,
    count: int,
    report: Path,
    answer: Callable[[], Answered],
    day: date,
) -> list[str]:
    """Replay the first analysis of the `count` closed issues opened last that
    people labelled or closed as a duplicate, write the report, and return the
    lines of the summary: each measure, and where the report is."""
    if not 1 <= count <= MOST_EVALUATED:
        raise AssistantError(
            f"--issues must be a whole number from 1 to {MOST_EVALUATED}, not {count}."
        )
    # The replays read again the comments and events that the choice read.
    github.remember()
    samples, read = choose_samples(github, config, count)
    if not samples:
        raise AssistantError(
            f"None of the {read} closed issues read has a label that people chose or "
            "the original of a duplicate, without an analysis of the assistant."
        )
    outcomes = replay(github, config, samples, answer)
    engine = ENGINES[engine_named(config.engine)]
    run = task_run(config, "triage")
    report.write_text(
        render_evaluation(config, engine, run, day, outcomes, read), encoding="utf-8"
    )
    failed = sum(bool(outcome.error) for outcome in outcomes)
    return [
        f"{score.name}: precision {share(score.right, score.chosen)}, recall "
        f"{share(score.right, score.actual)}, over {plural(score.issues, 'issue')}"
        for score in scores(outcomes, config)
    ] + [
        f"Replayed {len(outcomes)} of the {read} closed issues read with "
        f"{engine.name}, {run.model}; {failed} failed.",
        f"The report is in {report}.",
    ]
