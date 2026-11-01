"""The comments of the assistant, in the language of the reporter."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .cleaning import code_span, notice_link, one_line, sanitize
from .constants import ACTION, LET_IN
from .engines import DEFAULT_ENGINE, ENGINES
from .issues import render_marker
from .languages import format_date, say, texts
from .paths import FORM_STARTERS
from .patterns import Checked
from .settings import DEFAULT_LIFECYCLE, Config, Lifecycle, maintainers

# Where a reader of a comment finds the tool that wrote it; see invitation.
PROJECT = f"https://github.com/{ACTION}"

# A line of a list item after its first, with the spaces and tabs that indent it.
CONTINUATION = re.compile(r"\n([ \t]*)(?=[^ \t\n])")


def thanks(language: str, kind: str, author: str) -> str:
    key = f"thanks.{kind}"
    return say(
        language, key if key in texts(language) else "thanks.default", author=author
    )


def sentences(first: str, then: str) -> str:
    """Two sentences as one paragraph, with a space between them as most languages
    write it, but none after a full-width mark such as 。 or ！, after which Chinese
    and Japanese go straight on."""
    full_width = unicodedata.east_asian_width(first[-1:] or " ") in "FW"
    return f"{first}{'' if full_width else ' '}{then}"


def bullets(items: Iterable[str]) -> str:
    """A Markdown list of the items, each with all its lines.

    The lines after the first are indented under the bullet: without, the first of
    them would end the list item, and a code block in it with the item, so that the
    rest of the code became text. Tabs that indent a line become spaces, because
    under the bullet they would reach other tab stops than in the item alone.
    """
    return "\n".join(
        "- " + CONTINUATION.sub(lambda m: "\n  " + m[1].expandtabs(4), item)
        for item in items
    )


@dataclass
class Findings:
    """The engine's answer after the checks, ready to post."""

    duplicate: dict[str, Any] | None = None
    duplicate_reason: str = ""
    related: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    numbers: set[Checked] = field(default_factory=set)
    # The related repository that the issue may belong in; empty for this one.
    belongs_in: str = ""


# More links than this, to related issues and to files, fold into one line that
# opens them, so that the questions for the reporter stay in view.
MAX_OPEN_LINKS = 3


def link_sections(language: str, related: list[str], references: list[str]) -> str:
    """The lists of the related issues and of the files to look at, folded when they
    hold more than MAX_OPEN_LINKS links; empty without any."""
    sections = [
        f"### {say(language, key)}\n\n{bullets(items)}"
        for key, items in (("related", related), ("references", references))
        if items
    ]
    count = len(related) + len(references)
    if count <= MAX_OPEN_LINKS:
        return "\n\n".join(sections)
    # The blank lines let GitHub read the Markdown inside the HTML.
    return (
        f"<details><summary>{say(language, 'links', count=count)}</summary>\n\n"
        + "\n\n".join(sections)
        + "\n\n</details>"
    )


def invitation(language: str, config: Config, *, duplicate: bool = False) -> str:
    """The end of a footer: a link to how the assistant works, the request for a 👍
    or 👎, which on a duplicate notice also objects to the closing, and the link to
    the project, unless `[transparency] credit` leaves it out."""
    notice = notice_link(config)
    ends = [
        say(language, "about", notice=notice) if notice else "",
        say(language, "feedback.duplicate" if duplicate else "feedback"),
        say(language, "credit", project=PROJECT) if config.credit else "",
    ]
    return " · ".join(end for end in ends if end)


def for_maintainers(
    answer: dict[str, Any], config: Config, numbers: Iterable[Checked] = ()
) -> str:
    """The AI's summary for the maintainers, folded under a line in their language,
    when it answers in another one; empty for an answer in theirs or without a
    summary. `numbers` are the issues and discussions that its text may link."""
    own = maintainers(config)
    if answer["language"] == own:
        return ""
    summary = sanitize(answer.get("maintainer_summary", ""), config, numbers)
    if not summary:
        return ""
    # The blank lines let GitHub read the Markdown inside the HTML.
    return (
        f"<details><summary>{say(own, 'maintainers')}</summary>\n\n"
        f"{summary}\n\n</details>"
    )


