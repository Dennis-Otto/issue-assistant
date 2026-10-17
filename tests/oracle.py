"""What GitHub would make of a comment, judged by rules of its own.

The property tests and the fuzzers judge the assistant's comments with this oracle,
so it shares no code with the assistant: only the type of its settings. A CommonMark
parser decides what is code, HTML, a link or text. On top of it, the oracle follows
what GitHub adds to CommonMark, as GitHub's own renderer showed it
(`gh api markdown -f mode=gfm`, 2026-10-13); tests/test_oracle.py holds the
examples:

- **Tables.** A line of hyphens with a colon or a pipe, such as `:-:` or `--|--`,
  makes the line above it the header of a table, also in a quote or a list item.
  Each row is split at every `|` that no backslash escapes, also inside backticks,
  and each cell is read alone, so that a pipe can cut code in two.
- **Autolinks.** An address that starts with http:// or https://, in any case and
  after anything but a letter, or with www. in lower case at the start, after a
  space or one of `* _ ~ (`, runs over the source to the next space or `<`: past
  brackets, entities, backslashes and into the code that follows. Then GitHub
  leaves out of its end the characters `? ! . , : * _ ~ ' "`, a `)` without its
  `(`, and a `;` with the entity that it ends. An e-mail address in the text is a
  link too.
- **Where a link leads** is where a browser goes: a user or a password before the
  host hides it, and `.` and `..` segments, also percent-encoded, climb out of a
  path. A backslash or a backtick in a link is a problem in itself.
- **Mentions and references:** `@name`, `@org/team`, `#1`, `GH-1`, `owner/repo#1`,
  and commits of other repositories or forks, `owner/repo@sha` and `user@sha`.

Where GitHub's rules depend on what exists, or on details that a cleaning should not
rely on, the oracle errs on the safe side: it counts a mention or a reference after
any character but a letter or a digit, and any address that GitHub might link, also
in the text of a link. A paragraph that might hold a table it reads both as a table
and as it is, since GitHub makes no table of it when the header has other cells than
the delimiter row or a quote holds that row, and it counts what either finds.
"""

import re
import string
import unicodedata
from collections import Counter
from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from markdown_it import MarkdownIt
from markdown_it.token import Token

from issue_assistant import Config


class Parser(MarkdownIt):
    """CommonMark that keeps what was written: the destination of each link as
    GitHub passes it on, with its escapes and entities read but nothing encoded,
    and the escapes and entities of the text as tokens of their own."""

    def normalizeLink(self, url: str) -> str:
        # markdown-it would percent-encode a backslash, which a browser reads as a
        # slash, and GitHub keeps it as it is.
        return url

    def validateLink(self, url: str) -> bool:
        # A link that markdown-it refuses, such as javascript:, is still a link
        # that a comment shouldn't hold.
        return True


PARSER = Parser("commonmark").disable("text_join")
# One way to read a paragraph: its parts, each read on its own.
Reading = list[list[Token]]

# The delimiter row of a table: cells of hyphens, each perhaps with a colon at its
# ends, between pipes.
MARKER = r"[ \t]*:?-+:?[ \t]*"
DELIMITER_ROW = re.compile(rf"[ \t]*\|?{MARKER}(?:\|{MARKER})*\|?[ \t]*")
# What ends an autolink in the source: an ASCII space or a <.
ENDS_ADDRESS = frozenset(" \t\n\r\x0b\x0c<")
# What GitHub leaves out at the end of an autolink, and the entity that a ; ends.
TRIMMED = frozenset("?!.,:*_~'\"")
ENTITY_END = re.compile(r"&[A-Za-z]+;\Z")
SCHEME = re.compile(r"https?://", re.IGNORECASE)
WWW = re.compile(r"www\.")
WWW_AFTER = frozenset(" \t\n\r\x0b\x0c*_~(")
# What GitHub links in the text besides addresses. The oracle counts each wherever
# it starts, or after anything but a letter or a digit.
EMAIL = re.compile(r"[A-Za-z0-9.+_-]+@[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+")
COMMIT = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?@[0-9a-f]{7}", re.IGNORECASE)
CROSS_REFERENCE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[0-9]+")
REFERENCE = re.compile(r"(?<![A-Za-z0-9])(?:#|GH-)([0-9]+)", re.IGNORECASE)
MENTION = re.compile(r"(?<![A-Za-z0-9])@[A-Za-z0-9][A-Za-z0-9-]*(?:/[A-Za-z0-9_-]+)?")


@dataclass(frozen=True)
class Piece:
    """A part of a paragraph: what was written, and what GitHub shows of it as text,
    or the destination of a link."""

    source: str
    shown: str = ""
    # text, label (the text of a link), code, link, html, image, or other, such as
    # the marks of emphasis.
    kind: str = "other"


