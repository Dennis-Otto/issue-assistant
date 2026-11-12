"""Events: what a new issue, a new comment or a discussion starts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .applying import acted_on, assign_owners, earlier_post, has_its_answer
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
    BACKLOG,
    MAX_INSTRUCTION,
    MODES,
    MUTED,
    NEEDS_INFO,
    NEEDS_TRIAGE,
    OVERRIDING_ROLES,
    POSSIBLE_DUPLICATE,
    REFUSED,
    STALE,
    STARTING_ROLES,
    STEERED,
)
from .errors import AssistantError
from .github import GitHub
from .issues import (
    box_ticked,
    command_of,
    follow_up_due,
    form_labels,
    is_bot,
    is_digest,
    is_maintainer,
    kind_of,
    refusal,
    text_of,
    without_box,
)
from .settings import Config, answers_category, backlog_size, label_names
from .values import command, created, login, moment

# What an event on the issue of the digest does: comments and commands there start
# nothing, and the AI never reads it.
DIGEST_NOTE = "The issue of the digest: nothing to do."


@dataclass
class BacklogPlan(Plan):
    """What the first call of a run by hand with the task backlog chose: the issues,
    the lowest numbers first, each of which a call of the reusable workflow of its
    own gives its first analysis."""

    backlog: list[int] = field(default_factory=list)


def handle_event(
    github: GitHub,
    name: str,
    payload: dict[str, Any],
    config: Config,
    actor: str | None = None,
    backlog_issue: int = 0,
) -> Plan:
    """What the event starts. `actor` is the person who started the run, or who
    ran it again; without one, the sender of the event. `backlog_issue` is the
    issue of the backlog that a call of the reusable workflow analyzes."""
    if name == "workflow_dispatch":
        inputs = payload.get("inputs") or {}
        if "discussion" in inputs:
            return discussion_by_hand(github, int(inputs["discussion"]))
        if actor is None:
            actor = login(payload.get("sender"))
        if inputs.get("mode") == BACKLOG:
            return backlog_by_hand(github, inputs, actor, config, backlog_issue)
        if backlog_issue:
            raise AssistantError(
                f"The call names #{backlog_issue} of the backlog, but the run has "
                f"the task {inputs.get('mode') or 'triage'}, not {BACKLOG}."
            )
        return issue_by_hand(github, inputs, actor, config)
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
        if issue["state"] != "open" and by_reporter:
            # The questions offer an answer by an edit, and the closing comment
            # promises that an answer reopens the issue.
            at = moment(issue["updated_at"])
            reopened = reopen_if_answered(github, issue, at, config, edited=True)
            reopened.notes[:0] = plan.notes
            return limited(github, reopened, issue, at, config)
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


def issue_by_hand(
    github: GitHub, inputs: dict[str, Any], actor: str, config: Config
) -> Plan:
    """A run by hand of issue-assistant.yml on one issue, with its task, an optional
    instruction of the person who starts it, and the override of the one-comment rule.

    GitHub lets everyone with write access start it, and an app with the right to
    run workflows too: the role of the person is checked again here, before the AI
    runs, and the override is theirs only when they maintain the repository. A task
    that writes to the reporter and already has its comment runs again only with the
    override; the summary of the run records each one.
    """
    number = int(inputs["issue"])
    override = may_answer_again(github, inputs, actor)
    issue = github.issue(number)
    if "pull_request" in issue:
        raise AssistantError(f"#{number} is a pull request, not an issue.")
    if is_digest(issue):
        return Plan(number, notes=[DIGEST_NOTE])
    mode = inputs.get("mode") or "triage"
    if mode not in MODES:
        raise AssistantError(f"Unknown mode {mode}.")
    instruction = instruction_of(inputs, mode)
    add_form_labels(github, issue, config)
    notes = [f"Started by hand by @{actor}: {mode}."]
    if instruction:
        notes.append(f"The instruction of @{actor}: {instruction}")
    if override:
        overridden = (
            f"Override of the one-comment rule by @{actor}: the assistant may "
            f"answer #{number} again."
        )
        command("notice", overridden)
        return Plan(number, mode, [*notes, overridden])
    reason = nothing_to_post(github, issue, mode, config)
    if reason:
        notes.append(
            f"{reason}, and the assistant answers once: no AI run. To have it "
            "answer anyway, a maintainer runs it again with override."
        )
        return Plan(number, notes=notes)
    return Plan(number, mode, notes)


def backlog_by_hand(
    github: GitHub,
    inputs: dict[str, Any],
    actor: str,
    config: Config,
    number: int,
) -> Plan:
    """A run by hand with the task backlog, which a maintainer starts on the issues
    that were open before the assistant came: the first call of the reusable
    workflow chooses them, and one call for each analyzes it, `number`.

    Only a maintainer starts it, as it may start many runs of the AI, none of which
    counts toward [engine] max_per_day: that limit is there for the runs that others
    start. The input issue is the issue after which it starts, so that each run goes
    on where the last one stopped.
    """
    role = github.role(actor) if actor else "none"
    if role not in OVERRIDING_ROLES:
        raise AssistantError(
            "Only a maintainer, with the role admin or maintain, may start the "
            f"assistant on the {BACKLOG}; @{actor or 'nobody'} has the role {role}."
        )
    if " ".join(str(inputs.get("instruction") or "").split()) or ticked(
        inputs.get("override")
    ):
        raise AssistantError(
            f"The task {BACKLOG} takes no instruction and no override: it gives each "
            "issue its first analysis, once. Run it again without them."
        )
    given = str(inputs.get("issue") or "0").strip()
    if not given.isdecimal():
        raise AssistantError(
            f"The task {BACKLOG} starts after the issue that the input issue names, "
            f"a whole number, 0 or more, not {given!r}."
        )
    after = int(given)
    if number:
        return backlog_issue(github, number, after, actor, config)
    return choose_backlog(github, after, actor, config)


def choose_backlog(
    github: GitHub, after: int, actor: str, config: Config
) -> BacklogPlan:
    """The open issues after #`after` that a first analysis would have taken when
    they were new, the lowest numbers first, up to [engine] backlog_per_run; the
    summary names each one that it leaves out, with the reason."""
    size = backlog_size(config)
    plan = BacklogPlan(
        notes=[
            f"Started by hand by @{actor}: {BACKLOG} after #{after}, at most {size}."
        ]
    )
    left_out: dict[str, list[int]] = {}
    for issue in sorted(github.every_open_issue(), key=lambda item: item["number"]):
        if len(plan.backlog) == size:
            break
        if issue["number"] <= after:
            continue
        reason = left_out_of_backlog(github, issue, config)
        if reason:
            left_out.setdefault(reason, []).append(issue["number"])
        else:
            plan.backlog.append(issue["number"])
    plan.notes.append(f"Chosen for a first analysis: {numbered(plan.backlog)}.")
    plan.notes += [
        f"Left out, {reason}: {numbered(numbers)}."
        for reason, numbers in left_out.items()
    ]
    if not size:
        plan.notes.append(
            f"[engine] backlog_per_run is 0, so the {BACKLOG} analyzes no issue."
        )
    elif len(plan.backlog) == size:
        plan.notes.append(
            f"The next run of the {BACKLOG} goes on after #{plan.backlog[-1]}: give "
            "that number as the issue."
        )
    else:
        plan.notes.append(
            f"That is the whole {BACKLOG}: no other open issue after #{after} waits "
            "for a first analysis."
        )
    return plan


def numbered(numbers: list[int]) -> str:
    return ", ".join(f"#{number}" for number in numbers) or "none"


def left_out_of_backlog(github: GitHub, issue: dict[str, Any], config: Config) -> str:
    """Why the backlog leaves the open issue out; empty for one that it analyzes.

    It leaves out what a new issue would not have had analyzed either, such as the
    issue of the digest, which the assistant's bot opened, and an issue with a first
    analysis already: GitHub counts the comments of an issue, so only one with
    comments costs a request.
    """
    if is_bot(issue.get("user")):
        return "opened by a bot"
    if is_maintainer(issue):
        return "opened by a maintainer"
    if issue.get("locked"):
        return "locked"
    if MUTED in label_names(issue) or box_ticked(text_of(issue), config):
        return "muted, or its reporter keeps the AI out"
    if issue.get("comments") and any(
        kind_of(comment) == "analysis" for comment in github.comments(issue["number"])
    ):
        return "with a first analysis already"
    return ""


def backlog_issue(
    github: GitHub, number: int, after: int, actor: str, config: Config
) -> Plan:
    """The call of the reusable workflow for one issue of the backlog: a run by hand
    of its first analysis, as `issue_by_hand` starts one, while the issue is still
    one that the backlog chooses; it may have changed since."""
    if number <= after:
        raise AssistantError(
            f"#{number} is no issue of the {BACKLOG} after #{after}, which this run "
            "chose."
        )
    issue = github.issue(number)
    if "pull_request" in issue:
        raise AssistantError(f"#{number} is a pull request, not an issue.")
    reason = "closed" if issue["state"] != "open" else ""
    reason = reason or left_out_of_backlog(github, issue, config)
    if reason:
        return Plan(
            number, notes=[f"The {BACKLOG} leaves #{number} out now: {reason}."]
        )
    plan = issue_by_hand(github, {"issue": number, "mode": "triage"}, actor, config)
    plan.notes.insert(0, f"#{number} of the {BACKLOG} after #{after}.")
    return plan


def nothing_to_post(
    github: GitHub, issue: dict[str, Any], mode: str, config: Config
) -> str:
    """Why the apply job would post nothing for a run by hand without override, so
    that the run ends before the AI; empty when it may answer. The apply job checks
    the same again, as the conversation can change while the AI runs."""
    number = issue["number"]
    if mode == "maintainer-question":
        found = acted_on(github, issue, mode, None)
        if isinstance(found, str):
            return f"#{number} has no maintainer's question to answer"
        if has_its_answer(*found):
            return f"The question on #{number} has its answer already"
        return ""
    if mode not in STEERED:
        return ""
    comments = github.comments(number)
    if earlier_post(github, issue, mode, comments) is not None:
        return f"#{number} already has its {mode}"
    author = login(issue.get("user"))
    if (
        mode == "follow-up"
        and follow_up_due(comments, author, config.lifecycle.max_follow_ups) is None
    ):
        return (
            f"#{number} needs no follow-up: a maintainer joined the conversation, "
            "the assistant asked no questions, or it followed up as often as it may"
        )
    return ""


def may_answer_again(github: GitHub, inputs: dict[str, Any], actor: str) -> bool:
    """Whether a run by hand overrides the one-comment rule; an error when its person
    may not start it, with write access, or not override it, as a maintainer."""
    role = github.role(actor) if actor else "none"
    if role not in STARTING_ROLES:
        raise AssistantError(
            f"Only someone with write access may start the issue assistant by hand; "
            f"@{actor or 'nobody'} has the role {role} in the repository."
        )
    if not ticked(inputs.get("override")):
        return False
    if role not in OVERRIDING_ROLES:
        raise AssistantError(
            "Only a maintainer, with the role admin or maintain, may let the "
            f"assistant answer again past the one-comment rule; @{actor} has the "
            f"role {role}. Run it again without override."
        )
    return True


def instruction_of(inputs: dict[str, Any], mode: str) -> str:
    """The instruction of a run by hand, on one line, such as "ask for the logs";
    an error for one that is too long or for a task that it can't steer."""
    instruction = " ".join(str(inputs.get("instruction") or "").split())
    if not instruction:
        return ""
    if mode not in STEERED:
        raise AssistantError(
            f"An instruction steers only a task that writes to the reporter, "
            f"{' or '.join(STEERED)}, not {mode}."
        )
    if len(instruction) > MAX_INSTRUCTION:
        raise AssistantError(
            f"The instruction has {len(instruction)} characters; it may have at "
            f"most {MAX_INSTRUCTION}."
        )
    return instruction


def ticked(value: object) -> bool:
    """Whether a boolean input of a run by hand is on: true from the form, or "true"
    from the REST API, which passes every input as a string."""
    return value is True or str(value).strip().casefold() == "true"


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
        if login(comment.get("user")) != author:
            return Plan(number, notes=["Comment on a closed issue."])
        plan = reopen_if_answered(github, issue, created(comment), config)
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
