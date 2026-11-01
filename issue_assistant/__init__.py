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
import tomllib
import unicodedata
from collections.abc import Callable, Iterable, Iterator
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from fnmatch import fnmatchcase
from functools import cache, partial
from pathlib import Path, PurePath, PurePosixPath
from statistics import median
from typing import Any
from .paths import (
    ACTIONLINT,
    CHOOSER,
    CHOOSER_REPOSITORY,
    CONTEXT,
    DISCUSSION_WORKFLOW,
    ENGINE_LOCKS,
    FINDINGS_FILE,
    FIREWALL,
    FORMS,
    FORM_STARTERS,
    LABELS_FILE,
    PROMPTS,
    SETTINGS,
    STARTERS,
    TEMPLATES,
    TEMPLATE_WORKFLOWS,
    TOOL,
    VERSION_FILE,
    WORKFLOWS,
)
from .constants import (
    ACTION,
    BOT,
    DIGEST,
    DISCUSSION_MODES,
    DUPLICATE,
    EVALUATED,
    FIREWALL_RUNNER,
    FIXED,
    HANDLED,
    INVALID,
    LET_IN,
    LIFECYCLE,
    LOGIN,
    MAX_ASSIGNEES,
    MAX_NUMBER,
    MAX_RELATED,
    MAX_TEXT,
    MODES,
    MOST_EVALUATED,
    MUTED,
    NEEDS_INFO,
    NEEDS_TRIAGE,
    NO_PRIORITY,
    POSSIBLE_DUPLICATE,
    REFUSED,
    REPOSITORY_NAME,
    RUNNER_VARIABLE,
    STALE,
    WONTFIX,
)
from .patterns import CODE, HTML_COMMENT, SHA
from .values import (
    command,
    created,
    day_of,
    days_between,
    did_you_mean,
    fence,
    login,
    moment,
    over,
    stamp,
)
from .errors import AssistantError, GitHubError, SettingsError, SweepIncomplete
from .languages import COMMON_WORDS, LANGUAGES, LOCALES
from .engines import (
    Cli,
    DEFAULT_ENGINE,
    ENGINES,
    Engine,
    IN_PROMPT,
    Run,
    STRICT,
    engine_named,
)
from .settings import (
    Config,
    DEFAULT_LIFECYCLE,
    Label,
    Lifecycle,
    answers_category,
    days,
    engine_problems,
    is_repository,
    label_names,
    lifecycle_problems,
    limit,
    load_config,
    load_findings,
    maintainers,
    siblings,
    task_run,
)
from .fixture import FIXTURE_VARIABLE, read_fixture
from .github import GitHub
from .issues import (
    Activity,
    Command,
    Visible,
    box_ticked,
    command_of,
    conversing,
    edited_since,
    exemption,
    follow_up_due,
    form_fields,
    form_labels,
    is_bot,
    is_digest,
    is_duplicate_notice,
    is_maintainer,
    issue_language,
    judged_comment,
    kind_of,
    marker,
    muted_label,
    opted_out,
    pinned_label,
    reactions_on,
    record_run,
    refusal,
    render_marker,
    runs_of,
    text_of,
    upvotes,
    without_box,
)
from .cleaning import code_span, comment_parts, notice_link, one_line, strip_html
from .answer import parse_answer, schema_json
from .comments import (
    bullets,
    render_closed_duplicate,
    render_closed_unanswered,
    render_duplicate,
    render_fixed,
    render_let_in,
    render_locked,
    render_released,
    render_reminder,
    render_reopened,
)
from .applying import apply_answer, assign_owners, other_issue, questions, vouched_for
from .learning import (
    ADDITION_PERIOD,
    CHOSEN_GROUPS,
    CORRECTION_PERIOD,
    MAX_CORRECTED,
    corrections,
    plural,
    reacted_to,
    render_learned,
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

# The section that tells reporters what happens with their issue, which install adds
# to each file of [transparency] that doesn't name the engine yet, in English or, for
# a file such as SUPPORT.de.md, in German.
NOTICE = PurePosixPath("SUPPORT-notice.md")
NOTICE_DE = PurePosixPath("SUPPORT-notice.de.md")

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
# actionlint 1.7.12, the newest release, doesn't know the queue of a concurrency
# group yet, which issue-assistant.yml has; install lets it ignore that message.
QUEUE_MESSAGE = 'unexpected key "queue" for "concurrency" section'
# The environment that holds the engine's secret, limited to the default branch.
ENVIRONMENT = "issue-assistant"

# The tags of the releases, such as v1.2.3, as git's --match takes them.
RELEASE_TAGS = "v[0-9]*.[0-9]*.[0-9]*"

# The reasons GitHub accepts for dismissing a code scanning alert.
DISMISS_REASONS = ("false positive", "won't fix", "used in tests")

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

# The most issues that one sweep locks; the next sweep goes on with the rest, so
# that turning locking on in a repository with many old issues stays within the
# limits of GitHub for writes.
LOCKS_PER_SWEEP = 25

# Pull requests merged this long ago still mark the issues they fix, in case the
# event of the merge was missed.
MERGE_LOOKBACK = timedelta(days=14)
# A sweep reads the merges of MERGE_LOOKBACK before its start and labels their
# issues later in its run, which the workflows stop after 20 minutes at most. So a
# fix counts for its label for this much longer, or one merged at the edge of the
# lookback would lose the release that ships it.
LABEL_SLACK = timedelta(hours=1)
# The limits of the AI runs that people who don't maintain the repository start,
# unless [engine] in config.toml sets others: analyses, follow-ups and replies after
# a release in 24 hours, analyses of one reporter's new issues in 24 hours, and the
# reporter's comments after a release that the AI reads.
DAY = timedelta(days=1)

LIMITS = ("max_per_day", "max_per_author_per_day", "release_replies")

# The most of one text, the issue's or a comment's, that the engine reads in
# issue.md; a longer one is cut off with a note.
MAX_RENDERED_TEXT = 20_000

CONTEXT_LIMITS = ("max_issues", "max_discussions", "max_analyzed")

# The words of that search, joined by OR: GitHub's search takes five operators.
SEARCH_TERMS = 6
# The names of errors among them, before the words of the title.
SEARCH_ERRORS = 3
# The longest word of it, so that the query stays within GitHub's 256 characters.
SEARCH_WORD = 40
# The excerpt of each other issue in issues.jsonl; issues/<number>.md has its text.
EXCERPT = 400

# The moment that it replays: a minute after the issue was opened, when it had the
# labels of its form and the analysis would have started.
OPENING = timedelta(minutes=1)

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

# The tasks that reporters start, which the limits of the AI runs apply to; a
# maintainer starts the others.
LIMITED = {"triage", "follow-up", "release-reply", "discussion-answer"}
# The tasks that judge one comment, the one that started them, and read nothing of
# the repository's issues besides their own.
REPLIES = ("maintainer-reply", "release-reply", "maintainer-question")

# The original that /assistant duplicate names: #12, GH-12 or 12, also after "of".
ORIGINAL = re.compile(r"(?:of[ \t]+)?(?:#|GH-)?(\d{1,10})(?![\w-])", re.I)

# The longest title of an issue that GitHub takes.
MAX_TITLE = 256

# The words of a text, which a search for an issue is made of.
WORD = re.compile(r"\w+")
# The code of a system error, such as ECONNREFUSED or ENOENT.
ERROR_CODE = re.compile(r"E[A-Z]{3,}")


@dataclass
class Plan:
    """What the event job did, and which task the engine gets next."""

    issue: int | None = None
    mode: str = "none"
    notes: list[str] = field(default_factory=list)
    # The assistant's comment that the task answers: the questions that a follow-up
    # takes up, the release that a reply after it is about, or the analysis that the
    # reporter's /assistant again repeats. Its marker records the run, with `marks`,
    # once the limits let it start.
    answers: dict[str, Any] | None = None
    marks: dict[str, str] = field(default_factory=dict)


# What an event on the issue of the digest does: comments and commands there start
# nothing, and the AI never reads it.
DIGEST_NOTE = "The issue of the digest: nothing to do."


def handle_event(
    github: GitHub, name: str, payload: dict[str, Any], config: Config
) -> Plan:
    if name == "workflow_dispatch":
        inputs = payload.get("inputs") or {}
        if "discussion" in inputs:
            return discussion_by_hand(github, int(inputs["discussion"]))
        number = int(inputs["issue"])
        issue = github.issue(number)
        if "pull_request" in issue:
            raise AssistantError(f"#{number} is a pull request, not an issue.")
        if is_digest(issue):
            return Plan(number, notes=[DIGEST_NOTE])
        mode = inputs.get("mode") or "triage"
        if mode not in MODES:
            raise AssistantError(f"Unknown mode {mode}.")
        add_form_labels(github, issue, config)
        return Plan(number, mode, [f"Started by hand: {mode}."])
    if name == "discussion":
        return handle_discussion(github, payload, config)
    issue = payload["issue"]
    number = issue["number"]
    if "pull_request" in issue or is_bot(payload.get("sender")):
        return Plan(number, notes=["Pull request or bot: nothing to do."])
    if is_digest(issue):
        return Plan(number, notes=[DIGEST_NOTE])
    action = payload.get("action")
    current = label_names(issue)
    author = login(issue.get("user"))
    if name == "issues" and action == "opened":
        add_form_labels(github, issue, config)
        # The owners of the areas of the form, by its labels or its area field,
        # without the AI; the first analysis assigns those of the areas that it adds
        # to an issue still without.
        areas = label_names(issue).union(form_labels(issue, config))
        assign_owners(github, issue, areas, config)
        if NEEDS_TRIAGE not in current:
            github.add_labels(number, [NEEDS_TRIAGE])
        if box_ticked(text_of(issue), config):
            return keep_out_the_ai(github, number, config)
        if is_maintainer(issue):
            return Plan(number, notes=["Opened by a maintainer: no analysis."])
        return limited(github, Plan(number, "triage"), issue, created(issue), config)
    if name == "issues" and action == "edited":
        changes = payload.get("changes") or {}
        if "body" not in changes:
            return Plan(number, notes=["A new title alone answers nothing."])
        by_reporter = login(payload.get("sender")) == author
        before = changes["body"].get("from")
        # GitHub shows the box of the form as one that a click ticks in the issue.
        clicked = by_reporter and box_ticked(text_of(issue), config) != box_ticked(
            before, config
        )
        plan = Plan(number)
        if clicked and box_ticked(text_of(issue), config):
            plan = keep_out_the_ai(github, number, config)
        if clicked and without_box(text_of(issue), config) == without_box(
            before, config
        ):
            # A click answers no question, and unticking the box starts nothing.
            plan.notes = plan.notes or ["Unticking the box alone starts nothing."]
            return plan
        if issue["state"] == "open" and by_reporter and current & {NEEDS_INFO, STALE}:
            # A follow-up of an edit that also ticked the box stops at the mute here.
            plan = answered(github, issue, config, plan)
            return limited(github, plan, issue, moment(issue["updated_at"]), config)
        plan.notes.append("Edit without a pending question.")
        return plan
    if name == "issue_comment" and action == "created":
        comment = payload["comment"]
        given = command_of(issue, comment)
        if given is None:
            return handle_comment(github, issue, comment, config)
        refused = refusal(comment, given)
        if not refused:
            return handle_command(github, issue, comment, given, config)
        # A line that is no command the author may give is an ordinary comment,
        # such as an objection or an answer with a typo in its first line.
        github.react(comment["id"], REFUSED)
        plan = handle_comment(github, issue, comment, config)
        plan.notes.insert(0, refused)
        return plan
    return Plan(number, notes=[f"Event {name}.{action} is not handled."])


def keep_out_the_ai(github: GitHub, number: int, config: Config) -> Plan:
    """The reporter ticked the box of [forms] opt_out_field: the issue is muted, so
    that no run of the AI starts on it, and all that runs without AI goes on."""
    mute(github, number, config)
    return Plan(
        number,
        notes=[
            f"The reporter ticked the box {config.opt_out_field!r}: muted on "
            f"#{number} ({MUTED}), no AI run; /assistant again of the reporter or "
            "/assistant unmute ends it."
        ],
    )


def discussion_by_hand(github: GitHub, number: int) -> Plan:
    """A run by hand of the workflow of the discussions: a first answer, also after
    an earlier one, as a maintainer started it."""
    if github.discussion(number) is None:
        raise AssistantError(f"#{number} is no discussion.")
    return Plan(number, "discussion-answer", ["Started by hand: discussion-answer."])


def handle_discussion(github: GitHub, payload: dict[str, Any], config: Config) -> Plan:
    """A new discussion gets a first answer when its category is one of
    [discussions] answer_categories and someone who doesn't maintain the
    repository opened it, within the limits of the day."""
    discussion = payload["discussion"]
    number = discussion["number"]
    action = payload.get("action")
    if action != "created":
        return Plan(number, notes=[f"Event discussion.{action} is not handled."])
    if is_bot(payload.get("sender")):
        return Plan(number, notes=["Bot: nothing to do."])
    if login(payload.get("sender")) != login(discussion.get("user")):
        # Someone else created it, as a maintainer who converts an issue does.
        return Plan(
            number, notes=["Created by someone else than its author: no answer."]
        )
    category = str((discussion.get("category") or {}).get("name") or "")
    if not answers_category(config, category):
        return Plan(
            number,
            notes=[
                f"The category {category!r} is not one of [discussions] "
                "answer_categories: no answer."
            ],
        )
    if is_maintainer(discussion):
        return Plan(number, notes=["Opened by a maintainer: no answer."])
    plan = Plan(number, "discussion-answer")
    return within_limits(github, plan, discussion, created(discussion), config)


def handle_comment(
    github: GitHub, issue: dict[str, Any], comment: dict[str, Any], config: Config
) -> Plan:
    """A new comment that gives no command: an answer, an objection, a maintainer's
    reply or a reason to reopen."""
    number = issue["number"]
    author = login(issue.get("user"))
    current = label_names(issue)
    if issue["state"] != "open":
        plan = reopen_if_answered(github, issue, comment, config)
        return limited(github, plan, comment, created(comment), config)
    plan = Plan(number)
    if POSSIBLE_DUPLICATE in current:
        # Every human comment after the notice stops the automatic closing.
        github.remove_label(number, POSSIBLE_DUPLICATE)
        plan.notes.append("A comment stopped the duplicate closing.")
    if login(comment.get("user")) == author:
        if current & {NEEDS_INFO, STALE}:
            plan = answered(github, issue, config, plan)
            return limited(github, plan, comment, created(comment), config)
        return plan
    if is_maintainer(comment):
        if STALE in current:
            github.remove_label(number, STALE)
        if NEEDS_INFO not in current and not is_maintainer(issue):
            plan.mode = "maintainer-reply"
            return limited(github, plan, comment, created(comment), config)
    return plan


def limited(
    github: GitHub,
    plan: Plan,
    trigger: dict[str, Any],
    at: datetime,
    config: Config,
) -> Plan:
    """The plan of a run of the AI that an event asks for, or none while the issue is
    muted or the run would pass a limit of the day; see within_limits.

    The labels are read anew: the event can have waited its turn behind the run
    that muted the issue, and its own copy of them is older.
    """
    if plan.mode == "none":
        return plan
    # An event of an issue always names it.
    assert plan.issue is not None
    if MUTED in label_names(github.issue(plan.issue)):
        return stopped(
            plan,
            f"The assistant is muted on #{plan.issue} ({MUTED}): no AI run; "
            "/assistant unmute ends it.",
        )
    return within_limits(github, plan, trigger, at, config)


def handle_command(
    github: GitHub,
    issue: dict[str, Any],
    comment: dict[str, Any],
    given: Command,
    config: Config,
) -> Plan:
    """Carry out the command of a maintainer or of the reporter and react to it:
    👀 when it is done or its task starts, 😕 when it is refused, with the reason
    in the summary of the run."""
    number = issue["number"]
    maintainer = is_maintainer(comment)
    plan = carry_out(github, issue, comment, given, maintainer, config)
    if isinstance(plan, str):
        github.react(comment["id"], REFUSED)
        return Plan(number, notes=[plan])
    github.react(comment["id"], HANDLED)
    return plan


def carry_out(
    github: GitHub,
    issue: dict[str, Any],
    comment: dict[str, Any],
    given: Command,
    maintainer: bool,
    config: Config,
) -> Plan | str:
    """What the command did and the task it gives the engine, or why it is refused."""
    number = issue["number"]
    name = given.name
    if name == "mute":
        mute(github, number, config)
        return Plan(number, notes=[f"Muted on #{number}: no AI run of its own."])
    if name == "unmute":
        github.remove_label(number, MUTED)
        return Plan(number, notes=[f"Unmuted on #{number}."])
    if issue["state"] != "open":
        return f"#{number} is closed; only mute and unmute work on a closed issue."
    if name == "again":
        return again(github, issue, comment, maintainer, config)
    if name == "duplicate":
        return mark_duplicate(github, issue, given.argument, config)
    if name == "not-duplicate":
        github.remove_label(number, POSSIBLE_DUPLICATE)
        return Plan(number, notes=[f"#{number} is no duplicate: it stays open."])
    if name == "waiting":
        if NEEDS_INFO in label_names(issue):
            return Plan(number, notes=[f"#{number} already waits for the reporter."])
        github.add_labels(number, [NEEDS_INFO])
        return Plan(number, notes=[f"#{number} waits for the reporter."])
    if not given.argument:
        return "/assistant ask needs a question after it."
    return Plan(number, "maintainer-question", ["A maintainer asked a question."])


def mute(github: GitHub, number: int, config: Config) -> None:
    """Add the label of muted issues to the issue, which creates it where it is
    missing."""
    if not any(
        label["name"].casefold() == MUTED.casefold() for label in github.labels()
    ):
        github.create_label(muted_label(config))
    github.add_labels(number, [MUTED])


def again(
    github: GitHub,
    issue: dict[str, Any],
    comment: dict[str, Any],
    maintainer: bool,
    config: Config,
) -> Plan | str:
    """A new first analysis, which decides anew about a duplicate.

    A maintainer may ask for one at any time. The reporter may ask once per issue,
    while it waits for the maintainer's triage and after an analysis: its marker
    records the run, within the limits of the day, so that a deleted comment gives
    no second one. A reporter who kept the AI out with the box of the form lets it in
    this way, and ends that mute with it, and no other. Without an analysis, a comment
    of the assistant keeps that record, as nothing else would count the run or the
    request; see render_let_in.
    """
    number = issue["number"]
    plan = Plan(number, "triage", ["New analysis on request."])
    if not maintainer:
        if NEEDS_TRIAGE not in label_names(issue):
            return (
                f"The reporter of #{number} may ask for a new analysis only while it "
                f"has {NEEDS_TRIAGE}."
            )
        comments = github.comments(number)
        records = [item for item in comments if kind_of(item) in {"analysis", LET_IN}]
        own_mute = muted_by_reporter(github, issue, comments, config)
        if not records and not own_mute:
            return f"#{number} has no analysis that the reporter could ask again."
        if any("again" in (marker(item) or ("", {}))[1] for item in records):
            return f"The reporter of #{number} asked for a new analysis once already."
        # A comment that lets the AI in carries the mark from its start, so only
        # analyses are left here.
        if records:
            plan.answers, plan.marks = records[-1], {"again": "1"}
        at = created(comment)
        if own_mute:
            plan = within_limits(github, plan, comment, at, config, requested=True)
        else:
            plan = limited(github, plan, comment, at, config)
        if plan.mode == "none":
            return plan.notes[-1]
        if not records:
            language = issue_language(issue, comments)
            github.comment(number, render_let_in(login(issue["user"]), language, at))
        if own_mute:
            github.remove_label(number, MUTED)
    if POSSIBLE_DUPLICATE in label_names(issue):
        # The new analysis adds it again, with a new notice, if it finds one.
        github.remove_label(number, POSSIBLE_DUPLICATE)
    return plan


def muted_by_reporter(
    github: GitHub,
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
    config: Config,
) -> bool:
    """Whether the issue is muted by the box of its form, which the reporter may undo.

    A maintainer's mute, with /assistant mute or by hand, is not theirs to end, also
    when they add the box to the text afterwards: a label that a person added, and
    the command, tell the two apart, as the command adds it as a bot does.
    """
    if MUTED not in label_names(issue) or config.opt_out_field not in form_fields(
        text_of(issue)
    ):
        return False
    given = [command_of(issue, item) for item in comments if is_maintainer(item)]
    if any(item is not None and item.name == "mute" for item in given):
        return False
    return not any(
        event["event"] == "labeled"
        and (event.get("label") or {}).get("name") == MUTED
        and not is_bot(event.get("actor"))
        for event in github.events(issue["number"])
    )


def mark_duplicate(
    github: GitHub, issue: dict[str, Any], argument: str, config: Config
) -> Plan | str:
    """The notice of a duplicate that a maintainer named, with possible-duplicate;
    the sweep closes the issue after the same days as an analysis's notice, unless
    someone objects."""
    number = issue["number"]
    found = ORIGINAL.match(argument)
    if not found:
        return (
            "/assistant duplicate needs the original, such as /assistant duplicate #12."
        )
    named = int(found[1])
    original = other_issue(github, named, number) if named <= MAX_NUMBER else None
    if original is None or original["state"] != "open":
        return f"#{named} is no other open issue of this repository."
    exempt = exemption(issue, config.lifecycle)
    if exempt:
        # The sweep would only take possible-duplicate away again.
        return f"#{number} is exempt ({exempt}) and never closes as a duplicate."
    # Without its notice, possible-duplicate closes nothing.
    github.add_labels(number, [POSSIBLE_DUPLICATE])
    language = issue_language(issue, github.comments(number))
    github.comment(
        number, render_duplicate(named, login(issue.get("user")), language, config)
    )
    return Plan(
        number,
        notes=[
            f"Marked as a duplicate of #{named}; it closes in "
            f"{config.lifecycle.duplicate_grace_days} days unless someone objects."
        ],
    )


def add_form_labels(github: GitHub, issue: dict[str, Any], config: Config) -> None:
    missing = [
        name for name in form_labels(issue, config) if name not in label_names(issue)
    ]
    github.add_labels(issue["number"], missing)


def answered(
    github: GitHub, issue: dict[str, Any], config: Config, plan: Plan | None = None
) -> Plan:
    """The reporter answered: the issue no longer waits, and the assistant may follow up."""
    number = issue["number"]
    plan = plan or Plan(number)
    for name in (NEEDS_INFO, STALE):
        if name in label_names(issue):
            github.remove_label(number, name)
    plan.notes.append("The reporter answered.")
    asked = follow_up_due(
        github.comments(number),
        login(issue.get("user")),
        config.lifecycle.max_follow_ups,
    )
    if asked:
        plan.mode = "follow-up"
        plan.answers = asked
    return plan


def within_limits(
    github: GitHub,
    plan: Plan,
    trigger: dict[str, Any],
    at: datetime,
    config: Config,
    *,
    requested: bool = False,
) -> Plan:
    """The plan, or none when its run of the AI would pass a limit of the day.

    `trigger` is the new issue, the edit or the comment that asks for the run, and
    `at` its time; the day is the 24 hours before. Maintainers are never limited,
    and runs by hand never get here. Every new issue of the day that someone who
    doesn't maintain the repository opened counts, also one that a limit kept from
    the AI, so that a flood of issues at once, or issues that keep the AI from
    answering, start no more runs than the limit. A new issue counts only the
    issues opened before it, and only an analysis has a limit for each reporter.
    Every follow-up and reply after a release of the day counts too: as it starts,
    the run is recorded in the marker of the assistant's comment that it answers,
    so it counts also while it still runs. Decision 0009 says why. The new
    discussions of the categories that get a first answer count as new issues do,
    in one count with them, while [discussions] names any; issues and discussions
    share their numbers. Decision 0018 says why.
    """
    if plan.mode not in LIMITED or is_maintainer(trigger):
        return plan
    per_day = limit(config, "max_per_day")
    per_author = limit(config, "max_per_author_per_day")
    since = at - DAY
    # A new issue or discussion counts only those opened before it; a new analysis
    # that the reporter asked for counts every one of the day, as a follow-up does.
    first = (
        plan.mode in {"triage", "discussion-answer"}
        and plan.answers is None
        and not requested
    )
    opened = github.issues_since(since)
    if config.answer_categories:
        opened += [
            item
            for item in github.discussions_since(since)
            if answers_category(config, item["category"]["name"])
        ]
    reporters = [
        login(item.get("user"))
        for item in opened
        if since <= created(item) <= at
        and not is_bot(item.get("user"))
        and not is_maintainer(item)
        and (not first or item["number"] < plan.issue)
    ]
    # Recording a run changes the comment, so the comments that changed since then
    # hold every recorded run of the day.
    answers = sum(
        moment >= since.timestamp()
        for comment in github.comments_since(since)
        for moment in runs_of(comment)
    )
    if len(reporters) + answers >= per_day:
        return stopped(
            plan,
            f"Daily limit of AI runs reached (max_per_day = {per_day}); "
            "the maintainer takes over.",
        )
    if first and reporters.count(login(trigger.get("user"))) >= per_author:
        return stopped(
            plan,
            "Daily limit of analyses for this reporter reached "
            f"(max_per_author_per_day = {per_author}); the maintainer takes over.",
        )
    if plan.answers:
        record_run(github, plan.answers, at, plan.marks)
    return plan


def stopped(plan: Plan, note: str) -> Plan:
    """No task for the engine after all; the note says why, in the summary and as a
    notice of the run."""
    command("notice", note)
    return Plan(plan.issue, notes=[*plan.notes, note])


def reopen_if_answered(
    github: GitHub, issue: dict[str, Any], comment: dict[str, Any], config: Config
) -> Plan:
    """Reopen an issue the assistant closed once its reporter answers.

    An issue closed unanswered reopens at once; for one that a release closed, the
    engine decides whether the comment says that the problem persists, up to
    release_replies times after each release. The marker of the assistant's
    comment about the release counts these runs, so a reporter who deletes their
    comments gets no more of them.
    """
    number = issue["number"]
    author = login(issue.get("user"))
    if login(comment.get("user")) != author:
        return Plan(number, notes=["Comment on a closed issue."])
    events = github.events(number)
    closes = [event for event in events if event["event"] == "closed"]
    if not closes or login(closes[-1].get("actor")) != BOT:
        return Plan(number, notes=["Closed by a person: stays closed."])
    comments = github.comments(number)
    # The closing comment of the last closing: one from before a reopening belongs
    # to an earlier one, and another workflow may have closed the issue since.
    reopenings = [created(event) for event in events if event["event"] == "reopened"]
    closings = [
        item
        for item in comments
        if kind_of(item) in {"closed-unanswered", "closed-duplicate", "released"}
        and (not reopenings or created(item) > reopenings[-1])
    ]
    if closings and kind_of(closings[-1]) == "released":
        released = closings[-1]
        window = timedelta(days=config.lifecycle.reopen_after_release_days)
        if created(comment) - created(released) > window:
            return Plan(number, notes=["The release closed this issue long ago."])
        replies = limit(config, "release_replies")
        if len(runs_of(released)) >= replies:
            return stopped(
                Plan(number),
                "Limit of comments read after the release reached "
                f"(release_replies = {replies}); the maintainer takes over.",
            )
        return Plan(
            number,
            "release-reply",
            ["The reporter wrote after the release."],
            answers=released,
        )
    # Whatever its labels say: the reporter's answer can come in while the sweep
    # closes the issue, end the waiting, and remove them.
    if not closings or kind_of(closings[-1]) != "closed-unanswered":
        return Plan(number, notes=["Not closed for a missing answer."])
    github.reopen(number)
    for name in (STALE, NEEDS_INFO):
        github.remove_label(number, name)
    github.add_labels(number, [NEEDS_TRIAGE])
    language = issue_language(issue, comments)
    github.comment(number, render_reopened(author, language))
    plan = Plan(number, notes=["Reopened after the reporter's answer."])
    asked = follow_up_due(comments, author, config.lifecycle.max_follow_ups)
    if asked:
        plan.mode = "follow-up"
        plan.answers = asked
    return plan


def excerpt(text: str, limit: int) -> str:
    """The text of an issue in short: form answers as lines, without empty answers.

    The whole text is read, because comments and empty answers can come before the
    words that count, such as the instructions of an issue template; each step takes
    time linear in its length.
    """
    text = (text or "")[:MAX_TEXT]
    fields = form_fields(text)
    if fields:
        text = "\n".join(f"{key}: {value}" for key, value in fields.items() if value)
    comments, rest = comment_parts(text)
    text = HTML_COMMENT.sub("", comments) + rest
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def describe_author(item: dict[str, Any], reporter: str, role: str = "reporter") -> str:
    """Who wrote an issue or a comment, with their roles: `role` for `reporter`,
    who opened the issue or the discussion, and maintainer."""
    name = login(item.get("user"))
    if name == BOT and marker(item):
        return "the issue assistant (you, earlier)"
    roles = []
    if name == reporter:
        roles.append(role)
    if is_maintainer(item):
        roles.append("maintainer")
    return f"@{name}" + (f" ({', '.join(roles)})" if roles else "")


def render_issue(
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
    visible: Visible | None = None,
) -> str:
    """issue.md: the issue and its comments, each text in a code fence of its own.

    Who wrote a text, in which role and when stands only in the headings and lines
    that the script writes outside the fences, and nothing in a text can close its
    fence, so no text can pass for another comment, for the maintainer or for the
    assistant.
    """
    visible = visible or Visible()
    reporter = login(issue.get("user"))
    lines = [
        f"# Issue #{issue['number']}: {one_line(visible(issue['title']))}",
        "",
        f"- Reporter: {describe_author(issue, reporter)}",
        f"- State: {issue['state']}",
        f"- Labels: {', '.join(sorted(label_names(issue))) or 'none'}",
        f"- Opened: {issue['created_at'][:10]}",
        "",
        "## Text of the issue",
        "",
        rendered_text(text_of(issue), visible),
    ]
    for index, comment in enumerate(comments, start=1):
        lines += [
            "",
            f"## Comment {index} by {describe_author(comment, reporter)}, "
            f"{comment['created_at'][:10]}",
            "",
            rendered_text(text_of(comment), visible),
        ]
    return "\n".join(lines) + "\n"


def render_discussion(
    discussion: dict[str, Any], visible: Visible | None = None
) -> str:
    """discussion.md: the discussion, its comments and their replies, each text in a
    code fence of its own, as issue.md shows an issue, so that no text can pass for
    another one, for a maintainer or for the assistant."""
    visible = visible or Visible()
    author = login(discussion.get("user"))

    def by(item: dict[str, Any]) -> str:
        return f"{describe_author(item, author, 'author')}, {item['created_at'][:10]}"

    state = discussion["state"] + (", answered" if discussion["answered"] else "")
    lines = [
        f"# Discussion #{discussion['number']}: "
        f"{one_line(visible(discussion['title']))}",
        "",
        f"- Author: {describe_author(discussion, author, 'author')}",
        f"- Category: {one_line(visible(discussion['category']['name']))}",
        f"- State: {state}",
        f"- Opened: {discussion['created_at'][:10]}",
        "",
        "## Text of the discussion",
        "",
        rendered_text(text_of(discussion), visible),
    ]
    for index, comment in enumerate(discussion["comments"], start=1):
        lines += [
            "",
            f"## Comment {index} by {by(comment)}",
            "",
            rendered_text(text_of(comment), visible),
        ]
        for number, reply in enumerate(comment["replies"], start=1):
            lines += [
                "",
                f"### Reply {number} to comment {index} by {by(reply)}",
                "",
                rendered_text(text_of(reply), visible),
            ]
    later = discussion["comment_count"] - len(discussion["comments"])
    if later > 0:
        lines += ["", f"({plural(later, 'later comment')} not shown.)"]
    return "\n".join(lines) + "\n"


def rendered_text(text: str, visible: Visible) -> str:
    """A text for issue.md: without HTML and invisible characters, cut off after
    MAX_RENDERED_TEXT, and in a code fence that nothing in it can close.

    The HTML goes first, as GitHub hides it: an invisible character in the end of
    an HTML comment keeps it from ending on GitHub, and taken out first, it would end
    the comment and show the text after it to the engine. The invisible characters
    go before the fence is chosen, so that none can split a run of backticks that
    would close it.
    """
    text = visible(strip_html(text)).strip()
    if not text:
        return "(empty)"
    note = ""
    if len(text) > MAX_RENDERED_TEXT:
        text, note = text[:MAX_RENDERED_TEXT].rstrip(), "\n\n(shortened)"
    marks = fence(text)
    return f"{marks}\n{text}\n{marks}{note}"


def render_labels(config: Config) -> str:
    lines = ["# Labels you may choose", ""]
    groups = [("kind", "type"), ("areas", "area"), ("topics", "topic")]
    # Priorities are a choice of the repository: without them, nothing asks for one.
    if config.group("priority"):
        groups.append(("priority", "priority"))
    for title, name in groups:
        lines += [f"## {title}", ""]
        lines += [
            f"- `{label.name}`: {label.description}"
            for label in config.labels
            if label.group == name
        ] or ["- none"]
        lines.append("")
    lines.append('A kind of "spam" marks advertising or nonsense as invalid.')
    return "\n".join(lines) + "\n"


def render_judged(
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
    judged: dict[str, Any] | None,
) -> str:
    """comment.md: the comment that a reply task judges, as issue.md shows it."""
    if judged is None:
        return (
            "# No comment to judge\n\n"
            "The comment that this task judges was deleted, or there is none.\n"
        )
    index = next(
        number for number, item in enumerate(comments, start=1) if item is judged
    )
    author = describe_author(judged, login(issue.get("user")))
    return (
        f"# Comment {index} of issue #{issue['number']} by {author}, "
        f"{judged['created_at'][:10]}\n\n"
        f"{rendered_text(text_of(judged), Visible())}\n"
    )


def search_terms(issue: dict[str, Any]) -> list[str]:
    """The words of the search for older issues like this one: the names of errors in
    its text, such as TypeError or ECONNREFUSED, then the words of its title.

    Words alone, in lower case, so that none is a qualifier of the search, such as
    repo:, or one of its operators, such as OR; none longer than SEARCH_WORD, so
    that the query stays short enough for GitHub.
    """
    errors = [
        word
        for word in WORD.findall(text_of(issue))
        if word.endswith(("Error", "Exception")) or ERROR_CODE.fullmatch(word)
    ]
    words = list(dict.fromkeys(errors))[:SEARCH_ERRORS] + WORD.findall(issue["title"])
    terms = [
        word.lower()
        for word in words
        if 2 < len(word) <= SEARCH_WORD
        and not word.isdigit()
        and word.lower() not in COMMON_WORDS
    ]
    return list(dict.fromkeys(terms))[:SEARCH_TERMS]


def related_issues(
    github: GitHub, issue: dict[str, Any], mode: str, config: Config
) -> list[dict[str, Any]]:
    """The other issues that the engine reads, the newest first: every open one, the
    max_issues closed ones that changed last and, for a first analysis, those that a
    search for its words finds among the older ones; none whose reporter keeps the AI
    out of it."""
    found = {
        item["number"]: item
        for item in github.recent_issues(limit(config, "max_issues"))
    }
    terms = search_terms(issue) if mode in {"triage", *DISCUSSION_MODES} else []
    if terms:
        try:
            for item in github.search_issues(terms):
                found.setdefault(item["number"], item)
        except GitHubError as error:
            # The issues above are the context; the search only adds to them.
            command("warning", f"The search for older issues failed: {error}")
    found.pop(issue["number"], None)
    return [
        found[number]
        for number in sorted(found, reverse=True)
        if not is_digest(found[number]) and not opted_out(found[number], config)
    ]


def render_other_issue(
    item: dict[str, Any], visible: Visible, repository: str = ""
) -> str:
    """issues/<number>.md: the whole text of another issue, for the engine to read
    when its excerpt in issues.jsonl looks promising. For an issue of a related
    repository the heading names that repository, so its number is never taken for
    one of this repository."""
    return (
        f"# Issue {repository}#{item['number']}: "
        f"{one_line(visible(item['title']))}\n\n"
        f"{rendered_text(text_of(item), visible)}\n"
    )


def issue_line(
    item: dict[str, Any], visible: Visible, **more: object
) -> dict[str, Any]:
    """The line of an issue in issues.jsonl and related-issues.jsonl, with `more`
    before its excerpt."""
    return {
        "number": item["number"],
        "title": visible(item["title"]),
        "state": item["state"],
        "state_reason": item.get("state_reason"),
        "labels": sorted(label_names(item)),
        "opened": item["created_at"][:10],
        "closed": (item.get("closed_at") or "")[:10] or None,
        "comments": item.get("comments", 0),
        **more,
        "excerpt": visible(excerpt(item.get("body") or "", EXCERPT)),
    }


def write_lines(path: Path, items: Iterable[dict[str, Any]]) -> None:
    """A file of JSON Lines: one JSON object on each line."""
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items),
        encoding="utf-8",
    )


