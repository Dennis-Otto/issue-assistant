"""The cleaning of text that the engine wrote, before any of it reaches GitHub."""

from __future__ import annotations

import html
import re
import subprocess
from collections.abc import Callable, Iterable, Iterator
from functools import cache
from itertools import accumulate
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from .constants import MAX_NUMBER
from .languages import say
from .markup import CONTAINER, CONTAINER_MARK, ROW_END
from .patterns import (
    CODE,
    CROSS_REFERENCE,
    INVISIBLE,
    ISSUE_REFERENCE,
    Checked,
    fenced,
    guarded,
    without_references,
)
from .settings import Config
from .structures import (
    DRAWN,
    STOP,
    TITLE_NOTE,
    UNDERLINE,
    apart,
    info_cut,
    math_between,
    math_signs,
    quoted,
    structures_of,
)
from .tags import shown_html, shown_spans, strip_html

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
# Three tildes or more at the start of a line, also after the marks of quotes and
# list items, which would start or end a fenced block: text can't escape them with
# a backtick, as it does with the other fence, and GitHub would draw such a block of
# the language `mermaid` or read one of `math` as math.
TILDE_FENCE = re.compile(rf"^({CONTAINER.pattern})(?=~~~)", re.MULTILINE)
# A hyphen next to a pipe or a colon, perhaps with spaces between. Each line of
# hyphens with a pipe or a colon has one, such as `--|--` or `:-:`, the delimiter row
# below the header of a table, and GitHub splits every row of a table at each pipe,
# also inside code, so that the rest of the code would become text.
TABLE_HYPHEN = re.compile(r"(?<=[|:])([ \t]*)-|-(?=[ \t]*[|:])")

# The marks of a heading, without the line break after them, also in a quote or a
# list item, whose marks stay: at most 3 spaces before it, or 4 after such a mark,
# since more make it code.
HEADING_LINE = re.compile(
    rf"^(?:[ \t]{{0,3}}|(?P<marks>(?:[ \t]{{0,3}}{CONTAINER_MARK})+)"
    r"[ \t]{0,4})#{1,6}(?:[ \t]+|$)",
    re.MULTILINE,
)
# The marks of a heading that HEADING_LINE leaves, at any indentation: deeper in a
# list, a line indented by 4 or more spaces can still be a heading, as `- # b` is
# below `- a` and four spaces, and `# b` below `10. a` and a blank line. They are
# escaped, which in code only shows a backslash.
HEADING_MARKS = re.compile(
    rf"^((?:[ \t]*{CONTAINER_MARK})*[ \t]*)(?=#{{1,6}}(?:[ \t]|$))", re.MULTILINE
)
# A line of only `=` or only `-`, perhaps in a quote and at any indentation, that
# makes the line of text above it a heading. Below a blank line, it is text or a
# rule. Escaped wherever it might be one: in indented code that shows a backslash,
# and right below a kept code block it makes a rule text, which costs only looks.
SETEXT_LINE = re.compile(r"^((?:[ \t]*>)*[ \t]*)(?=(?:=+|-+)[ \t]*$)", re.MULTILINE)
QUOTE_MARKS = re.compile(r"(?:[ \t]*>)*[ \t]*")


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
        place = re.match(
            rf"^/{re.escape(config.repository)}/(?:(releases)|blob|tree)/(.*)",
            parts.path,
        )
        if not place:
            return False
        # A release is the repository's own at any tag.
        return bool(place[1]) or names_a_ref(place[2], config)
    return hostname in config.hosts


