"""Applying the engine's answer: its labels, its comment and what they lead to."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import Any

from .answer import parse_answer
from .cleaning import (
    cross_reference,
    elsewhere_link,
    one_line,
    plain,
    reference_link,
    sanitize,
    tracked_files,
)
from .comments import (
    Findings,
    render_analysis,
    render_answer,
    render_discussion_answer,
    render_follow_up,
    render_reopened_release,
    suits_newcomer,
)
from .constants import (
    DISCUSSION_MODES,
    INVALID,
    MAX_NUMBER,
    NEEDS_INFO,
    NEEDS_TRIAGE,
    NO_PRIORITY,
    POSSIBLE_DUPLICATE,
)
from .engines import DEFAULT_ENGINE, engine_named
from .errors import AssistantError, GitHubError
from .github import GitHub
from .issues import (
    edited_since,
    follow_up_due,
    is_maintainer,
    judged_comment,
    judges,
    kind_of,
    marker,
)
from .languages import say
from .paths import LABELS_FILE
from .patterns import CROSS_REFERENCE, ISSUE_REFERENCE, Checked
from .settings import Config, answers_category, label_names, siblings
from .values import command, created, login


def other_issue(github: GitHub, number: int, current: int) -> dict[str, Any] | None:
    """Another issue of the repository, or None for a pull request or a wrong number."""
    if number == current:
        return None
    try:
        issue = github.issue(number)
    except GitHubError as error:
        if not error.missing():
            raise
        return None
    return None if "pull_request" in issue else issue


# How many of the issue numbers that the engine's texts name are looked up, each
# with a request or two, so that those that exist stay links.
MAX_NAMED = 10


def named_numbers(
    github: GitHub, current: int, texts: Iterable[str], known: Collection[Checked]
) -> set[int]:
    """The other issues and discussions that the texts name, as #123 or GH-123, and
    that exist: of the first MAX_NAMED that the checks didn't find already.

    The cleaning keeps a checked number as a link and turns any other into code, so
    that a number that the engine made up refers to nothing.
    """
    named = dict.fromkeys(
        int(match[1]) for text in texts for match in ISSUE_REFERENCE.finditer(text)
    )
    unknown = [
        number
        for number in named
        if 0 < number <= MAX_NUMBER and number != current and number not in known
    ]
    return {
        number
        for number in unknown[:MAX_NAMED]
        if other_issue(github, number, current) is not None
        or github.discussion_exists(number)
    }


def issue_elsewhere(github: GitHub, repository: str, number: int) -> str | None:
    """An issue of a related repository, as owner/name#123, when the repository is
    public and has the issue; None otherwise, also for a pull request."""
    other = github.sibling(repository)
    if other is None or other_issue(other, number, 0) is None:
        return None
    return f"{repository}#{number}"


def named_elsewhere(
    github: GitHub, config: Config, texts: Iterable[str], known: Collection[Checked]
) -> set[str]:
    """The issues of the related repositories that the texts name, as owner/name#123,
    and that exist: of the first MAX_NAMED that the checks didn't find already, as
    named_numbers does for this repository."""
    names = {name.casefold(): name for name in siblings(config)}
    named = dict.fromkeys(
        found
        for text in texts
        for match in CROSS_REFERENCE.finditer(text)
        if (found := cross_reference(match[1], names)) and found not in known
    )
    return {
        found
        for found in list(named)[:MAX_NAMED]
        if issue_elsewhere(github, *split_reference(found))
    }


def split_reference(reference: str) -> tuple[str, int]:
    """The repository and the number of owner/name#123."""
    repository, _, number = reference.rpartition("#")
    return repository, int(number)


def vouched_for(original: dict[str, Any]) -> bool:
    """Whether a maintainer opened the issue or triaged it, so that another issue may
    close as its duplicate.

    Every analysis reads the text of every issue, so anyone could open one that tells
    the engine to call later issues its duplicates, and close them.
    """
    return is_maintainer(original) or NEEDS_TRIAGE not in label_names(original)


def questions(answer: dict[str, Any], config: Config) -> list[str]:
    """The questions for more information that the reporter gets for an answer:
    those it lists, for a kind that labels.toml asks for, and never for spam."""
    if answer["kind"] == "spam" or answer["kind"] not in config.asking_kinds():
        return []
    return [ask.strip() for ask in answer["missing_information"] if ask.strip()]


