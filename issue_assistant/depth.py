"""How deep GitHub nests a text: its renderer builds the HTML only down to a depth,
and drops the text inside more than 255 elements, everything after it in the HTML
and every footnote (`gh api -X POST markdown` with `mode: gfm`, 2026-11-21 and 22).
deep_spans finds the part of a text that goes for it, as the cleaning reads it, in
time linear in the length of the text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .markup import BLANK_ROW, FOOTNOTE_MARK, ROW_END, ROW_ENDS, ROW_START, row_end

Span = tuple[int, int]
# Where a row ends, also with `endpos`.
END = ROW_ENDS


def width(blanks: str) -> int:
    """How many columns some marks and blanks take, a tab up to the next multiple of
    four."""
    return len(blanks.expandtabs(4))


# How deep a text nests. GitHub's renderer builds the HTML of a text only down to a
# depth: it drops the text inside more than 255 elements, everything after it in
# the HTML, and every footnote, which it prints after the text (`gh api -X POST
# markdown` with `mode: gfm`, 2026-11-21 and 22). The cleaning doesn't copy that
# depth: it counts an upper bound of it for each row, in one pass, and cuts where the
# count reaches DEPTH_LIMIT, half of GitHub's limit. Where in doubt, it counts more.
DEPTH_LIMIT = 128
DEPTH_NOTE = "(the rest of the text removed: GitHub shows nothing nested this deep)"
# Every footnote goes with the rest, wherever it is written: GitHub prints the
# footnotes after the text.
FOOTNOTE_NOTE = "(footnote removed)"

# The marks at the start of a row: quotes, list items, footnote labels and blanks;
# and one of them, each of which counts for no more than 4.
DEPTH_UNIT = rf"(?:[ \t>]|(?:[-+*]|\d{{1,9}}[.)])(?=[ \t]|{END})|{FOOTNOTE_MARK})"
DEPTH_PREFIX = re.compile(rf"{DEPTH_UNIT}*+")
PREFIX_REACH = 5 * DEPTH_LIMIT
DEPTH_MARK = re.compile(rf"(?:[-+*]|\d{{1,9}}[.)])(?=[ \t]|{END})")
# A row on which the inline elements of a paragraph surely end: one of nothing but
# the marks of quotes and blanks, or one that starts a list item that can interrupt
# a paragraph. A row such as `*` alone, or `2.` after a paragraph, can go on with it.
INLINE_RESET = re.compile(
    rf"(?:[ ]{{0,3}}>[ ]?+)*+(?:[ \t>]*+{END}|[ ]{{0,3}}(?:[-+*]|1[.)])[ \t]++\S)"
)
# What the count reads of a row: a tag, a run of the marks of emphasis, a `[`; and
# where end tags may close elements, what may hold an end tag that is none: markup,
# a code span, math, a cell of a table, a link destination, a definition.
OPENERS = r"<(?P<slash>/?+)(?P<name>[A-Za-z][^\t\n\f\r />]*+)|(?P<run>\*++|_++|~++)|\["
DEPTH_TOKENS = {
    False: re.compile(OPENERS),
    True: re.compile(rf"{OPENERS}|<[!?]|`++|[$|]|\][(:]"),
}
END_TAG_TAIL = re.compile(r"[\t\f ]*+>")
# A link destination without blanks, brackets or a title, which no end tag is in;
# a row of a fence that may close fenced code; and the ends of markup.
PLAIN_DESTINATION = re.compile(r"[^\s()<>\\]*+\)")
CLOSING = re.compile(r"(?:`++|~++)[ \t]*+")
MARKUP_ENDS = (("<!--", "-->"), ("<![CDATA[", "]]>"), ("<?", "?>"), ("<!", ">"))
# A fence that opens fenced code, at the start of the text of a row; the marks of a
# row of no container but quotes, each indented by less than four columns; and an
# HTML block in which a fence is none, past blank rows.
FENCE_OPENING = re.compile(rf"`{{3,}}+(?=[^`\r\n]*+{END})|~{{3,}}+")
QUOTES_ONLY = re.compile(r"(?:[ ]{0,3}>[ ]?+)*+[ ]{0,3}")
RAW_BLOCK = re.compile(r"<(?i:pre|script|style|textarea)(?=[\t\n\f\r />]|\Z)")
# For shallow: a row of a fence at the start of a row, with no marks but up to three
# blanks before it; and
# a row that may start an HTML block after its marks.
FENCE_ROW = re.compile(
    rf"{ROW_START}[ ]{{0,3}}(?P<run>`{{3,}}+|~{{3,}}+)(?P<info>[^\r\n]*+)"
)
HTML_START_ROW = re.compile(rf"{ROW_START}[ \t>*+.)0-9-]*+<")
# A row of a table: its delimiter row, and a `|` that no `\` escapes, which ends a
# cell; and a row that may start another block, which ends a table and is no row
# of a header, as the rows of indented code are none.
TABLE_DELIMITER = re.compile(
    r"[ \t]*+\|?+[ \t]*+:?+-++:?+[ \t]*+(?:\|[ \t]*+:?+-++:?+[ \t]*+)*+\|?+[ \t]*+"
)
CELL_END = re.compile(r"(?<!\\)(?:\\\\)*+\|")
TABLE_END = re.compile(r"[#<`~\[]|(?:[*_-][ \t]*+){3,}+$")
TAG_TAIL = re.compile(r"[^>\"'\r\n]*+")
TAG_SPACES = "\t\n\f\r "
# Elements that add nothing to the depth, and those that the HTML parser nests in
# more: `<tbody>` and `<tr>` around a bare `<td>`, `<tbody>` around a `<tr>`.
VOID = frozenset(
    [
        "area",
        "base",
        "basefont",
        "bgsound",
        "br",
        "embed",
        "hr",
        "image",
        "img",
        "input",
        "keygen",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    ]
)
WEIGHTS = {"td": 3, "th": 3, "tr": 2}
# The elements that keep the end tag of an element around them from closing it, by
# the scopes of HTML's parser, so that a quote, a list item or a paragraph that
# ends in Markdown stays open around them (12 quotes of 20 marks, each with
# `<table><td>`, hide the last two on GitHub); and those of them that start an HTML
# block at the start of a row, with no paragraph around them.
SCOPES = frozenset(
    [
        "annotation-xml",
        "applet",
        "button",
        "caption",
        "desc",
        "foreignobject",
        "html",
        "marquee",
        "mi",
        "mn",
        "mo",
        "ms",
        "mtext",
        "object",
        "ol",
        "select",
        "table",
        "td",
        "template",
        "th",
        "title",
        "ul",
    ]
)
BLOCK_SCOPES = frozenset(["caption", "html", "ol", "table", "td", "th", "title", "ul"])
# For shallow: a start tag of SCOPES; and an element of another name on one row,
# between whose tags nothing stands that could hold the end tag as text, with values
# only in quotes after a `=`, which Depth closes as well.
SCOPE_NAMES = "|".join(sorted(SCOPES))
SCOPE_TAG = re.compile(rf"<(?i:{SCOPE_NAMES})(?=[\t\n\f\r />]|\Z)")
WEIGHTED_TAG = re.compile(r"<(?i:t[dhr])(?=[\t\n\f\r />]|\Z)")
CLOSED_PAIR = re.compile(
    rf"<(?<!\\<)(?=[A-Za-z])(?!(?i:{SCOPE_NAMES})(?![A-Za-z0-9-]))"
    r"(?P<name>[A-Za-z][A-Za-z0-9-]*+)(?=[\t\f />])"
    r"(?:[^<>\"'=\r\n]|=[ \t\f]*+(?:\"[^\"\r\n]*+\"|'[^'\r\n]*+'))*+>"
    r"[^<`$|\]!~\\\r\n]*+</(?P=name)[ \t\f]*+>"
)


@dataclass
class Open:
    """An element that no end tag closed: its name, where its tag starts and on
    which row, what it adds to the depth, what it keeps open of the Markdown around
    it, and of that, what the marks of emphasis and brackets keep."""

    name: str
    start: int
    row: int
    weight: int
    kept: int
    inline: int


@dataclass
class Depth:
    """The count of the depth of a text, row by row, as deep_spans reads it.

    A row counts its containers: 1 for each `>`, 2 for each list mark, 2 for each 2
    columns of blanks (a tab as 4) and 3 for a footnote label; a row that isn't
    blank after one that isn't keeps at least the containers of that one, which it
    may go on lazily. To that come 2 for its text and the element around it, 5 for
    the wrappers of a cell where the text holds a `|`, the open HTML, and the marks
    of its paragraph so far: each character of a run of `*`, `_` or `~` that no
    blank follows, but `_` between letters or digits, and 1 for each of the first
    three `[`, since no link holds a link: a link, and a footnote in it. A closing
    run takes nothing away, since one that closes nothing stays text. The marks
    count again only after a row of INLINE_RESET.

    The HTML is read over the whole text, code too: each start tag of an element
    that isn't void opens it, also one with `/>`, `<td>` and `<th>` as 3 and `<tr>`
    as 2. One of SCOPES also keeps the count of the Markdown around it, which
    stays open for good once a row ends while it is open. An end tag closes the
    innermost open element, and only one of its name, where it surely is an end
    tag: of no `>` but blanks after its name, with no `\\` before it, outside the
    tags before it, and outside all that started after that element and could hold
    the end tag as text, up to where that surely ends: a code span, math, a link
    destination that isn't plain, a definition or the description of an image up to
    the end of the paragraph; fenced code up to the fence that closes it; a comment
    or other markup up to its end; a cell of a table and a row that may be indented
    code up to the end of the row; and a tag that runs past its row, or whose end is
    hard to tell, up to the end of the text.
    """

    text: str
    # The containers of the last row, whether it was blank, and the marks and the
    # brackets of the paragraph so far.
    containers: int = 0
    blank: bool = True
    inline: int = 0
    brackets: int = 0
    # The open elements, and what they and those that closed add.
    tags: list[Open] = field(default_factory=list)
    html: int = 0
    # The starts of what may hold an end tag as text, by how far it may reach: the
    # end of the text, the paragraph, the row; the open fence, with the marks of
    # its row and its run; and the markup, each with its end.
    blocked: int = -1
    paragraph: int = -1
    row_blocked: int = -1
    fence: tuple[int, str, str] | None = None
    markup: list[Span] = field(default_factory=list)
    # The marks and the run of the fence of the fenced code that surely holds the
    # rows below it; whether a row since the last blank one may start an HTML block,
    # and whether the text may hold a block of `<pre>`, in which no fence is one.
    code: tuple[str, str] | None = None
    held: bool = False
    raw: bool = False
    # The end of the tags before, inside which no end tag closes anything, and the
    # end of a row after a tag that runs past it.
    covered: int = 0
    unsure: int = -1
    # The last search for the end of each kind of markup: where it started, and
    # what it found.
    searched: dict[str, Span] = field(default_factory=dict)
    # The footnotes, and where the last one starts while it goes on.
    footnotes: list[Span] = field(default_factory=list)
    footnote: int = -1
    # The marks, the start of its text and the end of the last row, and the marks
    # of the rows of the table that it is in, where each row is a paragraph.
    previous: tuple[str, int, int] | None = None
    table: str | None = None
    # The highest count so far.
    deepest: int = 0

    def cut(self) -> int | None:
        """The start of the first row whose count reaches DEPTH_LIMIT, or None."""
        text = self.text
        table = 5 if "|" in text else 0
        closing = "</" in text
        tokens = DEPTH_TOKENS[closing]
        self.raw = RAW_BLOCK.search(text) is not None
        breaks = ROW_END.finditer(text)
        start = 0
        while True:
            ending = next(breaks, None)
            end = ending.start() if ending else len(text)
            if self.row(start, end, 2 + table, tokens, closing):
                if self.footnote >= 0:
                    self.footnotes.append((self.footnote, start))
                return start
            if ending is None:
                return None
            start = ending.end()

    def row(
        self,
        start: int,
        end: int,
        text_depth: int,
        tokens: re.Pattern[str],
        closing: bool,
    ) -> bool:
        """Reads a row: its containers, its marks and its tags; whether its count
        reaches DEPTH_LIMIT."""
        text = self.text
        # Marks of no label count at least 1 for each 5 characters, as `123456789.`
        # and the blank after it count 3: a start of more than PREFIX_REACH of them
        # reaches the limit, and is read no further.
        reach = min(end, start + PREFIX_REACH)
        prefix = DEPTH_PREFIX.match(text, start, reach)
        assert prefix is not None  # It matches the empty text.
        body = prefix.end()
        if body == start + PREFIX_REACH and "[^" not in text[start:body]:
            return True
        marks = text[start:body]
        blank = body == end
        labels = marks.count("[^")
        blanks = marks.count(" ") + 4 * marks.count("\t")
        own = (
            marks.count(">")
            + 2 * len(DEPTH_MARK.findall(marks))
            + 3 * labels
            + 2 * ((blanks + 1) // 2)
        )
        self.goes_on(start, body, end, labels)
        self.containers = own if blank or self.blank else max(own, self.containers)
        self.blank = blank
        if self.tabled(body, end, marks) or INLINE_RESET.match(text, start, end):
            self.inline = self.brackets = 0
        if closing:
            self.regions(start, body, end, marks)
        base = self.containers + text_depth
        if self.reaches(base):
            return True
        if self.coded(start, body, end, marks):
            self.previous = self.table = None
            return False
        position = body
        while (token := tokens.search(text, position, end)) is not None:
            position = token.end()
            if token["run"]:
                self.inline += self.run(token, start, body, end)
            elif token["name"]:
                name = token["name"].lower()
                if token["slash"]:
                    self.end_tag(token, name, start)
                    continue
                self.start_tag(token, name, start, body, closing)
            elif token[0] == "[":
                if self.brackets < 3:
                    self.brackets += 1
                    self.inline += 1
                if text[token.start() - 1 : token.start()] == "!":
                    self.paragraph = token.start()  # The description of an image.
            else:
                self.region(token, start, body, end)
                continue
            if self.reaches(base):
                return True
        return False

    def tabled(self, body: int, end: int, marks: str) -> bool:
        """Whether a row is one of a table, from its delimiter row on: GitHub reads
        each cell by itself, so that no mark of a row goes on in the next. A table
        goes on over the rows of the same marks up to a blank row or a row that may
        start another block; it starts at a delimiter row below a row of the same
        marks and as many cells, which neither starts another block nor may be
        indented code."""
        text = self.text
        previous, self.previous = self.previous, (marks, body, end)
        if body == end or marks != self.table or TABLE_END.match(text, body, end):
            self.table = None
        if self.table is not None or body == end or previous is None:
            return self.table is not None
        above, header, header_end = previous
        if (
            above == marks
            and header < header_end
            and "|" in text[body:end]
            and TABLE_END.match(text, header, header_end) is None
            and "\t" not in marks
            and "    " not in marks[marks.rfind(">") + 1 :]
            and TABLE_DELIMITER.fullmatch(text, body, end)
            and cells(text[header:header_end]) == cells(text[body:end])
        ):
            self.table = marks
        return self.table is not None

    def coded(self, start: int, body: int, end: int, marks: str) -> bool:
        """Whether a row is one of fenced code that surely holds no tag and no mark:
        below a fence at the start of a row of no marks but quotes, with the same
        quotes and any blanks after them, up to its closing fence; where no HTML block may hold the fence, as
        one of `<pre>`, or any row since the last blank one that starts with `<`."""
        text = self.text
        if self.code is not None:
            opened, run = self.code
            inside, closes = code_row(marks, opened)
            if inside:
                if (
                    closes
                    and CLOSING.fullmatch(text, body, end)
                    and text.startswith(run, body)
                ):
                    self.code = None
                    self.fence = None
                return True
            self.code = None
        if BLANK_ROW.match(text, start, end):
            self.held = False
        if self.raw or self.held:
            return False
        if text.startswith("<", body):
            self.held = True
            return False
        opening = FENCE_OPENING.match(text, body, end)
        if opening is None or not QUOTES_ONLY.fullmatch(marks):
            return False
        self.code = (marks, opening[0])
        # Where a later row ends the code early, no end tag of it closes anything.
        self.fence = self.fence or (body, marks, opening[0])
        return True

    def reaches(self, base: int) -> bool:
        """Whether the count of the row so far, above `base`, reaches DEPTH_LIMIT."""
        count = base + self.html + self.inline
        self.deepest = max(self.deepest, count)
        return count >= DEPTH_LIMIT

    def regions(self, start: int, body: int, end: int, marks: str) -> None:
        """Ends what a row ends that may hold an end tag as text, and starts what it
        starts by its marks: a blank row ends a paragraph, a row of a fence closes
        fenced code, and a row that may be indented code is one."""
        text = self.text
        self.row_blocked = -1
        if BLANK_ROW.match(text, start, end):
            self.paragraph = -1
        if self.fence is not None:
            first, opened, run = self.fence
            if (
                code_row(marks, opened)[1]
                and CLOSING.fullmatch(text, body, end)
                and (text.startswith(run, body))
            ):
                self.fence = None
        if "\t" in marks or "    " in marks[marks.rfind(">") + 1 :]:
            self.row_blocked = start

    def region(self, token: re.Match[str], start: int, body: int, end: int) -> None:
        """Starts what a mark starts that may hold an end tag as text."""
        text, first = self.text, token.start()
        mark = token[0]
        if mark == "|":
            self.row_blocked = first  # A cell that GitHub may drop.
        elif mark[0] == "<":
            # Of the markup, only the latest start of each end counts: one that
            # ends no later than a later one adds nothing.
            ending = self.markup_end(first)
            while self.markup and self.markup[-1][1] <= ending:
                self.markup.pop()
            self.markup.append((first, ending))
        elif mark == "](":
            if PLAIN_DESTINATION.match(text, token.end(), end) is None:
                self.paragraph = first
        elif (
            mark[0] == "`"
            and first == body
            and len(mark) >= 3
            and (text.find("`", token.end(), end) < 0)
        ):
            self.fence = self.fence or (first, text[start:body], mark)
        else:
            self.paragraph = first

    def markup_end(self, start: int) -> int:
        """Where markup that starts at a `<` surely ends, as HTML reads it: a comment
        at its `-->`, an instruction at its `?>`, a section of CDATA at its `]]>`,
        other markup at its `>`; or at the end of the text. A search that found
        nothing, or found its end after the start, is not made again."""
        text = self.text
        opening, closing = next(
            pair for pair in MARKUP_ENDS if text.startswith(pair[0], start)
        )
        position = start + len(opening)
        since, found = self.searched.get(closing, (len(text) + 1, -1))
        if not (since <= position and (found < 0 or found >= position)):
            since, found = position, text.find(closing, position)
            self.searched[closing] = (since, found)
        return len(text) if found < 0 else found + len(closing)

    def goes_on(self, start: int, body: int, end: int, labels: int) -> None:
        """Follows the footnotes: one starts at a row with a label, and goes on up to
        a row after a blank one that is indented by less than four columns."""
        goes_on = self.footnote < 0 or not self.blank or body == end
        if not goes_on and (labels or width(self.text[start:body]) < 4):
            self.footnotes.append((self.footnote, start))
            self.footnote = -1
        if labels and self.footnote < 0:
            self.footnote = start

    def run(self, token: re.Match[str], start: int, body: int, end: int) -> int:
        """What a run of `*`, `_` or `~` may open: each of its characters where no
        blank follows it, and for `_` not between two letters or digits. Three `~`
        at the start of a row may open fenced code."""
        text, first, last = self.text, token.start(), token.end()
        mark = token["run"]
        if first == body and mark.startswith("~~~") and "</" in text:
            self.fence = self.fence or (first, text[start:body], mark)
        if last >= end or text[last].isspace():
            return 0
        if (
            mark[0] == "_"
            and text[last].isalnum()
            and text[first - 1 : first].isalnum()
        ):
            return 0
        return last - first

    def start_tag(
        self, token: re.Match[str], name: str, row: int, body: int, closing: bool
    ) -> None:
        """Opens the element of a start tag, as deep as it may nest."""
        if closing:
            self.reach(token)
        if name in VOID:
            return
        kept = inline = 0
        if name in SCOPES:
            inline = self.inline
            kept = self.containers + inline
            if token.start() != body or name not in BLOCK_SCOPES:
                # A paragraph, a heading or a cell around it stays open too.
                kept += 2 + (5 if "|" in self.text else 0)
        weight = WEIGHTS.get(name, 1)
        self.tags.append(Open(name, token.start(), row, weight, kept, inline))
        self.html += weight + kept

    def end_tag(self, token: re.Match[str], name: str, row: int) -> None:
        """Closes the innermost open element where an end tag surely closes it."""
        text, start = self.text, token.start()
        if END_TAG_TAIL.match(text, token.end()) is None:
            self.reach(token)
            return
        tags = self.tags
        if not tags or tags[-1].name != name or start < self.covered:
            return
        opened = tags[-1].start
        while self.markup and self.markup[-1][1] <= start:
            self.markup.pop()
        holders = [self.blocked, self.paragraph, self.row_blocked]
        if self.markup:
            holders.append(self.markup[-1][0])
        if self.fence is not None:
            holders.append(self.fence[0])
        if opened > max(holders) and text[start - 1 : start] != "\\":
            closed = tags.pop()
            # What it kept open of the Markdown around it stays open where that
            # closed meanwhile: an inline element on the same row, and anything
            # once a row ended.
            self.html -= closed.weight + closed.kept
            self.html += closed.kept if row > closed.row else closed.inline

    def reach(self, token: re.Match[str]) -> None:
        """Where a tag ends, inside which no end tag closes anything. A tag inside
        another, or one that runs past its row, ends no earlier than the end tag of
        an element that starts before it."""
        text, start = self.text, token.start()
        if start < self.covered or start < self.unsure:
            self.blocked = start
            return
        tail = TAG_TAIL.match(text, token.end())
        assert tail is not None  # It matches the empty text.
        after = tail.end()
        following = text[after : after + 1]
        end = after + 1 if following == ">" else None
        if following and following in "\"'":
            # A quote is part of a name or a value by the states before it.
            end = tag_end(text, token.end())
        if end is None:
            self.blocked = start
            # Each later tag of its row is as unsure, without reading it again.
            self.unsure = row_end(text, start)
        else:
            self.covered = end


def cells(row: str) -> int:
    """How many cells a row of a table holds, and 0 for a row with no `|`."""
    row = row.strip(" \t")
    ends = [cell.end() for cell in CELL_END.finditer(row)]
    if not ends:
        return 0
    return len(ends) + 1 - (ends[0] == 1) - (ends[-1] == len(row) and len(row) > 1)


def code_row(marks: str, opened: str) -> tuple[bool, bool]:
    """Whether a row with these marks goes on with fenced code whose fence row had
    the marks `opened`: with the same containers and nothing but blanks after them,
    which belong to the code; and whether a fence there may close it, indented by no
    more than three columns past them and the blank after a `>`. A tab ends the
    code, where its columns are hard to tell."""
    containers = opened.rstrip(" ")
    rest = marks[len(containers) :]
    if not marks.startswith(containers) or rest.strip(" "):
        return False, False
    return True, len(rest) <= 3 + containers.endswith(">")


def tag_end(text: str, position: int) -> int | None:
    """Where a tag ends on its row, read from a position after its name, by the
    states of HTML's tokenizer: after its `>`, or None where none ends it there."""
    state = "before"
    for index in range(position, len(text)):
        character = text[index]
        if character in "\r\n":
            return None
        if state in ('"', "'"):
            state = "quoted" if character == state else state
        elif character == ">":
            return index + 1
        elif state == "value":
            if character not in TAG_SPACES:
                state = character if character in "\"'" else "unquoted"
        elif state == "unquoted":
            state = "before" if character in TAG_SPACES else state
        elif character in TAG_SPACES:
            state = "after" if state in ("attribute", "after") else "before"
        elif character == "/":
            state = "before"
        elif character == "=" and state in ("attribute", "after"):
            state = "value"
        else:
            state = "attribute"
    return None


