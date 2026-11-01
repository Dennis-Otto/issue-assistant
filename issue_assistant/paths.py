"""Where the tool keeps its files, and where a repository keeps those of the assistant."""

from __future__ import annotations

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
