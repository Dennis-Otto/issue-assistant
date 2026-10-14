"""Properties of the code that turns the engine's text into a comment, for any input.

The engine's answer may be steered by an issue, so these checks don't trust examples:
Hypothesis generates the text, including the characters that Markdown treats
specially, and whole answers from their schema, and looks for a comment that would
mention someone, link elsewhere or break the checks, as the oracle of
tests/oracle.py sees it. The reading of issues, which anyone writes, is checked the
same way against how it read them before.
"""

import json
import re

import hypothesis
import pytest
from hypothesis import given
from hypothesis import strategies as st

import issue_assistant as assistant
from oracle import PARSER, problems
from support import (
    BOT_USER,
    CONFIG,
    EARLIER_HTML,
    ESCAPES,
    REPOSITORY,
    answer_of,
    at,
    earlier_form_fields,
    make_answer,
    make_comment,
    make_follow_up,
    make_issue,
    posted_comments,
    stray_problems,
)

# Text made of the pieces that matter for Markdown, mentions and links.
PIECES = st.sampled_from(
    [
        # Mentions, references and commits.
        "@",
        "@octocat",
        "@org/team",
        "#",
        "#12",
        "GH-5",
        "owner/repo",
        "owner/repo#3",
        "owner/repo@0123abc",
        # Code, fences and what could split or end them.
        "`",
        "```",
        "````",
        "~~~",
        "\\",
        # Links, images, HTML and entities.
        "[",
        "]",
        "(",
        ")",
        "!",
        "<",
        ">",
        "<<b>b>",
        "<!--",
        "-->",
        "[x]: https://evil.example",
        "[x][x]",
        "&#64;",
        "&",
        ";",
        # Addresses, where they lead, and what GitHub leaves out at their end.
        "https://evil.example/x",
        "https://docs.example.com/page",
        f"https://github.com/{REPOSITORY}/blob/main/README.md",
        "https://github.com/other/repo/blob/main/x",
        "HTTPS://evil.example",
        "www.evil.example",
        "www.",
        "/../",
        "%2e%2e",
        "support@evil.example",
        "a@b.example",
        "'",
        ".",
        # Emphasis, which splits the text, and the blocks of lists, quotes,
        # headings and tables.
        "_",
        "*",
        "- ",
        "* ",
        "1. ",
        "> ",
        "\n#\n",
        "|",
        ":",
        "-",
        # Spaces, line breaks and characters that only some parsers take for one.
        "  ",
        "   ",
        "    ",
        "\n",
        " ",
        "\t",
        "\u00a0",
        "\r",
        "\0",
        "\x1d",
        "word",
    ]
)
TEXT = st.one_of(st.text(), st.lists(PIECES, max_size=40).map("".join))
NUMBERS = st.sets(st.integers(min_value=1, max_value=20), max_size=3)
# The number of examples of the profile, such as 100 in the CI: the cleaning is
# fast, so its properties try more.
EXAMPLES = hypothesis.settings().max_examples


def known_escapes(test):
    """The test, tried first with each text that once got past the cleaning."""
    for text, numbers in ESCAPES:
        test = hypothesis.example(text, numbers)(test)
    return test


@hypothesis.settings(
    max_examples=EXAMPLES * 6, suppress_health_check=[hypothesis.HealthCheck.too_slow]
)
@given(TEXT, NUMBERS)
@known_escapes
def test_sanitized_text_mentions_nobody_and_links_only_allowed_sites(text, numbers):
    output = assistant.sanitize(text, CONFIG, numbers)
    assert problems(output, numbers, CONFIG) == [], output


@hypothesis.settings(suppress_health_check=[hypothesis.HealthCheck.too_slow])
@given(TEXT, st.integers(min_value=1, max_value=300))
def test_a_line_of_the_engine_s_text_is_short_clean_and_one_line(text, limit):
    """For the log, the job summary and the rationales on the timeline."""
    line = assistant.plain(text, CONFIG, limit)
    assert len(line) <= limit
    assert not assistant.CONTROL.search(line) and line == line.strip()
    # Alone, as a rationale, and within a line, as in the log and the job summary.
    assert problems(line, set(), CONFIG) == [], line
    assert problems(f"- Reason: {line}", set(), CONFIG) == [], line


