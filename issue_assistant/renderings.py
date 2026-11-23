"""GitHub's renderings of texts, read by their numbers in a second step.

Way D of #488 has the AI read each text as GitHub renders it. A page or a list of
GitHub can't carry the renderings: one text that GitHub can't render in time makes
the whole page fail, and a page can't leave it out (#521). So the sources are read
as before, and `read_renderings` reads the HTML of each text afterwards by its
number: `issue(number:)`, windows of the comments of an issue, and
`discussion(number:)`, in requests that `rendering_estimate` packs. A request that
fails is split in halves, and a text without a rendering is read from its cleaned
source, with a reason that the notice counts. The limits of a command bound the
time, the requests and the points that the renderings take. The readers of
`reading.py` take them when the switch `reading` says `rendered`.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections import Counter, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .errors import GitHubError
from .values import command

# The weights of the estimate, in bytes of HTML for each mark of the source. Every
# one is a proposal of #521, measured on planted texts, through `POST /markdown`
# or on recorded texts, as its comment says; a rendering is checked again when it
# arrives, since the estimate is no proven bound.
# Each byte of text: escapes, paragraphs and the `dir="auto"` of each block.
TEXT_BYTE = 8
# Each byte in a fence with a language, highlighted by spans (28.6 B a byte of
# dense `js`), and in one without.
HIGHLIGHTED_BYTE = 40
FENCED_BYTE = 12
# `[^`: 384 B for each footnote reference, 460 B for each distinct footnote.
FOOTNOTE_BYTES = 400
# `[!`: 718 B for each short alert.
ALERT_BYTES = 750
# An issue reference, and a word of 7 to 40 hex digits that GitHub may link as a
# commit: about 385 B for `#6` in a repository of 32 characters, whose full name
# each holds three times.
REFERENCE_BYTES = 400
REFERENCE_NAME_BYTES = 3
# A mention, a `$` of math, a task box, an address and a `:`, from the recordings
# of #488.
MENTION_BYTES = 250
DOLLAR_BYTES = 70
TASK_BYTES = 150
ADDRESS_BYTES = 300
COLON_BYTES = 25
# A Unicode emoji, and a shortcode such as `:warning:`: 57 B for the warning sign
# (U+26A0 U+FE0F) with its alias.
EMOJI_BYTES = 100
# An uploaded picture: 1,916 B for one of 130 B of source, with its signed addresses.
PICTURE_BYTES = 2000
# A permalink to lines of a file, which renders those lines.
PERMALINK_BYTES = 2000
PERMALINK_LINE_BYTES = 1000
# A table, about 128 B each, and each cell that GitHub pads to the width of the
# header: 10.1 B a cell, and 25.8 B when the delimiter row aligns a column.
TABLE_BYTES = 150
CELL_BYTES = 11
ALIGNED_CELL_BYTES = 26

# A text estimated above this is never asked for, and a rendering that arrives
# larger is not taken: at most about 95,000 padded cells, 0.3 to 1.3 s to render.
CAP = 1_000_000
# A request asks for at most this much estimate, these many padded cells, which
# GitHub renders as it reads them (358,800 took 4.5 to 4.8 s on their first read),
# and these many texts; a window of comments holds at most 100.
REQUEST_BYTES = 16_000_000
REQUEST_CELLS = 400_000
REQUEST_TEXTS = 100
# One call of gh: GitHub gives a request about 10 to 11 s.
CALL_SECONDS = 30.0
# The limits of a command: its time for renderings, its requests, its failed
# requests and its counted points. A request counts 1 point, and FAILED_POINTS when
# it failed or answered after more than SLOW_SECONDS, since the cost that GitHub
# reports leaves those out. No request goes once the lowest `remaining` that
# GitHub reported in the command is below RESERVE.
ATTACHMENTS_SECONDS = 60.0
CONTEXT_SECONDS = 180.0
EVALUATION_SECONDS = 180.0
SAMPLE_SECONDS = 10.0
REQUESTS = 60
FAILURES = 10
POINTS = 150
SAMPLE_POINTS = 2
FAILED_POINTS = 12
SLOW_SECONDS = 9.0
RESERVE = 300

# The kinds of texts, each read by the number of its issue or discussion.
ISSUE = "issue"
COMMENT = "comment"
DISCUSSION = "discussion"
ANSWER = "answer"

# Why a text is read from its cleaned source, in the order of the notice.
ESTIMATED_TOO_LARGE = "its estimate is above the cap"
ARRIVED_TOO_LARGE = "its rendering arrived above the cap"
FAILED = "its request failed"
NULL_ANSWER = "GitHub answered null for it"
CHANGED = "it changed between the two readings"
OUT_OF_TIME = "the time of the command ran out"
OTHER_LIMIT = "another limit of the command left it out"
REASONS = (
    ESTIMATED_TOO_LARGE,
    ARRIVED_TOO_LARGE,
    FAILED,
    NULL_ANSWER,
    CHANGED,
    OUT_OF_TIME,
    OTHER_LIMIT,
)
# The types of GraphQL's errors on the path of a null that leave only that text,
# or the texts of that repository, without a rendering.
NULL_TYPES = frozenset({"NOT_FOUND", "FORBIDDEN"})

# The documents of the renderings, the closed list: every request is built from
# them, and each reads its texts by their numbers.
RENDERINGS = (
    "query($owner: String!, $name: String!{variables}) {{ "
    "repository(owner: $owner, name: $name) {{ {fields} }} "
    "rateLimit {{ cost remaining }} }}"
)
ISSUE_FIELD = "{alias}: issue(number: {number}) {{ body bodyHTML }}"
COMMENTS_FIELD = "{alias}: issue(number: {number}) {{ {windows} }}"
WINDOW_FIELD = (
    "{alias}: comments(first: {first}{after}) "
    "{{ nodes {{ fullDatabaseId body bodyHTML }} }}"
)
DISCUSSION_FIELD = "{alias}: discussion(number: {number}) {{ body bodyHTML }}"
ANSWER_FIELD = "{alias}: discussion(number: {number}) {{ answer {{ body bodyHTML }} }}"
CURSORS = (
    "query($owner: String!, $name: String!, $number: Int!, $after: String) { "
    "repository(owner: $owner, name: $name) { issue(number: $number) { "
    "comments(first: 100, after: $after) { pageInfo { hasNextPage endCursor } "
    "edges { cursor node { fullDatabaseId } } } } } "
    "rateLimit { cost remaining } }"
)

LINE_END = re.compile(r"\r\n?|\n")
# The quote markers and the indentation of a list item before a row, with its
# marker; possessive, so that a row of blanks or markers is read once.
QUOTE_MARKERS = re.compile(r"(?:[ \t]*+>)*+[ \t]*+")
MARGIN = re.compile(r"(?:[ \t]*+(?:>|[-+*](?=[ \t])|\d{1,9}[.)](?=[ \t])))*+[ \t]*+")
FENCE_OPENING = re.compile(r"(`{3,}+|~{3,}+)(.*)")
DELIMITER_CELL = re.compile(r"[ \t]*+:?-++:?[ \t]*+")
REFERENCE_MARK = re.compile(r"#\d|\bGH-\d|/(?:issues|pull|discussions)/\d")
HEX_WORD = re.compile(r"\b[0-9a-fA-F]{7,40}\b")
MENTION_MARK = re.compile(r"@[A-Za-z0-9]")
TASK_MARK = re.compile(r"\[[ xX]\]")
EMOJI_MARK = re.compile("[‼⁉™ℹ↔-↪⌚-⏿Ⓜ▪-➿⤴⤵⬅-⭕〰〽㊗㊙\U0001f000-\U0001faff]")
SHORTCODE = re.compile(r":[a-z0-9_+-]{1,64}:")
PICTURE_MARK = re.compile(
    r"user-attachments/assets/|user-images\.githubusercontent\.com"
)
PERMALINK_MARK = re.compile(r"/blob/[^\s#]{1,1000}#L(\d{1,9})(?:C\d+)?(?:-L(\d{1,9}))?")


def size_of(text: str) -> int:
    return len(text.encode("utf-8", "surrogatepass"))


@dataclass(frozen=True)
class Estimate:
    """What a text may render to: its bytes, and the table cells that GitHub pads."""

    size: int
    cells: int


def rendering_estimate(body: str, repository: str) -> Estimate:
    """What the body may render to in the repository, in one scan of its rows.

    Each byte of text weighs TEXT_BYTE, or more in a fence, and each mark that GitHub
    renders larger than its source weighs what the comments of the weights say;
    nothing inside a fence is a mark. A table weighs TABLE_BYTES and each of its cells,
    columns × (rows + 1), weighs CELL_BYTES. No pattern here backtracks over more than a
    few characters, so the time grows with the length of the body.
    """
    rows = LINE_END.split(body)
    prose: list[str] = []
    outside: list[int] = []
    size = TEXT_BYTE * size_of(body)
    fence: tuple[str, int, int] | None = None
    for index, row in enumerate(rows):
        # Only a row with the character of a fence can open or close one.
        if fence is None and "`" not in row and "~" not in row:
            prose.append(row)
            outside.append(index)
            continue
        bare = row[MARGIN.match(row).end() :]  # type: ignore[union-attr]
        if fence is not None:
            if (
                bare.startswith(fence[0] * fence[1])
                and not bare.strip(fence[0]).strip()
            ):
                fence = None
            else:
                size += (fence[2] - TEXT_BYTE) * (size_of(row) + 1)
            continue
        opening = FENCE_OPENING.fullmatch(bare)
        if opening and not (opening[1][0] == "`" and "`" in opening[2]):
            weight = HIGHLIGHTED_BYTE if opening[2].strip() else FENCED_BYTE
            fence = (opening[1][0], len(opening[1]), weight)
            continue
        prose.append(row)
        outside.append(index)
    text = "\n".join(prose)
    size += marks_of(text, repository)
    tables, cells, aligned = tables_of(rows, outside)
    size += (
        TABLE_BYTES * tables
        + CELL_BYTES * (cells - aligned)
        + ALIGNED_CELL_BYTES * aligned
    )
    return Estimate(size, cells)


def marks_of(text: str, repository: str) -> int:
    """The weight of the marks of the text outside its fences."""
    reference = REFERENCE_BYTES + REFERENCE_NAME_BYTES * len(repository)
    permalinks = sum(
        PERMALINK_BYTES
        + PERMALINK_LINE_BYTES * (max(int(found[2] or found[1]) - int(found[1]), 0) + 1)
        for found in PERMALINK_MARK.finditer(text)
    )
    return (
        permalinks
        + FOOTNOTE_BYTES * text.count("[^")
        + ALERT_BYTES * text.count("[!")
        + reference * len(REFERENCE_MARK.findall(text))
        + reference * len(HEX_WORD.findall(text))
        + MENTION_BYTES * len(MENTION_MARK.findall(text))
        + DOLLAR_BYTES * text.count("$")
        + TASK_BYTES * len(TASK_MARK.findall(text))
        + ADDRESS_BYTES * (text.count("://") + text.count("www."))
        + COLON_BYTES * text.count(":")
        + EMOJI_BYTES * (len(EMOJI_MARK.findall(text)) + len(SHORTCODE.findall(text)))
        + PICTURE_BYTES * len(PICTURE_MARK.findall(text))
    )


def bare_rows(row: str) -> tuple[str, str]:
    """The row without its quote markers, and without the marks of a list as well."""
    quoted = row[QUOTE_MARKERS.match(row).end() :].rstrip()  # type: ignore[union-attr]
    listed = row[MARGIN.match(row).end() :].rstrip()  # type: ignore[union-attr]
    return quoted, listed


def cells_of(row: str) -> int:
    """The cells of a row of a table: an escaped `\\|` doesn't separate two."""
    pipes = row.count("|") - row.count("\\|")
    pipes -= row.startswith("|")
    pipes -= len(row) > 1 and row.endswith("|") and not row.endswith("\\|")
    return pipes + 1


