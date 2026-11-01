"""The settings of a repository: .github/labels.toml, config.toml and findings.toml."""

from __future__ import annotations

import tomllib
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .constants import EFFORTS, LOGIN, MAX_ASSIGNEES, MAX_RELATED, REPOSITORY_NAME
from .engines import (
    DEFAULT_ENGINE,
    ENGINES,
    Run,
    engine_named,
    is_model_name,
    model_problem,
)
from .errors import AssistantError, SettingsError
from .languages import LOCALES, plural_form, say
from .paths import FINDINGS_FILE, LABELS_FILE, SETTINGS
from .values import command, did_you_mean, fits

# The periods of the lifecycle are in Lifecycle, which [lifecycle] in config.toml
# sets. None is longer than this many days, ten years, which keeps every date that
# the sweep works out within the calendar of Python.
MAX_DAYS = 3650

MAX_PER_DAY = 20
MAX_PER_AUTHOR_PER_DAY = 3
RELEASE_REPLIES = 2

# What one run of the engine may spend, unless [engine] in config.toml sets more or
# less: the turns of a first analysis and of a maintainer's question, of which the
# other tasks take a part, and the dollars of one run, at which the engine stops.
# More turns than MOST_TURNS or dollars than MOST_BUDGET_USD are a typo, not a limit.
MAX_TURNS = 40
MOST_TURNS = 200
MAX_BUDGET_USD = 1.0
MOST_BUDGET_USD = 100.0
# The part of max_turns that each task takes, rounded up: all of them, half for a
# follow-up, which reads the reporter's answers to known questions, and a tenth for
# a reply to one comment, which reads two files.
TURN_DIVISORS = {
    "triage": 1,
    "follow-up": 2,
    "maintainer-reply": 10,
    "release-reply": 10,
    "maintainer-question": 1,
    "discussion-answer": 1,
}
# The fewest turns of any task, unless max_turns itself is fewer: a reply reads two
# files and then answers, three turns in a measured run, and one more to spare.
MIN_TASK_TURNS = 4
# The tasks that only judge one comment, yes or no; light_model runs them.
LIGHT = ("maintainer-reply", "release-reply")

# What the engine reads of the rest of the repository, unless [context] in
# config.toml sets other numbers: every open issue, the closed issues and the
# discussions that changed last, up to these numbers. So the context and the calls
# to GitHub stay bounded in a repository with tens of thousands of issues.
MAX_ISSUES = 1_000
MAX_DISCUSSIONS = 1_000
# A first analysis also learns from the maintainers' corrections of the analyses of
# this many issues, those analyzed last; see learned_corrections.
MAX_ANALYZED = 30

DIGEST_TITLE = "Triage digest"


def engine_problems(config: Config) -> list[str]:
    """The values of [engine] that the engine can't run with; `check` names them,
    and the engine command stops on them."""
    where = SETTINGS / "config.toml"
    problems = [
        model_problem(model, key)
        for key in ("model", "light_model")
        if (model := getattr(config, key)) and not is_model_name(model)
    ]
    turns = config.max_turns
    if not fits(turns, int) or not 1 <= turns <= MOST_TURNS:
        problems.append(f"{where}: max_turns must be a whole number, 1 to {MOST_TURNS}")
    budget = config.max_budget_usd
    # TOML has nan and inf, which no comparison lets through.
    if not fits(budget, float) or not 0 < budget <= MOST_BUDGET_USD:
        problems.append(
            f"{where}: max_budget_usd must be a number of dollars, more than 0 and "
            f"at most {MOST_BUDGET_USD:g}"
        )
    if config.effort and config.effort not in EFFORTS:
        problems.append(
            did_you_mean(
                f"{where}: unknown effort {config.effort!r}",
                str(config.effort),
                EFFORTS,
            )
        )
    elif (
        config.effort
        and (engine := ENGINES.get(config.engine))
        and config.effort not in engine.efforts
    ):
        takes = ", ".join(engine.efforts) or "none, so leave it out"
        problems.append(
            f"{where}: {engine.name} takes no effort {config.effort!r}; it takes {takes}"
        )
    return problems


