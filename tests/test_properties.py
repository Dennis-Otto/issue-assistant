"""Properties of the code that turns the engine's text into a comment, for any input.

The engine's answer may be steered by an issue, so these checks don't trust examples:
Hypothesis generates the text, including the characters that Markdown treats
specially, and looks for an output that would mention someone, link elsewhere or
break the checks. The reading of issues, which anyone writes, is checked the same
way against how it read them before.
"""

import json
import re

import hypothesis
from hypothesis import given
from hypothesis import strategies as st

import issue_assistant as assistant
from oracle import CONFIG, problems
from support import BOT_USER, EARLIER_HTML, REPOSITORY, earlier_form_fields

# Text made of the pieces that matter for Markdown, mentions and links.
PIECES = st.sampled_from(
    [
        "@",
        "@octocat",
        "@org/team",
        "`",
        "```",
        "~~~",
        "\\",
        "#",
        "#12",
        "owner/repo#3",
        "[",
        "]",
        "(",
        ")",
        "!",
        "<",
        ">",
        "<!--",
        "-->",
        "https://evil.example/x",
        "https://docs.example.com/page",
        f"https://github.com/{REPOSITORY}/blob/main/README.md",
        "https://github.com/other/repo/blob/main/x",
        "www.evil.example",
        "[x]: https://evil.example",
        "[x][x]",
        "&#64;",
        "&",
        "GH-5",
        "    ",
        "\n",
        " ",
        "\r",
        "\0",
        "word",
    ]
)
TEXT = st.one_of(st.text(), st.lists(PIECES, max_size=40).map("".join))
NUMBERS = st.sets(st.integers(min_value=1, max_value=20), max_size=3)


@hypothesis.settings(
    max_examples=600, suppress_health_check=[hypothesis.HealthCheck.too_slow]
)
@given(TEXT, NUMBERS)
def test_sanitized_text_mentions_nobody_and_links_only_allowed_sites(text, numbers):
    output = assistant.sanitize(text, CONFIG, numbers)
    assert problems(output, numbers) == [], output


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
    assert assistant.looks_secret(f"{before} {token} {after}")


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