def delimiter_of(row: str) -> tuple[int, bool] | None:
    """The columns of a delimiter row, and whether it aligns one; None for a row that
    is none: cells of `-`, each with an optional `:` at either end, between the `|`
    that the row holds."""
    parts = row.split("|")
    if row.startswith("|"):
        parts = parts[1:]
    if row.endswith("|"):
        parts = parts[:-1]
    if not parts or not all(DELIMITER_CELL.fullmatch(part) for part in parts):
        return None
    return len(parts), ":" in row


def tables_of(rows: list[str], outside: list[int]) -> tuple[int, int, int]:
    """The tables of the rows outside fences, their cells, and the cells of those
    whose delimiter row aligns a column. Quotes, alerts and lists hide none: the
    markers come off each row before it is read."""
    tables = cells = aligned = 0
    place = 1
    while place < len(outside):
        index = outside[place]
        heading = outside[place - 1]
        place += 1
        # A delimiter row holds a `|` and a `-`, right below the row of its header.
        if "|" not in rows[index] or "-" not in rows[index] or heading != index - 1:
            continue
        quoted, listed = bare_rows(rows[index])
        found = delimiter_of(quoted) or delimiter_of(listed)
        header = bare_rows(rows[heading])
        if found is None or not (header[0] or header[1]):
            continue
        columns = max(found[0], *(cells_of(row) for row in header if row))
        body = 0
        while place < len(outside) and outside[place] == index + 1 + body:
            if not any(bare_rows(rows[outside[place]])):
                break
            body += 1
            place += 1
        tables += 1
        cells += columns * (body + 1)
        aligned += columns * (body + 1) if found[1] else 0
    return tables, cells, aligned