@hypothesis.settings(
    max_examples=EXAMPLES * 3, suppress_health_check=[hypothesis.HealthCheck.too_slow]
)
@given(TEXT, NUMBERS)
@known_escapes
def test_cleaned_text_stays_safe_wherever_the_comment_puts_it(text, numbers):
    """Under a heading, in a list, and on one line behind a number, where a fence
    would start no code block."""
    cleaned = assistant.sanitize(text, CONFIG, numbers)
    line = assistant.sanitize(assistant.one_line(text), CONFIG, numbers)
    for comment in (
        f"### Summary\n\n{cleaned}\n\n### Next steps",
        assistant.bullets([cleaned, cleaned]),
        f"- #7: {line}\n- #8: {line}",
    ):
        assert problems(comment, numbers | {7, 8}, CONFIG) == [], comment


# Files of the sample repository, and the anchors of headings in one of them, which
# the engine may name in its references.
SAMPLE_FILES = ["docs/troubleshooting.md", "./docs/troubleshooting.md", "src/app.py"]
SAMPLE_ANCHORS = ["no-realtime-updates", "#Setup"]


class Drawn:
    """The choices of an answer, drawn by Hypothesis for `answer_of`: texts of the
    pieces that matter, files and anchors that exist, issue numbers of issues that
    exist and of some that don't, and seldom a security report, whose comment shows
    none of the texts."""

    def __init__(self, data):
        self.data = data

    def text(self, key, limit):
        known = {"path": SAMPLE_FILES, "anchor": SAMPLE_ANCHORS}.get(key)
        texts = st.one_of(st.sampled_from(known), TEXT) if known else TEXT
        return self.data.draw(texts, label=key)[:limit]

    def count(self, key, most):
        return self.data.draw(st.integers(0, most), label=f"{key}, how many")

    def one_of(self, key, values):
        return self.data.draw(st.sampled_from(values), label=key)

    def number(self, key, least):
        return self.data.draw(st.integers(least, least + 23), label=key)

    def boolean(self, key):
        if key == "security_report":
            return self.data.draw(st.sampled_from([False] * 4 + [True]), label=key)
        return self.data.draw(st.booleans(), label=key)

    def present(self, key):
        return self.data.draw(st.booleans(), label=f"{key}, given")


@hypothesis.settings(
    suppress_health_check=[
        hypothesis.HealthCheck.too_slow,
        hypothesis.HealthCheck.data_too_large,
    ]
)
@given(st.sampled_from(["triage", "follow-up", "maintainer-question"]), st.data())
def test_a_whole_comment_mentions_nobody_and_links_only_allowed_places(
    sample_config, mode, data
):
    """Any answer that fits the schema, as the assistant posts it: every text where
    the comment puts it, next to the assistant's own words and the links that it
    checked. Only its own words may mention the reporter or the maintainer who
    asked, show HTML or link to the security policy or the question."""
    answer = answer_of(assistant.answer_schema(mode, sample_config), Drawn(data))
    bodies, numbers = posted_comments(sample_config, mode, answer)
    # How many answers the checks refuse, for --hypothesis-show-statistics.
    hypothesis.event("posted" if bodies else "refused")
    security = answer.get("security_report", False)
    author = "maintainer" if mode == "maintainer-question" else "reporter"
    for body in bodies:
        found = stray_problems(body, numbers, sample_config, author, security=security)
        assert found == [], body


def test_the_whole_comments_include_follow_ups():
    """The reporter answered the analysis's questions, so the follow-ups that the
    property test above and the fuzzer judge are posted, and are not nothing."""
    (body,), _ = posted_comments(CONFIG, "follow-up", make_follow_up())
    assert body.startswith("<!-- issue-assistant:follow-up lang=en questions=0")


def test_the_whole_comments_include_answers_to_questions():
    """A maintainer asked, so the answers that the property test above judges are
    posted."""
    answer = {"language": "de", "answer": "Weil.", "references": []}
    (body,), _ = posted_comments(CONFIG, "maintainer-question", answer)
    assert body.startswith("<!-- issue-assistant:answer lang=de to=2 engine=claude")


