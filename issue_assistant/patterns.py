"""The patterns that read the Markdown of issues and comments, for several modules."""

from __future__ import annotations

import re

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
# An HTML comment. comment_parts splits a text where its last `-->` ends, so that
# this pattern only reads the part in which a `-->` follows every `<!--`.
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)

SHA = re.compile(r"[0-9a-f]{40}")
