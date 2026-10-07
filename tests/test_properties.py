"""Properties of the code that turns the engine's text into a comment, for any input.

The engine's answer may be steered by an issue, so these checks don't trust examples:
Hypothesis generates the text, including the characters that Markdown treats
specially, and looks for an output that would mention someone, link elsewhere or
break the checks.
"""

import json

import hypothesis
from hypothesis import given
from hypothesis import strategies as st

import issue_assistant as assistant
from oracle import CONFIG, problems
from support import BOT_USER, REPOSITORY

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


ATTRIBUTE = st.from_regex(r"[\w.+/-]+", fullmatch=True).filter(
    lambda value: "--" not in value and value.isascii()
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