def check_findings(
    github: GitHub,
    issue: dict[str, Any],
    answer: dict[str, Any],
    sha: str,
    config: Config,
) -> Findings:
    """Keep the duplicates and related issues that exist, and the files that fit.

    A sure duplicate of an open, older issue that a maintainer opened or triaged gets
    the notice and closes later; the first other candidate becomes a suggestion in
    GitHub's panel for the maintainer. A duplicate is always one of this
    repository: an issue of a related one can only be related, and the issue only
    said to belong in that one, which a maintainer then transfers by hand.
    """
    number = issue["number"]
    language = answer["language"]
    findings = Findings()
    related: list[tuple[Checked, str, str]] = []
    suggested = False
    for candidate in answer["duplicates"]:
        original = other_issue(github, candidate["number"], number)
        # Only an older issue can be the original, so two issues never close each other.
        if not original or original["number"] > number:
            continue
        if original["number"] in findings.numbers:
            continue
        findings.numbers.add(original["number"])
        if (
            candidate["confidence"] == "high"
            and original["state"] == "open"
            and vouched_for(original)
            and not findings.duplicate
        ):
            findings.duplicate = original
            findings.duplicate_reason = candidate["reason"]
            continue
        if not suggested and (
            original["state"] == "open" or original.get("state_reason") == "completed"
        ):
            github.suggest_duplicate(
                issue, original, plain(candidate["reason"], config)
            )
            suggested = True
        related.append((original["number"], "issue", candidate["reason"]))
    for item in answer["related"]:
        repository = item.get("repository", config.repository)
        if repository != config.repository:
            # Only issues: the context has no discussions of other repositories.
            found = (
                issue_elsewhere(github, repository, item["number"])
                if item["kind"] == "issue"
                else None
            )
            if found and found not in findings.numbers:
                findings.numbers.add(found)
                related.append((found, "issue", item["reason"]))
            continue
        if item["number"] in findings.numbers or item["number"] == number:
            continue
        if item["kind"] == "issue":
            exists = other_issue(github, item["number"], number) is not None
        else:
            exists = github.discussion_exists(item["number"])
        if exists:
            findings.numbers.add(item["number"])
            related.append((item["number"], item["kind"], item["reason"]))
    texts = [
        answer["summary"],
        answer["maintainer_summary"],
        answer["analysis"],
        *answer["next_steps"],
        *answer["missing_information"],
        findings.duplicate_reason,
        *(reason for _, _, reason in related),
        *(item["reason"] for item in answer["references"]),
    ]
    findings.numbers |= named_numbers(github, number, texts, findings.numbers)
    findings.numbers |= named_elsewhere(github, config, texts, findings.numbers)
    findings.related = [
        (elsewhere_link(entry) if isinstance(entry, str) else f"#{entry}")
        + (f" ({say(language, 'discussion')})" if kind == "discussion" else "")
        + f": {sanitize(one_line(reason), config, findings.numbers)}"
        for entry, kind, reason in related
    ]
    belongs_in = answer.get("belongs_in", config.repository)
    if belongs_in != config.repository and github.sibling(belongs_in):
        findings.belongs_in = belongs_in
    findings.references = references(
        answer["references"], sha, language, config, findings.numbers
    )
    return findings


def references(
    items: list[dict[str, Any]],
    sha: str,
    language: str,
    config: Config,
    numbers: Collection[Checked] = (),
) -> list[str]:
    """The links to the files to look at, each with its reason, in which the issues
    and discussions of `numbers`, which exist, stay links."""
    tracked = tracked_files(config.root)
    links: dict[str, str] = {}
    for item in items:
        link = reference_link(item, tracked, sha, language, config)
        if link and link not in links:
            links[link] = sanitize(one_line(item["reason"]), config, numbers)
    return [f"{link}: {reason}" for link, reason in links.items()]


# What GitHub shows next to the labels of a possible security report, instead of
# the engine's words, which could describe the vulnerability in public.
SECURITY_RATIONALE = "Possible security report; see the security policy."


def label_rationale(answer: dict[str, Any], config: Config) -> str:
    """Why the labels fit, for the timeline of the issue."""
    if answer.get("security_report"):
        return SECURITY_RATIONALE
    return plain(answer["label_rationale"], config)