# What a context writes besides rules.md, prompt.md and schema.json, each only for
# the tasks that need it. A run in the same checkout removes what an earlier one
# wrote, so that a reply task never reads the other issues of an analysis, and the
# answer to a discussion never the issue of an earlier task.
CONTEXT_FILES = (
    "issue.md",
    "discussion.md",
    "comment.md",
    "labels.md",
    "issues.jsonl",
    "discussions.jsonl",
    "corrections.md",
    "related-issues.jsonl",
)


def language_list() -> str:
    """The languages of the catalogs, as the rules of the prompt list them for
    `{languages}`: the code of each, its name and how it addresses a reporter."""
    return "\n".join(
        f'  - `"{code}"`: {locale.name}'
        + (
            f', addressing the reporter with "{locale.address}"'
            if locale.address
            else ""
        )
        for code, locale in LOCALES.items()
    )


def write_context(
    github: GitHub,
    number: int,
    mode: str,
    config: Config,
    comment_id: int | None = None,
) -> dict[str, str]:
    """Write the files that the task of the engine reads and return the prompts and
    the schema: the task, which the rules of rules.md precede as the engine's system
    prompt, and both in one prompt for a template that passes no system prompt.

    Every task of an issue reads the issue with its comments. A reply task judges
    one comment, `comment_id`, the one whose event started the run, and reads
    nothing else; the others read the labels, the other issues and the discussions,
    up to the numbers of [context], and a first analysis also what the maintainers
    corrected of the earlier analyses. The first answer to a discussion reads the
    discussion with its comments, the issues, also those that a search for its
    words finds, and the other discussions.
    """
    if mode not in MODES:
        raise AssistantError(f"Unknown mode {mode}.")
    folder = config.root / CONTEXT
    if mode in DISCUSSION_MODES:
        found = github.discussion(number)
        if found is None:
            raise AssistantError(f"#{number} is no discussion.")
        issue, comments = found, []
    else:
        issue, comments = github.issue(number), github.comments(number)
    folder.mkdir(parents=True, exist_ok=True)
    for texts in ("issues", "related"):
        shutil.rmtree(folder / texts, ignore_errors=True)
    for name in CONTEXT_FILES:
        (folder / name).unlink(missing_ok=True)
    # The invisible characters of the issue, and of the texts that every analysis
    # reads, in which anyone could plant instructions for later issues. They go after
    # the HTML comments, as in rendered_text, so that the engine reads no text that
    # GitHub hides.
    own, elsewhere = Visible(), Visible()
    if mode in DISCUSSION_MODES:
        # The discussion and what an analysis reads of the repository; no labels,
        # which a discussion doesn't get.
        (folder / "discussion.md").write_text(
            render_discussion(issue, own), encoding="utf-8"
        )
        write_related(github, folder, issue, mode, config, elsewhere)
    else:
        (folder / "issue.md").write_text(
            render_issue(issue, comments, own), encoding="utf-8"
        )
        if mode in REPLIES:
            judged = judged_comment(issue, comments, mode, comment_id)
            (folder / "comment.md").write_text(
                render_judged(issue, comments, judged), encoding="utf-8"
            )
        else:
            (folder / "labels.md").write_text(render_labels(config), encoding="utf-8")
            others = write_related(github, folder, issue, mode, config, elsewhere)
            if mode == "triage":
                (folder / "corrections.md").write_text(
                    render_learned(github, config, issue, comments, others),
                    encoding="utf-8",
                )
                write_siblings(github, folder, issue, config, elsewhere)
    if own.removed or elsewhere.removed:
        subject = "discussion" if mode in DISCUSSION_MODES else "issue"
        command(
            "notice",
            f"Removed {own.removed + elsewhere.removed} invisible characters, such "
            "as zero-width spaces and Unicode tags, from the texts for the engine: "
            f"{own.removed} from {subject} #{number}, {elsewhere.removed} from other "
            "issues and discussions.",
        )
    values = {
        "repository": config.repository,
        "issue": str(number),
        "context": str(CONTEXT),
        "max_issues": str(config.max_issues),
        "max_discussions": str(config.max_discussions),
        "max_analyzed": str(config.max_analyzed),
        "languages": language_list(),
        "maintainers": LOCALES[maintainers(config)].name,
        "maintainers_code": maintainers(config),
        "priority": PRIORITY_RULE if config.group("priority") else "",
        "newcomer": NEWCOMER_RULE if config.group("contribution") else "",
        **related_rules(config),
    }

    def render(name: str) -> str:
        text = (PROMPTS / name).read_text(encoding="utf-8")
        for key, value in values.items():
            text = text.replace("{" + key + "}", value)
        if "{release}" in text:
            # Only the tasks that weigh what a release changed ask GitHub for it.
            text = text.replace("{release}", github.latest_release() or "none yet")
        # Last, so that braces in the project's own text stay as they are.
        return text.replace("{project}", config.project)

    engine = ENGINES[engine_named(config.engine)]
    rules, task = render("rules.md"), render(f"{mode}.md")
    if engine.schema == IN_PROMPT:
        # An engine that can't enforce the schema reads it in the folder.
        task = f"{task.rstrip()}\n\n{render('answer.md')}"
    schema = schema_json(mode, config, strict=engine.schema == STRICT)
    # The rules come before everything else as the engine's system prompt, apart
    # from its task and from the texts of GitHub that it reads in the folder.
    (folder / "rules.md").write_text(rules, encoding="utf-8")
    (folder / "prompt.md").write_text(task, encoding="utf-8")
    (folder / "schema.json").write_text(schema, encoding="utf-8")
    return {
        "prompt": f"{rules}\n\n{task}",
        "task": task,
        "schema": schema,
        "engine-args": engine_arguments(engine, rules),
    }


