"""The structures of Markdown whose text GitHub doesn't show as it is written: the
cells of a row of a table beyond the cells of its header, which GitHub drops; the
source of a diagram, a map or a 3D model, which it draws in place of the source;
and math, whose TeX it hands to a renderer that can draw some of it as nothing.

Each is found in the text as it was written, as GitHub's renderer finds it
(`gh api -X POST markdown` with `mode: gfm` and a repository as `context`,
2026-11-21), in time linear in the length of the text. Where that is hard to tell,
as in an HTML block in a quote, a structure counts as one: the cleaning takes out
what either the HTML or a structure takes out, so that one structure too many only
takes out text that GitHub may show.
"""

from __future__ import annotations

import functools
import html
import re
from bisect import bisect_right
from dataclasses import dataclass, field

from .markup import (
    BLOCK_TAGS,
    COMMENT_START,
    CONTAINER,
    FENCE_ROW,
    HTML_ENDS,
    ROW_END,
    ROW_START,
    Fences,
    HtmlRows,
    caseless,
)
from .tags import BACKTICKS

# The notes that take the place of what goes, so that the engine knows that
# something was there.
TABLE_NOTE = "(cells beyond the header of the table removed)"
DRAWING_NOTE = (
    "(the source of a drawing removed: GitHub shows a diagram, a map or a 3D model "
    "in its place)"
)
DIAGRAM_NOTE = "(a part of the diagram that GitHub doesn't draw removed)"
MATH_NOTE = "(math removed)"

# The languages of code that GitHub draws, by the first word of the info string of
# a fence or of the `lang` of a `<pre>`, in small letters: `Mermaid` is code.
DRAWN = frozenset({"mermaid", "geojson", "topojson", "stl"})

# Where a row ends, also with `endpos`.
END = r"(?=[\r\n]|\Z)"
# The marks of the quotes at the start of a row, each with the blanks before it; a
# mark of a list item; and the text of a row after the marks of its quotes and its
# list items, and the blanks after them.
QUOTE_RUN = re.compile(r"(?:[ \t]*+>)*+")
ITEM_MARK = re.compile(rf"(?:[-+*]|\d{{1,9}}[.)])(?=[ \t]|{END})")
BLANKS = re.compile(r"[ \t]*+")
ROW_CONTENT = rf"{QUOTE_RUN.pattern}(?:[ \t]*+{ITEM_MARK.pattern})*+{BLANKS.pattern}"
# The row below the header of a table, after the marks of its quotes and list
# items: cells of hyphens, each with a colon at either end, between pipes. Only
# with a pipe or a colon is it one, and not the line under a heading.
DELIMITER_ROW = re.compile(
    rf"{ROW_START}(?P<marks>(?:[ \t]*+(?:>|(?:[-+*]|\d{{1,9}}[.)])(?=[ \t])))*+[ \t]*+)"
    r"(?=[^|:\r\n]*+[|:])"
    r"\|?+[ \t]*+:?+-++:?+[ \t]*+(?:\|[ \t]*+:?+-++:?+[ \t]*+)*+\|?+[ \t]*+" + END
)
# A hyphen next to a pipe or a colon, without which no row is a delimiter row: two
# patterns, which each find faster than one of both.
TABLE_HINTS = (re.compile(r"-[ \t]*+[|:]"), re.compile(r"[|:][ \t]*+-"))
# A row that ends a table, from its first character after the marks of its quotes
# and its blanks: a blank one, a heading, a list item, a quote, a fence, a rule, and
# an HTML block of a kind that can interrupt a paragraph. A tag alone on its row,
# CommonMark's seventh kind, is a row of the table: `<span>x</span>` was a cell.
INTERRUPTS = re.compile(
    rf"{END}|#{{1,6}}(?:[ \t]|{END})|(?:[-+*]|\d{{1,9}}[.)])(?:[ \t]|{END})|>|`{{3}}"
    rf"|~{{3}}|(?:\*[ \t]*+){{3,}}{END}|(?:-[ \t]*+){{3,}}{END}|(?:_[ \t]*+){{3,}}{END}"
    rf"|<(?:{caseless('pre|script|style|textarea')})(?=[\s>]|\Z)"
    r"|<!--|<\?|<![A-Za-z]|<!\[CDATA\["
    rf"|</?(?:{caseless(BLOCK_TAGS)})(?=[ \t>]|/>|{END})"
)
# A row of a table after the marks of its quotes: its blanks, and what ends the table.
BODY_ROW = re.compile(rf"(?P<blanks>[ \t]*+)(?P<interrupts>{INTERRUPTS.pattern})?+")
# An HTML block that starts a row outside quotes and list items, at most three
# spaces in, by its kind; and what ends each kind but that of a block tag, which a
# blank row ends.
HTML_START = re.compile(
    rf"{ROW_START} {{0,3}}(?:(?P<raw><(?:{caseless('pre|script|style|textarea')})"
    r"(?=[\s>]|\Z))|(?P<comment><!--)|(?P<processing><\?)|(?P<cdata><!\[CDATA\[)"
    r"|(?P<declaration><![A-Za-z])"
    rf"|(?P<tag></?(?:{caseless(BLOCK_TAGS)})(?=[ \t>]|/>|{END})))"
)
EMPTY_ROW = re.compile(rf"{ROW_START}[ \t]*+{END}")
# Display math: from a row whose text starts with `$$` and holds no other, over the
# rows of its paragraph, which no blank row ends, to the next `$$`.
DISPLAY = re.compile(
    rf"{ROW_CONTENT}(?P<open>\$\$)(?:(?!\$\$)[^\r\n])*+"
    rf"(?:(?:\r\n?|\n)(?![ \t>]*+{END})(?:(?!\$\$)[^\r\n])*+)++(?P<close>\$\$)"
)
HTML_END = {
    **{kind: HTML_ENDS[kind] for kind in ("raw", "processing", "cdata")},
    "comment": re.compile("-->"),
    "declaration": re.compile(">"),
}
# The first row that starts a list item or a footnote: above it, a row indented by
# four columns is code.
LISTED = re.compile(
    r"^(?:[ \t]*+>)*+[ \t]*+(?:(?:[-+*]|\d{1,9}[.)])(?:[ \t]|$)|\[\^[^\]\r\n]++\]:)",
    re.MULTILINE,
)

