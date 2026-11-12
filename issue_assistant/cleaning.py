"""The cleaning of text that the engine wrote, before any of it reaches GitHub."""

from __future__ import annotations

import html
import re
import subprocess
from collections.abc import Callable, Iterable, Iterator
from itertools import accumulate
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from .constants import MAX_NUMBER
from .languages import say
from .patterns import (
    CODE,
    CROSS_REFERENCE,
    HTML_COMMENT,
    INVISIBLE,
    ISSUE_REFERENCE,
    Checked,
)
from .settings import Config

# Control characters other than tab and line break, which no comment needs: Python
# takes some of them for spaces, which Markdown doesn't, so they could make the two
# see different lines.
STRAY_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

# Also a link without a target, [text](), which points to the page itself. A target
# with a ( is no link here: Markdown's target runs on past the ) that closes it.
MARKDOWN_LINK = re.compile(
    r"(?<!!)\[([^\]\n]*)\]\(\s*<?([^()\s>\0]*)>?(?:\s+\"[^\"]*\")?\s*\)"
)
LINK_DEFINITION = re.compile(r"^ {0,3}\[[^\]\n]*\]:.*$", re.MULTILINE)
IMAGE = re.compile(r"!\[([^\]\n]*)\]\([^)\n]*\)")
# GitHub links http:// and https:// in any case, also right after a digit or an
# underscore: only letters before them make another scheme, such as xhttps://.
BARE_URL = re.compile(r"(?<![A-Za-z])https?://[^\s)<>\]`\0]+", re.IGNORECASE)
# It links www. after an underscore too, as in x_www.…: only a letter, a digit, a
# dot or a dash before it leaves it text. It links www. alone too, to the host www.
WWW_URL = re.compile(r"(?<![^\W_])(?<![.-])www\.[^\s<>`\0]*", re.IGNORECASE)
# GitHub links a bare address only after these characters or at the start.
AUTOLINK_AFTER = frozenset(" \t\n*_~(")
# A www. address stays a link only after the others: a later rule can put an
# underscore before it into code, as in @a/b_www.…, and after code GitHub links no
# www. address, so the rest of it would be text that could mention.
WWW_AFTER = AUTOLINK_AFTER - {"_"}
# Its link runs to the next space, past brackets, backticks and the code that
# follows, and then leaves out these characters at its end.
SPACE = re.compile(r"[ \t\n]")
TRAILING = frozenset(")?!.,:;*_~'\"")
# GitHub turns these into a mention or a link at the start of a piece of text and
# after any character but a letter or digit; an underscore can start or end an
# emphasis, so a reference counts whatever follows its number.
MENTION = re.compile(r"(?<![A-Za-z0-9])@([A-Za-z0-9][A-Za-z0-9-]{0,38}(?:/[\w-]+)?)")

# GitHub also links an e-mail address, with or without mailto: or xmpp:, and a
# commit of another repository or of a fork: owner/repository@sha or user@sha. Each
# pattern starts only where a run of the characters it reads starts, so that it
# reads each character once.
EMAIL = re.compile(
    r"(?<![\w.+-])(?:(?:mailto|xmpp):)?[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE
)
COMMIT_REFERENCE = re.compile(
    r"(?<![\w.-])(?:[\w.-]+/)?[\w.-]+@[0-9a-f]{7}\w*", re.IGNORECASE
)
# Three tildes or more at the start of a line, which would start or end a fenced
# block: text can't escape them with a backtick, as it does with the other fence.
TILDE_FENCE = re.compile(r"^([ \t]*)(?=~~~)", re.MULTILINE)
# A hyphen next to a pipe or a colon, perhaps with spaces between. Each line of
# hyphens with a pipe or a colon has one, such as `--|--` or `:-:`, the delimiter row
# below the header of a table, and GitHub splits every row of a table at each pipe,
# also inside code, so that the rest of the code would become text.
TABLE_HYPHEN = re.compile(r"(?<=[|:])([ \t]*)-|-(?=[ \t]*[|:])")

