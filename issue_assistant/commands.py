"""The /assistant commands, what a reporter's answer does, and the limits of the AI runs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .applying import other_issue
from .comments import render_duplicate, render_let_in, render_reopened
from .constants import (
    BOT,
    HANDLED,
    LET_IN,
    MAX_NUMBER,
    MUTED,
    NEEDS_INFO,
    NEEDS_TRIAGE,
    POSSIBLE_DUPLICATE,
    REFUSED,
    STALE,
)
from .github import GitHub
from .issues import (
    Command,
    command_of,
    exemption,
    follow_up_due,
    form_fields,
    form_labels,
    is_bot,
    is_maintainer,
    issue_language,
    kind_of,
    marker,
    muted_label,
    record_run,
    runs_of,
    text_of,
)
from .settings import Config, answers_category, label_names, limit
from .values import command, created, login

# The limits of the AI runs that people who don't maintain the repository start,
# unless [engine] in config.toml sets others: analyses, follow-ups and replies after
# a release in 24 hours, analyses of one reporter's new issues in 24 hours, and the
# reporter's comments after a release that the AI reads.
DAY = timedelta(days=1)

# The tasks that reporters start, which the limits of the AI runs apply to; a
# maintainer starts the others.
LIMITED = {"triage", "follow-up", "release-reply", "discussion-answer"}

# The original that /assistant duplicate names: #12, GH-12 or 12, also after "of".
ORIGINAL = re.compile(r"(?:of[ \t]+)?(?:#|GH-)?(\d{1,10})(?![\w-])", re.I)


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
