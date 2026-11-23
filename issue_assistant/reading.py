"""How the texts of a command reach the AI: the experimental switch `reading` (#521).

`cleaned`, the default, reads each text from its source, cleaned as `shown` and
`as_shown` clean it, as before, and asks GitHub for no rendering. `rendered` is way D
of #488: after the sources, it reads GitHub's rendering of each text by its number
(`GitHub.renderings`), and the readers take the converter's Markdown of it
(`markdown_of`); a text without a rendering is read from its cleaned source. Nothing
else reaches the AI. The discussion assistant stays on `cleaned` in this step.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from .converter import markdown_of
from .github import GitHub
from .issues import collapsed
from .renderings import (
    ANSWER,
    COMMENT,
    CONTEXT_RENDERING_LIMITS,
    DISCUSSION,
    ISSUE,
    Limits,
    RenderingKey,
    Renderings,
    Text,
    comment_id,
)
from .values import login

# The values of the switch, the first the default, and the variable of the
# environment through which the action hands it to the command line.
READING_CLEANED = "cleaned"
READING_RENDERED = "rendered"
READINGS = (READING_CLEANED, READING_RENDERED)
READING_VARIABLE = "READING"

# The address of a picture uploaded to GitHub, as its source holds it.
UPLOADED = "https://github.com/user-attachments/assets/"
# The same picture in a rendering: a signed address of private-user-images, valid for
# minutes and new on every read, whose path holds the uuid of the source's address,
# as /<user id>/<number>-<uuid>.<kind>. Every quantifier has a bound.
SIGNED_PICTURE = re.compile(
    r"https://private-user-images\.githubusercontent\.com/[0-9]{1,20}/[0-9]{1,20}-"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"\.[A-Za-z0-9]{1,16}(?:\?[^\"'\s<>]{0,4096})?"
)


def canonical_pictures(html: str) -> str:
    """The rendering with each signed address of an uploaded picture written back as
    the address of the source, found by its uuid: the converter keeps an address only
    where the source holds it, and no signed address reaches the AI or
    attachments.md."""
    if "private-user-images" not in html:
        return html
    return SIGNED_PICTURE.sub(lambda found: UPLOADED + found[1], html)


def body_of(item: dict[str, Any]) -> str:
    """The text of an item of the first step, whole, as the answer of a rendering
    holds it beside its HTML."""
    return str(item.get("body") or "")


def issue_texts(
    repository: str,
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
    reporter_only: bool = False,
) -> list[Text]:
    """The issue and its comments, in their order, as texts of renderings: a
    comment that GitHub shows collapsed is never asked for, nor, with
    `reporter_only`, one that its reporter didn't write."""
    number = issue["number"]
    reporter = login(issue.get("user"))
    return [Text(repository, ISSUE, number, body_of(issue))] + [
        Text(
            repository,
            COMMENT,
            number,
            body_of(comment),
            comment_id(comment),
            collapsed(comment)
            or (reporter_only and login(comment.get("user")) != reporter),
        )
        for comment in comments
    ]


def other_texts(repository: str, items: list[dict[str, Any]]) -> list[Text]:
    """Other issues, those of issues.jsonl or of a related repository."""
    return [Text(repository, ISSUE, item["number"], body_of(item)) for item in items]


def discussion_texts(repository: str, items: list[dict[str, Any]]) -> list[Text]:
    """The other discussions, each with its marked answer, unless GitHub shows that
    collapsed."""
    texts = []
    for item in items:
        texts.append(Text(repository, DISCUSSION, item["number"], body_of(item)))
        answer = item.get("answer") or {}
        if answer and not answer.get("isMinimized"):
            texts.append(Text(repository, ANSWER, item["number"], body_of(answer)))
    return texts


@dataclass
class TextReader:
    """The texts of one command as its readers take them: on `cleaned` nothing, so
    that each reader reads the cleaned source as before; on `rendered` the
    converter's Markdown of each text that GitHub rendered, read within `limits`,
    which each read uses up for the next."""

    repository: str = ""
    reading: str = READING_CLEANED
    limits: Limits = CONTEXT_RENDERING_LIMITS
    renderings: Renderings | None = None
    converted: dict[tuple[RenderingKey, bool], str] = field(default_factory=dict)

    def read(self, github: GitHub, texts: list[Text]) -> None:
        """Ask GitHub for the renderings of the texts; nothing on `cleaned`."""
        if self.reading != READING_RENDERED:
            return
        began = time.monotonic()
        found = github.renderings(texts, self.limits)
        self.limits = self.limits.after(found, time.monotonic() - began)
        if self.renderings is None:
            self.renderings = found
        else:
            self.renderings.add(found)

    def notice(self) -> None:
        """The notice of the renderings of the command, with counts alone."""
        if self.renderings is not None:
            self.renderings.notice()

    def markdown(
        self, key: RenderingKey, source: str, notes: bool = True
    ) -> str | None:
        """The converter's Markdown of the rendering of a text, once for each key; None
        when it is read from its cleaned source. With `notes` False, as `as_shown`
        reads, each note is a space."""
        html = self.renderings.html.get(key) if self.renderings else None
        if html is None:
            return None
        if (key, notes) not in self.converted:
            converted = markdown_of(canonical_pictures(html), source, notes)
            self.converted[(key, notes)] = converted.text
        return self.converted[(key, notes)]

    def issue(
        self, item: dict[str, Any], repository: str = "", notes: bool = True
    ) -> str | None:
        """An issue of this repository, or of the related `repository`."""
        key = (repository or self.repository, ISSUE, item["number"], "")
        return self.markdown(key, body_of(item), notes)

    def comment(self, number: int, item: dict[str, Any]) -> str | None:
        """A comment of the issue `number`."""
        key = (self.repository, COMMENT, number, comment_id(item))
        return self.markdown(key, body_of(item))

    def discussion(self, item: dict[str, Any]) -> str | None:
        return self.markdown(
            (self.repository, DISCUSSION, item["number"], ""), body_of(item)
        )

    def answer(self, item: dict[str, Any]) -> str | None:
        """The marked answer of the discussion `item`."""
        return self.markdown(
            (self.repository, ANSWER, item["number"], ""),
            body_of(item.get("answer") or {}),
        )


# The reader of every command that reads no rendering.
CLEANED_READER = TextReader()