# A line that starts with `<!--`: on GitHub, an HTML block that runs to its `-->`, or
# without one, hides the rest of the text.
COMMENT_LINE = re.compile(r"^ {0,3}<!--", re.MULTILINE)
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

# The marks of a heading, without the line break after them.
HEADING_LINE = re.compile(r"^[ \t]{0,3}#{1,6}(?:[ \t]+|$)", re.MULTILINE)


def allowed_url(url: str, config: Config) -> bool:
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError:
        # Such as an unclosed IPv6 address: http://[
        return False
    # A backtick or backslash in a link could pair with code elsewhere in the text.
    if parts.scheme != "https" or "`" in url or "\\" in url:
        return False
    # The host alone: a user, a password or a port could hide where a link leads.
    if parts.netloc.lower() != hostname:
        return False
    # Browsers resolve . and .. in a path, also written as %2e, so a path that starts
    # in the repository could lead to any other one.
    segments = unquote(parts.path).split("/")
    if "%2e" in parts.path.lower() or "." in segments or ".." in segments:
        return False
    if hostname == "github.com":
        return bool(
            re.match(
                rf"^/{re.escape(config.repository)}/(?:blob|tree|releases)/", parts.path
            )
        )
    return hostname in config.hosts


def clean_prose(
    text: str,
    numbers: set[Checked],
    config: Config,
    before: str = " ",
    after: str = " ",
    *,
    lines: bool = True,
) -> str:
    """Text outside code without anything that GitHub turns into a mention, a
    reference, a foreign link, an image or HTML.

    `before` and `after` are the characters next to the text in the comment: a
    space at its edges, or the code around it, and `lines` is whether the text that
    it is part of has more than one line. First, the rules that remove something
    repeat until nothing changes, because a removal can join the characters around
    it into something new. Then the rules that keep or rewrite something repeat,
    each putting what it wrote behind a placeholder, so that no later rule changes
    it again: as they remove nothing, the characters next to a placeholder stay the
    ones they were checked with. Last, the characters that could start code, a
    link, an image, a fence or a table, escape the code written here or form an
    entity are escaped.
    """
    kept: list[str] = []

    def keep(value: str) -> str:
        kept.append(value)
        return f"\0{len(kept) - 1}\0"

    def code(value: str) -> str:
        return keep(f"`{value}`")

    def target(url: str) -> bool:
        # Markdown reads the entities in the target of a link, such as &#46; for a
        # dot, which could make a segment `..`.
        return allowed_url(url, config) and allowed_url(html.unescape(url), config)

    def link(match: re.Match[str]) -> str:
        # The link's text stays in the text, so the later rules clean it too. The
        # removals left only allowed links; should another one ever form, its
        # brackets are escaped below like any others.
        return (
            keep("[") + match[1] + keep(f"]({match[2]})")
            if target(match[2])
            else match[0]
        )

    def address(
        url: str, match: re.Match[str], starts: frozenset[str] = AUTOLINK_AFTER
    ) -> str:
        start, end = match.span()
        space = SPACE.search(match.string, end)
        rest = match.string[end : space.start() if space else None]
        # An address stays a link only where GitHub links all of it and nothing
        # more, and becomes code elsewhere: at the start or after one of `starts`
        # (other text before it, as in @www.…, leaves it text that could mention),
        # and before a space or the end, perhaps after characters that GitHub
        # leaves out of a link, such as ). and ,. Its link would run on past
        # anything else, even into code: https://docs.example.com`@evil.example/`
        # leads to evil.example.
        linked = (
            (match.string[start - 1] if start else before) in starts
            and (space[0] if space else after) in " \t\n"
            and set(rest) <= TRAILING
        )
        return keep(match[0]) if linked and allowed_url(url, config) else code(match[0])

    previous = None
    while text != previous:
        previous = text
        text = strip_html(text)
        text = LINK_DEFINITION.sub("", text)
        text = HEADING_LINE.sub("", text)
        text = IMAGE.sub(r"\1", text)
        text = MARKDOWN_LINK.sub(lambda m: m[0] if target(m[2]) else m[1], text)

    def reference(match: re.Match[str]) -> str:
        # Only a checked number stays a link, and not after an @: once the text
        # before the @ becomes code, the @ starts GitHub's text, and @GH-7 would
        # mention the user GH-7.
        start = match.start()
        checked = int(match[1]) in numbers and match.string[start - 1 : start] != "@"
        return keep(match[0]) if checked else code(match[0])

    names = {
        str(item).rpartition("#")[0].casefold(): str(item).rpartition("#")[0]
        for item in numbers
        if isinstance(item, str)
    }

    def cross(match: re.Match[str]) -> str:
        # Only a checked issue of a related repository stays a link, and one with
        # its address, which no emphasis around it can split, as it could split a
        # name like owner/name_ from its #12; not after an !, which would make the
        # link an image.
        start = match.start()
        found = cross_reference(match[1], names)
        if found in numbers and (match.string[start - 1] if start else before) != "!":
            return keep(elsewhere_link(str(found)))
        return code(match[1])

    rewrites: list[tuple[re.Pattern[str], Callable[[re.Match[str]], str]]] = [
        (MARKDOWN_LINK, link),
        (BARE_URL, lambda m: address(m[0], m)),
        (WWW_URL, lambda m: address(f"https://{m[0]}", m, WWW_AFTER)),
        (EMAIL, lambda m: code(m[0])),
        (COMMIT_REFERENCE, lambda m: code(m[0])),
        (MENTION, lambda m: code(m[0])),
        (CROSS_REFERENCE, cross),
        (ISSUE_REFERENCE, reference),
    ]
    previous = None
    while text != previous:
        previous = text
        for pattern, rewrite in rewrites:
            text = settled(pattern, rewrite, text)
    # Brackets too: links and images can span lines and nest brackets, so no other
    # link or image than the ones kept above may form.
    for character, escaped in (
        ("\\", "\\\\"),
        ("`", "\\`"),
        ("&", "&amp;"),
        ("<", "&lt;"),
        ("[", "\\["),
        ("]", "\\]"),
    ):
        text = text.replace(character, escaped)
    # At any indentation, because trimming the cleaned text can take the
    # indentation of its first line.
    text = TILDE_FENCE.sub(r"\1\\", text)
    # No table without its delimiter row, which needs a line below the header: a
    # text of one line, such as a line of the log, keeps its hyphens.
    if lines:
        text = TABLE_HYPHEN.sub(lambda m: f"{m[1] or ''}\\-", text)
    pieces = re.split(r"\0(\d+)\0", text)
    return joined(
        kept[int(piece)] if index % 2 else piece for index, piece in enumerate(pieces)
    )