def toml_string(text: str) -> str:
    """The text as a basic string of TOML, which takes no control character as it is."""
    escapes = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}
    return (
        '"'
        + "".join(
            escapes.get(character)
            or (
                f"\\u{ord(character):04x}"
                if unicodedata.category(character) == "Cc"
                else character
            )
            for character in text
        )
        + '"'
    )


def engine_arguments(engine: Engine, rules: str) -> str:
    """The arguments that the engine's action adds to its command line, as JSON,
    with the rules as a string of TOML; empty for an engine without any."""
    if not engine.action_arguments:
        return ""
    rules_toml = toml_string(rules)
    return json.dumps(
        [
            argument.replace("{rules}", rules_toml)
            for argument in engine.action_arguments
        ]
    )


# How the AI chooses the priority, in the step of the labels of an analysis and of a
# follow-up; only where labels.toml has priorities, as the schema of the answer.
PRIORITY_RULE = (
    " `priority` is the one of the priorities in `labels.md` whose description fits "
    "the issue best, judged by what the issue shows: how many people it affects, how "
    "badly, and whether there is a workaround; `priority_rationale` says why in one "
    'sentence. Choose `"none"` when the issue shows too little to judge.'
)

NEWCOMER_RULE = (
    "\n   - `newcomer`: whether someone new to the project could take this on. "
    "`fit` is true only for a confirmed, small, local change: the issue shows what is "
    "wrong or what to write, your investigation located it in the files that you name "
    "in `references`, and it takes no knowledge of the whole project, such as a bug "
    "with a located cause or a fix of the documentation. It is false for a security "
    "report, a duplicate, an open design question and anything that still needs "
    "information from the reporter or a decision of a maintainer, and whenever you "
    "doubt. `reason` says why in one sentence, in the language that the maintainers "
    "read, since nobody else reads it. For a security report, `fit` is false and "
    "`reason` stays empty, because the log of the run can be public. The maintainers "
    "alone decide what to offer to newcomers, so this field changes nothing that "
    "anyone sees: don't mention "
    "it in your other texts, and don't invite anyone to work on the issue."
)


