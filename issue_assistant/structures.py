"""The structures of Markdown whose text GitHub doesn't show as it is written: the
cells of a row of a table beyond the cells of its header, which GitHub drops; the
info string of fenced code, of which the page shows nothing; the source of a
diagram, a map or a 3D model, which it draws in place of the source; math, whose
TeX it hands to a renderer that can draw some of it as nothing; and the title of an
inline link or image, which the page shows only on hover.

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
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field

from .code_languages import CODE_LANGUAGES
from .depth import deep_spans
from .markup import (
    BLOCK_TAGS,
    COMMENT_START,
    CONTAINER,
    CONTAINER_MARK,
    FENCE_ROW,
    FOOTNOTE_MARK,
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
# After the fence or the language that stays, with a space before it, and without a
# backtick, so that the row stays a fence.
INFO_NOTE = " (words of the fence that GitHub doesn't show removed)"
DRAWING_NOTE = (
    "(the source of a drawing removed: GitHub shows a diagram, a map or a 3D model "
    "in its place)"
)
DIAGRAM_NOTE = "(a part of the diagram that GitHub doesn't draw removed)"
MATH_NOTE = "(math removed)"
# Nothing takes the place of the title of a link or an image, as of an attribute of
# a tag: the link stays a link, and its `)` keeps the words on both sides apart.
TITLE_NOTE = ""

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
# The label of a footnote at the start of its first row, with the blanks after it.
FOOTNOTE_LABEL = re.compile(rf"{FOOTNOTE_MARK}[ \t]*+")
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
    # What goes of the info string of each row that may open fenced code.
    infos: list[Span] = field(default_factory=list)
    drawn: list[Span] = field(default_factory=list)
    readable: set[Span] = field(default_factory=set)
    diagram: list[Span] = field(default_factory=list)
    math: list[Span] = field(default_factory=list)
    plain: list[Span] = field(default_factory=list)
    # The title of each inline link and image, with the blanks before it.
    titles: list[Span] = field(default_factory=list)
    # Whether the text holds a dollar sign, as an entity too, of which math could
    # also form where its HTML leaves the text.
    dollars: bool = False
    # What GitHub may not show for the depth of the text, with its notes: the rest
    # of the text from a row on, and the footnotes.
    deep: list[tuple[int, int, str]] = field(default_factory=list)

    def hidden(self) -> list[tuple[int, int, str]]:
        """The spans of which GitHub shows nothing as it is written, each with the
        note for its place, in order and apart."""
        spans = [(start, end, TABLE_NOTE) for start, end in self.cells]
        spans += [(start, end, INFO_NOTE) for start, end in self.infos]
        spans += [
            (start, end, DRAWING_NOTE)
            for start, end in self.drawn
            if (start, end) not in self.readable
        ]
        spans += [(start, end, DIAGRAM_NOTE) for start, end in self.diagram]
        spans += [(start, end, MATH_NOTE) for start, end in self.math]
        spans += [(start, end, TITLE_NOTE) for start, end in self.titles]
        return apart(spans + self.deep)

    def unlinked(self) -> list[tuple[int, int, str]]:
        """The spans in which GitHub shows no link: those that it hides, and all of
        a drawing or of math, whose text is drawn and leads nowhere."""
        spans = self.cells + self.infos + self.drawn + self.math + self.plain
        spans += self.titles
        spans += [(start, end) for start, end, _ in self.deep]
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
    """The tables, info strings of fences, drawn blocks, math and titles of links of
    a text, as GitHub reads them."""
    # The searches for a text are fast, a pattern reads every position: a fence or
    # a `<pre>` that GitHub draws, or whose content is math, names its language in
    # letters or entities. Only a row of a fence whose info string loses words asks
    # where it stands.
    found = Found(dollars="$" in text or "&#" in text or "&dollar;" in text)
    fences = "```" in text or "~~~" in text
    infos = hidden_infos(text) if fences else []
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
    # A title follows the destination of a link, after its `](`.
    titles = "](" in text and LINK_TITLE.search(text) is not None
    found.deep = deep_spans(text)
    if not (infos or fences or tables or pres or dollars or titles):
        return found
    reader = Reader(text)
    if infos:
        reader.infos(found, infos)
    if fences:
        reader.fenced(found)
    if pres:
        drawn_pres(text, reader, found)
    if tables:
        reader.tables(found)
    if dollars:
        reader.math(found)
    if titles:
        found.titles = Titles(text, reader).spans()
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
            # Without three backticks or tildes in a row, no row is fenced code; the
            # fenced code found before the reading stopped holds its rows.
            self.held = (
                [False] * len(self.starts)
                if self.fences.done and not self.fences.starts
                else [self.fences.holds(start) for start in self.starts]
            )
        return self.held

    def infos(self, found: Found, rows: list[tuple[re.Match[str], Span]]) -> None:
        """What goes of the info strings of the rows that may open fenced code, each
        with its span of hidden_infos: all but the rows that surely open none, which
        the page shows as they are. Those are a row of indented code above the first
        list item, one that fenced code surely holds, as Fences reads it and not as
        body_of guesses, and one that an HTML block surely holds: one that starts
        before Fences can't tell any more and before the first list item, which
        could hold the start of the block.

        Where it is hard to tell, as after a tab, in a footnote, in a row that an
        HTML block may hold, or after the point where Fences stops, the words go:
        GitHub may read the row as the opening of fenced code and show none of
        them."""
        held: list[Span] | None = None
        for row, span in rows:
            start = row.start()
            if start < self.listed and width(row["indent"]) >= 4 + bool(row["marks"]):
                continue
            block = self.fences.block(start)
            if block is not None and block[0] < start:
                continue
            if start < self.listed and "<" in self.text:
                if held is None:
                    held = [
                        (first, end)
                        for first, end in html_blocks(self.text, self)
                        if first < self.fences.sure
                    ]
                at = bisect_right(held, (start, len(self.text))) - 1
                if at >= 0 and start < held[at][1]:
                    continue
            found.infos.append(span)

    def fenced(self, found: Found) -> None:
        """The fences that GitHub draws, or whose content is math: those whose info
        string starts with a word of DRAWN, or is `math`, outside other code."""
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
            math = word == "math"
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
            column = indent(marks)
            listed = bool(ITEM_MARK.search(marks))
            header = self.rows[index - 1]
            container = CONTAINER.match(text, *header)
            assert container is not None  # It matches the empty text.
            first, above = container.end(), text[header[0] : container.end()]
            # The label of a footnote on the row of the header is one of its marks:
            # its cells start after it, and only a delimiter row indented by four
            # at least, as the footnote's text, is one of the footnote (`gh api
            # markdown`, 2026-11-23). A row of the table less indented than the
            # delimiter row may still be one of the footnote: it goes on the table.
            label = FOOTNOTE_LABEL.match(text, first, header[1])
            first = first if label is None else label.end()
            if (
                (column >= 4 and not listed and row_start < self.listed)
                or (label is not None and column < 4)
                or not (outside(index) and outside(index - 1))
                or first == header[1]
                or text.startswith("#", first)
                or (
                    indent(above) >= 4
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
            # A row indented as code ends the table: four columns in where no list
            # item or footnote may hold it, and otherwise four past its delimiter
            # row, which may be anywhere in the text of its list item.
            unheld = column < 4 and not listed and row_start < self.listed
            coded = 4 if unheld else column + 4
            table = Table(marks.count(">"), column, count, listed, coded)
            after = self.body(found, table, index + 1)

    def body(self, found: Found, table: Table, index: int) -> int:
        """Where the rows of a table end from row `index` on: the start of the first
        row that ends it. The rows that surely go on are read by two patterns, and
        one with a tab before its text alone."""
        text, starts, rows = self.text, self.starts, self.rows
        while index < len(rows):
            start = starts[index]
            stop = body_rows(table).match(text, start)
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
            marks = (start, quotes.end())
            if table.goes_on(text, found, depth, marks, rows[index][1]) is None:
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
    delimiter row starts, the number of cells of its header, whether a list item
    holds it, whose text a row less indented leaves, and the column from which a
    row is indented as code."""

    depth: int
    column: int
    count: int
    listed: bool
    code: int

    def goes_on(
        self, text: str, found: Found, depth: int, marks: Span, end: int
    ) -> Table | None:
        """The table, after the cells of a row beyond its header went to `found`, or
        None where the row ends it: one of other quotes, one that starts another
        block, one indented as code, and one less indented than its list item. The
        row starts at the first of `marks`, and its quotes end at the second."""
        row = BODY_ROW.match(text, marks[1], end)
        assert row is not None  # It matches the empty text.
        column = indent(text[marks[0] : row.end("blanks")])
        if (
            depth != self.depth
            or row["interrupts"] is not None
            or column >= self.code
            or (self.listed and column < self.column)
        ):
            return None
        pipes = splits(text, row.end("blanks"), end)
        if len(pipes) >= self.count:
            found.cells.append((pipes[self.count - 1], end))
        return self


