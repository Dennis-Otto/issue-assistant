"""The reading of the tags of an issue as GitHub reads them: the spans of the tags
that run past their rows or past a `>` in a quoted value, of the markup that GitHub
reads as a comment, of the content of the elements whose content GitHub keeps and
no browser shows, and of what a void element holds inside `<svg>` or `<math>`,
which GitHub doesn't write; where GitHub stops showing the text; and the text
without them, in time linear in its length.
"""

from __future__ import annotations

import functools
import re
from bisect import bisect_right

from .markup import (
    BLANK_ROW,
    COMMENT_START,
    CONTAINER,
    FILTERED_TAG,
    GOES_ON,
    HTML,
    HTML_AND_MARKUP,
    HTML_ENDS,
    HTML_TAG,
    HTML_TAG_AND_MARKUP,
    INLINE_TAG,
    LIST_ROW,
    MARKUP_REST,
    ROW_MARKUP_REST,
    TAG_NAME,
    TAG_REST,
    Block,
    Blocks,
    Fences,
    Following,
    HtmlRows,
    Paragraphs,
    Rows,
    Searches,
    block_tag_end,
    comment_closes,
    held_tag_end,
    hidden_html,
    inline_end,
    inline_markup_end,
    raw_tag_end,
    row_before,
    row_markup_end,
    row_or_position,
    row_start,
    tag_row,
)

TAG_REST_ONLY = re.compile(TAG_REST)
# The start of an element that GitHub's sanitizer keeps but whose content the page
# doesn't show: `rp`, the brackets of ruby for a browser without it, which browsers
# hide; `video`, whose content is for a browser without video; and `relative-time`,
# in whose place GitHub's script for the element draws a date. The search stops at
# it also where a `>` ends it at once. Its name is in ASCII letters of any case, as
# the HTML parser reads a name, and `<video/>` starts one as well. GitHub's sanitizer
# keeps the start tag of a `relative-time` only in lower case today, but could keep
# it in any: on the safe side, any case counts.
HIDDEN_NAME = (
    r"(?P<hidden>[Vv][Ii][Dd][Ee][Oo]|[Rr][Pp]"
    r"|[Rr][Ee][Ll][Aa][Tt][Ii][Vv][Ee]-[Tt][Ii][Mm][Ee])(?=[\t\n\f\r />])"
)
# The names of the elements that GitHub's HTML parser counts as special and keeps
# open, such as `p` and `div`, past which no end tag of an element that holds one
# ends that element; in ASCII of any case.
SPECIAL_TAGS = (
    "address|applet|article|aside|blockquote|body|button|caption|center|colgroup"
    "|dd|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form"
    "|frameset|h[1-6]|head|header|hgroup|html|iframe|li|listing|main|marquee|math"
    "|menu|nav|noembed|noframes|noscript|object|ol|p|plaintext|pre|script|search"
    "|section|select|style|summary|svg|table|tbody|td|template|textarea|tfoot|th"
    "|thead|title|tr|ul|xmp"
)
SPECIAL = re.compile(SPECIAL_TAGS, re.IGNORECASE | re.ASCII)
# In the content of a hidden element, the reading stops also at a tag that may end
# it or that starts another, of the names of hidden elements, of ruby and of the
# special elements, also where a `>` ends it at once.
INNER = (
    r"(?P<inner>(?ai:/?(?:video|rp|relative-time|ruby|rtc|rt|rb"
    rf"|{SPECIAL_TAGS}))(?=[\t\n\f\r />]))"
)
# The void elements of HTML, which hold nothing: outside `<svg>` and `<math>` alone,
# since GitHub's HTML parser makes no element void inside them.
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
# The names of the elements whose tags leave the `<p>` of a paragraph open on
# GitHub, as its HTML parser keeps a `<p>` open around them, and `<svg>` and `<math>`
# themselves (`gh api markdown`, 2026-11-23). A tag of any other name may close it:
# a start tag such as `<div>` or `<hr>`, the end tag `</p>`, and the end tag of an
# element that is open around the paragraph, such as `</h1>`, `</blockquote>`,
# `</table>`, or a `</div>` of an HTML block above. That is the safe side: a name
# that isn't listed counts, `<p>` among them, which GitHub doesn't always close.
PHRASING = frozenset(
    [
        "a",
        "abbr",
        "b",
        "bdi",
        "bdo",
        "big",
        "br",
        "cite",
        "code",
        "data",
        "del",
        "dfn",
        "em",
        "font",
        "i",
        "img",
        "ins",
        "kbd",
        "label",
        "mark",
        "math",
        "nobr",
        "picture",
        "q",
        "ruby",
        "s",
        "samp",
        "small",
        "span",
        "strike",
        "strong",
        "sub",
        "sup",
        "svg",
        "time",
        "tt",
        "u",
        "var",
        "video",
        "wbr",
    ]
)
# The start of a tag of a name outside PHRASING, in ASCII of any case.
CLOSING_TAG = re.compile(
    rf"</?(?!(?ai:{'|'.join(sorted(PHRASING))})(?![A-Za-z0-9-]))[A-Za-z]"
)
FOREIGN_NAME = r"(?ai:svg|math)(?=[\t\n\f\r />]|\Z)"
FOREIGN_START = re.compile(rf"<{FOREIGN_NAME}")
VOID_NAME = rf"(?P<void>(?ai:{'|'.join(sorted(VOID))})(?=[\t\n\f\r />]|\Z))"
# The kinds of content that the reading of the tags may be in: none, that of a
# hidden element, and what a void element holds inside `<svg>` or `<math>`.
CONTENT_KINDS = ("", "hidden", "void")


def tag_starts(
    comments: bool, fenced: bool, inner: str, foreign: bool
) -> re.Pattern[str]:
    """Where the reading of the tags takes its next step: with comments while a
    `-->` may still end one; in a text without fenced code past the markup that
    ends on its row; in the content of a hidden element also at its tags; at the
    start tags of `<svg>` and `<math>`, and while one is open, also at their end
    tags and at those of void elements; and in what a void element holds there at
    the tags of `<svg>` and `<math>`. In any content, it stops at the start tags
    that may follow its end, where the content ends at a paragraph or a heading:
    those of hidden elements, and of void ones while an `<svg>` or `<math>` is
    open."""
    comment, guard = ("(?P<comment>!--)|", "") if comments else ("", "(?!!--)")
    markup = MARKUP_REST if fenced else f"(?!{ROW_MARKUP_REST}){MARKUP_REST}"
    if inner == "void":
        tags = f"(?P<inner>/?{FOREIGN_NAME})|{VOID_NAME}|{HIDDEN_NAME}|"
    elif inner:
        tags = (
            f"{INNER}|{VOID_NAME}|{HIDDEN_NAME}|"
            if foreign
            else f"{INNER}|{HIDDEN_NAME}|"
        )
    elif foreign:
        tags = f"(?P<foreign>/?{FOREIGN_NAME})|{VOID_NAME}|{HIDDEN_NAME}|"
    else:
        tags = f"(?P<foreign>{FOREIGN_NAME})|{HIDDEN_NAME}|"
    return re.compile(f"<{guard}(?:{comment}{markup}|{tags}{TAG_REST})")