def write_related(
    github: GitHub,
    folder: Path,
    issue: dict[str, Any],
    mode: str,
    config: Config,
    visible: Visible,
) -> list[dict[str, Any]]:
    """What the engine reads of the rest of the repository: the other issues, each
    with an excerpt in issues.jsonl and its whole text in issues/<number>.md, and
    the other discussions in discussions.jsonl. Returns the other issues.

    `visible` counts the invisible characters of the whole texts; the titles and
    excerpts of issues.jsonl come from the same texts and don't count them again.
    """
    others = related_issues(github, issue, mode, config)
    texts = folder / "issues"
    texts.mkdir()
    for item in others:
        (texts / f"{item['number']}.md").write_text(
            render_other_issue(item, visible), encoding="utf-8"
        )
    quiet = Visible()
    write_lines(
        folder / "issues.jsonl",
        (issue_line(item, quiet, closed_by=item.get("closed_by")) for item in others),
    )
    write_lines(
        folder / "discussions.jsonl",
        (
            {
                "number": item["number"],
                "title": visible(item["title"]),
                "category": (item.get("category") or {}).get("name"),
                "answered": item.get("isAnswered"),
                "closed": item.get("closed"),
                "excerpt": visible(excerpt(item.get("body") or "", 1000)),
                "answer": visible(
                    excerpt((item.get("answer") or {}).get("body") or "", 1000)
                )
                or None,
            }
            for item in github.discussions(limit(config, "max_discussions"))
            if item["number"] != issue["number"]
        ),
    )
    return others


def write_siblings(
    github: GitHub,
    folder: Path,
    issue: dict[str, Any],
    config: Config,
    visible: Visible,
) -> None:
    """related-issues.jsonl, for a first analysis in a repository with related
    repositories: the issues of each that GitHub's search finds for the words of the
    issue, each with its repository and an excerpt, and its whole text in
    related/<owner>/<name>/<number>.md.

    Only public repositories count, as GitHub.sibling says. The search adds to the
    context, as the one for older issues does, so a repository that GitHub fails to
    search is a warning.
    """
    names = siblings(config)
    if not names:
        return
    terms = search_terms(issue)
    found: list[tuple[str, dict[str, Any]]] = []
    for repository in names if terms else []:
        try:
            other = github.sibling(repository)
            if other is None:
                command(
                    "warning",
                    f"[related] {repository} is no public repository, so the "
                    "analysis reads none of its issues.",
                )
                continue
            found += [
                (repository, item)
                for item in other.search_issues(terms)
                if not is_digest(item) and not opted_out(item, config)
            ]
        except GitHubError as error:
            command("warning", f"The search in {repository} failed: {error}")
    for repository, item in found:
        texts = folder / "related" / repository
        texts.mkdir(parents=True, exist_ok=True)
        (texts / f"{item['number']}.md").write_text(
            render_other_issue(item, visible, repository), encoding="utf-8"
        )
    quiet = Visible()
    write_lines(
        folder / "related-issues.jsonl",
        ({"repository": name, **issue_line(item, quiet)} for name, item in found),
    )


def related_rules(config: Config) -> dict[str, str]:
    """What the task of a first analysis says of the related repositories, for
    `{related_files}` and `{related_rule}`; nothing without any."""
    names = siblings(config)
    if not names:
        return {"related_files": "", "related_rule": ""}
    listed = ", ".join(f"`{name}`" for name in names)
    which = (
        f"repository {listed}, which shares"
        if len(names) == 1
        else f"repositories {listed}, which share"
    )
    return {
        "related_files": (
            f"\n- `{CONTEXT}/related-issues.jsonl`: the issues of the related {which} "
            "users and topics with this one, that a search for the words of this "
            "issue found there, in the form of "
            "`issues.jsonl` with the `repository` of each. "
            f"`{CONTEXT}/related/<owner>/<name>/<number>.md` holds the whole text of "
            "each of them."
        ),
        "related_rule": (
            "\n   - Search `related-issues.jsonl` the same way. An issue there can be "
            "`related`, with its `repository`, but never one of the `duplicates`, "
            "which are issues of this repository alone. The other items of "
            f"`related` have the `repository` `{config.repository}`. Refer to an "
            "issue of a related repository as owner/name#123 in your texts."
            "\n   - `belongs_in`: the related repository that the issue is about, "
            "when it was clearly filed in the wrong repository, such as a problem "
            "in the code or the documentation of that project, and `analysis` says "
            f"why; otherwise `{config.repository}`. Nothing moves the issue: the "
            "maintainers decide."
        ),
    }


# Runs a step of the sweep, named for the log, and returns what it did.
Isolate = Callable[[str, Callable[[], list[str]]], list[str]]


def directly(name: str, step: Callable[[], list[str]]) -> list[str]:
    """Run the step; an error of GitHub stops everything."""
    return step()


