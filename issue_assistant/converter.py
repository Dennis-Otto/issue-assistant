"""The Markdown that the AI is to read, made from GitHub's own HTML of a text.

#488 chose way D: the AI reads each text as GitHub's HTML of it (`bodyHTML`,
`body_html`) shows it, instead of the source cleaned by hand, once D has passed.
This module is its first step (#520): `markdown_of` makes the Markdown, and no
reader calls it yet, so every reader stays on `shown_text`.

It reads a closed list of what GitHub writes. An element whose tag, attribute,
class or value isn't on the list gives no text, nor does anything inside it, and
counts as `unknown`; so do a comment, a declaration, an instruction, an end tag that
closes nothing and an element that never ends. What the page never shows, such as
the content of an `rp` or a `relative-time`, goes and counts as `hidden`. A link or
a reference keeps only what the source holds, since GitHub renders some references
per viewer and makes addresses of its own, such as those of camo.

The tree is built and walked with stacks of its own, so a deep text costs memory and
not Python's stack; each row takes the marks of its quotes and list items once, so
the time grows with the length of the output, which stops at LONGEST; and content
inside more than DEEPEST open elements gives no text.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote

from .constants import MAX_TEXT
from .structures import DRAWING_NOTE, DRAWN, MATH_NOTE

# Content inside more open elements than this gives no text: half of GitHub's own
# limit of 255, so that the marks before a row stay short.
DEEPEST = 128
# The text stops at twice the length of a text that GitHub keeps.
LONGEST = 2 * MAX_TEXT
# A table keeps this many columns: the cells past them, and the columns that a
# colspan or a rowspan would add past them, give no text and count as `cut`, so that
# a few cells with wide spans can't make a table of millions of cells.
WIDEST = 64
CUT_NOTE = "(the rest of the text removed: it is longer than the assistant reads)"
# How D reads a mermaid diagram is still open (#520): with False, as here, every
# drawing gives DRAWING_NOTE; True isn't built.
DIAGRAM_SOURCE = False
# What the converter takes out, by kind, in the order of Converted.counts.
TAKEN_OUT = (
    "unknown",
    "hidden",
    "drawings",
    "math",
    "references",
    "targets",
    "depth",
    "cut",
)


@dataclass(frozen=True)
class Converted:
    """The Markdown for the AI, the addresses of the links and pictures that it
    wrote in their order, and what it took out: for each kind of TAKEN_OUT, how
    often each name came, such as `counts["unknown"]["tag <foo>"]`."""

    text: str
    addresses: tuple[str, ...]
    counts: dict[str, Counter[str]]

    def count(self, kind: str) -> int:
        """How often the kind of TAKEN_OUT came, all names together."""
        return sum(self.counts[kind].values())


# The elements that hold no content, and those that hold blocks.
VOID_TAGS = frozenset({"br", "hr", "img", "input", "source", "wbr"})
HEADINGS = {f"h{level}": level for level in range(1, 7)}
BLOCK_ELEMENTS = frozenset(
    {
        "p",
        "ul",
        "ol",
        "li",
        "blockquote",
        "pre",
        "div",
        "hr",
        "table",
        "details",
        "summary",
        "markdown-accessiblity-table",
        "thead",
        "tbody",
        "tfoot",
        "tr",
        "th",
        "td",
        "section",
        "dl",
        "dt",
        "dd",
        *HEADINGS,
    }
)
# The inline elements and their marks; the others of INLINE_TAGS give their text.
MARKS = {
    "strong": "**",
    "b": "**",
    "em": "_",
    "i": "_",
    "del": "~~",
    "s": "~~",
    "strike": "~~",
    "q": '"',
}
INLINE_TAGS = frozenset(
    {
        *MARKS,
        "sub",
        "sup",
        "tt",
        "kbd",
        "ins",
        "mark",
        "var",
        "samp",
        "span",
        "ruby",
        "rt",
        "picture",
        "themed-picture",
        "g-emoji",
        "a",
        "code",
    }
)

# A rule for the value of an attribute: any value, a set of values or a test.
ANY_VALUE = None
Rule = frozenset[str] | Callable[[str], bool] | None
ALIGNS = frozenset({"left", "right", "center", "justify"})
ALERTS = ("note", "tip", "important", "warning", "caution")
CHIP = frozenset({"ml-1", "d-inline-block", "border", "circle", "color-border-subtle"})
CHIP_STYLE = re.compile(
    r"background-color: #[0-9a-fA-F]{3,8}; height: 8px; width: 8px;"
)
PICTURE_STYLE = re.compile(
    r"max-width: 100%;(?: height: auto; max-height: [0-9]+px;)?"
    r"(?:; aspect-ratio: [0-9]+ / [0-9]+; background-color: var\(--bgColor-muted\);"
    r" border-radius: 6px)?"
)
REFERENCE_CLASSES = frozenset(
    {"issue-link", "js-issue-link", "user-mention", "commit-link", "notranslate"}
)
REFERENCES = frozenset({"issue-link", "js-issue-link", "user-mention", "commit-link"})
CODE_CLASSES = frozenset(
    {
        "highlight",
        "notranslate",
        "position-relative",
        "overflow-auto",
        "snippet-clipboard-content",
        "markdown-alert",
        *(f"markdown-alert-{kind}" for kind in ALERTS),
    }
)
HIGHLIGHT = re.compile(r"highlight-(?:source|text)-([\w.+-]+)")
NUMBER = re.compile(r"-?[0-9]{1,9}")
LANGUAGE = re.compile(r"[\w.+#-]+")


def classes(allowed: frozenset[str], pattern: re.Pattern[str] | None = None) -> Rule:
    """The rule of a class attribute: each of its classes allowed or matched."""
    return lambda value: all(
        one in allowed or (pattern is not None and pattern.fullmatch(one) is not None)
        for one in value.split()
    )


def matches(pattern: re.Pattern[str]) -> Rule:
    return lambda value: pattern.fullmatch(value) is not None


def prefixed(value: str) -> bool:
    """An id or a name that GitHub's sanitizer wrote, with its prefix."""
    return value.startswith("user-content-")