def comment_id(comment: dict[str, Any]) -> str:
    """The REST id of a comment as a text's key holds it."""
    found = comment.get("id")
    return "" if found is None else str(found)


@dataclass(frozen=True)
class Text:
    """A text of the first step whose rendering is asked for.

    `number` is that of its issue or discussion, `comment` the REST id of a comment,
    and `hidden` marks a comment that GitHub shows collapsed, which is never asked
    for. The comments of an issue come together, all of them, in the order of the
    first step, so that a window knows where it starts.
    """

    repository: str
    kind: str
    number: int
    body: str
    comment: str = ""
    hidden: bool = False

    @property
    def key(self) -> tuple[str, str, int, str]:
        return (self.repository, self.kind, self.number, self.comment)


@dataclass(frozen=True)
class Limits:
    """What the renderings of one command may take; `lowest` is the lowest
    `remaining` that an earlier read of the same command saw."""

    seconds: float
    requests: int = REQUESTS
    failures: int = FAILURES
    points: int = POINTS
    lowest: int | None = None

    @classmethod
    def of_evaluation(cls, samples: int) -> Limits:
        """Evaluate's, which grow with its replayed samples."""
        return cls(
            EVALUATION_SECONDS + SAMPLE_SECONDS * samples,
            REQUESTS + samples,
            FAILURES,
            POINTS + SAMPLE_POINTS * samples,
        )

    def after(self, spent: Renderings, seconds: float) -> Limits:
        """What is left to the command after a read that took `spent` and `seconds`:
        nothing more of what it ran out of, so that its next read stops at once."""
        stopped = spent.stopped
        return Limits(
            0.0 if stopped == OUT_OF_TIME else max(self.seconds - seconds, 0.0),
            0 if stopped == OTHER_LIMIT else max(self.requests - spent.requests, 0),
            max(self.failures - spent.failed, 0),
            max(self.points - spent.points, 0),
            spent.lowest,
        )