def sweep(
    github: GitHub,
    now: datetime,
    releases: bool = True,
    lifecycle: Lifecycle = DEFAULT_LIFECYCLE,
) -> list[str]:
    """The whole lifecycle; every run does all of it, so a missed run loses nothing.

    Every step and every issue runs on its own: one that GitHub fails becomes a
    warning, the others go on, and SweepIncomplete names it at the end. Each step
    can run twice, so the next sweep completes what one stopped.
    """
    failed: list[str] = []

    def isolate(name: str, step: Callable[[], list[str]]) -> list[str]:
        start = len(github.writes)
        try:
            return step()
        except GitHubError as error:
            failure = f"{name}: {error}"
            command("warning", failure)
            failed.append(failure)
            # What the step wrote before GitHub stopped it, and the write that failed.
            return github.writes[start:]

    done = []
    if releases:
        done += isolate(
            "Marking the fixed issues", partial(mark_fixed, github, now, isolate)
        )
        done += isolate(
            "Closing the released issues", partial(close_released, github, isolate)
        )
    for label, step in (
        (NEEDS_INFO, sweep_waiting),
        (POSSIBLE_DUPLICATE, sweep_duplicate),
    ):
        done += isolate(
            f"The issues labeled {label}",
            partial(sweep_labeled, github, label, step, now, lifecycle, isolate),
        )
    if lifecycle.lock_closed_after_days:
        done += isolate(
            "Locking the issues closed long ago",
            partial(lock_closed, github, now, lifecycle, isolate),
        )
    if failed:
        raise SweepIncomplete(done, failed)
    return done


# How far back the summary of the sweep looks: for the 👍 and 👎 that the footers of
# the analyses and follow-ups ask for, the conversations of the digest and the
# numbers of the quality.
FEEDBACK_PERIOD = timedelta(days=30)

# The sweeps whose summary counts the feedback and has the digest: the morning's,
# one started by hand and one on a computer, without an event. A merge or a release
# starts a sweep too, often many a day, and each lists every comment of the 30 days,
# which would spend the requests that the token has for the hour.
FEEDBACK_EVENTS = frozenset({"schedule", "workflow_dispatch", ""})

# An issue that needs triage waits on the maintainers once it is this old.
TRIAGE_WAIT = timedelta(days=3)

# The issues that each list of the digest names at most, the longest waiting first.
DIGEST_ITEMS = 20

# A section of the summary that GitHub failed; see part.
NOT_MADE = "Not made: GitHub failed."

# The text of the issue of the digest above its sections; see digest_body.
DIGEST_INTRO = (
    "What waits on whom, from the morning sweep. The sweep writes this text anew "
    "every morning, which notifies nobody, so a change here doesn't last. "
    "`[digest] issue` in `.github/issue-assistant/config.toml` turns it off."
)

# The numbers of the quality in the text of the issue of the digest, up to their day.
HELD_QUALITY = re.compile(r"^#### Assistant quality .*?^Counted on \S+$", re.M | re.S)


def asks(comment: dict[str, Any]) -> bool:
    """Whether the comment is an analysis or a follow-up with questions."""
    found = marker(comment)
    return (
        found is not None
        and found[0] in {"analysis", "follow-up"}
        and found[1].get("questions") == "1"
    )


def feedback(activity: Activity) -> str:
    """The 👍 and 👎 on the analyses and follow-ups of the last 30 days, for the
    summary of the sweep: numbers only, which tell the maintainers whether the
    assistant helps.

    GitHub counts the reactions of each comment in its list of the comments, so
    one list of the comments of the repository since then is enough.
    """
    analyses, up, down = reactions_on(activity, "analysis")
    follow_ups, up_after, down_after = reactions_on(activity, "follow-up")
    return (
        f"Feedback of the last {FEEDBACK_PERIOD.days} days: analyses {analyses}, "
        f"👍 {up}, 👎 {down}; follow-ups {follow_ups}, 👍 {up_after}, 👎 {down_after}."
    )


@dataclass(frozen=True)
class Waiting:
    """An issue in a list of the digest: since when it waits, and for what."""

    since: datetime
    number: int
    upvotes: int
    why: str


def ago(when: datetime, now: datetime) -> str:
    waited = days_between(when, now)
    return f"{days(waited, 'en')} ago" if waited else "today"


def issue_link(github: GitHub, number: int, path: str = "issues") -> str:
    return f"[#{number}](https://github.com/{github.repository}/{path}/{number})"


def listing(github: GitHub, heading: str, items: list[Waiting]) -> str:
    """A list of the digest: the issues that waited longest first, by the day, and
    among those of a day the ones with the most 👍; links and numbers alone."""
    ordered = sorted(
        items, key=lambda item: (day_of(item.since), -item.upvotes, item.number)
    )
    lines = [
        f"{issue_link(github, item.number)}: {item.why}"
        + (f", 👍 {item.upvotes}" if item.upvotes else "")
        for item in ordered[:DIGEST_ITEMS]
    ]
    if len(ordered) > DIGEST_ITEMS:
        lines.append(f"and {len(ordered) - DIGEST_ITEMS} more")
    return f"**{heading}: {len(ordered)}**" + (f"\n\n{bullets(lines)}" if lines else "")


def reporter_answered(
    issue: dict[str, Any], comments: list[dict[str, Any]]
) -> datetime | None:
    """When the reporter answered a maintainer who said nothing since; None when the
    last word of a person is a maintainer's, or no maintainer spoke."""
    author = login(issue.get("user"))
    asked = False
    answer = None
    for comment in comments:
        if is_bot(comment.get("user")):
            continue
        if login(comment.get("user")) == author:
            answer = answer or (created(comment) if asked else None)
        elif is_maintainer(comment):
            asked, answer = True, None
    return answer


def objected(comments: list[dict[str, Any]]) -> datetime | None:
    """When someone first objected in a comment to the last duplicate notice of the
    issue, and no maintainer answered since; None without such an objection.

    A 👎 alone, or a maintainer who just removed `possible-duplicate`, leaves the
    issue to `needs-triage`, if it still has it.
    """
    notices = [created(comment) for comment in comments if is_duplicate_notice(comment)]
    objection = None
    for comment in comments:
        if (
            notices
            and created(comment) > notices[-1]
            and not is_bot(comment.get("user"))
        ):
            objection = (
                None if is_maintainer(comment) else objection or created(comment)
            )
    return objection


def marks_newcomer(comment: dict[str, Any]) -> bool:
    """Whether the comment is an analysis that marks its issue as a candidate."""
    found = marker(comment)
    return (
        found is not None and found[0] == "analysis" and found[1].get("newcomer") == "1"
    )


def waits_on_others(issue: dict[str, Any]) -> bool:
    """Whether the issue waits on its reporter, the sweep or a release, or is
    locked."""
    return bool(issue.get("locked")) or bool(
        label_names(issue) & {NEEDS_INFO, STALE, POSSIBLE_DUPLICATE, FIXED}
    )


def waiting_on_maintainers(
    github: GitHub, activity: Activity, now: datetime
) -> list[Waiting]:
    """The open issues whose next step is the maintainers': those that need triage
    for TRIAGE_WAIT, those whose reporter answered a maintainer, and those whose
    duplicate notice someone objected to; each until a maintainer answers."""
    found: dict[int, Waiting] = {}
    for issue in github.open_issues(NEEDS_TRIAGE):
        opened = created(issue)
        if waits_on_others(issue) or is_maintainer(issue) or now - opened < TRIAGE_WAIT:
            continue
        found[issue["number"]] = Waiting(
            opened,
            issue["number"],
            upvotes(issue),
            f"needs triage for {days(days_between(opened, now), 'en')}",
        )
    for number, issue in activity.issues.items():
        if (
            number in found
            or issue["state"] != "open"
            or waits_on_others(issue)
            or is_maintainer(issue)
        ):
            continue
        comments = activity.comments.get(number, [])
        if since := reporter_answered(issue, comments):
            why = f"the reporter answered {ago(since, now)}"
        elif since := objected(comments):
            why = f"someone objected to the duplicate notice {ago(since, now)}"
        else:
            continue
        found[number] = Waiting(since, number, upvotes(issue), why)
    return list(found.values())


def waiting_on_reporters(
    github: GitHub, lifecycle: Lifecycle, now: datetime
) -> list[Waiting]:
    """The open issues that wait for an answer of their reporter, with the day of
    the reminder or of the closing, as the sweep keeps them."""
    found = []
    for issue in github.open_issues(NEEDS_INFO):
        if issue.get("locked"):
            continue
        comments = github.comments(issue["number"])
        since = asked_since(github, issue, comments)
        reminders = [
            comment
            for comment in comments
            if kind_of(comment) == "reminder" and created(comment) >= since
        ]
        if exempt := exemption(issue, lifecycle):
            then = f"exempt ({exempt}), never closes"
        elif reminders:
            then = f"closes on {closing_day(reminders[-1], lifecycle)}"
        else:
            reminder = since + timedelta(days=lifecycle.remind_after_days)
            then = f"reminder on {day_of(reminder)}"
        found.append(
            Waiting(
                since,
                issue["number"],
                upvotes(issue),
                f"asked {ago(since, now)}; {then}",
            )
        )
    return found


def waiting_for_release(
    github: GitHub, activity: Activity, now: datetime
) -> list[Waiting]:
    """The open issues that a merged pull request fixed, with that pull request
    when its notice is of the last 30 days."""
    found = []
    for issue in github.open_issues(FIXED):
        number = issue["number"]
        since, why = created(issue), "fixed"
        for comment in activity.comments.get(number, []):
            said = marker(comment)
            if said and said[0] == "fixed" and said[1].get("pull", "").isdigit():
                since = created(comment)
                pull = issue_link(github, int(said[1]["pull"]), "pull")
                why = f"fixed by {pull} {ago(since, now)}"
        found.append(Waiting(since, number, upvotes(issue), why))
    return found


def marked_newcomer(activity: Activity) -> dict[int, datetime]:
    """The issues whose last analysis of the activity marks them as candidates, and
    the moment of that analysis: another analysis by hand has the last word."""
    last = {
        number: comment
        for number, comment in activity.posted("analysis")
        if number in activity.issues
    }
    return {
        number: created(comment)
        for number, comment in last.items()
        if marks_newcomer(comment)
    }


def turned_down(
    github: GitHub,
    config: Config,
    number: int,
    comments: list[dict[str, Any]],
    pushers: dict[str, bool],
) -> bool:
    """Whether a maintainer said no to a candidate: removed a label of the group
    `contribution` from it, which only a person who may triage can, or gave one of its
    analyses a 😕, which says no to the candidate alone, or a 👎, which says that the
    analysis itself is wrong. One request for the events, which the GitHub object keeps
    for the run, and those of a reaction."""
    return any(
        event["event"] == "unlabeled"
        and (event.get("label") or {}).get("name") in config.group("contribution")
        and not is_bot(event.get("actor"))
        for event in github.events(number)
    ) or reacted_to(github, comments, pushers, ("confused", "-1"))


def newcomer_candidates(
    github: GitHub, config: Config, activity: Activity, now: datetime
) -> list[Waiting]:
    """The open issues for a maintainer to offer to newcomers: their last analysis
    marks them, a maintainer opened or triaged them, and they wait on nobody else.
    A label of the group `contribution` settles it, as does a decision against the
    issue or a maintainer's no; the assistant itself never sets such a label."""
    settled = {*config.group("contribution"), DUPLICATE, INVALID, WONTFIX, MUTED}
    pushers: dict[str, bool] = {}
    found = []
    for number, since in marked_newcomer(activity).items():
        issue = activity.issues[number]
        if (
            issue["state"] == "open"
            and vouched_for(issue)
            and not waits_on_others(issue)
            and not label_names(issue) & settled
            and not turned_down(
                github, config, number, activity.comments[number], pushers
            )
        ):
            found.append(
                Waiting(since, number, upvotes(issue), f"analyzed {ago(since, now)}")
            )
    return found


def candidates_for_newcomers(
    github: GitHub, config: Config, activity: Activity, now: datetime
) -> str:
    """The list of the candidates, and what a maintainer does with them."""
    found = newcomer_candidates(github, config, activity, now)
    listed = listing(github, "Good first issue candidates", found)
    if not found:
        return listed
    return (
        f"{listed}\n\nThe AI thinks that someone new to the project could take these; "
        f"it sets no label. Add `{config.group('contribution')[0]}` to those you "
        "confirm. A 😕 on the analysis takes one off this list."
    )


def digest(github: GitHub, config: Config, activity: Activity, now: datetime) -> str:
    """What waits on whom, for the summary of the sweep: links and numbers alone,
    no text of an issue."""
    lists = [
        listing(
            github,
            "Waiting on the maintainers",
            waiting_on_maintainers(github, activity, now),
        ),
        listing(
            github,
            "Waiting on reporters",
            waiting_on_reporters(github, config.lifecycle, now),
        ),
        listing(
            github,
            "Fixed, waiting for a release",
            waiting_for_release(github, activity, now),
        ),
    ]
    # Without labels to offer to newcomers, the AI isn't asked for candidates.
    if config.group("contribution"):
        lists.append(candidates_for_newcomers(github, config, activity, now))
    return "\n\n".join(lists)


