"""Checking a repository's set-up: everything that `check` reports."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .actionlint import load_yaml
from .constants import (
    ACTION,
    FIREWALL_RUNNER,
    FIXED,
    LIFECYCLE,
    LOGIN,
    MAX_ASSIGNEES,
    MAX_RELATED,
    NEEDS_INFO,
    NO_PRIORITY,
    POSSIBLE_DUPLICATE,
    RUNNER_VARIABLE,
    STALE,
)
from .engines import DEFAULT_ENGINE, ENGINES
from .errors import AssistantError, SettingsError
from .languages import LANGUAGES, LOCALES
from .paths import (
    CHOOSER_REPOSITORY,
    CONTEXT,
    DISCUSSION_WORKFLOW,
    FINDINGS_FILE,
    FIREWALL,
    FORMS,
    LABELS_FILE,
    SETTINGS,
    TEMPLATE_WORKFLOWS,
    TEMPLATES,
    VERSION_FILE,
    WORKFLOWS,
)
from .patterns import SHA
from .settings import (
    Config,
    Label,
    Lifecycle,
    engine_problems,
    is_repository,
    lifecycle_problems,
    limit,
    load_findings,
)
from .values import did_you_mean

HARDENED_RUNNER = re.compile(r"ubuntu-[0-9]{2}\.[0-9]{2}")
# The firewall allows the hosts of its policy and GitHub's own, whatever the port;
# Harden-Runner allows a host with its port: HTTPS, and HTTP, over which the
# archives of Ubuntu give the packages that the action of an engine installs. It
# allows the hosts of the runner itself, such as those of the logs, on its own.
FIREWALL_DEFAULT_HOSTS = ("github.com", "codeload.github.com")
ENDPOINT_PORTS = (443, 80)
# A host of the firewall policy, named in full: Harden-Runner reads its endpoints
# as words, so nothing but a host name may reach them, in small letters.
HOST_NAME = re.compile(
    r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+",
    re.IGNORECASE,
)

# The reasons GitHub accepts for dismissing a code scanning alert.
DISMISS_REASONS = ("false positive", "won't fix", "used in tests")

LIMITS = ("max_per_day", "max_per_author_per_day", "release_replies")

CONTEXT_LIMITS = ("max_issues", "max_discussions", "max_analyzed")

GROUPS = (
    "type",
    "area",
    "topic",
    "priority",
    "lifecycle",
    "decision",
    "contribution",
    "release",
    "dependabot",
)

# The longest label name that GitHub takes.
MAX_LABEL_NAME = 50
# project.md tells the AI what the project is, before every task: not the starter's
# placeholder, and at least this many characters.
MIN_PROJECT = 80

# The longest title of an issue that GitHub takes.
MAX_TITLE = 256


def normalized(node: Any, problems: list[str], where: str) -> Any:
    """A workflow without the refs of its actions, which must be commit hashes.

    PyYAML reads the key `on` as True and yq as "on"; both become "on".
    """
    if isinstance(node, dict):
        result = {}
        for key, value in node.items():
            name = "on" if key is True else key
            if name == "uses" and isinstance(value, str):
                action, _, ref = value.partition("@")
                if not SHA.fullmatch(ref):
                    problems.append(
                        f"{where}: pin {action} to a commit hash, not '{ref}'"
                    )
                result[name] = action
            else:
                result[name] = normalized(value, problems, where)
        return result
    if isinstance(node, list):
        return [normalized(item, problems, where) for item in node]
    return node


def check_labels(config: Config) -> list[str]:
    problems = []
    # GitHub ignores the case of label names: bug and Bug are one label for it.
    spellings: dict[str, list[str]] = {}
    for label in config.labels:
        spellings.setdefault(label.name.casefold(), []).append(label.name)
    for names in sorted(spellings.values()):
        if len(set(names)) > 1:
            problems.append(
                f"{LABELS_FILE}: the labels {', '.join(dict.fromkeys(names))} differ "
                "only in case, which GitHub ignores"
            )
        elif len(names) > 1:
            problems.append(f"{LABELS_FILE}: the label {names[0]} is defined twice")
    for label in config.labels:
        if not 0 < len(label.name) <= MAX_LABEL_NAME:
            problems.append(
                f"{LABELS_FILE}: the name '{label.name}' has {len(label.name)} "
                f"characters; GitHub takes 1 to {MAX_LABEL_NAME}"
            )
        if not re.fullmatch(r"[0-9a-f]{6}", label.color):
            problems.append(f"{LABELS_FILE}: {label.name} needs a color like 'd73a4a'")
        if not 0 < len(label.description) <= 100:
            problems.append(
                f"{LABELS_FILE}: {label.name} needs a description of 1 to 100 characters"
            )
        if label.group not in GROUPS:
            problems.append(
                f"{LABELS_FILE}: {label.name} has the unknown group {label.group}"
            )
        if label.form_options and label.group not in {"type", "area", "topic"}:
            problems.append(
                f"{LABELS_FILE}: only kinds, areas and topics have form_options"
            )
        problems += check_owners(label)
        if label.issue_type and label.group != "type":
            problems.append(
                f"{LABELS_FILE}: only kinds have an issue_type, not {label.name}"
            )
        if label.issue_type != label.issue_type.strip():
            problems.append(
                f"{LABELS_FILE}: the issue_type of {label.name} starts or ends with "
                "a space"
            )
        if label.group == "priority" and label.name == NO_PRIORITY:
            problems.append(
                f"{LABELS_FILE}: no priority can be named {NO_PRIORITY}, the answer "
                "of the AI for an issue without one"
            )
    lifecycle = {label.name for label in config.labels if label.group == "lifecycle"}
    for name in LIFECYCLE:
        if name == FIXED and config.releases is False:
            continue
        if name not in lifecycle:
            problems.append(f"{LABELS_FILE}: the lifecycle label {name} is missing")
    if not config.group("type"):
        problems.append(f"{LABELS_FILE}: at least one label needs the group type")
    return problems


def check_owners(label: Label) -> list[str]:
    """The problems of the owners of a label: only an area has them, each a login
    of GitHub, and no more than GitHub assigns to one issue."""
    if not label.owners:
        return []
    if label.group != "area":
        return [f"{LABELS_FILE}: only areas have owners, not {label.name}"]
    problems = []
    for owner in label.owners:
        if owner.startswith("@") and LOGIN.fullmatch(owner[1:]):
            problems.append(
                f"{LABELS_FILE}: write the owner {owner} of {label.name} without @"
            )
        elif not LOGIN.fullmatch(owner):
            problems.append(
                f"{LABELS_FILE}: the owner '{owner}' of {label.name} is no login of "
                "GitHub"
            )
    if len(label.owners) > MAX_ASSIGNEES:
        problems.append(
            f"{LABELS_FILE}: {label.name} has {len(label.owners)} owners; GitHub "
            f"assigns at most {MAX_ASSIGNEES} people to an issue"
        )
    return problems


def check_forms(config: Config) -> list[str]:
    problems = []
    defined = {label.name for label in config.labels}
    options: list[str] = []
    offered = False
    for path in sorted((config.root / FORMS).glob("*.y*ml")):
        where = path.relative_to(config.root).as_posix()
        try:
            form = load_yaml(path, where)
        except SettingsError as error:
            problems += error.problems
            continue
        if path.stem == "config":
            if CHOOSER_REPOSITORY in str(form):
                problems.append(
                    f"{where}: replace {CHOOSER_REPOSITORY} in its links with the "
                    "owner and name of the repository; install does it where the "
                    "remote origin names the repository"
                )
            continue
        if not isinstance(form, dict):
            problems.append(f"{where} is no issue form")
            continue
        names = form.get("labels") or []
        # GitHub takes a list of names or one text of them separated by commas.
        if isinstance(names, str):
            names = [name.strip() for name in names.split(",") if name.strip()]
        for name in names:
            if name not in defined:
                problems.append(f"{where}: the label {name} is not in {LABELS_FILE}")
        for item in form.get("body") or []:
            attributes = item.get("attributes") or {}
            if (
                item.get("type") == "dropdown"
                and attributes.get("label") == config.area_field
            ):
                options += attributes.get("options") or []
            if attributes.get("label") == config.opt_out_field:
                offered = True
                problems += opt_out_problems(where, item, config)
    # A repository that keeps the default name and has no such box offers no opt-out;
    # a name of its own that no form has is a mistake, and reporters would lose it.
    if not offered and config.opt_out_field != Config.opt_out_field:
        problems.append(
            f"{SETTINGS / 'config.toml'}: [forms] opt_out_field names "
            f"{config.opt_out_field!r}, which no form has"
        )
    for option in dict.fromkeys(options):
        owners = [label.name for label in config.labels if option in label.form_options]
        if len(owners) > 1:
            problems.append(
                f"{LABELS_FILE}: the form option '{option}' belongs to {', '.join(owners)}"
            )
        if not owners and option not in config.unmapped_options:
            problems.append(f"{LABELS_FILE}: no label has the form option '{option}'")
    for label in config.labels:
        for option in label.form_options:
            if option not in options:
                problems.append(
                    f"{LABELS_FILE}: {label.name} names the option '{option}', which no form has"
                )
    return problems


def opt_out_problems(where: str, item: dict[str, Any], config: Config) -> list[str]:
    """What keeps the field of [forms] opt_out_field in a form from being one box
    that a reporter may leave unticked."""
    field = f"{where}: the field {config.opt_out_field!r} of [forms] opt_out_field"
    boxes = (item.get("attributes") or {}).get("options") or []
    if item.get("type") != "checkboxes" or len(boxes) != 1:
        return [f"{field} must be checkboxes with one box"]
    if isinstance(boxes[0], dict) and boxes[0].get("required"):
        return [f"{field} must not be required, or every reporter would opt out"]
    return []


# A block of hosts in the firewall template that belongs to one engine starts with
# this comment; the other blocks hold the hosts that every engine needs.
ENGINE_BLOCK = re.compile(r" *# Engine ([a-z][a-z0-9]*):")


def firewall_policy(engine: str) -> str:
    """The firewall template for one engine: its header, the blocks of hosts that
    every engine needs and those of `engine`, each after a blank line."""
    template = (TEMPLATES / FIREWALL).read_text(encoding="utf-8")
    head, _, hosts = template.partition("\nallow:\n")
    blocks = [
        block.rstrip("\n")
        for block in hosts.split("\n\n")
        if (owner := ENGINE_BLOCK.match(block)) is None or owner[1] == engine
    ]
    return f"{head}\nallow:\n" + "\n\n".join(blocks) + "\n"


def allowed_hosts(policy: str) -> set[str]:
    """The hosts of a firewall policy in the form of the template."""
    return set(re.findall(r"(?m)^ +- (\S+)$", policy))


def runner_problems(runner: str) -> list[str]:
    """What keeps the runner that ISSUE_ASSISTANT_AI_RUNNER names from running the
    AI; nothing for none, for GitHub's egress-firewall runner and for one of
    GitHub's runners of Ubuntu with a fixed version."""
    if not runner or runner == FIREWALL_RUNNER or HARDENED_RUNNER.fullmatch(runner):
        return []
    return [
        f"{RUNNER_VARIABLE} names the runner {runner} for the AI, which runs only on "
        f"{FIREWALL_RUNNER} or on one of GitHub's runners of Ubuntu with a fixed "
        "version, such as ubuntu-24.04, where Harden-Runner blocks every host but "
        f"those of {FIREWALL}; on any other, such as a self-hosted runner, nothing "
        "would keep the AI to them. Unset the variable, or name such a runner."
    ]