ATTACHMENTS_RENDERING_LIMITS = Limits(ATTACHMENTS_SECONDS)
CONTEXT_RENDERING_LIMITS = Limits(CONTEXT_SECONDS)


@dataclass
class Renderings:
    """The HTML of each text that GitHub rendered, why each other text that was
    meant for a request has none, and the counts of the notice."""

    html: dict[tuple[str, str, int, str], str] = field(default_factory=dict)
    fallbacks: Counter[str] = field(default_factory=Counter)
    reasons: dict[tuple[str, str, int, str], str] = field(default_factory=dict)
    requests: int = 0
    failed: int = 0
    points: int = 0
    lowest: int | None = None
    seconds: float = 0.0
    # How many of `html` an earlier read of Evaluate held, so not read by this one.
    held: int = 0
    # Why the read stopped before its end, if it did: OUT_OF_TIME or OTHER_LIMIT.
    stopped: str | None = None

    def of(self, text: Text) -> str | None:
        """The rendering of the text; None when it is read from its cleaned source."""
        return self.html.get(text.key)

    def fall(self, key: tuple[str, str, int, str], reason: str) -> None:
        """The text of `key` is read from its cleaned source, for `reason`."""
        self.reasons[key] = reason
        self.fallbacks[reason] += 1

    def add(self, other: Renderings) -> None:
        """Take in a later read of the same command, for one notice of both."""
        self.html.update(other.html)
        self.fallbacks.update(other.fallbacks)
        self.reasons.update(other.reasons)
        self.requests += other.requests
        self.failed += other.failed
        self.points += other.points
        self.seconds += other.seconds
        self.held += other.held
        if other.lowest is not None:
            lowest = self.lowest
            self.lowest = other.lowest if lowest is None else min(lowest, other.lowest)
        self.stopped = other.stopped or self.stopped

    def notice(self) -> None:
        """A notice in the log with counts alone, no text: the renderings, the texts
        read from their cleaned source by reason, and what the requests took."""
        if not self.requests and not self.fallbacks:
            return
        reasons = "; ".join(
            f"{reason}: {self.fallbacks[reason]}"
            for reason in REASONS
            if self.fallbacks[reason]
        )
        lowest = "none reported" if self.lowest is None else self.lowest
        held = f", {self.held} held from an earlier replay" if self.held else ""
        command(
            "notice",
            f"Renderings of GitHub: {len(self.html) - self.held} read{held}; requests: {self.requests}, "
            f"failed: {self.failed}; counted points: {self.points}; seconds: "
            f"{self.seconds:.1f}; lowest remaining points: {lowest}. Texts read from "
            f"their cleaned source: {sum(self.fallbacks.values())}"
            + (f" ({reasons})." if reasons else "."),
        )