def duration(seconds: float) -> str:
    """A time in the unit that reads best: minutes, hours or days."""
    if seconds < 2 * 3600:
        return f"{round(seconds / 60)} min"
    if seconds < 2 * 86400:
        return f"{round(seconds / 3600)} h"
    return days(round(seconds / 86400), "en")


def duplicate_outcomes(activity: Activity) -> str:
    """What became of the issues with a duplicate notice: closed as a duplicate, by
    the sweep or by a maintainer, kept open after someone objected in a comment,
    kept open otherwise, or still waiting for the closing."""
    closed = objections = otherwise = pending = 0
    for number, issue in activity.issues.items():
        comments = activity.comments.get(number, [])
        # The assistant's notices alone: a maintainer's command is no guess of it.
        notices = [
            created(comment)
            for comment in comments
            if is_duplicate_notice(comment) and kind_of(comment) == "analysis"
        ]
        if not notices:
            continue
        if any(kind_of(comment) == "closed-duplicate" for comment in comments) or (
            issue["state"] == "closed" and issue.get("state_reason") == "duplicate"
        ):
            closed += 1
        elif issue["state"] == "open" and POSSIBLE_DUPLICATE in label_names(issue):
            pending += 1
        elif any(
            created(comment) > notices[-1]
            and not is_bot(comment.get("user"))
            and not is_maintainer(comment)
            for comment in comments
        ):
            objections += 1
        else:
            otherwise += 1
    return (
        f"{closed + objections + otherwise + pending}: closed {closed}, "
        f"objected to {objections}, kept open otherwise {otherwise}, "
        f"waiting {pending}"
    )


def question_outcomes(activity: Activity) -> str:
    """What became of the issues whose analysis or follow-up asked the reporter:
    answered in a comment, closed without an answer, settled otherwise, as by a
    maintainer, or still waiting."""
    answered = closed = otherwise = pending = 0
    for number, issue in activity.issues.items():
        comments = activity.comments.get(number, [])
        asked = [created(comment) for comment in comments if asks(comment)]
        if not asked:
            continue
        author = login(issue.get("user"))
        if any(kind_of(comment) == "closed-unanswered" for comment in comments):
            closed += 1
        elif issue["state"] == "open" and NEEDS_INFO in label_names(issue):
            pending += 1
        elif any(
            login(comment.get("user")) == author and created(comment) > asked[-1]
            for comment in comments
        ):
            answered += 1
        else:
            otherwise += 1
    return (
        f"{plural(answered + closed + otherwise + pending, 'issue')}: "
        f"answered {answered}, closed without an answer {closed}, "
        f"settled otherwise {otherwise}, waiting {pending}"
    )


def newcomer_outcomes(github: GitHub, config: Config, activity: Activity) -> str:
    """What became of the issues marked as candidates, at most of the MAX_CORRECTED
    marked last: they have a label of the group `contribution`, a maintainer said no,
    they closed without such a label, or they still wait."""
    found = marked_newcomer(activity)
    latest = sorted(found, key=lambda number: found[number], reverse=True)
    latest = latest[:MAX_CORRECTED]
    pushers: dict[str, bool] = {}
    kept = removed = closed = pending = 0
    for number in latest:
        issue = activity.issues[number]
        if label_names(issue) & set(config.group("contribution")):
            kept += 1
        elif turned_down(github, config, number, activity.comments[number], pushers):
            removed += 1
        elif issue["state"] == "open":
            pending += 1
        else:
            closed += 1
    of = f", of the {len(latest)} marked last" if len(latest) < len(found) else ""
    return (
        f"{len(found)}: kept {kept}, removed {removed}, closed without a label "
        f"{closed}, waiting {pending}{of}"
    )


def quality(github: GitHub, config: Config, activity: Activity) -> str:
    """How the assistant did since the activity's moment, as a table of numbers."""
    first = activity.analyzed()
    analyses = sum(1 for _ in activity.posted("analysis"))
    waits = [
        (first[number] - created(issue)).total_seconds()
        for number, issue in activity.issues.items()
        if number in first and created(issue) >= activity.since
    ]
    found = corrections(github, config, activity)
    kept = found.labeled - len(found.removed)
    share = f" ({round(100 * kept / found.labeled)} %)" if found.labeled else ""
    of = (
        f", of the {found.read} issues analyzed last"
        if found.read < found.analyzed
        else ""
    )
    _, up, down = reactions_on(activity, "analysis")
    rows = [
        ("Analyses posted", f"{analyses}, on {len(first)} issues"),
        (
            "Median time from a new issue to its analysis",
            duration(median(waits)) if waits else "no new issue analyzed",
        ),
        (
            f"Labels of the AI kept for {CORRECTION_PERIOD.days} days",
            f"{kept} of {found.labeled}{share}{of}",
        ),
        (
            f"Labels that maintainers added within {ADDITION_PERIOD.days} days",
            f"{len(found.added)}{of}",
        ),
        ("Duplicate notices", duplicate_outcomes(activity)),
        ("Questions to reporters", question_outcomes(activity)),
        ("Feedback on the analyses", f"👍 {up}, 👎 {down}"),
    ]
    if config.group("contribution"):
        rows.insert(
            -1,
            (
                "Good first issue candidates",
                newcomer_outcomes(github, config, activity),
            ),
        )
    return "| Number | Value |\n| --- | --- |\n" + "\n".join(
        f"| {name} | {value} |" for name, value in rows
    )


def monthly(event: str, now: datetime) -> bool:
    """Whether the summary has the quality numbers: in the morning sweep of the first
    day of a month, and in every sweep started by hand."""
    return event != "schedule" or now.day == 1


def part(title: str, make: Callable[[], str]) -> str:
    """A section of the summary; one that GitHub fails is a warning and a line that
    says so, since the summary is no step of the lifecycle."""
    try:
        text = make()
    except GitHubError as error:
        command("warning", f"{title}: {error}")
        text = NOT_MADE
    return f"#### {title}\n\n{text}"


def digest_issue(github: GitHub, activity: Activity) -> dict[str, Any] | None:
    """The one issue of the digest, also a closed one, by its marker; the first, if
    another workflow with the same token ever opened a second. The sweep edits it
    every morning, so the activity has it without a request, unless the sweeps paused
    for longer than it reads."""
    found = [issue for issue in activity.issues.values() if is_digest(issue)] or [
        issue for issue in github.issues_of_the_assistant() if is_digest(issue)
    ]
    return min(found, key=lambda issue: issue["number"], default=None)


def digest_body(
    github: GitHub, parts: list[str], line: str, previous: str, now: datetime
) -> str:
    """The text of the issue of the digest: the sections of the summary and the line
    of its feedback, numbers, links and days alone, and the day that it was written.

    The quality comes once a month, so that of the last time stays, with its day,
    until the next one. The links are relative because GitHub shows a link with the
    whole address as a mention on the issue that it leads to: every reporter would
    see this issue on theirs, and again whenever theirs comes back into a list.
    """
    day = day_of(now).isoformat()
    quality = next((section for section in parts[1:] if NOT_MADE not in section), "")
    if quality:
        quality += f"\n\nCounted on {day}."
    elif held := HELD_QUALITY.search(previous.replace("\r\n", "\n")):
        quality = held[0]
    sections = (
        f"{render_marker(DIGEST)}\n{DIGEST_INTRO}",
        parts[0],
        quality,
        line,
        f"Updated on {day}.",
    )
    return "\n\n".join(filter(None, sections)).replace(
        f"https://github.com/{github.repository}/", "../"
    )


def keep_digest_issue(
    github: GitHub,
    config: Config,
    activity: Activity,
    parts: list[str],
    line: str,
    now: datetime,
) -> str:
    """Keep the digest in one issue, as [digest] issue asks: open it when it is
    missing and edit it after that, which notifies nobody, and say so with a link for
    the summary. A sweep that GitHub stopped after the opening finds it the next
    morning. It stays as a maintainer left it: closed, or with other labels; the
    event job and the AI know it by its marker.
    """
    if NOT_MADE in parts[0]:
        # Yesterday's lists say more than a text that says that there are none.
        return "Digest issue not updated: GitHub failed."
    issue = digest_issue(github, activity)
    if issue is None:
        known = {label["name"].casefold() for label in github.labels()}
        labels = (muted_label(config), pinned_label(config))
        for label in labels:
            if label.name.casefold() not in known:
                github.create_label(label)
        number = github.create_issue(
            config.digest_title,
            digest_body(github, parts, line, "", now),
            [label.name for label in labels],
        )
        named = issue_link(github, number) if number else "a new issue"
        return (
            f"Digest issue: opened {named}. Pin it by hand, with Pin issue in the "
            "sidebar of the issue."
        )
    number = issue["number"]
    named = issue_link(github, number)
    previous = text_of(issue)
    body = digest_body(github, parts, line, previous, now)
    if body == previous and issue["title"] == config.digest_title:
        return f"Digest issue: {named} is current."
    github.edit_issue(number, config.digest_title, body)
    closed = "" if issue["state"] == "open" else " It stays closed until you reopen it."
    return f"Digest issue: updated {named}.{closed}"


def report(
    github: GitHub, config: Config, now: datetime, event: str
) -> tuple[list[str], str]:
    """The line of the feedback and the sections of the summary of the sweep: the
    maintainer digest, and the quality numbers once a month. Numbers and links
    alone, no text of an issue: the summary is for the maintainers, but anyone who
    can read the repository can open it. So can they the issue of the digest, where
    [digest] issue asks for one.
    """
    if event not in FEEDBACK_EVENTS:
        return [], ""
    period = FEEDBACK_PERIOD.days
    try:
        activity = Activity.read(github, now - FEEDBACK_PERIOD)
    except GitHubError as error:
        command("warning", f"Reading the comments and issues of {period} days: {error}")
        return ["Feedback not counted, no digest: GitHub failed."], ""
    parts = [
        part(
            "Maintainer digest",
            partial(digest, github, config, activity, now),
        )
    ]
    if monthly(event, now):
        parts.append(
            part(
                f"Assistant quality (last {period} days)",
                partial(quality, github, config, activity),
            )
        )
    lines = [feedback(activity)]
    if config.digest_issue:
        try:
            lines.append(
                keep_digest_issue(github, config, activity, parts, lines[0], now)
            )
        except GitHubError as error:
            command("warning", f"The issue of the digest: {error}")
            lines.append("Digest issue not updated: GitHub failed.")
    return lines, "\n\n".join(parts)


def sweep_labeled(
    github: GitHub,
    label: str,
    step: Callable[[GitHub, dict[str, Any], datetime, Lifecycle], list[str]],
    now: datetime,
    lifecycle: Lifecycle,
    isolate: Isolate,
) -> list[str]:
    done = []
    for issue in github.open_issues(label):
        if not issue.get("locked"):
            done += isolate(
                f"#{issue['number']}", partial(step, github, issue, now, lifecycle)
            )
    return done


def posted_since(comments: list[dict[str, Any]], kind: str, since: datetime) -> bool:
    """Whether the assistant posted a comment of the kind since the moment.

    It did when GitHub stopped an earlier sweep after the comment; this one
    completes the step without a second comment.
    """
    return any(
        kind_of(comment) == kind and created(comment) >= since for comment in comments
    )


def first_release(
    github: GitHub,
    releases: list[dict[str, Any]],
    shas: list[str],
    since: datetime,
) -> dict[str, Any] | None:
    """The first release after `since` that contains every merge commit in `shas`."""
    for release in releases:
        if moment(release["published_at"]) <= since:
            continue
        if all(github.contains(sha, release["tag_name"]) for sha in shas):
            return release
    return None


def mark_fixed(github: GitHub, now: datetime, isolate: Isolate = directly) -> list[str]:
    """Label the open issues that a recently merged pull request fixes.

    The issue stays open until a release ships the fix. A maintainer who removes the
    label keeps it removed: the notice of a pull request is never repeated.
    """
    branch, pulls = github.merged_pulls(now - MERGE_LOOKBACK)
    # Read once, by the first issue to mark.
    releases = cache(github.releases)
    done = []
    for pull in sorted(pulls, key=lambda item: item["mergedAt"]):
        if pull["baseRefName"] != branch:
            continue
        for reference in pull["closingIssuesReferences"]["nodes"]:
            # Most issues that a merge names are closed by the time of a sweep, and
            # each one read would spend the requests that the token has in an
            # hour; the read of an open one decides.
            if not github.owns(reference) or reference["state"] == "CLOSED":
                continue
            number = reference["number"]
            done += isolate(
                f"#{number}",
                partial(mark_issue_fixed, github, number, pull, branch, releases),
            )
    return done