Span = tuple[int, int]


@dataclass
class Found:
    """What GitHub doesn't show of a text as it is written, as spans of it: the cells
    beyond the header of a table; the source of each drawn block, and of those that
    the engine may read, the parts that the drawing leaves out; the math whose
    source could hide text, and the plain math."""

    cells: list[Span] = field(default_factory=list)
    drawn: list[Span] = field(default_factory=list)
    readable: set[Span] = field(default_factory=set)
    diagram: list[Span] = field(default_factory=list)
    math: list[Span] = field(default_factory=list)
    plain: list[Span] = field(default_factory=list)
    # Whether the text holds a dollar sign, as an entity too, of which math could
    # also form where its HTML leaves the text.
    dollars: bool = False

    def hidden(self) -> list[tuple[int, int, str]]:
        """The spans of which GitHub shows nothing as it is written, each with the
        note for its place, in order and apart."""
        spans = [(start, end, TABLE_NOTE) for start, end in self.cells]
        spans += [
            (start, end, DRAWING_NOTE)
            for start, end in self.drawn
            if (start, end) not in self.readable
        ]
        spans += [(start, end, DIAGRAM_NOTE) for start, end in self.diagram]
        spans += [(start, end, MATH_NOTE) for start, end in self.math]
        return apart(spans)

    def unlinked(self) -> list[tuple[int, int, str]]:
        """The spans in which GitHub shows no link: those that it hides, and all of
        a drawing or of math, whose text is drawn and leads nowhere."""
        spans = self.cells + self.drawn + self.math + self.plain
        return apart([(start, end, "") for start, end in spans])


