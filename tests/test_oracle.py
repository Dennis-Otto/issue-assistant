"""The oracle of tests/oracle.py, which judges the comments in the property tests and
the fuzzers, against what GitHub made of the same Markdown.

GitHub's renderer (`gh api markdown -f mode=gfm`) rendered each example on
2026-10-13. The oracle finds what GitHub linked, mentioned or referenced, and also
what it would where the user, the team or the commit exists.
"""

import pytest

from oracle import problems
from support import CONFIG


@pytest.mark.parametrize(
    ("markdown", "found"),
    [
        # An address with http:// or https://, in any case and after anything but
        # a letter, whose host starts with a letter, a digit or a symbol.
        ("@https://evil.example/x", ["link to https://evil.example/x"]),
        ("1https://evil.example/x", ["link to https://evil.example/x"]),
        ("HTTP://evil.example", ["link to HTTP://evil.example"]),
        ("https://localhost/x", ["link to https://localhost/x"]),
        ("https://©x y", ["link to https://©x"]),
        ("xhttps://evil.example ftp://evil.example https://_x https://\u00a0x", []),
        # It runs to the next ASCII space or <: into code, past entities and
        # backslashes, and past spaces of Unicode.
        (
            "x https://docs.example.com`@evil.example/` y",
            ["link to https://docs.example.com`@evil.example/`"],
        ),
        (
            "https://docs.example.com&lt;b@evil.example",
            ["link to https://docs.example.com&lt;b@evil.example"],
        ),
        (
            "https://docs.example.com\u00a0@evil.example/ z",
            ["link to https://docs.example.com\u00a0@evil.example/"],
        ),
        ("https://docs.example.com/x\\[y", ["link to https://docs.example.com/x\\[y"]),
        ("https://docs.example.com/x<b and https://docs.example.com/@*@o*", []),
        # Its end leaves out punctuation, a ) without its (, and an entity.
        ("(https://evil.example/w).", ["link to https://evil.example/w"]),
        (
            "(https://docs.example.com/x). https://docs.example.com/y' "
            "https://docs.example.com/(a)(b https://docs.example.com/z&amp; "
            "https://docs.example.com/z;",
            [],
        ),
        # www. in lower case, at the start, after a space or one of * _ ~ (.
        ("www.evil.example", ["link to www.evil.example"]),
        (
            "a_www.evil.example b*www.evil.example (www.evil.example)",
            ["link to www.evil.example"] * 3,
        ),
        ("www. x", ["link to www"]),
        ("`x`www.evil.example x\u00a0www.evil.example WWW.evil.example", []),
        # Where a link leads: a file, a folder or a release of the repository, or a
        # site of the settings.
        (
            "[a](https://github.com/owner/project/blob/main/x) "
            "[b](https://github.com/OWNER/project/tree/main/) "
            "[c](https://DOCS.example.com/x) https://github.com/owner/project/releases/",
            [],
        ),
        (
            "[a](https://github.com/owner/project/issues/1)",
            ["link to https://github.com/owner/project/issues/1"],
        ),
        (
            "https://github.com/owner/project/blob/x)/../../../../evil/repo",
            ["link to https://github.com/owner/project/blob/x)/../../../../evil/repo"],
        ),
        (
            "[a](https://github.com/owner/project/blob/%2e%2e/%2E%2E/evil/repo)",
            ["link to https://github.com/owner/project/blob/%2e%2e/%2E%2E/evil/repo"],
        ),
        # The target of a link balances its parentheses, and its entities count.
        (
            "[x](https://github.com/owner/project/blob/(a)/../../../../evil/repo)",
            ["link to https://github.com/owner/project/blob/(a)/../../../../evil/repo"],
        ),
        (
            "[x](https://github.com/owner/project/blob/&#46;&#46;/&#46;&#46;/&#46;&#46;/e/r)",
            ["link to https://github.com/owner/project/blob/../../../e/r"],
        ),
        (
            "[a](https://user@docs.example.com/) [b](https://docs.example.com:8443/)",
            [
                "link to https://user@docs.example.com/",
                "link to https://docs.example.com:8443/",
            ],
        ),
        (
            "[a](https://docs.example.com/\\\\x) [b](http://docs.example.com/) "
            "[c](/owner/project/blob/main/x) [d](javascript:alert(1))",
            [
                "link to https://docs.example.com/\\x",
                "link to http://docs.example.com/",
                "link to /owner/project/blob/main/x",
                "link to javascript:alert(1)",
            ],
        ),
        # E-mail addresses, also behind mailto: or xmpp:.
        (
            "a.b@c.example and mailto:a@b.example and xmpp:a@b.example/r",
            [
                "e-mail link to a.b@c.example",
                "e-mail link to a@b.example",
                "e-mail link to a@b.example",
            ],
        ),
        # Mentions after anything but a letter or a digit, also an entity or the
        # end of an emphasis; teams and commits where they exist.
        (
            "@octocat. ~~a~~@octocat *@octocat* &#64;octocat @org/team",
            ["mention @octocat"] * 4 + ["mention @org/team"],
        ),
        ("x@octocat 1@octocat x&#64;octocat", []),
        # Text after code starts anew, whatever the code ends with.
        ("`#0`@octocat", ["mention @octocat"]),
        (
            "#7 GH-7 gh-8 a#9 é#10 owner/repo#3 owner/repo@0123abc user@0123abc",
            [
                "reference owner/repo@0123abc",
                "reference user@0123abc",
                "reference owner/repo#3",
                "reference gh-8",
                "reference #10",
            ],
        ),
        # Code shows as it is; HTML and images are problems wherever they are.
        ("`@octocat` and\n```\n@octocat https://evil.example\n```", []),
        (
            "<b>x</b> <!-- y -->\n\n<div>\n@a\n</div>\n\n![a](https://docs.example.com/a.png)",
            [
                "HTML '<b>'",
                "HTML '</b>'",
                "HTML '<!-- y -->'",
                "HTML '<div>\\n@a\\n</div>'",
                "image ![a](https://docs.example.com/a.png)",
            ],
        ),
        ("x\rx\0", ["a NUL or carriage return"]),
        # GitHub links, references and mentions nothing in the text of a link,
        # also under emphasis; only where the link leads counts.
        (
            "[see *#1* by @octocat and x@example.com](https://evil.example)",
            ["link to https://evil.example"],
        ),
        # A table splits each row at every pipe that no backslash escapes, also in
        # code, in a quote, in a list item, and with one column.
        ("intro\nf | g\n--|--\n`x|#1 y` | w", ["reference #1"]),
        ("a\n-:\n`https://evil.example/s|x`", ["link to https://evil.example/s"]),
        ("> h\n> :-\n> `#1|q`", ["reference #1"]),
        ("- h\n  -:\n  `#1|q`", ["reference #1"]),
        ("f | g\n--|--\n`x\\|#1` | w", []),
        ("`a`\n---\n`x|#1`\n\na\n-\\|-\n`x|#1`", []),
        # GitHub makes no table where the header has other cells than the
        # delimiter row, or where the delimiter row continues a quote; the oracle
        # reads such a paragraph both ways.
        ("a | b\n:-:\n`x|` @octocat `|y`", ["mention @octocat"]),
        ("> h\n:-:\n`x|` @octocat `|y`", ["mention @octocat"]),
        (
            "a | b\n:-:\n`x|` https://evil.example/q `|y` #3",
            ["link to https://evil.example/q", "reference #3"],
        ),
    ],
)
def test_the_oracle_finds_what_github_links(markdown, found):
    assert sorted(problems(markdown, {7}, CONFIG)) == sorted(found)


