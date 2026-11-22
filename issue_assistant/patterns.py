"""The patterns that read the Markdown of issues and comments, for several modules."""

from __future__ import annotations

import re
from html.entities import html5

# Characters that GitHub shows as nothing, but that the engine reads, so that a text
# could hold instructions that no maintainer sees: the format characters of Unicode
# (category Cf), such as zero-width spaces and joiners, the controls of the direction
# of writing and the tags U+E0001 to U+E007F, which spell ASCII invisibly; and the
# code points that Unicode calls default ignorable, which a program shows as nothing
# when it doesn't know them, such as the variation selectors, which can carry a byte
# each behind any character, and the fillers of Hangul. A test checks the list
# against the Unicode of the Python that runs it. The pattern is built from the
# numbers of the code points, first and last of each range: CodeQL takes the
# characters above U+FFFF in a pattern for U+FFFD, and their ranges for overlapping.
INVISIBLE_RANGES = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x0600, 0x0605),
    (0x061C, 0x061C),
    (0x06DD, 0x06DD),
    (0x070F, 0x070F),
    (0x0890, 0x0891),
    (0x08E2, 0x08E2),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFFB),
    (0x110BD, 0x110BD),
    (0x110CD, 0x110CD),
    (0x13430, 0x1343F),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)
INVISIBLE = re.compile(
    "[" + "".join(f"{chr(first)}-{chr(last)}" for first, last in INVISIBLE_RANGES) + "]"
)
# A character reference, which GitHub reads as the character itself where it reads
# Markdown, so that `&#xE0041;` shows as nothing, as U+E0041 does. GitHub reads
# more of them in the text of an HTML block than in Markdown: a number with any
# number of digits, leading zeros counted, and without its `;`, and `&shy` without
# it, also before a letter. The pattern finds each of those: a number in hex or in
# decimal, with its leading zeros, at most 8 digits after them and its `;` if it
# has one, and a name with its `;`, or `shy` without. More digits make a number too
# large for a character, which stays as it is written, as does a name that HTML
# doesn't know. The quantifiers give nothing back, so each `&` is read once.
REFERENCE = re.compile(
    r"&(?:#[xX](0*+)([0-9A-Fa-f]{1,8})(;?)|#(0*+)([0-9]{1,8})(;?)"
    r"|([A-Za-z][A-Za-z0-9]{0,31});|shy)"
)
# The names of the references of the characters of INVISIBLE, each with its `;`, as
# HTML knows them: `&shy;`, `&ZeroWidthSpace;`, `&zwj;`, `&lrm;`, `&it;` and others.
INVISIBLE_NAMES = frozenset(
    name.removesuffix(";")
    for name, value in html5.items()
    if name.endswith(";") and len(value) == 1 and INVISIBLE.match(value)
)
# The one name that HTML reads without its `;`, also before a letter, whose
# character is one of INVISIBLE.
BARE_NAME = "shy"
# The most digits of a number that Markdown reads as a reference, leading zeros
# counted.
MARKDOWN_DIGITS = 8
# The matches of REFERENCE that can stand for a character of INVISIBLE, as
# hidden_rest reads them, found without a call for each other reference: a number of
# at least two digits in hex or three in decimal, as the smallest of INVISIBLE is
# U+00AD, 173 in decimal; a name of INVISIBLE_NAMES with its `;`; and `&shy`.
HIDING = re.compile(
    r"&(?:#[xX]0*+([0-9A-Fa-f]{2,8});?|#0*+([0-9]{3,8});?|(?:"
    + "|".join(sorted(INVISIBLE_NAMES, key=len, reverse=True))
    + ");|"
    + BARE_NAME
    + ")"
)
# Code that Markdown shows as it is, by the rules of CommonMark: a fenced block from
# a fence line to a closing fence line of the same character and at least the same
# length, and a code span within one line between backtick runs of the same length
# that no other backtick touches. A fence counts only at the very start of a line:
# indented, it can belong to a list item before it, which the next line without
# indentation ends, and the code with it. The line break before the fence belongs
# to the block, so that no cleaning of the text before can join the two. The fence
# is all of its characters: a shorter one doesn't close it, so GitHub's block would
# run on, out of the text into the next one of the comment. A span over several
# lines can be cut by a fence or a quote on the next line, so it, like anything else
# that doesn't fit these rules exactly, counts as text: the safe side.
CODE = re.compile(
    r"(?:\A|\n)(?P<fence>(?P<char>[`~])(?P=char){2,})(?!(?P=char))[^`\n]*\n.*?"
    r"^ {0,3}(?P=fence)(?P=char)*[ \t]*$"
    r"|(?<![`\\])(?P<run>`+)(?!`)[^\n]*?(?<!`)(?P=run)(?!`)",
    re.MULTILINE | re.DOTALL,
)

