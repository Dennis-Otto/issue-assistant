"""The oracle of tests/oracle.py, which judges the comments in the property tests and
the fuzzers, against what GitHub made of the same Markdown.

GitHub's renderer (`gh api markdown -f mode=gfm`) rendered each example on
2026-10-13. The oracle finds what GitHub linked, mentioned or referenced, and also
what it would where the user, the team or the commit exists.
"""

import json
from pathlib import Path

import pytest

from oracle import PARSER, browser_text, headings, hides_the_rest, problems
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


@pytest.mark.parametrize(
    ("markdown", "found"),
    [
        ("# A\n\nB\n===\n\nC\n---", ["A", "B", "C"]),
        # In a quote and in a list item, and underlined below their text.
        ("> # A\n\n- # B\n\n> C\n> ===\n\n- D\n  ---", ["A", "B", "C", "D"]),
        # A line of hyphens below a blank line is a rule, an escaped one text, and
        # four spaces make code.
        ("A\n\n---\n\nB\n\\===\n\n    # C\n\n\\# D", []),
        # What starts like a link reference definition, above a line under a
        # heading (2026-11-20).
        ("[0]:\n=\n\n[a\nb]:\n=\n\n[0]:\n--", ["[0]:", "[a\nb]:", "[0]:"]),
        ('[0]: /u "a\n=\nb"', ['[0]: /u "a']),
    ],
)
def test_the_oracle_finds_every_heading(markdown, found):
    assert headings(markdown) == found


@pytest.mark.parametrize(
    ("markdown", "labels"),
    [
        # A line under a heading ends the paragraph before a definition can read
        # it as its destination or its title; GitHub shows a heading.
        ("[0]:\n=", []),
        ('[x]:\n=\n"t"', []),
        ("[0]:\n--", []),
        ('[0]: /u "a\n=\nb"', []),
        ("[a\nb]:\n=", []),
        ("> [0]:\n> =\n> t", []),
        ("- [0]:\n  =\n  t", []),
        # A lone hyphen starts a list item where it doesn't underline.
        ('> [0]:\n-\n"t"', []),
        # A line of `=` below a definition is a paragraph of its own.
        ("[x]: /url\n===", ["X"]),
        # A lazy line underlines nothing: without the mark of its quote, or less
        # indented than its list item. GitHub reads on over it.
        ('> [0]:\n=\n"t"', ["0"]),
        ('> [0]:\n--\n"t"', ["0"]),
        ('>> [0]:\n> =\n"t"', ["0"]),
        ('- [0]:\n=\n"t"', ["0"]),
        ("- [0]:\n--", ["0"]),
        ('- a\n\n  [0]:\n=\n"t"', ["0"]),
        ('> [0]: /u\n"t\n=\nx"', ["0"]),
        # A backslash before a space, a tab or a line feed escapes nothing, and the
        # destination ends after it.
        ("[0]:\\\t0", []),
        ("[0]: a\\\nb", ["0"]),
        # An escaped backslash before a space stays one.
        ("[0]: a\\\\ b", []),
        ("[0]: a\\\\", ["0"]),
    ],
)
def test_the_oracle_reads_definitions_as_github_does(markdown, labels):
    """GitHub's renderer (`gh api markdown -f mode=gfm`, 2026-11-20) showed these
    texts with a definition, or with what starts like one as text."""
    environment: dict[str, dict[str, object]] = {}
    PARSER.parse(markdown, environment)
    assert list(environment.get("references", {})) == labels


def test_a_backslash_before_a_tab_makes_no_link():
    """GitHub shows `[a](\\<tab>0)` as text: the tab ends the destination."""
    assert PARSER.parseInline("[a](\\\t0)")[0].children[0].type == "text"
    assert problems("[a](\\\t0)", set(), CONFIG) == []


@pytest.mark.parametrize(
    ("markdown", "found"),
    [(">- <!A\n>", ["HTML '<!A'"]), ("> - <!--\n>", ["HTML '<!--'"])],
)
def test_the_oracle_reads_a_text_without_a_final_line_feed(markdown, found):
    """markdown-it-py 4.3.0 raises an IndexError for these texts as they are."""
    assert headings(markdown) == []
    assert problems(markdown, set(), CONFIG) == found


@pytest.mark.parametrize(
    ("markdown", "hidden"),
    [
        ("x\n\n> <!--\n> a\n\nhidden words", True),
        ("> - <!--\nhidden", True),
        ("- a\n  - b\n    <!--\n\nhidden", True),
        ("<div><!--\n\nhidden", True),
        ("<!-- a --> <!--\n\nhidden", True),
        ("x\r\n> <!--\r\n\r\nhidden", True),
        ("x <!--\n\nshown words", False),
        ("x\n\n> text <!--\n\nshown words", False),
        ("> <!-- a -->\n\nshown", False),
        ("    <!--\n\nshown", False),
    ],
)
def test_the_oracle_finds_the_comments_that_hide_the_rest(markdown, hidden):
    """GitHub's renderer (`gh api markdown -f mode=gfm`, 2026-11-20) showed nothing
    after the HTML block of these texts, or the rest of the others."""
    assert hides_the_rest(markdown) is hidden