def apart(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """The spans in order, each one that overlaps the one before joined to it, with
    the note of the first."""
    joined: list[tuple[int, int, str]] = []
    for start, end, note in sorted(spans):
        if joined and start < joined[-1][1]:
            first, last, kept = joined[-1]
            joined[-1] = (first, max(last, end), kept)
        else:
            joined.append((start, end, note))
    return joined


def structures_of(text: str) -> Found:
    """The tables, drawn blocks and math of a text, as GitHub reads them."""
    # The searches for a text are fast, a pattern reads every position: a fence or
    # a `<pre>` that GitHub draws, or whose content is math, names its language in
    # letters or entities.
    found = Found(dollars="$" in text or "&#" in text or "&dollar;" in text)
    fences = "```" in text or "~~~" in text
    pres = "<" in text and "lang" in text.lower()
    if fences or pres:
        named = "&" in text or any(word in text for word in ("math", *DRAWN))
        fences, pres = fences and named, pres and named
    tables = (
        "-" in text
        and ("|" in text or ":" in text)
        and any(hint.search(text) for hint in TABLE_HINTS)
    )
    dollars = found.dollars
    if not (fences or tables or pres or dollars):
        return found
    reader = Reader(text)
    if fences:
        reader.fenced(found)
    if pres:
        drawn_pres(text, reader, found)
    if tables:
        reader.tables(found)
    if dollars:
        reader.math(found)
    return found


class Reader:
    """The rows of a text, and whether fenced code surely holds each."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.rows = row_spans(text)
        self.starts = [start for start, _ in self.rows]
        self.fences = Fences(text, HtmlRows(text))
        self.held: list[bool] | None = None
        listed = LISTED.search(text)
        self.listed = listed.start() if listed else len(text)

    def code(self) -> list[bool]:
        """Whether fenced code surely holds each row, read once."""
        if self.held is None:
            # Without three backticks or tildes in a row, no row is fenced code.
            self.held = (
                [False] * len(self.starts)
                if self.fences.done
                else [self.fences.holds(start) for start in self.starts]
            )
        return self.held

    def fenced(self, found: Found) -> None:
        """The fences that GitHub draws, or whose content is math: those whose info
        string starts with a word of DRAWN, or with `math`, outside other code."""
        text, covered = self.text, 0
        for row in FENCE_ROW.finditer(text):
            if row.start() < covered or (row["fence"][0] == "`" and "`" in row["info"]):
                continue
            # Indented by four columns from its quotes, where no list item can hold
            # it, a fence is code; and GitHub shows the source of one on the row of
            # the label of a footnote.
            marks = row["marks"]
            if "[^" in marks or (
                row.start() < self.listed and width(row["indent"]) >= 4 + bool(marks)
            ):
                continue
            word = info_word(row["info"])
            math = word.startswith("math")
            if word not in DRAWN and not math:
                continue
            body = self.body_of(row)
            if body is None:
                continue
            covered = body[1]
            if math:
                (found.plain if plain_math(text, *body) else found.math).append(body)
                continue
            found.drawn.append(body)
            if word == "mermaid":
                diagram(text, body, found)

    def body_of(self, row: re.Match[str]) -> Span | None:
        """The rows that the fence of a row holds, without the fence that closes
        them, or None where the fence is in fenced code of another or holds none.
        Where it is hard to tell whether it opens fenced code, as in a footnote or
        after a tab, it does, up to the next row of a fence that could close it,
        with any marks and blanks before it, or to the end of the text."""
        block = self.fences.block(row.start())
        if block is not None and block[0] < row.start():
            return None
        if block is not None and block[2]:
            end = block[1]
        else:
            end = closing_fence(self.text, row)
        index = bisect_right(self.starts, row.start())
        last = bisect_right(self.starts, end - 1) - 1
        if last < index:
            return None
        fence = row["fence"]
        closing = self.text[self.rows[last][0] : self.rows[last][1]].lstrip(" \t>")
        closing = closing.rstrip(" \t")
        if len(closing) >= len(fence) and not closing.strip(fence[0]):
            last -= 1
        body = (self.starts[index], min(self.rows[last][1], end))
        return body if last >= index and body[1] > body[0] else None

    def tables(self, found: Found) -> None:
        """The cells of each row of a table beyond the cells of its header. A table
        starts at a delimiter row below its header, which is the row before: one of
        as many cells, with text, no heading, and outside code, outside the HTML
        blocks that surely hold rows and outside the table before."""
        text, code, starts = self.text, self.code(), self.starts
        held = html_blocks(text, self)
        held_starts = [start for start, _ in held]

        def outside(index: int) -> bool:
            start = starts[index]
            at = bisect_right(held_starts, start) - 1
            return not code[index] and (at < 0 or held[at][1] <= start)

        after = 0
        for row in DELIMITER_ROW.finditer(text):
            index = bisect_right(starts, row.start()) - 1
            if not index or starts[index - 1] < after:
                continue
            row_start, end = self.rows[index]
            marks = row["marks"]
            column = width(marks[marks.rfind(">") + 1 :])
            listed = bool(ITEM_MARK.search(marks))
            header = self.rows[index - 1]
            container = CONTAINER.match(text, *header)
            assert container is not None  # It matches the empty text.
            first, above = container.end(), text[header[0] : container.end()]
            if (
                (column >= 4 and not listed and row_start < self.listed)
                or not (outside(index) and outside(index - 1))
                or first == header[1]
                or text.startswith("#", first)
                or (
                    width(above[above.rfind(">") + 1 :]) >= 4
                    and not ITEM_MARK.search(above)
                    and header[0] < self.listed
                )
            ):
                continue
            # A delimiter row in other quotes than its header goes on its paragraph.
            count = len(splits(text, first, header[1])) + 1
            if above.count(">") != marks.count(">") or count != (
                len(splits(text, row.end("marks"), end)) + 1
            ):
                continue
            listed = listed or bool(ITEM_MARK.search(above))
            table = Table(marks.count(">"), column, count, listed)
            after = self.body(found, table, index + 1)

    def body(self, found: Found, table: Table, index: int) -> int:
        """Where the rows of a table end from row `index` on: the start of the first
        row that ends it. The rows that surely go on are read by two patterns, and
        one with a tab before its text alone."""
        text, starts, rows = self.text, self.starts, self.rows
        while index < len(rows):
            start = starts[index]
            stop = body_rows(table.depth, table.column, table.listed).match(text, start)
            assert stop is not None  # It matches the empty text.
            if stop.end() > start:
                cells = cut_cells(table.depth, table.count)
                found.cells += [
                    (cut.start("pipe"), cut.end())
                    for cut in cells.finditer(text, start, stop.end())
                ]
                index = bisect_right(starts, stop.end() - 1)
                continue
            quotes = QUOTE_RUN.match(text, start, rows[index][1])
            assert quotes is not None  # It matches the empty text.
            depth = text.count(">", start, quotes.end())
            if table.goes_on(text, found, depth, quotes.end(), rows[index][1]) is None:
                return start
            index += 1
        return len(text)

    def math(self, found: Found) -> None:
        """The math of each paragraph outside code and the drawn blocks: `$` and `$$`
        spans of a row, and `$$` rows around rows of their own."""
        text, code, rows = self.text, self.code(), self.rows
        taken = sorted(found.drawn + found.math + found.plain)
        starts = [start for start, _ in taken]
        # Only the rows that hold a dollar sign are read.
        signed = sorted(
            {
                bisect_right(self.starts, sign.start()) - 1
                for sign in DOLLAR.finditer(text)
            }
        )
        last = -1
        for index in signed:
            start, end = rows[index]
            at = bisect_right(starts, start) - 1
            if index <= last or code[index] or (at >= 0 and taken[at][1] > start):
                continue
            display = self.display(index)
            if display is not None:
                # The rest of its last row may hold math of its own.
                last, span, inner = display
                spans = [(span, inner), *inline_math(text, span[1], rows[last][1])]
            else:
                spans = inline_math(text, start, end)
            for span, inner in spans:
                (found.plain if plain_math(text, *inner) else found.math).append(span)

    def display(self, index: int) -> tuple[int, Span, Span] | None:
        """The last row, the span and the source of display math that a row of `$$`
        opens: up to the next row of its paragraph that holds a `$$`."""
        display = DISPLAY.match(self.text, self.starts[index])
        if display is None:
            return None
        last = bisect_right(self.starts, display.start("close")) - 1
        span = (display.start("open"), display.end())
        return last, span, (display.end("open"), display.start("close"))


def closing_fence(text: str, row: re.Match[str]) -> int:
    """Where the next row ends after a row of FENCE_ROW that could close its fence:
    one of its character, at least as long, with nothing after it; or the end of
    the text."""
    fence = row["fence"]
    for closing in FENCE_ROW.finditer(text, row.end()):
        found = closing["fence"]
        same = found[0] == fence[0] and len(found) >= len(fence)
        if same and not closing["info"].strip(" \t"):
            return closing.end()
    return len(text)


@dataclass(frozen=True)
class Table:
    """A table that its rows go on: the depth of its quotes, the column at which its
    delimiter row starts, the number of cells of its header, and whether a list
    item holds it, whose text a row less indented leaves."""

    depth: int
    column: int
    count: int
    listed: bool

    def goes_on(
        self, text: str, found: Found, depth: int, after: int, end: int
    ) -> Table | None:
        """The table, after the cells of a row beyond its header went to `found`, or
        None where the row ends it: one of other quotes, one that starts another
        block, one indented as code, and one less indented than its list item."""
        row = BODY_ROW.match(text, after, end)
        assert row is not None  # It matches the empty text.
        blanks = row["blanks"]
        column = width(blanks) if "\t" in blanks else len(blanks)
        if (
            depth != self.depth
            or row["interrupts"] is not None
            or column >= self.column + 4
            or (self.listed and column < self.column)
        ):
            return None
        pipes = splits(text, row.end("blanks"), end)
        if len(pipes) >= self.count:
            found.cells.append((pipes[self.count - 1], end))
        return self


@functools.cache
def body_rows(depth: int, column: int, listed: bool) -> re.Pattern[str]:
    """The rows that surely go on a table of `depth` quotes whose delimiter row
    starts at `column`, in a list item where `listed`: those of its quotes, with
    spaces before their text, no tab, fewer than four more than the delimiter row
    and in a list item no fewer, and none that ends a table."""
    least = f"(?= {{{column}}})" if listed and column else ""
    return re.compile(
        rf"(?:(?:[ \t]*+>){{{depth}}}(?![ \t]*+>)(?! *+\t)(?! {{{column + 4}}}){least}"
        rf"(?! *+(?:{INTERRUPTS.pattern}))[^\r\n]*+(?:\r\n?|\n|\Z))*+"
    )


@functools.cache
def cut_cells(depth: int, count: int) -> re.Pattern[str]:
    """The rows of a table of `depth` quotes and `count` cells in its header that
    hold more cells: each from its start, with the pipe before the first cell
    beyond them as `pipe`, to the end of its text. A pipe right after a backslash
    splits nothing, and none at the end of the row."""
    cell = r"(?:[^|\r\n]++|(?<=\\)\|)*+"
    return re.compile(
        rf"{ROW_START}(?:[ \t]*+>){{{depth}}}[ \t]*+\|?+(?:{cell}\|){{{count - 1}}}"
        rf"{cell}(?P<pipe>\|)(?![ \t]*+{END})[^\r\n]*+"
    )


def splits(text: str, start: int, end: int) -> list[int]:
    """The pipes that split the cells of a row of a table, from its first character
    that is no blank: every pipe but one right after a backslash, however many
    backslashes come before it, a pipe at the start and one at the end."""
    last = start + len(text[start:end].rstrip(" \t"))
    position = start + text.startswith("|", start)
    found = []
    while (pipe := text.find("|", position, last)) >= 0:
        position = pipe + 1
        if text[pipe - 1] == "\\":
            continue
        if pipe == last - 1:
            break
        found.append(pipe)
    return found


def row_spans(text: str) -> list[Span]:
    """Where each row of a text starts and where its text ends, before its line
    break."""
    rows, start = [], 0
    for match in ROW_END.finditer(text):
        rows.append((start, match.start()))
        start = match.end()
    rows.append((start, len(text)))
    return rows


def width(blanks: str) -> int:
    """How many columns some marks and blanks take, a tab up to the next multiple of
    four."""
    return len(blanks.expandtabs(4))


def html_blocks(text: str, reader: Reader) -> list[Span]:
    """The HTML blocks that surely hold rows, as spans of the text: from a row
    outside fenced code that HTML_START finds, up to the next blank row for a block
    tag, or to the end of what ends its kind, or of the text. A row that starts in
    one is HTML, in which a table is none: GitHub shows it as it is."""
    code, starts = reader.code(), reader.starts
    blocks: list[Span] = []
    position = 0
    while (opening := HTML_START.search(text, position)) is not None:
        start = opening.start()
        if code[bisect_right(starts, start) - 1]:
            position = opening.end()
            continue
        kind = opening.lastgroup
        assert kind is not None  # Every alternative is a group.
        if kind == "tag":
            closing = EMPTY_ROW.search(text, opening.end())
            end = len(text) if closing is None else closing.start()
        else:
            closing = HTML_END[kind].search(text, opening.end())
            end = len(text) if closing is None else closing.end()
        blocks.append((start, end))
        position = max(end, opening.end())
    return blocks


ESCAPED = re.compile(r"\\([!-/:-@\[-`{-~])")


def info_word(info: str) -> str:
    """The first word of the info string of a fence as CommonMark reads it, with its
    escapes and entities read, up to a space or a tab."""
    decoded = html.unescape(ESCAPED.sub(r"\1", info.strip(" \t")))
    return decoded.replace("\t", " ").partition(" ")[0]


# What a diagram's drawing doesn't show of its source: a comment, from `%%` to the
# end of its row, and the title and description for screen readers. A `click` gives
# a node a tooltip, which shows only on hover.
DIAGRAM_HIDDEN = re.compile(
    r"%%[^\r\n]*+|^[ \t>]*+(?:accTitle|accDescr|click)\b[^\r\n]*+", re.MULTILINE
)
DESCRIPTION_BLOCK = re.compile(r"^[ \t>]*+accDescr[ \t]*+\{", re.MULTILINE)
# What can draw the rest of a diagram otherwise than it reads, or hide it: a
# directive or front matter, which can set a theme, and the styles and classes of
# its nodes and links.
DIAGRAM_UNSURE = re.compile(
    r"%%\{|^[ \t>]*+---|\b(?:style|classDef|linkStyle|class|cssClass)\b|:::",
    re.MULTILINE,
)


def diagram(text: str, body: Span, found: Found) -> None:
    """What the drawing of a diagram shows of its source: all but its comments, its
    title and description and its tooltips; or nothing, where something in it could
    draw the rest otherwise than it reads."""
    start, end = body
    if DIAGRAM_UNSURE.search(text, start, end):
        return
    found.readable.add(body)
    found.diagram += [part.span() for part in DIAGRAM_HIDDEN.finditer(text, start, end)]
    # A description over rows, up to its `}`; the parts within it go with it.
    for block in DESCRIPTION_BLOCK.finditer(text, start, end):
        close = text.find("}", block.end(), end)
        found.diagram.append((block.start(), end if close < 0 else close + 1))


PRE = re.compile(r"<pre(?=[\t\n\f\r />])", re.IGNORECASE)
PRE_ATTRIBUTE = re.compile(
    r"[\t\n\f\r /]*+(?P<name>[^\t\n\f\r />\"'=]++)"
    r"(?:[\t\n\f\r ]*+=[\t\n\f\r ]*+(?:\"(?P<double>[^\"]*+)\"|'(?P<single>[^']*+)'"
    r"|(?P<bare>[^\t\n\f\r >]++)))?+"
)
TAG_END = re.compile(r"[\t\n\f\r /]*+>")
# What the HTML in a drawn `<pre>` holds before its end: a comment, in which no
# `</pre>` counts, a tag, whose quoted values may hold one, and the end itself.
PRE_CONTENT = re.compile(r"<!--|</?pre(?=[\t\n\f\r />])|<[A-Za-z/]", re.IGNORECASE)
TAG_INSIDE = re.compile(r"(?:[^>\"']++|\"[^\"]*+\"|'[^']*+')*+>")


def drawn_pres(text: str, reader: Reader, found: Found) -> None:
    """The content of each `<pre>` whose `lang` GitHub draws, up to the `</pre>` that
    ends it as HTML reads it, or to the end of the text; outside fenced code and
    comments. Once one never ends, nothing after it is read."""
    code, starts = reader.code(), reader.starts
    position = 0
    while (pre := PRE.search(text, position)) is not None:
        comment = COMMENT_START.search(text, position, pre.start())
        if comment is not None:
            close = text.find("-->", comment.end())
            if close < 0:
                return
            position = close + 3
            continue
        position = pre.end()
        if code[bisect_right(starts, pre.start()) - 1]:
            continue
        lang, tag_end = pre_lang(text, pre.end())
        if tag_end is None:
            continue
        position = tag_end
        if lang not in DRAWN:
            continue
        position = pre_end(text, tag_end)
        if position > tag_end:
            found.drawn.append((tag_end, position))
        if position == len(text):
            return


def pre_lang(text: str, position: int) -> tuple[str, int | None]:
    """The first word of the `lang` of a `<pre>` whose name ends at `position`, with
    its entities read, and where its tag ends, or None where no `>` ends it."""
    lang = None
    while (attribute := PRE_ATTRIBUTE.match(text, position)) is not None:
        position = attribute.end()
        if attribute["name"].lower() == "lang" and lang is None:
            value = attribute["double"] or attribute["single"] or attribute["bare"]
            lang = (html.unescape(value or "").split() or [""])[0]
    end = TAG_END.match(text, position)
    return lang or "", (end.end() if end else None)


def pre_end(text: str, position: int) -> int:
    """Where the content of a `<pre>` that starts at `position` ends: at the `</pre>`
    that ends it, past those in comments, in quoted values and of a `<pre>` within
    it, or at the end of the text."""
    depth = 1
    while (mark := PRE_CONTENT.search(text, position)) is not None:
        if mark[0] == "<!--":
            close = text.find("-->", mark.end())
            if close < 0:
                return len(text)
            position = close + 3
            continue
        rest = TAG_INSIDE.match(text, mark.end())
        if rest is None:
            return len(text)
        position = rest.end()
        name = mark[0].lower()
        depth += (name == "<pre") - (name == "</pre")
        if not depth:
            return mark.start()
    return len(text)


# A dollar sign as GitHub reads it in text: also escaped, and as an entity.
DOLLAR = re.compile(r"\\?+(?:\$|&#0*+36;|&#[xX]0*+24;|&dollar;)")
# A dollar sign that could open math, and one that could close it.
OPENS = re.compile(rf"(?<![^\W_]){DOLLAR.pattern}(?=[^ \t\r\n])")
CLOSES = re.compile(rf"(?<=[^ \t\r\n]){DOLLAR.pattern}(?![^\W_])")
# A link on one row, in whose text and address GitHub reads no math.
LINK = re.compile(r"(?<![\\!])\[[^\[\]\r\n]*+\]\([^()\s]*+\)")


def code_spans(text: str, start: int, end: int) -> list[Span]:
    """The code spans of a row, in which no `$` is math: from a run of backticks that
    no backslash comes before to the next run of the same length. One over rows
    counts as none, so the math in it counts: the safe side."""
    runs = [
        run.span()
        for run in BACKTICKS.finditer(text, start, end)
        if text[run.start() - 1 : run.start()] != "\\"
    ]
    lengths: dict[int, list[int]] = {}
    for index, (first, last) in enumerate(runs):
        lengths.setdefault(last - first, []).append(index)
    pointers = dict.fromkeys(lengths, 0)
    spans, index = [], 0
    while index < len(runs):
        first, last = runs[index]
        same, pointer = lengths[last - first], pointers[last - first]
        while pointer < len(same) and same[pointer] <= index:
            pointer += 1
        pointers[last - first] = pointer
        if pointer < len(same):
            spans.append((first, runs[same[pointer]][1]))
            index = same[pointer]
        index += 1
    return spans


def inline_math(text: str, start: int, end: int) -> list[tuple[Span, Span]]:
    """The spans of math in a row, each with its source between its dollar signs,
    as GitHub reads them outside code spans: `$` or `$$` that opens before no blank
    and after no letter or digit, up to the next of the same kind on the row that
    closes after no blank and before no letter or digit. A code span may stand
    between them, as in $`x`$. Not in a link, whose text and address GitHub reads
    as none. A row without a dollar sign that could open before one that could
    close holds none, which two searches tell."""
    opening = OPENS.search(text, start, end)
    if opening is None or CLOSES.search(text, opening.end(), end) is None:
        return []
    spans = [
        (first, last)
        for first, last, _ in apart(
            [(*span, "") for span in code_spans(text, start, end)]
            + [(*link.span(), "") for link in LINK.finditer(text, start, end)]
        )
    ]
    span_starts = [first for first, _ in spans]
    tokens: list[tuple[int, int, bool]] = []
    for dollar in DOLLAR.finditer(text, start, end):
        at = bisect_right(span_starts, dollar.start()) - 1
        if at >= 0 and spans[at][1] > dollar.start():
            continue
        if tokens and tokens[-1][1] == dollar.start() and not tokens[-1][2]:
            tokens[-1] = (tokens[-1][0], dollar.end(), True)
        else:
            tokens.append((dollar.start(), dollar.end(), False))
    # The next token from each on that can close math of each kind: after no blank
    # and before no letter or digit.
    singles: list[int | None] = [None] * (len(tokens) + 1)
    doubles: list[int | None] = [None] * (len(tokens) + 1)
    for index in reversed(range(len(tokens))):
        first, last, double = tokens[index]
        singles[index], doubles[index] = singles[index + 1], doubles[index + 1]
        if (
            first > start
            and text[first - 1] not in " \t"
            and not alnum_at(text, last, start, end)
        ):
            (doubles if double else singles)[index] = index
    found, index = [], 0
    while index < len(tokens):
        first, last, double = tokens[index]
        closer = (doubles if double else singles)[index + 1]
        if (
            closer is not None
            and last < end
            and text[last] not in " \t"
            and not alnum_at(text, first - 1, start, end)
        ):
            found.append(((first, tokens[closer][1]), (last, tokens[closer][0])))
            index = closer
        index += 1
    return found


def alnum_at(text: str, position: int, start: int, end: int) -> bool:
    """Whether a letter or a digit stands at a position of the row from `start` to
    `end`."""
    return start <= position < end and text[position].isalnum()


def plain_math(text: str, start: int, end: int) -> bool:
    """Whether math shows all of its source: one without a backslash, a `%` or an
    `&` holds no command, no comment and no entity, and so nothing that can draw
    text as nothing. GitHub reads the entities in math, and `\\%` as a `%`."""
    return not any(mark in text[start:end] for mark in "\\%&")


def math_between(text: str, spans: list[Span]) -> list[tuple[int, int, str]]:
    """The math, with a backslash, a `%` or an `&` in it, that GitHub finds in the
    parts of a text that its HTML leaves, which are its spans: a comment over rows
    can join two rows of math. Each as a span of the text, from the start of its
    first part to the end of its last, with its note."""
    joined = "".join(text[start:end] for start, end in spans)
    if not any(mark in joined for mark in ("$", "&#", "&dollar;")):
        return []
    found = Found()
    Reader(joined).math(found)
    starts, offset = [], 0
    for start, end in spans:
        starts.append(offset)
        offset += end - start

    def position(at: int) -> int:
        index = bisect_right(starts, at) - 1
        return spans[index][0] + at - starts[index]

    return [
        (position(start), position(end - 1) + 1, MATH_NOTE) for start, end in found.math
    ]


def math_signs(text: str, before: str = " ", after: str = " ") -> list[str]:
    """A text split at each dollar sign that could open or close math on GitHub, so
    that each of them, at the odd places, can become something else: on each row,
    one before no blank that a dollar sign after no blank follows, and that one; on
    the last row, one before no blank where the code after the text, `after`, could
    hold the end of the math; and on the first row, one after no blank where the
    code before it, `before`, could hold its start. More of them count than GitHub
    reads as math, which only changes more."""
    signs = [index for index, character in enumerate(text) if character == "$"]

    def blank(position: int) -> bool:
        if position < 0:
            return before in " \t\n"
        return (text[position] if position < len(text) else after) in " \t\n"

    opening = {sign for sign in signs if not blank(sign + 1)}
    closing = {sign for sign in signs if not blank(sign - 1)}
    rows: dict[int, list[int]] = {}
    row = position = 0
    for sign in signs:
        row += text.count("\n", position, sign)
        position = sign
        rows.setdefault(row, []).append(sign)
    last = text.rfind("\n")
    taken: set[int] = set()
    for row, found in rows.items():
        opened = closes = False
        for sign in found:
            if opened and sign in closing:
                taken.add(sign)
            opened = opened or sign in opening
        for sign in reversed(found):
            if closes and sign in opening:
                taken.add(sign)
            closes = closes or sign in closing
        if row == 0 and before == "`":
            taken.update(sign for sign in found if sign in closing)
        if after == "`" and found[0] > last:
            taken.update(sign for sign in found if sign in opening)
    pieces, position = [], 0
    for sign in sorted(taken):
        pieces += [text[position:sign], "$"]
        position = sign + 1
    pieces.append(text[position:])
    return pieces