Send = Callable[[dict[str, Any], float], subprocess.CompletedProcess[str]]
Clock = Callable[[], float]
RenderingKey = tuple[str, str, int, str]


@dataclass
class Cursors:
    """The cursors of the comments of an issue, by their REST id, read on demand."""

    found: dict[str, str] = field(default_factory=dict)
    after: str | None = None
    more: bool = True
    failed: bool = False


@dataclass
class Window:
    """Comments of one issue that follow each other, from the place `start` on, and
    the cursor of the comment before it when it doesn't start the comments."""

    repository: str
    number: int
    start: int
    texts: list[Text]
    after: str = ""


def windows_of(
    texts: list[Text], positions: dict[tuple[str, str], int]
) -> list[Window]:
    """The windows of the comments among the texts of a request, in their order."""
    windows: list[Window] = []
    for text in texts:
        if text.kind != COMMENT:
            continue
        position = positions[(text.repository, text.comment)]
        last = windows[-1] if windows else None
        if (
            last is not None
            and last.number == text.number
            and last.start + len(last.texts) == position
        ):
            last.texts.append(text)
        else:
            windows.append(Window(text.repository, text.number, position, [text]))
    return windows


@dataclass
class Asked:
    """One request: its document and variables, and the texts that each alias holds."""

    document: str
    variables: dict[str, Any]
    single: dict[str, Text]
    windows: dict[str, tuple[str, list[Text]]]


def asked_of(texts: list[Text], windows: list[Window]) -> Asked:
    """The request of the texts, all of one repository, built from the documents
    above: an alias for each issue, discussion and answer by its number, and for
    each window of the comments of an issue by that issue's number."""
    owner, name = texts[0].repository.split("/")
    fields: list[str] = []
    variables: dict[str, Any] = {"owner": owner, "name": name}
    declared = ""
    single: dict[str, Text] = {}
    shapes = {ISSUE: ISSUE_FIELD, DISCUSSION: DISCUSSION_FIELD, ANSWER: ANSWER_FIELD}
    for text in texts:
        if text.kind in shapes:
            alias = f"{text.kind[0]}{text.number}"
            single[alias] = text
            fields.append(shapes[text.kind].format(alias=alias, number=text.number))
    by_issue: dict[int, list[str]] = {}
    held: dict[str, tuple[str, list[Text]]] = {}
    for index, window in enumerate(windows):
        alias, after = f"w{index}", ""
        if window.start:
            variables[alias] = window.after
            declared += f", ${alias}: String!"
            after = f", after: ${alias}"
        by_issue.setdefault(window.number, []).append(
            WINDOW_FIELD.format(alias=alias, first=len(window.texts), after=after)
        )
        held[alias] = (f"c{window.number}", window.texts)
    for number, parts in by_issue.items():
        fields.append(
            COMMENTS_FIELD.format(
                alias=f"c{number}", number=number, windows=" ".join(parts)
            )
        )
    document = RENDERINGS.format(variables=declared, fields=" ".join(fields))
    return Asked(document, variables, single, held)


def refused(body: dict[str, Any], path: list[str]) -> bool:
    """Whether GitHub answered null on the path because the thing isn't there or the
    token may not read it: an error of NULL_TYPES on that very path."""
    return any(
        isinstance(error, dict)
        and error.get("type") in NULL_TYPES
        and error.get("path") == path
        for error in body.get("errors") or []
    )