CROSS_REFERENCE = re.compile(r"(?<![A-Za-z0-9])([\w.-]+/[\w.-]+#\d+)")
# An issue or a discussion that the checks found, which a comment may link: one of
# this repository by its number, or an issue of a related repository of [related]
# as owner/name#123, with the name that [related] gives it.
Checked = int | str
ISSUE_REFERENCE = re.compile(r"(?<![A-Za-z0-9])(?:#|GH-)(\d+)", re.IGNORECASE)

# The patterns that read the text of an issue look at each character a bounded
# number of times, so that no text, however crafted, makes them slow. The tests and
# fuzz/fuzz_issue_text.py time them on texts that took earlier patterns hours.
#
# An HTML comment. html_parts finds where the last `-->` of a text ends, so that
# this pattern only reads the part in which a `-->` follows every `<!--`.
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

SHA = re.compile(r"[0-9a-f]{40}")

# The fence that opens a fenced block of code: three backticks or more, with no other
# backtick on its line, or three tildes or more, indented by at most three blanks.
FENCE = re.compile(r" {0,3}(`{3,}(?!.*`)|~{3,})")


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


# The characters of which a row makes a block of Markdown at its start: a heading,
# a rule or the line under a heading, a list item, a quote, fenced code, a row of a
# table, HTML or indented code.
BLOCK_START = frozenset("#-+*>=_`~|<0123456789 \t")
# The mark of a quote or a list item at the start of a row, after blanks.
ROW_MARK = re.compile(r"[ \t]*+(?:>|(?:[-+*]|\d{1,9}[.)])(?=[ \t\r\n]|\Z))")
ROW_BREAK = re.compile(r"\r\n?|\n")
BACKTICK_RUN = re.compile(r"`+")
# A row that may open fenced code after the marks of quotes and list items, or
# indented, where GitHub may read what follows the fence as its language.
FENCE_AFTER_MARKS = re.compile(
    r"(?:[ \t]*+(?:>|(?:[-+*]|\d{1,9}[.)])(?=[ \t]|$)))*+[ \t]*+(?:```|~~~)"
)


def hidden_rest(match: re.Match[str]) -> str | None:
    """What stays of a match of REFERENCE where GitHub may read it as a character of
    INVISIBLE, in Markdown or in the text of an HTML block: nothing, or what follows
    `&shy` where HTML reads that much of a name that it doesn't know, as in
    `&shyx;`; and None for the reference of any other character, a number too large
    for one, zero or a surrogate, which stay as they are written."""
    name = match[7]
    if name is not None:
        if name in INVISIBLE_NAMES:
            return ""
        return match[0][len(BARE_NAME) + 1 :] if name.startswith(BARE_NAME) else None
    if match[2] is not None:
        point = int(match[2], 16)
    elif match[5] is not None:
        point = int(match[5])
    else:
        return ""
    return "" if point <= 0x10FFFF and INVISIBLE.match(chr(point)) else None


