"""Checking a repository's set-up: everything that `check` reports."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path, PurePosixPath
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
from .issues import labels_of_form
from .languages import LANGUAGES, LOCALES, texts
from .paths import (
    ASSISTANT_WORKFLOW,
    CHOOSER_REPOSITORY,
    CONTEXT,
    DEPENDABOT_FILES,
    DISCUSSION_WORKFLOW,
    FAILURE_JOB,
    FAILURE_WORKFLOW,
    FINDINGS_FILE,
    FIREWALL,
    FORMS,
    LABELS_FILE,
    PROJECT_CONFIG,
    RENOVATE_FILES,
    RENOVATE_PACKAGE,
    SETTINGS,
    TEMPLATE_WORKFLOWS,
    TEMPLATES,
    UPDATES_GUIDE,
    VARIANT_KEYS,
    VARIANTS,
    VERSION_FILE,
    WORKFLOWS,
)
from .patterns import SHA
from .settings import (
    CONFIG_KEYS,
    Config,
    Label,
    Lifecycle,
    Problems,
    backlog_size,
    engine_problems,
    is_repository,
    lifecycle_problems,
    limit,
    load_findings,
    read_settings,
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

# The reusable workflow of the findings, which findings.yml calls, and another
# workflow of a repository may call after the scans of pull requests.
FINDINGS_WORKFLOW = "reusable-findings.yml"

# The reasons GitHub accepts for dismissing a code scanning alert.
DISMISS_REASONS = ("false positive", "won't fix", "used in tests")

LIMITS = (
    "max_per_day",
    "max_per_author_per_day",
    "release_replies",
    "backlog_per_run",
)

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

# An entry of Dependabot's configuration for the actions and reusable workflows of
# the workflows, in block style, as GitHub's docs write it; a line in a comment
# doesn't count.
DEPENDABOT_ACTIONS = re.compile(
    r"""^[ \t-]*["']?package-ecosystem["']?[ \t]*:[ \t]*["']?github-actions["']?"""
    r"[ \t]*(?:#.*)?$",
    re.MULTILINE,
)


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
        for name in labels_of_form(form):
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
    if shape := policy_shape(policy):
        raise SettingsError(shape)
    hosts = policy.get("allow")
    if not hosts:
        raise AssistantError(f"{FIREWALL} allows no host; {reinstall()}")
    wrong = [host for host in hosts if not full_host(host)]
    if wrong:
        raise SettingsError(
            [f"{FIREWALL}: name the host {host} in full" for host in wrong]
        )
    every = dict.fromkeys(host.lower() for host in [*FIREWALL_DEFAULT_HOSTS, *hosts])
    return " ".join(f"{host}:{port}" for host in every for port in ENDPOINT_PORTS)


def policy_shape(policy: object) -> list[str]:
    """What keeps a firewall policy from the shape of the template, which `check`
    and the endpoints of Harden-Runner read: a mapping whose allow, where it has
    one, is a list of host names, each a string."""
    if not isinstance(policy, dict):
        what = (
            "is empty"
            if policy is None
            else "must be a mapping of mode, allow and the other keys of the template"
        )
        return [f"{FIREWALL} {what}; {reinstall()}"]
    hosts = policy.get("allow")
    if hosts is None:
        return []
    if not isinstance(hosts, list):
        return [
            f"{FIREWALL}: allow must be a list, with each host on a line of its own "
            "after a dash"
        ]
    return [
        f"{FIREWALL}: allow lists {json.dumps(host, default=str)}, where it takes a "
        "host name such as api.github.com"
        for host in hosts
        if not isinstance(host, str)
    ]


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