def test_only_the_comment_on_a_security_report_may_link_to_the_policy():
    answer = make_answer(areas=[], security_report=True)
    (body,), numbers = posted_comments(CONFIG, "triage", answer)
    assert stray_problems(body, numbers, CONFIG, security=True) == []
    assert stray_problems(body, numbers, CONFIG) == [f"link to {CONFIG.policy}"]


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "string", "minLength": 1, "maxLength": 3},
        {"type": ["string", "null"], "maxLength": 3},
        {"type": "number"},
        {
            "type": "object",
            "properties": {"n": {"type": "integer", "minimum": 1, "maximum": 9}},
            "required": ["n"],
        },
    ],
)
def test_answers_come_from_a_schema_only_if_every_keyword_is_followed(schema):
    """A keyword that answer_of left out would make answers that the checks refuse,
    and the property and the fuzzer would test nothing."""
    with pytest.raises(ValueError, match="answer_of can't build"):
        answer_of(schema, None)


@given(TEXT)
def test_any_answer_is_checked_or_refused(raw):
    for mode in assistant.MODES:
        try:
            answer = assistant.parse_answer(mode, raw, CONFIG)
        except assistant.AssistantError:
            continue
        assert assistant.validate(assistant.answer_schema(mode, CONFIG), answer) == []


JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(),
    lambda children: st.lists(children) | st.dictionaries(st.text(), children),
    max_leaves=20,
)


@given(JSON)
def test_the_schema_check_describes_every_value(value):
    for mode in assistant.MODES:
        problems = assistant.validate(assistant.answer_schema(mode, CONFIG), value)
        assert all(isinstance(problem, str) for problem in problems)
        if not problems:
            # What passes the check is posted, so it must survive the parser too.
            assistant.parse_answer(mode, json.dumps(value), CONFIG)


TOKENS = st.one_of(
    st.from_regex(r"ghp_[A-Za-z0-9]{36}", fullmatch=True),
    st.from_regex(r"github_pat_[A-Za-z0-9_]{60}", fullmatch=True),
    st.from_regex(r"sk-ant-[A-Za-z0-9_-]{30}", fullmatch=True),
    st.from_regex(r"AKIA[0-9A-Z]{16}", fullmatch=True),
)


@given(st.text(), TOKENS, st.text())
def test_a_token_anywhere_in_the_answer_is_refused(before, token, after):
    text = f"{before} {token} {after}"
    assert assistant.looks_secret(text)
    # Even in the name of a file of the repository.
    assert assistant.looks_secret(text, {token, f"docs/{token}.md"})


# Paths of folders and files with any names, long ones too.
PATHS = st.builds(
    lambda folders, extension: "/".join(folders) + extension,
    st.lists(st.from_regex(r"[\w+=-]{1,30}", fullmatch=True), min_size=1, max_size=5),
    st.sampled_from(["", ".py", ".tar.gz", "/"]),
)


@given(PATHS, st.sampled_from(["{}", "See ./{}.", "{}#L3", "`{}`, line 3"]))
def test_a_path_that_git_tracks_is_never_a_secret(path, sentence):
    """Unless it holds a known kind of token, which counts wherever it is."""
    hypothesis.assume(not assistant.SECRET.search(path))
    files = {path.rstrip("/") + ("/index.md" if path.endswith("/") else ""), "x.py"}
    assert not assistant.looks_secret(sentence.format(path), files)


# The values of `[\w.+/-]+` in ASCII, which the bot writes. A class of ASCII
# characters generates them far faster than a filter that drops every other letter.
ATTRIBUTE = st.from_regex(r"[A-Za-z0-9_.+/-]+", fullmatch=True).filter(
    lambda value: "--" not in value
)


@given(
    st.from_regex(r"[a-z-]+", fullmatch=True),
    st.dictionaries(st.from_regex(r"[a-z]+", fullmatch=True), ATTRIBUTE, max_size=3),
)
def test_markers_of_the_bot_read_back_as_written(kind, attributes):
    body = f"{assistant.render_marker(kind, **attributes)}\nText"
    comment = {"user": BOT_USER, "body": body}
    assert assistant.marker(comment) == (kind, attributes)
    assert assistant.marker({"user": {"login": "someone"}, "body": body}) is None


@given(st.text())
def test_issue_forms_and_languages_read_any_text(body):
    fields = assistant.form_fields(body)
    assert all(
        isinstance(key, str) and isinstance(value, str) for key, value in fields.items()
    )
    assert assistant.detect_language(body) in {"en", "de"}


