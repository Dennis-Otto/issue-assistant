"""What an issue and its comments say: who wrote them, the marks of the assistant, the
issue forms and the commands."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .constants import BOT, DIGEST, MAX_TEXT, MUTED, PINNED
from .github import GitHub
from .languages import ANSWER_LANGUAGES, detect_language
from .patterns import INVISIBLE
from .settings import DEFAULT_LIFECYCLE, Config, Label, Lifecycle, label_names
from .values import created, login

MAINTAINERS = {"OWNER", "MEMBER", "COLLABORATOR"}

# A command on the first line of a comment: /assistant, its name and what follows,
# such as /assistant duplicate #12. Maintainers use every command, the reporter
# those of REPORTER_COMMANDS; from anyone else, the line is an ordinary comment.
# Commands steer the assistant and are no part of the conversation: they answer no
# question, ask none and object to no duplicate notice.
COMMAND_LINE = re.compile(r" {0,3}/assistant(?![\w-])[ \t]*(\S*)[ \t]*(.*)", re.I)
COMMANDS = ("again", "duplicate", "not-duplicate", "waiting", "mute", "unmute", "ask")
REPORTER_COMMANDS = ("again", "not-duplicate")

MARKER = re.compile(r"<!-- issue-assistant:([a-z-]+)((?: [a-z]+=[\w.+/-]+)*) -->")

# A heading of an issue form: GitHub writes the label of each field as `### Label`
# above its answer.
FORM_HEADING = re.compile(r"^###[ \t]+(.*)$", re.MULTILINE)
# A ticked box of a form, as GitHub writes it into the text of the issue.
TICKED_BOX = re.compile(r"^[ \t]*[-*][ \t]+\[[xX]\]", re.MULTILINE)


def is_bot(user: dict[str, Any] | None) -> bool:
    return (user or {}).get("type") == "Bot" or login(user).endswith("[bot]")


def is_maintainer(item: dict[str, Any]) -> bool:
    return item.get("author_association") in MAINTAINERS


def text_of(item: dict[str, Any]) -> str:
    """The text of an issue or a comment, as much of it as GitHub keeps."""
    return str(item.get("body") or "")[:MAX_TEXT]


def marker(comment: dict[str, Any]) -> tuple[str, dict[str, str]] | None:
    """The kind and attributes of an assistant comment; None for every other comment."""
    if login(comment.get("user")) != BOT:
        return None
    match = MARKER.search(text_of(comment))
    if not match:
        return None
    attributes = dict(part.split("=", 1) for part in match[2].split())
    return match[1], attributes


def kind_of(comment: dict[str, Any]) -> str | None:
    found = marker(comment)
    return found[0] if found else None


def is_digest(issue: dict[str, Any]) -> bool:
    """Whether the issue is the one that keeps the digest of the sweep: the assistant
    opened it, with its marker, so that nobody else can make an issue pass for it."""
    return kind_of(issue) == DIGEST


def render_marker(kind: str, **attributes: object) -> str:
    parts = "".join(f" {key}={value}" for key, value in attributes.items())
    return f"<!-- issue-assistant:{kind}{parts} -->"


def form_fields(body: str) -> dict[str, str]:
    """The answers of an issue form, by the label of their field.

    An answer is the text between its heading and the next one. Each heading is
    found once and the answers are cut out between them, so the time grows with the
    length of the text and no faster. GitHub writes an answer under every heading of
    a form, so a heading on the last line, with no line break after it, is a heading
    of free text and starts no field.
    """
    body = body or ""
    headings = list(FORM_HEADING.finditer(body))
    starts = [heading.start() for heading in headings] + [len(body)]
    fields = {}
    for heading, end in zip(headings, starts[1:], strict=True):
        if heading.end() == len(body):
            continue
        value = body[heading.end() : end].strip()
        fields[heading[1].strip()] = "" if value == "_No response_" else value
    return fields


def form_labels(issue: dict[str, Any], config: Config) -> list[str]:
    """The labels that the answer of the area field chooses.

    GitHub joins the options of a dropdown with `multiple: true` with ", "; an
    option that holds ", " itself stays one answer.
    """
    area = form_fields(text_of(issue)).get(config.area_field, "")
    options = {option for label in config.labels for option in label.form_options}
    answers = {area} if area in options else set(area.split(", "))
    return [label.name for label in config.labels if answers & set(label.form_options)]


def box_ticked(body: str | None, config: Config) -> bool:
    """Whether the text of an issue has the box of [forms] opt_out_field ticked, with
    which its reporter keeps the AI out of it."""
    answer = form_fields(body or "").get(config.opt_out_field, "")
    return TICKED_BOX.search(answer) is not None


def without_box(body: str | None, config: Config) -> str:
    """The text of an issue without the answer of the box of [forms] opt_out_field, to
    tell a click on the box from an edit of the text. The answer is cut out at its last
    place, as a form writes the box last."""
    body = body or ""
    answer = form_fields(body).get(config.opt_out_field)
    if not answer:
        return body
    start, _, end = body.rpartition(answer)
    return start + end


def opted_out(issue: dict[str, Any], config: Config) -> bool:
    """Whether the AI stays out of the issue at its reporter's wish, also when it
    reads for another issue: the box is ticked and the issue still muted. Both ways
    back remove the label: the reporter's /assistant again and a maintainer's
    /assistant unmute."""
    return MUTED in label_names(issue) and box_ticked(text_of(issue), config)


def issue_language(issue: dict[str, Any], comments: list[dict[str, Any]]) -> str:
    """The language of the assistant's last analysis, or the one the reporter writes in."""
    for comment in reversed(comments):
        found = marker(comment)
        if found and found[0] in {"analysis", "follow-up"} and "lang" in found[1]:
            language = found[1]["lang"]
            return language if language in ANSWER_LANGUAGES else "en"
    author = login(issue.get("user"))
    body = text_of(issue)
    answers = " ".join(form_fields(body).values()) or body
    texts = [answers] + [
        text_of(comment) for comment in comments if login(comment.get("user")) == author
    ]
    return detect_language(" ".join(texts))


