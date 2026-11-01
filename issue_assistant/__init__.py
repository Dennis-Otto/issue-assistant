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
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from fnmatch import fnmatchcase
from pathlib import Path, PurePath, PurePosixPath
from typing import Any
from .paths import (
    CHOOSER,
    CHOOSER_REPOSITORY,
    CONTEXT,
    DISCUSSION_WORKFLOW,
    ENGINE_LOCKS,
    FIREWALL,
    FORMS,
    FORM_STARTERS,
    LABELS_FILE,
    SETTINGS,
    STARTERS,
    TEMPLATES,
    TEMPLATE_WORKFLOWS,
    TOOL,
    WORKFLOWS,
)
from .constants import (
    ACTION,
    DISCUSSION_MODES,
    EVALUATED,
    FIREWALL_RUNNER,
    FIXED,
    MODES,
    MOST_EVALUATED,
    NEEDS_INFO,
    NO_PRIORITY,
    REPOSITORY_NAME,
    RUNNER_VARIABLE,
)
from .patterns import CODE, HTML_COMMENT, SHA
from .values import command, created, fence, login, moment, stamp
from .errors import AssistantError, GitHubError, SettingsError, SweepIncomplete
from .engines import Cli, ENGINES, Engine, Run, engine_named
from .settings import Config, label_names, lifecycle_problems, load_config, task_run
from .fixture import FIXTURE_VARIABLE, read_fixture
from .github import GitHub
from .issues import command_of, is_bot, is_maintainer, kind_of, opted_out, text_of
from .cleaning import code_span, notice_link, one_line
from .answer import parse_answer
from .comments import bullets
from .applying import apply_answer, other_issue, questions, vouched_for
from .learning import CHOSEN_GROUPS, plural
from .digests import report, share
from .context import write_context
from .events import handle_event
from .sweeps import sweep
from .labels import sync_labels
from .code_scanning import watch_findings
from .actionlint import add_to_actionlint
from .setup_check import (
    check,
    configured_engine,
    firewall_endpoints,
    firewall_policy,
    runner_problems,
    runner_trade_off,
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

# The section that tells reporters what happens with their issue, which install adds
# to each file of [transparency] that doesn't name the engine yet, in English or, for
# a file such as SUPPORT.de.md, in German.
NOTICE = PurePosixPath("SUPPORT-notice.md")
NOTICE_DE = PurePosixPath("SUPPORT-notice.de.md")

# The environment that holds the engine's secret, limited to the default branch.
ENVIRONMENT = "issue-assistant"

# The tags of the releases, such as v1.2.3, as git's --match takes them.
RELEASE_TAGS = "v[0-9]*.[0-9]*.[0-9]*"

REUSABLE_PREFIX = "reusable-"
# A pin of the issue assistant in a template: of one of its reusable workflows, or
# of the action itself.
ACTION_PIN = re.compile(
    rf"(?P<uses>{re.escape(ACTION)}(?:/{re.escape(str(WORKFLOWS))}/"
    rf"{REUSABLE_PREFIX}[\w-]+\.yml)?)@[0-9a-f]{{40}} # \S+"
)
# A checkout of a release, from which install writes the templates of that release.
CLONE = f"git clone --depth 1 --branch vX.Y.Z https://github.com/{ACTION}"

# A remote of a checkout on GitHub, over HTTPS or SSH.
GITHUB_REMOTE = re.compile(
    r"(?:https://(?:[^/@]+@)?github\.com/|(?:ssh://)?git@github\.com[:/])"
    r"(?P<name>\S+?)(?:\.git)?/?"
)

# The moment that it replays: a minute after the issue was opened, when it had the
# labels of its form and the analysis would have started.
OPENING = timedelta(minutes=1)


def git_says(folder: Path, *args: str) -> str | None:
    """What git prints for `args` in `folder`; None when git fails or is missing."""
    try:
        result = subprocess.run(
            ["git", "-C", str(folder), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except FileNotFoundError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def release_of(tool: Path, ref: str | None, version: str | None) -> tuple[str, str]:
    """The commit and the tag of the release whose templates install writes.

    They are those of the git checkout in `tool`, which a clone of a release tag
    names on its own. A --ref that is given must be the commit of the checkout, not
    the object of an annotated tag, which `git ls-remote` lists first; a --version
    that the checkout has as a tag must point to it. Without a checkout of its own,
    such as an unpacked package of a release, install takes both as they are given.
    """
    if ref is not None and not SHA.fullmatch(ref):
        raise AssistantError(
            f"--ref takes the full commit hash of the release, not {ref!r}; leave it "
            "out to take the commit of the checkout."
        )
    top = git_says(tool, "rev-parse", "--show-toplevel")
    # A folder in the checkout of another repository is no checkout of its own.
    own = top is not None and Path(top).resolve() == tool.resolve()
    head = git_says(tool, "rev-parse", "HEAD^{commit}") if own else None
    if head is None:
        if ref is None or version is None:
            raise AssistantError(
                f"{tool.as_posix()} is no git checkout, so give --ref and --version, "
                f"or run install from a checkout of a release: {CLONE}"
            )
        return ref, version
    if ref is not None and ref != head:
        if git_says(tool, "cat-file", "-t", ref) == "tag":
            raise AssistantError(
                f"{ref} is the object of a tag, not the commit of a release; use "
                f"{git_says(tool, 'rev-parse', ref + '^{commit}')}, or leave out --ref."
            )
        raise AssistantError(
            f"The checkout in {tool.as_posix()} is at the commit {head}, not {ref}, "
            "and install writes its templates; leave out --ref, or check out the "
            "release of that commit."
        )
    if version is None:
        tag = git_says(
            tool, "describe", "--tags", "--exact-match", "--match", RELEASE_TAGS
        )
        if not tag:
            raise AssistantError(
                f"The checkout in {tool.as_posix()} is at no release; check out the "
                f"tag of one, or clone it: {CLONE}"
            )
        return head, tag
    tagged = git_says(
        tool, "rev-parse", "--verify", "--quiet", f"refs/tags/{version}^{{commit}}"
    )
    if tagged is not None and tagged != head:
        raise AssistantError(
            f"The tag {version} is the commit {tagged}, not {head} of the checkout in "
            f"{tool.as_posix()}; leave out --version, or check out {version}."
        )
    if tagged is None and fnmatchcase(version, RELEASE_TAGS):
        # A clone of one release has no tag of another, and Dependabot would read
        # the version in the pin's comment as the one installed.
        tag = git_says(
            tool, "describe", "--tags", "--exact-match", "--match", RELEASE_TAGS
        )
        if tag:
            raise AssistantError(
                f"The checkout in {tool.as_posix()} is the release {tag}, not "
                f"{version}; leave out --version, or check out {version}."
            )
    return head, version


def chooser(config: Config) -> str:
    """The chooser of the templates for the repository of `config`.

    A question goes to the first category whose discussions get a first answer, as
    someone with a question needs one place to ask it. Without such a category, or
    without a notice to reporters, it has no link there.
    """
    category = config.answer_categories[0] if config.answer_categories else ""
    values = {
        CHOOSER_REPOSITORY: config.repository,
        # GitHub's slug of a category, such as q-a for Q&A.
        "CATEGORY": re.sub(r"\W+", "-", category.lower()).strip("-"),
        "NOTICE_LINK": notice_link(config) or "",
    }
    template = (TEMPLATES / CHOOSER).read_text(encoding="utf-8")
    kept = [
        part
        for part in re.split(r"(?m)^(?=  - name: )", template)
        if all(value or name not in part for name, value in values.items())
    ]
    return re.sub("|".join(values), lambda found: values[found[0]], "".join(kept))


def name_repository_in_chooser(root: Path, repository: str) -> list[str]:
    """The links of a chooser that was written before the repository was known, now
    with its name: the check asks for it, and only install, which knows it, can do it
    without a person."""
    placeholder = CHOOSER_REPOSITORY.encode()
    done = []
    for path in sorted((root / FORMS).glob("config.y*ml")) if repository else ():
        content = path.read_bytes()
        if placeholder in content:
            path.write_bytes(content.replace(placeholder, repository.encode()))
            where = path.relative_to(root).as_posix()
            done.append(f"named {repository} in the links of {where}")
    return done


def add_forms(root: Path, repository: str, *, starter_labels: bool) -> list[str]:
    """The forms of the starters and their chooser, for a repository without issue
    forms or templates.

    The labels of the forms and the answers of their field Area are those of the
    starter labels, so install writes them only together with them; labels of the
    repository's own may not map them, and a form overrides the templates of its
    owner's .github. The chooser turns blank issues off, so it comes only with forms,
    and one that the repository has stays. Its links need `repository`.
    """
    forms = root / FORMS
    done = name_repository_in_chooser(root, repository)
    if forms.is_dir() and any(
        path.is_file() and path.stem != "config" for path in forms.iterdir()
    ):
        return done
    if not starter_labels:
        return [
            *done,
            f"wrote no issue forms for your own {LABELS_FILE}; the starters in "
            f"{(TEMPLATES / FORMS).as_posix()} need its labels and areas there",
        ]
    forms.mkdir(parents=True, exist_ok=True)
    for form in FORM_STARTERS.values():
        shutil.copyfile(TEMPLATES / form, root / form)
        done.append(f"wrote {form}; adapt it")
    if not any(forms.glob("config.y*ml")):
        config = load_config(root, repository or CHOOSER_REPOSITORY, lenient=True)
        (root / CHOOSER).write_text(chooser(config), encoding="utf-8", newline="\n")
        done.append(
            f"wrote {CHOOSER}; adapt it, and turn on what its links need on GitHub"
            if repository
            else f"wrote {CHOOSER}; replace {CHOOSER_REPOSITORY} in its links with the "
            "repository, which neither GH_REPO nor the remote origin names, or run "
            "install again once the remote does"
        )
    return done


def notice(config: Config, engine: Engine, language: str) -> str:
    """What happens with an issue, for reporters: the notice of the templates in
    the language, with the engine, the periods of the repository and the categories
    of the discussions that get a first answer."""
    lifecycle = config.lifecycle
    lines = [
        line
        for line in (TEMPLATES / (NOTICE_DE if language == "de" else NOTICE))
        .read_text(encoding="utf-8")
        .splitlines(keepends=True)
        # Without releases, GitHub closes a fixed issue with the merge.
        if (config.releases or FIXED not in line)
        and (lifecycle.lock_closed_after_days or "{lock_days}" not in line)
        and (config.answer_categories or "{categories}" not in line)
    ]
    return filled("".join(lines), config, engine, language)


def filled(text_: str, config: Config, engine: Engine, language: str) -> str:
    """A text of the notice with the engine, the periods and the categories."""
    values = {
        "engine": engine.name,
        "vendor": engine.vendor,
        "categories": ", ".join(config.answer_categories),
    }
    for key, value in (values | config.lifecycle.text(language)).items():
        text_ = text_.replace(f"{{{key}}}", value)
    return text_


def discussions_line(text_: str, config: Config, engine: Engine, language: str) -> str:
    """The line of the notice about the answers in Discussions, for a notice that
    was written before the repository named categories; empty where it has one."""
    template = (TEMPLATES / (NOTICE_DE if language == "de" else NOTICE)).read_text(
        encoding="utf-8"
    )
    line = next(line for line in template.splitlines() if "{categories}" in line)
    if not config.answer_categories or line.split("{categories}")[0] in text_:
        return ""
    return filled(line, config, engine, language)


def add_notices(root: Path) -> list[str]:
    """Tell reporters what happens with their issue, and which AI reads it, in each
    file of [transparency] that doesn't name the engine yet, as `check` asks."""
    config = load_config(root, "", lenient=True)
    engine = ENGINES.get(config.engine)
    if engine is None or lifecycle_problems(config.lifecycle):
        # check names the engine that the assistant doesn't know, and the periods
        # that the sweep can't keep; a notice would promise them.
        return []
    done = []
    for name in config.notices:
        path = root / name
        if not path.resolve().is_relative_to(root.resolve()):
            done.append(f"kept {name}, which is outside the repository")
            continue
        text = path.read_text(encoding="utf-8") if path.is_file() else "# Support\n"
        language = "de" if name.casefold().endswith(".de.md") else "en"
        if engine.name in text and engine.vendor in text:
            line = discussions_line(text, config, engine, language)
            if line:
                path.write_text(
                    f"{text.rstrip()}\n\n{line}\n", encoding="utf-8", newline="\n"
                )
                done.append(f"added the answers in Discussions to {name}; adapt it")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"{text.rstrip()}\n\n{notice(config, engine, language)}",
            encoding="utf-8",
            newline="\n",
        )
        done.append(f"added what happens with an issue to {name}; adapt it")
    return done


def pinned(template: str, ref: str, version: str) -> str:
    """A template with every pin of the issue assistant at the commit of a release."""
    return ACTION_PIN.sub(lambda pin: f"{pin['uses']}@{ref} # {version}", template)


def install(root: Path, ref: str, version: str, repository: str = "") -> list[str]:
    """Write the workflows and the firewall policy of the templates into a repository.

    The workflow of the discussions only when config.toml names categories whose
    discussions get a first answer, as the starter does, or when it is there
    already. The starters are only written when they are missing: labels,
    project.md, config.toml, findings.toml, and, with the starter labels, the forms of
    a bug report and a feature request with their chooser, whose links name
    `repository`, in a repository without issue forms. The notice to reporters, the
    entry of .gitignore and what actionlint needs are added where they are missing,
    so that running it again changes nothing but the workflows.
    """
    if not SHA.fullmatch(ref):
        raise AssistantError("Give the commit hash of the issue assistant's release.")
    done = []

    def write(name: str) -> None:
        target = root / WORKFLOWS / name
        target.parent.mkdir(parents=True, exist_ok=True)
        content = pinned(
            (TEMPLATES / WORKFLOWS / name).read_text(encoding="utf-8"), ref, version
        )
        target.write_text(content, encoding="utf-8", newline="\n")
        done.append(f"wrote {WORKFLOWS / name}")

    for name in TEMPLATE_WORKFLOWS:
        if name != DISCUSSION_WORKFLOW:
            write(name)
    firewall = root / FIREWALL
    if firewall.is_file():
        done.append(f"kept {FIREWALL}; check names each host of the engine it lacks")
    else:
        engine = configured_engine(root)
        firewall.write_text(firewall_policy(engine), encoding="utf-8", newline="\n")
        done.append(f"wrote {FIREWALL} for the engine {engine}")
    starter_labels = not (root / LABELS_FILE).is_file()
    for starter in STARTERS:
        target = root / starter
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(TEMPLATES / starter, target)
            done.append(f"wrote {starter}; adapt it")
    # Once the starters are there: the settings say whether discussions get answers.
    if (root / WORKFLOWS / DISCUSSION_WORKFLOW).is_file() or load_config(
        root, "", lenient=True
    ).answer_categories:
        write(DISCUSSION_WORKFLOW)
    ignore = root / ".gitignore"
    entry = f"/{CONTEXT}/"
    lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.is_file() else []
    if entry not in lines:
        ignore.write_text(
            "\n".join([*lines, entry]) + "\n", encoding="utf-8", newline="\n"
        )
        done.append(f"added {entry} to .gitignore")
    # Last, as the chooser links the notice.
    return [
        *done,
        *add_to_actionlint(root),
        *add_notices(root),
        *add_forms(root, repository, starter_labels=starter_labels),
    ]


def repository_of_remote(root: Path) -> str:
    """The repository on GitHub that the remote origin of the checkout in `root`
    names, as install finds it without gh; nothing for another host or no remote."""
    found = GITHUB_REMOTE.fullmatch(git_says(root, "remote", "get-url", "origin") or "")
    name = found["name"] if found else ""
    return name if REPOSITORY_NAME.fullmatch(name) else ""


def repository_to_name(root: Path) -> str:
    """The repository that install names in the links of the chooser: GH_REPO, or else
    GITHUB_REPOSITORY, or else the remote origin. Nothing here calls GitHub, so a value
    of another form, such as HOST/OWNER/REPO, which gh takes, doesn't stop install."""
    name = os.environ.get("GH_REPO") or os.environ.get("GITHUB_REPOSITORY", "")
    return name if REPOSITORY_NAME.fullmatch(name) else repository_of_remote(root)


def repository_of_checkout(root: Path) -> str:
    """The repository on GitHub of the checkout in `root`, as gh finds it."""
    result = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    name = result.stdout.strip()
    if result.returncode or not REPOSITORY_NAME.fullmatch(name):
        raise AssistantError(
            f"gh finds no repository on GitHub for {root.as_posix()} "
            f"({result.stderr.strip() or name or 'no remote'}); name it with "
            "GH_REPO=owner/name."
        )
    return name


def set_up_github(github: GitHub, config: Config, *, ask: bool) -> Iterator[str]:
    """Set up on GitHub what the workflows need, and say what is left to do.

    The engine's secret belongs to the environment issue-assistant, which only the
    default branch may use, so that no other branch's workflow can read it. Each
    step looks first, so that running it again changes nothing; in a terminal, gh
    asks for the secret, which never passes through this script.
    """
    branch = github.default_branch()
    path = f"environments/{ENVIRONMENT}"
    try:
        environment = github.rest(path)
    except GitHubError as error:
        if not error.missing():
            raise
        environment = github.rest(
            path,
            method="PUT",
            data={
                "deployment_branch_policy": {
                    "protected_branches": False,
                    "custom_branch_policies": True,
                }
            },
        )
        yield f"created the environment {ENVIRONMENT}"
    policy = environment.get("deployment_branch_policy") or {}
    if not policy.get("custom_branch_policies"):
        # A maintainer's choice, such as protected branches, which may also be
        # meant for reviewers or a wait timer; only a person changes it.
        yield (
            f"kept the environment {ENVIRONMENT}, which "
            + ("protected branches" if policy else "every branch")
            + f" may use; limit it to {branch} under Settings > Environments"
        )
    else:
        policies = github.rest(f"{path}/deployment-branch-policies?per_page=100")
        if any(
            item.get("name") == branch and item.get("type", "branch") == "branch"
            for item in policies.get("branch_policies") or []
        ):
            yield f"kept the environment {ENVIRONMENT}, for the branch {branch}"
        else:
            github.rest(
                f"{path}/deployment-branch-policies",
                method="POST",
                data={"name": branch, "type": "branch"},
            )
            yield f"let the branch {branch} use the environment {ENVIRONMENT}"
    engine = ENGINES[engine_named(config.engine)]
    stored = github.rest(f"{path}/secrets?per_page=100")
    present = {item.get("name") for item in stored.get("secrets") or []}
    command_line = (
        f"gh secret set {engine.credentials[0]} --env {ENVIRONMENT} "
        f"--repo {github.repository}"
    )
    others = ", ".join(engine.credentials[1:])
    if present & set(engine.credentials):
        yield f"kept the secret of {engine.name} in the environment {ENVIRONMENT}"
    elif not ask:
        yield f"left for you: {command_line}" + (
            f", or set {others} instead" if others else ""
        )
    else:
        yield f"{command_line}: paste the secret of {engine.name}" + (
            f", or {others} later" if others else ""
        )
        asked = subprocess.run(
            ["gh", "secret", "set", engine.credentials[0], "--env", ENVIRONMENT]
            + ["--repo", github.repository],
            check=False,
        )
        if asked.returncode:
            raise AssistantError(f"gh set no secret; run {command_line}")
        yield f"set the secret {engine.credentials[0]} of the environment {ENVIRONMENT}"
    if config.releases:
        yield (
            "left for you: clear Settings > General > Issues > Auto-close issues with "
            f"merged linked pull requests on https://github.com/{github.repository}"
            "/settings, so that a fixed issue stays open until its release"
        )


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