def cross_reference(text: str, names: dict[str, str]) -> str | None:
    """The issue that a reference such as owner/name#12 names, as name#number with
    the name of the repository in `names`, by its name in lower case; None for a
    repository that `names` doesn't have, or for no number of an issue."""
    repository, _, number = text.rpartition("#")
    name = names.get(repository.casefold())
    # Ten digits at most, so that int() never reads a number of a thousand.
    if name is None or len(number) > len(str(MAX_NUMBER)):
        return None
    value = int(number)
    return f"{name}#{value}" if 0 < value <= MAX_NUMBER else None


def elsewhere_link(reference: str) -> str:
    """The link of an issue of a related repository, owner/name#12."""
    repository, _, number = reference.rpartition("#")
    return f"[{reference}](https://github.com/{repository}/issues/{number})"


def settled(
    pattern: re.Pattern[str], rewrite: Callable[[re.Match[str]], str], text: str
) -> str:
    """The text with each match of the pattern rewritten, again until none is left.

    A rewrite can free the next match glued to it, as in @a@b; repeating one rule
    on its own spares the other rules a reading of the text for each.
    """
    previous = None
    while text != previous:
        previous, text = text, pattern.sub(rewrite, text)
    return text


def comment_parts(text: str) -> tuple[str, str]:
    """A text in two parts: up to the end of its last `-->`, and what GitHub shows of
    the rest.

    In the first part, a `-->` follows every `<!--`, so a pattern finds the end of
    each comment without reading on to the end of the text. In the rest, no comment
    can end: a `<!--` within a line stays text, as GitHub shows it, and one that
    starts a line hides the rest of the text, as GitHub hides it.
    """
    last = text.rfind("-->")
    middle = last + 3 if last >= 0 else 0
    hidden = COMMENT_LINE.search(text, middle)
    return text[:middle], text[middle : hidden.start() if hidden else len(text)]