SEARCH = json.loads(
    (Path(__file__).with_name("fixtures") / "tags-over-rows.json").read_text("utf-8")
)


def test_a_browser_reads_the_searched_tags_as_github_showed_them():
    """The text that browser_text finds of each text of the search shows the marker
    words that GitHub's renderer showed, and none of those that it hid."""
    for case in SEARCH["texts"]:
        seen = browser_text(case["text"])
        assert [word for word in case["github shows"] if word not in seen] == []
        assert [word for word in case["github hides"] if word in seen] == []


@pytest.mark.parametrize(
    ("markdown", "shown", "hidden"),
    [
        # A `<!--` in the name or the attributes of a tag is part of the tag.
        ("<div><div<!--\n\nzqd", ["zqd"], []),
        ("<div>\n<a b <!--\nzqa>\n\nzqd", ["zqd"], ["zqa"]),
        ("2) <div\n\n<div>\n\nzqd", ["zqd"], []),
        # An instruction, a declaration and a section of CDATA are comments up to
        # their first `>` for a browser, and up to the end of the text without one.
        ("x\n\n<?php\nzqa\n\nzqd", [], ["zqa", "zqd"]),
        ("x\n\n<!X zqa\n\nzqd", [], ["zqa", "zqd"]),
        ("x\n\n<![CDATA[ zqa\n\nzqd", [], ["zqa", "zqd"]),
        ("x <? zqa > zqb ?> zqc\n\nzqd", ["zqb", "zqc", "zqd"], ["zqa"]),
        ("x <!X zqa\nzqb> zqc\n\nzqd", ["zqc", "zqd"], ["zqa", "zqb"]),
        ("<div>\nx <? zqa\n\nzqd", ["zqd"], ["zqa"]),
        ("<div>\nx </ zqa\n\nzqd", ["zqd"], ["zqa"]),
        ("x\n\n<!DOCTYPE zqa>\nzqb\n\nzqd", ["zqb", "zqd"], ["zqa"]),
        ("x <![CDATA[ zqa ]]> zqb\n\nzqd", ["zqb", "zqd"], ["zqa"]),
        # HTML's tokenizer ends a comment at `--!>` and at `--->`, CommonMark at the
        # `-->` after them, and a tag between them holds that `-->` in a quoted value
        # (2026-11-23).
        (
            'zqa <!-- a --!> <a title="-->zqb">zqc</a> zqd',
            ["zqa"],
            ["zqb", "zqc", "zqd"],
        ),
        (
            "zqa <!-- a --!> <a title='-->zqb'>zqc</a> zqd",
            ["zqa", "zqd"],
            ["zqb", "zqc"],
        ),
        ('zqa <!-- a --!> <img alt="-->zqb" src="x"> zqd', ["zqa"], ["zqb", "zqd"]),
        (
            'zqa <!-- a ---> <a title="-->zqb">zqc</a> zqd',
            ["zqa"],
            ["zqb", "zqc", "zqd"],
        ),
        ("zqa <!-- zqb --!> zqc --> zqd", ["zqa", "zqc", "zqd"], ["zqb"]),
        # Without a later `-->`, CommonMark starts no comment at the `<!--`.
        ("zqa <!-- zqb ---> zqc", ["zqa", "zqb", "zqc"], []),
    ],
)
def test_a_browser_reads_markup_as_github_showed_it(markdown, shown, hidden):
    """GitHub's renderer (`gh api markdown -f mode=gfm`, 2026-11-20) showed the
    words of `shown` and hid those of `hidden`."""
    seen = browser_text(markdown)
    assert [word for word in shown if word not in seen] == []
    assert [word for word in hidden if word in seen] == []


