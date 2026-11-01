"""The digest of the sweep and its measures: what waits for whom, the feedback on the
analyses and the quality of the month."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import partial
from statistics import median
from typing import Any

from .applying import vouched_for
from .comments import bullets
from .constants import (
    DIGEST,
    DUPLICATE,
    FIXED,
    INVALID,
    MUTED,
    NEEDS_INFO,
    NEEDS_TRIAGE,
    POSSIBLE_DUPLICATE,
    STALE,
    WONTFIX,
)
from .errors import GitHubError
from .github import GitHub
from .issues import (
    Activity,
    conversing,
    exemption,
    is_bot,
    is_digest,
    is_duplicate_notice,
    is_maintainer,
    kind_of,
    marker,
    muted_label,
    pinned_label,
    reactions_on,
    render_marker,
    text_of,
    upvotes,
)
from .learning import (
    ADDITION_PERIOD,
    CORRECTION_PERIOD,
    MAX_CORRECTED,
    corrections,
    plural,
    reacted_to,
)
from .settings import Config, Lifecycle, days, label_names
from .values import command, created, day_of, days_between, login

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


def share(part: int, whole: int) -> str:
    """A share as a percentage with its numbers; a dash when there is no whole."""
    return f"{100 * part / whole:.0f} % ({part} of {whole})" if whole else "–"