def mark_issue_fixed(
    github: GitHub,
    number: int,
    pull: dict[str, Any],
    branch: str,
    releases: Callable[[], list[dict[str, Any]]],
) -> list[str]:
    try:
        issue = github.issue(number)
    except GitHubError as error:
        # An issue that was deleted or moved to another repository.
        if not error.missing():
            raise
        return []
    if issue["state"] != "open" or issue.get("locked"):
        return []
    current = label_names(issue)
    comments = github.comments(number)
    # Nor is a fix announced again that a release has shipped and closed the issue
    # with since its merge, such as at once, without the notice: an issue reopened
    # after it says that the fix didn't help.
    if (
        FIXED in current
        or any(
            (found := marker(comment))
            and found[0] == "fixed"
            and found[1].get("pull") == str(pull["number"])
            for comment in comments
        )
        or posted_since(comments, "released", moment(pull["mergedAt"]))
    ):
        return []
    # The labels before anything else: the label closes the issue with the
    # release, and every later sweep skips an issue that has it or the notice. So
    # the issue stops waiting for its reporter first, or the sweep would remind the
    # reporter of a fixed issue and close it unanswered. The notice is the only
    # thing a run can lose, when GitHub fails it or the releases read before it;
    # the release closes the issue anyway.
    for name in (NEEDS_INFO, STALE):
        if name in current:
            github.remove_label(number, name)
    github.add_labels(number, [FIXED])
    # A sweep that sees the merge late, as the run of the merge failed, may find a
    # release that ships the fix already: then the issue closes with it at once,
    # without a notice that promises the next release.
    shipped = pull["mergeCommit"] and first_release(
        github, releases(), [pull["mergeCommit"]["oid"]], moment(pull["mergedAt"])
    )
    marked = {
        **issue,
        "labels": [
            {"name": name} for name in sorted(current - {NEEDS_INFO, STALE} | {FIXED})
        ],
    }
    # Unless another pull request that fixes the issue waits for its release.
    if shipped and (closed := close_issue_released(github, marked, releases())):
        return closed
    github.comment(
        number,
        render_fixed(pull["number"], branch, issue_language(issue, comments)),
    )
    return [f"#{number}: fixed by #{pull['number']}; closes with the next release."]


def close_released(github: GitHub, isolate: Isolate = directly) -> list[str]:
    """Close the fixed issues whose fix a published release ships, with its link."""
    issues = [issue for issue in github.open_issues(FIXED) if not issue.get("locked")]
    if not issues:
        return []
    releases = github.releases()
    done = []
    for issue in issues:
        done += isolate(
            f"#{issue['number']}",
            partial(close_issue_released, github, issue, releases),
        )
    return done


def close_issue_released(
    github: GitHub, issue: dict[str, Any], releases: list[dict[str, Any]]
) -> list[str]:
    number = issue["number"]
    events = github.events(number)
    label = max(
        (
            event
            for event in events
            if event["event"] == "labeled"
            and (event.get("label") or {}).get("name") == FIXED
        ),
        key=created,
        default=None,
    )
    labeled = created(label or issue)
    comments = cache(partial(github.comments, number))
    # A fix that a release closed the issue with before it was last reopened
    # didn't help. Any other reopening, such as of an issue that GitHub closed at
    # the merge, leaves the fixes as they are.
    reopened = max(
        (created(event) for event in events if event["event"] == "reopened"),
        default=None,
    )
    spent = reopened and max(
        (
            created(comment)
            for comment in comments()
            if kind_of(comment) == "released" and created(comment) <= reopened
        ),
        default=None,
    )
    # The fixes that the label waits for: merged after that release, and at most
    # MERGE_LOOKBACK before the label, the latest that a sweep sets it, with the
    # time of its run.
    pulls = [
        pull
        for pull in github.fixing_pulls(number)
        if not (spent and moment(pull["mergedAt"]) <= spent)
        and labeled - moment(pull["mergedAt"]) <= MERGE_LOOKBACK + LABEL_SLACK
    ]
    # The first release that ships them all, published after the last of them was
    # merged: also before the label, when a sweep set it late. Without one, the
    # label promises the next release after it, and so does a label that someone
    # set by hand, such as after a fix that fell short: never a release before it.
    since = max((moment(pull["mergedAt"]) for pull in pulls), default=labeled)
    if label and login(label.get("actor")) != BOT:
        since = max(since, labeled)
    release = first_release(
        github, releases, [pull["mergeCommit"]["oid"] for pull in pulls], since
    )
    if not release:
        return []
    if not posted_since(comments(), "released", since):
        github.comment(
            number,
            render_released(
                login(issue.get("user")),
                release,
                sorted(pull["number"] for pull in pulls),
                issue_language(issue, comments()),
            ),
        )
    # Closed first: with the label, the next sweep finds an issue that GitHub
    # didn't close.
    github.close(
        issue,
        "COMPLETED",
        f"Fixed and released in {release['tag_name']} on "
        f"{release['published_at'][:10]}.",
    )
    for name in (FIXED, NEEDS_TRIAGE, NEEDS_INFO, STALE):
        if name in label_names(issue):
            github.remove_label(number, name)
    try:
        github.set_milestone(number, release["tag_name"], release["published_at"])
    except GitHubError as error:
        # The milestone only files the issue; the release closed it in any case.
        command("notice", f"No milestone {release['tag_name']} on #{number}: {error}")
    return [f"#{number}: closed, released in {release['tag_name']}."]


def sweep_waiting(
    github: GitHub, issue: dict[str, Any], now: datetime, lifecycle: Lifecycle
) -> list[str]:
    """Remind the reporter after remind_after_days, 15 by default, and close after
    close_after_days, 30, without an answer.

    An answer is a comment or an edit of the text by the reporter after the last
    question. The closing comes on the day that the reminder named. An exempt issue
    goes on waiting without a reminder and never closes.
    """
    number = issue["number"]
    author = login(issue.get("user"))
    comments = github.comments(number)
    since = asked_since(github, issue, comments)
    current = label_names(issue)
    if any(
        login(comment.get("user")) == author
        and created(comment) > since
        and conversing(issue, comment)
        for comment in comments
    ) or edited_since(github, issue, since):
        # The event workflow missed the answer, for example while it was switched off.
        for name in (NEEDS_INFO, STALE):
            if name in current:
                github.remove_label(number, name)
        return [f"#{number}: the reporter answered; no longer waiting."]
    exempt = exemption(issue, lifecycle)
    if exempt:
        if STALE not in current:
            return []
        # Exempted after its reminder: it no longer closes.
        github.remove_label(number, STALE)
        return [f"#{number}: exempt ({exempt}); it no longer closes."]
    reminders = [
        comment
        for comment in comments
        if kind_of(comment) == "reminder" and created(comment) >= since
    ]
    language = issue_language(issue, comments)
    waited = days_between(since, now)
    if reminders:
        reminded = created(reminders[-1])
        close_after = timedelta(days=lifecycle.close_after_days)
        if over(since, close_after, now) and day_of(now) >= closing_day(
            reminders[-1], lifecycle
        ):
            if not posted_since(comments, "closed-unanswered", reminded):
                github.comment(
                    number, render_closed_unanswered(author, language, lifecycle)
                )
            github.close(
                issue,
                "NOT_PLANNED",
                f"No answer to the questions for {waited} days; "
                f"reminded on {day_of(reminded)}.",
            )
            return [f"#{number}: closed after {waited} days without an answer."]
        if STALE not in current:
            github.add_labels(number, [STALE])
        return []
    if STALE in current:
        # A new question started a new waiting period.
        github.remove_label(number, STALE)
    if over(since, timedelta(days=lifecycle.remind_after_days), now):
        questions = questions_address(github, issue, comments)
        closes = now + timedelta(days=lifecycle.warning_days)
        github.comment(number, render_reminder(author, language, closes, questions))
        github.add_labels(number, [STALE])
        return [f"#{number}: reminded the reporter after {waited} days."]
    return []