def shallow(text: str) -> bool:
    """Whether no row of a text can reach DEPTH_LIMIT, by searches over the whole
    text, or over the text without the fenced code that surely holds its rows."""
    if counted_shallow(text):
        return True
    if "```" not in text and "~~~" not in text:
        return False
    code_free = without_code(text)
    return code_free is not None and counted_shallow(code_free)


def without_code(text: str) -> str | None:
    """The text without each block of fenced code from a fence at the start of a
    row to the fence that closes it, there too: Markdown reads all between as code,
    where no row outside them starts with `<` after its marks, which could start an
    HTML block that holds a fence, and no tag of `<pre>` or its kind is in the text.
    Or None, where that is hard to tell."""
    if RAW_BLOCK.search(text):
        return None
    pieces, position, opened = [], 0, None
    for row in FENCE_ROW.finditer(text):
        run, info = row["run"], row["info"]
        if opened is None:
            if run[0] == "~" or "`" not in info:
                opened = (row.start(), run)
        elif run[0] == opened[1][0] and len(run) >= len(opened[1]) and not info.strip():
            pieces.append(text[position : opened[0]])
            position, opened = row.end(), None
    code_free = "".join(pieces) + text[position:]
    return None if HTML_START_ROW.search(code_free) else code_free


def counted_shallow(text: str) -> bool:
    """Whether no row of a text can reach DEPTH_LIMIT by its counts: each unit of
    the marks of the longest start of a row counts 4, each mark of emphasis 1, the
    brackets 3; each start tag as its weight in Depth, but those that an end tag on
    the row surely closes, and each of SCOPES as much again as all of the
    Markdown."""
    markdown = (
        2
        + (5 if "|" in text else 0)
        + text.count("*")
        + text.count("_")
        + text.count("~")
        + (3 if "[" in text else 0)
    )
    if markdown >= DEPTH_LIMIT:
        return False
    weights = boundaries = 0
    if "<" in text:
        marks = text
        if "</" in text:
            # The innermost elements first, then those that held them.
            for _ in range(4):
                text, closed = CLOSED_PAIR.subn("", text)
                if not closed:
                    break
        weights = (
            text.count("<") - text.count("</") - text.count("<!") - text.count("<?")
        )
        if weights >= DEPTH_LIMIT - markdown:
            return False
        weights += sum(
            WEIGHTS[tag[1:].lower()] - 1 for tag in WEIGHTED_TAG.findall(text)
        )
        boundaries = len(SCOPE_TAG.findall(text))
        text = marks
    room = (DEPTH_LIMIT - 1 - weights) // (1 + boundaries) - markdown
    if room < 0:
        return False
    # Without a label, a start of a row of so many units holds as many of their
    # characters in a row, which a search finds faster. A search for a line break
    # first finds it fastest.
    unit = DEPTH_UNIT if "[^" in text else r"[ \t>*+.)0-9-]"
    marks_run = rf"{unit}{{{room // 4 + 1}}}"
    return re.match(marks_run, text) is None and not any(
        breaking in text and re.search(breaking + marks_run, text)
        for breaking in "\n\r"
    )


def deep_spans(text: str) -> list[tuple[int, int, str]]:
    """The parts of a text that GitHub may not show for its depth, each with its
    note: from the start of the first row whose count of Depth reaches DEPTH_LIMIT
    to the end of the text, and every footnote before it; or none."""
    if shallow(text):
        return []
    depth = Depth(text)
    cut = depth.cut()
    if cut is None:
        return []
    footnotes = depth.footnotes
    # A footnote right above the cut goes with it, under its note.
    while footnotes and footnotes[-1][1] >= cut:
        cut = min(cut, footnotes.pop()[0])
    return [(start, end, FOOTNOTE_NOTE) for start, end in footnotes] + [
        (cut, len(text), DEPTH_NOTE)
    ]