def names_a_ref(rest: str, config: Config) -> bool:
    """Whether the part of a path after /blob/ or /tree/ starts with a ref that the
    checkout vouches for: HEAD, which GitHub reads as the default branch, or one of
    `config.refs`.

    The address of the repository alone doesn't make a file the repository's: what
    a link shows depends on its ref, and only a ref of the repository's own shows
    the repository's files. GitHub reads the path decoded, and so does this check.
    """
    named = unquote(rest)
    # A ref ends at a slash or at the end of the path, and no ref is longer than the
    # longest one: so only a few prefixes are looked up, each in constant time, however
    # many refs a deep history gives and however long the path is.
    reach = max(len("HEAD"), longest(config.refs))
    ends = [end for end, char in enumerate(named[: reach + 1]) if char == "/"]
    return any(
        named[:end] == "HEAD" or named[:end] in config.refs
        for end in [*ends, len(named)]
    )


@cache
def longest(refs: frozenset[str]) -> int:
    """The length of the longest of `refs`, read once for each set of them."""
    return max(map(len, refs), default=0)


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
        text = HEADING_LINE.sub(lambda m: f"{m['marks']} " if m["marks"] else "", text)
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
    text = HEADING_MARKS.sub(r"\1\\", text)
    # No table without its delimiter row, which needs a line below the header: a
    # text of one line, such as a line of the log, keeps its hyphens.
    if lines:
        text = TABLE_HYPHEN.sub(lambda m: f"{m[1] or ''}\\-", text)
        text = SETEXT_LINE.sub(lambda m: m[1] + underline(m.string, m.start()), text)
    # No math: GitHub reads the escaped backslashes before it looks for math, and a
    # dollar sign in a code span is none.
    text = "".join(
        keep("`$`") if index % 2 else piece
        for index, piece in enumerate(math_signs(text, before, after))
    )
    pieces = re.split(r"\0(\d+)\0", text)
    return joined(
        kept[int(piece)] if index % 2 else piece for index, piece in enumerate(pieces)
    )


def underline(text: str, start: int) -> str:
    """The escape of a line of `=` or `-` at `start` that would make the line above
    it a heading: a backslash, unless that line is blank. On the first two lines,
    the line above may go on from code that this text follows, such as a code span,
    so they are escaped whatever is above."""
    begins = text.rfind("\n", 0, max(start - 1, 0)) + 1
    above = text[begins : start - 1]
    first = not start or not begins
    return "\\" if first or run(QUOTE_MARKS, above, 0) < len(above) else ""


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


# Besides HTML, GitHub shows nothing of a link reference definition, such as
# `[//]: # (a comment)` or `[x]: <> "a comment"`, the usual ways to write a comment
# in Markdown, and of a footnote that nothing refers to, such as `[^1]: a comment`.
# A note takes the place of each in the text for the engine, so that it doesn't
# wonder about the missing target of a [text][label] link.
DEFINITION_NOTE = "(link definition removed)"
FOOTNOTE_NOTE = "(footnote removed)"

LIST_MARK = re.compile(r"(?:[-+*]|\d{1,9}[.)])(?:[ \t]|$)")
# A row that may open a fence, after the marks of quotes and list items and at any
# indentation.
FENCE_ROW = re.compile(rf"(?:[ \t]*{CONTAINER_MARK})*+[ \t]*+(?:`{{3,}}(?!.*`)|~{{3}})")
# A row after which the next one starts a block of its own, where a definition can
# begin: a heading, a fence, a rule or the line under a heading. More rows count
# than in CommonMark, which only removes more.
BOUNDARY = re.compile(r"#{1,6}(?:[ \t]|$)|`{3}|~{3}|([-*_=])(?:[ \t]*\1)*[ \t]*$")
# The paragraph of a definition ends, so that its label or its title can't go on
# into it, at a row of STOP and at the line under a heading of UNDERLINE, as the
# paragraph of a title does (structures.py).
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
# A reference to a footnote that no backslash escapes.
FOOTNOTE_REFERENCE = re.compile(r"(?<!\\)(?:\\\\)*+\[\^([^\]\s]+)\](?!:)")
# The target and title of an inline link, in which a `[^1]` is no reference.
LINK_TARGET = re.compile(r"\]\([^()\n]*\)")
# The start of each row on which a definition or a footnote could begin: its label,
# over any rows, reaches a `]:`. The quantifiers give nothing back, so each attempt
# ends at the first bracket.
CANDIDATE = re.compile(
    r"^(?:[ \t]*(?:>|(?:[-+*]|\d{1,9}[.)])(?=[ \t]|$)))*+[ \t]*+"
    r"\[(?:[^\\\[\]]|\\[\s\S])*+\]:",
    re.MULTILINE,
)
# HTML after which other rows start a block than strip_html leaves to see: a row
# that starts an HTML block, in which a fence is no fence, also in a quote or a list
# item, which then takes no lazy row, and text after the end of a comment, whose row
# ends the block that the comment started. strip_html takes out a comment that
# starts a row outside them, or the rest of the text with it.
HTML_BLOCK = re.compile(
    rf"^(?:[ \t]*{CONTAINER_MARK})++[ \t]*+<[A-Za-z/?!]"
    r"|^[ \t]*<(?!!--)[A-Za-z/?!]|-->[ \t]*\S",
    re.MULTILINE,
)