def runner_trade_off(runner: str) -> str:
    """What the AI loses on `runner`, one of GitHub's runners of Ubuntu, against
    GitHub's egress-firewall runner."""
    return (
        f"The AI runs on {runner} in place of {FIREWALL_RUNNER}: Harden-Runner blocks "
        f"every host but those of {FIREWALL} for the job analyze, where GitHub's "
        "firewall blocks them for the whole machine, out of reach of every step. In "
        "a private repository it blocks only with a subscription of StepSecurity, "
        "and analyze stops where nothing blocks; decision 0019 says more."
    )


def firewall_endpoints(root: Path) -> str:
    """The hosts of the repository's firewall policy and GitHub's own, which the
    firewall allows without the policy, as the endpoints of Harden-Runner: each
    host with each port of ENDPOINT_PORTS."""
    path = root / FIREWALL
    if not path.is_file():
        raise AssistantError(f"{FIREWALL} is missing; {reinstall()}")
    policy = load_yaml(path, str(FIREWALL))
    hosts = policy.get("allow") if isinstance(policy, dict) else None
    if not isinstance(hosts, list) or not hosts:
        raise AssistantError(f"{FIREWALL} allows no host; {reinstall()}")
    wrong = [host for host in hosts if not full_host(host)]
    if wrong:
        raise SettingsError(
            [f"{FIREWALL}: name the host {host} in full" for host in wrong]
        )
    every = dict.fromkeys(host.lower() for host in [*FIREWALL_DEFAULT_HOSTS, *hosts])
    return " ".join(f"{host}:{port}" for host in every for port in ENDPOINT_PORTS)