def variant_settings(root: Path) -> Config:
    """The settings of the repository in `root` that choose the variants of the
    templates, for install, which runs before the starters are there: the defaults
    without config.toml, with a value that isn't a boolean, and with a file that
    isn't TOML, which load_config stops at afterwards."""
    try:
        values = read_settings(root / SETTINGS / "config.toml", Problems())
    except SettingsError:
        values = {}
    chosen = {
        CONFIG_KEYS[section][key].attribute for section, key in VARIANT_KEYS.values()
    }
    return Config(
        root,
        "",
        (),
        "",
        **{name: value for name, value in values.items() if name in chosen},
    )


def chosen_variant(name: str, config: Config) -> str:
    """The setting of config.toml, as `[section] key = value`, that chooses the
    variant of the template of the workflow `name` with these settings; empty where
    the template itself is wanted: the key has its default or is left out, or the
    workflow has no variant."""
    if name not in VARIANT_KEYS:
        return ""
    section, key = VARIANT_KEYS[name]
    attribute = CONFIG_KEYS[section][key].attribute
    value = getattr(config, attribute)
    if value == VARIANT_DEFAULTS[attribute]:
        return ""
    # As config.toml has it: false, or the name in quotes.
    return f"[{section}] {key} = {json.dumps(value)}"


# The defaults of the keys that choose a variant, which choose the template.
VARIANT_DEFAULTS = {
    field.name: field.default
    for field in fields(Config)
    if field.name
    in {CONFIG_KEYS[section][key].attribute for section, key in VARIANT_KEYS.values()}
}


def template_of(name: str, config: Config) -> Path:
    """The template of the workflow `name` that install writes and check expects
    with these settings: its variant where a key chooses it, else the template."""
    folder = VARIANTS if chosen_variant(name, config) else TEMPLATES
    return folder / WORKFLOWS / name


def template_text(name: str, config: Config) -> str:
    """The text of the template of the workflow `name` that install writes with
    these settings: that of template_of, with the workflow of [failures] workflow in
    the last job of the variant of issue-assistant.yml."""
    text = template_of(name, config).read_text(encoding="utf-8")
    if name == ASSISTANT_WORKFLOW and chosen_variant(name, config):
        text = FAILURE_WORKFLOW.sub(
            lambda line: line["key"] + config.failure_workflow, text
        )
    return text


def expected_workflow(name: str, config: Config) -> Any:
    """The workflow `name` as check expects it with these settings, without the
    refs of its actions: the YAML of template_text."""
    template = template_of(name, config)
    expected = normalized(load_yaml(template), [], template.name)
    if name == ASSISTANT_WORKFLOW and chosen_variant(name, config):
        expected["jobs"][FAILURE_JOB]["env"]["WORKFLOW"] = config.failure_workflow
    return expected


def check_workflows(config: Config) -> list[str]:
    problems: list[str] = []
    for name in TEMPLATE_WORKFLOWS:
        path = config.root / WORKFLOWS / name
        where = str(WORKFLOWS / name)
        if not path.is_file():
            if name != DISCUSSION_WORKFLOW or config.answer_categories:
                problems.append(f"{where} is missing; {reinstall()}")
            continue
        expected = expected_workflow(name, config)
        actual = normalized(load_yaml(path, where), problems, where)
        if actual != expected:
            key = chosen_variant(name, config)
            form = f" for {key} of config.toml" if key else ""
            problems.append(
                f"{where} differs from the template of the issue assistant{form}, "
                f"and only the refs of its actions may differ; {reinstall()}"
            )
    problems += findings_variant_problems(config)
    problems += failures_variant_problems(config)
    firewall = config.root / FIREWALL
    if not firewall.is_file():
        problems.append(f"{FIREWALL} is missing; {reinstall()}")
    else:
        policy = load_yaml(firewall, str(FIREWALL))
        shape = policy_shape(policy)
        problems += shape or policy_problems(policy, config.engine)
    ignore = config.root / ".gitignore"
    if not ignore.is_file() or f"/{CONTEXT}/" not in ignore.read_text("utf-8"):
        problems.append(f".gitignore: add /{CONTEXT}/")
    return problems