def shown_text(text: str, notes: bool = True) -> tuple[str, int]:
    """The text as GitHub shows it, as shown_counts reads it, and the number of
    definitions and footnotes that went."""
    shown, definitions, _ = shown_counts(text, notes)
    return shown, definitions


def shown_counts(text: str, notes: bool = True) -> tuple[str, int, int]:
    """The text as shown_parts reads it, the number of definitions and footnotes
    that went, and that of the structures but the titles."""
    shown, definitions, structures, _ = shown_parts(text, notes)
    return shown, definitions, structures


def shown_parts(text: str, notes: bool = True) -> tuple[str, int, int, int]:
    """The text as GitHub shows it, without HTML, link reference definitions and
    footnotes that nothing refers to, and without the structures whose text GitHub
    doesn't show as it is written: the cells of a table beyond its header, the
    source of a drawing, math that can hide text and the title of a link or an
    image. A note takes the place of each but the HTML and the titles, or a space
    without `notes`. Also the number of definitions and footnotes that went, that of
    the other structures and that of the titles.

    The space is for what the assistant decides by itself, which must not read the
    words of a note, such as the language: it keeps the words on both sides apart,
    as the note does, and no note holds a line break, so the rows stay as they are.
    The definitions are found in the text with the notes of the structures, as the
    engine reads it, and the spaces go in after them: a row that held nothing but a
    structure stays a row with text, after which no definition starts.

    The HTML and the structures are both read from the text as it was written, and
    what either takes out goes: taking out the cells first could take a `<!--` that
    hides the rest out with them, and taking out the HTML first the pipe by which
    GitHub drops a cell. The definitions go after both, so that taking one out
    can't end a comment that hides text. Where the HTML could have started a block,
    every row counts as the start of one, and no fence as code. Math is read in what
    the HTML leaves as well, since a comment over rows can join two rows of it.
    """
    found = structures_of(text)
    hidden, joins = found.hidden(), found.dollars and "<" in text
    if hidden or joins:
        # The text of the spans keeps the length of the text, with a space in the
        # place of each date that the page draws.
        spaced_text, spans, cut = shown_spans(text)
        hidden = apart(hidden + math_between(spaced_text, spans)) if joins else hidden
        pieces, placed = cut_out(spaced_text, spans, hidden)
        shown = "".join(pieces)
    else:
        (shown, cut), pieces, placed = shown_html(text), [], []
    html = HTML_BLOCK.search(text)
    removals = definition_spans(
        shown, None if html is None else html_row(text, shown, html), cut
    )
    definitions = len(removals)
    noted = [index for index in placed if pieces[index] != TITLE_NOTE]
    titles = len(placed) - len(noted)
    if not notes:
        # A note of a structure in a definition goes with it: the two overlap. No
        # space takes the place of a title, of which no note took it either. The
        # marks of quotes and list items before a definition stay, as with its note.
        removals = apart(
            [(start, end, spaced(note)) for start, end, note in removals]
            + [(start, end, " ") for start, end in places(pieces, noted)]
        )
    return splice(shown, removals), definitions, len(noted), titles


