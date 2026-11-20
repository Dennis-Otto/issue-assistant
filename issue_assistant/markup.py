"""The HTML of an issue as GitHub reads it: the HTML blocks and the paragraphs that
hold a tag or a comment, how far each tag and comment reaches, and the text without
them.

GitHub's renderer hands the rows of an HTML block on as HTML, and a tag or a comment
of a paragraph only where CommonMark reads it as one; its HTML parser then reads the
tags and comments, and its sanitizer drops what they hold. So the text for the
engine loses what GitHub shows nothing of, in time linear in its length.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Callable, Iterator

from .patterns import HTML_COMMENT

# The mark of a quote or of a list item: a `>` needs no space after it, a list mark
# does.
CONTAINER_MARK = r"(?:>|(?:[-+*]|\d{1,9}[.)])(?=[ \t]|$))"
# The start of a row, after a line break as GitHub reads one, and its end.
ROW_START = r"(?:\A|(?<=\n)|(?<=\r)(?!\n))"
ROW_ENDS = r"(?=[\r\n]|\Z)"
# A row whose text starts an HTML block of CommonMark, after the marks of its quotes
# and list items: GitHub hands each row of such a block on as HTML, so that a `<!--`
# in it that no `-->` follows hides the rest of the text, also what follows the
# quote or the list. A tag alone on its row starts a block, which only a paragraph
# above it would keep from it: a tag of a valid name, whose quoted values may hold a
# `<` or a `>`, not an autolink such as <https://example.com>. A footnote holds
# blocks as a list item does.
FOOTNOTE_MARK = r"\[\^[^\]\r\n]++\]:"
BLOCK_TAGS = (
    "address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup"
    "|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame"
    "|frameset|h[1-6]|head|header|hr|html|iframe|legend|li|link|main|menu|menuitem"
    "|nav|noframes|ol|optgroup|option|p|param|search|section|source|summary|table"
    "|tbody|td|tfoot|th|thead|title|tr|track|ul"
)
HTML_ROW = re.compile(
    rf"{ROW_START}(?P<marks>(?:[ \t]*+(?:{CONTAINER_MARK}|{FOOTNOTE_MARK}))*+[ \t]*+)"
    rf"(?:(?P<raw><(?:pre|script|style|textarea)(?=[\s>]|{ROW_ENDS}))"
    r"|(?P<comment><!--)|(?P<processing><\?)|(?P<cdata><!\[CDATA\[)"
    r"|(?P<declaration><![A-Za-z])"
    rf"|(?P<tag></?(?:{BLOCK_TAGS})(?=[\s>]|/>|{ROW_ENDS})"
    r"|</?[A-Za-z][A-Za-z0-9-]*+(?=[\t />])"
    r"(?:[^<>\"'\r\n]|\"[^\"\r\n]*+\"|'[^'\r\n]*+')*+>"
    rf"[ \t]*+{ROW_ENDS}))",
    re.IGNORECASE,
)
# What ends each kind of block but a tag's, which a blank row ends: a row that holds
# it. For a declaration, that is a `>` on its first row, after its start, or one
# after the marks of a later row, which may be those of the quote around it. A
# comment's end is its first `-->`, which block_end finds with str.find: CommonMark
# ends the block only there, also after a `--!>`, at which GitHub's HTML parser ends
# the comment and reads the rest of the block as HTML, where a `<!--` still hides the
# rest of the text.
HTML_ENDS = {
    "raw": re.compile(r"</(?:pre|script|style|textarea)>", re.IGNORECASE),
    "processing": re.compile(r"\?>"),
    "cdata": re.compile(r"\]\]>"),
    "declaration": re.compile(
        rf"{ROW_START}(?:[ \t]*+{CONTAINER_MARK})*+[ \t]*+[^>\r\n]*+>"
    ),
}
# A blank row, or one of no more quotes than the index: it ends a block that starts
# with a tag at the depth of the index. A block deeper in quotes ends only where a
# blank row of the deepest of them would end it.
BLANK_ROWS = tuple(
    re.compile(rf"{ROW_START}(?:[ \t]*>){{0,{depth}}}[ \t]*{ROW_ENDS}")
    for depth in range(4)
)
# A row that starts a list item or a footnote, perhaps in a quote, also an empty
# one.
LIST_ROW = re.compile(
    rf"{ROW_START}(?:[ \t]*+>)*+[ \t]*+"
    rf"(?:(?:[-+*]|\d{{1,9}}[.)])(?:[ \t]|{ROW_ENDS})|{FOOTNOTE_MARK})"
)
COMMENT_START = re.compile("<!--")
COMMENT_END = re.compile("-->")
# Where the reading of the tags takes its next step: at a comment, in which no tag
# is one, or at the start of a tag that a `>` doesn't end before a quote, a `<` or
# the end of its row. The search passes over the others, which HTML takes out as it
# always did, and over comments once no `-->` follows.
TAG_ONLY = re.compile(r"</?[A-Za-z][^<>\"'\r\n]*+(?!>)")
TAG_START = re.compile(rf"(?P<comment><!--)|{TAG_ONLY.pattern}")
# The tags that GitHub shows as text: its filter of tags turns their `<` into `&lt;`,
# wherever they stand.
FILTERED_TAG = re.compile(
    r"</?(?:iframe|noembed|noframes|plaintext|script|style|textarea|title|xmp)"
    r"(?=[\t\n\v\f\r >]|/>)",
    re.IGNORECASE,
)
# A tag as GitHub's HTML parser reads it in an HTML block, a row at a time: its name,
# then its attributes, each a name, perhaps with an `=` and a value, quoted or not,
# up to its `>`, the end of the row, or a quote whose value doesn't end on the row;
# `equals` is a name and an `=` whose value follows later. A name may start with an
# `=`, which gives it no value. A row of a block hands its text on without the marks
# of its quotes, and a value that starts on a later row is a quote or the value
# itself; so is what follows an `=` at the start of a later row, which may follow a
# name of the row above.
TAG_NAME = re.compile(r"</?[A-Za-z][^\t\n\f\r />]*+")
NAME = r"[^\t\n\f\r />][^\t\n\f\r />=]*+"
EQUALS = r"[\t\f ]*+=[\t\f ]*+"
ROW_VALUE = r"(?:\"[^\"\r\n]*+\"|'[^'\r\n]*+'|(?![\"'])[^\t\n\f\r >]++)"
ATTRIBUTES = re.compile(
    rf"(?:[\t\f /]++|{NAME}(?:{EQUALS}{ROW_VALUE}|(?![\t\f ]*+=)))*+"
    rf"(?P<equals>{NAME}{EQUALS})?+"
)
VALUE = re.compile(r"[\t\f ]*+(?:(?P<quote>[\"'])|(?P<value>[^\t\n\f\r >]++))?+")
LEADING_EQUALS = re.compile(r"[\t\f ]*+=")
INERT = re.compile(r"[^\"'=>]*+")
ANY_QUOTE_MARK = re.compile(r"[ \t]*+>")
BLANK_ROWS_AHEAD = re.compile(r"(?:[ \t>]*+(?:\r\n?|\n))*+")
# The marks of list items and footnotes among the marks of a row.
LIST_MARKS = re.compile(rf"(?:[-+*]|\d{{1,9}}[.)])(?=[ \t]|$)|{FOOTNOTE_MARK}")
# Marks of list items alone, of the kinds that start a list item also below a
# paragraph, so that a later row of a block in it needs their indentation.
LIST_ITEMS = re.compile(r"(?:[ \t]*+(?:[-+*]|0*1[.)])(?=[ \t]))++[ \t]*+")
# A tag of a paragraph, which GitHub's renderer hands on as HTML only when it is one
# by CommonMark's rules: a name, attributes of names and values, and a `>`, with
# spaces and line breaks between them, and line breaks in a quoted value; or a
# closing tag, of a name and a `>` with only spaces between them. A later
# row of the paragraph starts with the marks of its quotes, of which it holds no more
# than the paragraph does: a row with more starts a quote of its own, which ends the
# paragraph. A tab before a `>` indents it by four columns, which makes it text.
PARAGRAPH_ROW = re.compile(r"((?: {0,3}>[ \t]?)*+)([^\r\n]*+)(\r\n?|\n)?")
TAG_LINE = r"(?:\r\n?|\n)(?: {0,3}>[ \t]?)*+"
TAG_SPACE = rf"(?:[ \t\v\f]|{TAG_LINE})"
INLINE_TAG = re.compile(
    rf"<[A-Za-z][A-Za-z0-9-]*+(?:{TAG_SPACE}++[A-Za-z_:][A-Za-z0-9_.:-]*+"
    rf"(?:{TAG_SPACE}*+={TAG_SPACE}*+(?:[^ \t\v\f\r\n\"'=<>`]++"
    rf"|'(?:[^'\r\n]|{TAG_LINE})*+'|\"(?:[^\"\r\n]|{TAG_LINE})*+\"))?+)*+{TAG_SPACE}*+/?>"
    rf"|</[A-Za-z][A-Za-z0-9-]*+{TAG_SPACE}*+>"
)
# An HTML tag within one line.
TAG = r"</?[A-Za-z][^>\n]*>"
# An HTML comment, or a tag. When no `>` closes a tag on its line, no later tag on
# that line can close either: the third alternative takes them all in one step, up
# to the next comment, and strip_html keeps them as they are.
HTML = re.compile(
    rf"{HTML_COMMENT.pattern}|{TAG}"
    r"|(?P<open></?[A-Za-z][^>\n<]*(?:<(?!!--)[^>\n<]*)*)",
    re.DOTALL,
)
# The same for the part of a text after its last `-->`, where no comment can end:
# a `<!--` there is text, and a tag start without its `>` takes the rest of its line.
HTML_TAG = re.compile(rf"{TAG}|(?P<open></?[A-Za-z][^>\n]*)")
ROW_END = re.compile(r"(\r\n|\r|\n)")
# The marks of the quotes and list items that hold a row, before its text.
CONTAINER = re.compile(r"(?:[ \t]*(?:>|(?:[-+*]|\d{1,9}[.)])(?=[ \t]|$)))*[ \t]*")


def comment_parts(text: str) -> tuple[str, str]:
    """A text in two parts, with its tags: up to the end of its last `-->`, and what
    GitHub shows of the rest, as html_parts finds them."""
    _, middle, end = html_parts(text)
    return text[: min(middle, end)], text[middle:end]


def html_parts(text: str) -> tuple[list[tuple[int, int]], int, int]:
    """The tags of a text that GitHub reads past their rows, as long_tags finds
    them; the end of its last `-->`; and where GitHub stops showing it.

    Up to the last `-->`, one follows every `<!--`, so a pattern finds the end of each
    comment without reading on to the end of the text. After it, no comment can end:
    a `<!--` in a paragraph stays text, as GitHub shows it, and one in an HTML block
    hides the rest of the text, as GitHub hides it. Neither counts in a tag, where
    GitHub reads it as part of the tag, but the tag stays where it is for the blocks:
    a tag that starts a block, as `<a title=">">` does, keeps the rows below it in
    the block. Only a `<!--` that starts the text of a later row of a tag counts
    still: where it is hard to tell whether the tag goes on in that row, the row may
    start a comment of its own.
    """
    rows = HtmlRows(text)
    spans, end = long_tags(text, rows)
    starts = [start for start, _ in spans]

    def outside(position: int) -> bool:
        index = bisect_right(starts, position) - 1
        return index < 0 or spans[index][1] <= position

    limit = len(text)
    while (last := text.rfind("-->", 0, limit)) >= 0 and not outside(last):
        limit = starts[bisect_right(starts, last) - 1] + 2
    middle = last + 3 if last >= 0 else 0
    comments = (
        comment.start()
        for comment in COMMENT_START.finditer(text, middle)
        if not spans
        or outside(comment.start())
        or row_start(text, comment.start()) is not None
    )
    return spans, middle, min(end, hidden_html(text, middle, Following(comments), rows))


class Following:
    """The first of rising positions at or after a position, for positions that
    never fall, each position taken once."""

    def __init__(self, positions: Iterator[int]) -> None:
        self.positions = positions
        self.current = next(positions, None)

    def __call__(self, position: int) -> int | None:
        while self.current is not None and self.current < position:
            self.current = next(self.positions, None)
        return self.current


class Searches:
    """The first match of a pattern at or after a position, read again only where
    the last search of the pattern can't tell: from rising positions, each pattern
    reads no part of the text twice."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.found: dict[re.Pattern[str], tuple[int, re.Match[str] | None]] = {}

    def __call__(self, pattern: re.Pattern[str], position: int) -> re.Match[str] | None:
        start, match = self.found.get(pattern, (len(self.text) + 1, None))
        if (
            not start
            <= position
            <= (len(self.text) if match is None else match.start())
        ):
            match = pattern.search(self.text, position)
            self.found[pattern] = (position, match)
        return match