def rel(value: str) -> bool:
    return set(value.split()) <= {"noopener", "noreferrer", "nofollow"}


# Allowed on every element of the list, and never read, but the `lang` of a `pre`.
GLOBAL: dict[str, Rule] = {
    "dir": frozenset({"auto", "ltr", "rtl"}),
    "id": prefixed,
    "title": ANY_VALUE,
    "lang": ANY_VALUE,
}
SIZE = matches(re.compile(r"[0-9]+%?"))
DIGITS = matches(NUMBER)
# The attributes of GitHub's references and footnote links, never read.
LINK_DATA = dict.fromkeys(
    (
        "data-hovercard-type",
        "data-hovercard-url",
        "data-error-text",
        "data-id",
        "data-permission-text",
        "data-url",
        "data-octo-click",
        "data-octo-dimensions",
        "data-footnote-ref",
        "data-footnote-backref",
        "aria-describedby",
        "aria-label",
    ),
    ANY_VALUE,
)
PLAIN = (
    "strong",
    "b",
    "em",
    "i",
    "s",
    "strike",
    "sub",
    "sup",
    "tt",
    "kbd",
    "mark",
    "var",
    "samp",
    "br",
    "hr",
    "summary",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "markdown-accessiblity-table",
    "ruby",
    "rt",
    "dl",
    "dt",
    "dd",
    "picture",
)
# For each tag of the list, the attributes that it may carry besides GLOBAL.
LISTED_ATTRIBUTES: dict[str, dict[str, Rule]] = {
    **{tag: {} for tag in PLAIN},
    "p": {"align": ALIGNS, "class": frozenset({"markdown-alert-title"})},
    **{tag: {"align": ALIGNS} for tag in HEADINGS},
    "h2": {
        "align": ALIGNS,
        "class": frozenset({"sr-only"}),
        "id": lambda value: value == "footnote-label" or prefixed(value),
    },
    "ul": {"class": frozenset({"contains-task-list"})},
    "ol": {
        "start": DIGITS,
        "type": frozenset({"1", "a", "A", "i", "I"}),
        "class": frozenset({"contains-task-list"}),
    },
    "li": {"class": frozenset({"task-list-item"}), "value": DIGITS},
    "input": {
        "type": frozenset({"checkbox"}),
        "id": frozenset({""}),
        "disabled": ANY_VALUE,
        "checked": ANY_VALUE,
        "class": frozenset({"task-list-item-checkbox"}),
        "aria-label": frozenset({"Completed task", "Incomplete task"}),
    },
    "blockquote": {"cite": ANY_VALUE},
    "q": {"cite": ANY_VALUE},
    "del": {"cite": ANY_VALUE, "datetime": ANY_VALUE},
    "ins": {"cite": ANY_VALUE, "datetime": ANY_VALUE},
    "div": {
        "align": ALIGNS,
        "itemprop": ANY_VALUE,
        "class": classes(CODE_CLASSES, HIGHLIGHT),
        "data-snippet-clipboard-copy-content": ANY_VALUE,
    },
    "section": {"class": frozenset({"footnotes"}), "data-footnotes": frozenset({""})},
    "pre": {"class": frozenset({"notranslate"}), "lang": matches(LANGUAGE)},
    "code": {"class": frozenset({"notranslate"})},
    "span": {
        "class": lambda value: (
            set(value.split()) in ({"sr-only"}, CHIP)
            or all(one.startswith("pl-") for one in value.split())
        ),
        "style": matches(CHIP_STYLE),
    },
    "table": {"role": frozenset({"table"})},
    "th": {"align": ALIGNS, "colspan": DIGITS, "rowspan": DIGITS},
    "td": {"align": ALIGNS, "colspan": DIGITS, "rowspan": DIGITS},
    "details": {"open": frozenset({""})},
    "a": {
        "href": ANY_VALUE,
        "rel": rel,
        "target": frozenset({"_blank"}),
        "name": prefixed,
        "class": classes(REFERENCE_CLASSES | {"data-footnote-backref"}),
        **LINK_DATA,
    },
    "img": {
        "src": ANY_VALUE,
        "alt": ANY_VALUE,
        "data-canonical-src": ANY_VALUE,
        "width": SIZE,
        "height": SIZE,
        "align": ALIGNS,
        "data-animated-image": ANY_VALUE,
        "class": frozenset({"js-gh-image-fallback"}),
        "style": matches(PICTURE_STYLE),
    },
    "g-emoji": {
        "class": frozenset({"g-emoji"}),
        "alias": ANY_VALUE,
        "fallback-src": ANY_VALUE,
    },
    "themed-picture": {"data-catalyst-inline": frozenset({"true"})},
    "source": {"media": ANY_VALUE, "srcset": ANY_VALUE},
    "math-renderer": {
        "class": frozenset({"js-inline-math", "js-display-math"}),
        "style": frozenset({"display: inline-block", "display: block"}),
        "data-run-id": ANY_VALUE,
    },
}
# What the page never shows, and those of it that leave no space in their place.
HIDDEN_TAGS = frozenset({"rp", "relative-time", "video"})
LEAVE_NOTHING = frozenset({"rp"})
BACK_LINK = re.compile(r"↩(?:<sup>[0-9]+</sup>)?")
# A row of text that would start a block in Markdown, and a number of a list.
STARTS_BLOCK = re.compile(
    r"#{1,6}(?![^ \t])|>|[-+*](?![^ \t])|(?:[-*_][ \t]*){3,}$|[=-]+[ \t]*$"
    r"|`{3}|~{3}|\|"
)
LIST_NUMBER = re.compile(r"([0-9]{1,9})([.)])(?![^ \t])")
BLANK_RUN = re.compile(r"[ \t\r\f\v]+")
BACKTICK_RUNS = re.compile(r"`+")
TEX_THAT_HIDES = re.compile(r"[\\%&]")