def markdown_characters(match: re.Match[str]) -> str | None:
    """The characters of a match of REFERENCE as GitHub reads them in Markdown: a
    name with its `;`, or a number with its `;` and at most MARKDOWN_DIGITS digits,
    leading zeros counted; a line break as a space, so that no row forms that the
    page doesn't show. None where the page shows the reference as it is written,
    or a mark in place of a control character, zero, a surrogate or a number too
    large for a character. Only a match that hidden_rest leaves comes here."""
    if match[7] is not None:
        value = html5.get(match[7] + ";")
    else:
        hexadecimal = match[2] is not None
        zeros, digits, end = (
            match.group(1, 2, 3) if hexadecimal else match.group(4, 5, 6)
        )
        point = int(digits, 16 if hexadecimal else 10)
        value = (
            chr(point)
            if end
            and len(zeros) + len(digits) <= MARKDOWN_DIGITS
            and point <= 0x10FFFF
            and not 0xD800 <= point <= 0xDFFF
            and (point >= 0xA0 or 0x20 <= point < 0x7F or point in (9, 10, 13))
            else None
        )
    return None if value is None else value.replace("\r", " ").replace("\n", " ")


def code_parts(text: str, until: int | None = None) -> list[tuple[int, int]]:
    """The parts of a text that GitHub surely shows as code, in order and apart, up
    to `until`, the start of the row from which HTML could have started a block, in
    which GitHub reads no code.

    Fenced code counts after a fence at the very start of a row, as CODE reads it,
    to the fence that closes it, as `fenced` reads them: an indented fence before
    it can make that fence a closing one. The word after the fence, which names the
    language, is no code: GitHub reads its references, and the page shows nothing of
    it.

    A code span counts on one row, where each backtick before it in its paragraph
    closed its span on its own row, no `<` before it can start a tag or an autolink
    with a backtick in it, no backslash escapes its first backtick and no pipe can
    cut it into two cells of a table. From a row that may open fenced code in a
    quote or a list item, or that only looks like a fence, no span counts up to the
    next blank row: the rest of the row may be the language of the code, or a
    backtick in it may open a span over rows.

    Anything else, such as fenced code in a quote, in a list item or one that
    doesn't close, counts as text: the safe side. Each row is read once.
    """
    end = len(text) if until is None else until
    parts: list[tuple[int, int]] = []
    fence, opened = "", None
    # The run of backticks that opens a span, whether each span of the paragraph so
    # far closed on its row, and where the text after the last span starts.
    opener: re.Match[str] | None = None
    sure, since = True, -1
    start = 0
    while True:
        found = ROW_BREAK.search(text, start, end)
        stop = end if found is None else found.start()
        row = text[start:stop]
        if fence:
            body = row.lstrip(" ")
            marks = len(body) - len(body.lstrip(fence[0]))
            if (
                len(row) - len(body) <= 3
                and marks >= len(fence)
                and not body[marks:].strip(" \t")
            ):
                if opened is not None:
                    parts.append((opened, stop))
                fence = ""
            opener, sure, since = None, True, -1
        elif opening := FENCE.match(row):
            fence, opened = opening[1], stop if opening.start(1) == 0 else None
            opener, sure, since = None, True, -1
        elif not row.strip(" \t"):
            opener, sure, since = None, True, -1
        elif FENCE_AFTER_MARKS.match(row):
            opener, sure = None, False
        elif sure:
            since = start if since < 0 else since
            for run in BACKTICK_RUN.finditer(text, start, stop):
                if opener is None:
                    if (
                        text.find("<", since, run.start()) >= 0
                        or text[run.start() - 1 : run.start()] == "\\"
                    ):
                        sure = False
                        break
                    opener = run
                elif len(run[0]) == len(opener[0]):
                    if text.find("|", opener.end(), run.start()) >= 0:
                        sure = False
                        break
                    parts.append((opener.start(), run.end()))
                    opener, since = None, run.end()
            if opener is not None:
                opener, sure = None, False
        if found is None:
            return parts
        start = found.end()