def chosen_priority(
    github: GitHub,
    issue: dict[str, Any],
    answer: dict[str, Any],
    config: Config,
    keep: Collection[str] = (),
) -> str | None:
    """The priority of the answer for an issue without one; None otherwise.

    The assistant never changes a priority: one that the issue has stays, and so
    does the absence of one that someone removed (`keep`, or any removal on the
    timeline of the issue). A possible security report gets none: a public
    priority would judge the vulnerability in public, so the maintainers set it.
    """
    priorities = set(config.group("priority"))
    chosen = answer.get("priority", NO_PRIORITY)
    if (
        chosen == NO_PRIORITY
        or answer.get("security_report")
        or priorities & (label_names(issue) | set(keep))
    ):
        return None
    removed = any(
        event["event"] == "unlabeled"
        and (event.get("label") or {}).get("name") in priorities
        for event in github.events(issue["number"])
    )
    return None if removed else str(chosen)


def apply_issue_type(
    github: GitHub,
    issue: dict[str, Any],
    kind: str,
    config: Config,
    keep: Collection[str] = (),
) -> None:
    """Give the issue the issue type of its kind, as its label: an issue without a
    type, and one whose type is that of another kind while nobody triaged it. A
    kind without a type then takes away the type of the other kind.

    A type that no kind has, such as Epic, is someone's choice and stays, and so
    does the type of an issue whose kind someone changed (`keep`). A repository
    without the type, such as one of a person, which has no issue types, keeps
    the labels alone, and so does an issue whose type GitHub doesn't change.
    """
    if kind in keep:
        return
    wanted = config.issue_type(kind)
    number = issue["number"]
    # GitHub's REST API gives the type of an issue as `type`, or null.
    current = (issue.get("type") or {}).get("name") or ""
    of_kinds = {
        label.issue_type
        for label in config.labels
        if label.group == "type" and label.issue_type
    }
    if current == wanted or (
        current and (NEEDS_TRIAGE not in label_names(issue) or current not in of_kinds)
    ):
        return
    if not wanted:
        # Here `current` is the type of another kind, of an issue that needs triage.
        try:
            github.set_issue_type(issue, None, None)
        except GitHubError as error:
            command("notice", f"#{number} keeps its issue type: {error}")
        return
    try:
        type_id = github.issue_type_id(wanted)
        if type_id is None:
            command(
                "notice",
                f"{config.repository} has no issue type {wanted}, which {LABELS_FILE} "
                f"gives the kind {kind}; #{number} keeps its type.",
            )
            return
        github.set_issue_type(issue, wanted, type_id)
    except GitHubError as error:
        command("notice", f"#{number} keeps its issue type: {error}")


def assign_owners(
    github: GitHub, issue: dict[str, Any], labels: Collection[str], config: Config
) -> list[str]:
    """Assign an issue that nobody is assigned to to the owners of its areas among
    `labels`; GitHub notifies them. The assistant never mentions them.

    An issue that someone unassigned anyone from stays as it is: a person chose.
    """
    if issue.get("assignees"):
        return []
    owners = config.owners(labels)
    if not owners or any(
        event["event"] == "unassigned" for event in github.events(issue["number"])
    ):
        return []
    github.assign(issue["number"], owners)
    return owners


def apply_labels(
    github: GitHub,
    issue: dict[str, Any],
    answer: dict[str, Any],
    config: Config,
    keep: Collection[str] = (),
    *,
    assign: bool = False,
) -> list[str]:
    """Correct the kind while nobody triaged the issue, with its issue type, add
    areas and topics, and a priority to an issue without one; with `assign`, assign
    an issue that nobody is assigned to to the owners of its areas.

    The labels in `keep` stay as they are, added or removed: someone changed them.
    """
    current = label_names(issue)
    kind = answer["kind"]
    if NEEDS_TRIAGE in current:
        for name in config.group("type"):
            if name in current and name != kind and name not in keep:
                github.remove_label(issue["number"], name)
    wanted = [kind, *answer["areas"], *answer["topics"]]
    added = [
        name
        for name in dict.fromkeys(wanted)
        if name not in current and name not in keep
    ]
    github.add_explained_labels(issue, added, label_rationale(answer, config))
    if priority := chosen_priority(github, issue, answer, config, keep):
        github.add_explained_labels(
            issue, [priority], plain(answer["priority_rationale"], config)
        )
        added.append(priority)
    apply_issue_type(github, issue, kind, config, keep)
    if assign:
        assign_owners(github, issue, current.union(added), config)
    return added