def strip_html(text: str) -> str:
    """The text as GitHub shows it, without HTML comments and tags, in time linear
    in its length."""
    comments, rest = comment_parts(text)
    return HTML.sub(open_tag, comments) + HTML_TAG.sub(open_tag, rest)


def open_tag(match: re.Match[str]) -> str:
    """What stays of an HTML match: a tag start that no `>` closes, or nothing."""
    return match["open"] or ""


# Besides HTML, GitHub shows nothing of a link reference definition, such as
# `[//]: # (a comment)` or `[x]: <> "a comment"`, the usual ways to write a comment
# in Markdown, and of a footnote that nothing refers to, such as `[^1]: a comment`.
# A note takes the place of each in the text for the engine, so that it doesn't
# wonder about the missing target of a [text][label] link.
DEFINITION_NOTE = "(link definition removed)"
FOOTNOTE_NOTE = "(footnote removed)"

ROW_END = re.compile(r"(\r\n|\r|\n)")
# The marks of the quotes and list items that hold a row, before its text.
CONTAINER = re.compile(r"(?:[ \t]*(?:>|(?:[-+*]|\d{1,9}[.)])(?=[ \t]|$)))*[ \t]*")
LIST_MARK = re.compile(r"(?:[-+*]|\d{1,9}[.)])(?:[ \t]|$)")
QUOTE_MARK = re.compile(r" {0,3}>[ \t]?")
FENCE = re.compile(r" {0,3}(`{3,}(?!.*`)|~{3,})")
# A row after which the next one starts a block of its own, where a definition can
# begin: a heading, a fence, a rule or the line under a heading. More rows count
# than in CommonMark, which only removes more.
BOUNDARY = re.compile(r"#{1,6}(?:[ \t]|$)|`{3}|~{3}|([-*_=])(?:[ \t]*\1)*[ \t]*$")
# A row that ends the paragraph of a definition, so that its label or its title
# can't go on into it: a blank row, and the blocks that can interrupt a paragraph.
# Fewer rows count than in CommonMark, which only removes more.
STOP = re.compile(
    r"[ \t]*$| {0,3}(?:>|#{1,6}(?:[ \t]|$)|`{3,}[^`]*$|~{3}"
    r"|([-*_])(?:[ \t]*\1){2,}[ \t]*$|(?:=+|-+)[ \t]*$|(?:[-+*]|0*1[.)])[ \t]+\S)"
)
INDENTED = re.compile(r" {0,3}\t| {4}")
# The pieces of a definition within a row, each up to the character that ends it.
# A backslash takes the character after it, if there is one, in two ways that the
# quantifiers, which give nothing back, never try again one after the other: each
# backslash would double the tries. GitHub shows a control character as U+FFFD,
# also in a destination.
LABEL = re.compile(r"(?:[^\\\[\]]|\\.?)*+")
DESTINATION = re.compile(r"<(?:[^<>\\]|\\.?)*+>|[^ \t]+")
TITLES = {
    '"': (re.compile(r'(?:[^"\\]|\\.?)*+'), '"'),
    "'": (re.compile(r"(?:[^'\\]|\\.?)*+"), "'"),
    "(": (re.compile(r"(?:[^()\\]|\\.?)*+"), ")"),
}
FOOTNOTE_REFERENCE = re.compile(r"\[\^([^\]\s]+)\](?!:)")
# The start of each row on which a definition or a footnote could begin: its label,
# over any rows, reaches a `]:`. The quantifiers give nothing back, so each attempt
# ends at the first bracket.
CANDIDATE = re.compile(
    r"^(?:[ \t]*(?:>|(?:[-+*]|\d{1,9}[.)])(?=[ \t]|$)))*+[ \t]*+"
    r"\[(?:[^\\\[\]]|\\[\s\S])*+\]:",
    re.MULTILINE,
)
# HTML after which other rows start a block than strip_html leaves to see: a row
# that starts an HTML block, in which a fence is no fence, and text after the end
# of a comment, whose row ends the block that the comment started.
HTML_BLOCK = re.compile(r"^[ \t]*<(?!!--)[A-Za-z/?!]|-->[ \t]*\S", re.MULTILINE)


