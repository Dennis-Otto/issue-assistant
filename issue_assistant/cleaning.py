"""The cleaning of text that the engine wrote, before any of it reaches GitHub."""

from __future__ import annotations

import html
import re
import subprocess
from collections.abc import Callable, Iterable
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