def maintainer_mark(folded: str, config: Config) -> dict[str, str]:
    """The attribute of a comment's marker that records the language of its summary
    for the maintainers, such as maint=en; none for a comment without one."""
    return {"maint": maintainers(config)} if folded else {}


def suits_newcomer(answer: dict[str, Any], findings: Findings) -> bool:
    """Whether the analysis marks the issue as a candidate for a label of the group
    `contribution`, which the digest names once a maintainer has triaged it.

    The AI says so, and the script checks what it can: a change is located only by a
    file that exists, and is confirmed only with nothing missing. A security report, a
    duplicate and an issue of another repository never count, whatever the AI says.
    """
    return bool(
        answer.get("newcomer", {}).get("fit")
        and findings.references
        and not answer["missing_information"]
        and not answer["duplicates"]
        and not answer["security_report"]
        and not findings.belongs_in
    )


def render_analysis(
    answer: dict[str, Any],
    author: str,
    findings: Findings,
    asks: list[str],
    labels: list[str],
    config: Config,
    engine: str = DEFAULT_ENGINE,
) -> str:
    language = answer["language"]
    numbers = findings.numbers
    periods = config.lifecycle.text(language)

    def clean(value: str) -> str:
        return sanitize(value, config, numbers)

    # Nothing about a possible vulnerability is repeated, not even for them.
    folded = (
        "" if answer["security_report"] else for_maintainers(answer, config, numbers)
    )
    parts = [
        render_marker(
            "analysis",
            lang=language,
            **maintainer_mark(folded, config),
            questions=int(bool(asks)),
            engine=engine,
            **(
                {"duplicate": findings.duplicate["number"]}
                if findings.duplicate
                else {}
            ),
            **({"newcomer": 1} if suits_newcomer(answer, findings) else {}),
        ),
        sentences(thanks(language, answer["kind"], author), say(language, "intro")),
    ]
    if answer["sensitive_data"] != "none":
        parts.append(say(language, f"sensitive.{answer['sensitive_data']}"))
    if answer["security_report"]:
        parts.append(say(language, "security", policy=config.policy))
    else:
        if findings.duplicate:
            number = findings.duplicate["number"]
            parts.append(
                f"### {say(language, 'duplicate')}\n\n"
                + say(
                    language,
                    "duplicate.text",
                    number=number,
                    reason=clean(one_line(findings.duplicate_reason)),
                )
                + "\n\n"
                + say(language, "duplicate.close", number=number, **periods)
            )
        parts.append(f"### {say(language, 'summary')}\n\n{clean(answer['summary'])}")
        parts.append(f"### {say(language, 'analysis')}\n\n{clean(answer['analysis'])}")
        if findings.belongs_in:
            parts.append(
                say(language, "belongs_in", repository=code_span(findings.belongs_in))
            )
        steps = [clean(step) for step in answer["next_steps"] if step.strip()]
        if steps:
            parts.append(f"### {say(language, 'next_steps')}\n\n{bullets(steps)}")
        if asks:
            parts.append(
                f"### {say(language, 'missing')}\n\n{say(language, 'missing.intro')}\n\n"
                f"{bullets(clean(ask) for ask in asks)}\n\n"
                f"{say(language, 'missing.outro', **periods)}"
            )
        links = link_sections(language, findings.related, findings.references)
        if links:
            parts.append(links)
        if folded:
            parts.append(folded)
    shown = ", ".join(f"`{name}`" for name in labels)
    parts.append(
        say(
            language,
            "footer",
            labels=say(language, "labels", labels=shown) if labels else "",
            engine=ENGINES[engine].name,
            feedback=invitation(language, config, duplicate=bool(findings.duplicate)),
        )
    )
    return "\n\n".join(parts) + "\n"