class HtmlElement:
    """An element of GitHub's HTML: its tag, attributes and content, and whether its
    end came. A comment, a declaration, an instruction and an end tag that closes
    nothing are elements of a tag of their own that never ends."""

    __slots__ = ("tag", "attributes", "children", "ended")

    def __init__(self, tag: str, attributes: dict[str, str], ended: bool = False):
        self.tag = tag
        self.attributes = attributes
        self.children: list[HtmlElement | str] = []
        self.ended = ended

    def classes(self) -> set[str]:
        return set(self.attributes.get("class", "").split())


class TreeBuilder(HTMLParser):
    """The tree of GitHub's HTML, built with a stack of open elements."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = HtmlElement("", {}, ended=True)
        self.open = [self.root]
        self.names: Counter[str] = Counter()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes: dict[str, str] = {}
        for name, value in attrs:
            attributes.setdefault(name, value or "")
        element = HtmlElement(tag, attributes, ended=tag in VOID_TAGS)
        self.open[-1].children.append(element)
        if tag not in VOID_TAGS:
            self.open.append(element)
            self.names[tag] += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # `<div/>` opens a div in HTML: only a void element ends in its tag.
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if not self.names[tag]:
            self.open[-1].children.append(HtmlElement(f"</{tag}>", {}))
            return
        while True:
            element = self.open.pop()
            self.names[element.tag] -= 1
            if element.tag == tag:
                element.ended = True
                return

    def handle_data(self, data: str) -> None:
        self.open[-1].children.append(data.replace("\x00", "\ufffd"))

    def handle_comment(self, data: str) -> None:
        self.open[-1].children.append(HtmlElement("<!-- -->", {}))

    def handle_decl(self, decl: str) -> None:
        self.open[-1].children.append(HtmlElement("<!>", {}))

    def handle_pi(self, data: str) -> None:
        self.open[-1].children.append(HtmlElement("<?>", {}))

    def unknown_decl(self, data: str) -> None:
        self.handle_decl(data)

    def close(self) -> None:
        # A tag, a comment or other markup that the text leaves unfinished is no
        # text: html.parser would hand some of it on as text, in a way that differs
        # between the releases of Python, and a browser shows none of it.
        if self.rawdata.startswith("<"):
            self.rawdata = ""
            self.open[-1].children.append(HtmlElement("<markup without end>", {}))
        super().close()


def tree_of(html: str) -> HtmlElement:
    builder = TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def unknown_part(element: HtmlElement) -> str | None:
    """What the converter doesn't know of the element, by name, or None when it
    knows its tag and each of its attributes, classes and values."""
    tag = element.tag
    if not element.ended:
        return f"element without end <{tag}>" if tag[:1].isalpha() else tag
    allowed = LISTED_ATTRIBUTES.get(tag)
    if allowed is None:
        return f"tag <{tag}>"
    for name, value in element.attributes.items():
        if name in allowed:
            rule = allowed[name]
        elif name in GLOBAL:
            rule = GLOBAL[name]
        else:
            return f"attribute {tag}[{name}]"
        if rule is None:
            continue
        if not (value in rule if isinstance(rule, frozenset) else rule(value)):
            return f"value {tag}[{name}={value[:40]!r}]"
    return None


def ticks(text: str, least: int) -> str:
    longest = max((len(run) for run in BACKTICK_RUNS.findall(text)), default=0)
    return "`" * max(least, longest + 1)


def escaped(row: str) -> str:
    """The row with a backslash where it would start a block of Markdown, so that
    no reader takes a paragraph for a heading, a field of a form or a box."""
    number = LIST_NUMBER.match(row)
    if number:
        return f"{number[1]}\\{row[len(number[1]) :]}"
    if STARTS_BLOCK.match(row):
        return "\\" + row
    return row


class Holdings:
    """Whether the source holds a text, in time linear in the source over a whole
    conversion: each search starts where the last one found its text, and once the
    searches have read the source BUDGET times over, the source holds nothing more,
    which is the safe side for a link and a reference."""

    BUDGET = 16

    def __init__(self, source: str):
        self.texts = {False: source, True: source.lower()}
        self.cursor = {False: 0, True: 0}
        self.left = self.BUDGET * (len(source) + 1)
        self.known: dict[tuple[str, bool], bool] = {}

    def holds(self, text: str, ignore_case: bool = False) -> bool:
        if not text:
            return False
        if ignore_case:
            text = text.lower()
        key = (text, ignore_case)
        if key not in self.known:
            self.known[key] = self.search(text, ignore_case)
        return self.known[key]

    def search(self, text: str, ignore_case: bool) -> bool:
        source, cursor = self.texts[ignore_case], self.cursor[ignore_case]
        if self.left <= 0:
            return False
        found = source.find(text, cursor)
        if found < 0:
            found = source.find(text, 0, cursor + len(text))
            self.left -= len(source)
        else:
            self.left -= found - cursor + len(text)
        if found >= 0:
            self.cursor[ignore_case] = found
        return found >= 0

    def address(self, href: str) -> str:
        """The address as the source holds it, as GitHub wrote it or with its
        %-escapes read; empty where the source holds neither."""
        if self.holds(href):
            return href
        if "%" in href and self.holds(unquote(href)):
            return unquote(href)
        return ""


@dataclass
class RowMarks:
    """The marks of a quote or a list item before its rows: `first` before its first
    row, `rest` before every other."""

    first: str
    rest: str
    used: bool = False


@dataclass
class TableRows:
    """The rows of a table as its cells come, and the text around them."""

    rows: list[list[str]] = field(default_factory=list)
    aligns: list[str] = field(default_factory=list)
    heads: int = 0
    spans: dict[int, int] = field(default_factory=dict)
    loose: list[str] = field(default_factory=list)


Exit = Callable[[], None]


class MarkdownRows:
    """The rows of the Markdown, each with the marks of its quotes and list items,
    and the inline text of the paragraph that is being written.

    A mark of emphasis, a quote or a link is a placeholder in the paragraph until
    its element ends: it is written only when its content is in the same paragraph.
    """

    def __init__(self, notes: bool, counts: dict[str, Counter[str]]):
        self.notes = notes
        self.counts = counts
        self.rows: list[str] = []
        self.length = 0
        self.cut = False
        self.prefixes: list[RowMarks] = []
        self.rest = ""
        self.blank = False
        self.inline: list[str] = []
        self.paragraph = 0
        self.words = 0
        self.lead = ""
        self.one_row = 0
        self.cell: list[str] | None = None

    def prefix(self) -> str:
        if self.prefixes and not self.prefixes[-1].used:
            text = "".join(
                mark.rest if mark.used else mark.first for mark in self.prefixes
            )
            for mark in self.prefixes:
                mark.used = True
            self.rest = "".join(mark.rest for mark in self.prefixes)
            return text
        return self.rest

    def push(self, first: str, rest: str) -> None:
        self.flush()
        self.prefixes.append(RowMarks(first, rest))

    def pop(self, empty_row: bool = False) -> None:
        """Ends a quote or a list item; an empty list item keeps its row."""
        self.flush()
        mark = self.prefixes[-1]
        if not mark.used and empty_row:
            self.row("")
        self.prefixes.pop()
        self.rest = "".join(mark.rest for mark in self.prefixes)

    def row(self, text: str) -> None:
        """Writes a row, after a blank row where a block ended before it."""
        if self.cut:
            return
        if self.blank and self.rows:
            # Before the first row of a quote or a list item, `rest` still holds
            # only the marks of the containers around it.
            self.write(self.rest.rstrip())
        self.blank = False
        self.write((self.prefix() + text).rstrip())

    def write(self, line: str) -> None:
        if self.cut:
            return
        room = LONGEST - self.length
        if len(line) >= room:
            self.rows.append(line[: max(room, 0)])
            self.cut = True
            self.counts["cut"]["text"] += 1
            self.rows.append(CUT_NOTE if self.notes else " ")
            return
        self.rows.append(line)
        self.length += len(line) + 1

    def note(self, note: str) -> None:
        self.add(note if self.notes else " ")

    def add(self, text: str) -> None:
        """Adds inline text to the paragraph."""
        if text:
            self.inline.append(text)
            if not text.isspace():
                self.words += 1

    def text(self, data: str) -> None:
        data = BLANK_RUN.sub(" ", data)
        if self.one_row or self.cell is not None:
            data = data.replace("\n", " ")
        if data[:1] == " " and self.inline and self.inline[-1][-1:] == " ":
            data = data[1:]
        self.add(data)

    def space(self) -> None:
        if not self.inline or not self.inline[-1][-1:].isspace():
            self.inline.append(" ")

    def mark(self) -> tuple[int, int, int]:
        """A placeholder for the mark before an inline element."""
        self.inline.append("")
        return len(self.inline) - 1, self.paragraph, self.words

    def enclose(self, place: tuple[int, int, int], mark: str, end: str = "") -> None:
        index, paragraph, words = place
        if paragraph == self.paragraph and self.words > words:
            self.inline[index] = mark
            self.inline.append(end or mark)

    def since(self, place: tuple[int, int, int]) -> str | None:
        """The inline text since the placeholder, or None after a block."""
        if place[1] != self.paragraph:
            return None
        return "".join(self.inline[place[0] :])

    def flush(self) -> None:
        """Writes the paragraph as its rows: each row of text with a backslash where
        it would start a block, and the heading before the first."""
        text, lead = "".join(self.inline), self.lead
        self.inline, self.lead = [], ""
        self.paragraph += 1
        rows = [row.strip() for row in text.split("\n")]
        rows = [row for row in rows if row]
        if not rows:
            return
        for number, row in enumerate(rows):
            self.row(lead + row if lead and not number else escaped(row))
        self.blank = True

    def end_block(self) -> None:
        self.flush()
        self.blank = True

    def end_cell(self) -> str:
        assert self.cell is not None
        text = BLANK_RUN.sub(" ", "".join(self.inline).replace("\n", " ")).strip()
        self.inline = []
        self.paragraph += 1
        return text.replace("|", "\\|")

    def markdown(self) -> str:
        self.flush()
        return "\n".join(self.rows).strip("\n")


class HtmlWalk:
    """One walk over the tree, with a stack of its own: each element of the list
    gives its Markdown as it starts and ends, and the content of code, math and
    references is read at once by `text_within`."""

    def __init__(self, source: str, notes: bool):
        self.counts: dict[str, Counter[str]] = {kind: Counter() for kind in TAKEN_OUT}
        self.out = MarkdownRows(notes, self.counts)
        self.held = Holdings(source)
        self.addresses: list[str] = []
        self.table: TableRows | None = None
        self.stack: list[list[Any]] = []

    def run(self, root: HtmlElement) -> None:
        self.stack = [[root, 0, None, None]]
        while self.stack:
            frame = self.stack[-1]
            node: HtmlElement = frame[0]
            if frame[1] == len(node.children):
                self.stack.pop()
                if frame[2] is not None:
                    frame[2]()
                continue
            child = node.children[frame[1]]
            frame[1] += 1
            if isinstance(child, str):
                self.text(child)
            elif len(self.stack) > DEEPEST:
                self.counts["depth"][f"content below {DEEPEST} elements"] += 1
                self.out.space()
            else:
                self.enter(child, node)

    def descend(
        self, element: HtmlElement, exit: Exit | None = None, state: Any = None
    ) -> None:
        self.stack.append([element, 0, exit, state])

    def text(self, data: str) -> None:
        table = self.table
        if table is not None and self.out.cell is None:
            if data.strip():
                table.loose.append(data)
            return
        self.out.text(data)

    def unknown(self, name: str) -> None:
        self.counts["unknown"][name] += 1
        self.out.space()

    def hidden(self, name: str, space: bool = True) -> None:
        self.counts["hidden"][name] += 1
        if space:
            self.out.space()

    def enter(self, element: HtmlElement, parent: HtmlElement) -> None:
        tag = element.tag
        if element.ended and tag in HIDDEN_TAGS:
            self.hidden(tag, tag not in LEAVE_NOTHING)
            return
        if tag == "section" and "js-render-needs-enrichment" in element.classes():
            self.drawing(element.attributes.get("data-type", "drawing"))
            return
        problem = unknown_part(element)
        if problem is not None:
            self.unknown(problem)
            return
        if self.out.cell is not None and tag in BLOCK_ELEMENTS - {"pre"}:
            self.out.space()
            self.descend(element, self.out.space)
            return
        if tag in BLOCK_ELEMENTS:
            self.enter_block(element, parent)
        else:
            self.enter_inline(element, parent)

    def drawing(self, kind: str) -> None:
        self.counts["drawings"][kind] += 1
        if self.out.cell is not None or not self.out.notes:
            self.out.space()
            return
        self.out.flush()
        self.out.row(DRAWING_NOTE)
        self.out.blank = True

    def enter_block(self, element: HtmlElement, parent: HtmlElement) -> None:
        tag, out = element.tag, self.out
        if tag in HEADINGS:
            self.heading(element)
        elif tag in ("ul", "ol"):
            out.flush()
            if parent.tag == "li":
                out.blank = False
            start = element.attributes.get("start", "1")
            footnotes = parent.tag == "section" and "footnotes" in parent.classes()
            self.descend(element, out.end_block, [tag, int(start), footnotes, 0])
        elif tag == "li":
            self.item(element, parent)
        elif tag == "blockquote":
            out.push("> ", "> ")
            self.descend(element, self.end_container)
        elif tag == "pre":
            self.fence(element, parent)
        elif tag == "hr":
            out.flush()
            out.row("---")
            out.blank = True
        elif tag == "table":
            self.start_table(element)
        elif tag in ("tr", "th", "td") and self.table is not None:
            self.table_part(element)
        elif tag == "summary":
            out.flush()
            out.one_row += 1
            place = out.mark()
            self.descend(element, lambda: self.end_summary(place))
        elif tag == "p" and "markdown-alert-title" in element.classes():
            return
        elif tag == "div" and "markdown-alert" in element.classes():
            kind = next(
                (k for k in ALERTS if f"markdown-alert-{k}" in element.classes()),
                "note",
            )
            out.push("> ", "> ")
            out.row(f"[!{kind.upper()}]")
            self.descend(element, self.end_container)
        elif tag in ("dt", "dd"):
            out.flush()
            self.descend(element, self.end_row)
        else:
            out.flush()
            self.descend(element, out.end_block)

    def end_container(self) -> None:
        self.out.pop()
        self.out.blank = True

    def end_row(self) -> None:
        self.out.flush()
        self.out.blank = False

    def end_summary(self, place: tuple[int, int, int]) -> None:
        self.out.enclose(place, "**")
        self.out.one_row -= 1
        self.out.end_block()

    def heading(self, element: HtmlElement) -> None:
        out = self.out
        if "sr-only" in element.classes():
            self.hidden("h2.sr-only", False)
            return
        out.flush()
        out.lead = "#" * HEADINGS[element.tag] + " "
        out.one_row += 1

        def end() -> None:
            out.one_row -= 1
            out.end_block()

        self.descend(element, end)

    def item(self, element: HtmlElement, parent: HtmlElement) -> None:
        state = self.stack[-1][3] if parent.tag in ("ul", "ol") else None
        if state is None:
            self.out.flush()
            self.descend(element, self.out.end_block)
            return
        kind, number, footnotes, place = state
        if "value" in element.attributes:
            number = int(element.attributes["value"])
        if footnotes:
            place += 1
            marker = f"[^{place}]: "
            rest = "    "
        else:
            marker = f"{number}. " if kind == "ol" else "- "
            rest = " " * len(marker)
        state[1], state[3] = number + 1, place
        self.out.push(marker, rest)

        def end() -> None:
            self.out.pop(empty_row=True)
            self.out.blank = False

        self.descend(element, end)

    def enter_inline(self, element: HtmlElement, parent: HtmlElement) -> None:
        tag, out = element.tag, self.out
        if tag == "br":
            out.text("\n" if not out.one_row else " ")
        elif tag in ("img", "input", "source"):
            self.leaf(element)
        elif tag == "code":
            self.code_span(self.text_within(element, " ")[0])
        elif tag == "math-renderer":
            self.math(element)
        elif tag == "sup" and self.footnote_reference(element):
            return
        elif tag == "a":
            self.link(element)
        elif tag == "span" and "sr-only" in element.classes():
            self.hidden("span.sr-only", False)
        elif tag == "span" and element.classes() == CHIP:
            return
        elif tag in MARKS:
            place = out.mark()
            self.descend(element, lambda: out.enclose(place, MARKS[tag]))
        else:
            self.descend(element)

    def leaf(self, element: HtmlElement) -> None:
        if element.tag == "img":
            self.picture(element, "")
        elif element.tag == "input":
            self.out.add("[x]" if "checked" in element.attributes else "[ ]")

    def picture(self, element: HtmlElement, link: str) -> None:
        """A picture with the first address that the source holds of its own, of its
        `src` and of its link."""
        attributes = element.attributes
        alt = BLANK_RUN.sub(" ", attributes.get("alt", "").replace("\n", " ")).strip()
        candidates = (
            attributes.get("data-canonical-src", ""),
            attributes.get("src", ""),
        )
        address = next(
            (held for one in (*candidates, link) if (held := self.held.address(one))),
            "",
        )
        if address:
            self.addresses.append(address)
        self.out.add(f"![{alt}]({address})")

    def code_span(self, code: str) -> None:
        code = code.replace("\n", " ")
        marks = ticks(code, 1)
        pad = " " if code.startswith("`") or code.endswith("`") else ""
        self.out.add(f"{marks}{pad}{code}{pad}{marks}")

    def math(self, element: HtmlElement) -> None:
        if any(isinstance(child, HtmlElement) for child in element.children):
            self.counts["unknown"]["element in math"] += 1
            self.out.note(MATH_NOTE)
            return
        tex = "".join(str(child) for child in element.children)
        if TEX_THAT_HIDES.search(tex):
            self.counts["math"]["math"] += 1
            self.out.note(MATH_NOTE)
            return
        self.out.text(tex)

    def footnote_reference(self, element: HtmlElement) -> bool:
        """Writes `[^n]` for a `sup` that holds only a footnote link whose text is a
        number; False where the `sup` and its link are read as any other."""
        children = element.children
        if len(children) != 1 or not isinstance(children[0], HtmlElement):
            return False
        link = children[0]
        if link.tag != "a" or "data-footnote-ref" not in link.attributes:
            return False
        if unknown_part(link) is not None:
            return False
        if any(isinstance(child, HtmlElement) for child in link.children):
            self.unknown("element in a footnote reference")
            return True
        number = "".join(str(child) for child in link.children)
        if not number.isascii() or not number.isdigit():
            return False
        self.out.add(f"[^{number}]")
        return True

    def link(self, element: HtmlElement) -> None:
        attributes, classes = element.attributes, element.classes()
        href = attributes.get("href", "")
        if "data-footnote-backref" in classes or "data-footnote-backref" in attributes:
            self.back_link(element)
            return
        if classes & REFERENCES:
            self.reference(element, href)
            return
        content = [
            child
            for child in element.children
            if not (isinstance(child, str) and child.isspace())
        ]
        if (
            len(content) == 1
            and isinstance(content[0], HtmlElement)
            and content[0].tag == "img"
            and unknown_part(content[0]) is None
        ):
            self.picture(content[0], href)
            return
        place = self.out.mark()
        self.descend(element, lambda: self.end_link(place, href))

    def end_link(self, place: tuple[int, int, int], href: str) -> None:
        text = self.out.since(place)
        if text is None or not href or not text.strip():
            return
        shown = text.strip()
        held = self.held.address(href)
        if href in (shown, f"mailto:{shown}", f"http://{shown}", f"https://{shown}"):
            if held:
                self.addresses.append(held)
            return
        if held:
            self.addresses.append(held)
            self.out.enclose(place, "[", f"]({held})")
        else:
            self.counts["targets"]["link"] += 1

    def back_link(self, element: HtmlElement) -> None:
        text = "".join(
            child
            if isinstance(child, str)
            else f"<{child.tag}>{''.join(map(str, child.children))}</{child.tag}>"
            for child in element.children
        )
        if BACK_LINK.fullmatch(text):
            self.hidden("back link", False)
        else:
            self.counts["unknown"]["back link with other text"] += 1

    def reference(self, element: HtmlElement, href: str) -> None:
        """A reference as its text where the source holds that text, ignoring case,
        else as its address where the source holds that, else as nothing."""
        text, known = self.text_within(element, " ", count=False)
        text = BLANK_RUN.sub(" ", text).strip()
        if known and self.held.holds(text, ignore_case=True):
            self.out.add(text)
        elif held := self.held.address(href):
            self.addresses.append(held)
            self.out.add(held)
        else:
            self.counts["references"]["reference"] += 1
            self.out.space()

    def fence(self, element: HtmlElement, parent: HtmlElement) -> None:
        language = element.attributes.get("lang", "")
        if parent.tag == "div":
            for one in sorted(parent.classes()):
                found = HIGHLIGHT.fullmatch(one)
                if found:
                    language = found[1]
        if language.lower() in DRAWN:
            self.drawing(language.lower())
            return
        children = [child for child in element.children if child != ""]
        inside = element
        if len(children) == 1 and isinstance(children[0], HtmlElement):
            code = children[0]
            if code.tag == "code":
                problem = unknown_part(code)
                if problem is not None:
                    self.unknown(problem)
                    return
                inside = code
        body = self.text_within(inside, "\n")[0].removesuffix("\n")
        if self.out.cell is not None:
            self.code_span(body)
            return
        out = self.out
        out.flush()
        marks = ticks(body, 3)
        out.row(marks + language)
        for line in body.split("\n"):
            if out.cut:
                break
            out.write(out.prefix() + line if line else out.rest.rstrip())
        out.write(out.rest + marks)
        out.blank = True

    def text_within(
        self, element: HtmlElement, newline: str, count: bool = True
    ) -> tuple[str, bool]:
        """The text of code, or of a reference: an inline element of the list gives
        its text without marks and a picture its `alt`, a line break `newline`;
        anything else gives a space and counts. With `count` False, nothing counts,
        and the second part says whether all of it was known."""
        parts: list[str] = []
        known = True
        stack: list[tuple[HtmlElement, int]] = [(element, 0)]
        depth = len(self.stack)
        while stack:
            node, index = stack.pop()
            if index == len(node.children):
                continue
            stack.append((node, index + 1))
            child = node.children[index]
            if isinstance(child, str):
                parts.append(child)
                continue
            name = self.code_part(child, depth + len(stack))
            if name is None:
                if child.tag == "img":
                    parts.append(child.attributes.get("alt", ""))
                elif child.tag == "br":
                    parts.append(newline)
                elif child.classes() != CHIP:
                    stack.append((child, 0))
                continue
            known = False
            silent = name in LEAVE_NOTHING or name == "span.sr-only"
            if not silent and (not parts or not parts[-1][-1:].isspace()):
                parts.append(" ")
            if count:
                kind = "hidden" if name in HIDDEN_TAGS | {"span.sr-only"} else "unknown"
                if name.startswith("content below"):
                    kind = "depth"
                if (
                    kind == "unknown"
                    and name.startswith("tag <")
                    and name[5:-1] in (BLOCK_ELEMENTS | {"math-renderer", "input"})
                ):
                    name = f"in code <{name[5:-1]}>"
                self.counts[kind][name] += 1
        return "".join(parts), known

    def code_part(self, child: HtmlElement, depth: int) -> str | None:
        """None for an element that code shows the text of, or its name."""
        if depth > DEEPEST:
            return f"content below {DEEPEST} elements"
        if child.ended and child.tag in HIDDEN_TAGS:
            return child.tag
        problem = unknown_part(child)
        if problem is not None:
            return problem
        if child.tag == "span" and "sr-only" in child.classes():
            return "span.sr-only"
        if child.tag in INLINE_TAGS or child.tag in ("img", "br"):
            return None
        return f"tag <{child.tag}>"

    def start_table(self, element: HtmlElement) -> None:
        out = self.out
        out.flush()
        outer = self.table
        self.table = TableRows()

        def end() -> None:
            table = self.table
            self.table = outer
            assert table is not None
            self.write_table(table)

        self.descend(element, end)

    def table_part(self, element: HtmlElement) -> None:
        table = self.table
        assert table is not None
        if element.tag == "tr":
            table.rows.append([])
            self.fill_spans(table)
            self.descend(element, lambda: self.fill_spans(table, True))
            return
        if not table.rows:
            table.rows.append([])
        out = self.out
        out.cell = []
        heading = any(frame[0].tag == "thead" for frame in self.stack)

        def end() -> None:
            text = out.end_cell()
            out.cell = None
            if heading:
                table.heads = len(table.rows)
            row = table.rows[-1]
            column = len(row)
            if column >= WIDEST:
                self.counts["cut"][f"cells past {WIDEST} columns"] += 1
                return
            if len(table.aligns) <= column:
                table.aligns.append(element.attributes.get("align", ""))
            row.append(text)
            span = int(element.attributes.get("colspan", "1") or 1)
            down = int(element.attributes.get("rowspan", "1") or 1)
            if span > WIDEST - column:
                self.counts["cut"][f"spans past {WIDEST} columns"] += 1
                span = WIDEST - column
            for extra in range(span):
                if down > 1:
                    table.spans[column + extra] = min(down, 1000) - 1
                if extra:
                    row.append("")
            self.fill_spans(table)

        self.descend(element, end)

    def fill_spans(self, table: TableRows, finished: bool = False) -> None:
        """Adds an empty cell in each column that a cell above spans down into.

        `spans` holds only the columns that still span, at most WIDEST of them, so
        that each row costs at most WIDEST cells."""
        row = table.rows[-1]
        while len(row) in table.spans:
            spanned(table, len(row))
            row.append("")
        if finished:
            for column in sorted(table.spans):
                if column >= len(row):
                    row.extend([""] * (column - len(row) + 1))
                    spanned(table, column)

    def write_table(self, table: TableRows) -> None:
        out = self.out
        if table.loose:
            out.text(" ".join(table.loose))
            out.end_block()
        rows = [row for row in table.rows if row]
        if not rows:
            return
        width = max(len(row) for row in rows)
        if table.heads:
            head, rows = rows[0], rows[1:]
        else:
            head = []
        aligns = table.aligns + [""] * (width - len(table.aligns))
        delimiter = {"left": ":--", "right": "--:", "center": ":-:"}
        out.flush()
        for row in [head, [delimiter.get(a, "---") for a in aligns[:width]], *rows]:
            if out.cut:
                # The rows past the cut give no text: padding them would cost
                # time that grows with the rows times the width.
                break
            cells = row + [""] * (width - len(row))
            out.row("| " + " | ".join(cells) + " |")
        out.blank = True


def spanned(table: TableRows, column: int) -> None:
    """One row less for the span of the column, which ends when none is left."""
    table.spans[column] -= 1
    if not table.spans[column]:
        del table.spans[column]


def markdown_of(html: str, source: str, notes: bool = True) -> Converted:
    """The Markdown that the AI reads of a text, made from GitHub's HTML of it, by
    the closed list of this module; `source` is the text as written, which the rules
    of references and addresses read. With `notes` False, each note is a single
    space, as `as_shown` reads it, so that the decisions can read the same call."""
    walk = HtmlWalk(source or "", notes)
    walk.run(tree_of(html or ""))
    return Converted(walk.out.markdown(), tuple(walk.addresses), walk.counts)