def spaced(note: str) -> str:
    """The note of a definition or a footnote with a space in place of its words."""
    return note.removesuffix(DEFINITION_NOTE).removesuffix(FOOTNOTE_NOTE) + " "


def shown_references(
    text: str, notes: bool = True, decode: bool = False
) -> tuple[str, int, int, int, int]:
    """The text as shown_parts reads it, without the references of the characters
    that GitHub shows as nothing outside code, as without_references reads them,
    with `decode` also with the references of other characters read; the numbers
    of shown_parts, and that of the references that went.

    The references go last: an HTML comment that one of them keeps from ending on
    GitHub, which reads no reference in it, hides the rest, and a definition that
    one keeps from starting a row shows. The code that keeps its references is
    found in the text as it is written, where GitHub finds it (guarded), and from
    the row on which HTML could have started a block, where GitHub reads them in
    the text of the block, none counts.
    """
    guard = ""
    if "&" in text:
        html = HTML_BLOCK.search(text)
        row = None
        if html is not None:
            start = html.start()
            row = 1 + max(text.rfind("\n", 0, start), text.rfind("\r", 0, start))
        text, guard = guarded(text, row)
    shown, definitions, structures, titles = shown_parts(text, notes)
    shown, references = without_references(shown, decode, guard)
    if guard:
        shown = shown.replace(guard, "&")
    return shown, definitions, structures, titles, references


def splice(text: str, spans: list[tuple[int, int, str]]) -> str:
    """The text with each span, in order and apart, replaced by its note."""
    if not spans:
        return text
    pieces, cursor = [], 0
    for start, end, note in spans:
        pieces += [text[cursor:start], note]
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces)


def places(pieces: list[str], indices: list[int]) -> list[tuple[int, int]]:
    """Where each of the pieces at the indices stands in the pieces joined."""
    offsets = [0, *accumulate(map(len, pieces))]
    return [(offsets[index], offsets[index + 1]) for index in indices]


def cut_out(
    text: str, spans: list[tuple[int, int]], hidden: list[tuple[int, int, str]]
) -> tuple[list[str], list[int]]:
    """The spans of a text without the hidden ones, each with its note in its place
    where it holds a part of the spans, as pieces to join; and the index of each
    of those notes among them. Both lists are in order and apart."""
    pieces: list[str] = []
    notes: list[int] = []
    if len(spans) == 1:
        # The whole text, or the part before the end that GitHub shows: each hidden
        # span within it goes, in one pass.
        first, last = spans[0]
        position = first
        inside = [
            (max(start, first), min(end, last), note)
            for start, end, note in hidden
            if end > first and start < last
        ]
        for start, end, note in inside:
            pieces += [text[position:start], note]
            notes.append(len(pieces) - 1)
            position = end
        pieces.append(text[position:last])
        return pieces, notes
    index = position = 0

    def take(stop: int) -> bool:
        """Adds the parts of the spans from `position` to `stop`; whether any."""
        nonlocal index
        took = False
        while index < len(spans) and spans[index][0] < stop:
            first, last = spans[index]
            if min(last, stop) > max(first, position):
                pieces.append(text[max(first, position) : min(last, stop)])
                took = True
            if last > stop:
                break
            index += 1
        return took

    for start, end, note in hidden:
        take(start)
        position, before = start, len(pieces)
        if take(end):
            del pieces[before:]
            notes.append(len(pieces))
            pieces.append(note)
        position = end
    take(len(text))
    return pieces, notes


def html_row(text: str, shown: str, html: re.Match[str]) -> int:
    """The row of what strip_html left of a text, `shown`, from which HTML could
    have started a block: the row of the match, where strip_html left the rows
    before it as they were, and otherwise the first."""
    before = strip_html(text[: html.start()])
    return len(ROW_END.findall(before)) if shown.startswith(before) else 0