def rendered(entry: Any) -> tuple[str, str] | None:
    """The source and the HTML of an entry of an answer, if it holds both."""
    if isinstance(entry, dict):
        body, html = entry.get("body"), entry.get("bodyHTML")
        if isinstance(body, str) and isinstance(html, str):
            return body, html
    return None


@dataclass
class Taken:
    """What an answer gave: the HTML of each text, and why each other has none."""

    html: dict[RenderingKey, str] = field(default_factory=dict)
    fallen: dict[RenderingKey, str] = field(default_factory=dict)

    def keep(self, text: Text, body: str, html: str) -> None:
        """Keep the HTML, unless the text was edited between the two steps or its
        rendering is larger than the cap."""
        if body != text.body:
            self.fallen[text.key] = CHANGED
        elif size_of(html) > CAP:
            self.fallen[text.key] = ARRIVED_TOO_LARGE
        else:
            self.html[text.key] = html

    def single(
        self, body: dict[str, Any], repository: dict[str, Any], asked: Asked
    ) -> bool:
        """Take each issue, discussion and answer by its alias; False when an entry
        is missing or has another shape, which fails the request."""
        for alias, text in asked.single.items():
            entry = repository.get(alias, False)
            if entry is None and refused(body, ["repository", alias]):
                self.fallen[text.key] = NULL_ANSWER
                continue
            if text.kind == ANSWER and isinstance(entry, dict) and "answer" in entry:
                entry = entry["answer"]
                if entry is None:
                    # The discussion has no answer any more.
                    self.fallen[text.key] = CHANGED
                    continue
            pair = rendered(entry)
            if pair is None:
                return False
            self.keep(text, *pair)
        return True

    def windows(
        self, body: dict[str, Any], repository: dict[str, Any], asked: Asked
    ) -> bool:
        """Take each comment of a window by its fullDatabaseId, which must be the
        REST id of the first step; False when a window is missing or has another
        shape, which fails the request."""
        for alias, (issue, texts) in asked.windows.items():
            entry = repository.get(issue, False)
            if entry is None and refused(body, ["repository", issue]):
                self.fallen.update(dict.fromkeys(keys_of(texts), NULL_ANSWER))
                continue
            window = entry.get(alias) if isinstance(entry, dict) else None
            nodes = window.get("nodes") if isinstance(window, dict) else None
            if not isinstance(nodes, list):
                return False
            by_id: dict[str, tuple[str, str]] = {}
            for node in nodes:
                pair = rendered(node)
                if pair is None or node.get("fullDatabaseId") is None:
                    return False
                by_id[str(node["fullDatabaseId"])] = pair
            for text in texts:
                pair = by_id.get(text.comment)
                if pair is None:
                    self.fallen[text.key] = CHANGED
                else:
                    self.keep(text, *pair)
        return True


def keys_of(texts: list[Text]) -> list[RenderingKey]:
    return [text.key for text in texts]


def taken_of(
    body: dict[str, Any] | None, asked: Asked, texts: list[Text]
) -> Taken | None:
    """What the answer gives for the request of the texts; None when it failed: no
    answer, no `data`, or no entry of the right shape for an alias asked for."""
    if body is None:
        return None
    taken = Taken()
    repository = body["data"].get("repository")
    if repository is None and refused(body, ["repository"]):
        taken.fallen = dict.fromkeys(keys_of(texts), NULL_ANSWER)
        return taken
    if not isinstance(repository, dict):
        return None
    if taken.single(body, repository, asked) and taken.windows(body, repository, asked):
        return taken
    return None


def rate_limited(error: GitHubError) -> bool:
    """Whether GitHub refused the call for a rate limit, secondary or primary."""
    return (
        error.rate_limited()
        or "api rate limit exceeded" in str(error).casefold()
        or "RATE_LIMITED" in error.types
    )


Page = tuple[list[tuple[str, str]], str | None, bool]