def problems(
    markdown: str, numbers: Collection[int | str], config: Config
) -> list[str]:
    """What in the Markdown would mention, reference, link elsewhere or show HTML.

    `numbers` are the issues that it may reference: those of the repository by their
    number, and those of other repositories, as owner/name#123, that it may link to.
    `config` names the repository and the sites that it may link to.
    """
    found: list[str] = []
    for readings in inline_parts(markdown, found):
        found += either(
            [
                problem
                for children in reading
                for problem in judged(children, set(numbers), config)
            ]
            for reading in readings
        )
    if "\0" in markdown or "\r" in markdown:
        found.append("a NUL or carriage return")
    return found


def inline_parts(markdown: str, found: list[str]) -> Iterator[list[Reading]]:
    """The ways in which GitHub might read each heading and paragraph. The HTML
    blocks go to `found`."""
    env: dict[str, Any] = {}
    tokens = PARSER.parse(markdown, env)
    for index, token in enumerate(tokens):
        if token.type == "html_block":
            found.append(f"HTML {token.content.strip()!r}")
        elif token.type == "inline" and tokens[index - 1].type == "paragraph_open":
            yield readings(token, env)
        elif token.type == "inline":
            yield [[token.children or []]]


def readings(paragraph: Token, env: dict[str, Any]) -> list[Reading]:
    """A paragraph as it is, and, when it might hold a table, first as its lines
    before the table and each cell of the table."""
    whole = [paragraph.children or []]
    lines = paragraph.content.split("\n")
    for row in range(1, len(lines)):
        delimiter = lines[row]
        if DELIMITER_ROW.fullmatch(delimiter) and (
            "|" in delimiter or ":" in delimiter
        ):
            table = [inline("\n".join(lines[: row - 1]), env)] if row > 1 else []
            table += [
                inline(cell, env)
                for line in [lines[row - 1], *lines[row + 1 :]]
                for cell in cells(line)
            ]
            return [table, whole]
    return [whole]


def either(found: Iterable[list[str]]) -> list[str]:
    """What the readings of a paragraph find, in their order: each problem as often
    as the reading that finds it most often."""
    union: list[str] = []
    for reading in found:
        more = Counter(reading) - Counter(union)
        for problem in reading:
            if more[problem]:
                more[problem] -= 1
                union.append(problem)
    return union


def cells(row: str) -> list[str]:
    """The cells of a row of a table: split at each `|` that no backslash escapes,
    also inside code, each with `\\|` as a `|`."""
    found, cell, index = [], "", 0
    while index < len(row):
        pair = row[index : index + 2]
        if pair[0] == "\\" and pair[1:] in set(string.punctuation):
            cell += pair
            index += 2
            continue
        if pair[0] == "|":
            found.append(cell)
            cell = ""
        else:
            cell += pair[0]
        index += 1
    found.append(cell)
    return [cell.strip().replace("\\|", "|") for cell in found]


def inline(text: str, env: dict[str, Any]) -> list[Token]:
    return PARSER.parseInline(text, env)[0].children or []


def pieces(children: list[Token]) -> Iterator[Piece]:
    """The parts of a paragraph, each with what was written for it. GitHub links,
    references and mentions nothing in the text of a link, so that text is a label,
    not text."""
    href = ""
    inside = 0
    for token in children:
        if token.type == "text":
            yield Piece(token.content, token.content, "label" if inside else "text")
        elif token.type == "text_special":
            # An escape or an entity: GitHub shows one character for it.
            yield Piece(token.markup, token.content, "label" if inside else "text")
        elif token.type in {"softbreak", "hardbreak"}:
            # GitHub would link the backslash of a hard break as part of an address
            # before it; a backslash that the cleaning leaves is always escaped.
            yield Piece("\n", "\n", "text")
        elif token.type == "code_inline":
            yield Piece(token.markup + token.content + token.markup, kind="code")
        elif token.type == "html_inline":
            yield Piece(token.content, kind="html")
        elif token.type == "image":
            yield Piece(f"![{token.content}]({token.attrGet('src')})", kind="image")
        elif token.type == "link_open":
            href = str(token.attrGet("href"))
            inside += 1
            yield Piece("<" if token.markup == "autolink" else "[", href, "link")
        elif token.type == "link_close":
            inside -= 1
            yield Piece(">" if token.markup == "autolink" else f"]({href})")
        else:
            # The marks of emphasis.
            yield Piece(token.markup)