@pytest.mark.parametrize(
    ("markdown", "shown", "hidden"),
    [
        (">" * 30 + " zqdeep\n\nzqafter", ["zqdeep", "zqafter"], []),
        (
            "zqthanks\n\n" + ">" * 255 + " x\n\nzqsystem",
            ["zqthanks"],
            ["zqsystem"],
        ),
        (">" * 254 + " zqshown\n\nzqafter", ["zqshown", "zqafter"], []),
        (">" * 256 + "\n\nzqhidden", [], ["zqhidden"]),
        ("<div>" * 256 + "</div>" * 256 + "\n\nzqhidden", [], ["zqhidden"]),
        ("<div>" * 255 + "zqshown", ["zqshown"], []),
        (
            "".join(" " * (2 * k) + f"- zqa{k}\n" for k in range(128)) + "\nzqhidden",
            ["zqa126"],
            ["zqa127", "zqhidden"],
        ),
        ("<div></b>" * 256 + "x\n\nzqhidden", [], ["zqhidden"]),
        ("<div/>" * 256 + "x\n\nzqhidden", [], ["zqhidden"]),
        ("<div></div2>" * 256 + "x\n\nzqhidden", [], ["zqhidden"]),
        ("<b><div></b>" * 256 + "x\n\nzqhidden", [], ["zqhidden"]),
        ("<table><td>" * 64 + "x\n\nzqhidden", [], ["zqhidden"]),
        ("<table><td>" * 63 + "zqshown", ["zqshown"], []),
        ("<div>zqa</div>" * 300 + "\n\nzqafter", ["zqafter"], []),
    ],
)
def test_a_browser_reads_the_depth_as_github_showed_it(markdown, shown, hidden):
    """GitHub's renderer (`gh api markdown -f mode=gfm`, 2026-11-21 and 22) showed
    the words of `shown` and hid those of `hidden`: nothing inside more than 255
    elements, and nothing after it."""
    seen = browser_text(markdown)
    assert [word for word in shown if word not in seen] == []
    assert [word for word in hidden if word in seen] == []


# Forms of an `<svg>` with a `<br>` in it, `{}` where a `</svg> After` may follow it,
# and whether a tag before the `<svg>` closes the `<p>` of the paragraph: an `<hr>`,
# a `<li>` and a `</table>` in a cell do; inside the element, no start tag closes
# anything, nor breaks out of it, also in a later paragraph.
CLOSED_P_FORMS = [
    ("x <hr><svg><br>Plugh{}", True),
    ("x <li><svg><br>Plugh{}", True),
    ("| a |\n|---|\n| x </table><svg><br>Plugh{} |", True),
    ("x <svg><div><br>Plugh{}", False),
    ("x <div><svg>\n\ny <div><br>Plugh{}", False),
    *(
        (f"a <svg><{name}><br> Plugh{{}}", False)
        for name in (
            "div",
            "span",
            "table",
            "ul",
            "i",
            "em",
            "strong",
            "pre",
            "h1",
            "blockquote",
            "center",
            "li",
        )
    ),
]


@pytest.mark.parametrize("ending", ["", "</svg> After"])
@pytest.mark.parametrize("rest", ["\n\nXyzzy", "\nXyzzy"])
@pytest.mark.parametrize(("form", "closes"), CLOSED_P_FORMS)
def test_a_browser_reads_an_svg_after_a_closed_paragraph_as_github_showed_it(
    form, closes, ending, rest
):
    """GitHub's renderer (`gh api markdown`, mode gfm, 2026-11-23) showed nothing
    that the `<br>` holds, up to a `</svg>` or the end of a paragraph whose `<p>`
    is open; where a tag before the `<svg>` closes it, up to the end of the text."""
    seen = browser_text(form.format(ending) + rest)
    shown = (
        ["After", "Xyzzy"]
        if ending
        else []
        if closes or "\n\n" not in rest
        else ["Xyzzy"]
    )
    assert [word for word in ("After", "Plugh", "Xyzzy") if word in seen] == shown


@pytest.mark.parametrize(
    ("markdown", "shown"),
    [
        ("x <dd><svg><br>Plugh\n\nXyzzy", []),
        ("x <dt><svg><br>Plugh\n\nXyzzy", []),
        ("x <button><svg><br>Plugh\n\nXyzzy", []),
        ("x <div><svg>\n\ny </p><br>Plugh\n\nXyzzy", []),
        ("a <svg><h2><br> Plugh\n\nXyzzy", ["Xyzzy"]),
        ("x <div><svg><br>Plugh\n\n## Steps\n\nXyzzy", []),
        ("x <svg></p><br>Plugh\n\nXyzzy", ["Plugh", "Xyzzy"]),
    ],
)
def test_a_browser_closes_the_paragraph_around_an_svg_as_github_did(markdown, shown):
    """GitHub's renderer (`gh api markdown`, mode gfm, 2026-11-23) showed these words:
    a `<dd>`, a `<dt>` and a `<button>` close the `<p>`, a `</p>` closes that of a
    later paragraph inside the element, and inside it an `<h2>` breaks out of
    nothing; a `</p>` in the element's own paragraph closes it with the `<p>`."""
    seen = browser_text(markdown)
    assert [word for word in ("Plugh", "Steps", "Xyzzy") if word in seen] == shown


def test_a_browser_shows_a_lone_angle_bracket_as_text():
    assert browser_text("x < y </") == "x < y </\n"
    assert browser_text("<div>\nx </> <!-->y<!--->z") == "\nx  yz\n"