def default_branch_name(root: Path) -> str:
    """The name of the default branch of the repository in `root`: from the event of
    the run in GitHub Actions, or from the remote origin of the checkout; empty when
    neither names it, such as in a clone whose origin/HEAD git doesn't know."""
    path = os.environ.get("GITHUB_EVENT_PATH")
    try:
        event = json.loads(Path(path).read_text("utf-8")) if path else {}
    except (OSError, ValueError):
        event = {}
    repository = event.get("repository") if isinstance(event, dict) else None
    branch = repository.get("default_branch") if isinstance(repository, dict) else None
    if isinstance(branch, str) and branch:
        return branch
    try:
        origin = subprocess.run(
            ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
            cwd=root,
            capture_output=True,
            check=False,
        )
    except OSError:
        return ""
    named = origin.stdout.decode("utf-8", "replace").strip()
    return named.removeprefix("origin/") if origin.returncode == 0 else ""


def dismisses_in_pull_requests(workflow: object) -> bool:
    """Whether a workflow starts on workflow_run and has a job that calls the
    reusable workflow of the findings: the one that dismisses the accepted alerts
    of the pull requests where findings.yml runs after the scans of main alone."""
    if not isinstance(workflow, dict):
        return False
    # PyYAML reads the key `on` as True, and yq as "on".
    events = workflow.get("on", workflow.get(True))
    if isinstance(events, (str, list)):
        events = {
            str(event): None
            for event in ([events] if isinstance(events, str) else events)
        }
    jobs = workflow.get("jobs")
    if not isinstance(events, dict) or "workflow_run" not in events:
        return False
    called = f"{ACTION}/{WORKFLOWS}/{FINDINGS_WORKFLOW}@".casefold()
    return isinstance(jobs, dict) and any(
        isinstance(job, dict) and str(job.get("uses", "")).casefold().startswith(called)
        for job in jobs.values()
    )


def findings_variant_problems(config: Config) -> list[str]:
    """What keeps [findings] pull_requests = false from working: a default branch
    that isn't main, which the variant of findings.yml names, and a repository
    whose other workflows leave the accepted alerts of its pull requests alone."""
    if config.findings_pull_requests:
        return []
    key = f"{SETTINGS / 'config.toml'}: [findings] pull_requests = false"
    problems = []
    branch = default_branch_name(config.root)
    if branch and branch != "main":
        problems.append(
            f"{key} runs Findings after the scans of main alone, and the default "
            f"branch is {branch}; take the key out"
        )
    others = sorted(
        path
        for pattern in ("*.yml", "*.yaml")
        for path in (config.root / WORKFLOWS).glob(pattern)
        if path.name != "findings.yml"
    )
    if not any(dismisses_in_pull_requests(readable_yaml(path)) for path in others):
        problems.append(
            f"{key} needs another workflow of {WORKFLOWS} that starts on "
            f"workflow_run and calls {ACTION}/{WORKFLOWS}/{FINDINGS_WORKFLOW} after "
            "the scans of pull requests, or nothing dismisses their accepted alerts; "
            "add one, or take the key out"
        )
    return problems


def failures_variant_problems(config: Config) -> list[str]:
    """What keeps [failures] workflow from working: a workflow of that name that
    isn't there, or that the last job of issue-assistant.yml can't start with the
    run, since it has no input `run` of workflow_dispatch."""
    name = config.failure_workflow
    if not name:
        return []
    key = f"{SETTINGS / 'config.toml'}: [failures] workflow = {json.dumps(name)}"
    path = config.root / WORKFLOWS / name
    if not path.is_file():
        return [f"{key} names no workflow of {WORKFLOWS}; add it, or take the key out"]
    if name == ASSISTANT_WORKFLOW or not takes_a_run(readable_yaml(path)):
        return [
            f"{key} names a workflow that the last job of {ASSISTANT_WORKFLOW} can't "
            "start with the failed run: it needs the input run under "
            "workflow_dispatch, which gets the id of the run, as the bot-health bot "
            "of the Repo Blueprint has it"
        ]
    return []