def page_of(body: dict[str, Any] | None) -> Page | None:
    """The REST ids and cursors of a page of the comments of an issue, its end and
    whether more follow; None for an answer without them."""
    data = body["data"] if body is not None else None
    repository = data.get("repository") if isinstance(data, dict) else None
    issue = repository.get("issue") if isinstance(repository, dict) else None
    comments = issue.get("comments") if isinstance(issue, dict) else None
    info = comments.get("pageInfo") if isinstance(comments, dict) else None
    edges = comments.get("edges") if isinstance(comments, dict) else None
    if not isinstance(info, dict) or not isinstance(edges, list):
        return None
    more, end = info.get("hasNextPage"), info.get("endCursor")
    if not isinstance(more, bool) or (more and not isinstance(end, str)):
        return None
    pairs: list[tuple[str, str]] = []
    for edge in edges:
        node = edge.get("node") if isinstance(edge, dict) else None
        cursor = edge.get("cursor") if isinstance(edge, dict) else None
        found = node.get("fullDatabaseId") if isinstance(node, dict) else None
        if found is None or not isinstance(cursor, str):
            return None
        pairs.append((str(found), cursor))
    return pairs, end if isinstance(end, str) else None, more


class Reading:
    """The renderings of the texts of one command, one request at a time."""

    def __init__(self, limits: Limits, send: Send, clock: Clock) -> None:
        self.limits = limits
        self.send = send
        self.clock = clock
        self.start = clock()
        self.result = Renderings(lowest=limits.lowest)
        self.stop: str | None = None
        # The place of each comment among those of its issue, and the REST id of
        # the comment at each place.
        self.positions: dict[tuple[str, str], int] = {}
        self.ids: dict[tuple[str, int], list[str]] = {}
        self.cursors: dict[tuple[str, int], Cursors] = {}

    def fall(self, texts: list[Text], reason: str) -> None:
        for text in texts:
            self.result.fall(text.key, reason)

    def planned(self, texts: list[Text]) -> list[list[Text]]:
        """The requests of the texts, in their order: each of one repository and
        within the budget of a request, without the texts that aren't asked for."""
        requests: list[list[Text]] = []
        current: list[Text] = []
        size = cells = 0
        seen: set[RenderingKey] = set()
        for text in texts:
            if text.key in seen:
                continue
            seen.add(text.key)
            if text.kind == COMMENT:
                ids = self.ids.setdefault((text.repository, text.number), [])
                self.positions[(text.repository, text.comment)] = len(ids)
                ids.append(text.comment)
            if text.hidden or not text.body.strip():
                continue
            estimate = rendering_estimate(text.body, text.repository)
            if estimate.size > CAP:
                self.fall([text], ESTIMATED_TOO_LARGE)
                continue
            if current and (
                text.repository != current[0].repository
                or size + estimate.size > REQUEST_BYTES
                or cells + estimate.cells > REQUEST_CELLS
                or len(current) == REQUEST_TEXTS
            ):
                requests.append(current)
                current, size, cells = [], 0, 0
            current.append(text)
            size += estimate.size
            cells += estimate.cells
        if current:
            requests.append(current)
        return requests

    def stopped(self) -> str | None:
        """Why no request goes any more, once a limit of the command is reached."""
        result, limits = self.result, self.limits
        if self.stop is None and self.clock() - self.start >= limits.seconds:
            self.stop = OUT_OF_TIME
        if self.stop is None and (
            result.requests >= limits.requests
            or result.failed >= limits.failures
            or result.points + FAILED_POINTS > limits.points
            or (result.lowest is not None and result.lowest < RESERVE)
        ):
            self.stop = OTHER_LIMIT
        return self.stop

    def call(
        self, request: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, float | None]:
        """GitHub's answer to the request, sent once, and the seconds it took; None
        for no answer: gh failed or ran out of time, or the answer isn't JSON with
        `data`. A rate limit, or the end of the time of the command, ends the
        renderings of the command; the seconds are None for a request that the end
        of the time kept from going."""
        left = self.limits.seconds - (self.clock() - self.start)
        if left <= 0:
            self.stop = OUT_OF_TIME
            return None, None
        timeout = min(CALL_SECONDS, left)
        began = self.clock()
        try:
            result: subprocess.CompletedProcess[str] | None = self.send(
                request, timeout
            )
        except subprocess.TimeoutExpired:
            result = None
        elapsed = self.clock() - began
        if result is None:
            if timeout < CALL_SECONDS:
                self.stop = OUT_OF_TIME
            return None, elapsed
        try:
            body = json.loads(result.stdout) if result.stdout.strip() else None
        except ValueError:
            body = None
        if result.returncode or (isinstance(body, dict) and body.get("errors")):
            error = GitHubError.of(["api", "graphql"], result.stdout, result.stderr)
            if rate_limited(error):
                self.stop = OTHER_LIMIT
                return None, elapsed
        if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
            return None, elapsed
        rate = body["data"].get("rateLimit")
        remaining = rate.get("remaining") if isinstance(rate, dict) else None
        if isinstance(remaining, int) and not isinstance(remaining, bool):
            lowest = self.result.lowest
            self.result.lowest = remaining if lowest is None else min(lowest, remaining)
        return body, elapsed

    def counted(self, elapsed: float | None, answered: bool) -> None:
        """Count a request: its seconds, and its points, those of a failure when it
        failed or took longer than SLOW_SECONDS; not one that didn't go."""
        if elapsed is None:
            return
        result = self.result
        result.requests += 1
        result.seconds += elapsed
        result.failed += not answered
        result.points += 1 if answered and elapsed <= SLOW_SECONDS else FAILED_POINTS

    def cursor_of(self, repository: str, number: int, comment: str) -> str | None:
        """The cursor of the comment, read with the cursors of its issue when they
        aren't known yet; None when the read failed, stopped or didn't find it."""
        state = self.cursors.setdefault((repository, number), Cursors())
        owner, name = repository.split("/")
        while comment not in state.found and state.more and not state.failed:
            if self.stopped():
                return None
            variables = {
                "owner": owner,
                "name": name,
                "number": number,
                "after": state.after,
            }
            body, elapsed = self.call({"query": CURSORS, "variables": variables})
            page = page_of(body)
            self.counted(elapsed, page is not None)
            if page is None:
                state.failed = True
            else:
                state.found.update(page[0])
                state.after, state.more = page[1], page[2]
        return state.found.get(comment)

    def placed(self, request: list[Text]) -> list[Text]:
        """The texts of the request whose windows can start where they start: a
        window after the first comment needs the cursor of the one before it. The
        others are read from their cleaned source."""
        dropped: set[RenderingKey] = set()
        for window in windows_of(request, self.positions):
            if not window.start:
                continue
            before = self.ids[(window.repository, window.number)][window.start - 1]
            if self.cursor_of(window.repository, window.number, before) is not None:
                continue
            if self.stopped():
                # The run counts the rest under the limit; what fell back is counted.
                break
            state = self.cursors[(window.repository, window.number)]
            self.fall(window.texts, FAILED if state.failed else CHANGED)
            dropped.update(text.key for text in window.texts)
        return [text for text in request if text.key not in dropped]

    def asked(self, request: list[Text]) -> Taken | None:
        """What GitHub answered to the request; None when the request failed."""
        windows = windows_of(request, self.positions)
        for window in windows:
            if window.start:
                where = (window.repository, window.number)
                before = self.ids[where][window.start - 1]
                window.after = self.cursors[where].found[before]
        asked = asked_of(request, windows)
        body, elapsed = self.call(
            {"query": asked.document, "variables": asked.variables}
        )
        taken = taken_of(body, asked, request)
        self.counted(elapsed, taken is not None)
        return taken

    def run(self, texts: list[Text]) -> Renderings:
        """Ask for the renderings of the texts, request by request in their order. A
        request that fails is split in halves in the order of its texts, each sent
        once, and a text that fails alone is read from its cleaned source."""
        queue = deque(self.planned(texts))
        while queue:
            request = queue.popleft()
            if not self.stopped():
                request = self.placed(request)
            if reason := self.stopped():
                self.fall(request, reason)
                continue
            if not request:
                continue
            taken = self.asked(request)
            if taken is not None:
                self.result.html.update(taken.html)
                for key, reason in taken.fallen.items():
                    self.result.fall(key, reason)
            elif self.stop is not None:
                self.fall(request, self.stop)
            elif len(request) == 1:
                self.fall(request, FAILED)
            else:
                half = (len(request) + 1) // 2
                queue.extendleft([request[half:], request[:half]])
        self.result.stopped = self.stop
        return self.result


def read_renderings(
    texts: Iterable[Text], limits: Limits, send: Send, clock: Clock = time.monotonic
) -> Renderings:
    """GitHub's HTML of each text, read by its number in requests that `send` sends,
    within the limits of the command; see Reading."""
    return Reading(limits, send, clock).run(list(texts))