TAG_STARTS = {
    (comments, fenced, inner, foreign): tag_starts(comments, fenced, inner, foreign)
    for comments in (True, False)
    for fenced in (True, False)
    for inner in CONTENT_KINDS
    for foreign in (True, False)
}


def html_parts(
    text: str, rows: HtmlRows | None = None
) -> tuple[list[tuple[int, int]], int, int]:
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
    rows = rows or HtmlRows(text)
    spans, end = long_tags(text, rows)
    # The content of a hidden element is HTML as any other, whose comments count.
    tags = (
        [span for span in spans if span[0] not in rows.hidden] if rows.hidden else spans
    )
    starts = [start for start, _ in tags]

    def outside(position: int) -> bool:
        index = bisect_right(starts, position) - 1
        return index < 0 or tags[index][1] <= position

    limit = len(text)
    while (last := text.rfind("-->", 0, limit)) >= 0 and not outside(last):
        limit = starts[bisect_right(starts, last) - 1] + 2
    middle = last + 3 if last >= 0 else 0
    comments = (
        comment.start()
        for comment in COMMENT_START.finditer(text, middle)
        if not tags
        or outside(comment.start())
        or row_start(text, comment.start()) is not None
    )
    return spans, middle, min(end, hidden_html(text, middle, Following(comments), rows))


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
    and so do the tags in fenced code and in a comment, and a tag that ends on its
    row, which HTML takes out as it always did, but for one with a `>` in a quoted
    value. The content of a hidden element goes as one span, up to where Element
    finds its end, with the spans of the tags that it holds, and so does what a
    void element holds inside `<svg>` or `<math>`, up to where Foreign finds its
    end, but for its tag.

    Each step reads on from the end of the last: each tag is read once, up to its
    end, and the searches for the ends of the blocks and the paragraphs read no part
    of the text twice.
    """
    rows = rows or HtmlRows(text)
    closes = text.rfind(">")
    if closes < 0 and not rows.found():
        return [], len(text)  # No tag of a paragraph ends, and no block holds one.
    fences = Fences(text, rows)
    # Without fenced code, no row is one of code, and the markup of a row goes by a
    # pattern, everywhere.
    fenced = rows.fenced()
    blocks = Blocks(text, rows, fences if fenced else None)
    paragraphs, search = Paragraphs(text), Searches(text)
    hidden = Hidden(text, rows, blocks, paragraphs, search)
    elements = Elements(hidden, Foreign(text, blocks, hidden))
    spans: list[tuple[int, int]] = []
    position = 0
    # Where the HTML ends that CommonMark hands on from a paragraph for the last
    # instruction or section of CDATA, whose rest GitHub's HTML parser reads.
    raw = -1
    # Whether a `-->` may still end a comment, and where the last comment that the
    # reading passed over ends.
    comments, passed = True, 0
    starts, open_elements = elements.starts(comments, fenced), elements.open
    while (found := starts.search(text, position)) is not None:
        start, position, kind = found.start(), found.end(), found.lastgroup
        element = elements.element
        if (element is not None and 0 <= element.end() <= start) or (
            open_elements and open_elements[-1][1] <= start
        ):
            # Past the end of the paragraph or heading of an element, which ends it.
            elements.passed(start, spans)
            position = start
            starts = elements.starts(comments, fenced)
            continue
        if kind == "comment":
            closed = search.comment_end(position)
            if closed < 0:
                # No `-->` follows: no comment hides a tag.
                comments = False
                starts = elements.starts(comments, fenced)
            elif not comment_closes(text, start, closed, fences, blocks):
                return spans, cut(text, start, elements.element)
            position = passed = max(closed, position)
            continue
        if FILTERED_TAG.match(text, start) or (fenced and fences.holds(start)):
            continue
        if kind == "inner":
            assert element is not None  # Only its content holds such a tag.
            content = element.meets(found)
            if content is not None:
                if content < 0:
                    return spans, cut(text, start, element)
                # A tag of ruby that ends an `rp` is read as any other.
                position = start if content <= start else max(position, content)
                close(spans, element.start, content, rows)
                elements.element = None
                starts = elements.starts(comments, fenced)
                continue
            if text[start + 1] in "MmSs":
                read = max(passed, spans[-1][1] if spans else 0)
                if elements.opens(found, read):
                    starts = elements.starts(comments, fenced)
        elif kind in ELEMENT_KINDS:
            opened = bool(open_elements)
            read = max(passed, spans[-1][1] if spans else 0)
            step = elements.step(found, read, spans)
            if elements.element is not element or bool(open_elements) != opened:
                starts = elements.starts(comments, fenced)
            if step is not None:
                if step < 0:
                    return spans, cut(text, start, element)
                position = step
                continue
        if kind in ELEMENT_KINDS:
            # No such element starts there, or it goes on: the tag is read as any
            # other.
            rest = TAG_REST_ONLY.match(text, start + 1)
            if rest is None:
                continue  # A `>` ends it on its row, and HTML takes it out.
            position = rest.end()
        markup = kind == "markup"
        if markup and (ends := row_markup_end(text, start, search)) is not None:
            position, raw = ends[0], max(raw, ends[1])
            spans.append((start, position))
            continue
        held = blocks.holding(start)
        if held:
            end, whole = hidden.held_end(found, held)
            if end is None:
                return spans, cut(text, start, elements.element)
            if not whole:
                position = end
                continue
        elif markup:
            if start < raw:
                # In the rest of the HTML of an instruction or a section of CDATA:
                # its end follows, so a `>` does.
                end = search.close(start)
            elif (ends := inline_markup_end(text, start, paragraphs, search)) is None:
                continue
            else:
                end, raw = ends
        elif start < raw:
            # A tag in the rest of the HTML of an instruction, which GitHub's HTML
            # parser reads up to its `>` in that HTML; what it holds is none of the
            # markup. The text after that HTML reaches the parser with its quotes
            # written as entities, so a quoted value that goes on past it ends not
            # before the end of the text.
            tag_end = raw_tag_end(text, start, raw, search)
            if tag_end is None:
                return spans, cut(text, start, elements.element)
            spans.append((start, tag_end))
            position = tag_end
            continue
        elif text[position : position + 1] not in GOES_ON or start > closes:
            # A tag of a paragraph that meets a `<` or the end of the text before a
            # quote or a line break ends on its row, or is none, and so does one
            # that no `>` follows.
            continue
        elif (end := inline_end(text, start, paragraphs)) is None:
            continue
        spans.append((start, end))
        position = end
    return spans, elements.finish(spans)


# The kinds of the tags that Elements reads.
ELEMENT_KINDS = frozenset(["foreign", "hidden", "inner", "void"])


class Elements:
    """The element whose content the reading of the tags is in, if any: a hidden one,
    as Hidden reads it, or what a void element holds inside `<svg>` or `<math>`, as
    Foreign reads it; and the reading of the tags of their names."""

    def __init__(self, hidden: Hidden, foreign: Foreign) -> None:
        self.hidden, self.foreign = hidden, foreign
        self.element: Element | Void | None = None
        # The `<svg>` and `<math>` elements that are open, as Foreign keeps them.
        self.open = foreign.open

    def starts(self, comments: bool, fenced: bool) -> re.Pattern[str]:
        """Where the reading of the tags takes its next step, as tag_starts says."""
        element = self.element
        inner = "void" if isinstance(element, Void) else "hidden" if element else ""
        return TAG_STARTS[comments, fenced, inner, bool(self.open)]

    def passed(self, start: int, spans: list[tuple[int, int]]) -> None:
        """Ends the content of the element at the end of its paragraph or heading,
        or else closes the `<svg>` and `<math>` elements whose paragraph or heading
        ends, where a tag at a position is past it."""
        element = self.element
        if element is not None and 0 <= element.end() <= start:
            close(spans, element.start, element.end(), self.hidden.rows)
            self.element = None
        else:
            self.foreign.passed(start)

    def finish(self, spans: list[tuple[int, int]]) -> int:
        """Ends the content of the element at the end of the text, and says where
        GitHub stops showing the text: at its end, or at the start of the content
        where it runs to the end of the text."""
        element, text = self.element, self.hidden.text
        if element is None:
            return len(text)
        if element.end() < 0:
            return cut(text, len(text), element)
        close(spans, element.start, element.end(), self.hidden.rows)
        return len(text)

    def opens(self, found: re.Match[str], read: int) -> bool:
        """Whether a tag in the content of a hidden element opens an `<svg>` or a
        `<math>`, which it does as anywhere else, and opens it then."""
        if not isinstance(self.element, Element) or not FOREIGN_START.match(found[0]):
            return False
        before = len(self.open)
        self.foreign.start_tag(found, found["inner"].lower(), read)
        return len(self.open) > before

    def step(
        self, found: re.Match[str], read: int, spans: list[tuple[int, int]]
    ) -> int | None:
        """Reads a tag of the kinds of ELEMENT_KINDS but `inner`, which the reading
        of the tags reads itself: the position to read on from, REST where the rest
        of the text goes from the start of the tag, or None where the tag is read as
        any other, as in the content of an element, which holds it. `read` is where
        the last span or comment ends that the reading of the tags passed over."""
        kind, start, rows = found.lastgroup, found.start(), self.hidden.rows
        foreign = self.foreign
        if self.element is not None:
            if kind == "void" and isinstance(self.element, Element):
                # In the content of a hidden element, which ends at the end of its
                # paragraph, a void tag hides the rest of the text where the `<p>`
                # is closed, as by a `<div>` before it, which closes the element.
                held = foreign.void(found, read)
                if held == REST or (isinstance(held, Void) and held.end() < 0):
                    return REST
            return None
        if kind == "foreign":
            name = found["foreign"].lower()
            if name[0] != "/":
                foreign.start_tag(found, name, read)
            elif foreign.end_tag(found, name[1:]):
                return found.end() + 1
            return None
        if kind == "void":
            opened: Element | Void | int | None = foreign.void(found, read)
        else:
            opened = self.hidden.open(start, read)
        if isinstance(opened, Void):
            # The tag stays, as a span of its own, whose addresses count.
            spans.append((start, opened.start))
            rows.spaced.add(opened.start)
        if isinstance(opened, (Element, Void)):
            self.element = opened
            return opened.opened
        if opened is not None and opened >= 0:
            close(spans, start, opened, rows)
        return opened


def close(spans: list[tuple[int, int]], start: int, end: int, rows: HtmlRows) -> None:
    """Ends the content of a hidden element whose start tag starts at `start` at a
    position: its span takes in the spans that start in it, and so ends no earlier
    than they do."""
    while spans and spans[-1][0] >= start:
        end = max(end, spans.pop()[1])
    spans.append((start, end))
    rows.hidden.add(start)


def cut(text: str, start: int, element: Element | Void | None) -> int:
    """Where GitHub stops showing a text whose rest markup at a position hides: at
    it, or at the start of its row where it starts the text of the row, or at the
    start of the hidden element whose content holds it."""
    return row_or_position(text, start if element is None else element.start)


# The rest of an end tag after its name, as an end tag of a paragraph or one alone on
# its row has it: blanks and a `>`.
END_TAG_REST = re.compile(r"[\t\n\f\r ]*+>")
RUBY_TAG = re.compile(r"<(?P<close>/)?ruby(?=[\t\n\f\r />])", re.IGNORECASE | re.ASCII)
BACKTICK = re.compile("`")
FENCE_RUN = re.compile("```|~~~")
# A heading of one row, after the marks of its quotes and list items.
HEADING_ROW = re.compile(r"#{1,6}(?=[ \t\r\n]|\Z)")
# Where a code span of a row may start, or something that a backtick in it doesn't
# start one in: an escape, or HTML that CommonMark reads before a code span.
CODE_STOP = re.compile(r"[\\`<]")
BACKTICKS = re.compile("`++")
AUTOLINK = re.compile(
    r"<[A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*+>"
    r"|<[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]++@[A-Za-z0-9-]++(?:\.[A-Za-z0-9-]++)*+>"
)
# The rest of a row after an end tag that stands alone on it.
BLANK_REST = re.compile(r"[ \t]*+(?:[\r\n]|\Z)")
# The content of a hidden element runs to the end of the text.
REST = -1
# Content without a `<` or a backtick up to the end tag of its element.
SIMPLE_CONTENT = {
    name: re.compile(rf"[^<`]*+(?P<end></(?ai:{name})[\t\n\f\r ]*+>)")
    for name in ("video", "rp", "relative-time")
}


def hidden_name(text: str, start: int) -> str:
    """The name of the hidden element whose start tag HIDDEN_NAME found at a
    position, in lower case."""
    if text[start + 1] in "Vv":
        return "video"
    return "rp" if text[start + 2] in "Pp" else "relative-time"


@functools.lru_cache(maxsize=64)
def backtick_run(length: int) -> re.Pattern[str]:
    """A run of exactly `length` backticks, which ends a code span of that many."""
    return re.compile(rf"(?<!`)`{{{length}}}(?!`)")


class Hidden:
    """The `rp`, `video` and `relative-time` elements of a text, for start tags at
    rising positions: GitHub keeps them and what they hold, and the page shows none
    of it, as a browser hides the brackets of ruby and the content for a browser
    without video, and GitHub's script draws a date in place of a `relative-time`.
    Their start tags open an Element, whose content the reading of the tags then
    reads, with every tag in it. A `relative-time` counts whatever its `datetime`:
    the script shows the words of one whose date doesn't parse, but which dates the
    browser parses can't be told here.

    The content ends where GitHub's HTML parser ends the element: at its end tag,
    one for each start tag of its name, but only one in the same paragraph, heading
    or HTML block, or alone on a row after a blank row, at the top of the text; an
    `rp` in a `ruby` also at the `</ruby>` and at the next `<rt>`, `<rp>`, `<rb>` or
    `<rtc>`. Without such an end, it runs to the end of the paragraph or the
    heading, and otherwise, as in an HTML block, a quote or a list item, whose end
    the parser may find later, to the end of the text.

    Where it is hard to tell, it hides more: no end counts after a backtick of the
    content, which may start code that holds it, after a fence in the rows between,
    or after the start of an element that the parser keeps open, such as a `<p>` or
    a `<div>`. A start tag in a code span of its row starts nothing, as GitHub shows
    it as code; where a backtick of an earlier row of its paragraph could start code,
    or a `|` of a table could cut the code, it starts an element.

    Each search reads on from the last: the rows, the blank rows, the backticks and
    the tags of ruby once for the text, and the content of each element once, by
    the reading of the tags."""

    def __init__(
        self,
        text: str,
        rows: HtmlRows,
        blocks: Blocks,
        paragraphs: Paragraphs,
        search: Searches,
    ) -> None:
        self.text, self.rows, self.blocks = text, rows, blocks
        self.paragraphs, self.search = paragraphs, search
        # The searches that read back from where the others read: each reads on
        # from its own last position.
        self.ticks, self.content, self.rubies = (
            Searches(text),
            Searches(text),
            Searches(text),
        )
        # Where the row starts that was read last, and how far it was read back.
        self.begin, self.checked = 0, 0
        # Where the text after the last blank row starts, and where to look next.
        self.blank, self.blank_from = 0, 0
        # Where the row of the last `<ruby>` ends, and where to look for the next.
        self.ruby, self.ruby_from = -1, 0
        # The code spans of a row: the row, how far it was read, where the last
        # span ends, and whether a `|` may cut it.
        self.code: tuple[int, int, int, bool] = (-1, 0, -1, False)
        # The row that was read last for a heading, and whether it is one.
        self.headed = (-1, False)

    def open(self, start: int, before: int) -> Element | int | None:
        """The element whose start tag starts at a position, or where its content
        ends where no tag but its end tag follows it, before the limit of its end
        tags; REST where the start tag hides the rest of the text; None where no
        element starts there: where the tag is none, or a code span holds it.
        `before` is where the last span or comment ends that the reading of the
        tags passed over."""
        text, search = self.text, self.search
        name = hidden_name(text, start)
        held = self.blocks.holding(start)
        container: int | None = None
        rows = None
        if held:
            opened, _ = block_tag_end(text, start, held, self.blocks, search)
            if opened is None:
                return REST
            rows = Rows(text, held, self.blocks)
            limit = rows.region if rows.top else search.row_end(opened)
        else:
            tag = INLINE_TAG.match(text, start)
            # The end of the last row of the paragraph, before its line break.
            container = limit = self.paragraphs.end(start)
            if limit < len(text):
                container = limit = row_before(text, start, limit)
            if tag is None or tag.end() > limit or self.in_code(start, before):
                return None
            opened = tag.end()
            if self.heading(start):
                container = limit = search.row_end(start)
            elif self.rows.lists() <= start:
                # A paragraph of a tight list item reaches the parser without its
                # own element, which would end this one.
                container = REST
        code = self.content(BACKTICK, opened)
        if code is not None:
            limit = min(limit, code.start())
        simple = SIMPLE_CONTENT[name].match(text, opened)
        if simple and simple.start("end") < limit:
            return simple.end()
        element = Element(text, start, opened, name, container)
        element.limit = limit
        if rows is not None and rows.top:
            element.top, element.fence = True, self.content(FENCE_RUN, opened)
            element.listed = self.content(LIST_ROW, opened)
        if name == "rp" and self.in_ruby(start):
            element.ruby, element.following = True, text.find("<", opened)
        return element

    def held_end(
        self, found: re.Match[str], held: list[Block]
    ) -> tuple[int | None, bool]:
        """Where a tag or markup ends that the blocks of `held` may hold, None where it
        hides the rest of the text, and whether a span takes it out: HTML takes out a
        tag that ends on its row as it always did, but for one that a `>` in a quoted
        value would end early for HTML, which a span takes out whole. What a tag that
        ends on its row holds, such as a `<!` in a quoted value, is none of the markup
        that GitHub reads."""
        text, blocks, search = self.text, self.blocks, self.search
        start = found.start()
        if found.lastgroup == "markup":
            stop = search.row_end(found.end())
            after, state = tag_row(text, found.end(), stop, "!")
            if state == ">":
                return after, True
            return held_tag_end(text, (after, stop, state), held, blocks), True
        name = TAG_NAME.match(text, start)
        assert name is not None  # TAG_START found a tag there.
        stop = search.row_end(name.end())
        after, state = tag_row(text, name.end(), stop, "")
        if state == ">":
            return after, text.find(">", start, after - 1) >= 0
        rows = Rows(text, held, blocks)
        end = held_tag_end(text, (after, stop, state), held, blocks, rows)
        if (
            end is not None
            and (inline := inline_end(text, start, self.paragraphs)) is not None
            and (begin := self.row_begin(start))
            > max(block.row.start() for block in held)
            and rows.outside(begin)[1]
        ):
            # A row that no HTML block goes on in, such as a lazy row below one in a
            # quote, is one of a paragraph, where CommonMark may read the tag over
            # rows: the longer reading counts.
            end = max(end, inline)
        return end, True

    def heading(self, start: int) -> bool:
        """Whether the row of a position is a heading, read once for each row: not
        where its `#` is indented by four columns or a tab, as code would be, since
        such a row goes on the paragraph above it, as GitHub reads `a` / `    # b`
        (`gh api markdown`, 2026-11-23). The space after the `>` of a quote is part
        of its mark."""
        begin = self.row_begin(start)
        if begin != self.headed[0]:
            marks = CONTAINER.match(self.text, begin)
            assert marks is not None  # It matches the empty text.
            heading = HEADING_ROW.match(self.text, marks.end()) is not None
            if heading:
                mark = marks[0].rstrip(" \t")
                blanks = marks[0][len(mark) :]
                indent = len(blanks) - bool(mark.endswith(">") and blanks)
                heading = "\t" not in blanks and indent < 4
            self.headed = (begin, heading)
        return self.headed[1]

    def row_begin(self, position: int) -> int:
        """Where the row of a position starts."""
        text = self.text
        found = max(
            text.rfind("\n", self.checked, position),
            text.rfind("\r", self.checked, position),
        )
        if found >= 0:
            self.begin = found + 1
        self.checked = position
        return self.begin

    def in_ruby(self, position: int) -> bool:
        """Whether a `ruby` may hold a position: a `<ruby>` before it on its row,
        with no `</ruby>` and no backtick between them."""
        search = self.rubies
        while (tag := search(RUBY_TAG, self.ruby_from)) is not None and (
            tag.start() < position
        ):
            self.ruby_from = tag.end()
            self.ruby = -1 if tag["close"] else search.row_end(tag.end())
        if self.ruby > position:
            code = search(BACKTICK, self.ruby_from)
            return code is None or code.start() >= position
        return False

    def in_code(self, start: int, before: int) -> bool:
        """Whether a code span of its row surely holds a tag that starts at a
        position of a paragraph: no backtick between the last blank row and the
        reading of the row, which starts at `before` where that is later."""
        text, search = self.text, self.search
        begin = max(self.row_begin(start), before)
        while (blank := search(BLANK_ROW, self.blank_from)) is not None and (
            blank.start() < begin
        ):
            self.blank, self.blank_from = blank.end(), blank.end() + 1
        # Without a backtick before it in its paragraph, no code span holds it.
        earlier = self.ticks(BACKTICK, self.blank)
        if earlier is None or not begin <= earlier.start() < start:
            return False
        row, read, until, cut = self.code
        if row != self.begin:
            row, read, until, cut = self.begin, begin, -1, False
        read = max(read, begin)
        stop = search.row_end(start)
        while until <= start and (mark := CODE_STOP.search(text, read, start)):
            read = mark.end()
            if mark[0] == "\\":
                read += 1
            elif mark[0] == "<":
                read = self.html_end(mark.start(), stop)
            else:
                run = BACKTICKS.match(text, mark.start())
                assert run is not None  # A backtick starts it.
                closer = search(backtick_run(len(run[0])), run.end())
                if closer is None or closer.start() >= stop:
                    read = run.end()
                    continue
                until, read = closer.end(), closer.end()
                cut = cut or text.find("|", run.end(), closer.start()) >= 0
        self.code = (row, read, until, cut)
        return start < until and not cut

    def html_end(self, position: int, stop: int) -> int:
        """Where HTML ends that starts at a `<` of a row that ends at `stop`, and that
        CommonMark reads before a code span, so that a backtick in it starts none;
        or the position after the `<`: a comment, an instruction, a section of CDATA
        or a declaration up to its end, which the reading of the tags has passed,
        and a tag or an autolink of the row."""
        text, search = self.text, self.search
        if text.startswith("<!--", position):
            closed = search.comment_end(position + 4)
        elif text.startswith(("<?", "<![CDATA["), position):
            kind = "processing" if text[position + 1] == "?" else "cdata"
            found = search(HTML_ENDS[kind], position + 2)
            closed = found.end() if found else -1
        elif text.startswith("<!", position):
            closed = search.close(position)
        else:
            found = INLINE_TAG.match(text, position, stop) or AUTOLINK.match(
                text, position, stop
            )
            closed = found.end() if found else -1
        return closed if closed > position else position + 1


class Element:
    """An `rp`, a `video` or a `relative-time` whose content the reading of the tags
    is in: where its start tag starts and ends, its name, and where its paragraph or
    heading ends it, REST in a list item, or None in an HTML block. Its end tags
    count only before `limit`, the end of its row, its block at the top of the text,
    its paragraph or its heading, or a backtick before them; after it, at the top of
    the text, only one alone on a row, before `fence`, and not indented after
    `listed`, the first row of a list item in its content, which may hold it:
    GitHub's HTML parser ignores it in a list item. `ruby` says whether a `ruby`
    holds it, and `following` where the first `<` of its content stands."""

    __slots__ = (
        "container",
        "depth",
        "fence",
        "following",
        "limit",
        "listed",
        "name",
        "opened",
        "ruby",
        "start",
        "text",
        "top",
    )

    def __init__(
        self, text: str, start: int, opened: int, name: str, container: int | None
    ) -> None:
        self.text, self.start, self.opened, self.name = text, start, opened, name
        self.container = container
        self.depth, self.limit, self.top = 1, opened, False
        self.fence: re.Match[str] | None = None
        self.listed: re.Match[str] | None = None
        self.ruby, self.following = False, -1

    def meets(self, found: re.Match[str]) -> int | None:
        """Where the content ends at a tag of the names that may end it, which the
        reading of the tags found in it, REST where it hides the rest of the text
        from its start on, or None where it goes on: at its own end tag, at the
        `</ruby>` or at the next tag of ruby of an `rp` in a `ruby`; at the start of
        an element that GitHub's HTML parser keeps open, in an HTML block, past which
        no end tag counts, the rest of the text."""
        text, at = self.text, found.start()
        name = found["inner"].lower()
        closing = name.startswith("/")
        name = name.lstrip("/")
        if self.container is None and not closing and SPECIAL.fullmatch(name):
            return REST
        if not closing:
            if (
                self.ruby
                and at < self.limit
                and self.depth == 1
                and self.following == at
                and name in ("rt", "rp", "rb", "rtc")
                and INLINE_TAG.match(text, at)
            ):
                return at
            self.ruby = self.ruby and name != "ruby"
            self.depth += name == self.name
            return None
        close = END_TAG_REST.match(text, found.end())
        if close is None or (
            at >= self.limit
            and (
                not self.top
                or name != self.name
                or (self.fence is not None and self.fence.start() < at)
                or (
                    self.listed is not None
                    and self.listed.start() < at
                    and text[at - 1] not in "\r\n"
                )
                or not own_row(text, at, close.end())
            )
        ):
            return None
        if name == self.name:
            self.depth -= 1
            return None if self.depth else close.end()
        return at if name == "ruby" and self.ruby else None

    def end(self) -> int:
        """Where the content ends that no end tag ended: at the end of its paragraph
        or heading, or of the text."""
        return REST if self.container is None else self.container


# After the start of the outermost open `<svg>` or `<math>`, what may hold one of
# their end tags as text or code, past which none counts: a backtick, a `$` of math,
# a `[` of a link, whose destination or title may hold it, and a fence of tildes.
UNSURE_END = re.compile(r"[`$\[]|~~~")
# The end of a start tag whose `/` is the last of an unquoted value, which ends no
# element: `<br class=x/>` holds what follows it.
UNQUOTED_END = re.compile(r"=[\t\n\f\r ]*+[^\t\n\f\r \"'>=]*+\Z")


def closes_itself(tag: str) -> bool:
    """Whether a start tag ends with a `/>` that GitHub's HTML parser reads as that
    of a tag that closes itself, outside a value."""
    return tag.endswith("/>") and UNQUOTED_END.search(tag, 0, len(tag) - 2) is None


class Foreign:
    """The `<svg>` and `<math>` elements that GitHub's HTML parser keeps open, for
    tags at rising positions, and what a void element holds inside them.

    GitHub's sanitizer drops their tags and keeps their text, but its parser makes
    no element void inside them, and none breaks out: a `<br>`, `<hr>`, `<img>` or
    `<source>` there holds what follows it, and GitHub writes none of it, as the
    tag of a void element holds nothing (`gh api markdown`, 2026-11-22). Every name
    of VOID counts, not only these four, so that a change of the sanitizer opens no
    gap; a tag that closes itself, with `/>`, holds nothing.

    A start tag of either name opens one, but where it closes itself or in a paragraph
    where it is text or a code span of its row surely holds it. One of an HTML block
    stays open to its end tag, also past blank rows and to the end of the text, as on
    GitHub; one of a paragraph or a heading also closes at its end, which GitHub ends
    with its own end tag, but only where the `<p>` of the paragraph is open where it
    starts, as Closings reads it, and one of a list item, which a tight list hands on
    without one, only at an end tag. An end tag counts only where it surely is one: of
    the innermost element and of its name, in any case, with its `>` right after the
    name, as GitHub reads `</svg>` but not `</svg >`; not after a `\\`, nor on a row
    indented as code may be, nor after what may make it text or code (UNSURE_END).

    What a void element holds there ends at the end tag that closes the outermost
    element, or at the end of the paragraph or the heading that holds the tag, which
    GitHub ends with an end tag, where its `<p>` is open at the tag; in an HTML block or
    a list item, or where that `<p>` is closed, at the end of the text. GitHub also ends
    it at the end tag of an element around it, as `</g>`, `</b>` or an inner `</svg>`,
    which can be hard to tell, and at an enclosing `</div>` or `</details>`: none of
    them counts, on the safe side, and neither does an integration point, such as
    `<foreignObject>`, which GitHub doesn't read as HTML reads it (`<title>` of SVG is
    none on GitHub)."""

    def __init__(self, text: str, blocks: Blocks, hidden: Hidden) -> None:
        self.text, self.blocks, self.hidden = text, blocks, hidden
        self.closings = Closings(text, hidden.rows, blocks)
        # The open elements, the innermost last: the name of each, and where its
        # paragraph or heading ends it, or the end of the text.
        self.open: list[tuple[str, int]] = []
        # Where the first of UNSURE_END after the start of the outermost stands.
        self.unsure = len(text)
        # The row that was read last for its indent, and whether code may hold it.
        self.indented = (-1, False)

    def passed(self, position: int) -> None:
        """Closes the elements whose paragraph or heading ends before a position."""
        while self.open and self.open[-1][1] <= position:
            self.open.pop()

    def reach(self, start: int, held: bool, before: int) -> int:
        """Where the paragraph or the heading ends that holds a position, and the end
        of the text in an HTML block or a list item, or where the `<p>` of the
        paragraph is closed there, no earlier than which the open elements end: an
        element ends no later than the one around it. `before` is where the last
        span or comment ends that the reading of the tags passed over."""
        text, hidden, closings = self.text, self.hidden, self.closings
        outermost = not self.open
        if held or hidden.rows.lists() <= start:
            if outermost:
                closings.outer = (start, REST)
            end = len(text)
        else:
            heading = hidden.heading(start)
            end = paragraph = hidden.paragraphs.end(start)
            if outermost:
                closings.outer = (start, paragraph)
            if heading:
                end = hidden.search.row_end(start)
            elif end < len(text):
                end = row_before(text, start, end)
            # Read only where a tag before it may count: one not read yet, or one
            # of its paragraph. Where the `<p>` is closed, the element ends with
            # no element around it, since the tag that closed the `<p>` may have
            # closed those as well, such as a `</p>` inside them.
            if (
                closings.next < start or closings.paragraph == paragraph
            ) and closings.closed(start, before, paragraph, heading):
                return len(text)
        return end if outermost else min(end, self.open[-1][1])

    def start_tag(self, found: re.Match[str], name: str, before: int) -> None:
        """Opens the element whose start tag starts at a position, if it opens one.
        `before` is where the last span or comment ends that the reading of the
        tags passed over."""
        text, start = self.text, found.start()
        held = bool(self.blocks.holding(start))
        tag = INLINE_TAG.match(text, start)
        if tag is not None and closes_itself(tag[0]):
            return
        if not held and (tag is None or self.hidden.in_code(start, before)):
            return
        if not self.open:
            mark = self.hidden.search(UNSURE_END, start)
            self.unsure = len(text) if mark is None else mark.start()
        self.open.append((name, self.reach(start, held, before)))

    def end_tag(self, found: re.Match[str], name: str) -> bool:
        """Closes the innermost element where an end tag surely closes it, and says
        whether it did."""
        text, start = self.text, found.start()
        if (
            not self.open
            or self.open[-1][0] != name
            or text[found.end() : found.end() + 1] != ">"
            or start >= self.unsure
            or text[start - 1 : start] == "\\"
        ):
            return False
        begin = self.hidden.row_begin(start)
        if begin != self.indented[0]:
            # Read once for each row, whatever the number of its end tags.
            marks = CONTAINER.match(text, begin)
            assert marks is not None  # It matches the empty text.
            blanks = marks[0][len(marks[0].rstrip(" \t")) :]
            self.indented = (begin, "\t" in blanks or len(blanks) >= 4)
        if self.indented[1]:
            return False  # The row may be one of indented code.
        self.open.pop()
        return True

    def void(self, found: re.Match[str], before: int) -> Void | int | None:
        """What the start tag of a void element at a position holds while an element
        is open, as a Void from the end of the tag; REST where the tag hides the rest
        of the text; or None where it holds nothing: where it closes itself, or in a
        paragraph where it is text or a code span of its row surely holds it."""
        text, start = self.text, found.start()
        held = self.blocks.holding(start)
        if held:
            end, _ = block_tag_end(text, start, held, self.blocks, self.hidden.search)
            if end is None:
                return REST
        else:
            tag = INLINE_TAG.match(text, start)
            if tag is None or self.hidden.in_code(start, before):
                return None
            end = tag.end()
        if closes_itself(text[start:end]):
            return None
        return Void(self, end, self.reach(start, bool(held), before))


class Void:
    """What a void element holds inside `<svg>` or `<math>`, which the reading of the
    tags is in, as Foreign reads it: from the end of its tag, `start`, to the end tag
    that closes the outermost open element, or to `limit`, the end of the paragraph
    or the heading that holds it, or of the text."""

    __slots__ = ("foreign", "limit", "opened", "start")

    def __init__(self, foreign: Foreign, start: int, limit: int) -> None:
        self.foreign, self.start, self.opened, self.limit = foreign, start, start, limit

    def meets(self, found: re.Match[str]) -> int | None:
        """Where the content ends at a tag of `<svg>` or `<math>`, after the end tag
        that closes the outermost, or None where it goes on. A start tag in it opens
        an element, whatever holds it."""
        name = found["inner"].lower()
        foreign = self.foreign
        if name[0] != "/":
            tag = INLINE_TAG.match(foreign.text, found.start())
            if tag is None or not closes_itself(tag[0]):
                foreign.open.append((name, foreign.open[-1][1]))
            return None
        if foreign.end_tag(found, name[1:]) and not foreign.open:
            return found.end() + 1
        return None

    def end(self) -> int:
        """Where the content ends that no end tag ended: at the end of its paragraph
        or heading, or REST, at the end of the text."""
        return REST if self.limit >= len(self.foreign.text) else self.limit


@functools.lru_cache(maxsize=16)
def closing_tags(text: str) -> tuple[int, ...]:
    """Where the tags of a text start whose names are outside PHRASING, which may
    close the `<p>` of a paragraph: found once for a text that several readings
    read, each up to its end."""
    return tuple(found.start() for found in CLOSING_TAG.finditer(text))


class Closings:
    """Whether the `<p>` of the paragraph that holds a position is closed there, as
    GitHub's HTML parser closes it, for positions that never fall: Foreign asks it
    at the start tags of `<svg>` and `<math>` and at those of void elements.

    GitHub ends a paragraph with its own `</p>`, which closes an `<svg>` or `<math>`
    in it, and what a void element holds there. Where the `<p>` is closed already,
    that `</p>` closes nothing, and the element stays open past the end of the
    paragraph, as one of an HTML block does (`gh api markdown`, 2026-11-23). The
    `<p>` is closed at a position where one of these comes before it in its
    paragraph: an end tag of a name outside PHRASING, `</p>` among them, inside or
    outside the element, which may close it or an element around it, whichever that
    is; or, before the outermost open `<svg>` or `<math>`, a start tag of such a
    name. Inside the element, a start tag closes nothing, also in a later paragraph,
    whose `<p>` GitHub opens inside the element. An end tag in it counts as well:
    one in its own paragraph closes it with the `<p>`, so that an `<svg>` after it
    stays open, which can't be told apart safely from one that closes nothing. In a
    heading, only an end tag of its row counts, since GitHub ends the element with
    the heading after a start tag.

    A tag counts only where CommonMark hands it on as one: not after a `\\`, nor in
    a code span of its row, as Hidden reads them; but where that can't be told, as
    in a span or a comment that the reading of the tags passed over, it counts. A
    `<` inside an autolink starts no tag, and one after it that is no part of one,
    as in `<https://e.com/<div>>`, does.

    The tags are found once for each text, by closing_tags, and read once, from
    rising positions, and for each paragraph only up to its first such end tag of
    each row and its first such start tag: nothing is read back from a tag."""

    def __init__(self, text: str, rows: HtmlRows, blocks: Blocks) -> None:
        self.text = text
        self.search, self.paragraphs = Searches(text), Paragraphs(text)
        # The code spans of the rows, read on their own.
        self.code = Hidden(text, rows, blocks, self.paragraphs, self.search)
        # Where the tags start that may close it, found at the first question,
        # which of them is next, and where it starts: -1 before the first question,
        # and past the end of the text after the last tag.
        self.tags: tuple[int, ...] = ()
        self.index, self.next = 0, -1
        # The paragraph of the last tag that counts, by where it ends; where its
        # first end tag and its first start tag stand that count; and the row of
        # the last end tag that counts, with the first that counts on it.
        self.paragraph, self.ends, self.starts = -1, len(text), len(text)
        self.row = (-1, len(text))
        # Where the outermost open `<svg>` or `<math>` starts, and its paragraph by
        # where it ends, or REST in an HTML block or a list item.
        self.outer = (-1, REST)

    def closed(self, position: int, before: int, paragraph: int, heading: bool) -> bool:
        """Whether the `<p>` of the paragraph or heading that holds a position is
        closed there, where an element is open or opens: `paragraph` is where that
        paragraph ends. `before` is where the last span or comment ends that the
        reading of the tags passed over."""
        if self.next < position:
            self.passed(position, before)
        if paragraph != self.paragraph:
            return False  # No tag before it in its paragraph counts.
        if heading:
            # An end tag of its row, which comes before it.
            return self.row[0] == self.code.row_begin(position)
        start, holder = self.outer
        return self.ends < position or (holder == paragraph and self.starts < start)

    def passed(self, position: int, before: int) -> None:
        """Reads the tags before a position that may close the `<p>`."""
        text, none = self.text, len(self.text)
        if self.next < 0:
            self.tags = closing_tags(text)
            self.follow()
        while (at := self.next) < position:
            self.index += 1
            self.follow()
            closing = text[at + 1] == "/"
            paragraph = self.paragraphs.end(at)
            if paragraph == self.paragraph and (
                self.starts < none
                if not closing
                else self.row[0] == self.code.row_begin(at)
            ):
                continue  # The first of its kind counts already.
            if not self.counts(at, before):
                continue
            if paragraph != self.paragraph:
                self.paragraph, self.ends, self.starts = paragraph, none, none
            if closing:
                self.ends = min(self.ends, at)
                self.row = (self.code.row_begin(at), at)
            else:
                self.starts = at

    def follow(self) -> None:
        """Takes the next tag that may close the `<p>`: where it starts, or a
        position past the end of the text after the last."""
        tags, index = self.tags, self.index
        self.next = tags[index] if index < len(tags) else len(self.text) + 1

    def counts(self, start: int, before: int) -> bool:
        """Whether a tag at a position of a paragraph is one, or may be one."""
        text = self.text
        if INLINE_TAG.match(text, start) is None:
            return False
        if start < before:
            return True  # In a span or a comment that the reading passed over.
        escape = start
        while text[escape - 1 : escape] == "\\":
            escape -= 1
        return (start - escape) % 2 == 0 and not self.code.in_code(start, before)


def own_row(text: str, start: int, end: int) -> bool:
    """Whether a tag from `start` to `end` stands alone on its row, indented by
    three blanks at most, after a blank row: an HTML block of its own."""
    begin = start
    while begin > start - 3 and text[begin - 1 : begin] == " ":
        begin -= 1
    if not begin or text[begin - 1] not in "\r\n" or not BLANK_REST.match(text, end):
        return False
    above = begin - 1
    if text.startswith("\r\n", above - 1):
        above -= 1
    while above > 0 and text[above - 1] in " \t":
        above -= 1
    return above > 0 and text[above - 1] in "\r\n"


def strip_html(text: str) -> str:
    """The text as GitHub shows it, without HTML comments and tags, in time linear
    in its length: first the tags that run past their rows, with what they hold."""
    return shown_html(text)[0]


def shown_html(text: str) -> tuple[str, bool]:
    """The text as strip_html leaves it, and whether GitHub hides the rest of it from
    a position on, as after a comment that never ends: then it also hides each
    footnote, which it shows after the end of the text."""
    rows = HtmlRows(text)
    spans, middle, end = html_parts(text, rows)
    text, spans = dated(text, spans, rows)
    comments = without(text, spans, 0, min(middle, end))
    rest = without(text, spans, middle, end)
    html, tags = (
        (HTML, HTML_TAG) if rows.fenced() else (HTML_AND_MARKUP, HTML_TAG_AND_MARKUP)
    )
    # Of each match, a tag start that no `>` closes stays, or nothing: a group that
    # doesn't match stands for the empty text.
    shown = html.sub(r"\g<open>", comments) + tags.sub(r"\g<open>", rest)
    return shown, end < len(text)


def shown_spans(
    text: str, limit: int | None = None
) -> tuple[str, list[tuple[int, int]], bool]:
    """The parts of a text that strip_html keeps, as spans in their order, so that
    what another reading of the same text takes out can go as well; the text of the
    spans, which is the text with a space in the place of each `relative-time` whose
    content goes, as dated leaves it, and as long as the text; and whether GitHub
    hides the rest of it from a position on, as shown_html says.

    Where another reading takes out all from `limit` on, the spans before it are
    read as ever, and one span from it to the end of the text stands for the rest:
    each part of the HTML that starts before it is read to its end."""
    rows = HtmlRows(text)
    spans, middle, end = html_parts(text, rows)
    text, spans = dated(text, spans, rows)
    html, tags = (
        (HTML, HTML_TAG) if rows.fenced() else (HTML_AND_MARKUP, HTML_TAG_AND_MARKUP)
    )
    limit = len(text) if limit is None else limit
    kept: list[tuple[int, int]] = []
    for start, stop, pattern in ((0, min(middle, end), html), (middle, end, tags)):
        if start >= limit:
            break
        pieces = between(spans, start, stop)
        joined = "".join(text[first:last] for first, last in pieces)
        # Where the limit falls in the joined pieces.
        before = [(first, min(last, limit)) for first, last in pieces if first < limit]
        reach = sum(last - first for first, last in before)
        # As in shown_html, a match goes but for a tag start that no `>` closes.
        removed = []
        for match in pattern.finditer(joined):
            if match.start() >= reach:
                break
            if match["open"] is None:
                removed.append(match.span())
        kept += subtract(before, removed)
    if limit < len(text):
        kept.append((limit, len(text)))
    return text, kept, end < len(text)


def dated(
    text: str, spans: list[tuple[int, int]], rows: HtmlRows
) -> tuple[str, list[tuple[int, int]]]:
    """The text with a space in place of the `<` of each `relative-time` whose
    content a span takes out, and in place of the first character of what a void
    element holds inside `<svg>` or `<math>`, and the spans without them: the page
    draws a date, a line break, a rule or an image there, so the words on both sides
    stay apart, as `Value` and `Error` around one stay two words for the search. The
    text keeps its length, so a position in it is one in the text as it is written
    for every other reading. Of a `video` and an `rp`, which draw no words in their
    place, nothing stays, as before."""
    places = [
        start
        for start, end in spans
        if start in rows.hidden
        and end > start
        and (start in rows.spaced or text[start + 2] in "Ee")
    ]
    if not places:
        return text, spans
    pieces, position = [], 0
    for place in places:
        pieces += [text[position:place], " "]
        position = place + 1
    pieces.append(text[position:])
    starts = set(places)
    return "".join(pieces), [(start + (start in starts), end) for start, end in spans]


def between(
    spans: list[tuple[int, int]], start: int, stop: int
) -> list[tuple[int, int]]:
    """The parts of a text from `start` to `stop` outside the spans, which follow
    each other and don't overlap, that `without` joins."""
    pieces, position = [], start
    for begin, end in spans:
        if begin >= stop:
            break
        if end > position:
            pieces.append((position, max(begin, position)))
            position = end
    pieces.append((position, stop))
    return [(first, last) for first, last in pieces if last > first]


def subtract(
    pieces: list[tuple[int, int]], removed: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """The pieces of a text without what goes of them once they are joined: spans of
    the joined pieces, in order and apart."""
    kept: list[tuple[int, int]] = []
    offset, index = 0, 0
    for first, last in pieces:
        position, top = offset, offset + last - first
        while index < len(removed) and removed[index][0] < top:
            begin, end = removed[index]
            if begin > position:
                kept.append((first + position - offset, first + begin - offset))
            position = max(position, min(end, top))
            if end > top:
                break
            index += 1
        if position < top:
            kept.append((first + position - offset, last))
        offset = top
    return kept


def without(text: str, spans: list[tuple[int, int]], start: int, stop: int) -> str:
    """The part of a text from `start` to `stop`, without the spans, which follow
    each other and don't overlap."""
    pieces, position = [], start
    for begin, end in spans:
        if begin >= stop:
            break
        if end > position:
            pieces.append(text[position : max(begin, position)])
            position = end
    pieces.append(text[position:stop])
    return "".join(pieces)