def test_the_text_of_a_checked_link_to_another_repository_references_nothing():
    """GitHub rendered `_see [owner/app_#5](https://github.com/owner/app_/issues/5)_`
    as one link in emphasis: the `#5` in its text references no issue of this
    repository, although the `_` before it ends no word."""
    linked = "_see [owner/app_#5](https://github.com/owner/app_/issues/5)_"
    assert problems(linked, {"owner/app_#5"}, CONFIG) == []
    assert problems(linked, set(), CONFIG) == [
        "link to https://github.com/owner/app_/issues/5"
    ]


@pytest.mark.parametrize(
    "output",
    [
        "see https://docs.example.com`@evil.example/` end",
        "https://docs.example.com&lt;b@evil.example",
        "https://github.com/owner/project/blob/x)/../../../../evil/repo",
        "[a](https://github.com/owner/project/blob/%2e%2e/%2e%2e/evil/repo)",
        "[a](https://user@docs.example.com/)",
        "Write to support@evil.example",
        "mailto:x@evil.example",
        "octocat/Hello-World@7fd1a60b01f91b314f59955a4e4d4e80d8edf11d",
        "octocat@7fd1a60",
        "HTTPS://evil.example/x",
        "_https://evil.example/y",
        "x_www.evil.example y",
    ],
)
def test_the_oracle_finds_what_the_cleaning_once_let_through(output):
    """What the cleaning made of texts that got past it before #65, in which the
    oracle of that time found nothing."""
    assert problems(output, set(), CONFIG)
