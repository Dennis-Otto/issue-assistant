"""The converter of way D (#520): the Markdown that the AI is to read, made from
GitHub's own HTML of a text, by a closed list of what GitHub writes.

No reader calls it yet; these tests hold it to what the page shows, on GitHub's HTML
of recorded texts and of texts rendered for them (tests/fixtures/renderings.json),
and on the renderings of the texts of the four fixtures of marker words.
"""

import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from test_assistant import CEILING, built

import issue_assistant as assistant
from issue_assistant.converter import tree_of
from support import CONFIG, GROWTH, Growth, growth, make_issue, too_slow

FIXTURES = Path(__file__).with_name("fixtures")
RENDERINGS = json.loads((FIXTURES / "renderings.json").read_text("utf-8"))
MARKERS = {
    name: json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))
    for name in (
        "hidden-content",
        "markup-over-rows",
        "tags-over-rows",
        "character-references",
    )
}
# A word as the tests compare them: letters and digits, so that `_No response_`
# holds the words of `No response`.
WORD = re.compile(r"[^\W_]+")
# Why the converter and the cleaning of the source read a word differently, and
# why the converter takes out a word that the HTML holds.
CAUSES = {
    "a tag, a comment or an autolink that the cleaning takes out",
    "the language of a fence as GitHub names it",
    "a reference or a hash that GitHub writes in short",
    "a link target of GitHub's own",
    "a note",
    "a number of a list or a footnote, which GitHub counts",
    "the source of a drawing, which the page draws",
    "content deeper than the bound of the converter",
    "a back link of a footnote, which the converter takes out",
    "the mark of a ticked box, which the converter writes as [x]",
}
TAKEN_OUT = {
    "the title of an alert, in whose place [!KIND] stands",
    "a reference whose text the source doesn't hold",
    "math that holds markup",
    "content deeper than the bound of the converter",
    "a back link of a footnote with other text",
}


def words(text):
    return set(WORD.findall(text))


def converted(html, source=""):
    return assistant.markdown_of(html, source)


def counted(result):
    """The kinds that came, with how often."""
    return {
        kind: result.count(kind) for kind in assistant.TAKEN_OUT if result.count(kind)
    }