# The characters of private use that stand for the `&` of a reference that code
# holds while the text is read: one that the text doesn't hold.
GUARDS = [chr(code) for code in range(0xE000, 0xE100)]


def guarded(text: str, until: int | None = None) -> tuple[str, str]:
    """The text with each `&` in the parts that code_parts finds, where GitHub
    shows a reference as it is written, replaced with a character of GUARDS that
    the text doesn't hold, which no reading of Markdown takes for anything; and
    that character, or "" where code holds no `&` or the text holds every one of
    them. The parts are found in the text as it is written, before its HTML and its
    structures go, which could join two runs of backticks: GitHub finds its code
    there. `until` is the start of the row from which HTML could have started a
    block, in which GitHub reads no code."""
    pieces, position = [], 0
    for start, end in code_parts(text, until):
        found = text.find("&", start, end)
        while found >= 0:
            pieces.append(text[position:found])
            position = found + 1
            found = text.find("&", position, end)
    if not pieces:
        return text, ""
    held = set(text)
    guard = next((guard for guard in GUARDS if guard not in held), "")
    if not guard:
        return text, ""
    return guard.join([*pieces, text[position:]]), guard


def without_references(
    text: str, decode: bool = False, guard: str = ""
) -> tuple[str, int]:
    """The text without the references of the characters of INVISIBLE, and how many
    went: `guarded` keeps those that code holds apart, where GitHub shows them as
    they are written.

    Every form that GitHub reads somewhere goes, so also one that it shows as text
    in Markdown, such as `&#x200B` without its `;`, `\\&#x200B;` or a number of nine
    digits: the safe side. A reference of any other character stays, also
    `&amp;#xE0041;`, whose `&` belongs to `&amp;`.

    With `decode`, each reference of another character becomes that character as
    markdown_characters reads it, so that what the assistant decides reads what the
    page shows, such as `doesn&#39;t`. One of BLOCK_START stays as it is written
    where it could make a block of its row, as in `&#35;## Title`, `###&#32;Title`
    and `-&#32;[x]`: where its row holds nothing before it but characters of
    BLOCK_START and starts with no mark of a quote or a list item. The page shows
    the character there, but makes no block of it. After such a mark, the page
    reads it as text, as GitHub ticks the box of `- &#32;[x]` (gh api markdown,
    2026-11-22), and no reading of the assistant takes it for a block. Each
    character before a reference is read once: those of BLOCK_START before one end
    at the `&` of the reference before it at the latest. A reference of the `guard`
    that stands for the `&` of code stays as it is written, such as `&#xE000;`, so
    that putting the `&` of code back can't take its character for one.
    """
    if "&" not in text:
        return text, 0
    count = 0
    if not decode:

        def remove(match: re.Match[str]) -> str:
            nonlocal count
            digits = match[1] or match[2]
            if digits is not None:
                point = int(digits, 16 if match[1] else 10)
                if point > 0x10FFFF or not INVISIBLE.match(chr(point)):
                    return match[0]
            count += 1
            return ""

        return HIDING.sub(remove, text), count
    # What each reference becomes, read once for each way of writing it: what
    # stays of it, or its characters, or None for itself.
    read: dict[str, tuple[str | None, str | None]] = {}

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        found = read.get(match[0])
        if found is None:
            rest = hidden_rest(match)
            found = read[match[0]] = (
                rest,
                None if rest is not None else markdown_characters(match),
            )
        rest, value = found
        if rest is not None:
            count += 1
            return rest
        if value is None or (guard and guard in value):
            return match[0]
        if value[0] in BLOCK_START:
            start = match.start()
            while start and text[start - 1] in BLOCK_START:
                start -= 1
            if text[start - 1 : start] in ("", "\n", "\r") and not ROW_MARK.match(
                text, start
            ):
                return match[0]
        return value

    return REFERENCE.sub(replace, text), count
