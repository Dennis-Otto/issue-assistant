"""The HTML of an issue as GitHub reads it: the HTML blocks and the paragraphs that
hold a tag, a comment or markup that GitHub's HTML parser reads as a comment, such
as an instruction, and how far each reaches; tags.py reads the tags with them.

GitHub's renderer hands the rows of an HTML block on as HTML, and a tag, a comment,
an instruction, a declaration or a section of CDATA of a paragraph only where
CommonMark reads it as one; its HTML parser then reads them, and its sanitizer
drops what they hold. So the text for the engine loses what GitHub shows nothing
of, in time linear in its length.
"""

from __future__ import annotations

import functools
import re
from bisect import bisect_right
from collections.abc import Callable, Iterator

from .patterns import (
    BANG_END_FROM,
    COMMENT_END_FROM,
    DASHED_END_FROM,
    HTML_COMMENT,
    TAG_START,
    comment_close,
    plain_comment_close,
)

# The mark of a quote or of a list item: a `>` needs no space after it, a list mark
# does.
CONTAINER_MARK = r"(?:>|(?:[-+*]|\d{1,9}[.)])(?=[ \t]|$))"
# The start of a row, after a line break as GitHub reads one, and its end. The
# start is where no character but a line break precedes, other than between the
# `\r` and the `\n` of one: a single test that fails at once in the middle of a
# row, where a search spends most of its time.
ROW_START = r"(?<![^\r\n])(?!(?<=\r)\n)"
ROW_ENDS = r"(?=[\r\n]|\Z)"
# A row whose text starts an HTML block of CommonMark, after the marks of its quotes
# and list items: GitHub hands each row of such a block on as HTML, so that a `<!--`
# in it that no `-->` follows hides the rest of the text, also what follows the
# quote or the list. A tag alone on its row starts a block, which only a paragraph
# above it would keep from it: a tag of a valid name, whose quoted values may hold a
# `<` or a `>`, not an autolink such as <https://example.com>. A footnote holds
# blocks as a list item does.
FOOTNOTE_MARK = r"\[\^[^\]\r\n]++\]:"
# The names of CommonMark's block tags, by their first letter, so that a row tries
# only those of its letter: one alternative for each name takes most of the time of
# a text of many rows of tags. The `>`, `/>` or blank that follows a name in
# HTML_ROW lets no name end within a longer one, so their order counts for nothing.
BLOCK_TAGS = (
    "a(?:ddress|rticle|side)|b(?:ase|asefont|lockquote|ody)"
    "|c(?:aption|enter|ol|olgroup)|d(?:d|etails|ialog|ir|iv|l|t)"
    "|f(?:ieldset|igcaption|igure|ooter|orm|rame|rameset)|h(?:[1-6]|ead|eader|r|tml)"
    "|iframe|l(?:egend|i|ink)|m(?:ain|enu|enuitem)|n(?:av|oframes)"
    "|o(?:l|ptgroup|ption)|p(?:aram)?|s(?:earch|ection|ource|ummary)"
    "|t(?:able|body|d|foot|h|head|itle|r|rack)|ul"
)
# The characters that a letter matches where a pattern ignores case: its capital and
# its small letter, and for some a letter of another script. HTML_ROW matches them
# as it did with the flag IGNORECASE, without which a row takes about half the time:
# no step of a name looks up the case of a character.
FOLDS = {"i": "\u0130\u0131", "k": "\u212a", "s": "\u017f"}
LETTER = f"[A-Za-z{''.join(FOLDS.values())}]"
TAG_NAME_CHARACTER = f"[A-Za-z0-9{''.join(FOLDS.values())}-]"


def caseless(letters: str) -> str:
    """A pattern of small letters, digits and groups, which matches what it matched
    with the flag IGNORECASE."""
    return re.sub(
        "[a-z]",
        lambda letter: f"[{letter[0].upper()}{letter[0]}{FOLDS.get(letter[0], '')}]",
        letters,
    )