class ShownWords(HTMLParser):
    """The words of the text nodes of GitHub's HTML that the page shows: not those
    of an `rp`, a `relative-time`, a `video`, an element only for a screen reader, a
    back link of a footnote, a drawing or math, which the converter reads by rules
    of their own."""

    SKIPPED = {"rp", "relative-time", "video", "math-renderer"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.open = []
        self.found = set()

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = set((attributes.get("class") or "").split())
        skip = (
            tag in self.SKIPPED
            or "sr-only" in classes
            or "data-footnote-backref" in classes
            or "js-render-needs-enrichment" in classes
        )
        if tag not in assistant.VOID_TAGS:
            self.open.append(skip or bool(self.open and self.open[-1]))

    def handle_endtag(self, tag):
        if self.open:
            self.open.pop()

    def handle_data(self, data):
        if not (self.open and self.open[-1]):
            self.found |= words(data)


def shown_words(html):
    parser = ShownWords()
    parser.feed(html)
    parser.close()
    return parser.found


# Each row of the closed list, as GitHub writes it, and the Markdown that the
# converter makes of it (measurement 6 of #488, and the renderings of #520).
ROWS = [
    ('<p dir="auto" align="center">zqa</p>', "zqa"),
    ('<h3 dir="auto">Area</h3>\n<p dir="auto">Live card</p>', "### Area\n\nLive card"),
    ('<h6 align="left">zqh</h6>', "###### zqh"),
    (
        '<ul dir="auto">\n<li>zqa</li>\n<li>zqb</li>\n</ul>',
        "- zqa\n- zqb",
    ),
    (
        '<ol start="3" type="a" dir="auto"><li value="7">zqv</li><li>zqw</li></ol>',
        "7. zqv\n8. zqw",
    ),
    (
        '<ul class="contains-task-list">\n<li class="task-list-item"><input '
        'type="checkbox" id="" disabled="" class="task-list-item-checkbox" '
        'aria-label="Completed task" checked=""> zqdone</li>\n<li class="task-list-'
        'item"><input type="checkbox" id="" disabled="" class="task-list-item-'
        'checkbox" aria-label="Incomplete task"> zqopen</li>\n</ul>',
        "- [x] zqdone\n- [ ] zqopen",
    ),
    (
        '<ol class="contains-task-list"><li class="task-list-item"><input type="checkbox"'
        ' id="" disabled="" class="task-list-item-checkbox" aria-label="Completed task"'
        ' checked=""> zqn</li></ol>',
        "1. [x] zqn",
    ),
    ('<blockquote>\n<p dir="auto">zqq</p>\n</blockquote>', "> zqq"),
    (
        '<div class="markdown-alert markdown-alert-warning" dir="auto"><p class="'
        'markdown-alert-title" dir="auto"><svg data-component="Octicon" class="octicon '
        'octicon-alert mr-2" viewBox="0 0 16 16" version="1.1" width="16" height="16" '
        'aria-hidden="true"><path d="M0 0"></path></svg>Warning</p><p dir="auto">zqw'
        "</p>\n</div>",
        "> [!WARNING]\n> zqw",
    ),
    (
        '<div class="highlight highlight-source-python notranslate position-relative '
        'overflow-auto" dir="auto" data-snippet-clipboard-copy-content="print(1)"><pre>'
        '<span class="pl-en">print</span>(<span class="pl-c1">1</span>)</pre></div>',
        "```python\nprint(1)\n```",
    ),
    (
        '<div class="snippet-clipboard-content notranslate position-relative '
        'overflow-auto" data-snippet-clipboard-copy-content="a ``` b"><pre lang="text"'
        ' class="notranslate"><code class="notranslate">a ``` b\n</code></pre></div>',
        "````text\na ``` b\n````",
    ),
    (
        '<p dir="auto"><code class="notranslate">#ff0000<span class="ml-1 d-inline-block'
        ' border circle color-border-subtle" style="background-color: #ff0000; height: '
        '8px; width: 8px;"></span></code> <code class="notranslate">a`b</code></p>',
        "`#ff0000` ``a`b``",
    ),
    (
        '<markdown-accessiblity-table><table role="table">\n<thead>\n<tr>\n<th align='
        '"left">L</th>\n<th align="center">C</th>\n<th align="right">R</th>\n</tr>\n'
        '</thead>\n<tbody>\n<tr>\n<td><code class="notranslate">a|b</code></td>\n<td>'
        "<strong>zqb</strong></td>\n<td><ul><li>zqli</li><li>zqlj</li></ul></td>\n</tr>"
        "\n</tbody>\n</table></markdown-accessiblity-table>",
        "| L | C | R |\n| :-- | :-: | --: |\n| `a\\|b` | **zqb** | zqli zqlj |",
    ),
    (
        '<details open=""><summary>zqs <code class="notranslate">c</code></summary>\n'
        '<p dir="auto">zqd</p>\n</details>',
        "**zqs `c`**\n\nzqd",
    ),
    (
        '<p dir="auto"><strong>a</strong> <b>b</b> <em>c</em> <i>d</i> <del>e</del> '
        "<s>f</s> <strike>g</strike> <q>h</q></p>",
        '**a** **b** _c_ _d_ ~~e~~ ~~f~~ ~~g~~ "h"',
    ),
    (
        '<p dir="auto"><sub>a</sub><sup>b</sup> <tt>c</tt> <kbd>d</kbd> <ins>e</ins> '
        "<mark>f</mark> <var>g</var> <samp>h</samp> <span>i</span></p><section>j"
        "</section><div>k</div><dl><dt>l</dt><dd>m</dd></dl>",
        "ab c d e f g h i\n\nj\n\nk\n\nl\nm",
    ),
    (
        '<p dir="auto"><ruby>漢<rp>(</rp><rt>kan</rt><rp>)</rp></ruby> '
        '<themed-picture data-catalyst-inline="true"><picture><source media="(prefers-'
        'color-scheme: dark)" srcset="https://example.com/d.png"><img alt="zqpic" '
        'src="https://example.com/l.png" style="max-width: 100%;"></picture>'
        "</themed-picture></p>",
        "漢kan ![zqpic](https://example.com/l.png)",
    ),
    ('<p dir="auto">a<br>\nb</p>\n<hr>\n<p dir="auto">c</p>', "a\nb\n\n---\n\nc"),
    (
        '<p dir="auto"><g-emoji class="g-emoji" alias="tada" fallback-src="https://'
        'github.githubassets.com/images/icons/emoji/unicode/1f389.png">🎉</g-emoji></p>',
        "🎉",
    ),
    (
        '<p dir="auto"><math-renderer class="js-inline-math" style="display: inline-'
        'block" data-run-id="1">$x + 1$</math-renderer> <math-renderer class="js-inline-'
        'math" style="display: inline-block" data-run-id="1">$\\color{white}{zqc}$'
        "</math-renderer></p>",
        "$x + 1$ (math removed)",
    ),
    (
        '<p dir="auto">zqa<sup><a href="#user-content-fn-note-1" id="user-content-fnref-'
        'note-1" data-footnote-ref="" aria-describedby="footnote-label">2</a></sup></p>'
        '<section data-footnotes="" class="footnotes"><h2 id="footnote-label" class='
        '"sr-only" dir="auto">Footnotes</h2>\n<ol dir="auto">\n<li id="user-content-fn-'
        'x-1">\n<p dir="auto">zqone <a href="#user-content-fnref-x-1" data-footnote-'
        'backref="" aria-label="Back to reference 1" class="data-footnote-backref">↩</a>'
        '</p>\n</li>\n<li id="user-content-fn-note-1">\n<p dir="auto">zqtwo <a href="#user'
        '-content-fnref-note-1" data-footnote-backref="" aria-label="Back to reference 2"'
        ' class="data-footnote-backref">↩<sup>2</sup></a></p>\n</li>\n</ol>\n</section>',
        "zqa[^2]\n\n[^1]: zqone\n[^2]: zqtwo",
    ),
]


@pytest.mark.parametrize(("html", "markdown"), ROWS)
def test_each_row_of_the_list_reads_as_github_shows_it(html, markdown):
    result = converted(html, "https://example.com/l.png")
    assert result.text == markdown
    assert result.count("unknown") == 0


# What the page never shows, and what the converter reads by rules of its own: the
# HTML, the marker words that it shows, those that it hides, and what counts.
SHOWN_AND_HIDDEN = [
    (
        '<p dir="auto">a <relative-time datetime="2020-01-01T00:00:00Z" lang="de" '
        'title="zqt">zqrt</relative-time>b</p>',
        ["a", "b"],
        ["zqrt", "zqt"],
        {"hidden": 1},
    ),
    ('<p dir="auto">a<rp>(zqrp)</rp>b</p>', ["ab"], ["zqrp"], {"hidden": 1}),
    ('<p dir="auto">a<video src="v">zqv</video>b</p>', ["a b"], ["zqv"], {"hidden": 1}),
    (
        '<section class="js-render-needs-enrichment render-needs-enrichment position-'
        'relative" data-type="mermaid"><div class="render-plaintext-hidden"><pre lang='
        '"mermaid">graph TB\n A[zqlabel]</pre></div><span class="sr-only">Loading'
        "</span></section>",
        [assistant.DRAWING_NOTE],
        ["zqlabel", "Loading"],
        {"drawings": 1},
    ),
    (
        '<pre lang="geojson" class="notranslate"><code class="notranslate">{"zqgeo": 1}'
        "</code></pre>",
        [assistant.DRAWING_NOTE],
        ["zqgeo"],
        {"drawings": 1},
    ),
    (
        '<p dir="auto"><img src="https://private-user-images.githubusercontent.com/1/2-'
        'abc.png?jwt=x.y.z" alt="zqshot" style="max-width: 100%;"></p>',
        ["![zqshot]()"],
        ["jwt"],
        {},
    ),
    (
        '<p id="user-content-zqid" lang="de" title="zqt" dir="auto">zqp <span lang="de" '
        'title="zqst" id="user-content-x">zqs</span> <img src="https://example.com/i.png"'
        ' title="zqit" alt="zqalt" style="max-width: 100%;"></p>',
        ["zqp", "zqs", "zqalt"],
        ["zqid", "zqt", "zqst", "zqit"],
        {},
    ),
    (
        '<p dir="auto"><a href="https://example.com/" name="user-content-zqn" rel="'
        'nofollow">zqan</a> <q cite="https://example.com/q">zqq</q> <del cite="https://'
        'example.com/d" datetime="2020">zqdel</del></p><blockquote cite="https://example'
        '.com/b">zqbq</blockquote><div lang="de" itemprop="x" dir="auto">zqlang</div>',
        ["zqan", '"zqq"', "~~zqdel~~", "> zqbq", "zqlang"],
        ["zqn"],
        {},
    ),
]


@pytest.mark.parametrize(("html", "shown", "hidden", "counts"), SHOWN_AND_HIDDEN)
def test_what_the_page_never_shows_goes_and_counts(html, shown, hidden, counts):
    result = converted(html, "https://example.com/")
    assert [part for part in shown if part not in result.text] == []
    assert [part for part in hidden if part in result.text] == []
    assert counted(result) == counts


@pytest.mark.parametrize(
    ("html", "name"),
    [
        ('<p dir="auto">Value<foo>zqx</foo>Error</p>', "tag <foo>"),
        ('<p dir="auto">Value<span onclick="x">zqx</span>Error</p>', "span[onclick]"),
        ('<p dir="auto">Value<span class="zq">zqx</span>Error</p>', "span[class="),
        ('<p dir="auto">Value<p align="zq">zqx</p>Error</p>', "p[align="),
        ('<p dir="auto">Value<span id="zqid">zqx</span>Error</p>', "span[id="),
        ('<p dir="auto">Value<!-- zqx -->Error</p>', "<!-- -->"),
        ('<p dir="auto">Value<!DOCTYPE zqx>Error</p>', "<!>"),
        ('<p dir="auto">Value<?zqx>Error</p>', "<?>"),
        ('<p dir="auto">Value</zqx>Error</p>', "</zqx>"),
        ('<p dir="auto">Value<b>zqx</p>Error', "element without end <b>"),
        ('<p dir="auto">Value<div/>zqx</p>Error', "element without end <div>"),
        ('<p dir="auto">Value Error</p><span>zqx', "element without end <span>"),
        ('<p dir="auto">Value<svg>zqx</svg>Error</p>', "tag <svg>"),
        (
            '<p dir="auto">Value<code class="notranslate">a<foo>zqx</foo>b</code>Error</p>',
            "tag <foo>",
        ),
        (
            '<p dir="auto">Value<code class="notranslate">a<p>zqx</p>b</code>Error</p>',
            "in code <p>",
        ),
        (
            '<pre class="notranslate"><code class="notranslate">a<span class="zq">zqx'
            "</span>b</code></pre><p>Value Error</p>",
            "span[class=",
        ),
        (
            '<pre class="zq"><code class="notranslate">zqx</code></pre><p>Value Error</p>',
            "pre[class=",
        ),
        (
            '<pre class="notranslate"><code class="zq">zqx</code></pre><p>Value Error</p>',
            "code[class=",
        ),
        (
            '<p dir="auto">Value<math-renderer class="js-inline-math" style="display: '
            'inline-block" data-run-id="1">$<b>zqx</b>$</math-renderer>Error</p>',
            "element in math",
        ),
        (
            '<p dir="auto">Value<sup><a href="#fn" data-footnote-ref="" aria-describedby'
            '="footnote-label"><b>zqx</b></a></sup>Error</p>',
            "element in a footnote reference",
        ),
    ],
)
def test_an_element_off_the_list_gives_no_text(html, name):
    """Each counts with its name and leaves a space, so that `Value` and `Error`
    stay two words; nothing inside it gives text, in code, `pre`, math and a
    footnote reference as well."""
    result = converted(html)
    assert "zqx" not in result.text
    assert {"Value", "Error"} <= words(result.text), result.text
    assert "ValueError" not in result.text
    assert any(name in found for found in result.counts["unknown"]), result.counts


def test_a_reference_of_many_digits_stands_for_its_character():
    """A decimal reference of more digits than int() reads, as Fuzzing found one,
    stands for no character, as HTML reads it; zeros before its digits change
    nothing, in the text and in an attribute."""
    digits = "8" + "3" * 5000
    result = converted(f"<p>/&#{digits}x &#{'0' * 5000}65; &#000; &#x41; &#</p>")
    assert result.text == "/�x A � A &#"
    found = tree_of(f'<img alt="&#{digits};&#{"0" * 5000}66">')
    assert found.children[0].attributes["alt"] == "�B"


def test_a_run_of_text_is_one_text_node():
    """html.parser hands on each `<` of a run by itself, as Fuzzing found it slow;
    the tree holds one text node for each run of text, as a browser does, and the
    text of an element that html.parser reads raw stays as it is. How long a run of
    `<` takes is held by `a run of <` of CONVERTER_TEXTS, to its growth (#528)."""
    root = tree_of('a<<b c="<<">x<<<script>y<<z</script>&#0;<<')
    assert root.children[0] == "a<"
    b = root.children[1]
    assert (b.tag, b.attributes) == ("b", {"c": "<<"})
    assert b.children[0] == "x<<"
    assert b.children[1].tag == "script"
    assert b.children[1].children == ["y<<z"]
    # After an element of raw text, the text stays as html.parser reads it.
    assert b.children[2] == "�<"
    assert b.children[3].tag == "<markup without end>"
    assert converted("<p>" + "<" * assistant.MAX_TEXT + "</p>").text.count("<") > 0


# The end of the input that failed Fuzzing by chance on a slow runner (run
# 45041178791, #528), after a run of 76,180 `<`.
FUZZED_END = (
    "&" + "\x00" * 31 + "'" + "\x00" * 9 + "<!--\x00\x00'&'&\x00<![\x00\x00222222"
)


def test_the_input_that_failed_fuzzing_by_chance_reads_as_before():
    """The run of `<` stays text, the nulls become replacement characters and the
    unclosed comment is markup without end, as on main before #528."""
    result = converted("<" * 76_180 + FUZZED_END)
    assert result.text == "<" * 76_180 + "&" + "\ufffd" * 31 + "'" + "\ufffd" * 9
    assert result.addresses == ()
    assert counted(result) == {"unknown": 1}
    assert result.counts["unknown"] == {"<markup without end>": 1}


def test_a_space_goes_only_where_the_page_leaves_one():
    """The brackets of ruby, a heading only for screen readers and a back link of a
    footnote leave nothing, and two spaces become one."""
    result = converted(
        '<p dir="auto">a<rp>(</rp>b <relative-time>x</relative-time> c<span class='
        '"sr-only">d</span>e</p>'
    )
    assert result.text == "ab ce"


def test_a_forged_footnote_reads_only_what_the_page_shows():
    """A reporter can write the markup of a footnote, and GitHub keeps it (probe p4
    of #520): a back link with other text goes and counts, a reference with words
    is a link, and a reference with a number is one."""
    html = (
        '<p dir="auto">U2 </p><section data-footnotes="" class="footnotes"><h2 id="'
        'footnote-label" class="sr-only" dir="auto">Footnotes</h2><ol dir="auto"><li id'
        '="user-content-fn-9-6">zqfakenote <a href="#user-content-fnref-9-6" data-'
        'footnote-backref="" aria-label="Back to reference 9" class="data-footnote-'
        'backref">zqforged</a></li></ol></section><p dir="auto"></p>\n<p dir="auto">U3 '
        '<sup><a href="#user-content-fn-2-6" data-footnote-ref="" aria-describedby="'
        'footnote-label">zq words</a></sup> U4 <sup><a href="#user-content-fn-3-6" data-'
        'footnote-ref="" aria-describedby="footnote-label">4</a></sup></p><h2 id="user-'
        'content-footnote-label" dir="auto">zqlabel</h2>'
    )
    result = converted(html, "U2 U3 U4")
    assert result.text == (
        "U2\n\n[^1]: zqfakenote\n\nU3 zq words U4 [^4]\n\n## zqlabel"
    )
    assert result.counts["unknown"] == {"back link with other text": 1}
    assert counted(result) == {"unknown": 1, "hidden": 1, "targets": 1}


def reference(text, href, kind="issue-link js-issue-link"):
    return (
        f'<p dir="auto"><a class="{kind}" data-error-text="Failed" data-id="1" data-'
        'permission-text="Title is private" data-url="https://github.com/o/r/issues/1" '
        f'data-hovercard-type="issue" data-hovercard-url="/o/r/issues/1/hovercard" '
        f'href="{href}">{text}</a></p>'
    )


@pytest.mark.parametrize(
    ("html", "source", "markdown", "counts"),
    [
        (
            reference("#488", "https://github.com/o/r/issues/488"),
            "see #488",
            "#488",
            {},
        ),
        (
            reference("GH-488", "https://github.com/o/r/issues/488"),
            "GH-488",
            "GH-488",
            {},
        ),
        (
            reference("o/r#488", "https://github.com/o/r/issues/488"),
            "o/r#488",
            "o/r#488",
            {},
        ),
        # The address of a comment, which GitHub shows as `#N (comment)`.
        (
            reference("#488 (comment)", "https://github.com/o/r/issues/488#c-1"),
            "https://github.com/o/r/issues/488#c-1",
            "https://github.com/o/r/issues/488#c-1",
            {},
        ),
        (
            '<p dir="auto"><a class="commit-link" data-hovercard-type="commit" href="'
            'https://github.com/o/r/commit/0fd016b635"><tt>0fd016b</tt></a></p>',
            "0fd016b635",
            "0fd016b",
            {},
        ),
        (
            '<p dir="auto"><a class="user-mention notranslate" data-hovercard-type="user"'
            ' href="https://github.com/dennis-otto" data-octo-click="hovercard-link-click"'
            ">@Dennis-Otto</a></p>",
            "thanks @dennis-otto",
            "@Dennis-Otto",
            {},
        ),
        # A renamed repository: GitHub writes its new name, which the source
        # doesn't hold, nor the address.
        (
            reference("o/new#416", "https://github.com/o/new/issues/416"),
            "o/old#416",
            "",
            {"references": 1},
        ),
        # A reference rendered for a viewer who can see a private repository.
        (
            reference("o/private#5", "https://github.com/o/private/issues/5"),
            "o/control#5",
            "",
            {"references": 1},
        ),
        # A reference holds only what GitHub writes in it.
        (
            reference("#1<foo>x</foo>", "https://github.com/o/r/issues/1"),
            "#1 x",
            "",
            {"references": 1},
        ),
    ],
)
def test_a_reference_reads_only_what_the_source_holds(html, source, markdown, counts):
    result = converted(html, source)
    assert result.text == markdown
    assert counted(result) == counts


@pytest.mark.parametrize(
    ("html", "source", "markdown", "counts"),
    [
        (
            '<p dir="auto"><a href="https://example.com/a" rel="nofollow">t</a></p>',
            "[t](https://example.com/a)",
            "[t](https://example.com/a)",
            {},
        ),
        (
            '<p dir="auto"><a href="https://example.com/a%20b" rel="nofollow">t</a></p>',
            "[t](<https://example.com/a b>)",
            "[t](https://example.com/a b)",
            {},
        ),
        # GitHub's own target of an identifier, which the source doesn't hold.
        (
            '<p dir="auto"><a href="https://github.com/advisories/GHSA-x" rel="nofollow">'
            'GHSA-x</a> and <a href="https://github.com/o/r/blob/main/a.md">a</a></p>',
            "GHSA-x and [a](a.md)",
            "GHSA-x and a",
            {"targets": 2},
        ),
        (
            '<p dir="auto"><a href="http://www.example.com" rel="nofollow">www.example.'
            'com</a> <a href="mailto:a@example.com">a@example.com</a></p>',
            "www.example.com a@example.com",
            "www.example.com a@example.com",
            {},
        ),
        (
            '<p dir="auto"><a target="_blank" rel="noopener noreferrer nofollow" href="'
            'https://camo.githubusercontent.com/1"><img src="https://camo.githubusercontent'
            '.com/1" alt="zqa" data-canonical-src="https://example.com/i.png" style="max-'
            'width: 100%;"></a> <a target="_blank" rel="noopener noreferrer" href="https://'
            'github.com/user-attachments/assets/1"><img src="https://private-user-images.'
            'githubusercontent.com/1?jwt=x" alt="zqb" style="max-width: 100%;"></a> <img '
            'alt="zqc"></p>',
            "![zqa](https://example.com/i.png) ![zqb](https://github.com/user-attachments/"
            "assets/1) <img alt=zqc>",
            "![zqa](https://example.com/i.png) ![zqb](https://github.com/user-attachments/"
            "assets/1) ![zqc]()",
            {},
        ),
    ],
)
def test_a_link_and_a_picture_keep_only_an_address_that_the_source_holds(
    html, source, markdown, counts
):
    result = converted(html, source)
    assert result.text == markdown
    assert counted(result) == counts
    assert all(address in markdown for address in result.addresses)


def test_a_row_that_would_start_a_block_gets_a_backslash():
    html = "".join(
        f'<p dir="auto">{row}</p>'
        for row in (
            "### a",
            "&gt; b",
            "- c",
            "+ d",
            "* e",
            "===",
            "---",
            "1. f",
            "2) g",
        )
    )
    html += '<p dir="auto">```h</p><p dir="auto">~~~i</p><p dir="auto">| j |</p>'
    html += '<p dir="auto">#1 and +1 stay</p>'
    rows = converted(html).text.split("\n\n")
    assert rows == [
        "\\### a",
        "\\> b",
        "\\- c",
        "\\+ d",
        "\\* e",
        "\\===",
        "\\---",
        "1\\. f",
        "2\\) g",
        "\\```h",
        "\\~~~i",
        "\\| j |",
        "#1 and +1 stay",
    ]


def test_loose_text_becomes_a_paragraph_of_its_own():
    """Text that GitHub puts at block level, as a caption in the wrapper of a table
    and a `figcaption` after `</p>` (probes p1 and f2 of #520)."""
    html = (
        '<p dir="auto">zqa</p>zqcap\n<markdown-accessiblity-table>zqtcap<table role="'
        'table"><tbody><tr><th>a</th></tr><tr><td>b</td></tr></tbody></table>'
        "</markdown-accessiblity-table>"
    )
    assert (
        converted(html).text == "zqa\n\nzqcap\n\nzqtcap\n\n|  |\n| --- |\n| a |\n| b |"
    )


def test_a_table_keeps_each_row_and_spans_as_empty_cells():
    html = (
        '<table><thead><tr><th colspan="2">zqh</th></tr></thead><tbody><tr><td '
        'rowspan="2">zqr</td><td>zqc</td></tr></tbody><tfoot><tr><td>zqf</td></tr>'
        "</tfoot></table><table><tbody><tr><td>a</td><td><pre>b\nc</pre><details>"
        "<summary>d</summary>e</details></td></tr></tbody></table>"
    )
    assert converted(html).text == (
        "| zqh |  |\n| --- | --- |\n| zqr | zqc |\n|  | zqf |\n\n"
        "|  |  |\n| --- | --- |\n| a | `b c` d e |"
    )


def test_a_list_numbers_in_digits_and_keeps_its_rows_together():
    html = (
        '<ol dir="auto">\n<li>a\n<ol dir="auto">\n<li>b\n<blockquote>\n<p dir="auto">zqq'
        "</p>\n</blockquote>\n</li>\n</ol>\n</li>\n<li></li>\n</ol>\n<ul><li><pre>x\ny</pre>"
        "</li></ul>"
    )
    assert converted(html).text == (
        "1. a\n   1. b\n\n      > zqq\n2.\n\n- ```\n  x\n  y\n  ```"
    )


def test_with_notes_false_each_note_is_a_space():
    html = (
        '<p dir="auto">Value<math-renderer class="js-inline-math" style="display: '
        'inline-block" data-run-id="1">$\\color{red}{x}$</math-renderer>Error</p>'
        '<section class="js-render-needs-enrichment" data-type="mermaid">x</section>'
    )
    assert assistant.markdown_of(html, "", notes=False).text == "Value Error"
    assert assistant.markdown_of(html, "").text == (
        f"Value{assistant.MATH_NOTE}Error\n\n{assistant.DRAWING_NOTE}"
    )


# The shapes that nest, each a thousand times, past the bound of the depth.
NESTED = {
    "span and div": ("<span><div>", "</div></span>"),
    "quotes": ("<blockquote>", "</blockquote>"),
    "list items": ("<ul><li>", "</li></ul>"),
    "details": ("<details><summary>s</summary>", "</details>"),
    "bold": ("<b>", "</b>"),
}


@pytest.mark.parametrize("shape", NESTED.values(), ids=NESTED)
def test_deep_html_converts_within_the_bounds(shape):
    start, end = shape
    result = converted(start * 1000 + "zqdeep" + end * 1000 + "<p>zqafter</p>")
    assert "zqdeep" not in result.text and "zqafter" in result.text
    assert result.count("depth") >= 1 and result.count("unknown") == 0
    shallow = converted(start * 20 + "zqdeep" + end * 20)
    assert "zqdeep" in shallow.text and shallow.count("depth") == 0


def test_a_long_text_stops_at_the_bound_of_the_length():
    result = converted('<p dir="auto">x</p>\n' * 70_000 + "<p>zqlast</p>")
    assert len(result.text) <= assistant.LONGEST + len(assistant.CUT_NOTE) + 1
    assert result.text.endswith(assistant.CUT_NOTE)
    assert result.counts["cut"] == {"text": 1} and "zqlast" not in result.text
    quotes = converted(
        "<blockquote>" * 120 + "<p>x</p>" * 4_000 + "</blockquote>" * 120
    )
    assert quotes.count("cut") == 1
    assert len(quotes.text) <= assistant.LONGEST + len(assistant.CUT_NOTE) + 1


def test_a_source_that_is_searched_too_often_holds_nothing_more():
    """Each search of the source for a reference or an address starts where the
    last one stopped, and a budget of reading keeps them linear in the source: past
    it, the safe side."""
    held = assistant.Holdings("ab" * 10)
    assert held.holds("ab") and held.holds("AB", ignore_case=True)
    assert not held.holds("") and not held.holds("abc")
    held.left = 0
    assert not held.holds("ba")
    assert held.address("ab") == "ab"


def recorded_texts():
    return [(text["name"], text) for text in RENDERINGS["texts"]]


@pytest.mark.parametrize(
    "text",
    [text for _, text in recorded_texts()],
    ids=[name for name, _ in recorded_texts()],
)
def test_the_converter_loses_no_word_of_the_html(text):
    """Every word of the HTML's text nodes that the page shows is in the text, but
    for those that a rule takes out, which the fixture lists with its rule. A word
    counts where the text holds it, also inside a longer one: the text nodes of
    code and of ruby stand side by side."""
    result = converted(text["html"], text["source"])
    lost = {word for word in shown_words(text["html"]) if word not in result.text}
    assert {word: text["taken out"].get(word) for word in lost} == text["taken out"]
    assert set(text["taken out"].values()) <= TAKEN_OUT


@pytest.mark.parametrize(
    "text",
    [text for _, text in recorded_texts()],
    ids=[name for name, _ in recorded_texts()],
)
def test_the_converter_and_the_cleaning_differ_as_recorded(text):
    """The words that only `markdown_of` reads and those that only `shown_text` of
    the source reads, each with its cause. A change of the cleaning, such as one for
    #506 or #517, changes the entries that it changes."""
    ours = words(converted(text["html"], text["source"]).text)
    cleaned = words(assistant.shown_text(text["source"])[0])
    differences = text["differences"]
    assert sorted(ours - cleaned) == sorted(differences["only in the converter"])
    assert sorted(cleaned - ours) == sorted(differences["only in the cleaning"])
    causes = {
        *differences["only in the converter"].values(),
        *differences["only in the cleaning"].values(),
    }
    assert causes <= CAUSES


def test_the_fixture_names_where_each_rendering_came_from():
    sources = {text["html from"] for text in RENDERINGS["texts"]}
    assert sources == set(RENDERINGS["html from"])
    assert len({name for name, _ in recorded_texts()}) == len(RENDERINGS["texts"])


FORMS = [text for text in RENDERINGS["texts"] if "\n### " in "\n" + text["source"]]


@pytest.mark.parametrize("text", FORMS, ids=[text["name"] for text in FORMS])
def test_the_form_rows_read_as_from_the_source(text):
    """The labels of a form come out the same on the text as on the cleaned source,
    and what the assistant decides from a reporter's words decides the same."""
    source, html = text["source"], text["html"]
    ours = converted(html, source).text
    cleaned = assistant.shown_text(source)[0]
    labels = [label for label, _ in assistant.form_parts(ours, outside_code=True)[1]]
    expected = [
        label for label, _ in assistant.form_parts(cleaned, outside_code=True)[1]
    ]
    assert labels == expected
    plain = assistant.markdown_of(html, source, notes=False).text
    shown = assistant.as_shown(source)
    issue, plain_issue = make_issue(body=source), make_issue(body=plain)
    assert assistant.box_ticked(plain, CONFIG) == assistant.box_ticked(shown, CONFIG)
    assert assistant.form_labels(plain_issue, CONFIG) == assistant.form_labels(
        issue, CONFIG
    )
    assert assistant.issue_language(plain_issue, []) == assistant.issue_language(
        issue, []
    )
    assert assistant.denies_closing(plain, "o/r", 1) == assistant.denies_closing(
        source, "o/r", 1
    )
    assert set(assistant.search_terms(plain_issue)) == set(
        assistant.search_terms(issue)
    ) | set(text.get("search terms only in the converter", []))


def test_no_answer_of_a_form_reads_differently():
    """`_No response_` stays an empty answer."""
    html = '<h3 dir="auto">Logs</h3>\n<p dir="auto"><em>No response</em></p>'
    assert assistant.form_parts(converted(html).text)[1] == [("Logs", "")]


MARKER_CASES = [
    (f"{name}-{index}", case)
    for name, data in MARKERS.items()
    for index, case in enumerate(data["texts"])
]


def test_each_text_of_marker_words_has_its_rendering():
    for _, case in MARKER_CASES:
        assert isinstance(case["html"], str)


def test_the_markers_read_as_github_shows_them():
    """On the renderings of the four fixtures of marker words, no word that GitHub
    hides is in the text, but the `alt` of a picture, which the converter keeps
    (#520), and each that it shows is, or the fixture names the rule that takes it
    out."""
    wrong = []
    for name, case in MARKER_CASES:
        text = converted(case["html"], case["text"]).text
        found = words(text) | {text}
        hidden = [
            word
            for word in case["github hides"]
            if word in text and word not in case.get("the converter keeps", {})
        ]
        missing = [
            word
            for word in case["github shows"]
            if not any(word in part for part in found)
            and word not in case.get("the converter takes out", {})
        ]
        if hidden or missing:
            wrong.append((name, hidden, missing))
    assert wrong == []


# Long HTML texts in the shape of SLOW_TEXTS of tests/test_assistant.py, each built
# with an eighth of its repetitions too, the depth held inside a piece.
P = '<p dir="auto">'
CONVERTER_TEXTS = {
    "rows in nested quotes": [
        "<blockquote>" * 120,
        (P + "row</p>\n", 4_000),
        "</blockquote>" * 120,
    ],
    # A list item is two open elements, so 60 of them stay below the bound.
    "rows in nested list items": [
        "<ul><li>" * 60,
        (P + "row</p>\n", 4_000),
        "</li></ul>" * 60,
    ],
    "pairs past the bound of the depth": [
        ("<span><div>" * 70 + "x" + "</div></span>" * 70, 120),
        (P + "row</p>\n", 2_000),
    ],
    "rows in one quote": ["<blockquote>", (P + "row</p>\n", 8_000), "</blockquote>"],
    "highlighted code": [
        '<div class="highlight highlight-source-python"><pre>',
        ('<span class="pl-k">def</span> <span class="pl-en">f</span>():\n', 16_000),
        "</pre></div>",
    ],
    # Spans widen each row to WIDEST columns at most, and the rows past the cut
    # aren't padded: without both, these grew with the square or the cube.
    "rows of cells with wide spans": [
        "<table>",
        ('<tr><td colspan="999" rowspan="999">x</td></tr>', 800),
        "</table>",
    ],
    "a row of wide spans over empty rows": [
        "<table><tr>",
        ('<td colspan="999" rowspan="999">x</td>', 60),
        "</tr>",
        ("<tr></tr>", 999),
        "</table>",
    ],
    "a row of wide cells over narrow rows": [
        "<table><tr>",
        ('<td colspan="999">x</td>', 800),
        "</tr>",
        ("<tr><td>y</td></tr>", 800),
        "</table>",
    ],
    "rows of a table": [
        '<table role="table"><thead><tr><th>a</th><th>b</th></tr></thead><tbody>',
        ("<tr><td>b</td><td>c</td></tr>", 6_000),
        "</tbody></table>",
    ],
    "references in a row": [
        P,
        (
            '<a class="issue-link js-issue-link" href="https://github.com/o/r/issues/1">#1'
            "</a> ",
            4_000,
        ),
        "</p>",
    ],
    "footnote references and their notes": [
        P,
        ('<sup><a href="#user-content-fn-1" data-footnote-ref="">1</a></sup>', 3_000),
        '</p><section data-footnotes="" class="footnotes"><ol>',
        (
            '<li id="user-content-fn-1"><p>n <a href="#x" class="data-footnote-backref" '
            'data-footnote-backref="">↩</a></p></li>',
            2_000,
        ),
        "</ol></section>",
    ],
    "nested details": [
        ("<details><summary>s</summary>" * 8 + "x" + "</details>" * 8, 1_000)
    ],
    "task items": [
        '<ul class="contains-task-list">',
        (
            '<li class="task-list-item"><input type="checkbox" id="" disabled="" class="'
            'task-list-item-checkbox" checked=""> x</li>',
            3_000,
        ),
        "</ul>",
    ],
    "alerts": [
        (
            '<div class="markdown-alert markdown-alert-note"><p class="markdown-alert-'
            'title">Note</p><p>x</p></div>',
            2_000,
        ),
    ],
    "math": [
        P,
        (
            '<math-renderer class="js-inline-math" style="display: inline-block" data-run-'
            'id="1">$x\\y$</math-renderer> ',
            2_000,
        ),
        "</p>",
    ],
    "elements that are never shown": [
        P,
        ("<rp>(</rp><video>x</video>y", 6_000),
        "</p>",
    ],
    "elements that are not on the list": [
        P,
        ("<foo>x</foo><b class=y>z</b>w", 6_000),
        "</p>",
    ],
    "unclosed comments and tags": [P, ("<!--", 8_000), ("<a ", 8_000)],
    "character references": [P, ("&#xE0041;&amp;&ZeroWidthSpace;x", 6_000), "</p>"],
    # The dense but linear HTML that is the slowest under the fuzzer (#528):
    # html.parser hands on a `<` that can't start a tag by itself, and reads each tag.
    "a run of <": [P, ("<", 2 * assistant.MAX_TEXT), "</p>"],
    "< before a space": [P, ("< ", assistant.MAX_TEXT), "</p>"],
    "< before a null": [P, ("<\x00", assistant.MAX_TEXT), "</p>"],
    "the input that failed Fuzzing by chance": [("<", 76_180), FUZZED_END],
    "rows of a table without a table": [("<tr><td>b</td><td>c</td></tr>", 4_519)],
    "paragraphs without an end": [("<p>x", assistant.MAX_TEXT // 2)],
}


@pytest.mark.parametrize("parts", CONVERTER_TEXTS.values(), ids=CONVERTER_TEXTS)
def test_no_html_makes_the_converter_slow(parts):
    eighth, whole = built(parts, 8), built(parts)

    def convert(html):
        return lambda: assistant.markdown_of(html, html)

    measured = growth(convert(eighth), convert(whole))
    assert measured.times < GROWTH and measured.whole < CEILING, (
        f"{measured.eighth:.4f} s on an eighth of the text, "
        f"{measured.whole:.4f} s on the whole, {measured.times:.2f} times as long"
    )
    result = assistant.markdown_of(whole, whole)
    assert len(result.text) <= assistant.LONGEST + len(assistant.CUT_NOTE) + 1


def test_the_fuzz_target_judges_a_slow_conversion_by_its_growth(monkeypatch):
    """fuzz/fuzz_converter.py passes a conversion slower than its budget that grows
    with its text, and fails one that grows with its square, one above its ceiling
    and one without an eighth (#528). No input is slower than the budget on a fast
    machine, so local fuzzing never gets here."""

    def linear(text):
        return lambda: [text.count("\x00", i, i + 64) for i in range(0, len(text), 64)]

    def square(text):
        return lambda: [text.count("\x00", i) for i in range(0, len(text), 64)]

    eighth, whole = "x" * 8_000, "x" * 64_000
    assert too_slow(0.5, 8, square(eighth), square(whole), 0.5, 1) is None
    assert too_slow(0.6, 8, linear(eighth), linear(whole), 0.5, 1) is None
    problem = too_slow(0.6, 8, square(eighth), square(whole), 0.5, 1)
    assert problem.startswith("took ") and problem.endswith(
        "(at most 13 times and 1 s)"
    )
    assert GROWTH == 13
    assert too_slow(0.6, 7, linear(eighth), linear(whole), 0.5, 1) == (
        "took 0.60 s, more than 0.5 s"
    )

    def linear_but_slow(eighth, whole):
        return Growth(0.15, 1.2, 8.0)

    assert too_slow(0.9, 8, print, print, 0.5, 1, linear_but_slow) == (
        "took 0.1500 s on an eighth of the text, 1.2000 s on the whole, "
        "8.00 times as long (at most 13 times and 1 s)"
    )

    # A first call above the ceiling is told against one call of its eighth, and
    # the whole is not called again: the timeout of the fuzzer counts every call.
    calls = []
    clock = iter([2.0, 2.25, 3.0, 3.0])
    monkeypatch.setattr("support.time.process_time", lambda: next(clock))
    assert too_slow(1.5, 8, lambda: calls.append(8), calls.clear, 0.5, 1) == (
        "took 0.2500 s on an eighth of the text, 1.5000 s on the whole, "
        "6.00 times as long (at most 13 times and 1 s)"
    )
    assert too_slow(1.5, 8, lambda: calls.append(8), calls.clear, 0.5, 1).startswith(
        "took 0.0000 s on an eighth of the text, 1.5000 s on the whole, inf times"
    )
    assert calls == [8, 8]


CHIP = (
    '<span class="ml-1 d-inline-block border circle color-border-subtle" style="'
    'background-color: #ff0000; height: 8px; width: 8px;"></span>'
)


@pytest.mark.parametrize(
    ("html", "markdown", "counts"),
    [
        # GitHub writes none of these, but a reporter's HTML or a fuzzer can.
        ('<p dir="auto">a<![CDATA[zqx]]>b</p>', "a b", {"unknown": 1}),
        ("<li>zqa</li><td>zqb</td><tr><td>zqc</td></tr>", "zqa\n\nzqb\n\nzqc", {}),
        ("<p>a" + CHIP + "b</p>", "ab", {}),
        ("<sup><b>zqa</b></sup> <sup>zqb</sup>", "**zqa** zqb", {}),
        (
            '<sup><a href="#f" data-footnote-ref="" onclick="x">1</a></sup>',
            "",
            {"unknown": 1},
        ),
        (
            "<code>a<br>b" + CHIP + '<span class="sr-only">zqx</span></code>'
            "<pre>c<br>d</pre>",
            "`a b`\n\n```\nc\nd\n```",
            {"hidden": 1},
        ),
        (
            "<code>" + "<span>" * 200 + "zqx" + "</span>" * 200 + "</code>",
            "` `",
            {"depth": 1},
        ),
        ("<table>zqloose<td>zqa</td></table>", "zqloose\n\n|  |\n| --- |\n| zqa |", {}),
        (
            '<table><tr><td rowspan="3">a</td><td rowspan="3">b</td></tr><tr></tr>'
            "<tr><td>c</td></tr></table>",
            "|  |  |  |\n| --- | --- | --- |\n| a | b |  |\n|  |  |  |\n|  |  | c |",
            {},
        ),
        (
            '<table><tr><td rowspan="2">a</td><td>b</td><td rowspan="2">c</td></tr>'
            "<tr></tr></table>",
            "|  |  |  |\n| --- | --- | --- |\n| a | b | c |\n|  |  |  |",
            {},
        ),
        ('<p><a href="x">a<div>zqb</div></a></p>', "a\n\nzqb", {}),
    ],
)
def test_html_that_github_doesn_t_write_fails_closed(html, markdown, counts):
    result = converted(html)
    assert result.text == markdown
    assert counted(result) == counts


def test_a_table_keeps_widest_columns():
    wide = assistant.WIDEST
    result = converted(
        f'<table><tr><td colspan="{wide + 5}" rowspan="2">zqa</td><td>zqb</td></tr>'
        "<tr></tr><tr><td>zqc</td></tr></table>"
    )
    rows = result.text.split("\n")
    assert [row.count("|") for row in rows] == [wide + 1] * 5
    assert "zqa" in rows[2] and "zqc" in rows[4] and "zqb" not in result.text
    assert result.counts["cut"] == {
        f"spans past {wide} columns": 1,
        f"cells past {wide} columns": 1,
    }