def strip_definitions(
    text: str, html: int | None = None, cut: bool = False
) -> tuple[str, int]:
    """The text without the link reference definitions and the footnotes without a
    reference that GitHub shows as nothing, outside code, with a note in the place
    of each; and their number. definition_spans finds them."""
    removals = definition_spans(text, html, cut)
    return splice(text, removals), len(removals)


def definition_spans(
    text: str, html: int | None = None, cut: bool = False
) -> list[tuple[int, int, str]]:
    """The link reference definitions and the footnotes without a reference that
    GitHub shows as nothing, outside code, each as the span of its rows in the text,
    without the line break after the last, with its note; in order and apart.

    A definition starts a block: at the beginning of the text, after a blank row or
    a heading, or with a quote or a list item. It can go on over the rows of its
    paragraph. A footnote can also interrupt a paragraph, and takes the rows of it
    and the indented ones after it. Fenced code outside quotes and list items is
    code up to its end, or to the end of the text, as GitHub shows it. `html` is the
    row from which HTML could have started a block, or None: with it, every row
    counts as the start of a block and nothing as code. Where CommonMark
    is hard to follow exactly, such as in an indented row, which may be code or
    belong to a list item, a row counts as one in which a definition can start: the
    safe side. One pattern finds the rows whose label reaches a `]:`, and only those
    are read further; each row is read a bounded number of times, so the time is
    linear in the length of the text.
    """
    flat = text.replace("\r\n", "\n").replace("\r", "\n")
    candidates = list(rows_of(CANDIDATE, flat))
    if not candidates:
        return []
    parts = ROW_END.split(text)
    rows = parts[::2]
    fence = fenced(rows)
    anywhere = html is not None
    code = [False] * len(rows) if anywhere else fence
    # A reference counts only where GitHub surely shows it: not in code, in an
    # indented row, which may be code, in a definition or a footnote, after a
    # backslash or in the target of a link. A footnote without one goes: the safe
    # side. Taking a footnote out can let a definition after it start a block, so
    # the references are counted again until the rows found stay the same. Where
    # the HTML could have started a block, it can hold any row from there on, so
    # none counts there. A fence that `fenced` doesn't follow, in a quote, in a list
    # item or indented further, may make code of every row after it, so no
    # reference counts from there on either.
    unsure = next(
        (
            index
            for index, row in enumerate(rows)
            if not fence[index] and FENCE_ROW.match(row)
        ),
        len(rows),
    )
    if html is not None:
        unsure = min(unsure, html)
    # The first row in a list item: above it, no list item holds a definition.
    listed = next(
        (
            index
            for index, row in enumerate(rows)
            if LIST_MARK.search(row, 0, run(CONTAINER, row, 0))
        ),
        len(rows),
    )
    masked = LINK_TARGET.sub(blank, flat)
    shown = [
        (index, match[1].casefold())
        for index, match in zip(
            rows_of(FOOTNOTE_REFERENCE, masked),
            FOOTNOTE_REFERENCE.finditer(masked),
            strict=True,
        )
        if not fence[index]
        and index < unsure
        and "`" not in rows[index]
        and not INDENTED.match(rows[index])
    ]
    hidden: set[int] = set()
    while True:
        referenced = {name for index, name in shown if index not in hidden}
        removals, found = scan(rows, code, candidates, referenced, anywhere, listed)
        if found <= hidden:
            break
        hidden |= found
    # The row on which GitHub hides the rest where `cut` says so: the last one. Where
    # the rest starts at the beginning of that row, no definition or footnote holds
    # it, even right after one; where it starts after blanks, the row may go on an
    # indented footnote, and the last row with text counts.
    last = len(rows) - 1
    if rows[last] and not rows[last].strip():
        last = next(
            (index for index in reversed(range(last)) if rows[index].strip()), 0
        )
    elif not rows[last]:
        last = -1
    if cut and last not in found:
        # GitHub hides the rest of the text from a position outside every
        # definition and footnote on, and with it the footnotes, which it shows
        # after the end of the text: each goes. Where the position is in one, as in
        # a comment that a footnote holds, the footnotes before it may show.
        removals = scan(rows, code, candidates, set(), anywhere, listed)[0]
    offsets = [0, *accumulate(map(len, parts))]
    return [
        (offsets[2 * first], offsets[2 * final + 1], note)
        for first, final, note in removals
    ]