class HtmlRows:
    """The rows of a text that may start an HTML block, and where the first list
    item starts, each found once and only when asked for."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.rows: list[re.Match[str]] | None = None
        self.listed: int | None = None

    def found(self) -> list[re.Match[str]]:
        """The rows that may start an HTML block."""
        if self.rows is None:
            self.rows = list(HTML_ROW.finditer(self.text))
        return self.rows

    def lists(self) -> int:
        """Where the first row starts that starts a list item, or the end."""
        if self.listed is None:
            listed = LIST_ROW.search(self.text)
            self.listed = listed.start() if listed else len(self.text)
        return self.listed


class Block:
    """An HTML block that may hold a position: the row that starts it, and the
    latest earlier block of its kind, of which it may be a row if that one hadn't
    ended where it starts."""

    __slots__ = ("earlier", "measures", "row")

    def __init__(self, row: re.Match[str], earlier: Block | None) -> None:
        self.row = row
        self.earlier = earlier
        self.measures: tuple[int, int, str] | None = None

    def chain(self, blocks: Blocks) -> list[Block] | None:
        """It and the earlier blocks that it may be a row of, or None where there are
        more than MOST_ROWS."""
        chain, block = [self], self
        while (earlier := block.earlier) is not None and blocks.end(
            earlier.row, blocks.back
        ) > block.row.start():
            if len(chain) == MOST_ROWS:
                return None
            chain.append(earlier)
            block = earlier
        return chain

    def measured(self) -> tuple[int, int, str]:
        """The marks of its quotes, the columns that its list items need of a later
        row, and how a later row starts that it surely holds; read once."""
        if self.measures is None:
            marks = self.row["marks"]
            indent = len(marks.expandtabs(4)) if LIST_ITEMS.fullmatch(marks) else 0
            self.measures = (marks.count(">"), indent, continuation(marks))
        return self.measures


# How many blocks of a kind, each within the one before, the cleaning tells apart;
# with more, it takes as much out as for any of them.
MOST_ROWS = 16


class Blocks:
    """The HTML blocks of a text that may hold a position, for positions that never
    fall.

    Where it is hard to tell whether a row is in a block, it counts as one: a block
    in a quote or a list item runs on past their end, a block may start in fenced
    code, below a paragraph and within another block, since the block around it may
    have ended with its quote or list item. Only a row indented as code, where no
    list item could hold it, starts none.

    Of the blocks of one kind that start before a position, only the last can hold
    it: one that starts earlier ends no later. So each block is judged once, and
    the searches for the ends of the blocks read no part of the text twice. A block
    that starts within an earlier one of its kind may be a row of it, and keeps the
    marks of both.
    """

    def __init__(self, text: str, rows: HtmlRows) -> None:
        self.text = text
        self.found, self.lists = rows.found(), rows.lists()
        self.index = 0
        self.latest: dict[str | int | None, Block] = {}
        self.ends: dict[int, int] = {}
        # The searches for the ends of earlier blocks, which read back, keep apart
        # from those that read on.
        self.search, self.back = Searches(text), Searches(text)

    def holding(self, position: int) -> list[Block]:
        """The blocks that may hold a position."""
        found, index, latest = self.found, self.index, self.latest
        while index < len(found) and found[index].start() <= position:
            row = found[index]
            if row.start() >= self.lists or not code(row["marks"]):
                kind = row.lastgroup
                depth = min(row["marks"].count(">"), len(BLANK_ROWS) - 1)
                key = kind if kind == "comment" or kind in HTML_ENDS else depth
                latest[key] = Block(row, latest.get(key))
            index += 1
        self.index = index
        for key in [
            key for key, block in latest.items() if self.end(block.row) <= position
        ]:
            del latest[key]
        return list(latest.values())

    def following(self) -> int | None:
        """Where the next block may start."""
        found = self.found
        return found[self.index].start() if self.index < len(found) else None

    def end(self, row: re.Match[str], search: Searches | None = None) -> int:
        """Where the block ends that a row starts: at the start of a blank row, or
        at the end of the row of its end."""
        end = self.ends.get(row.start())
        if end is None:
            end = block_end(self.text, row, search or self.search)
            self.ends[row.start()] = end
        return end


def hidden_html(
    text: str, start: int, first: Callable[[int], int | None], rows: HtmlRows
) -> int:
    """Where GitHub stops showing a text: at the first position from `start` on that
    `first` finds in a row that an HTML block may hold, at the start of its row when
    it starts the text of the row, or at the end of the text."""
    position = first(start)
    blocks = Blocks(text, rows)
    while position is not None:
        if blocks.holding(position):
            return row_or_position(text, position, start)
        following = blocks.following()
        position = None if following is None else first(following)
    return len(text)


def row_or_position(text: str, position: int, start: int = 0) -> int:
    """A position, or the start of its row when what stands there starts the text of
    the row, which takes the marks of the row along; never before `start`."""
    begin = row_start(text, position)
    return position if begin is None else max(begin, start)


def row_start(text: str, position: int) -> int | None:
    """Where the row of a position starts, if what stands there starts the text of
    the row, after the marks of its quotes and list items. Only the characters of
    such marks before the position are read."""
    begin = position
    while begin and text[begin - 1] in MARK_CHARACTERS:
        begin -= 1
    if begin and text[begin - 1] not in "\r\n":
        return None
    return begin if CONTAINER.fullmatch(text, begin, position) else None


MARK_CHARACTERS = frozenset(" \t>-+*.)0123456789")


def block_end(text: str, row: re.Match[str], search: Searches) -> int:
    """Where the HTML block ends that a row starts: at the start of a blank row, or
    at the end of the row of its end."""
    end = row_end(text, row.end())
    if row.lastgroup == "comment":
        found = search(COMMENT_END, row.end("marks"))
        return row_end(text, found.end()) if found else len(text)
    ending = HTML_ENDS.get(row.lastgroup or "")
    if ending is None:
        depth = min(row["marks"].count(">"), len(BLANK_ROWS) - 1)
        found = search(BLANK_ROWS[depth], end)
        return found.start() if found else len(text)
    if row.lastgroup == "declaration":
        if text.find(">", row.end(), end) >= 0:
            return end
        found = search(ending, end)
    else:
        found = search(ending, row.end("marks"))
    return row_end(text, found.end()) if found else len(text)


def row_end(text: str, position: int) -> int:
    """Where the row of a position ends, before its line break."""
    found = ROW_END.search(text, position)
    return found.start() if found else len(text)


def code(marks: str) -> bool:
    """Whether the text of a row after the marks of its quotes, without a list mark,
    is indented code: by four columns or more before the first `>`, or after the
    last and the one blank that it takes, with a tab up to the next fourth column."""
    columns = marks.expandtabs(4)
    lead = len(columns) - len(columns.lstrip(" "))
    quote = columns.rfind(">")
    return lead >= 4 or (quote >= 0 and len(columns) - quote - 1 >= 5)


QUOTES = frozenset("\"'")
GOES_ON = frozenset("\"'\r\n")


def long_tags(
    text: str, rows: HtmlRows | None = None
) -> tuple[list[tuple[int, int]], int]:
    """The tags that GitHub reads past the end of their row, or past a `>` in a
    quoted value, each as the span that it takes of the text; and where GitHub stops
    showing the text: at its end, or at a tag in an HTML block with a quoted value
    that the block doesn't end, since GitHub looks for the end of the value in the
    rest of the text and shows nothing of what it read.

    In a row that an HTML block may hold, GitHub's HTML parser reads a tag up to its
    `>` over the rows of the block, and up to the end of the block when no `>` comes:
    the markup that follows the block ends it, and shows nothing. In a paragraph,
    GitHub's renderer hands a tag on as HTML only where CommonMark reads it as one,
    and otherwise shows it as text, as `<a` above a row that starts a quote. The
    tags that GitHub's filter shows as text, such as `<script`, stay as they are,
    and so do the tags in a comment, and a tag that ends on its row, which HTML
    takes out as it always did.

    Each step reads on from the end of the last: each tag is read once, up to its
    end, and the searches for the ends of the blocks and the paragraphs read no part
    of the text twice.
    """
    rows = rows or HtmlRows(text)
    closes = text.rfind(">")
    if closes < 0 and not rows.found():
        return [], len(text)  # No tag of a paragraph ends, and no block holds one.
    blocks, paragraphs, search = Blocks(text, rows), Paragraphs(text), Searches(text)
    spans: list[tuple[int, int]] = []
    position = 0
    starts = TAG_START
    while (found := starts.search(text, position)) is not None:
        start, position = found.start(), found.end()
        if found.lastgroup == "comment":
            closed = search(COMMENT_END, position)
            if closed is None:
                starts = TAG_ONLY  # No `-->` follows: no comment hides a tag.
            position = closed.end() if closed else position
            continue
        if FILTERED_TAG.match(text, start):
            continue
        held = blocks.holding(start)
        if held:
            name = TAG_NAME.match(text, start)
            assert name is not None  # TAG_START found a tag there.
            stop = row_end(text, name.end())
            after, state = tag_row(text, name.end(), stop, "")
            if state == ">":
                continue
            end = held_tag_end(text, (after, stop, state), held, blocks)
            if end is None:
                return spans, row_or_position(text, start)
        elif text[position : position + 1] not in GOES_ON or start > closes:
            # A tag of a paragraph that meets a `<` or the end of the text before a
            # quote or a line break ends on its row, or is none, and so does one
            # that no `>` follows.
            continue
        elif (end := inline_end(text, start, paragraphs)) is None:
            continue
        spans.append((start, end))
        position = end
    return spans, len(text)


def held_tag_end(
    text: str, first: tuple[int, int, str], held: list[Block], blocks: Blocks
) -> int | None:
    """Where a tag ends that the blocks of `held` may hold, which its first row
    doesn't end: `first` is where that row ends, and the state in which tag_row left
    the tag there. It ends after its `>`, or at the end of the blocks; None where a
    quoted value runs on past the rows that the blocks surely hold, whose end GitHub
    looks for in the rest of the text.

    A later row of a block hands on its text without the marks of the quotes of the
    block, of which the cleaning takes as many as the deepest block has, so that it
    reads no such mark as the end of the tag. A row with fewer marks than even the
    shallowest block has ends the blocks, and so does a row of text indented less
    than the list items of every block, since no HTML block goes on in a row that
    its quote or its list item doesn't hold; and with them the tag. A row is surely
    held where it starts as a row of each block would, with the marks of its quotes
    and the indentation of its list items.

    Where a block ends without the `>`, the tag goes on in an HTML block that follows
    it, which GitHub hands on with only a line break between them, not in a comment,
    which the cleaning takes out as one: it hides at least as much as the tag would.
    The markup of a quote or a list item that starts or ends between them ends the
    tag. In a quote or a list item, where it is hard to tell whether the next block
    is in the same one, the cleaning takes out the rest of the text instead.
    """
    position, stop, state = first
    rows = Rows(text, held, blocks)
    while True:
        if rows.top and state == "":
            # Rows without a quote or an `=` hold only names, up to a `>`.
            names = INERT.match(text, stop, rows.region)
            assert names is not None  # It matches the empty text.
            if text.startswith(">", names.end()):
                return names.end() + 1
            if names.end() == rows.region:
                stop = rows.region
        line = ROW_END.match(text, stop)
        if line is None or line.end() >= rows.region:
            if state in QUOTES:
                return None
            following = next_html(text, rows.region)
            if (
                following is None
                or following.lastgroup == "comment"
                or rows.outside(following.start())[1]
                or (rows.top and following["marks"].strip(" \t"))
            ):
                return rows.region
            if not rows.top:
                return None
            # At the top of the text, the next block asks the same of its rows.
            rows.region = blocks.end(following)
            position = following.end("marks")
            if state == "":
                names = INERT.match(text, position, rows.region)
                assert names is not None  # It matches the empty text.
                if text.startswith(">", names.end()):
                    return names.end() + 1
                if names.end() == rows.region:
                    stop = rows.region
                    continue
        else:
            position = line.end()
            if state in QUOTES and not rows.surely_held(position):
                return None
            position, outside = rows.outside(position)
            if outside:
                return None if state in QUOTES else stop
            if state == "" and (equals := LEADING_EQUALS.match(text, position)):
                position, state = equals.end(), "="
        stop = row_end(text, position)
        position, state = tag_row(text, position, min(stop, rows.region), state)
        if state == ">":
            return position


class Rows:
    """What the blocks that hold a tag ask of its later rows."""

    def __init__(self, text: str, held: list[Block], blocks: Blocks) -> None:
        self.text = text
        self.region = max(blocks.end(block.row) for block in held)
        chains = [block.chain(blocks) for block in held]
        if None in chains:
            # Too many blocks to tell apart: every mark of a quote goes, no row
            # counts as outside or surely held, and the next block as the same.
            self.depth, self.shallow, self.indent = len(text), 0, 0
            self.starts: tuple[str, ...] | None = None
        else:
            measures = [
                block.measured() for chain in chains if chain for block in chain
            ]
            depths = [depth for depth, _, _ in measures]
            self.depth, self.shallow = max(depths), min(depths)
            self.indent = min(indent for _, indent, _ in measures)
            self.starts = tuple(dict.fromkeys(start for _, _, start in measures))
        self.top = self.depth == 0 and self.starts == ("",)

    def surely_held(self, row: int) -> bool:
        """Whether a row starts as a row of each block would."""
        return self.starts is not None and all(
            self.text.startswith(start, row) for start in self.starts
        )

    def outside(self, row: int) -> tuple[int, bool]:
        """Where the text of a row starts after the marks of the quotes of the
        blocks, and whether the row is outside all of them."""
        if not self.depth and not self.indent:
            return row, False
        position, marks = quote_marks(self.text, row, self.depth)
        return position, marks < self.shallow or outdented(
            self.text, position, self.indent
        )


def quote_marks(text: str, position: int, depth: int) -> tuple[int, int]:
    """Where the text of a row starts after at most `depth` marks of quotes, at any
    indentation, and how many it took."""
    for marks in range(depth):
        mark = ANY_QUOTE_MARK.match(text, position)
        if mark is None:
            return position, marks
        position = mark.end()
    return position, depth


def outdented(text: str, position: int, indent: int) -> bool:
    """Whether the row from a position holds text indented by fewer columns than
    `indent`, so that it is in no list item that needs that many."""
    columns = text[position : row_end(text, position)].expandtabs(4)
    text_of_row = columns.lstrip(" ")
    return bool(text_of_row) and len(columns) - len(text_of_row) < indent


def tag_row(text: str, position: int, stop: int, state: str) -> tuple[int, str]:
    """How far a row takes a tag from a position, in the state that the rows before
    left: `""` between attributes, `=` before a value, or the quote of a value. It
    ends after the `>` of the tag, in the state `>`, or at the end of the row, in the
    state in which the next row goes on."""
    while True:
        if state in QUOTES:
            closed = text.find(state, position, stop)
            if closed < 0:
                return stop, state
            position, state = closed + 1, ""
        elif state == "=":
            value = VALUE.match(text, position, stop)
            assert value is not None  # It matches the empty text.
            position = value.end()
            if value["quote"]:
                state = value["quote"]
                continue
            if value["value"] is None and position == stop:
                return stop, state
        found = ATTRIBUTES.match(text, position, stop)
        assert found is not None  # It matches the empty text.
        position = found.end()
        if position == stop:
            return stop, "" if found["equals"] is None else "="
        if text[position] == ">":
            return position + 1, ">"
        state, position = text[position], position + 1


def next_html(text: str, position: int) -> re.Match[str] | None:
    """The row that starts an HTML block after the blank rows from a position, if
    one does: GitHub hands both blocks on as HTML, with only a line break between
    them, so that a tag that the first doesn't end goes on in the second."""
    blank = BLANK_ROWS_AHEAD.match(text, position)
    assert blank is not None  # It matches the empty text.
    return HTML_ROW.match(text, blank.end())