def follow_up_due(
    comments: list[dict[str, Any]],
    author: str,
    most: int = DEFAULT_LIFECYCLE.max_follow_ups,
) -> dict[str, Any] | None:
    """The assistant's comment with the questions that the reporter answered, when
    the assistant asked them last and may follow up, up to `most` times; None
    otherwise."""
    asks = [
        (index, comment)
        for index, comment in enumerate(comments)
        if kind_of(comment) in {"analysis", "follow-up"}
    ]
    if not asks:
        return None
    index, last = asks[-1]
    found = marker(last)
    if not found or found[1].get("questions") != "1":
        return None
    if sum(kind_of(comment) == "follow-up" for comment in comments) >= most:
        return None
    # Once the maintainer joined the conversation, the assistant stays out of it.
    if any(
        is_maintainer(comment) and login(comment.get("user")) != author
        for comment in comments[index + 1 :]
    ):
        return None
    return last


def edited_since(github: GitHub, issue: dict[str, Any], since: datetime) -> bool:
    """Whether the reporter changed the text of the issue after the moment.

    That answers the assistant's questions as a comment does, since they offer both;
    a new title answers nothing.
    """
    edit = github.last_edit(issue["number"])
    return edit is not None and edit[0] == login(issue.get("user")) and edit[1] > since


def runs_of(comment: dict[str, Any]) -> list[int]:
    """The moments, in seconds since 1970, of the AI runs that answered this comment
    of the assistant, which its marker records; nobody else can change it."""
    found = marker(comment)
    runs = found[1].get("runs", "") if found else ""
    return [int(moment) for moment in re.findall(r"\d{1,10}", runs)]