def task_run(config: Config, mode: str | None) -> Run:
    """The model, turns, budget and effort of the engine for a task.

    A reply to one comment runs light_model when it is set, and every task takes its
    part of max_turns, but no fewer than MIN_TASK_TURNS. Without a task, as from a template before the tasks had
    their own limits, it is the run of a first analysis.
    """
    problems = engine_problems(config)
    if problems:
        raise SettingsError(problems)
    task = mode or "triage"
    model = (task in LIGHT and config.light_model) or config.model
    divisor = TURN_DIVISORS[task]
    return Run(
        model=model or ENGINES[engine_named(config.engine)].model,
        turns=max(
            min(MIN_TASK_TURNS, config.max_turns), -(-config.max_turns // divisor)
        ),
        budget=f"{float(config.max_budget_usd):g}",
        effort=config.effort,
    )


@dataclass(frozen=True)
class Label:
    name: str
    color: str
    description: str
    group: str
    form_options: tuple[str, ...] = ()
    # For a kind: whether the assistant may ask the reporter for information.
    ask: bool = True
    # For an area: the logins of the people that an issue of the area is assigned to.
    owners: tuple[str, ...] = ()
    # For a kind: the issue type of the repository that issues of the kind get.
    issue_type: str = ""


@dataclass(frozen=True)
class Acceptance:
    """A finding of code scanning that the repository accepts, with its reason."""

    tool: str
    rule: str
    reason: str
    comment: str
    # Only alerts in this file or in the files of this folder; empty for every file.
    path: str = ""

    def covers(self, alert: dict[str, Any]) -> bool:
        location = (alert.get("most_recent_instance") or {}).get("location") or {}
        path = str(location.get("path") or "")
        # A folder covers its files, not every name that starts alike: tests covers
        # tests/test_a.py, but not testsuite/test_a.py.
        folder = self.path.rstrip("/")
        return (
            (alert.get("tool") or {}).get("name", "").casefold() == self.tool.casefold()
            and (alert.get("rule") or {}).get("id") == self.rule
            and (not folder or path == folder or path.startswith(f"{folder}/"))
        )


@dataclass(frozen=True)
class Lifecycle:
    """The periods and the exemptions of the lifecycle, from [lifecycle] in
    config.toml. The waiting for a reporter counts days of the calendar in UTC, see
    over(); lifecycle_problems() names the values that the sweep can't use."""

    # A reminder this many days after the last question without an answer, and the
    # closing this many days after it. The reminder names the day the issue closes,
    # the difference of the two later, also when it came late, for example after
    # the workflow was switched off.
    remind_after_days: int = 15
    close_after_days: int = 30
    # A sure duplicate closes this many days after the notice, unless someone
    # objects.
    duplicate_grace_days: int = 3
    # How long a comment of the reporter can reopen an issue that a release closed.
    reopen_after_release_days: int = 30
    # How often the AI follows up on the reporter's answers to its questions.
    max_follow_ups: int = 2
    # Issues that the sweep never reminds, closes or locks: those with one of these
    # labels, in any case, those assigned to someone when exempt_assignees is true,
    # and those with at least exempt_upvotes 👍, unless it is 0.
    exempt_labels: tuple[str, ...] = ("pinned", "security")
    exempt_assignees: bool = False
    exempt_upvotes: int = 0
    # The sweep locks issues closed for more than this many days, and after the
    # reopen window of a release; 0 locks none.
    lock_closed_after_days: int = 0

    @property
    def warning_days(self) -> int:
        """The days from the reminder to the closing that it names."""
        return self.close_after_days - self.remind_after_days

    def text(self, language: str) -> dict[str, str]:
        """The periods as the comments name them, such as "15 days"."""
        return {
            "remind_days": days(self.remind_after_days, language),
            "close_days": days(self.close_after_days, language),
            "grace_days": days(self.duplicate_grace_days, language),
            "reopen_days": days(self.reopen_after_release_days, language),
            "lock_days": days(self.lock_closed_after_days, language),
        }


DEFAULT_LIFECYCLE = Lifecycle()


def days(count: int, language: str) -> str:
    """A number of days in the words of a comment, in the form that its texts need:
    German needs the dative, as after "nach", "in", "seit" and "vor", and Russian
    and Ukrainian the form that the number asks for, as in 2 дня and 5 дней."""
    return say(language, f"days.{plural_form(count, language)}", count=count)


def lifecycle_problems(lifecycle: Lifecycle) -> list[str]:
    """What the sweep can't work with in the periods of [lifecycle]."""
    where = f"{SETTINGS / 'config.toml'}: [lifecycle]"
    problems = []
    for name, least in (
        ("remind_after_days", 1),
        ("close_after_days", 2),
        ("duplicate_grace_days", 1),
        ("reopen_after_release_days", 1),
        ("max_follow_ups", 0),
        ("exempt_upvotes", 0),
        ("lock_closed_after_days", 0),
    ):
        value = getattr(lifecycle, name)
        if not least <= value <= MAX_DAYS:
            problems.append(f"{where} {name} must be {least} to {MAX_DAYS}")
    if lifecycle.close_after_days <= lifecycle.remind_after_days:
        problems.append(
            f"{where} close_after_days must be more than remind_after_days, so that "
            "the reminder comes before the closing"
        )
    if 0 < lifecycle.lock_closed_after_days < lifecycle.reopen_after_release_days:
        problems.append(
            f"{where} lock_closed_after_days must be 0 or at least "
            "reopen_after_release_days, so that a reporter can still say that a "
            "release didn't fix their issue"
        )
    return problems


@dataclass(frozen=True)
class Config:
    """What the assistant knows about the repository it looks after."""

    root: Path
    repository: str
    labels: tuple[Label, ...]
    project: str
    engine: str = DEFAULT_ENGINE
    # What a run of the engine may use; see Run and task_run.
    model: str = ""
    light_model: str = ""
    effort: str = ""
    max_turns: int = MAX_TURNS
    max_budget_usd: float = MAX_BUDGET_USD
    hosts: frozenset[str] = frozenset()
    area_field: str = "Area"
    unmapped_options: tuple[str, ...] = ("Other",)
    # The box of the forms that keeps the AI out of an issue; see box_ticked.
    opt_out_field: str = "Automated analysis"
    notices: tuple[str, ...] = ("SUPPORT.md",)
    # Whether the footers of the AI's comments link the project; see invitation.
    credit: bool = True
    # Whether a fixed issue stays open until a release ships the fix.
    releases: bool = True
    # The limits of the AI runs; see LIMITS.
    max_per_day: int = MAX_PER_DAY
    max_per_author_per_day: int = MAX_PER_AUTHOR_PER_DAY
    release_replies: int = RELEASE_REPLIES
    lifecycle: Lifecycle = DEFAULT_LIFECYCLE
    # What the engine reads of the other issues and the discussions; see
    # CONTEXT_LIMITS.
    max_issues: int = MAX_ISSUES
    max_discussions: int = MAX_DISCUSSIONS
    max_analyzed: int = MAX_ANALYZED
    # Public repositories that share users and topics with this one, as owner/name:
    # a first analysis searches their issues too; see siblings.
    related_repositories: tuple[str, ...] = ()
    # The categories of GitHub Discussions whose new discussions get a first answer;
    # none answers no discussion. See answers_category.
    answer_categories: tuple[str, ...] = ()
    # The language that the maintainers read, the code of a catalog in locales/: a
    # comment in another one folds a summary for them in it. See maintainers.
    maintainer_language: str = "en"
    # Whether the morning sweep also keeps its digest in one issue, and the title of
    # that issue; see keep_digest_issue.
    digest_issue: bool = False
    digest_title: str = DIGEST_TITLE
    # What `check` reports of labels.toml and config.toml beyond its own checks: the
    # values that the assistant can't use and the keys that it doesn't know.
    problems: tuple[str, ...] = ()

    @property
    def blob(self) -> str:
        return f"https://github.com/{self.repository}/blob"

    @property
    def policy(self) -> str:
        return f"https://github.com/{self.repository}/security/policy"

    def group(self, name: str) -> list[str]:
        return [label.name for label in self.labels if label.group == name]

    def asking_kinds(self) -> set[str]:
        return {
            label.name for label in self.labels if label.group == "type" and label.ask
        }

    def owners(self, names: Collection[str]) -> list[str]:
        """The owners of the areas among `names`, each once, in the order of
        labels.toml, and as many as GitHub assigns to one issue; `check` names an
        owner that is no login, which is left out."""
        found = [
            owner
            for label in self.labels
            if label.group == "area" and label.name in names
            for owner in label.owners
            if LOGIN.fullmatch(owner)
        ]
        return list(dict.fromkeys(found))[:MAX_ASSIGNEES]

    def issue_type(self, kind: str) -> str:
        """The issue type of a kind; empty when it has none."""
        return next(
            (
                label.issue_type
                for label in self.labels
                if label.group == "type" and label.name == kind
            ),
            "",
        )


@dataclass(frozen=True)
class Key:
    """A key of config.toml: the attribute of Config that it sets, such as
    lifecycle.max_follow_ups for one of Config.lifecycle, and its type."""

    attribute: str
    # str, bool, int for a whole number, float for any number, or list for a list
    # of strings.
    kind: type
    # The value of the attribute, from the value in the file.
    convert: Callable[[Any], Any] = lambda value: value


# Every key of config.toml, by its section. A key that isn't here is a typo, which
# would keep the default unnoticed.
CONFIG_KEYS: dict[str, dict[str, Key]] = {
    "engine": {
        "name": Key("engine", str),
        "model": Key("model", str),
        "light_model": Key("light_model", str),
        "effort": Key("effort", str),
        "max_turns": Key("max_turns", int),
        "max_budget_usd": Key("max_budget_usd", float, float),
        "max_per_day": Key("max_per_day", int),
        "max_per_author_per_day": Key("max_per_author_per_day", int),
        "release_replies": Key("release_replies", int),
    },
    "context": {
        "max_issues": Key("max_issues", int),
        "max_discussions": Key("max_discussions", int),
        "max_analyzed": Key("max_analyzed", int),
    },
    "related": {"repositories": Key("related_repositories", list, tuple)},
    "discussions": {"answer_categories": Key("answer_categories", list, tuple)},
    "maintainers": {"language": Key("maintainer_language", str)},
    "links": {"hosts": Key("hosts", list, frozenset)},
    "forms": {
        "area_field": Key("area_field", str),
        "unmapped_options": Key("unmapped_options", list, tuple),
        "opt_out_field": Key("opt_out_field", str),
    },
    "transparency": {
        "files": Key("notices", list, tuple),
        "credit": Key("credit", bool),
    },
    "releases": {"close_with_release": Key("releases", bool)},
    # The attributes of Config.lifecycle.
    "lifecycle": {
        "remind_after_days": Key("lifecycle.remind_after_days", int),
        "close_after_days": Key("lifecycle.close_after_days", int),
        "duplicate_grace_days": Key("lifecycle.duplicate_grace_days", int),
        "reopen_after_release_days": Key("lifecycle.reopen_after_release_days", int),
        "max_follow_ups": Key("lifecycle.max_follow_ups", int),
        "exempt_labels": Key("lifecycle.exempt_labels", list, tuple),
        "exempt_assignees": Key("lifecycle.exempt_assignees", bool),
        "exempt_upvotes": Key("lifecycle.exempt_upvotes", int),
        "lock_closed_after_days": Key("lifecycle.lock_closed_after_days", int),
    },
    "digest": {
        "issue": Key("digest_issue", bool),
        "title": Key("digest_title", str),
    },
}
# The keys of a [[label]] of labels.toml with their types; the first four are needed.
LABEL_KEYS: dict[str, type] = {
    "name": str,
    "color": str,
    "description": str,
    "group": str,
    "form_options": list,
    "ask": bool,
    "owners": list,
    "issue_type": str,
}
NEEDED_LABEL_KEYS = ("name", "color", "description", "group")
# The keys of an [[accept]] of findings.toml; all but path are needed.
ACCEPT_KEYS = ("tool", "rule", "reason", "comment", "path")
KINDS = {
    str: "a string",
    bool: "true or false",
    int: "a whole number",
    float: "a number",
    list: "a list of strings",
}


@dataclass
class Problems:
    """What reading the settings found."""

    # Values that the assistant can't use, which stop every command.
    wrong: list[str] = field(default_factory=list)
    # Keys that it doesn't know, which change nothing.
    unknown: list[str] = field(default_factory=list)


def read_toml(path: Path, where: PurePosixPath) -> dict[str, Any]:
    """A settings file; one that isn't TOML stops every command."""
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        raise SettingsError([f"{where} is no valid TOML: {error}"]) from None


def load_labels(path: Path, problems: Problems) -> tuple[Label, ...]:
    """The labels of labels.toml; a label that can't be used is left out."""
    data = read_toml(path, LABELS_FILE)
    for name in data:
        if name in LABEL_KEYS:
            problems.unknown.append(f"{LABELS_FILE}: {name} belongs in a [[label]]")
        elif name != "label":
            problems.unknown.append(
                did_you_mean(f"{LABELS_FILE}: unknown key {name}", name, ["label"])
            )
    items = data.get("label", [])
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        problems.wrong.append(
            f"{LABELS_FILE}: each label must be a table, [[label]], with its keys "
            "below it"
        )
        return ()
    labels = []
    for number, item in enumerate(items, start=1):
        name = item.get("name")
        where = (
            f"{LABELS_FILE}: the label {name}"
            if isinstance(name, str) and name
            else f"{LABELS_FILE}: label {number}"
        )
        for key in item:
            if key not in LABEL_KEYS:
                problems.unknown.append(
                    did_you_mean(f"{where}: unknown key {key}", key, LABEL_KEYS)
                )
        missing = [key for key in NEEDED_LABEL_KEYS if key not in item]
        wrong = [
            key
            for key, kind in LABEL_KEYS.items()
            if key in item and not fits(item[key], kind)
        ]
        if missing:
            problems.wrong.append(f"{where} needs {', '.join(missing)}")
        for key in wrong:
            problems.wrong.append(f"{where}: {key} must be {KINDS[LABEL_KEYS[key]]}")
        if not missing and not wrong:
            labels.append(
                Label(
                    name=item["name"],
                    color=item["color"].lower(),
                    description=item["description"],
                    group=item["group"],
                    form_options=tuple(item.get("form_options", ())),
                    ask=item.get("ask", True),
                    owners=tuple(item.get("owners", ())),
                    issue_type=item.get("issue_type", ""),
                )
            )
    return tuple(labels)


def read_settings(path: Path, problems: Problems) -> dict[str, Any]:
    """The attributes of Config that config.toml sets; none without the file."""
    if not path.is_file():
        return {}
    where = SETTINGS / "config.toml"
    home = {name: section for section, keys in CONFIG_KEYS.items() for name in keys}
    values: dict[str, Any] = {}
    for section, table in read_toml(path, where).items():
        keys = CONFIG_KEYS.get(section)
        if keys is None and section in home:
            problems.unknown.append(
                f"{where}: {section} belongs in the section [{home[section]}]"
            )
        elif keys is None:
            problems.unknown.append(
                did_you_mean(
                    f"{where}: unknown section [{section}]",
                    section,
                    CONFIG_KEYS,
                    "[{}]",
                )
            )
        elif not isinstance(table, dict):
            problems.wrong.append(
                f"{where}: {section} must be a section, [{section}], with its keys "
                "below it"
            )
        else:
            for name, value in table.items():
                key = keys.get(name)
                if key is None and name in home:
                    problems.unknown.append(
                        f"{where}: {name} belongs in the section [{home[name]}], "
                        f"not in [{section}]"
                    )
                elif key is None:
                    problems.unknown.append(
                        did_you_mean(
                            f"{where}: unknown key {name} in [{section}]", name, keys
                        )
                    )
                elif not fits(value, key.kind):
                    problems.wrong.append(
                        f"{where}: [{section}] {name} must be {KINDS[key.kind]}"
                    )
                else:
                    values[key.attribute] = key.convert(value)
    return values


def load_config(root: Path, repository: str, *, lenient: bool = False) -> Config:
    """The settings of the repository in `root`; config.toml is optional.

    A file that isn't TOML stops every command. So does a value that the assistant
    can't use, such as a word where a number belongs, with one error that names each
    such value; only `check` reads the rest, `lenient`, and reports them with its
    other problems. A key that the assistant doesn't know changes nothing, so that a
    typo would keep a default unnoticed: `check` fails on it, and the other commands
    warn of it.
    """
    project_file = root / SETTINGS / "project.md"
    if not project_file.is_file() or not (root / LABELS_FILE).is_file():
        raise AssistantError(
            f"{LABELS_FILE} and {SETTINGS / 'project.md'} are needed; "
            "see https://dennis-otto.github.io/issue-assistant/getting-started.html."
        )
    try:
        project = project_file.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError as error:
        raise SettingsError(
            [f"{SETTINGS / 'project.md'} is no UTF-8 text: {error}"]
        ) from None
    problems = Problems()
    labels = load_labels(root / LABELS_FILE, problems)
    values = read_settings(root / SETTINGS / "config.toml", problems)
    if lenient:
        values["problems"] = (*problems.wrong, *problems.unknown)
    elif problems.wrong:
        raise SettingsError(problems.wrong)
    else:
        for problem in problems.unknown:
            command("warning", problem)
    # The keys of [lifecycle] set the attributes of Config.lifecycle.
    periods = {
        name: values.pop(f"lifecycle.{name}")
        for name in CONFIG_KEYS["lifecycle"]
        if f"lifecycle.{name}" in values
    }
    lifecycle = Lifecycle(**periods)
    # Comments, notices and the sweep all name these periods, so a period that the
    # sweep can't keep stops every command, not only the sweep.
    if not lenient and (wrong := lifecycle_problems(lifecycle)):
        raise SettingsError(wrong)
    return Config(
        lifecycle=lifecycle,
        root=root,
        repository=repository,
        labels=labels,
        project=project,
        **values,
    )


def limit(config: Config, key: str) -> int:
    """A limit of the AI runs, one of LIMITS, or of what it reads, one of
    CONTEXT_LIMITS.

    A value that is no whole number of 0 or more stops the run instead of lifting
    the limit; `check` names it before.
    """
    value = getattr(config, key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AssistantError(
            f"{SETTINGS / 'config.toml'}: {key} must be a whole number, 0 or more"
        )
    return value


def is_repository(name: str) -> bool:
    """Whether the name is one of a repository on GitHub, as owner/name, whose owner
    is a login."""
    return bool(
        REPOSITORY_NAME.fullmatch(name) and LOGIN.fullmatch(name.partition("/")[0])
    )


def answers_category(config: Config, category: str) -> bool:
    """Whether new discussions of the category get a first answer: it is one of
    [discussions] answer_categories, in any case, as GitHub names categories."""
    wanted = category.strip().casefold()
    return bool(wanted) and any(
        name.strip().casefold() == wanted for name in config.answer_categories
    )


def maintainers(config: Config) -> str:
    """The language of the maintainers, [maintainers] language: the code of a catalog
    in locales/, or English for a code without one, which `check` names."""
    code = config.maintainer_language
    return code if code in LOCALES else "en"


def siblings(config: Config) -> list[str]:
    """The related repositories of [related] that a first analysis searches: each a
    repository other than this one, once in any case, and the first MAX_RELATED;
    `check` names the others."""
    found: dict[str, str] = {}
    for name in config.related_repositories:
        key = name.casefold()
        if is_repository(name) and key != config.repository.casefold():
            found.setdefault(key, name)
    return list(found.values())[:MAX_RELATED]


def load_findings(root: Path) -> tuple[Acceptance, ...]:
    """The accepted findings of .github/findings.toml; none without the file.

    Only the findings command and the check read it, so a broken file never stops
    the work on issues. A key that it doesn't know stops them, since a typo such as
    `paths` would accept the findings of a rule in every file.
    """
    path = root / FINDINGS_FILE
    if not path.is_file():
        return ()
    data = read_toml(path, FINDINGS_FILE)
    try:
        items = data.get("accept", [])
        accepted = tuple(
            Acceptance(
                tool=str(item["tool"]),
                rule=str(item["rule"]),
                reason=str(item["reason"]),
                comment=str(item["comment"]),
                path=str(item.get("path", "")),
            )
            for item in items
        )
    except (KeyError, TypeError) as error:
        raise SettingsError(
            [
                f"{FINDINGS_FILE}: every [[accept]] needs tool, rule, reason and "
                f"comment ({type(error).__name__}: {error})"
            ]
        ) from error
    problems = [
        did_you_mean(f"{FINDINGS_FILE}: unknown key {name}", name, ["accept"])
        for name in data
        if name != "accept"
    ]
    for item, acceptance in zip(items, accepted, strict=True):
        where = f"{FINDINGS_FILE}: {acceptance.tool} {acceptance.rule}"
        problems += [
            did_you_mean(f"{where}: unknown key {key}", key, ACCEPT_KEYS)
            for key in item
            if key not in ACCEPT_KEYS
        ]
    if problems:
        raise SettingsError(problems)
    return accepted


def label_names(issue: dict[str, Any]) -> set[str]:
    return {label["name"] for label in issue.get("labels") or []}