def inline_end(text: str, start: int, paragraphs: Paragraphs) -> int | None:
    """Where a tag ends that a paragraph holds, if GitHub's renderer hands it on as
    HTML and it runs past its row. The end of the paragraph is looked for only for
    a tag that is one without it: one that runs past that end is none."""
    found = INLINE_TAG.match(text, start)
    if found is None or not ROW_END.search(text, start, found.end()):
        return None
    return found.end() if found.end() <= paragraphs.end(start) else None


def continuation(marks: str) -> str:
    """How a later row of a block starts that the block surely holds: with the marks
    of its quotes, and with spaces for the list items, as wide as their marks."""
    prefix = LIST_MARKS.sub(lambda mark: " " * len(mark[0]), marks)
    if ">" in marks and not LIST_MARKS.search(marks, marks.rfind(">")):
        return prefix.rstrip(" \t")
    return prefix


class Paragraphs:
    """Where the paragraph that may hold a position ends, for positions that never
    fall: at a blank row, or at a row with more marks of quotes than any row of the
    paragraph above it, which starts a quote of its own. The depth of the quotes
    counts from the last blank row, which ends the paragraph before it, and may be
    deeper than that of the paragraph, which only ends it later.

    Each row is read once for the depth, and once more for the end: from rising
    positions, the end of a paragraph is the end found for the last position until
    the depth changes, which only a row at or after that end can change."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.read = 0
        self.depth = 0
        self.found = (-1, 0, 0)

    def end(self, position: int) -> int:
        """Where the paragraph that may hold a position ends."""
        while self.read <= position:
            blank, depth, following = self.row(self.read)
            self.depth = 0 if blank else max(self.depth, depth)
            self.read = following
        depth, start, end = self.found
        if not (depth == self.depth and start <= position < end):
            end = self.read
            while end <= len(self.text):
                blank, depth, following = self.row(end)
                if blank or depth > self.depth:
                    break
                end = following
            self.found = (self.depth, position, end)
        return min(end, len(self.text))

    def row(self, start: int) -> tuple[bool, int, int]:
        """Whether a row is blank but for the marks of its quotes, how many it has,
        and where the next row starts, or a position after the end of the text."""
        row = PARAGRAPH_ROW.match(self.text, start)
        assert row is not None  # It matches the empty text.
        following = row.end() if row[3] else len(self.text) + 1
        return not row[2].strip(" \t"), row[1].count(">"), following


def strip_html(text: str) -> str:
    """The text as GitHub shows it, without HTML comments and tags, in time linear
    in its length: first the tags that run past their rows, with what they hold."""
    spans, middle, end = html_parts(text)
    comments = without(text, spans, 0, min(middle, end))
    rest = without(text, spans, middle, end)
    return HTML.sub(open_tag, comments) + HTML_TAG.sub(open_tag, rest)


def without(text: str, spans: list[tuple[int, int]], start: int, stop: int) -> str:
    """The part of a text from `start` to `stop`, without the spans."""
    starts = [start, *(end for _, end in spans)]
    stops = [*(begin for begin, _ in spans), len(text)]
    return "".join(
        text[max(begin, start) : min(end, stop)]
        for begin, end in zip(starts, stops, strict=True)
        if begin < stop and end > start
    )


def open_tag(match: re.Match[str]) -> str:
    """What stays of an HTML match: a tag start that no `>` closes, or nothing."""
    return match["open"] or ""