def asking(
    issue: dict[str, Any], comments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The comments that may ask the reporter something: those of a maintainer who
    isn't the reporter, other than commands, and the assistant's analyses and
    follow-ups."""
    author = login(issue.get("user"))
    return [
        comment
        for comment in comments
        if (
            is_maintainer(comment)
            and login(comment.get("user")) != author
            and conversing(issue, comment)
        )
        or kind_of(comment) in {"analysis", "follow-up"}
    ]


def asked_since(
    github: GitHub, issue: dict[str, Any], comments: list[dict[str, Any]]
) -> datetime:
    """When the issue started to wait for its reporter: the last comment that asked
    it something, or the last labeling with `needs-info`; without either, its
    opening."""
    labeled = [
        created(event)
        for event in github.events(issue["number"])
        if event["event"] == "labeled"
        and (event.get("label") or {}).get("name") == NEEDS_INFO
    ]
    asked = [created(comment) for comment in asking(issue, comments)]
    return max(labeled + asked or [created(issue)])


def closing_day(reminder: dict[str, Any], lifecycle: Lifecycle) -> date:
    """The day that the reminder named for the closing.

    A reminder of an earlier version, which didn't record it, named the day
    warning_days after it.
    """
    _, said = marker(reminder) or ("", {})
    try:
        return date.fromisoformat(said.get("closes", ""))
    except ValueError:
        return day_of(created(reminder) + timedelta(days=lifecycle.warning_days))


def questions_address(
    github: GitHub, issue: dict[str, Any], comments: list[dict[str, Any]]
) -> str:
    """The address of the comment with the questions that the reporter is to answer.

    That is the last comment that asked, of a maintainer or the assistant's analysis
    or follow-up; empty when that one asked the reporter nothing, or GitHub gave it
    no address on this issue.
    """
    asked = asking(issue, comments)
    if not asked:
        return ""
    found = marker(asked[-1])
    address = str(asked[-1].get("html_url") or "")
    expected = (
        rf"https://github\.com/{re.escape(github.repository)}"
        rf"/issues/{issue['number']}#issuecomment-\d+"
    )
    if (found and found[1].get("questions") != "1") or not re.fullmatch(
        expected, address, re.IGNORECASE
    ):
        return ""
    return address


def sweep_duplicate(
    github: GitHub, issue: dict[str, Any], now: datetime, lifecycle: Lifecycle
) -> list[str]:
    """Close a possible duplicate duplicate_grace_days after the notice, 3 by
    default, unless someone objected or the issue is exempt.

    The notice is the analysis that found the duplicate, or the comment that a
    maintainer's /assistant duplicate posted.
    """
    number = issue["number"]
    author = login(issue.get("user"))
    comments = github.comments(number)
    notices = [comment for comment in comments if is_duplicate_notice(comment)]
    if not notices:
        return []
    notice = notices[-1]
    if now - created(notice) < timedelta(days=lifecycle.duplicate_grace_days):
        return []
    exempt = exemption(issue, lifecycle)
    if exempt:
        github.remove_label(number, POSSIBLE_DUPLICATE)
        return [f"#{number}: stays open, exempt ({exempt})."]
    # A comment of anyone objects, other than a command, and a thumbs down of the reporter or of anyone
    # who may push to the repository: in the repository of an organization, the
    # owner is the organization, which never reacts.
    objected = any(
        created(comment) > created(notice)
        and not is_bot(comment.get("user"))
        and conversing(issue, comment)
        for comment in comments
    ) or any(
        reaction["content"] == "-1"
        and (
            login(reaction.get("user")) == author
            or github.may_push(login(reaction.get("user")))
        )
        for reaction in github.reactions(notice["id"])
    )
    kind, said = marker(notice)  # type: ignore[misc]
    original = other_issue(github, int(said["duplicate"]), number)
    # A notice from before an original had to be vouched for closes nothing; a
    # maintainer who names the original vouches for it.
    if (
        objected
        or not original
        or original["state"] != "open"
        or (kind == "analysis" and not vouched_for(original))
    ):
        github.remove_label(number, POSSIBLE_DUPLICATE)
        return [
            f"#{number}: stays open, the duplicate notice was objected to or is outdated."
        ]
    language = issue_language(issue, comments)
    if not posted_since(comments, "closed-duplicate", created(notice)):
        github.comment(
            number, render_closed_duplicate(original["number"], language, lifecycle)
        )
    github.close(
        issue,
        "DUPLICATE",
        f"Duplicate of #{original['number']}, announced on "
        f"{created(notice):%Y-%m-%d} without objection.",
        duplicate_of=original,
    )
    github.remove_label(number, POSSIBLE_DUPLICATE)
    for name in (NEEDS_TRIAGE, NEEDS_INFO, STALE):
        if name in label_names(issue):
            github.remove_label(number, name)
    github.add_labels(number, [DUPLICATE])
    return [f"#{number}: closed as a duplicate of #{original['number']}."]


def lock_closed(
    github: GitHub, now: datetime, lifecycle: Lifecycle, isolate: Isolate = directly
) -> list[str]:
    """Lock the issues closed for more than lock_closed_after_days, the oldest
    first and at most LOCKS_PER_SWEEP of them; the next sweep locks the rest.

    lifecycle_problems() keeps the period at least as long as the reopen window of
    a release. Exempt issues stay unlocked; GitHub's search leaves out those with an
    exempt label, and those assigned to someone when assignees exempt.
    """
    days_ago = timedelta(days=lifecycle.lock_closed_after_days)
    qualifiers = [f"is:closed is:unlocked closed:<{day_of(now) - days_ago}"]
    # A quote would end the name in the query; exemption() leaves such a label out.
    qualifiers += [
        f'-label:"{name}"' for name in lifecycle.exempt_labels if '"' not in name
    ]
    if lifecycle.exempt_assignees:
        qualifiers.append("no:assignee")
    done: list[str] = []
    locked = 0
    for issue in github.search_issues_where(" ".join(qualifiers)):
        if locked == LOCKS_PER_SWEEP:
            break
        if issue.get("locked") or exemption(issue, lifecycle):
            continue
        lines = isolate(
            f"#{issue['number']}", partial(lock_issue, github, issue, lifecycle)
        )
        locked += bool(lines)
        done += lines
    return done


def lock_issue(
    github: GitHub, issue: dict[str, Any], lifecycle: Lifecycle
) -> list[str]:
    """Lock an issue closed long ago as resolved, with one line that points to a new
    issue.

    The line comes once, also when GitHub failed the lock after it. An issue that
    someone unlocked stays unlocked: they want its conversation to go on.
    """
    number = issue["number"]
    if any(event["event"] == "unlocked" for event in github.events(number)):
        return []
    comments = github.comments(number)
    if not any(kind_of(comment) == "locked" for comment in comments):
        language = issue_language(issue, comments)
        github.comment(number, render_locked(language, lifecycle))
    github.lock(number)
    return [
        f"#{number}: locked, closed for more than "
        f"{lifecycle.lock_closed_after_days} days."
    ]


def sync_labels(github: GitHub, config: Config) -> list[str]:
    """Create and update the labels of labels.toml; never delete one.

    GitHub ignores the case of label names, so a label that it has in another case,
    such as Bug for bug, is renamed; creating it would fail.
    """
    existing = {label["name"].casefold(): label for label in github.labels()}
    done = []
    for label in config.labels:
        current = existing.get(label.name.casefold())
        if current is None:
            created = github.create_label(label)
            done.append(f"{'created' if created else 'updated'} {label.name}")
        elif current["name"] != label.name:
            github.update_label(label, current["name"])
            done.append(f"renamed {current['name']} to {label.name}")
        elif (current.get("color") or "").lower() != label.color or (
            current.get("description") or ""
        ) != label.description:
            github.update_label(label)
            done.append(f"updated {label.name}")
    configured = {label.name.casefold() for label in config.labels}
    for name in sorted(
        label["name"] for key, label in existing.items() if key not in configured
    ):
        command("notice", f"Label {name} is not in {LABELS_FILE}; it stays.")
    for problem in check_routing(github, config):
        command("warning", problem)
    return done


def check_routing(github: GitHub, config: Config) -> list[str]:
    """What GitHub can't do of labels.toml: assign an owner of an area, or give
    issues the issue type of a kind. `check` knows only the spelling of both."""
    problems = []
    areas: dict[str, list[str]] = {}
    for label in config.labels:
        if label.group == "area":
            for owner in label.owners:
                areas.setdefault(owner, []).append(label.name)
    for owner, names in areas.items():
        if LOGIN.fullmatch(owner) and not github.assignable(owner):
            problems.append(
                f"{LABELS_FILE}: {owner}, the owner of {', '.join(names)}, can't be "
                f"assigned to issues of {config.repository}; GitHub assigns only "
                "people with access to it"
            )
    wanted = list(
        dict.fromkeys(
            label.issue_type
            for label in config.labels
            if label.group == "type" and label.issue_type
        )
    )
    if not wanted:
        return problems
    types = github.issue_types()
    if not types:
        return problems + [
            f"{LABELS_FILE}: {config.repository} has no issue types, which "
            "organizations give their repositories; issue_type changes nothing"
        ]
    for name in wanted:
        if name not in types:
            problems.append(
                did_you_mean(
                    f"{LABELS_FILE}: {config.repository} has no issue type {name}",
                    name,
                    types,
                )
            )
    return problems


def watch_findings(github: GitHub, root: Path) -> tuple[list[str], int]:
    """Dismiss the accepted findings and count the others, which stay open.

    The summary of a public repository's run is public, while code scanning alerts
    are only shown to maintainers: the lines name an open alert by its number and
    link only, never its rule, file or message. An accepted finding is named, since
    .github/findings.toml names it anyway.
    """
    accepted = load_findings(root)
    alerts = github.open_alerts()
    if alerts is None:
        return ["Code scanning isn't set up; there is nothing to check."], 0
    done, left = [], []
    for alert in sorted(alerts, key=lambda item: item["number"]):
        acceptance = next((item for item in accepted if item.covers(alert)), None)
        if acceptance is None:
            left.append(alert)
            continue
        github.dismiss_alert(
            alert["number"],
            acceptance.reason,
            f"{acceptance.comment} (accepted in {FINDINGS_FILE})",
        )
        done.append(
            f"Alert #{alert['number']} ({acceptance.tool} {acceptance.rule}) "
            f"dismissed as {acceptance.reason}."
        )
    done += [f"Alert #{alert['number']} is open: {alert['html_url']}" for alert in left]
    if left:
        done.append(
            f"Fix these findings, or accept them with a reason in {FINDINGS_FILE}."
        )
    return done, len(left)


def load_yaml(path: Path, where: str = "") -> Any:
    """A YAML file as data, with PyYAML or with yq, which GitHub's runners have.

    A file that isn't YAML is a SettingsError that names it as `where`.
    """
    try:
        import yaml
    except ImportError:
        try:
            result = subprocess.run(
                ["yq", "-o=json", ".", str(path)],
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError:
            raise AssistantError(
                "Reading the workflows needs PyYAML or yq; install one of them, "
                "for example with: pip install pyyaml"
            ) from None
        except subprocess.CalledProcessError as error:
            reason = error.stderr.strip()
        else:
            return json.loads(result.stdout)
    else:
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError) as error:
            reason = str(error)
    raise SettingsError([f"{where or path} is no valid YAML: {reason}"])


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


def reinstall() -> str:
    """The commands that write the templates of this release anew, for `check`."""
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    return (
        "write the templates of this release anew with: "
        f'dir="$(mktemp -d)" && git clone --quiet --depth 1 --branch v{version} '
        f'https://github.com/{ACTION} "$dir" && '
        'PYTHONPATH="$dir" python3 -P -m issue_assistant install'
    )


def yaml_of(text: str) -> Any:
    """YAML text as data, read as load_yaml reads a file; None if it isn't YAML."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "settings.yaml"
        path.write_text(text, encoding="utf-8")
        try:
            return load_yaml(path)
        except SettingsError:
            return None


def items_at(data: Any, keys: tuple[str, ...]) -> list[Any]:
    """The list that the mappings of YAML data hold at `keys`; empty if none."""
    for key in keys:
        data = data.get(key) if isinstance(data, dict) else None
    return data if isinstance(data, list) else []


def with_listed(data: Any, keys: tuple[str, ...], item: str) -> Any:
    """YAML data with `item` first in the list at `keys`, which is added if missing;
    None where something else than a mapping or a list is in the way."""
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return None
    data = deepcopy(data)
    node = data
    for key in keys[:-1]:
        if node.get(key) is None:
            node[key] = {}
        node = node[key]
        if not isinstance(node, dict):
            return None
    items = node.get(keys[-1])
    if items is not None and not isinstance(items, list):
        return None
    node[keys[-1]] = [item, *(items or [])]
    return data


def indentation(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def content_lines(lines: list[str], block: range) -> list[int]:
    """The lines of `block` that hold more than blanks or a comment."""
    return [
        number
        for number in block
        if lines[number].strip() and not lines[number].lstrip().startswith("#")
    ]


def with_item(text: str, keys: tuple[str, ...], item: str) -> str | None:
    """YAML text with `item` first in the list at `keys`, adding the keys it lacks.

    The edit adds lines below the deepest of the keys that the text has, or puts the
    item into the brackets of a list such as `labels: []`, so that every comment and
    the order of the keys stay. Its result is read back: None when that isn't the
    data with the item added, for YAML that the edit doesn't fit, such as a list of
    the top level or a mapping in braces.
    """
    expected = with_listed(yaml_of(text), keys, item)
    if expected is None:
        return None
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    # The key found last, its line and its match, and the lines of its block, which
    # are indented more deeply; at first the whole text, the block of the top level.
    found, line, match = 0, -1, None
    block = range(len(lines))
    for key in keys:
        content = content_lines(lines, block)
        if not content:
            break
        inner = indentation(lines[content[0]])
        name = re.escape(key)
        pattern = re.compile(
            rf" *(?:{name}|'{name}'|\"{name}\"):[ \t]*(?P<flow>\[[^\]]*\])?"
            r"[ \t]*(?:#.*)?\n"
        )
        hit = next(
            (
                (number, matched)
                for number in content
                if indentation(lines[number]) == inner
                and (matched := pattern.fullmatch(lines[number]))
            ),
            None,
        )
        if hit is None:
            break
        (line, match), found = hit, found + 1
        end = line + 1
        while end < len(lines) and (
            not lines[end].strip()
            or lines[end].lstrip().startswith("#")
            or indentation(lines[end]) > inner
        ):
            end += 1
        block = range(line + 1, end)
    scalar = (
        item if re.fullmatch(r"[\w.-]+", item) else "'" + item.replace("'", "''") + "'"
    )
    if found == len(keys) and match and match["flow"]:
        inside = match["flow"][1:-1].strip()
        start, stop = match.span("flow")
        lines[line] = (
            lines[line][:start]
            + f"[{scalar}{', ' + inside if inside else ''}]"
            + lines[line][stop:]
        )
    else:
        content = content_lines(lines, block)
        after = content_lines(lines, range(block.stop, len(lines)))
        if line < 0:
            indent, position = 0, len(lines)
        elif content:
            indent, position = indentation(lines[content[0]]), line + 1
        elif (
            found == len(keys)
            and after
            and indentation(lines[after[0]]) == indentation(lines[line])
            and lines[after[0]].lstrip().startswith("- ")
        ):
            # The items of a list at the indentation of its key, as many
            # formatters write them.
            indent, position = indentation(lines[line]), line + 1
        else:
            indent, position = indentation(lines[line]) + 2, line + 1
        added = [
            " " * (indent + 2 * step) + f"{key}:\n"
            for step, key in enumerate(keys[found:])
        ]
        added.append(" " * (indent + 2 * len(added)) + f"- {scalar}\n")
        lines[position:position] = added
    result = "".join(lines)
    return result if yaml_of(result) == expected else None


def add_to_actionlint(root: Path) -> list[str]:
    """Let actionlint, where a repository runs it with settings, ignore the queue of
    issue-assistant.yml, which it doesn't know yet. The runner of the AI is in the
    reusable workflow, which actionlint doesn't read in a repository that calls it."""
    path = root / ACTIONLINT
    if not path.is_file():
        return []
    done = []
    for keys, item, what in (
        (
            ("paths", str(WORKFLOWS / "issue-assistant.yml"), "ignore"),
            QUEUE_MESSAGE,
            "let actionlint ignore the queue of issue-assistant.yml",
        ),
    ):
        text = path.read_text(encoding="utf-8")
        reason = "its YAML is too unusual to edit safely"
        try:
            if item in items_at(load_yaml(path, str(ACTIONLINT)), keys):
                continue
            edited = with_item(text, keys, item)
        except AssistantError as error:
            # Without PyYAML or yq, or with settings that are no YAML, install
            # writes the rest of the set-up and says what is left to do here.
            edited, reason = None, str(error)
        if edited is None:
            done.append(
                f"{ACTIONLINT}: add {item!r} to the list {' > '.join(keys)}; {reason}"
            )
            continue
        path.write_text(edited, encoding="utf-8", newline="\n")
        done.append(f"{what} in {ACTIONLINT}")
    return done


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


def share(part: int, whole: int) -> str:
    """A share as a percentage with its numbers; a dash when there is no whole."""
    return f"{100 * part / whole:.0f} % ({part} of {whole})" if whole else "–"


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