def full_host(host: object) -> bool:
    """Whether `host` of a firewall policy is a host name in full, without a
    wildcard, a path or a port."""
    return isinstance(host, str) and HOST_NAME.fullmatch(host) is not None


def configured_engine(root: Path) -> str:
    """The engine that config.toml of a repository names; the default one without
    the file, or for one that names no engine that the assistant knows, which
    check reports."""
    try:
        settings = tomllib.loads((root / SETTINGS / "config.toml").read_text("utf-8"))
        name = settings["engine"]["name"]
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_ENGINE
    return name if name in ENGINES else DEFAULT_ENGINE


def check_workflows(config: Config) -> list[str]:
    problems: list[str] = []
    for name in TEMPLATE_WORKFLOWS:
        template = TEMPLATES / WORKFLOWS / name
        path = config.root / WORKFLOWS / name
        where = str(WORKFLOWS / name)
        if not path.is_file():
            if name != DISCUSSION_WORKFLOW or config.answer_categories:
                problems.append(f"{where} is missing; {reinstall()}")
            continue
        expected = normalized(load_yaml(template), [], template.name)
        actual = normalized(load_yaml(path, where), problems, where)
        if actual != expected:
            problems.append(
                f"{where} differs from the template of the issue assistant, and "
                f"only the refs of its actions may differ; {reinstall()}"
            )
    firewall = config.root / FIREWALL
    if not firewall.is_file():
        problems.append(f"{FIREWALL} is missing; {reinstall()}")
    else:
        policy = load_yaml(firewall, str(FIREWALL))
        engine = config.engine if config.engine in ENGINES else DEFAULT_ENGINE
        needed = allowed_hosts(firewall_policy(engine))
        hosts = set(policy.get("allow") or [])
        if policy.get("mode") != "enforce":
            problems.append(f"{FIREWALL}: mode must be enforce")
        if policy.get("no-default-urls"):
            problems.append(f"{FIREWALL}: the assistant needs GitHub's default hosts")
        for host in sorted(needed - hosts):
            problems.append(f"{FIREWALL}: the engine needs the host {host}")
        # Harden-Runner takes the same hosts on a runner without the firewall.
        for host in sorted(hosts):
            if not full_host(host):
                problems.append(f"{FIREWALL}: name the host {host} in full")
    ignore = config.root / ".gitignore"
    if not ignore.is_file() or f"/{CONTEXT}/" not in ignore.read_text("utf-8"):
        problems.append(f".gitignore: add /{CONTEXT}/")
    return problems