def scan(
    rows: list[str],
    code: list[bool],
    candidates: list[int],
    referenced: set[str],
    anywhere: bool,
    listed: int,
) -> tuple[list[tuple[int, int, str]], set[int]]:
    """The first row, the last row and the note of each definition, and of each
    footnote whose label isn't `referenced`, that starts on a candidate row; and
    the rows of every definition and footnote found, also those that stay."""
    removals: list[tuple[int, int, str]] = []
    found: set[int] = set()
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
        hit = definition(rows, code, index, prefix, depth, starts, index >= listed)
        if not hit:
            continue
        found.update(range(index, hit[0] + 1))
        if hit[2] not in referenced:
            removals.append((index, hit[0], row[:prefix] + hit[1]))
            last = hit[0]
    return removals, found


def blank(match: re.Match[str]) -> str:
    """As many spaces as the match is long, so that every row keeps its place."""
    return " " * len(match[0])


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
    in fewer quotes, after which a quote starts.

    A row is indented when 4 columns of spaces stand before its text or before any
    of its marks: `    >` at the start of a row is code, not a quote.
    """
    prefix = run(CONTAINER, row, 0)
    marks = row[:prefix]
    return (
        code
        or depth > marks.count(">")
        or not row[prefix:].strip(" \t")
        or bool(INDENTED.search(marks))
        or bool(BOUNDARY.match(row, prefix))
    )


def run(pattern: re.Pattern[str], row: str, position: int) -> int:
    """Where a pattern ends from `position`, at which it always matches."""
    match = pattern.match(row, position)
    assert match is not None  # It matches the empty text, or any character there.
    return match.end()


def definition(
    rows: list[str],
    code: list[bool],
    start: int,
    column: int,
    depth: int,
    starts: bool,
    listed: bool,
) -> tuple[int, str, str] | None:
    """The last row, the note and, for a footnote, the label of the definition or
    the footnote whose `[` stands at `column` of row `start`, or None.

    A link reference definition only starts a block, which `starts` says; a footnote
    can also interrupt a paragraph. The rows after the first are read without the
    marks of the `depth` quotes around it.
    """
    # How far the `[` stands in from the quotes, with the marks of the list items
    # and a tab as wide as it can be: as far as the text of a list item that holds
    # it, or a little further. A line under a heading indented less may be lazy.
    # Where no list item is `listed` yet, none holds it, and no such line is lazy.
    inside = quoted(rows[start], depth)
    indent = len(rows[start][inside[0] : column].replace("\t", " " * 4)) * listed

    def follow(index: int) -> str | None:
        """Row `index` without its quote marks, if the paragraph goes on into it."""
        if index >= len(rows) or code[index]:
            return None
        row = rows[index]
        position, marks = quoted(row, depth)
        if STOP.match(row, position):
            return None
        line = UNDERLINE.match(row, position)
        if line and marks == depth and len(line[1]) >= indent:
            return None
        return row[position:]

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
    """The engine's text without mentions, images, HTML, headings, foreign links and
    math.

    Code stays as it is, but for the info string of fenced code, of which GitHub
    shows nothing and which keeps only the language that the engine may read, and
    fenced code that GitHub would draw or read as math, which shows as code of the
    language `text`; issue numbers, and references to issues of related
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
        parts += [prose(last, match.start()), plain_code(match)]
        last = match.end()
    parts.append(prose(last, len(text)))
    # Only the spaces and line breaks that the cleaning left at the ends, which
    # Markdown reads as such, too.
    return joined(parts).strip(" \t\n")