def render_follow_up(
    answer: dict[str, Any],
    author: str,
    references: list[str],
    asks: list[str],
    config: Config,
    engine: str = DEFAULT_ENGINE,
    numbers: Collection[int] = (),
) -> str:
    """The follow-up on an answer of the reporter; `numbers` are the issues and
    discussions that exist, which its text may link."""
    language = answer["language"]
    folded = for_maintainers(answer, config, numbers)
    parts = [
        render_marker(
            "follow-up",
            lang=language,
            **maintainer_mark(folded, config),
            questions=int(bool(asks)),
            engine=engine,
        ),
        say(language, "follow-up.thanks", author=author),
    ]
    if answer["sensitive_data"] != "none":
        parts.append(say(language, f"sensitive.{answer['sensitive_data']}"))
    parts.append(sanitize(answer["reply"], config, numbers))
    if asks:
        parts.append(
            f"### {say(language, 'follow-up.missing')}\n\n"
            f"{say(language, 'follow-up.missing.intro')}\n\n"
            f"{bullets(sanitize(ask, config, numbers) for ask in asks)}\n\n"
            f"{say(language, 'missing.outro', **config.lifecycle.text(language))}"
        )
    links = link_sections(language, [], references)
    if links:
        parts.append(links)
    if folded:
        parts.append(folded)
    parts.append(
        say(
            language,
            "follow-up.footer",
            engine=ENGINES[engine].name,
            feedback=invitation(language, config),
        )
    )
    return "\n\n".join(parts) + "\n"


def render_answer(
    answer: dict[str, Any],
    asker: str,
    number: int,
    question: int,
    references: list[str],
    config: Config,
    engine: str = DEFAULT_ENGINE,
    numbers: Collection[int] = (),
) -> str:
    """The answer to the question of a maintainer's /assistant ask, the comment
    `question` of issue #number; its marker names the comment, so that a run again
    answers it once. `numbers` are the issues and discussions that exist, which its
    text may link."""
    language = answer["language"]
    address = (
        f"https://github.com/{config.repository}/issues/{number}"
        f"#issuecomment-{question}"
    )
    parts = [
        render_marker("answer", lang=language, to=question, engine=engine),
        say(language, "answer.intro", asker=asker, question=address),
        sanitize(answer["answer"], config, numbers),
    ]
    links = link_sections(language, [], references)
    if links:
        parts.append(links)
    parts.append(
        say(
            language,
            "answer.footer",
            engine=ENGINES[engine].name,
            feedback=invitation(language, config),
        )
    )
    return "\n\n".join(parts) + "\n"


def issue_form(config: Config, kind: str) -> str:
    """Where someone opens the issue that a discussion suggests: the form of the
    starter for a bug or a feature, where the repository has it, else the list of
    forms."""
    new = f"https://github.com/{config.repository}/issues/new"
    form = FORM_STARTERS[kind]
    if (config.root / form).is_file():
        return f"{new}?template={form.name}"
    return f"{new}/choose"


def render_discussion_answer(
    answer: dict[str, Any],
    author: str,
    references: list[str],
    asks: list[str],
    config: Config,
    engine: str = DEFAULT_ENGINE,
    numbers: Collection[Checked] = (),
    *,
    answerable: bool = True,
) -> str:
    """The first answer to a discussion, with what it still needs to know and the
    files to look at; a bug or a request for a feature also gets a sentence that
    asks for an issue. `numbers` are the issues and discussions that exist, which
    its text may link; `answerable` says that the asker can mark it as the answer
    in this category. A possible security report gets only the pointer to the
    security policy."""
    language = answer["language"]
    security = answer["security_report"]
    folded = "" if security else for_maintainers(answer, config, numbers)
    parts = [
        render_marker(
            "discussion-answer",
            lang=language,
            **maintainer_mark(folded, config),
            type=answer["kind"],
            questions=int(bool(asks)),
            engine=engine,
        ),
        say(language, "discussion.intro", author=author),
    ]
    if answer["sensitive_data"] != "none":
        parts.append(say(language, f"discussion.sensitive.{answer['sensitive_data']}"))
    if security:
        parts.append(say(language, "security", policy=config.policy))
    else:
        parts.append(sanitize(answer["answer"], config, numbers))
        if answer["kind"] in {"bug", "feature"}:
            parts.append(
                say(
                    language,
                    f"discussion.{answer['kind']}",
                    form=issue_form(config, answer["kind"]),
                )
            )
        if asks:
            parts.append(
                f"### {say(language, 'missing')}\n\n"
                f"{say(language, 'discussion.missing.intro')}\n\n"
                f"{bullets(sanitize(ask, config, numbers) for ask in asks)}"
            )
        links = link_sections(language, [], references)
        if links:
            parts.append(links)
        if folded:
            parts.append(folded)
    parts.append(
        say(
            language,
            "discussion.footer",
            engine=ENGINES[engine].name,
            answerable=say(language, "discussion.answerable")
            if answerable and not security
            else "",
            feedback=invitation(language, config),
        )
    )
    return "\n\n".join(parts) + "\n"