def digest_problems(config: Config) -> list[str]:
    """What GitHub would refuse as the title of the issue of the digest, which only
    the morning sweep would find out."""
    title = config.digest_title
    if title.strip() and len(title) <= MAX_TITLE and len(title.splitlines()) == 1:
        return []
    return [
        f"{SETTINGS / 'config.toml'}: [digest] title must be one line of 1 to "
        f"{MAX_TITLE} characters"
    ]


def check_config(config: Config) -> list[str]:
    problems = []
    if config.engine not in ENGINES:
        problems.append(
            f"{SETTINGS / 'config.toml'}: unknown engine {config.engine}; "
            f"known engines: {', '.join(sorted(ENGINES))}"
        )
    for host in sorted(config.hosts):
        if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", host):
            problems.append(f"{SETTINGS / 'config.toml'}: {host} is no host name")
    problems += related_problems(config)
    problems += digest_problems(config)
    problems += [
        f"{SETTINGS / 'config.toml'}: [discussions] answer_categories names "
        f"{name!r} {problem}"
        for name, problem in category_problems(config)
    ]
    if config.maintainer_language not in LOCALES:
        # A regional code, such as pt-BR or de_CH, means the language of its catalog.
        code = re.split(r"[-_]", config.maintainer_language.casefold())[0]
        problems.append(
            did_you_mean(
                f"{SETTINGS / 'config.toml'}: [maintainers] language "
                f"{config.maintainer_language!r} is no code of a catalog in locales/",
                code,
                LANGUAGES,
                '"{}"',
            )
        )
    problems += engine_problems(config)
    for key in (*LIMITS, *CONTEXT_LIMITS):
        try:
            limit(config, key)
        except AssistantError as error:
            problems.append(str(error))
    if config.engine in ENGINES:
        engine = ENGINES[config.engine]
        for name in config.notices:
            path = config.root / name
            if not path.is_file():
                problems.append(
                    f"{name} is missing; it tells reporters who reads their issue"
                )
                continue
            content = path.read_text(encoding="utf-8")
            if engine.name not in content or engine.vendor not in content:
                problems.append(
                    f"{name}: say that {engine.name} by {engine.vendor} reads the issues"
                )
    return problems