def takes_a_run(workflow: object) -> bool:
    """Whether a workflow starts on workflow_dispatch with an input `run`."""
    if not isinstance(workflow, dict):
        return False
    # PyYAML reads the key `on` as True, and yq as "on".
    events = workflow.get("on", workflow.get(True))
    dispatch = events.get("workflow_dispatch") if isinstance(events, dict) else None
    inputs = dispatch.get("inputs") if isinstance(dispatch, dict) else None
    return isinstance(inputs, dict) and "run" in inputs


def readable_yaml(path: Path) -> object:
    """A workflow as data; None for one that isn't YAML, which isn't check's to
    judge in a workflow other than the templates."""
    try:
        return load_yaml(path, str(path.name))
    except SettingsError:
        return None


def policy_problems(policy: dict[str, Any], engine: str) -> list[str]:
    """What a firewall policy in the shape of the template lacks for the assistant
    and its engine."""
    problems: list[str] = []
    engine = engine if engine in ENGINES else DEFAULT_ENGINE
    needed = allowed_hosts(firewall_policy(engine))
    hosts: set[str] = set(policy.get("allow") or [])
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
            where = (
                PROJECT_CONFIG
                if host in config.project_hosts
                else SETTINGS / "config.toml"
            )
            problems.append(f"{where}: {host} is no host name")
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
            # The backlog has a top as well: the size of a matrix.
            if key == "backlog_per_run":
                backlog_size(config)
            else:
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
            problems += [
                notice_problem(name, line)
                for line in stale_notice(content, config, notice_language(name))
            ]
    return problems


# The section that tells reporters what happens with their issue, which install adds
# to each file of [transparency] that doesn't name the engine yet, in English or, for
# a file such as SUPPORT.de.md, in German.
NOTICE = PurePosixPath("SUPPORT-notice.md")
NOTICE_DE = PurePosixPath("SUPPORT-notice.de.md")
# The placeholders of the notice for the periods of [lifecycle], as Lifecycle.text
# names them.
PERIODS = ("remind_days", "close_days", "grace_days", "reopen_days", "lock_days")
PERIOD = re.compile(rf"\{{({'|'.join(PERIODS)})\}}")


def notice_language(name: str) -> str:
    """The language of the notice in a file of [transparency]: German for a file
    such as SUPPORT.de.md, English for the others."""
    return "de" if name.casefold().endswith(".de.md") else "en"


def notice_template(language: str) -> str:
    """The notice of the templates in the language, with its placeholders."""
    return (TEMPLATES / (NOTICE_DE if language == "de" else NOTICE)).read_text(
        encoding="utf-8"
    )


def turned_off(line: str, config: Config) -> str:
    """The setting that turns off the step that a line of the notice's template
    describes, such as "[lifecycle] lock_closed_after_days = 0"; empty while the
    step is on."""
    # Without releases, GitHub closes a fixed issue with the merge.
    if FIXED in line and not config.releases:
        return "[releases] close_with_release = false"
    if "{lock_days}" in line and not config.lifecycle.lock_closed_after_days:
        return "[lifecycle] lock_closed_after_days = 0"
    return ""


@dataclass(frozen=True)
class StaleLine:
    """A line of a notice that still reads as a line of the template, but names
    other periods than [lifecycle] or a step that the settings turn off."""

    number: int  # from 1
    line: str  # as the file has it, without the end of the line
    said: tuple[str, ...]  # the periods that the line names, such as "15 days"
    wanted: tuple[str, ...]  # the periods of the settings in their place
    fixed: str  # the line as the settings want it, empty to take it out
    off: str  # the setting that turns off its step, see turned_off()