def shown_text(text: str) -> tuple[str, int]:
    """The text as GitHub shows it, without HTML, link reference definitions and
    footnotes that nothing refers to, each of those with a note in its place; and
    the number of definitions and footnotes that went.

    The definitions go after the HTML, so that taking one out can't end a comment
    that hides text. Where the HTML could have started a block, every row counts as
    the start of one, and no fence as code.
    """
    return strip_definitions(strip_html(text), bool(HTML_BLOCK.search(text)))


def strip_definitions(text: str, anywhere: bool = False) -> tuple[str, int]:
    """The text without the link reference definitions and the footnotes without a
    reference that GitHub shows as nothing, outside code, with a note in the place
    of each; and their number.

    A definition starts a block: at the beginning of the text, after a blank row or
    a heading, or with a quote or a list item. It can go on over the rows of its
    paragraph. A footnote can also interrupt a paragraph, and takes the rows of it
    and the indented ones after it. Fenced code outside quotes and list items is
    code up to its end, or to the end of the text, as GitHub shows it. `anywhere`
    counts every row as the start of a block and nothing as code. Where CommonMark
    is hard to follow exactly, such as in an indented row, which may be code or
    belong to a list item, a row counts as one in which a definition can start: the
    safe side. One pattern finds the rows whose label reaches a `]:`, and only those
    are read further; each row is read a bounded number of times, so the time is
    linear in the length of the text.
    """
    flat = text.replace("\r\n", "\n").replace("\r", "\n")
    candidates = list(rows_of(CANDIDATE, flat))
    if not candidates:
        return text, 0
    parts = ROW_END.split(text)
    rows, ends = parts[::2], [*parts[1::2], ""]
    code = [False] * len(rows) if anywhere else fenced(rows)
    referenced = {
        match[1].casefold()
        for index, match in zip(
            rows_of(FOOTNOTE_REFERENCE, flat),
            FOOTNOTE_REFERENCE.finditer(flat),
            strict=True,
        )
        if not code[index] and "`" not in rows[index]
    }
    removals: list[tuple[int, int, str]] = []
    last = -1
    for index in candidates:
        if index <= last or code[index]:
            continue
        row = rows[index]
        prefix = run(CONTAINER, row, 0)
        depth = row[:prefix].count(">")
        starts = (
            anywhere
            or index == last + 1
            or opens(rows[index - 1], code[index - 1], depth)
            or bool(LIST_MARK.search(row, 0, prefix))
        )
        found = definition(rows, code, index, prefix, depth, starts)
        if found and found[2] not in referenced:
            removals.append((index, found[0], row[:prefix] + found[1]))
            last = found[0]
    offsets = [0, *accumulate(map(len, parts))]
    pieces, cursor = [], 0
    for first, final, note in removals:
        pieces += [text[cursor : offsets[2 * first]], note, ends[final]]
        cursor = offsets[2 * final + 1] + len(ends[final])
    pieces.append(text[cursor:])
    return "".join(pieces), len(removals)


def rows_of(pattern: re.Pattern[str], text: str) -> Iterator[int]:
    """The row of each match of a pattern in a text whose rows end in line feeds."""
    row = position = 0
    for match in pattern.finditer(text):
        row += text.count("\n", position, match.start())
        position = match.start()
        yield row