def category_problems(config: Config) -> list[tuple[str, str]]:
    """The names of [discussions] answer_categories that answer nothing: an empty
    one, which no category has, and one named before, in any case."""
    seen: set[str] = set()
    found = []
    for name in config.answer_categories:
        key = name.strip().casefold()
        if not key:
            found.append((name, "which is no category"))
        elif key in seen:
            found.append((name, "twice"))
        seen.add(key)
    return found


def related_problems(config: Config) -> list[str]:
    """The names of [related] that no analysis searches: no repository, this one, one
    named before, or one past the first MAX_RELATED."""
    where = SETTINGS / "config.toml"
    own = config.repository.casefold()
    problems, seen = [], set()
    for name in config.related_repositories:
        key = name.casefold()
        if not is_repository(name):
            problems.append(
                f"{where}: [related] {name} is no repository; name it as owner/name"
            )
        elif key == own:
            problems.append(f"{where}: [related] {name} is this repository")
        elif key in seen:
            problems.append(f"{where}: [related] names {name} twice")
        else:
            seen.add(key)
    if len(seen) > MAX_RELATED:
        problems.append(
            f"{where}: [related] names more than {MAX_RELATED} repositories; an "
            f"analysis searches the first {MAX_RELATED}"
        )
    return problems


# The lifecycle labels whose descriptions may name periods of [lifecycle], and
# those periods in the order of the starter of labels.toml.
LABEL_PERIODS: dict[str, Callable[[Lifecycle], tuple[int, ...]]] = {
    NEEDS_INFO: lambda lifecycle: (
        lifecycle.remind_after_days,
        lifecycle.close_after_days,
    ),
    STALE: lambda lifecycle: (lifecycle.warning_days,),
    POSSIBLE_DUPLICATE: lambda lifecycle: (lifecycle.duplicate_grace_days,),
}
NAMED_DAYS = re.compile(r"\b(\d+) days?\b")


def check_lifecycle(config: Config) -> list[str]:
    """The periods of [lifecycle] are usable, and the descriptions of the lifecycle
    labels, which GitHub shows to reporters, name no other days."""
    problems = lifecycle_problems(config.lifecycle)
    for label in config.labels:
        periods = LABEL_PERIODS.get(label.name)
        said = tuple(int(days) for days in NAMED_DAYS.findall(label.description))
        if periods is None or not said:
            continue
        expected = periods(config.lifecycle)
        if said != expected:
            problems.append(
                f"{LABELS_FILE}: {label.name} says "
                f"{' and '.join(map(str, said))} days, {SETTINGS / 'config.toml'} "
                f"says {' and '.join(map(str, expected))}"
            )
    return problems


def check_accepted_findings(config: Config) -> list[str]:
    try:
        accepted = load_findings(config.root)
    except SettingsError as error:
        return error.problems
    problems = []
    for item in accepted:
        where = f"{FINDINGS_FILE}: {item.tool} {item.rule}"
        if not item.tool.strip() or not item.rule.strip():
            problems.append(f"{FINDINGS_FILE}: name the tool and the rule")
        if item.reason not in DISMISS_REASONS:
            problems.append(
                f"{where}: the reason must be one of {', '.join(DISMISS_REASONS)}"
            )
        if not 0 < len(item.comment.strip()) <= 200:
            problems.append(f"{where}: explain it in a comment of 1 to 200 characters")
    return problems


def check_project(config: Config) -> list[str]:
    """project.md must tell the AI what the project is, which it reads first."""
    where = SETTINGS / "project.md"
    starter = (TEMPLATES / where).read_text(encoding="utf-8").splitlines()[0]
    if starter in config.project or "ONE SENTENCE" in config.project:
        return [
            f"{where}: replace the starter's first line with what the project does "
            "and for whom; the AI reads this file before every task"
        ]
    if len(config.project) < MIN_PROJECT:
        return [
            f"{where}: describe the project in at least {MIN_PROJECT} characters: "
            "what it does, for whom, and where its code and documentation are"
        ]
    return []


def check(config: Config) -> list[str]:
    """Everything that keeps the repository's set-up safe and consistent.

    The problems of reading the settings come first; `load_config` found them.
    """
    return (
        list(config.problems)
        + check_project(config)
        + check_labels(config)
        + check_forms(config)
        + check_workflows(config)
        + check_config(config)
        + check_lifecycle(config)
        + check_accepted_findings(config)
    )


def reinstall() -> str:
    """The commands that write the templates of this release anew, for `check`."""
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    return (
        "write the templates of this release anew with: "
        f'dir="$(mktemp -d)" && git clone --quiet --depth 1 --branch v{version} '
        f'https://github.com/{ACTION} "$dir" && '
        'PYTHONPATH="$dir" python3 -P -m issue_assistant install'
    )