@functools.cache
def body_rows(table: Table) -> re.Pattern[str]:
    """The rows that surely go on a table: those of its quotes, with spaces before
    their text, no tab, fewer than its column of code, in a list item no fewer than
    its delimiter row, and none that ends a table. In a quote, the space after its
    `>`, which indent leaves out, is one more."""
    quoted = table.depth > 0
    least = f"(?= {{{table.column + quoted}}})" if table.listed and table.column else ""
    return re.compile(
        rf"(?:(?:[ \t]*+>){{{table.depth}}}(?![ \t]*+>)(?! *+\t)"
        rf"(?! {{{table.code + quoted}}}){least}"
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


def indent(marks: str) -> int:
    """How many columns the blanks after the last `>` of the marks at the start of
    a row take, as CommonMark counts them: from the column where they start, a tab
    up to the next multiple of four, without the one column after the `>` that
    belongs to it; or the columns of all the marks, without a `>`. A tab after `>`
    is 2 columns in, as GitHub reads it, not 4."""
    quote = marks.rfind(">") + 1
    if not quote:
        return width(marks)
    return max(width(marks) - width(marks[:quote]) - 1, 0)


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
        elif kind == "comment":
            # A comment's end is its first `-->`, found as text, not by a pattern.
            close = text.find("-->", opening.end())
            end = len(text) if close < 0 else close + 3
        else:
            closing = HTML_END[kind].search(text, opening.end())
            end = len(text) if closing is None else closing.end()
        blocks.append((start, end))
        position = max(end, opening.end())
    return blocks


ESCAPED = re.compile(r"\\([!-/:-@\[-`{-~])")


# A row of FENCE_ROW whose info string holds more than blanks, which alone can lose
# words: only those rows are read. A fence of backticks with a backtick after it is
# none, but code of a row.
FENCE_MARKS = rf"(?:[ \t]*+(?:{CONTAINER_MARK}|{FOOTNOTE_MARK}))*+"
INFO_FENCE = r"`{3,}+(?![^\r\n]*`)|~{3,}+"
INFO_PART = r"[ \t]*+[^ \t\r\n][^\r\n]*+"
INFO_ROW = re.compile(
    rf"{ROW_START}(?P<marks>{FENCE_MARKS})(?P<indent>[ \t]*+)(?P<fence>{INFO_FENCE})"
    rf"(?P<info>{INFO_PART})"
)
# The info strings of those rows alone, which findall reads faster than each row.
INFO_STRINGS = re.compile(
    rf"{ROW_START}{FENCE_MARKS}[ \t]*+(?:{INFO_FENCE})({INFO_PART})"
)
# The blanks before the first word of an info string, and the word.
INFO_WORD = re.compile(r"[ \t]*+(?P<word>[^ \t]*+)")


def info_cut(info: str) -> int:
    """Where the part of an info string ends that the engine reads: after its first
    word, up to a space or a tab, where that is a plain word of CODE_LANGUAGES,
    written in ASCII, without an entity or an escape; before a comma where the part
    before it is one, at which GitHub ends a language that it knows; at the start
    where the word is none; and at the end where only blanks follow what stays.

    GitHub shows nothing of the info string: it keeps a language that it highlights
    as a class, another word as the `lang` of the `<pre>` and the words after it in
    its `data-meta`, which no reader sees (`gh api -X POST markdown`, 2026-11-21).
    A word of a fixed list can carry no message, and the AI learns the language."""
    first = INFO_WORD.match(info)
    assert first is not None  # It matches the empty text.
    word = first["word"]
    name = word.partition(",")[0]
    if name != word and name.isascii() and name.lower() in CODE_LANGUAGES:
        cut = first.start("word") + len(name)
    elif word.isascii() and word.lower() in CODE_LANGUAGES:
        cut = first.end()
    else:
        cut = 0
    return len(info) if not info[cut:].strip(" \t") else cut


def hidden_infos(text: str) -> list[tuple[re.Match[str], Span]]:
    """Each row of INFO_ROW whose info string loses words, as info_cut reads it,
    with the span of what goes. Each info string is read once, and the rows only
    where one loses words: the rows of a text often repeat theirs, such as
    `mermaid`."""
    cuts = {info: info_cut(info) for info in set(INFO_STRINGS.findall(text))}
    if all(cut == len(info) for info, cut in cuts.items()):
        return []
    return [
        (row, (row.start("info") + cuts[row["info"]], row.end()))
        for row in INFO_ROW.finditer(text)
        if cuts[row["info"]] < len(row["info"])
    ]


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


# The blanks that GitHub reads around the destination and the title of a link:
# spaces, tabs, form feeds, vertical tabs and line breaks, each with the marks of
# the quotes of its next row. That the rows go on in one paragraph is read after.
SPACING = r"(?:[ \t\f\v]|(?:\r\n?|\n)[ \t\f\v>]*+)"
# The title of an inline link or an image, from the `](` that ends its text: the
# blanks before its destination; the destination, in `<>` or as a run of characters
# without a space, a tab or a line break, which ends here before the next `](`, so
# that each run is read once: the first `](` of a run holds the rest of it in its
# destination, and the last one finds the same title; at least one blank before the
# title; the title in `"…"`, `'…'` or `(…)`, in which a backslash takes the
# character after it, if there is one; and the blanks and the `)` after it. A form
# feed, a vertical tab or a no-break space before the quote is part of a run, as
# GitHub reads it, and a quote right after it too; after a `>`, a form feed or a
# vertical tab is a blank. The text of the link is read after.
LINK_TITLE = re.compile(
    rf"\]\({SPACING}*+"
    r"(?:<(?:[^<>\\\r\n]|\\[^\r\n]?+)*+>(?=[ \t\f\v\r\n])"
    r"|(?:[^ \t\r\n\\\]]|\\[^ \t\r\n]?+|\](?!\())++)"
    rf"(?P<title>{SPACING}++"
    r"(?:\"(?:[^\"\\]|\\[\s\S]?+)*+\"|'(?:[^'\\]|\\[\s\S]?+)*+'"
    r"|\((?:[^()\\]|\\[\s\S]?+)*+\)))"
    rf"{SPACING}*+\)"
)
# A `[` that no backslash escapes, which can open the text of a link or an image,
# and a `]`, which can close it.
OPENER = re.compile(r"(?<!\\)(?:\\\\)*+\[")
CLOSER = re.compile(r"(?<!\\)(?:\\\\)*+\]")
# What can hold a `]` that closes nothing: a tag, an autolink, a code span, math or
# the destination of a link.
UNSURE = re.compile(r"[<`$]|\]\(")
# A row indented as code, after the blanks before its text; and a heading, after the
# marks of its quotes and list items and their blanks.
INDENTED_ROW = re.compile(r" {0,3}\t| {4}")
HEADING = re.compile(r"#{1,6}(?:[ \t]|$)")
# A blank row, also one of the marks of quotes alone: it ends a paragraph.
BLANK_ROW = re.compile(rf"{ROW_START}[ \t>]*+{END}")
BLANK = re.compile(r"[ \t]")
# What ends a run of characters that a destination can be.
RUN_ENDS = " \t\r\n"
# The marks of a quote, each with its blanks, as a row of a paragraph in it starts.
QUOTE_MARK = re.compile(r" {0,3}>[ \t]?")
# A row that ends a paragraph, from its first character after the marks of the
# quotes that hold it: a blank one, and the blocks that can interrupt a paragraph.
# A lone `-` ends it in any place: below the paragraph it makes a heading, and where
# the row is lazy or less indented than a list item, it starts a list item. Fewer
# rows count than in CommonMark: what such a row would end goes on, which takes out
# only more.
STOP = re.compile(
    r"[ \t]*$| {0,3}(?:>|#{1,6}(?:[ \t]|$)|`{3,}[^`]*$|~{3}"
    r"|([-*_])(?:[ \t]*\1){2,}[ \t]*$|-[ \t]*$|(?:[-+*]|0*1[.)])[ \t]+\S)"
)
# HTML that starts a block of a kind that can interrupt a paragraph, which ends it.
HTML_INTERRUPT = re.compile(
    rf" {{0,3}}(?:<(?:{caseless('pre|script|style|textarea')})(?=[\s>]|\Z)"
    r"|<!--|<\?|<![A-Za-z]|<!\[CDATA\["
    rf"|</?(?:{caseless(BLOCK_TAGS)})(?=[ \t>]|/>|\Z))"
)
# The line under a heading, which ends the paragraph only where it can underline
# it: in every quote of the paragraph, and in its list items, indented as far as
# its first row. A lazy row, which goes on in a quote or a list item without its
# marks or its indentation, makes no heading, and GitHub reads on.
UNDERLINE = re.compile(r"( {0,3})(?:=+|-+)[ \t]*$")
# A row that ends a paragraph, or a line that can underline it, with the blanks
# before it as `underline`: one pattern for the three.
PARAGRAPH_END = re.compile(
    rf"(?:{STOP.pattern})|(?:{HTML_INTERRUPT.pattern})"
    r"|(?P<underline> {0,3})(?:=+|-+)[ \t]*$"
)


def quoted(row: str, depth: int) -> tuple[int, int]:
    """Where the marks of up to `depth` quotes at the start of a row end, and how
    many there are."""
    position = 0
    for marks in range(depth):
        mark = QUOTE_MARK.match(row, position)
        if not mark:
            return position, marks
        position = mark.end()
    return position, depth


class Places:
    """Where code surely holds a position of a text, and the paragraphs and the
    quotes of its rows, read from the top as GitHub reads them: fenced code, an HTML
    block that surely holds its row, indented code, and a code span that starts
    before the position on its row, past `after`. Where an earlier row of the
    paragraph holds a backtick, a code span could run over rows, and none counts on
    the row. The rows and the positions are asked in order, and each part of the
    text is read a bounded number of times."""

    def __init__(self, text: str, reader: Reader) -> None:
        self.text = text
        self.reader = reader
        self.code = reader.code()
        self.held = html_blocks(text, reader)
        self.held_starts = [start for start, _ in self.held]
        # The last row read for indented code and for the quotes of its paragraph:
        # whether it is code, whether it is blank, and the quotes of the rows of its
        # paragraph up to it.
        self.coded_row = -1
        self.code_above, self.blank_above = False, True
        self.depth_row, self.quotes = -1, 0
        self.blank_rows: list[int] | None = None
        self.ticks: list[int] | None = None
        # The row whose backtick runs are read, the runs, the next of the same
        # length after each, and the first that may still open a code span.
        self.row = -1
        self.runs: list[Span] = []
        self.closers: list[int | None] = []
        self.run = 0
        self.joins: tuple[int, bool] = (-1, False)
        # Where the reading of code spans starts: nothing before it opens one.
        self.after = 0

    def block(self, row: int, position: int) -> bool:
        """Whether code of a block surely holds a position of a row: fenced or
        indented code, or an HTML block that surely holds its row."""
        return self.code[row] or self.held_at(position) or self.indented_code(row)

    def held_at(self, position: int) -> bool:
        """Whether an HTML block that surely holds its rows holds a position."""
        at = bisect_right(self.held_starts, position) - 1
        return at >= 0 and self.held[at][1] > position

    def blanks(self) -> list[int]:
        """Where each blank row starts, found once."""
        if self.blank_rows is None:
            self.blank_rows = [row.start() for row in BLANK_ROW.finditer(self.text)]
        return self.blank_rows

    def indented_code(self, row: int) -> bool:
        """Whether a row is surely indented code: outside quotes and list items, it
        and the rows above it up to a blank one, or to the start of the text, are
        indented by four columns. The rows are read once, from the top, and none
        in a text without a tab or four spaces."""
        text, rows, listed = self.text, self.reader.rows, self.reader.listed
        if self.coded_row < 0 and "\t" not in text and "    " not in text:
            self.coded_row = len(rows)
        while self.coded_row < row:
            self.coded_row += 1
            first, last = rows[self.coded_row]
            above = self.code_above
            self.code_above = (
                first < listed
                and INDENTED_ROW.match(text, first, last) is not None
                and (above or self.blank_above)
            )
            self.blank_above = not text[first:last].strip(" \t")
        return self.code_above

    def coded(self, row: int, start: int) -> bool:
        """Whether a code span that starts before `start` on its row holds it: from
        a run of backticks that no backslash comes before, after `after`, to the
        next run of the same length on the row."""
        if row != self.row:
            self.enter(row)
        text, runs, closers = self.text, self.runs, self.closers
        while self.run < len(runs) and runs[self.run][0] < start:
            first = runs[self.run][0]
            closer = closers[self.run]
            if first < self.after or closer is None or text[first - 1 : first] == "\\":
                self.run += 1
                continue
            if runs[closer][0] > start:
                return not self.joined(row, first)
            self.run = closer + 1
        return False

    def span_end(self, row: int, start: int) -> int:
        """Where a code span that opens at `start` on its row ends, past its closing
        backticks, or -1 where none surely opens there; asked in order, at a
        position that no code span holds."""
        if not self.coded(row, start + 1):
            return -1
        # No span holds `start`, so the one that holds the next position opens there.
        closer = self.closers[self.run]
        assert closer is not None  # coded holds only a span that closes.
        return self.runs[closer][1]

    def enter(self, row: int) -> None:
        """Reads the backtick runs of a row, unless an earlier row of its paragraph
        holds a backtick: then a code span could start there, and none counts."""
        text = self.text
        first, last = self.reader.rows[row]
        self.row, self.run = row, 0
        runs = [ticks.span() for ticks in BACKTICKS.finditer(text, first, last)]
        if runs:
            if self.ticks is None:
                self.ticks = [ticks.start() for ticks in BACKTICKS.finditer(text)]
            blanks = self.blanks()
            above = bisect_left(blanks, first) - 1
            paragraph = blanks[above] if above >= 0 else 0
            tick = bisect_left(self.ticks, first) - 1
            if tick >= 0 and self.ticks[tick] >= paragraph:
                runs = []
        closers: list[int | None] = [None] * len(runs)
        later: dict[int, int] = {}
        for index in reversed(range(len(runs))):
            length = runs[index][1] - runs[index][0]
            closers[index] = later.get(length)
            later[length] = index
        self.runs, self.closers = runs, closers

    def joined(self, row: int, first: int) -> bool:
        """Whether a `](` after `after` stands before the backticks at `first` with
        no blank between: its destination holds them, and the code span starts in a
        link."""
        if self.joins[0] != first:
            text = self.text
            joint = text.rfind("](", self.reader.rows[row][0], first)
            self.joins = (
                first,
                joint >= self.after and BLANK.search(text, joint, first) is None,
            )
        return self.joins[1]

    def goes_on(self, row: int, last: int) -> bool:
        """Whether the rows after `row` up to `last` go on its paragraph: none is
        code or ends it, read without the marks of the quotes of the paragraph; a
        line under a heading ends it in all of them, indented as far as the text of
        a list item that holds the row."""
        text, rows = self.text, self.reader.rows
        depth = self.depth(row)
        line = text[rows[row][0] : rows[row][1]]
        container = CONTAINER.match(line)
        assert container is not None  # It matches the empty text.
        if HEADING.match(line, container.end()):
            # A heading is a row of its own.
            return False
        indent = 0
        if rows[row][0] >= self.reader.listed:
            indent = width(line[quoted(line, depth)[0] : container.end()])
        for index in range(row + 1, last + 1):
            line = text[rows[index][0] : rows[index][1]]
            position, marks = quoted(line, depth)
            end = PARAGRAPH_END.match(line, position)
            if self.code[index] or (
                end is not None
                and (
                    end["underline"] is None
                    or (marks == depth and len(end["underline"]) >= indent)
                )
            ):
                return False
        return True

    def depth(self, row: int) -> int:
        """How many quotes hold the paragraph of a row: as many as any row of it up
        to that one has, from a blank row on. A row with more starts a quote of its
        own, but one quote too many only goes on the paragraph further. The rows
        are read once, from the top, and none in a text without a `>`."""
        text, rows = self.text, self.reader.rows
        if self.depth_row < 0 and ">" not in text:
            self.depth_row = len(rows)
        while self.depth_row < row:
            self.depth_row += 1
            first, last = rows[self.depth_row]
            container = CONTAINER.match(text, first, last)
            assert container is not None  # It matches the empty text.
            quotes = text.count(">", first, container.end())
            blank = not text[first:last].strip(" \t>")
            self.quotes = 0 if blank else max(self.quotes, quotes)
        return self.quotes


class Titles(Places):
    """The titles of the inline links and images of a text, as GitHub reads them:
    each that follows the destination after the `](` of a link, from its blanks to
    the end of its quote, of which the page shows nothing but on hover.

    The text of a link reaches back to the last `[` of its paragraph that no
    backslash escapes and no link took; one that holds a link, which GitHub shows as
    text, or an autolink also takes it, so that its title goes as well: the safe
    side. No link starts in fenced code, in an HTML block that surely holds its row,
    or in a code span that starts before its `](` on its row, read from the left as
    GitHub reads it, past the titles found before. A title over rows goes on only
    in the rows of its paragraph.

    Each part of the text is read a bounded number of times: the destination ends
    before the next `](`, a title before the next quote of its kind, and the brackets
    and backticks are found once.
    """

    def __init__(self, text: str, reader: Reader) -> None:
        super().__init__(text, reader)
        self.brackets = [opener.end() - 1 for opener in OPENER.finditer(text)]
        self.ends = [closer.end() - 1 for closer in CLOSER.finditer(text)]
        self.end = 0
        self.unsure: list[int] | None = None
        # For each `[` that a link took or holds, the index of the next one before
        # it that may still open one; -1 where none can.
        self.taken: dict[int, int] = {}
        # Where the last title found ends: nothing before it opens anything, a code
        # span neither (`after`); and where the blanks before the last title that a
        # match found start.
        self.title = -1

    def spans(self) -> list[Span]:
        """The span of each title with the blanks before it, in order and apart."""
        text, found, position = self.text, [], 0
        while (match := LINK_TITLE.search(text, position)) is not None:
            start = match.start()
            self.close(start)
            self.title = match.start("title")
            opener = self.opener(start)
            if opener is None or not self.link(match):
                position = start + 2
                continue
            self.take(opener, start, match.end())
            found.append(match.span("title"))
            position = self.after = match.end()
        return found

    def free(self, index: int) -> int:
        """The index of the last `[` from `index` down that may still open a link,
        or -1: each step that a taken one leads to is kept, so the next reading
        skips it at once."""
        path = []
        while index >= 0 and index in self.taken:
            path.append(index)
            index = self.taken[index]
        for step in path:
            self.taken[step] = index
        return index

    def close(self, stop: int) -> None:
        """Lets each `]` before `stop` after the last title take the `[` that it
        closes, as GitHub reads it: the last that may still open a link, where
        nothing between them can hold the `]`, as UNSURE finds it. Where something
        can, the `[` stays free, and a title after it goes: the safe side.

        A `](` in the run of characters that ends before the title of the match at
        `stop` closes the same link: the first of them holds the rest of the run in
        its destination, as `[a](x`](y` "t")` shows."""
        text, ends, brackets = self.text, self.ends, self.brackets
        run = -1
        while self.end < len(ends) and ends[self.end] < stop:
            closing = ends[self.end]
            self.end += 1
            if closing < self.after:
                continue
            if text.startswith("](", closing):
                if run < 0:
                    low = max(self.title, 0)
                    ends_of_runs = (text.rfind(blank, low, stop) for blank in RUN_ENDS)
                    run = 1 + max(self.title, *ends_of_runs)
                if closing >= run:
                    continue
            index = self.free(bisect_left(brackets, closing) - 1)
            if index < 0:
                continue
            if self.unsure is None:
                self.unsure = [mark.start() for mark in UNSURE.finditer(self.text)]
            at = bisect_right(self.unsure, brackets[index])
            if at == len(self.unsure) or self.unsure[at] >= closing:
                self.taken[index] = index - 1

    def opener(self, start: int) -> int | None:
        """The index of the `[` that opens the text of a link whose `](` stands at
        `start`: the last one before it that may, in its paragraph; or None, also
        where a backslash escapes the `]`."""
        text = self.text
        if text[start - 1 : start] == "\\":
            escapes = start - 1
            while escapes and text[escapes - 1] == "\\":
                escapes -= 1
            if (start - escapes) % 2:
                return None
        index = self.free(bisect_left(self.brackets, start) - 1)
        if index < 0:
            return None
        blanks = self.blanks()
        at = bisect_right(blanks, self.brackets[index])
        if at < len(blanks) and blanks[at] < start:
            # A blank row ends the paragraph of every `[` before it.
            self.taken[index] = -1
            return None
        return index

    def take(self, opener: int, start: int, end: int) -> None:
        """The `[` that a link took, and those in its destination and its title,
        from `start` to `end`, can open no other."""
        self.taken[opener] = opener - 1
        first = bisect_left(self.brackets, start)
        last = bisect_left(self.brackets, end) - 1
        if last >= first:
            self.taken[last] = opener

    def link(self, match: re.Match[str]) -> bool:
        """Whether GitHub reads a match of LINK_TITLE as a link: its `](` is in no
        code and no HTML block, and the rows after its first go on its paragraph."""
        start, end = match.span()
        starts = self.reader.starts
        row = bisect_right(starts, start) - 1
        if self.block(row, start) or self.coded(row, start):
            return False
        last = bisect_right(starts, end - 1) - 1
        return last == row or self.goes_on(row, last)