def render_reminder(
    author: str, language: str, closes: datetime, questions: str = ""
) -> str:
    """The reminder, which names the day the issue closes and links the comment
    that asked the questions at the address `questions`, when it is known; its
    marker records the day, so that the sweep closes the issue on it."""
    named = format_date(closes, language)
    asked = say(language, "reminder.questions")
    if questions:
        asked = f"[{asked}]({questions})"
    return (
        f"{render_marker('reminder', closes=closes.date().isoformat())}\n"
        f"{say(language, 'reminder', author=author, date=named, questions=asked)}\n"
    )


def render_closed_unanswered(
    author: str, language: str, lifecycle: Lifecycle = DEFAULT_LIFECYCLE
) -> str:
    periods = lifecycle.text(language)
    return (
        f"{render_marker('closed-unanswered')}\n"
        f"{say(language, 'closed-unanswered', author=author, **periods)}\n"
    )


def render_closed_duplicate(
    number: int, language: str, lifecycle: Lifecycle = DEFAULT_LIFECYCLE
) -> str:
    periods = lifecycle.text(language)
    return (
        f"{render_marker('closed-duplicate', of=number)}\n"
        f"{say(language, 'closed-duplicate', number=number, **periods)}\n"
    )


def render_duplicate(number: int, author: str, language: str, config: Config) -> str:
    """The notice of a duplicate of #number that a maintainer named with /assistant
    duplicate. Its marker names the original, as that of an analysis does, so that
    the sweep closes the issue after the same days unless someone objects."""
    periods = config.lifecycle.text(language)
    notice = notice_link(config)
    about = f"\n<sub>{say(language, 'about', notice=notice)}</sub>\n" if notice else ""
    return (
        f"{render_marker('duplicate', lang=language, duplicate=number)}\n"
        f"{say(language, 'duplicate.named', author=author, number=number)} "
        f"{say(language, 'duplicate.close', number=number, **periods)}\n{about}"
    )


def render_let_in(author: str, language: str, at: datetime) -> str:
    """The note that a reporter's /assistant again ended the mute of the box of the
    form on an issue without an analysis. Its marker records the request and the run,
    as the marker of the analysis does for any other new analysis."""
    mark = render_marker(LET_IN, again=1, runs=int(at.timestamp()))
    return f"{mark}\n{say(language, 'let-in', author=author)}\n"


def render_reopened(author: str, language: str) -> str:
    return f"{render_marker('reopened')}\n{say(language, 'reopened', author=author)}\n"


def render_fixed(pull: int, branch: str, language: str) -> str:
    return (
        f"{render_marker('fixed', pull=pull)}\n"
        f"{say(language, 'fixed', pull=pull, branch=branch)}\n"
    )


def render_released(
    author: str, release: dict[str, Any], pulls: list[int], language: str
) -> str:
    joined = ", ".join(f"#{pull}" for pull in pulls)
    fixes = say(language, "released.fixes", pulls=joined) if pulls else ""
    text_ = say(
        language,
        "released",
        tag=release["tag_name"],
        url=release["html_url"],
        fixes=fixes,
        author=author,
    )
    tag = release["tag_name"]
    attributes = {"tag": tag} if re.fullmatch(r"[\w.+/-]+", tag) else {}
    return f"{render_marker('released', **attributes)}\n{text_}\n"


def render_reopened_release(author: str, language: str) -> str:
    return (
        f"{render_marker('reopened')}\n"
        f"{say(language, 'reopened-release', author=author)}\n"
    )


def render_locked(language: str, lifecycle: Lifecycle) -> str:
    """The one line on an issue that the sweep locks: where to go instead."""
    periods = lifecycle.text(language)
    return f"{render_marker('locked')}\n{say(language, 'locked', **periods)}\n"