def opens(row: str, code: bool, depth: int) -> bool:
    """Whether the row before one `depth` quotes deep lets that one start a block:
    code, a blank row, an indented one, which may be code, one in BOUNDARY, or one
    in fewer quotes, after which a quote starts."""
    prefix = run(CONTAINER, row, 0)
    marks = row[:prefix]
    indent = marks[len(marks.rstrip(" \t")) :].expandtabs(4)
    return (
        code
        or depth > marks.count(">")
        or not row[prefix:].strip(" \t")
        or len(indent) >= 4
        or bool(BOUNDARY.match(row, prefix))
    )


def run(pattern: re.Pattern[str], row: str, position: int) -> int:
    """Where a pattern ends from `position`, at which it always matches."""
    match = pattern.match(row, position)
    assert match is not None  # It matches the empty text, or any character there.
    return match.end()


def fenced(rows: list[str]) -> list[bool]:
    """Which rows are fenced code outside quotes and list items: from its fence to
    the one that closes it, or to the end of the text without one."""
    code, fence = [], ""
    for row in rows:
        if fence:
            code.append(True)
            body = row.lstrip(" ")
            marks = len(body) - len(body.lstrip(fence[0]))
            if len(row) - len(body) <= 3 and marks >= len(fence):
                fence = fence if body[marks:].strip(" \t") else ""
            continue
        opening = FENCE.match(row)
        fence = opening[1] if opening else ""
        code.append(bool(fence))
    return code


def definition(
    rows: list[str], code: list[bool], start: int, column: int, depth: int, starts: bool
) -> tuple[int, str, str] | None:
    """The last row, the note and, for a footnote, the label of the definition or
    the footnote whose `[` stands at `column` of row `start`, or None.

    A link reference definition only starts a block, which `starts` says; a footnote
    can also interrupt a paragraph. The rows after the first are read without the
    marks of the `depth` quotes around it.
    """

    def follow(index: int) -> str | None:
        """Row `index` without its quote marks, if the paragraph goes on into it."""
        if index >= len(rows) or code[index]:
            return None
        row, position = rows[index], 0
        for _ in range(depth):
            mark = QUOTE_MARK.match(row, position)
            if not mark:
                break
            position = mark.end()
        return None if STOP.match(row, position) else row[position:]

    if not starts and not rows[start].startswith("[^", column):
        return None
    index, row, position, label = start, rows[start], column + 1, []
    while True:
        end = run(LABEL, row, position)
        label.append(row[position:end])
        if end < len(row):
            break
        index += 1
        following = follow(index)
        if following is None:
            return None
        row, position = following, 0
    # CANDIDATE found the same first bracket that no backslash escapes.
    assert row.startswith("]:", end)
    if label[0].startswith("^"):
        # Only such a row gets here without `starts`.
        name = " ".join(" ".join(label)[1:].split()).casefold()
        return footnote(rows, code, index, follow), FOOTNOTE_NOTE, name
    position = spaces(row, end + 2)
    if position == len(row):
        index += 1
        following = follow(index)
        if following is None:
            return None
        row, position = following, spaces(following, 0)
    target = run(DESTINATION, row, position)
    position = spaces(row, target)
    if position == len(row):
        # Complete without a title; one may follow on the next row.
        titled = title(follow, index + 1, follow(index + 1), 0)
        return (index if titled is None else titled), DEFINITION_NOTE, ""
    if position == target:
        return None
    last = title(follow, index, row, position)
    return None if last is None else (last, DEFINITION_NOTE, "")


def spaces(row: str, position: int) -> int:
    """Where the spaces and tabs from `position` of a row end."""
    return len(row) - len(row[position:].lstrip(" \t"))


def title(
    follow: Callable[[int], str | None], index: int, row: str | None, position: int
) -> int | None:
    """The last row of the title of a definition that starts at `position` of row
    `index`, if a title starts there and nothing but spaces follow it on its row."""
    if row is None:
        return None
    position = spaces(row, position)
    if position == len(row) or row[position] not in TITLES:
        return None
    text, closing = TITLES[row[position]]
    position += 1
    while True:
        position = run(text, row, position)
        if position < len(row):
            break
        index += 1
        following = follow(index)
        if following is None:
            return None
        row, position = following, 0
    if row[position] != closing or row[position + 1 :].strip(" \t"):
        return None
    return index


