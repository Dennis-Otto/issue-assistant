"""Events: what a new issue, a new comment or a discussion starts."""

from __future__ import annotations

from typing import Any

from .applying import assign_owners
from .commands import (
    Plan,
    add_form_labels,
    answered,
    handle_command,
    limited,
    mute,
    reopen_if_answered,
    within_limits,
)
from .constants import (
    MODES,
    MUTED,
    NEEDS_INFO,
    NEEDS_TRIAGE,
    POSSIBLE_DUPLICATE,
    REFUSED,
    STALE,
)
from .errors import AssistantError
from .github import GitHub
from .issues import (
    box_ticked,
    command_of,
    form_labels,
    is_bot,
    is_digest,
    is_maintainer,
    refusal,
    text_of,
    without_box,
)
from .settings import Config, answers_category, label_names
from .values import created, login, moment

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