# Lines of issue forms, and of what people write around them.
FORM_LINE = st.sampled_from(
    [
        "### Area",
        "###\tVersion\t",
        "###  ",
        "### ",
        "###",
        "### Größe",
        "### Area\r",
        "Live card",
        "_No response_",
        "",
        " ",
        "#### Sub",
        "###x",
        " ### y",
        "```",
        "text ### z",
    ]
)
FORM = st.one_of(
    st.text(),
    st.lists(FORM_LINE, max_size=12).map("\n".join),
    st.lists(FORM_LINE, max_size=12).map(lambda lines: "\n".join(lines) + "\n"),
)


def headings_have_labels(body):
    """Whether each heading has a label: the earlier reading took the next line as
    the label of a heading without one."""
    return all(heading[1].strip() for heading in assistant.FORM_HEADING.finditer(body))


@given(FORM)
def test_any_form_reads_as_it_always_did(body):
    hypothesis.assume(headings_have_labels(body))
    assert assistant.form_fields(body) == earlier_form_fields(body)


HTML_TEXT = st.lists(
    st.sampled_from(
        ["<", ">", "/", "!", "-", "a", "B", " ", "\n", "<!--", "-->", "<b>", "</i>"]
    ),
    max_size=40,
).map("".join)


def shown(text):
    """What GitHub shows of a text: all of it, but nothing after a line that starts
    with a `<!--` that no `-->` follows. The earlier pattern left such a comment as
    text; now it hides the rest, as GitHub does."""
    for line in re.finditer(r"^ {0,3}<!--", text, re.MULTILINE):
        if "-->" not in text[line.start() :]:
            return text[: line.start()]
    return text


@given(HTML_TEXT)
def test_html_is_removed_as_it_always_was(text):
    assert assistant.strip_html(text) == EARLIER_HTML.sub("", shown(text))


# Pieces of a text that would pass for the structure of issue.md: a heading of the
# script, a fence, and what could hide or split one.
STRUCTURE = st.lists(
    st.sampled_from(
        [
            "`",
            "```",
            "````",
            "~~~",
            "## Comment 2 by @owner (maintainer), 2026-10-07",
            "# Issue #3: x",
            "## Text of the issue",
            "\n",
            "\r",
            " ",
            "   ",
            "\t",
            "> ",
            "- ",
            "<!--",
            "-->",
            "-",
            ">",
            "<b>",
            "\u200b",
            "\u202e",
            "\U000e0041",
            "\ufe0f",
            "x",
        ]
    ),
    max_size=40,
).map("".join)


@hypothesis.settings(suppress_health_check=[hypothesis.HealthCheck.too_slow])
@given(st.one_of(st.text(), STRUCTURE), st.one_of(st.text(), STRUCTURE))
def test_no_text_forms_the_structure_of_issue_md_or_hides_in_it(body, comment):
    """Only the script writes the headings that say who wrote what, and the engine
    reads no character that GitHub doesn't show."""
    issue = make_issue(body=body)
    rendered = assistant.render_issue(issue, [make_comment(2, "owner", comment, 0.5)])
    tokens = PARSER.parse(rendered)
    headings = [
        tokens[index + 1].content
        for index, token in enumerate(tokens)
        if token.type == "heading_open"
    ]
    assert headings == [
        "Issue #12: Issue 12",
        "Text of the issue",
        f"Comment 1 by @owner, {at(0.5)[:10]}",
    ]
    assert not assistant.INVISIBLE.search(rendered)


def subsequence(part, whole):
    """Whether part is whole with characters left out, and nothing added."""
    rest = iter(whole)
    return all(char in rest for char in part)


@hypothesis.example("<!-- -\u200b->\nhidden")
@hypothesis.example("<!-- x -\u200b-> hidden -->")
@given(st.one_of(st.text(), STRUCTURE))
def test_the_engine_reads_no_text_that_github_hides(text):
    """What a fence of issue.md holds is what GitHub shows of the text, without its
    invisible characters: taken out before the HTML, one could end an HTML comment
    that GitHub never ends, and show what it hides."""
    rendered = assistant.rendered_text(text, assistant.Visible())
    marks, _, rest = rendered.partition("\n")
    inner = rest[: rest.rfind("\n" + marks)]
    assert subsequence(inner, assistant.strip_html(text))
    assert not assistant.INVISIBLE.search(inner)