def footnote(
    rows: list[str], code: list[bool], index: int, follow: Callable[[int], str | None]
) -> int:
    """The last row of a footnote whose label ends on row `index`: the rows of its
    paragraph, and after blank rows each indented one with the rows of its own."""
    last = index
    while True:
        while follow(last + 1) is not None:
            last += 1
        after = last + 1
        while after < len(rows) and not code[after] and not rows[after].strip(" \t>"):
            after += 1
        if (
            after == last + 1
            or after == len(rows)
            or code[after]
            or not INDENTED.match(rows[after])
        ):
            return last
        last = after


def joined(pieces: Iterable[str]) -> str:
    """The pieces in a row, with a space wherever two backtick runs would touch.

    Touching runs form one longer run, and Markdown would pair the code differently.
    """
    text = ""
    for piece in pieces:
        if text.endswith("`") and piece.startswith("`"):
            text += " "
        text += piece
    return text


def sanitize(text: str, config: Config, numbers: Iterable[Checked] = ()) -> str:
    """The engine's text without mentions, images, HTML, headings and foreign links.

    Code stays as it is; issue numbers, and references to issues of related
    repositories, stay links only when they were checked.
    """
    checked = set(numbers)
    # Before the code is found, so that what is code stays code: strip() takes some
    # control characters for spaces, which Markdown doesn't, and would turn a line
    # that starts with one into a fence after the code was found. Invisible
    # characters go too, so that no comment holds a text that nobody sees.
    text = STRAY_CONTROL.sub("", INVISIBLE.sub("", text)).strip()

    def prose(start: int, end: int) -> str:
        """The text between two pieces of code, cleaned for its place among them."""
        return clean_prose(
            text[start:end],
            checked,
            config,
            before=text[start - 1] if start else " ",
            after=text[end] if end < len(text) else " ",
            lines="\n" in text,
        )

    parts, last = [], 0
    for match in CODE.finditer(text):
        parts += [prose(last, match.start()), match[0]]
        last = match.end()
    parts.append(prose(last, len(text)))
    # Only the spaces and line breaks that the cleaning left at the ends, which
    # Markdown reads as such, too.
    return joined(parts).strip(" \t\n")


CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def one_line(text: str) -> str:
    """The text on one line: each run of blanks, line breaks and control characters,
    such as the escapes that color a terminal, becomes one space.

    Also for a place behind other text in a comment, such as `#7: `, where a fence
    would start no code block and its code would become text.
    """
    return " ".join(CONTROL.sub(" ", text).split())


def plain(text: str, config: Config, limit: int = 280) -> str:
    """The engine's text for one line outside its comments, at most `limit` long.

    Such as a line of the log and of the job summary, or a rationale that GitHub
    shows on the timeline of an issue. It is put on one line first, so that it can't
    start a workflow command or hold a block of Markdown, such as a code block whose
    mention would become text when its lines were joined later. Then it is cleaned as
    in a comment, so that it mentions nobody and links nowhere. A text that is too
    long is shortened before the cleaning too, which would otherwise lose the
    backtick that keeps a mention in its code.
    """
    line = sanitize(one_line(text), config)
    if len(line) <= limit:
        return line

    def shortened(end: int) -> str:
        return sanitize(one_line(text[:end]) + "…", config)

    # A longer start of the text can clean into a shorter line, when it closes a
    # tag for example, so the search finds a start that fits, not always the longest.
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if len(shortened(middle)) <= limit:
            low = middle
        else:
            high = middle - 1
    return shortened(low)


def tracked_files(root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
    )
    return {name for name in result.stdout.decode("utf-8").split("\0") if name}


def slug(heading: str) -> str:
    """The anchor GitHub gives a heading."""
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[^\w\- ]", "", text.lower())
    return text.replace(" ", "-")


