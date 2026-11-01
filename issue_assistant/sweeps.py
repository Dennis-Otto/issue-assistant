"""The daily sweep: reminders, closing, locking, and what a merge or a release settles."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import cache, partial
from typing import Any

from .applying import other_issue, vouched_for
from .comments import (
    render_closed_duplicate,
    render_closed_unanswered,
    render_fixed,
    render_locked,
    render_released,
    render_reminder,
)
from .constants import (
    BOT,
    DUPLICATE,
    FIXED,
    NEEDS_INFO,
    NEEDS_TRIAGE,
    POSSIBLE_DUPLICATE,
    STALE,
)
from .digests import asked_since, asking, closing_day
from .errors import GitHubError, SweepIncomplete
from .github import GitHub
from .issues import (
    conversing,
    edited_since,
    exemption,
    is_bot,
    is_duplicate_notice,
    issue_language,
    kind_of,
    marker,
)
from .settings import DEFAULT_LIFECYCLE, Lifecycle, label_names
from .values import command, created, day_of, days_between, login, moment, over

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