def plain_code(code: re.Match[str]) -> str:
    """Code of CODE as GitHub shows it: fenced code with only the part of its info
    string that the engine reads of an issue (info_cut), since the page shows none
    of it, so that no answer posts words that nobody sees; and fenced code that
    GitHub would draw, or whose content it would read as math, with `text` as its
    language, so that each of its rows, of which a drawing or math may show
    nothing, shows as it is. The drawing is judged by the word that is posted:
    `mermaid,x` is code on GitHub, but `mermaid` alone would be drawn."""
    if code["fence"] is None:
        return code[0]
    start = code.end("fence")
    end = code.string.index("\n", start)
    info = code.string[start:end]
    cut = info_cut(info)
    word = info[:cut].strip(" \t")
    if word in DRAWN or word == "math":
        word = "text"
    elif cut == len(info):
        return code[0]
    return code[0][: start - code.start()] + word + code[0][end - code.start() :]


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


# The most commits of the checkout's history that a link may name: a shallow
# checkout, as that of the job apply, has fewer, and a long history needn't be read
# whole for links that the engine is asked not to write at all.
MAX_COMMITS = 10_000
# The refs whose names GitHub serves for the repository: its branches and tags, and
# the branches of the remote that the checkout came from. Not refs/pull/ or other
# remotes, whose heads may be anyone's.
OWN_REFS = re.compile(r"refs/(?:heads|tags|remotes/origin)/(.+)")


def repository_refs(root: Path) -> frozenset[str]:
    """The refs of the repository in `root` that a link into it may name, for
    Config.refs: its branches and tags, and the full hashes of the commits of HEAD's
    history. A commit of a fork or of a pull request is no ancestor of HEAD, so a
    link to it stays code; so does every link when `root` is no checkout.
    """

    def read(*arguments: str) -> list[str]:
        result = subprocess.run(
            ["git", *arguments], cwd=root, capture_output=True, check=False
        )
        if result.returncode:
            return []
        return result.stdout.decode("utf-8", "replace").splitlines()

    names = read("for-each-ref", "--format=%(refname)")
    commits = read("rev-list", f"--max-count={MAX_COMMITS}", "HEAD")
    return frozenset(
        {match[1] for name in names if (match := OWN_REFS.fullmatch(name))}
        | set(commits)
    )


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
# The same heading in the notices of the other languages that install writes, such
# as the German one of templates/SUPPORT-notice.de.md.
NOTICE_ANCHORS = {"de": "was-mit-deinem-issue-geschieht"}


def notice_file(name: str, config: Config) -> str | None:
    """The path of a file of `[transparency] files` in the repository, or None when
    it is missing or outside it."""
    name = name.strip().removeprefix("./")
    parts = PurePosixPath(name).parts
    if not parts or parts[0] == "/" or ".." in parts:
        return None
    return name if (config.root / name).is_file() else None


def notice_link(config: Config, language: str = "en") -> str | None:
    """Where a comment tells reporters how the assistant works and who decides.

    A file of `[transparency] files` in the language of the comment, such as
    SUPPORT.de.md for one in German, else the first file; at its part on what happens
    with an issue when it has one. None when neither a file in that language nor the
    first file is there; `check` reports a missing first file.
    """
    translated = (
        notice_file(name, config)
        for name in config.notices
        if language != "en" and name.strip().lower().endswith(f".{language}.md")
    )
    first = notice_file(config.notices[0], config) if config.notices else None
    name = next(filter(None, translated), first)
    if name is None:
        return None
    address = f"{config.blob}/HEAD/{quote(name)}"
    if name.lower().endswith(".md"):
        titles = headings(config.root / name)
        for anchor in (NOTICE_ANCHORS.get(language), NOTICE_ANCHOR):
            if anchor in titles:
                return f"{address}#{anchor}"
    return address
