"""Setting up a repository: the files that `install` writes, and the repository on GitHub."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from .actionlint import add_to_actionlint
from .cleaning import notice_link
from .constants import ACTION, FIXED, REPOSITORY_NAME
from .engines import ENGINES, Engine, engine_named
from .errors import AssistantError, GitHubError
from .github import GitHub
from .paths import (
    CHOOSER,
    CHOOSER_REPOSITORY,
    CONTEXT,
    DISCUSSION_WORKFLOW,
    FIREWALL,
    FORM_STARTERS,
    FORMS,
    LABELS_FILE,
    STARTERS,
    TEMPLATE_WORKFLOWS,
    TEMPLATES,
    WORKFLOWS,
)
from .patterns import SHA
from .settings import Config, lifecycle_problems, load_config
from .setup_check import configured_engine, firewall_policy

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