def judged(children: list[Token], numbers: set[int | str], config: Config) -> list[str]:
    """What a paragraph would mention, reference, link elsewhere or show."""
    found = []
    # The issues of other repositories that the comment may reference and link.
    checked = {str(item).casefold() for item in numbers if isinstance(item, str)}
    elsewhere = {
        f"https://github.com/{repository}/issues/{number}"
        for repository, _, number in (item.rpartition("#") for item in checked)
    }
    parts = list(pieces(children))
    starts, source = [], ""
    for part in parts:
        starts.append(len(source))
        source += part.source
        if part.kind == "link" and not (
            allowed(part.shown, config) or part.shown.casefold() in elsewhere
        ):
            found.append(f"link to {part.shown}")
        elif part.kind == "html":
            found.append(f"HTML {part.source!r}")
        elif part.kind == "image":
            found.append(f"image {part.source}")
    in_text = [False] * len(source)
    for part, start in zip(parts, starts, strict=True):
        if part.kind == "text":
            in_text[start : start + len(part.source)] = [True] * len(part.source)
    # Addresses first, as GitHub links them while it reads the paragraph; the rest
    # is linked in the text that they leave.
    taken = [False] * len(source)
    index = 0
    while index < len(source):
        end = autolink(source, index) if in_text[index] else None
        if end is None:
            index += 1
            continue
        address = source[index:end]
        url = address if SCHEME.match(address) else f"https://{address}"
        if not allowed(url, config):
            found.append(f"link to {address}")
        taken[index:end] = [True] * (end - index)
        index = end
    for node in text_nodes(parts, starts, taken):
        found += [f"e-mail link to {m[0]}" for m in EMAIL.finditer(node)]
        found += [f"reference {m[0]}" for m in COMMIT.finditer(node)]
        found += [
            f"reference {m[0]}"
            for m in CROSS_REFERENCE.finditer(node)
            if m[0].casefold() not in checked
        ]
        found += [
            f"reference {m[0]}"
            for m in REFERENCE.finditer(node)
            if int(m[1]) not in numbers
        ]
        found += [f"mention {m[0]}" for m in MENTION.finditer(node)]
    return found


def autolink(source: str, start: int) -> int | None:
    """The end of the address that GitHub links from `start` on, if it links one."""
    if scheme := SCHEME.match(source, start):
        before = source[start - 1] if start else " "
        # Letters before it make another scheme, such as xhttp://, and the host
        # must start with a character that can start a name.
        if (before.isascii() and before.isalpha()) or not starts_host(
            source[scheme.end() : scheme.end() + 1]
        ):
            return None
    elif not WWW.match(source, start) or (start and source[start - 1] not in WWW_AFTER):
        return None
    end = start
    while end < len(source) and source[end] not in ENDS_ADDRESS:
        end += 1
    while end > start:
        last = source[end - 1]
        if last in TRIMMED:
            end -= 1
        elif last == ";":
            entity = ENTITY_END.search(source, start, end)
            end = entity.start() if entity else end - 1
        elif last == ")" and source.count(")", start, end) > source.count(
            "(", start, end
        ):
            end -= 1
        else:
            break
    return end if end > start else None


def starts_host(character: str) -> bool:
    """Whether a host can start with the character: one that is no space and no
    punctuation."""
    return bool(character) and not (
        character.isspace()
        or character in string.punctuation
        or unicodedata.category(character).startswith("P")
    )


def text_nodes(parts: list[Piece], starts: list[int], taken: list[bool]) -> list[str]:
    """The text that GitHub shows, in runs between code, links, HTML and the marks of
    emphasis, with a space for each character of an address that it links."""
    nodes, node = [], ""
    for part, start in zip(parts, starts, strict=True):
        if part.kind != "text":
            nodes.append(node)
            node = ""
        elif part.source == part.shown:
            node += "".join(
                " " if taken[start + index] else character
                for index, character in enumerate(part.shown)
            )
        else:
            node += " " if taken[start] else part.shown
    return [*nodes, node]


def allowed(url: str, config: Config) -> bool:
    """Whether a browser that follows the link lands where a comment may lead: at a
    file, a folder or a release of the repository on GitHub, or on an allowed site.

    By the rules for URLs that browsers follow: any number of slashes may follow
    the scheme, a user or a password stands before the host and a port after it,
    and the path's `.` and `..` segments are resolved, percent-encoded ones too.
    """
    if "\\" in url or "`" in url:
        return False
    url = re.sub(r"[\t\n\r]", "", url).strip(" " + "".join(map(chr, range(32))))
    https = re.match(r"https:/*", url, re.IGNORECASE)
    if not https:
        return False
    rest = url[https.end() :]
    authority = re.split(r"[/?#]", rest, maxsplit=1)[0]
    if "@" in authority or ":" in authority:
        return False
    host = unquote(authority).lower()
    path = re.split(r"[?#]", rest[len(authority) :], maxsplit=1)[0]
    segments = resolved(unquote(path))
    if host == "github.com":
        return (
            [segment.lower() for segment in segments[:2]]
            == config.repository.lower().split("/")
            and len(segments) > 3
            and segments[2] in {"blob", "tree", "releases"}
        )
    return host in {name.lower() for name in config.hosts}


def resolved(path: str) -> list[str]:
    """The segments of a path after its `.` and `..` are resolved, as a browser
    resolves them."""
    segments: list[str] = []
    names = path.removeprefix("/").split("/")
    for index, name in enumerate(names):
        if name == "..":
            if segments:
                segments.pop()
        elif name != ".":
            segments.append(name)
            continue
        if index == len(names) - 1:
            segments.append("")
    return segments