def earlier_post(
    github: GitHub, issue: dict[str, Any], mode: str, comments: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The comment of an earlier run of the same event, which a run again repeats.

    A follow-up answers the reporter's comment or edit of the text before it; one
    after it is a new answer.
    """
    if mode == "triage":
        analyses = [comment for comment in comments if kind_of(comment) == "analysis"]
        return analyses[-1] if analyses else None
    author = login(issue.get("user"))
    conversation = [
        comment
        for comment in comments
        if kind_of(comment) in {"analysis", "follow-up"}
        or login(comment.get("user")) == author
    ]
    if (
        conversation
        and kind_of(conversation[-1]) == "follow-up"
        and not edited_since(github, issue, created(conversation[-1]))
    ):
        return conversation[-1]
    return None


def complete_labels(
    github: GitHub,
    issue: dict[str, Any],
    answer: dict[str, Any],
    config: Config,
    comments: list[dict[str, Any]],
    posted: dict[str, Any],
) -> list[str]:
    """Add the labels that belong to a comment of an earlier run, and are missing.

    GitHub can stop a run after its comment: before `needs-info`, which comes
    after the question, or, in a run of an earlier version, before any label. A
    label that anyone added or removed since the comment stays as it is. Whether
    the issue waits for its reporter, or is a possible duplicate, follows the
    comment, which said so, not the new answer: the reporter can already have
    answered it. The owners of the areas are assigned after an analysis, unless
    someone assigned or unassigned anyone since.
    """
    number = issue["number"]
    since = created(posted)
    events = [event for event in github.events(number) if created(event) >= since]
    changed = {
        (event.get("label") or {}).get("name", "")
        for event in events
        if event["event"] in {"labeled", "unlabeled"}
    }
    assign = kind_of(posted) == "analysis" and not any(
        event["event"] in {"assigned", "unassigned"} for event in events
    )
    added = apply_labels(github, issue, answer, config, keep=changed, assign=assign)
    _, said = marker(posted) or ("", {})
    author = login(issue.get("user"))
    # The reporter answers in a comment or by an edit of the text.
    waits = said.get("questions") == "1" and not (
        any(
            login(comment.get("user")) == author and created(comment) > since
            for comment in comments
        )
        or edited_since(github, issue, since)
    )
    lifecycle = [
        name
        for name, wanted in (
            (NEEDS_INFO, waits),
            (POSSIBLE_DUPLICATE, "duplicate" in said),
        )
        if wanted and name not in label_names(issue) and name not in changed
    ]
    github.add_labels(number, lifecycle)
    return added + lifecycle


def apply_answer(
    github: GitHub,
    mode: str,
    number: int,
    raw: str,
    config: Config,
    sha: str,
    *,
    repeat: bool = False,
    engine: str = DEFAULT_ENGINE,
    comment_id: int | None = None,
) -> list[str]:
    """Check the engine's answer and post it; `repeat` allows another analysis by
    hand. A reply task acts on the comment `comment_id`, which its context judged."""
    engine = engine_named(engine)
    answer = parse_answer(mode, raw, config)
    if mode in DISCUSSION_MODES:
        return apply_discussion_answer(
            github, number, answer, config, sha, engine, repeat=repeat
        )
    issue = github.issue(number)
    if "pull_request" in issue:
        raise AssistantError(f"#{number} is a pull request, not an issue.")
    if mode == "release-reply":
        return apply_release_reply(github, issue, answer, config, comment_id)
    if issue["state"] != "open" or issue.get("locked"):
        return [f"#{number} is closed or locked; nothing posted."]
    author = login(issue.get("user"))
    if mode == "maintainer-reply":
        return apply_maintainer_reply(github, issue, answer, config, comment_id)
    if mode == "maintainer-question":
        return apply_maintainer_question(
            github, issue, answer, config, sha, engine, comment_id, repeat=repeat
        )
    if not repeat:
        comments = github.comments(number)
        posted = earlier_post(github, issue, mode, comments)
        if posted is not None:
            # An earlier run posted the comment: never a second one.
            added = []
            if answer["kind"] != "spam":
                added = complete_labels(github, issue, answer, config, comments, posted)
            if not added:
                return [f"#{number} already has this {mode}; nothing posted."]
            return [
                f"#{number} already has this {mode}; no second comment, "
                f"labels added: {', '.join(added)}."
            ]
        if (
            mode == "follow-up"
            and follow_up_due(comments, author, config.lifecycle.max_follow_ups) is None
        ):
            # The analyze job takes minutes, in which a maintainer can join the
            # conversation, and the assistant stays out of it then.
            return [
                f"#{number} needs no follow-up any more: a maintainer joined the "
                "conversation, or its questions changed; nothing posted."
            ]
    if answer["kind"] == "spam":
        github.add_explained_labels(issue, [INVALID], label_rationale(answer, config))
        return [f"#{number} looks like spam: marked invalid, no comment."]
    asks = questions(answer, config)
    # The labels come before the comment, so that a run that GitHub stops leaves no
    # comment without its labels; the run again posts the comment. Only `needs-info`
    # comes after it: without the question, the sweep would remind the reporter of
    # one that nobody asked, and close the issue. A run again adds it, as the
    # comment says.
    if mode == "follow-up":
        numbers = named_numbers(
            github,
            number,
            [
                answer["reply"],
                answer["maintainer_summary"],
                *asks,
                *(item["reason"] for item in answer["references"]),
            ],
            (),
        )
        body = render_follow_up(
            answer,
            author,
            references(answer["references"], sha, answer["language"], config, numbers),
            asks,
            config,
            engine,
            numbers,
        )
        added = apply_labels(github, issue, answer, config)
        github.comment(number, body)
        if asks and NEEDS_INFO not in label_names(issue):
            github.add_labels(number, [NEEDS_INFO])
        return [f"Follow-up on #{number}; labels added: {', '.join(added) or 'none'}."]
    if answer["security_report"]:
        # Nothing about a possible vulnerability is discussed in public.
        asks = []
        findings = Findings()
    else:
        findings = check_findings(github, issue, answer, sha, config)
    planned = apply_labels(github, issue, answer, config, assign=True)
    # Without its notice, `possible-duplicate` closes nothing.
    duplicate = [POSSIBLE_DUPLICATE] if findings.duplicate else []
    github.add_labels(number, duplicate)
    github.comment(
        number,
        render_analysis(answer, author, findings, asks, planned, config, engine),
    )
    waiting = [NEEDS_INFO] if asks and NEEDS_INFO not in label_names(issue) else []
    github.add_labels(number, waiting)
    lifecycle = waiting + duplicate
    done = [
        f"Analysis on #{number}; labels added: {', '.join(planned + lifecycle) or 'none'}."
    ]
    if findings.belongs_in:
        done.append(
            f"#{number} may belong in {findings.belongs_in}; a maintainer can "
            "transfer it."
        )
    if suits_newcomer(answer, findings):
        done.append(
            f"#{number} may suit a newcomer, so the digest names it once a maintainer "
            f"has triaged it: {plain(answer['newcomer']['reason'], config)}"
        )
    return done


def acted_on(
    github: GitHub, issue: dict[str, Any], mode: str, comment_id: int | None
) -> tuple[list[dict[str, Any]], dict[str, Any]] | str:
    """The comments of the issue and the one that the reply task judged, read
    again, or why the task may not act on it.

    The engine judged the comment of comment.md alone, so the task acts only on
    it, and only when it comes from the reporter (release-reply) or a maintainer
    (maintainer-reply), or asks a maintainer's question (maintainer-question):
    neither a newer comment nor a run by hand on the comment of someone else stands
    in for it.
    """
    number = issue["number"]
    comments = github.comments(number)
    judged = judged_comment(issue, comments, mode, comment_id)
    if judged is None:
        return f"The comment judged on #{number} is gone; nothing done."
    if not judges(issue, judged, mode):
        role = {
            "release-reply": "the reporter's",
            "maintainer-question": "a maintainer's question",
        }.get(mode, "a maintainer's")
        return f"The comment judged on #{number} is not {role}; nothing done."
    return comments, judged


def apply_maintainer_reply(
    github: GitHub,
    issue: dict[str, Any],
    answer: dict[str, Any],
    config: Config,
    comment_id: int | None = None,
) -> list[str]:
    number = issue["number"]
    reason = plain(answer["reason"], config)
    if not answer["waiting_for_reporter"]:
        return [f"#{number} does not wait for the reporter: {reason}"]
    if NEEDS_INFO in label_names(issue):
        return [f"#{number} already waits for the reporter."]
    found = acted_on(github, issue, "maintainer-reply", comment_id)
    if isinstance(found, str):
        return [found]
    comments, judged = found
    author = login(issue.get("user"))
    if any(
        login(comment.get("user")) == author and created(comment) > created(judged)
        for comment in comments
    ):
        return [f"The reporter of #{number} already answered."]
    github.add_labels(number, [NEEDS_INFO])
    return [f"#{number} waits for the reporter: {reason}"]


def apply_maintainer_question(
    github: GitHub,
    issue: dict[str, Any],
    answer: dict[str, Any],
    config: Config,
    sha: str,
    engine: str = DEFAULT_ENGINE,
    comment_id: int | None = None,
    *,
    repeat: bool = False,
) -> list[str]:
    """Post the answer to the question of a maintainer's /assistant ask, once,
    checked and cleaned as every text of the engine; a run by hand answers again."""
    number = issue["number"]
    found = acted_on(github, issue, "maintainer-question", comment_id)
    if isinstance(found, str):
        return [found]
    comments, judged = found
    question = str(judged["id"])
    if not repeat and any(
        (marker(item) or ("", {}))[1].get("to") == question
        for item in comments
        if kind_of(item) == "answer"
    ):
        return [f"The question on #{number} has its answer already; nothing posted."]
    numbers = named_numbers(
        github,
        number,
        [answer["answer"], *(item["reason"] for item in answer["references"])],
        (),
    )
    asker = login(judged.get("user"))
    github.comment(
        number,
        render_answer(
            answer,
            asker,
            number,
            judged["id"],
            references(answer["references"], sha, answer["language"], config, numbers),
            config,
            engine,
            numbers,
        ),
    )
    return [f"Answer on #{number} to the question of {asker}."]


def apply_discussion_answer(
    github: GitHub,
    number: int,
    answer: dict[str, Any],
    config: Config,
    sha: str,
    engine: str = DEFAULT_ENGINE,
    *,
    repeat: bool = False,
) -> list[str]:
    """Post the first answer to a discussion, checked and cleaned as every text of
    the engine, and never mark it as the answer.

    The discussion is read again, because the analysis took minutes: it gets no
    answer once it is closed or locked, and, unless a maintainer runs it by hand,
    none once it has one of the assistant, an answer that its author marked, or a
    comment of a maintainer, who took it over, or once it moved to a category that
    gets none.
    """
    discussion = github.discussion(number)
    if discussion is None:
        return [f"#{number} is no discussion; nothing posted."]
    if discussion["state"] != "open" or discussion["locked"]:
        return [f"Discussion #{number} is closed or locked; nothing posted."]
    if not repeat:
        comments = discussion["comments"]
        thread = [*comments, *(reply for item in comments for reply in item["replies"])]
        # An issue converted into a discussion keeps the assistant's analysis.
        if any(marker(item) is not None for item in comments):
            return [
                f"Discussion #{number} has an answer of the assistant; nothing posted."
            ]
        if discussion["answered"] or any(map(is_maintainer, thread)):
            return [
                f"Discussion #{number} has an answer or a maintainer's comment "
                "already; nothing posted."
            ]
        if not answers_category(config, discussion["category"]["name"]):
            return [
                f"Discussion #{number} is no longer in a category of [discussions] "
                "answer_categories; nothing posted."
            ]
    if answer["kind"] == "spam":
        return [f"Discussion #{number} looks like spam: no answer."]
    asks: list[str] = []
    numbers: set[int] = set()
    links: list[str] = []
    # Nothing about a possible vulnerability is discussed in public: no questions,
    # no files and no other issue.
    if not answer["security_report"]:
        asks = [ask.strip() for ask in answer["missing_information"] if ask.strip()]
        reasons = [item["reason"] for item in answer["references"]]
        texts = [answer["answer"], answer["maintainer_summary"], *asks, *reasons]
        numbers = named_numbers(github, number, texts, ())
        links = references(
            answer["references"], sha, answer["language"], config, numbers
        )
    github.answer_discussion(
        discussion,
        render_discussion_answer(
            answer,
            login(discussion.get("user")),
            links,
            asks,
            config,
            engine,
            numbers,
            answerable=discussion["category"]["is_answerable"],
        ),
    )
    return [f"First answer on discussion #{number}: {answer['kind']}."]


def apply_release_reply(
    github: GitHub,
    issue: dict[str, Any],
    answer: dict[str, Any],
    config: Config,
    comment_id: int | None = None,
) -> list[str]:
    """Reopen an issue that a release closed when its reporter says it isn't fixed."""
    number = issue["number"]
    reason = plain(answer["reason"], config)
    if issue["state"] == "open" or issue.get("locked"):
        return [f"#{number} is open or locked; nothing to reopen."]
    if not answer["problem_persists"]:
        return [f"#{number} stays closed: {reason}"]
    found = acted_on(github, issue, "release-reply", comment_id)
    if isinstance(found, str):
        return [found]
    github.reopen(number)
    github.add_labels(number, [NEEDS_TRIAGE])
    github.comment(
        number, render_reopened_release(login(issue.get("user")), answer["language"])
    )
    return [f"#{number} reopened: {reason}"]