# Every kind starts with a `<`, after marks without a line break: a row without one,
# such as a blank row, fails at once, before its marks are read.
HOLDS_A_TAG_START = r"(?=[^\r\n<]*+<)"
HTML_ROW = re.compile(
    rf"{ROW_START}{HOLDS_A_TAG_START}"
    rf"(?P<marks>(?:[ \t]*+(?:{CONTAINER_MARK}|{FOOTNOTE_MARK}))*+[ \t]*+)"
    rf"(?:(?P<raw><(?:{caseless('pre|script|style|textarea')})(?=[\s>]|{ROW_ENDS}))"
    rf"|(?P<comment><!--)|(?P<processing><\?)|(?P<cdata><!\[{caseless('cdata')}\[)"
    rf"|(?P<declaration><!{LETTER})"
    rf"|(?P<tag></?(?:{caseless(BLOCK_TAGS)})(?=[\s>]|/>|{ROW_ENDS})"
    rf"|</?{LETTER}{TAG_NAME_CHARACTER}*+(?=[\t />])"
    r"(?:[^<>\"'\r\n]|\"[^\"\r\n]*+\"|'[^'\r\n]*+')*+>"
    rf"[ \t]*+{ROW_ENDS}))"
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
# Where the reading of the tags takes its next step: at a comment, in which no tag
# is one; at markup that GitHub's HTML parser reads as a comment up to its first
# `>`: an instruction, `<?`, a declaration or a section of CDATA, `<!` but for a
# comment, and `</` without a name; or at the start of a tag that a `>` doesn't end
# before a quote, a `<` or the end of its row. The search passes over the other
# tags, which HTML takes out as it always did, and over comments once no `-->`
# follows.
# Each pattern reads the character after the `<` once: a `<!--` passes at once.
MARKUP_REST = r"(?P<markup>\?|!|/(?![A-Za-z>]))"
TAG_REST = r"/?[A-Za-z][^<>\"'\r\n]*+(?!>)"
# An instruction, a declaration or a section of CDATA whose first `>` ends it, on
# its row and before any `<`: CommonMark hands it on as HTML wherever it stands, in a
# paragraph as in a block, and GitHub shows nothing of it. Outside fenced code it
# goes with the comments and tags of a row, by a pattern, and the reading of the
# tags passes over it.
ROW_MARKUP_REST = (
    r"(?:\?(?:[^<>?\r\n]|\?(?!>))*+\?>|!(?:[A-Za-z][^<>\r\n]*+>"
    r"|\[CDATA\[(?:[^<>\]\r\n]|\](?!\]>))*+\]\]>))"
)
# A `<!--`, of which a text may hold thousands, fails at once.
ROW_MARKUP = f"<(?!!-){ROW_MARKUP_REST}"

# The markup of a paragraph that CommonMark hands on as HTML, where its end follows
# in the paragraph: an instruction up to `?>`, a section of CDATA up to `]]>`, and a
# declaration, `<!` and a letter, up to its `>`. GitHub's renderer reads a
# declaration only with a capital letter, the cleaning with any, as CommonMark has
# since its version 0.30: a newer renderer would hide it.
INLINE_MARKUP = re.compile(r"<(?:(?P<processing>\?)|(?P<cdata>!\[CDATA\[)|![A-Za-z])")
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
# A row blank but for the marks of its quotes, which ends a paragraph.
BLANK_ROW = re.compile(rf"{ROW_START}[ \t>]*+{ROW_ENDS}")
# Reading in bulk what held_tag_end would read a row at a time, where each row leaves
# a tag between its attributes as it was: a row without a quote, an `=` or a `>`
# after the marks of its quotes, of which it has as many as the blocks allow; and at
# the top of the text, the blocks of block tags that follow each other, each after
# the blank rows that end the one before, with no quote, `=` or `>` in their rows.
# Wherever a row holds more, the reading goes on a row at a time.
LINE_BREAK = r"(?>\r\n|\r|\n)"
INERT_ROW = r"[^\"'=>\r\n]*+"
TAG_BLOCKS = re.compile(
    rf"(?:(?:[ \t>]*+{LINE_BREAK})*+[ \t]*+"
    rf"(?=</?(?:{caseless(BLOCK_TAGS)})(?=[\s>]|/>|{ROW_ENDS}))"
    rf"{INERT_ROW}(?:{LINE_BREAK}(?![ \t]*+{ROW_ENDS}){INERT_ROW})*+"
    rf"(?:{LINE_BREAK}(?=[ \t]*+{ROW_ENDS})|\Z))*+"
)


# The start of a tag of an HTML block that may interrupt a paragraph, after the
# marks of its row: of CommonMark's kind 6, the block tags.
BLOCK_TAG_START = re.compile(rf"</?(?:{caseless(BLOCK_TAGS)})(?=[\s>]|/>|{ROW_ENDS})")
# The text of a row that starts a link reference definition or a footnote, for which
# GitHub writes nothing where it stands, or may start one: the rows of the next
# block after it count with it.
DEFINITION_ROW = re.compile(r" {0,3}+\[(?:[^\\\[\]\r\n]|\\.)++\]:")
# The text of a row that starts a block whose element holds the paragraphs in it, or
# that holds none: a quote, a list item, a fence or a rule.
OWN_ELEMENT = re.compile(
    rf"[ \t]*+(?:>|(?:[-+*]|\d{{1,9}}[.)])(?:[ \t]|{ROW_ENDS}))"
    rf"| {{0,3}}+(?:```|~~~|(?:(?:\*[ \t]*+){{3,}}|(?:-[ \t]*+){{3,}}"
    rf"|(?:_[ \t]*+){{3,}}){ROW_ENDS})"
)


# The depths come from the text, which an outsider writes: the cache keeps the
# patterns of the latest pairs, not one for every pair that a text can name.
@functools.lru_cache(maxsize=64)
def inert_rows(shallow: int, depth: int) -> re.Pattern[str]:
    """The later rows of a tag in quotes that leave it between its attributes: each
    with at least `shallow` and at most `depth` marks of quotes, as quote_marks reads
    them, and nothing after them that could start a value or end the tag."""
    return re.compile(
        rf"(?:{LINE_BREAK}(?:[ \t]*+>){{{shallow},{depth}}}+{INERT_ROW}{ROW_ENDS})*+"
    )


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
# Both, also for the markup of a row, in a text without fenced code.
HTML_AND_MARKUP = re.compile(f"{ROW_MARKUP}|{HTML.pattern}", re.DOTALL)
HTML_TAG_AND_MARKUP = re.compile(f"{ROW_MARKUP}|{HTML_TAG.pattern}")
ROW_END = re.compile(r"(\r\n|\r|\n)")
# The marks of the quotes and list items that hold a row, before its text.
CONTAINER = re.compile(r"(?:[ \t]*(?:>|(?:[-+*]|\d{1,9}[.)])(?=[ \t]|$)))*[ \t]*")


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
    reads no part of the text twice. The same for the end of a comment, `-->`, which
    is found as a string: CommonMark ends a comment's block only there."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.found: dict[re.Pattern[str], tuple[int, re.Match[str] | None]] = {}
        self.comment = (len(text) + 1, -1)
        self.closing = (len(text) + 1, -1)
        self.plain = (len(text) + 1, -1)

    def comment_end(self, opening: int) -> int:
        """Where the comment ends whose `<!--` starts at a position, after the `-->`
        that comment_close finds, or -1."""
        start, found = self.comment
        if (
            not start
            <= opening
            <= (len(self.text) if found < 0 else found - COMMENT_END_FROM)
        ):
            found = comment_close(self.text, opening)
            self.comment = (opening, found)
        return found + 3 if found >= 0 else -1

    def plain_close(self, position: int) -> int:
        """Where the first `-->` at or after a position starts that no `-` comes
        before, as plain_comment_close finds it, or -1."""
        start, found = self.plain
        if not start <= position <= (len(self.text) if found < 0 else found):
            found = plain_comment_close(self.text, position)
            self.plain = (position, found)
        return found

    def close(self, position: int) -> int:
        """Where the first `>` at or after a position ends that is no mark of a quote
        at the start of a later row, or -1."""
        start, found = self.closing
        if not start <= position <= (len(self.text) if found < 0 else found):
            found = first_close(self.text, position)
            self.closing = (position, found)
        return found + 1 if found >= 0 else -1

    def row_end(self, position: int) -> int:
        """Where the row of a position ends, before its line break: the rows of
        several tags on one long row are read once, not once for each tag."""
        found = self(ROW_END, position)
        return found.start() if found else len(self.text)

    def __call__(self, pattern: re.Pattern[str], position: int) -> re.Match[str] | None:
        found = self.found.get(pattern)
        if found is not None:
            start, match = found
            if (
                start
                <= position
                <= (len(self.text) if match is None else match.start())
            ):
                return match
        match = pattern.search(self.text, position)
        self.found[pattern] = (position, match)
        return match


class HtmlRows:
    """The rows of a text that may start an HTML block, and where the first list
    item starts, each found once and only when asked for; and where each block
    ends, which every reading of the blocks of the text shares."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.rows: list[re.Match[str]] | None = None
        self.starts: list[int] = []
        self.listed: int | None = None
        self.ends: dict[int, int] = {}
        self.fences: bool | None = None
        self.judged: list[str | int | None] = []
        # Where the spans that long_tags finds for the content of a hidden element
        # start, in which no comment counts.
        self.hidden: set[int] = set()
        # Those of them that start what a void element holds inside `<svg>` or
        # `<math>`, in whose place the page shows the element.
        self.spaced: set[int] = set()
        self.own: list[int] | None = None

    def fenced(self) -> bool:
        """Whether a row may open fenced code: only where three backticks or three
        tildes follow each other."""
        if self.fences is None:
            self.fences = "```" in self.text or "~~~" in self.text
        return self.fences

    def found(self) -> list[re.Match[str]]:
        """The rows that may start an HTML block."""
        if self.rows is None:
            self.rows = list(HTML_ROW.finditer(self.text))
            self.starts = [row.start() for row in self.rows]
        return self.rows

    def owned(self) -> list[int]:
        """Where the rows start that may start an HTML block of a kind with an end
        of its own, such as a comment or a declaration."""
        if self.own is None:
            self.own = [
                row.start()
                for row in self.found()
                if row.lastgroup not in (None, "tag")
            ]
        return self.own

    def lists(self) -> int:
        """Where the first row starts that starts a list item, or the end."""
        if self.listed is None:
            listed = LIST_ROW.search(self.text)
            self.listed = listed.start() if listed else len(self.text)
        return self.listed

    def judge(self, stop: int) -> None:
        """Reads the kind of the block that each row up to the index `stop` starts, by
        which a later block of its kind may be a row of it: a comment's or that of a
        kind with an end of its own, or for a tag the depth of its quotes, of which
        the cleaning tells as many apart as BLANK_ROWS; None for a row indented as
        code, where no list item could hold it, which starts none. Each row is read
        once for the text, not once for each reading of its blocks, and at least
        twice as many as the last time: the readings ask for a few rows at a time.
        The kinds are in `judged`."""
        kinds, found, starts = self.judged, self.found(), self.starts
        for index in range(len(kinds), min(len(found), max(stop, 2 * len(kinds)))):
            row = found[index]
            # A row without marks is never indented code, and only for one that is,
            # the start of the first list item is looked for.
            marks = row["marks"]
            if marks and code(marks) and starts[index] < self.lists():
                kinds.append(None)
                continue
            kind = row.lastgroup
            kinds.append(
                kind
                if kind == "comment" or kind in HTML_ENDS
                else min(marks.count(">"), len(BLANK_ROWS) - 1)
            )


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
    code that `fences` doesn't surely find, below a paragraph and within another
    block, since the block around it may have ended with its quote or list item.
    Only a row indented as code, where no list item could hold it, and a row of the
    fenced code that `fences` finds start none.

    Of the blocks of one kind that start before a position, only the last can hold
    it: one that starts earlier ends no later. So each block is judged once, and
    the searches for the ends of the blocks read no part of the text twice. A block
    that starts within an earlier one of its kind may be a row of it, and keeps the
    marks of both.
    """

    def __init__(self, text: str, rows: HtmlRows, fences: Fences | None = None) -> None:
        self.text = text
        self.rows, self.fences = rows, fences
        self.found = rows.found()
        self.starts = rows.starts
        self.index = 0
        # The last block of each kind that may hold a later position, and its end.
        self.latest: dict[str | int | None, tuple[Block, int]] = {}
        self.ends = rows.ends
        # The searches for the ends of earlier blocks, which read back, keep apart
        # from those that read on.
        self.search, self.back = Searches(text), Searches(text)
        # Where the last block starts and ends whose element a tag or markup that
        # its HTML block ends without a `>` takes, as take reads it, and up to where
        # it read.
        self.taken, self.handed = (-1, -1), -1
        self.paragraphs: Paragraphs | None = None

    def holding(self, position: int) -> list[Block]:
        """The blocks that may hold a position."""
        index, latest, starts = self.index, self.latest, self.starts
        if index < len(starts) and starts[index] <= position:
            # The rows that start up to the position, found in one search.
            stop = bisect_right(starts, position, index)
            self.index = stop
            found, fences, kinds = self.found, self.fences, self.rows.judged
            if len(kinds) < stop:
                self.rows.judge(stop)
            started = [
                next_row
                for next_row in range(index, stop)
                if kinds[next_row] is not None
                and (fences is None or not fences.holds(starts[next_row]))
            ]
            # Of the blocks of a kind that start before the position, the last ends
            # last: where it ends before the position, so do the others, and the
            # latest of the kind goes, without a block made for any of them. Each
            # block of a kind that goes on is made, within the one before it, and
            # the last stays the latest, with its end.
            ends = self.ends
            for key, last in {kinds[row]: row for row in started}.items():
                end = ends.get(starts[last])
                if end is None and isinstance(key, int):
                    # The block of a tag, which a blank row of its depth ends, as
                    # block_end finds it, without reading the row again.
                    blank = self.search(BLANK_ROWS[key], found[last].end())
                    end = ends[starts[last]] = (
                        blank.start() if blank else len(self.text)
                    )
                elif end is None:
                    end = self.end(found[last])
                if end <= position:
                    latest.pop(key, None)
                    continue
                earlier = latest[key][0] if key in latest else None
                for next_row in started:
                    if kinds[next_row] == key:
                        earlier = Block(found[next_row], earlier)
                assert earlier is not None  # The last of the kind is among them.
                latest[key] = (earlier, end)
        if not latest:
            return []  # No block holds the position, nor has since the last ended.
        # Each block of `latest` is the last of its kind, whose end is known.
        for key in [key for key, (_, end) in latest.items() if end <= position]:
            del latest[key]
        return [block for block, _ in latest.values()]

    def following(self) -> int | None:
        """Where the next block may start."""
        starts = self.starts
        return starts[self.index] if self.index < len(starts) else None

    def end(self, row: re.Match[str], search: Searches | None = None) -> int:
        """Where the block ends that a row starts: at the start of a blank row, or
        at the end of the row of its end; found once for the text."""
        end = self.ends.get(row.start())
        if end is None:
            end = block_end(self.text, row, search or self.search)
            self.ends[row.start()] = end
        return end

    def take(self, region: int, rows: Rows) -> None:
        """Hands on the block that follows the end of the blocks of `rows`, at
        `region`, where a tag or markup reaches that end without its `>`: GitHub's
        renderer writes the start tag of that block right after it, and its HTML
        parser reads that tag into it, up to the `>`. So a paragraph, a heading or
        a table there has no element of its own (next_block), as a paragraph of a
        tight list item has none: lost says so for each position of it. The
        blocks are read from rising ends, each row once; past the first list item,
        every paragraph reads as one without its element already."""
        if region <= self.handed or region >= self.rows.lists():
            return
        self.handed = region
        found = next_block(self.text, region, rows)
        if found is not None:
            self.paragraphs = self.paragraphs or Paragraphs(self.text)
            self.taken = (found[0], self.paragraphs.end(found[1]))

    def lost(self, position: int) -> bool:
        """Whether the paragraph, the heading or the table that holds a position has
        no element of its own, as take found it."""
        start, end = self.taken
        return start <= position < end


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
    if row.lastgroup == "comment":
        closed = search.comment_end(row.end("marks"))
        return row_end(text, closed) if closed >= 0 else len(text)
    ending = HTML_ENDS.get(row.lastgroup or "")
    if ending is None:
        # No row starts before the end of the row, so the search starts at once.
        depth = min(row["marks"].count(">"), len(BLANK_ROWS) - 1)
        found = search(BLANK_ROWS[depth], row.end())
        return found.start() if found else len(text)
    end = row_end(text, row.end())
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


def row_before(text: str, start: int, position: int) -> int:
    """Where the last row ends, before its line break, that ends between `start`, the
    end of a row, and a position on a later row or the same; `start` where none
    does."""
    found = max(text.rfind("\n", start, position), text.rfind("\r", start, position))
    if found > start and text.startswith("\r\n", found - 1):
        return found - 1
    return max(found, start)


def code(marks: str) -> bool:
    """Whether the text of a row after the marks of its quotes, without a list mark,
    is indented code: by four columns or more before the first `>`, or after the
    last and the one blank that it takes, with a tab up to the next fourth column."""
    columns = marks.expandtabs(4)
    lead = len(columns) - len(columns.lstrip(" "))
    quote = columns.rfind(">")
    return lead >= 4 or (quote >= 0 and len(columns) - quote - 1 >= 5)


QUOTES = frozenset("\"'")
QUOTE_MARK = re.compile("[\"']")
GOES_ON = frozenset("\"'\r\n")


# A row that may open or close fenced code: three backticks or tildes or more, after
# any marks of quotes, list items and footnotes and any blanks. Fenced code shows
# its rows as they are, also a tag that runs over them.
FENCE_ROW = re.compile(
    rf"{ROW_START}(?P<marks>(?:[ \t]*+(?:{CONTAINER_MARK}|{FOOTNOTE_MARK}))*+)"
    r"(?P<indent>[ \t]*+)(?P<fence>`{3,}|~{3,})(?P<info>[^\r\n]*+)"
)
# The marks of a row, each with the blanks before it.
SPACED_MARK = re.compile(r"( *+)(>|[-+*]|\d{1,9}[.)])")
# A row of nothing but a fence of backticks or tildes and blanks: the only rows
# that can end fenced code whose rows need no marks and no indentation.
CLOSING_ROWS = {
    character: re.compile(
        rf"{ROW_START}(?P<blanks>[ \t]*+)(?P<fence>{character}{{3,}}+)[ \t]*+{ROW_ENDS}"
    )
    for character in ("`", "~")
}


class Fences:
    """The fenced code of a text, for positions that never fall: from the row that
    surely opens it to the row that closes it, or to the end of the text.

    Only what surely is fenced code counts: the rows below the fence that start
    with the marks of its quotes and with spaces as wide as its list items and its
    indentation, or that are blank in its quotes, up to a fence of that start, its
    character and at least its length. Where that is hard to tell, as for a row of
    another start, a fence with tabs or with more blanks before it, in a footnote, or
    in a row that an HTML block may hold, a row could open or close fenced code in
    GitHub's reading and not in this one: so no later fence counts, and the tags
    there are read as GitHub reads them outside code. Each row is read once, and a
    position before the last one read is looked up among the fenced code found.
    """

    def __init__(self, text: str, rows: HtmlRows) -> None:
        self.text = text
        self.blocks = Blocks(text, rows, self)
        self.starts: list[int] = []
        self.ends: list[int] = []
        # Whether each surely ends where `ends` says, or may go on after it.
        self.clear: list[bool] = []
        self.after = 0
        self.pending: re.Match[str] | None = None
        # Without three backticks or tildes in a row, no row opens fenced code.
        self.done = not rows.fenced()
        # Up to where the reading is sure, once it has read that far: the start of
        # the row that it can't tell, or the end of fenced code that may go on.
        self.sure = len(text)

    def holds(self, position: int) -> bool:
        """Whether fenced code surely holds a position."""
        while not self.done and self.after <= position:
            row = self.pending or FENCE_ROW.search(self.text, self.after)
            if row is None or row.start() > position:
                self.pending, self.done = row, row is None
                break
            # The blocks that may hold the row ask for the rows before it.
            self.pending, self.after = None, row.end()
            opens = self.opens(row)
            if opens is None:
                self.done, self.sure = True, row.start()
            elif opens:
                end, clear = self.closed(row)
                self.starts.append(row.start())
                self.ends.append(end)
                self.clear.append(clear)
                self.after, self.done = end, not clear
                if not clear:
                    self.sure = end
        index = bisect_right(self.starts, position) - 1
        return index >= 0 and position < self.ends[index]

    def block(self, position: int) -> tuple[int, int, bool] | None:
        """Where the fenced code starts and ends that surely holds a position, and
        whether it surely ends there; None where none surely holds the position."""
        if not self.holds(position):
            return None
        index = bisect_right(self.starts, position) - 1
        return self.starts[index], self.ends[index], self.clear[index]

    def opens(self, row: re.Match[str]) -> bool | None:
        """Whether a row opens fenced code, or None where that is hard to tell. A
        backtick after a fence of backticks makes it none: it starts code of a row."""
        if row["fence"][0] == "`" and "`" in row["info"]:
            return False
        lead = row["marks"] + row["indent"]
        if "\t" in lead or "[" in lead or self.blocks.holding(row.start()):
            return None
        mark = ""
        for found in SPACED_MARK.finditer(row["marks"]):
            if not blanks_fit(mark, found[1]):
                return None
            mark = found[2]
        return True if blanks_fit(mark, row["indent"]) else None

    def closed(self, row: re.Match[str]) -> tuple[int, bool]:
        """Where the fenced code ends that a row opens, and whether it surely ends
        there: after its closing row, or before a row that it may not hold.

        Above the first list item, only the marks of its quotes start a row of the
        code, and a fence closes it after as many blanks as one may open; in a list
        item, a row starts as wide as the fence does, and the fence that closes it
        right there."""
        text, fence = self.text, row["fence"]
        marked = LIST_MARKS.search(row["marks"])
        listed = marked is not None or row.start() >= self.blocks.rows.lists()
        prefix = (
            LIST_MARKS.sub(lambda mark: " " * len(mark[0]), row["marks"])
            + row["indent"]
            if listed
            else row["marks"]
        )
        position = row_end(text, row.end())
        if not prefix:
            return closing_row(text, position, fence, listed)
        quotes = prefix[: prefix.rfind(">") + 1]
        while (following := ROW_END.match(text, position)) is not None:
            start = following.end()
            position = row_end(text, start)
            line = text[start:position]
            if not line.startswith(prefix):
                if line.rstrip(" \t") == quotes:
                    continue
                # A row that starts with less and in no quote ends the quotes and
                # the list item, unless the fence may be outside a list item that
                # its blanks seem to put it in.
                first = line.lstrip(" ")[:1]
                return start, (not first or first not in ">\t") and (
                    not listed or marked is not None
                )
            rest = line[len(prefix) :]
            body = rest.lstrip(" \t")
            closing = body.rstrip(" \t")
            if len(closing) >= len(fence) and not closing.strip(fence[0]):
                blanks = rest[: len(rest) - len(body)]
                if not blanks:
                    return position, True
                if listed or "\t" in blanks:
                    return start, False
                if blanks_fit(prefix[-1:], blanks):
                    return position, True
        return len(text), True


def closing_row(text: str, position: int, fence: str, listed: bool) -> tuple[int, bool]:
    """What Fences.closed finds for fenced code whose rows start with nothing, from
    the end of the row of its fence on: every row starts as one of the code, so
    only a row of a fence may end it, which one search finds instead of a reading of
    each row."""
    rows = CLOSING_ROWS[fence[0]]
    while (found := rows.search(text, position)) is not None:
        position = found.end()
        if len(found["fence"]) < len(fence):
            continue
        blanks = found["blanks"]
        if not blanks:
            return position, True
        if listed or "\t" in blanks:
            return found.start(), False
        if blanks_fit("", blanks):
            return position, True
    return len(text), True


def blanks_fit(mark: str, blanks: str) -> bool:
    """Whether the blanks after a mark, or at the start of a row, leave what follows
    them outside indented code, and a list mark its blank."""
    if not mark:
        return len(blanks) <= 3
    if mark == ">":
        return len(blanks) <= 4
    return 1 <= len(blanks) <= 4


def comment_runs_on(text: str, opening: int, close: int, search: Searches) -> bool:
    """Whether GitHub may hide text after the `-->` at `close` that ends the comment
    whose `<!--` starts at `opening`, as comment_close finds it.

    GitHub's HTML parser ends a comment at the first `-->` or, from the `<` + 4 on,
    `--!>`. CommonMark ends an inline comment at a `-->` too, but at none that a
    `-` comes before, as in `--->`: from there it reads on to the next `-->` without
    one, as plain_comment_close finds it. Up to its own end it hands the HTML on, and
    the parser reads what follows the end of the comment as HTML: a tag that starts
    there and holds a `-->` in a quoted value reads on into the text after it, to a
    quote in the HTML that follows, and an element such as a `<video>` hides what it
    holds (`gh api -X POST markdown` with the mode gfm, 2026-11-23). Where a tag
    starts there, the reading can't tell how far it reaches, and the comment hides
    the rest of the text."""
    bang = text.find("--!>", opening + BANG_END_FROM, close)
    reach = close
    if close >= opening + DASHED_END_FROM and text[close - 1] == "-":
        plain = search.plain_close(close + len("-->"))
        reach = close if plain < 0 else plain
    if bang < 0 and reach == close:
        return False  # The HTML parser ends the comment where CommonMark does.
    tag = search(TAG_START, close if bang < 0 else bang)
    return tag is not None and tag.start() < reach


def comment_closes(
    text: str, start: int, closed: int, fences: Fences, blocks: Blocks, search: Searches
) -> bool:
    """Whether a comment that starts at a position ends at the `-->` that ends at
    `closed` for GitHub: where an HTML block may hold it, no block of another kind
    that may hold it ends before that `-->`, such as one of a tag at a blank row, and
    in a quote or a list item every row up to that `-->` is surely a row of the
    block, as the blocks of tags ask of their later rows, or a blank row of a list
    item. Otherwise the block may end before it: GitHub shows that `-->` as text, and
    the comment hides the rest of the text. So it does where GitHub's HTML parser
    ends it before CommonMark does and a tag may follow, as comment_runs_on says."""
    if fences.holds(start):
        return True
    if comment_runs_on(text, start, closed - len("-->"), search):
        return False
    if ROW_END.search(text, start, closed) is None:
        return True
    held = blocks.holding(start)
    if not held:
        return True  # A comment of a paragraph, which its own `-->` ends.
    if any(
        blocks.end(block.row) < closed
        for block in held
        if block.row.lastgroup != "comment"
    ):
        return False  # A block of another kind may hold it and end before.
    rows = Rows(text, held, blocks)
    if rows.top:
        return True
    position = start
    while (line := ROW_END.search(text, position, closed)) is not None:
        position = line.end()
        if not rows.surely_held(position) and (
            rows.depth or text[position : row_end(text, position)].strip(" \t")
        ):
            return False
    return True


def row_markup_end(text: str, start: int, search: Searches) -> tuple[int, int] | None:
    """Where an instruction, a declaration or a section of CDATA ends whose end
    follows on its row, if it does: after its first `>`; and where that end is,
    which inline_markup_end calls the end of the HTML. CommonMark hands it on as
    HTML in a paragraph, and an HTML block hands on its row, so that it goes outside
    code wherever it stands, without a look at the blocks."""
    found = INLINE_MARKUP.match(text, start)
    if found is None:
        return None
    row = search(ROW_END, start)
    stop = row.start() if row else len(text)
    end = search.close(start)
    if not 0 <= end <= stop:
        return None
    kind = found.lastgroup
    if kind is None:
        return end, end
    closer = search(HTML_ENDS[kind], found.end())
    if closer is None or closer.end() > stop:
        return None
    return end, closer.end()


def block_tag_end(
    text: str, start: int, held: list[Block], blocks: Blocks, search: Searches
) -> tuple[int | None, bool]:
    """Where a tag ends that the blocks of `held` may hold, as GitHub's HTML parser
    reads it, None where it hides the rest of the text; and whether it ends on its
    row."""
    name = TAG_NAME.match(text, start)
    assert name is not None  # TAG_START found a tag there.
    stop = search.row_end(name.end())
    after, state = tag_row(text, name.end(), stop, "")
    if state == ">":
        return after, True
    first = (after, stop, state)
    return held_tag_end(text, first, held, blocks, takes=takes_a_tag(name[0])), False


def takes_a_tag(name: str) -> bool:
    """Whether a tag of a name, as TAG_NAME finds it, takes the element of the
    block that follows the HTML block that ends it without its `>`: every tag but
    a start tag named `p`, which GitHub's parser reads as a `<p>` of its own."""
    return name.lower() != "<p"


def raw_tag_end(text: str, start: int, raw: int, search: Searches) -> int | None:
    """Where a tag ends that starts in the HTML that CommonMark hands on up to `raw`,
    as GitHub's HTML parser reads it over the rows of a paragraph, which reach it
    without the marks of their quotes: after its `>` in that HTML, which ends with a
    `>`; None where a quoted value goes on past it."""
    name = TAG_NAME.match(text, start)
    assert name is not None  # TAG_START found a tag there.
    position, state = name.end(), ""
    while True:
        stop = min(search.row_end(position), raw)
        position, state = tag_row(text, position, stop, state)
        if state == ">":
            return position
        if stop >= raw:
            return None
        line = ROW_END.match(text, stop)
        assert line is not None  # The row ends before `raw`.
        marks = PARAGRAPH_ROW.match(text, line.end())
        assert marks is not None  # It matches the empty text.
        position = marks.end(1)


def inline_markup_end(
    text: str, start: int, paragraphs: Paragraphs, search: Searches
) -> tuple[int, int] | None:
    """Where GitHub stops hiding an instruction, a declaration or a section of CDATA
    of a paragraph: after its first `>`, at which GitHub's HTML parser ends it; and
    where the HTML ends that CommonMark hands on, at its own end. None where
    CommonMark reads it as text, since its end doesn't follow in the paragraph. A
    later row of the paragraph hands on its text without the marks of its quotes,
    which end nothing."""
    found = INLINE_MARKUP.match(text, start)
    if found is None:
        return None
    end = search.close(start)
    if end < 0 or end > paragraphs.end(start):
        return None
    kind = found.lastgroup
    if kind is None:
        return end, end
    closer = search(HTML_ENDS[kind], found.end())
    if closer is None or closer.end() > paragraphs.end(start):
        return None
    return end, closer.end()


def first_close(text: str, position: int) -> int:
    """Where the first `>` stands at or after a position that is no mark of a quote
    at the start of a later row, or -1."""
    while (closed := text.find(">", position)) >= 0:
        breaks = max(
            text.rfind("\n", position, closed), text.rfind("\r", position, closed)
        )
        if breaks < 0:
            return closed
        marks = PARAGRAPH_ROW.match(text, breaks + 1)
        assert marks is not None  # It matches the empty text.
        if closed >= marks.end(1):
            return closed
        position = marks.end(1)
    return -1


def held_tag_end(
    text: str,
    first: tuple[int, int, str],
    held: list[Block],
    blocks: Blocks,
    rows: Rows | None = None,
    takes: bool = True,
) -> int | None:
    """Where a tag ends that the blocks of `held` may hold, which its first row
    doesn't end: `first` is where that row ends, and the state in which tag_row left
    the tag there. It ends after its `>`, or at the end of the blocks; None where a
    quoted value runs on past the rows that the blocks surely hold, whose end GitHub
    looks for in the rest of the text. Where it ends at the end of the blocks, it
    takes the start tag of the block that GitHub writes next, which Blocks.take
    hands on, but where `takes` is False, for a start tag named `p`: GitHub's
    parser reads it as a `<p>` of its own, which the paragraph's `</p>` closes.

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
    rows = rows or Rows(text, held, blocks)
    while True:
        equals = None
        # At or after the end of the blocks, nothing is left to read: the reading
        # of the next block below has looked there, or a line break stands there.
        if rows.top and state == "!" and stop < rows.region:
            # At the top of the text, no mark of a quote ends a row: the first `>`
            # of the blocks ends the markup, unless a row before it may start a
            # block of its own, as below a paragraph, which hides more.
            closed = text.find(">", stop, rows.region)
            if own_end_row(blocks.rows, stop, rows.region if closed < 0 else closed):
                return None
            if closed >= 0:
                return closed + 1
            stop = rows.region
        if rows.top and state == "" and stop < rows.region:
            # Rows without a quote or an `=` hold only names, up to a `>`.
            names = INERT.match(text, stop, rows.region)
            assert names is not None  # It matches the empty text.
            if text.startswith(">", names.end()):
                return names.end() + 1
            if names.end() == rows.region:
                stop = rows.region
            else:
                # Each row before the one of the quote or the `=` holds only
                # names and leaves the tag as it was, and at the top of the text,
                # where no list item needs an indentation, no row is outside the
                # blocks: the reading goes on at that row, so that it reads the
                # rows between once, not once for each of them.
                stop = row_before(text, stop, names.end())
        if (
            state == ""
            and not rows.top
            and not rows.indent
            and rows.starts is not None
            and stop < rows.region
        ):
            # The rows that leave the tag as it was, in one search; a row that ends
            # the quotes, or that holds more, is read below.
            inert = inert_rows(rows.shallow, rows.depth).match(text, stop, rows.region)
            assert inert is not None  # It matches the empty text.
            stop = inert.end()
        # A row that starts at or after the end of the blocks is none of theirs.
        line = ROW_END.match(text, stop) if stop < rows.region else None
        if line is None or line.end() >= rows.region:
            if state in QUOTES:
                return None
            if rows.top and state == "":
                # The blocks of block tags that follow and leave the tag as it was,
                # in one search, each as the reading of the next block below would
                # read it.
                chained = TAG_BLOCKS.match(text, rows.region)
                assert chained is not None  # It matches the empty text.
                if chained.end() > rows.region:
                    rows.region = stop = chained.end()
                    continue
            following = next_html(text, rows.region)
            if (
                following is None
                or following.lastgroup == "comment"
                or rows.outside(following.start())[1]
                or (rows.top and following["marks"].strip(" \t"))
            ):
                # In a list item, the rows that follow the blocks may reach GitHub's
                # HTML parser without a paragraph around them, which a tight list
                # leaves out: the tag goes on in them. So it does in a row of the
                # blocks that may start a block of its own, which may run on.
                if own_end_row(blocks.rows, first[1], rows.region):
                    return None
                if (
                    rows.top
                    or not glued(text, rows.region)
                    or rows.region < blocks.rows.lists()
                ):
                    if following is None and takes:
                        blocks.take(rows.region, rows)
                    return rows.region
                return None
            if not rows.top:
                return None
            # At the top of the text, the next block asks the same of its rows.
            rows.region = blocks.end(following)
            position = following.end("marks")
            if state == "!":
                stop = position
                continue
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
            if state == "!" and not rows.surely_held(position):
                # A row that the blocks may not hold may start a block of its own,
                # which hides more than the markup does.
                block = HTML_ROW.match(text, position)
                if block is not None and block.lastgroup not in (None, "tag"):
                    return None
            position, outside = rows.outside(position)
            if outside:
                return None if state in QUOTES else stop
            if state == "":
                equals = LEADING_EQUALS.match(text, position)
        stop = row_end(text, position)
        position, state = row_reading(
            text, position, min(stop, rows.region), state, equals
        )
        if state == ">":
            return position


def glued(text: str, position: int) -> bool:
    """Whether the row at a position, or the row after it where the position ends a
    row, holds text: a row with which the paragraph of a list item may go on right
    after a block, with no blank row between them."""
    if position and text[position - 1] not in "\r\n":
        line = ROW_END.match(text, position)
        if line is None:
            return False
        position = line.end()
    return position < len(text) and not BLANK_ROW.match(text, position)


def row_reading(
    text: str, position: int, stop: int, state: str, equals: re.Match[str] | None
) -> tuple[int, str]:
    """How far a later row of a tag takes it from a position, as tag_row reads it.
    An `=` at the start of the row, `equals`, gives the name above it its value, or,
    where no name is open, as after a quoted value, starts a name of its own, which
    takes a value after a second `=`: where a quote follows, the reading that takes
    the tag further counts."""
    if equals is None:
        return tag_row(text, position, stop, state)
    reading = tag_row(text, equals.end(), stop, "=")
    if reading[1] not in QUOTES and QUOTE_MARK.search(text, equals.end(), stop):
        reading = max(reading, tag_row(text, position, stop, ""), key=reach)
    return reading


def reach(reading: tuple[int, str]) -> tuple[bool, bool, int]:
    """How far a reading of a row by tag_row takes a tag: a quoted value that goes
    on furthest, then a tag that goes on past the row, then the later end."""
    position, state = reading
    return state in QUOTES, state != ">", position


def own_end_row(rows: HtmlRows, start: int, stop: int) -> bool:
    """Whether a row that starts between two positions may start an HTML block of a
    kind with an end of its own, such as a declaration, which hides more than a tag
    or markup in the block of a tag around it would."""
    owned = rows.owned()
    index = bisect_right(owned, start)
    return index < len(owned) and owned[index] < stop


def in_tag_block(
    text: str, start: int, held: list[Block], blocks: Blocks
) -> list[Block]:
    """The blocks that may hold markup that starts the text of a later row of the
    block of a block tag, such as `<?x` below `<div>`, without the block that the row
    would start of its own: the block of the tag holds the row up to its blank row,
    so that the markup ends at the end of that block, as a tag of the row would, not
    at a `>` past it. That holds only where the block of the tag surely is one: at
    the top of the text, with no row before it that may start a block of a kind with
    an end of its own, which may hold the tag, and no fence, which may make it code.
    Elsewhere the row may start a block of its own, which hides more, as it does
    where the quote or the list item of the tag ends before it."""
    own = [block for block in held if block.row.end("marks") == start]
    if not own or blocks.rows.fenced():
        return held
    owned = blocks.rows.owned()
    for block in held:
        row = block.row
        if (
            row.lastgroup == "tag"
            and row.start() < own[0].row.start()
            and not row["marks"].strip(" \t")
            and not code(row["marks"])
            and BLOCK_TAG_START.match(text, row.end("marks"))
            and bisect_right(owned, row.start()) == 0
        ):
            return [block for block in held if block is not own[0]]
    return held


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
        # No row is indented by fewer columns than none.
        return position, marks < self.shallow or (
            self.indent > 0 and outdented(self.text, position, self.indent)
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
    left: `""` between attributes, `=` before a value, the quote of a value, or `!`
    in markup that GitHub's HTML parser reads as a comment, which only a `>` ends. It
    ends after the `>` of the tag, in the state `>`, or at the end of the row, in the
    state in which the next row goes on."""
    if state == "!":
        closed = text.find(">", position, stop)
        return (stop, state) if closed < 0 else (closed + 1, ">")
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


def next_block(text: str, position: int, rows: Rows) -> tuple[int, int] | None:
    """Where the next block starts after the end of the blocks of `rows` at a
    position, if it is a paragraph, a heading or a table, whose element a tag that
    the blocks end without its `>` takes; and where that block starts, after the
    definitions and footnotes before it, which GitHub writes elsewhere. None where
    another block follows, which keeps the elements of the paragraphs in it, or
    nothing; and where the quotes of the blocks end before it, at a blank row or a
    row with fewer marks than the shallowest block has, since the tag takes the end
    tag of the quote then. Where it is hard to tell whether a row is in the same
    quotes, it is, so that more is taken out."""
    first = None
    shallow = rows.shallow if rows.starts is not None else 0
    while position < len(text):
        start, marks = quote_marks(text, position, rows.depth)
        end = row_end(text, start)
        rest = text[start:end].lstrip(" \t")
        if marks < shallow:
            return None
        if rest and DEFINITION_ROW.match(text, start) is not None:
            first = position if first is None else first
        elif rest:
            if code(text[position : end - len(rest)]) or OWN_ELEMENT.match(text, start):
                return None
            return (position if first is None else first), position
        line = ROW_END.match(text, end)
        if line is None:
            return None
        position = line.end()
    return None


def inline_end(text: str, start: int, paragraphs: Paragraphs) -> int | None:
    """Where a tag ends that a paragraph holds, if GitHub's renderer hands it on as
    HTML and it runs past its row, or past a `>` in a quoted value. The end of the
    paragraph is looked for only for a tag that is one without it: one that runs past
    that end is none."""
    found = INLINE_TAG.match(text, start)
    if found is None or (
        not ROW_END.search(text, start, found.end())
        and text.find(">", start, found.end() - 1) < 0
    ):
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