def days_pattern(language: str) -> str:
    """A pattern of any number of days in the words of the language, as days()
    writes them, such as "1 day" or "15 days"."""
    forms = sorted(
        (form for key, form in texts(language).items() if key.startswith("days.")),
        key=len,
        reverse=True,
    )
    return "|".join(
        r"\d+".join(re.escape(part) for part in form.split("{count}")) for form in forms
    )


def stale_notice(text: str, config: Config, language: str) -> list[StaleLine]:
    """The lines of a notice in the language that match a line of its template with
    a period, by the template's fixed words, but not the periods that [lifecycle]
    sets, or that describe a step that the settings turn off. A line that the
    maintainer rewrote so that it no longer matches is left alone."""
    lifecycle = config.lifecycle.text(language)
    days = days_pattern(language)
    templates = []
    for template in notice_template(language).splitlines():
        parts = PERIOD.split(template.strip())
        if len(parts) > 1:
            source = "".join(
                f"({days})" if index % 2 else re.escape(part)
                for index, part in enumerate(parts)
            )
            templates.append((template.strip(), parts[1::2], re.compile(source)))
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for template, names, pattern in templates:
            match = pattern.fullmatch(line.strip())
            if match is None:
                continue
            off = turned_off(template, config)
            fixed = "" if off else PERIOD.sub(lambda m: lifecycle[m[1]], template)
            if fixed != line.strip():
                wanted = tuple(lifecycle[name] for name in names)
                found.append(
                    StaleLine(number, line, match.groups(), wanted, fixed, off)
                )
            break
    return found


def notice_problem(name: str, stale: StaleLine) -> str:
    """What `check` says about a line of a notice that the settings left behind."""
    where = f"{name}, line {stale.number}"
    if stale.off:
        return (
            f"{where}: describes a step that {stale.off} in "
            f"{SETTINGS / 'config.toml'} turns off; install takes the line out"
        )
    said = " and ".join(f'"{period}"' for period in stale.said)
    wanted = " and ".join(f'"{period}"' for period in stale.wanted)
    return (
        f"{where}: says {said}, {SETTINGS / 'config.toml'} says {wanted}; install "
        "writes them into the line"
    )


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


def renovate_in_package(root: Path) -> bool:
    """Whether package.json in `root` holds a configuration of Renovate under its
    key "renovate", which Renovate reads as well as a file of its own."""
    path = root / RENOVATE_PACKAGE
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return False
    return isinstance(data, dict) and "renovate" in data


def updates_missing(root: Path) -> str:
    """Why the pins of the issue assistant in the workflows of the repository in
    `root` never move to a new release, for `install` and a warning of `check`;
    empty when a configuration of Renovate or Dependabot's ecosystem github-actions
    moves them.

    A warning, never a problem: a repository may run Renovate from elsewhere, such
    as an app of its organization with a shared preset, without a file of its own.
    """
    if any((root / name).is_file() for name in RENOVATE_FILES) or renovate_in_package(
        root
    ):
        return ""
    dependabot = [name for name in DEPENDABOT_FILES if (root / name).is_file()]
    if any(
        DEPENDABOT_ACTIONS.search(
            (root / name).read_text(encoding="utf-8", errors="replace")
        )
        for name in dependabot
    ):
        return ""
    cause = (
        f"{dependabot[0]} updates no package-ecosystem github-actions, and no "
        "configuration of Renovate is there"
        if dependabot
        else "neither Dependabot nor Renovate is configured"
    )
    return (
        f"{cause}, so the pins of the issue assistant in {WORKFLOWS} stay on this "
        "release, and its fixes, security fixes among them, never arrive: add the "
        f"package-ecosystem github-actions for the directory / to "
        f"{DEPENDABOT_FILES[0]}, or a configuration of Renovate, such as "
        f"{RENOVATE_FILES[0]}, as {UPDATES_GUIDE} shows. A repository that runs "
        "Renovate from elsewhere, such as with a preset of its organization, can "
        "leave it so"
    )


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
