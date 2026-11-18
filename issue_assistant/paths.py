"""Where the tool keeps its files, and where a repository keeps those of the assistant."""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

TOOL = Path(__file__).resolve().parent.parent
PROMPTS = TOOL / "prompts"
# The files a repository gets, in the places they go: the workflows, the firewall
# policy and the starter files. This repository's own copies of the workflows and
# the policy are the blueprint's, of the release that it pins; install and check
# never read them.
TEMPLATES = TOOL / "templates"
# The command lines of the engines that `analyze` runs, each in a folder named by
# its key in ENGINES: a package.json that pins it, and the package-lock.json of
# every package that it installs, each with its hash. Renovate keeps them current,
# and npm ci installs them as they are (decision 0017).
ENGINE_LOCKS = TOOL / "engines"
# Files of the repository the assistant looks after, relative to its root. Messages
# name them, so they are written with slashes on every system, Windows too;
# `root / LABELS_FILE` is still a Path of the system.
LABELS_FILE = PurePosixPath(".github/labels.toml")
SETTINGS = PurePosixPath(".github/issue-assistant")
CONTEXT = PurePosixPath(".issue-assistant")
WORKFLOWS = PurePosixPath(".github/workflows")
FIREWALL = PurePosixPath(".github/egress-firewall.yaml")
ACTIONLINT = PurePosixPath(".github/actionlint.yaml")
FINDINGS_FILE = PurePosixPath(".github/findings.toml")
# The settings that a project adds to those of config.toml, in a file of its own: a
# template such as Repo Blueprint writes config.toml anew with each update, but never
# this file. It is optional, and install writes none.
PROJECT_CONFIG = SETTINGS / "config.project.toml"
# The alerts of code scanning that a run of the findings reported, each with the
# time it opened, so that an alert fails one run and not every one after it. The
# Findings workflow keeps it from run to run in the cache of the default branch.
FINDINGS_RECORD = CONTEXT / "findings.json"
# Files that install writes only when a repository doesn't have them yet.
STARTERS = (
    LABELS_FILE,
    SETTINGS / "project.md",
    SETTINGS / "config.toml",
    FINDINGS_FILE,
)
# The issue forms and templates of a repository, the forms that install writes into
# a repository that has none, by the kind of issue that each opens, and the chooser
# that it writes with them, which holds this name until someone names the repository.
FORMS = PurePosixPath(".github/ISSUE_TEMPLATE")
FORM_STARTERS = {
    "bug": FORMS / "bug_report.yml",
    "feature": FORMS / "feature_request.yml",
}
CHOOSER = FORMS / "config.yml"
CHOOSER_REPOSITORY = "OWNER/NAME"

# The version of this checkout, which the release bot writes.
VERSION_FILE = TOOL / "version.txt"

# The workflows of the templates: install copies them, with the commit hash of the
# release in the pins of the issue assistant, and check compares a repository's
# workflows with them. Each one calls the reusable workflow of this repository whose
# name is that of the template after "reusable-", which has its jobs; so a release
# that changes no template reaches a repository as a move of the pins alone.
TEMPLATE_WORKFLOWS = (
    "issue-assistant.yml",
    "issue-lifecycle.yml",
    "labels.yml",
    "findings.yml",
    "discussion-assistant.yml",
)
# The workflow of the first answers in Discussions, which a repository needs only
# once [discussions] answer_categories of config.toml names a category: install
# writes it then, and check compares it with its template whenever it is there. Its
# apply job writes to discussions, which a repository that only moves the pin of
# issue-assistant.yml doesn't grant, so the discussions have a reusable workflow of
# their own; decision 0018 says why.
DISCUSSION_WORKFLOW = "discussion-assistant.yml"
# The variants of the templates: a workflow of the same name that differs from its
# template in exactly one thing, which a key of config.toml, by its section and
# name, chooses when it differs from its default: a boolean when it is false, a
# string when it names something. Without the key a repository gets the template;
# with it, install writes the variant and check expects it (decisions 0035 and
# 0037). They are kept out of templates/.github/workflows/, each of whose *.yml the
# repository blueprint takes over as a workflow of its own.
VARIANTS = TEMPLATES / "variants"
VARIANT_KEYS = {
    # Another workflow of the repository dismisses the accepted alerts of its pull
    # requests, so Findings runs after the scans of main alone.
    "findings.yml": ("findings", "pull_requests"),
    # The release workflow starts the sweep itself, so the end of Release doesn't.
    "issue-lifecycle.yml": ("releases", "sweep_after_release_workflow"),
    # A workflow of the repository gives a failed run a second try, such as the
    # bot-health bot of the Repo Blueprint, and a last job starts it.
    "issue-assistant.yml": ("failures", "workflow"),
}
# The workflow of the issue assistant that has the last job for failed runs, the
# job, and the line of its environment that names the workflow that it starts:
# install writes the name of [failures] workflow there, and check expects it.
ASSISTANT_WORKFLOW = "issue-assistant.yml"
FAILURE_JOB = "failure"
FAILURE_WORKFLOW = re.compile(r"^(?P<key> +WORKFLOW: ).*$", re.MULTILINE)
# The name of a workflow file, which [failures] workflow may be: no folder, nothing
# that YAML or the shell would read as more than a name.
WORKFLOW_FILE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\.ya?ml")
# The files that turn on the updates of a repository's pins, which bring it each
# release of the issue assistant: Dependabot's, with the ecosystem github-actions,
# and every configuration of Renovate, whose defaults move pinned actions and
# reusable workflows. The page of the set-up says how to add one.
DEPENDABOT_FILES = (
    PurePosixPath(".github/dependabot.yml"),
    PurePosixPath(".github/dependabot.yaml"),
)
RENOVATE_FILES = tuple(
    PurePosixPath(name)
    for name in (
        "renovate.json",
        "renovate.json5",
        ".github/renovate.json",
        ".github/renovate.json5",
        ".gitlab/renovate.json",
        ".gitlab/renovate.json5",
        ".renovaterc",
        ".renovaterc.json",
        ".renovaterc.json5",
    )
)
# Renovate also reads its configuration from the key "renovate" of package.json.
RENOVATE_PACKAGE = PurePosixPath("package.json")
UPDATES_GUIDE = (
    "https://dennis-otto.github.io/issue-assistant/getting-started.html#turn-on-updates"
)