def record_run(
    github: GitHub,
    comment: dict[str, Any],
    at: datetime,
    marks: dict[str, str] | None = None,
) -> None:
    """Add the run of the AI that starts at the moment to the marker of the
    assistant's comment that it answers, with the attributes `marks`. Only the
    assistant can change its comments, so the record holds where a reporter could
    delete their own."""
    runs = ".".join(str(moment) for moment in [*runs_of(comment), int(at.timestamp())])
    attributes = {**(marks or {}), "runs": runs}

    def recorded(match: re.Match[str]) -> str:
        others = re.sub(rf" (?:{'|'.join(attributes)})=\S+", "", match[2])
        added = "".join(f" {key}={value}" for key, value in attributes.items())
        return f"<!-- issue-assistant:{match[1]}{others}{added} -->"

    github.edit_comment(comment["id"], MARKER.sub(recorded, text_of(comment), count=1))


@dataclass(frozen=True)
class Command:
    """A command of a comment: its name, in lower case, and what follows it."""

    name: str
    argument: str


def command_of(issue: dict[str, Any], comment: dict[str, Any]) -> Command | None:
    """The command on the first line of a comment of a maintainer or of the reporter
    of the issue; None for any other comment. The same line from anyone else is an
    ordinary comment.

    The argument is the rest of the comment, for the question of ask, which may
    take several lines.
    """
    if is_bot(comment.get("user")) or not (
        is_maintainer(comment) or login(comment.get("user")) == login(issue.get("user"))
    ):
        return None
    first, _, rest = text_of(comment).partition("\n")
    match = COMMAND_LINE.fullmatch(first.rstrip("\r"))
    if not match:
        return None
    return Command(match[1].lower(), f"{match[2]}\n{rest}".strip())


def refusal(comment: dict[str, Any], given: Command) -> str:
    """Why the line is no command that its author may give, or empty when it is one.

    Its text is the author's, so the reason doesn't repeat it.
    """
    if given.name not in COMMANDS:
        return f"No command of the assistant; the commands are {', '.join(COMMANDS)}."
    if not is_maintainer(comment) and given.name not in REPORTER_COMMANDS:
        return (
            f"Only maintainers use /assistant {given.name}; the reporter may use "
            f"{' and '.join(REPORTER_COMMANDS)}."
        )
    return ""


def conversing(issue: dict[str, Any], comment: dict[str, Any]) -> bool:
    """Whether the comment is part of the conversation: no command that its author
    may give, which asks, answers and objects to nothing. A refused line, such as a
    mistyped command, is an ordinary comment."""
    given = command_of(issue, comment)
    return given is None or bool(refusal(comment, given))


def defined_label(config: Config, starter: Label) -> Label:
    """A label that the assistant creates when it needs it, as labels.toml defines
    it or else as `starter` does."""
    return next(
        (label for label in config.labels if label.name == starter.name), starter
    )


def muted_label(config: Config) -> Label:
    """The label of muted issues."""
    return defined_label(
        config,
        Label(
            MUTED,
            "ededed",
            "The assistant starts no AI run on this issue; /assistant unmute ends it",
            "decision",
        ),
    )


def pinned_label(config: Config) -> Label:
    """The label that exempts the issue of the digest from the lifecycle."""
    return defined_label(
        config,
        Label(
            PINNED,
            "ededed",
            "Stays open: the sweep never reminds, closes or locks this issue",
            "lifecycle",
        ),
    )