def headings(path: Path) -> dict[str, str]:
    """The anchors of a Markdown file with the text of their heading."""
    text = re.sub(
        r"^(```|~~~).*?^\1",
        "",
        path.read_text(encoding="utf-8"),
        flags=re.M | re.S,
    )
    found: dict[str, str] = {}
    seen: dict[str, int] = {}
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.MULTILINE):
        base = slug(heading)
        count = seen.get(base, 0)
        seen[base] = count + 1
        title = re.sub(r"[*_`]", "", re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading))
        found[base if count == 0 else f"{base}-{count}"] = title
    return found


# The files that GitHub shows rendered instead of as their source: Markdown and the
# other markup languages that it renders, notebooks and SVG pictures.
RENDERED = frozenset(
    {
        ".md",
        ".markdown",
        ".mdown",
        ".mkdn",
        ".mkd",
        ".rst",
        ".adoc",
        ".asciidoc",
        ".asc",
        ".textile",
        ".rdoc",
        ".org",
        ".creole",
        ".mediawiki",
        ".wiki",
        ".pod",
        ".ipynb",
        ".svg",
    }
)


def code_span(text: str) -> str:
    """The text as inline code, also when it holds backticks.

    A backslash escapes nothing in code, so the code is set off by one backtick more
    than its longest run of them, and by a blank inside where it starts or ends with
    one, which Markdown then leaves out.
    """
    longest = max((len(run) for run in re.findall("`+", text)), default=0)
    marks = "`" * (longest + 1)
    blank = " " if text[:1] == "`" or text[-1:] == "`" else ""
    return f"{marks}{blank}{text}{blank}{marks}"


def reference_link(
    reference: dict[str, Any],
    tracked: set[str],
    sha: str,
    language: str,
    config: Config,
) -> str | None:
    """A checked link to a file of the repository, or None for a path that doesn't fit.

    Code lines link to the analyzed commit, so they stay right when the file changes;
    files and headings link to the default branch. git also tracks a submodule, as a
    folder, and a link that may lead nowhere; neither is a file to link to.
    """
    path = reference["path"].strip().removeprefix("./")
    file = config.root / path
    if path not in tracked or ".." in path.split("/") or not file.is_file():
        return None
    # A blank or a bracket in the path would end the address of the link.
    address = quote(path)
    name = code_span(path)
    line = reference.get("line")
    anchor = (reference.get("anchor") or "").strip().lstrip("#").lower()
    if line:
        if line > len(file.read_text(encoding="utf-8", errors="replace").splitlines()):
            return None
        # GitHub shows a rendered file, such as Markdown, without its lines; the
        # anchor of a line works only in its source.
        source = "?plain=1" if PurePosixPath(path).suffix.lower() in RENDERED else ""
        words = say(language, "line", file=name, line=line)
        return f"[{words}]({config.blob}/{sha}/{address}{source}#L{line})"
    if anchor and path.endswith(".md"):
        titles = headings(file)
        if anchor in titles:
            heading = f"[{titles[anchor]}]({config.blob}/HEAD/{address}#{anchor})"
            return say(language, "heading", heading=heading, file=name)
    return f"[{name}]({config.blob}/HEAD/{address})"


# The heading of the notice to reporters that the footers of an analysis and of a
# follow-up link: the part on what happens with an issue, which the SUPPORT.md of
# the blueprint has, as docs/getting-started.md advises.
NOTICE_ANCHOR = "what-happens-with-your-issue"


def notice_link(config: Config) -> str | None:
    """Where a comment tells reporters how the assistant works and who decides.

    The first file of `[transparency] files`, at its part on what happens with an
    issue when it has one; None when the file is missing, which `check` reports.
    """
    if not config.notices:
        return None
    name = config.notices[0].strip().removeprefix("./")
    parts = PurePosixPath(name).parts
    file = config.root / name
    if not parts or parts[0] == "/" or ".." in parts or not file.is_file():
        return None
    address = f"{config.blob}/HEAD/{quote(name)}"
    if name.lower().endswith(".md") and NOTICE_ANCHOR in headings(file):
        return f"{address}#{NOTICE_ANCHOR}"
    return address