@dataclass
class Visible:
    """Takes the characters that GitHub shows as nothing out of texts, and counts
    them."""

    removed: int = 0

    def __call__(self, text: str) -> str:
        text, count = INVISIBLE.subn("", text)
        self.removed += count
        return text

    def everywhere(self, value: Any) -> Any:
        """A value of JSON with each of its texts, at any depth, taken through."""
        if isinstance(value, str):
            return self(value)
        if isinstance(value, dict):
            return {key: self.everywhere(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.everywhere(item) for item in value]
        return value


def judges(issue: dict[str, Any], comment: dict[str, Any], mode: str) -> bool:
    """Whether a reply task may act on the comment: release-reply on one of the
    reporter, maintainer-reply on one of a maintainer who isn't the reporter and
    gives no command, maintainer-question on a maintainer's /assistant ask."""
    reporter = login(issue.get("user"))
    if mode == "release-reply":
        return login(comment.get("user")) == reporter
    given = command_of(issue, comment)
    if mode == "maintainer-question":
        return is_maintainer(comment) and given is not None and given.name == "ask"
    return (
        is_maintainer(comment)
        and login(comment.get("user")) != reporter
        and conversing(issue, comment)
    )


def judged_comment(
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
    mode: str,
    comment_id: int | None,
) -> dict[str, Any] | None:
    """The comment that a reply task judges: the one whose event started the run,
    not a newer one that came in while the run waited, or, in a run by hand, the
    newest on which the task may act. None once it is deleted, or without one."""
    if comment_id is not None:
        return next((item for item in comments if item.get("id") == comment_id), None)
    fitting = [item for item in comments if judges(issue, item, mode)]
    return fitting[-1] if fitting else None


def issue_of(comment: dict[str, Any]) -> int:
    """The number of the issue or pull request of a comment of the repository's list."""
    return int(str(comment["issue_url"]).rsplit("/", 1)[1])


def is_duplicate_notice(comment: dict[str, Any]) -> bool:
    """Whether the comment names a sure duplicate: an analysis, or the comment that a
    maintainer's /assistant duplicate posted."""
    found = marker(comment)
    return (
        found is not None
        and found[0] in {"analysis", "duplicate"}
        and "duplicate" in found[1]
    )


def upvotes(issue: dict[str, Any]) -> int:
    return int((issue.get("reactions") or {}).get("+1") or 0)


@dataclass(frozen=True)
class Activity:
    """What the summary of the sweep reads once: the comments written since the
    moment, the oldest first, by the number of their issue or pull request, and the
    issues that changed since, by their number."""

    since: datetime
    comments: dict[int, list[dict[str, Any]]]
    issues: dict[int, dict[str, Any]]

    @classmethod
    def read(cls, github: GitHub, since: datetime) -> Activity:
        comments: dict[int, list[dict[str, Any]]] = {}
        # GitHub lists the comments edited since as well; only those written count.
        for comment in sorted(github.comments_since(since), key=created):
            if created(comment) >= since:
                comments.setdefault(issue_of(comment), []).append(comment)
        issues = {issue["number"]: issue for issue in github.issues_since(since)}
        return cls(since, comments, issues)

    def posted(self, kind: str) -> Iterator[tuple[int, dict[str, Any]]]:
        """The assistant's comments of the kind, with the number of their issue."""
        for number, comments in self.comments.items():
            for comment in comments:
                if kind_of(comment) == kind:
                    yield number, comment

    def analyzed(self) -> dict[int, datetime]:
        """The issues with an analysis, and the moment of their first one."""
        first: dict[int, datetime] = {}
        for number, comment in self.posted("analysis"):
            if number in self.issues:
                first.setdefault(number, created(comment))
        return first


def reactions_on(activity: Activity, kind: str) -> tuple[int, int, int]:
    """How many comments of the kind the assistant posted, and their 👍 and 👎."""
    count = up = down = 0
    for _, comment in activity.posted(kind):
        reactions = comment.get("reactions") or {}
        count += 1
        up += int(reactions.get("+1") or 0)
        down += int(reactions.get("-1") or 0)
    return count, up, down


def exemption(issue: dict[str, Any], lifecycle: Lifecycle) -> str:
    """Why the sweep leaves the issue to the maintainers: a label of exempt_labels,
    an assignee or enough 👍, in words for its summary; empty when it doesn't."""
    exempt = {name.casefold() for name in lifecycle.exempt_labels}
    labels = sorted(name for name in label_names(issue) if name.casefold() in exempt)
    if labels:
        return f"labeled {labels[0]}"
    if lifecycle.exempt_assignees and issue.get("assignees"):
        return "assigned"
    count = upvotes(issue)
    if lifecycle.exempt_upvotes and count >= lifecycle.exempt_upvotes:
        return f"{count} 👍"
    return ""
