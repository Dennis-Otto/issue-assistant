"""Properties of the code that turns the engine's text into a comment, for any input.

The engine's answer may be steered by an issue, so these checks don't trust examples:
Hypothesis generates the text, including the characters that Markdown treats
specially, and whole answers from their schema, and looks for a comment that would
mention someone, link elsewhere or break the checks, as the oracle of
tests/oracle.py sees it. The reading of issues, which anyone writes, is checked the
same way against how it read them before.
"""

import collections
import dataclasses
import itertools
import json
import re
import unicodedata
from unittest import mock

import hypothesis
import pytest
from hypothesis import given
from hypothesis import strategies as st

import issue_assistant as assistant
from issue_assistant import markup
from oracle import (
    PARSER,
    browser_text,
    code_text,
    headings,
    hides_the_rest,
    problems,
)
from support import (
    BOT_USER,
    BOX,
    CONFIG,
    EARLIER_HTML,
    ESCAPES,
    FORK_COMMIT,
    REPOSITORY,
    answer_of,
    at,
    earlier_form_fields,
    earlier_form_fields_outside_code,
    make_answer,
    make_comment,
    make_discussion_answer,
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
        # A commit that the checkout doesn't have, such as one of a fork.
        f"https://github.com/{REPOSITORY}/blob/{FORK_COMMIT}/install.sh",
        f"https://github.com/{REPOSITORY}/tree/{FORK_COMMIT}",
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
        ">#",
        "\n    ",
        "=",
        "\n===\n",
        "\n---\n",
        "|",
        ":",
        "-",
        # Math and the code that GitHub draws or reads as math.
        "$",
        "$$",
        "\\$",
        "&#36;",
        "x$",
        "$x",
        "```math\n",
        "~~~ math\n",
        "```mermaid\n",
        "```&#109;ermaid\n",
        "%% ",
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
    # Only the assistant writes the headings of a comment.
    assert headings(output) == [], output


# Text with references to the issues of related repositories, of which owner/server#3,
# #4 and owner/app_#5 exist, and the characters that could join them to something
# else; a name that ends in `_` can end emphasis.
ELSEWHERE = st.lists(
    st.one_of(
        PIECES,
        st.sampled_from(
            [
                "owner/server#3",
                "Owner/Server#03",
                "owner/server#4",
                "owner/server_#3",
                "x_owner/server#3",
                "owner/servers#3",
                "owner/app_#5",
                "owner/app_#6",
                "owner/",
                "server#3",
                "!",
                "_",
                "*",
            ]
        ),
    ),
    max_size=30,
).map("".join)
CHECKED_ELSEWHERE = st.sets(
    st.sampled_from(["owner/server#3", "owner/server#4", "owner/app_#5"])
)


@hypothesis.settings(
    max_examples=EXAMPLES * 3, suppress_health_check=[hypothesis.HealthCheck.too_slow]
)
@given(ELSEWHERE, NUMBERS, CHECKED_ELSEWHERE)
def test_only_checked_issues_of_related_repositories_become_links(
    text, numbers, elsewhere
):
    """Wherever the comment puts the text, as in
    test_cleaned_text_stays_safe_wherever_the_comment_puts_it."""
    checked = numbers | elsewhere
    cleaned = assistant.sanitize(text, CONFIG, checked)
    line = assistant.sanitize(assistant.one_line(text), CONFIG, checked)
    for comment in (
        cleaned,
        f"### Summary\n\n{cleaned}\n\n### Next steps",
        assistant.bullets([cleaned, cleaned]),
        f"- #7: {line}\n- #8: {line}",
    ):
        assert problems(comment, checked | {7, 8}, CONFIG) == [], comment


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
    for comment, own in (
        (f"### Summary\n\n{cleaned}\n\n### Next steps", ["Summary", "Next steps"]),
        (assistant.bullets([cleaned, cleaned]), []),
        (f"- #7: {line}\n- #8: {line}", []),
    ):
        assert problems(comment, numbers | {7, 8}, CONFIG) == [], comment
        assert headings(comment) == own, comment


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
@given(
    st.sampled_from(
        ["triage", "follow-up", "maintainer-question", "discussion-answer"]
    ),
    st.data(),
)
def test_a_whole_comment_mentions_nobody_and_links_only_allowed_places(
    sample_config, mode, data
):
    """Any answer that fits the schema, as the assistant posts it: every text where
    the comment puts it, next to the assistant's own words and the links that it
    checked. Only its own words may mention the reporter or the maintainer who
    asked, show HTML or link to the security policy, the question or the project."""
    answer = answer_of(assistant.answer_schema(mode, sample_config), Drawn(data))
    bodies, numbers = posted_comments(sample_config, mode, answer)
    # How many answers the checks refuse, for --hypothesis-show-statistics.
    hypothesis.event("posted" if bodies else "refused")
    security = answer.get("security_report", False)
    author = "maintainer" if mode == "maintainer-question" else "reporter"
    for body in bodies:
        found = stray_problems(body, numbers, sample_config, author, security=security)
        assert found == [], body


@hypothesis.settings(
    suppress_health_check=[
        hypothesis.HealthCheck.too_slow,
        hypothesis.HealthCheck.data_too_large,
    ]
)
@given(st.data())
def test_a_whole_analysis_with_related_repositories_links_only_what_it_checked(
    sample_config, data
):
    """As above, for a first analysis in a repository with a public related
    repository, owner/server, and a private one, whose issues it may name."""
    config = dataclasses.replace(
        sample_config, related_repositories=("owner/server", "owner/private")
    )
    answer = answer_of(assistant.answer_schema("triage", config), Drawn(data))
    bodies, numbers = posted_comments(config, "triage", answer)
    hypothesis.event("posted" if bodies else "refused")
    security = answer.get("security_report", False)
    for body in bodies:
        assert stray_problems(body, numbers, config, security=security) == [], body


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


def test_the_whole_comments_include_first_answers_to_discussions():
    """The discussion is a question in Q&A, so the answers that the property test
    above and the fuzzer judge are posted, and are not nothing."""
    answer = make_discussion_answer(kind="bug", missing_information=["Which?"])
    (body,), numbers = posted_comments(CONFIG, "discussion-answer", answer)
    assert body.startswith(
        "<!-- issue-assistant:discussion-answer lang=en type=bug questions=1"
    )
    assert stray_problems(body, numbers, CONFIG) == []


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
    st.from_regex(r"AIza[0-9A-Za-z_-]{35}", fullmatch=True),
    st.from_regex(r"glpat-[A-Za-z0-9_-]{20}", fullmatch=True),
    st.from_regex(r"[sr]k_live_[A-Za-z0-9]{24}", fullmatch=True),
    st.from_regex(r"hf_[A-Za-z0-9]{34}", fullmatch=True),
    st.from_regex(r"[0-9]{10}:[A-Za-z0-9_-]{35}", fullmatch=True),
)


@given(st.text(), TOKENS, st.text())
def test_a_token_anywhere_in_the_answer_is_refused(before, token, after):
    text = f"{before} {token} {after}"
    assert assistant.looks_secret(text)
    # Even in the name of a file of the repository.
    assert assistant.looks_secret(text, {token, f"docs/{token}.md"})


# Hex as long as a commit, or longer, in either case.
HEX = st.one_of(
    st.from_regex(r"[0-9a-f]{40,70}", fullmatch=True),
    st.from_regex(r"[0-9A-F]{40,70}", fullmatch=True),
)
# Names that say that a value is a credential, and what stands between the two.
CREDENTIALS = st.sampled_from(
    [
        "token",
        "Token",
        "GITHUB_TOKEN",
        "access_token",
        "x-auth-token",
        "secret",
        "CLIENT_SECRET",
        "secrets",
        "password",
        "passwd",
        "passphrase",
        "key",
        "api_key",
        "apikey",
        "X-Api-Key",
        "Authorization",
        "auth",
        "credentials",
        # Longer names that go on past the name of the credential.
        "secret_key_base",
        "SECRET_KEY_BASE",
        "api-key-id",
        "apiKeyId",
        "passwordHash",
        # A plural that goes on as well.
        "passwords_file",
        "CREDENTIALS_JSON",
        "SECRETS_BASE",
        "apiKeysId",
        # A name that ends in a digit, as one of several keys does.
        "key1",
        "token2",
        "API_KEY_2",
        "password12",
        "secrets3",
        "apiKey2Id",
        "access_token2_old",
    ]
)
BETWEEN = st.sampled_from(
    [
        ": ",
        ":",
        "=",
        " = ",
        " ",
        "\t",
        ": token ",
        ": Bearer ",
        '": "',
        "='",
        "']='",
        # A list, a call and short words before the value.
        '": [ "',
        '":["',
        "=('",
        " is ",
        " was ",
        " is set to ",
        " is: ",
    ]
)


@given(st.text(), CREDENTIALS, BETWEEN, HEX, st.text())
def test_hex_after_the_name_of_a_credential_is_refused(
    before, name, between, value, after
):
    """Such as a token of GitHub's old kind, which is as long as a commit."""
    text = f"{before} {name}{between}{value} {after}"
    assert assistant.looks_secret(text)
    # Even when the hex is the name of a file of the repository.
    assert assistant.looks_secret(text, {value, f"docs/{value}.md"})


@given(
    st.from_regex(r"[0-9a-f]{40}", fullmatch=True),
    st.sampled_from(
        [
            "{}",
            "commit {}",
            "Fixed in commit {}.",
            "The token was rotated in commit {}.",
            "https://github.com/owner/project/commit/{}",
            "https://github.com/owner/secret-key/blob/{}/src/token.py#L3",
            "See `{}` for the key.",
            "The key is in commit {}.",
            "The secret_key_base was moved in commit {}.",
            "The keyboard works since {}.",
            "The keyboard1 works since {}.",
            "The key2 is in commit {}.",
            "sha1 {}",
            "sha1: {}",
            "https://github.com/owner/key1/commit/{}",
            "Our tokenizer {} is faster.",
            "[{}]",
        ]
    ),
)
def test_a_commit_is_no_secret(commit, sentence):
    """Alone, in a link or after the word commit, even where a credential is named."""
    assert not assistant.looks_secret(sentence.format(commit))


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
    assert assistant.detect_language(body) in assistant.LANGUAGES


# The scripts of their own: each letter that only one language of the catalogs
# writes counts for that language, whatever the text around it.


def letters_named(*prefixes):
    """The letters of the Basic Multilingual Plane whose Unicode names start so."""
    return [
        char
        for char in map(chr, range(0x10000))
        if unicodedata.name(char, "").startswith(prefixes)
    ]


KANA = letters_named("HIRAGANA LETTER", "KATAKANA LETTER", "HALFWIDTH KATAKANA LETTER")
HAN = letters_named("CJK UNIFIED IDEOGRAPH", "CJK COMPATIBILITY IDEOGRAPH-")
HANGUL = letters_named("HANGUL SYLLABLE")
SCRIPTS = {
    "de": list("ßẞ"),
    "es": list("ñÑ¿¡"),
    "fr": list("œŒ"),
    "it": list("ìòÌÒ"),
    "ko": HANGUL,
    "pt": list("ãõÃÕ"),
    "ru": list("ёъыэЁЪЫЭ"),
    "uk": list("ґєіїҐЄІЇ"),
    "zh": HAN,
}


def test_each_letter_of_a_script_of_its_own_counts_for_one_language():
    patterns = {
        **assistant.OWN_LETTERS,
        "kana": assistant.KANA,
        "han": assistant.HAN,
    }
    for char in map(chr, range(0x10000)):
        owners = [name for name, pattern in patterns.items() if pattern.match(char)]
        assert len(owners) <= 1, (char, owners)
    for letters, name in ((KANA, "kana"), (HAN, "han"), (HANGUL, "ko")):
        assert all(patterns[name].match(char) for char in letters), name
    # Each language with letters of its own has a catalog to answer in.
    assert {*assistant.OWN_LETTERS, "ja", "zh"} <= set(assistant.LOCALES)


@given(
    st.sampled_from(sorted(SCRIPTS)).flatmap(
        lambda code: st.tuples(
            st.just(code), st.text(st.sampled_from(SCRIPTS[code]), min_size=2)
        )
    )
)
def test_a_text_in_a_script_of_its_own_is_in_its_language(case):
    code, text = case
    assert assistant.detect_language(text) == code


@given(
    st.text(st.sampled_from(KANA), min_size=1, max_size=20).flatmap(
        lambda kana: st.tuples(
            st.just(kana),
            st.text(st.sampled_from(HAN), max_size=10 * len(kana)),
            st.randoms(use_true_random=False),
        )
    )
)
def test_kana_make_the_chinese_characters_around_them_japanese(case):
    kana, han, random = case
    letters = list(kana + han)
    random.shuffle(letters)
    text = "".join(letters)
    assert assistant.detect_language(text) == ("ja" if len(text) >= 2 else "en")


@given(st.text(st.sampled_from(HAN), min_size=11, max_size=40))
def test_a_stray_kana_makes_no_japanese_of_chinese(han):
    assert assistant.detect_language(han[:5] + "の" + han[5:]) == "zh"


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


@given(FORM)
def test_an_excerpt_reads_a_form_as_before_but_for_headings_in_code(body):
    # Only a heading in fenced code reads differently for an excerpt: as text.
    hypothesis.assume(headings_have_labels(body) and "\0" not in body)
    fields = dict(assistant.form_parts(body, outside_code=True)[1])
    assert fields == earlier_form_fields_outside_code(body)


@given(FORM)
def test_no_text_before_a_ticked_box_hides_it(body):
    assert assistant.box_ticked(body + BOX.format("X"), CONFIG)


# Texts of what the assistant decides from: names of errors, words of a language,
# answers of the area and of the box, closing keywords with words against them, and
# the HTML, the link reference definitions and the code that GitHub hides or shows.
DECIDED = st.lists(
    st.sampled_from(
        [
            "It fails with ImportError.",
            "Die Karte bleibt leer, und ich weiß nicht, warum das so ist.",
            "Fixes #12",
            "This does not close #12",
            "### Area\n\n",
            "Live card",
            "X01 game, Other",
            "_No response_",
            "### Automated analysis\n\n",
            "- [X] Keep the AI out.",
            "- [ ] Keep the AI out.",
            "<!--",
            "-->",
            "<!-- ZebraError -->",
            "<?x QuokkaError ?>",
            '<span data-x="YakError">',
            "</span>",
            "[a]: https://example.com/GnuError",
            "<details>",
            "</details>",
            '<relative-time datetime="2020-01-01T00:00:00Z">',
            "OwlError",
            "</relative-time>",
            "```",
            "`TypeError`",
            "> ",
            "- ",
            "\n",
            "\n\n",
            " ",
        ]
    ),
    max_size=30,
).map("".join)
AREAS = dataclasses.replace(
    CONFIG,
    labels=(
        assistant.Label("area: cards", "c5def5", "Cards", "area", ("Live card",)),
        assistant.Label("area: games", "c5def5", "Games", "area", ("X01 game",)),
    ),
)


def decisions(body):
    """What the assistant decides from a text of the reporter, as the body of an
    issue and as a comment of its reporter."""
    issue = make_issue(body=body)
    return (
        assistant.search_terms(issue),
        assistant.issue_language(issue, [make_comment(1, "reporter", body, 0)]),
        assistant.form_labels(issue, AREAS),
        assistant.denies_closing(body, REPOSITORY, 12),
    )


@hypothesis.example("Fixes #12\n\n<!-- This does not close #12 -->")
@hypothesis.example("### Area\n\nLive card\n<!--\n### Area\n\nX01 game\n\n### x\n-->")
@given(DECIDED)
def test_what_the_assistant_decides_it_decides_from_what_github_shows(body):
    """The search, the language, the area and the denial of a fix are the same for
    a text and for the text that GitHub shows of it; the box of the opt-out is
    ticked whenever it is ticked in either of them.

    Only where GitHub shows that text as it is: what is left of a text can read as
    other Markdown, as a `<!--` that a tag before it kept inside a paragraph, which
    starts a block on a row of its own."""
    shown = assistant.as_shown(body)
    hypothesis.assume(assistant.as_shown(shown) == shown)
    assert decisions(body) == decisions(shown)
    ticked = [
        assistant.TICKED_BOX.search(answer) is not None
        for label, answer in assistant.form_parts(body)[1]
        if label == CONFIG.opt_out_field
    ]
    if any(ticked) or assistant.box_ticked(shown, CONFIG):
        assert assistant.box_ticked(body, CONFIG)


HTML_TEXT = st.lists(
    st.sampled_from(
        ["<", ">", "/", "!", "-", "a", "B", " ", "\n", "<!--", "-->", "<b>", "</i>"]
    ),
    max_size=40,
).map("".join)


def shown(text):
    """What GitHub shows of a text: all of it up to where an HTML block holds a
    `<!--` that no `-->` follows, without the tags that run past their rows. The
    earlier pattern left such a comment and such a tag as text; now both go, as
    GitHub hides them. Where they go,
    test_the_engine_reads_nothing_that_an_html_block_hides holds against a CommonMark
    parser and a browser's reading of its HTML."""
    spans, _, end = assistant.html_parts(text)
    return assistant.without(text, spans, 0, end)


# The earlier pattern, and an instruction, a declaration or a section of CDATA that
# ends on its row at its first `>`, which the earlier pattern left as text, and
# which GitHub hides wherever it stands outside code.
EARLIER_HTML_AND_MARKUP = re.compile(
    rf"{EARLIER_HTML.pattern}|<(?:\?[^<>\r\n]*\?|![A-Za-z][^<>\r\n]*"
    r"|!\[CDATA\[[^<>\r\n]*\]\])>",
    re.DOTALL,
)


@hypothesis.example("<!a>")
@given(HTML_TEXT)
def test_html_is_removed_as_it_always_was(text):
    assert assistant.strip_html(text) == EARLIER_HTML_AND_MARKUP.sub("", shown(text))


# Rows of HTML blocks and of the tags that run over them, at the top of the text, in
# quotes and in list items, with what starts a value or ends a tag, and with each
# kind of line break.
TAG_ROWS = st.tuples(
    st.lists(
        st.sampled_from(
            [
                "<div",
                "<DIV a",
                "</p",
                "<td\tb",
                "<pre>",
                "<a>",
                "<!--",
                "-->",
                "<?",
                "<!X",
                "```",
                "x",
                "a b",
                "",
                " ",
                "\t",
                ">",
                "> ",
                "> <div",
                "> x",
                ">> x",
                "> > <p",
                "- <div",
                "  x",
                "    <div",
                "=",
                "a=",
                "'",
                '"',
                "b c='d'",
                "x >",
            ]
        ),
        max_size=30,
    ),
    st.sampled_from(["\n", "\r\n", "\r"]),
).map(lambda rows: rows[1].join(rows[0]))


@hypothesis.example("<div\nx\n\n<DIV a\n \n\t\n<td\tb\n\n<div\n<a>\n\n</p\nb c='d'")
@hypothesis.example("<div\r\n\r\n<p\r\nx\r\n\r\n> <div\r\n\r\n<div a=")
@hypothesis.example("> <div\n> x\n>> x\n> > <p\n\n> <div\n> a b\n> x >\nx")
@hypothesis.example("> > <p\n> x\n>> a b\n> a=\n> > <p\n>\n> x")
@hypothesis.example("<div\nx\n\n<div\ny\n\nz")
@hypothesis.example("> <div\n> a=\n> 'x\n> y\nz")
@given(TAG_ROWS)
def test_the_rows_read_in_bulk_read_as_one_at_a_time(text):
    """The searches that read many rows of a tag at once find what the reading of a
    row at a time finds, which reads them where the searches match nothing."""
    bulk = assistant.html_parts(text)
    nothing = re.compile("")
    with (
        mock.patch.object(markup, "TAG_BLOCKS", nothing),
        mock.patch.object(markup, "inert_rows", lambda shallow, depth: nothing),
    ):
        assert assistant.html_parts(text) == bulk


# Rows of HTML blocks, and what passes for one: a footnote may hold a `<` in its label
# before the tag of its row.
BLOCK_ROWS = st.one_of(
    TAG_ROWS,
    HTML_TEXT,
    st.lists(
        st.sampled_from(["[^<a]: <div", "[^a]: ", "- ", "> ", "  ", "\t", "<", "x"]),
        max_size=12,
    ).map("".join),
)


@hypothesis.example("[^<a]: <div\n\n> - <p\n<")
@given(BLOCK_ROWS)
def test_a_row_without_a_tag_start_starts_no_block_as_before(text):
    """HTML_ROW passes over a row without a `<` at once; without that look ahead,
    it finds the same rows, with the same marks and kinds."""
    pattern = markup.HTML_ROW.pattern
    assert markup.HOLDS_A_TAG_START in pattern
    earlier = re.compile(pattern.replace(markup.HOLDS_A_TAG_START, "", 1))

    def rows(found):
        return [(row.span(), row.span("marks"), row.lastgroup) for row in found]

    assert rows(markup.HTML_ROW.finditer(text)) == rows(earlier.finditer(text))


class EarlierBlocks:
    """Blocks.holding as it read up to 2026-11-21: a block made for each row that may
    start one, of which the latest of each kind is kept until it ends."""

    def __init__(self, text, fenced):
        rows = markup.HtmlRows(text)
        self.found, self.lists = rows.found(), rows.lists()
        self.blocks = markup.Blocks(text, rows)
        self.fences = markup.Fences(text, rows) if fenced else None
        self.index, self.latest = 0, {}

    def holding(self, position):
        while (
            self.index < len(self.found) and self.found[self.index].start() <= position
        ):
            row = self.found[self.index]
            self.index += 1
            marks = row["marks"]
            if (row.start() >= self.lists or not marks or not markup.code(marks)) and (
                self.fences is None or not self.fences.holds(row.start())
            ):
                kind = row.lastgroup
                key = (
                    kind
                    if kind == "comment" or kind in markup.HTML_ENDS
                    else min(marks.count(">"), len(markup.BLANK_ROWS) - 1)
                )
                self.latest[key] = markup.Block(row, self.latest.get(key))
        for key, block in list(self.latest.items()):
            if self.blocks.end(block.row) <= position:
                del self.latest[key]
        return list(self.latest.values())


def chains(blocks):
    """Each block as the start of its row and of the rows of the blocks before it."""
    found = []
    for block in blocks:
        starts = []
        while block is not None:
            starts.append(block.row.start())
            block = block.earlier
        found.append(starts)
    return found


@hypothesis.example(
    ("<div\n<div\n\n<a>\n> <p\n\n<!--\n<p\n\n<?", [0, 0, 5, 30, 31]), False
)
@hypothesis.example(("```\n<div\n```\n<div\n<p\n\n<a>", [4, 13, 18, 25]), True)
@given(
    BLOCK_ROWS.flatmap(
        lambda text: st.tuples(
            st.just(text), st.lists(st.integers(0, len(text)), max_size=8).map(sorted)
        )
    ),
    st.booleans(),
)
def test_the_blocks_that_hold_a_position_are_as_before(case, fenced):
    """Blocks.holding, which makes no block of a kind whose last block has ended,
    finds the same blocks, each within the same blocks before it, in the same order,
    as when it made a block for each row."""
    text, positions = case
    rows = markup.HtmlRows(text)
    blocks = markup.Blocks(text, rows, markup.Fences(text, rows) if fenced else None)
    earlier = EarlierBlocks(text, fenced)
    for position in positions:
        assert chains(blocks.holding(position)) == chains(earlier.holding(position))


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


# Text made of the pieces of link reference definitions and footnotes, and of the
# blocks around them.
DEFINITIONS = st.lists(
    st.sampled_from(
        [
            "[x]:",
            "[^x]:",
            "[^x]",
            "[",
            "]",
            "\\",
            "#",
            "/url",
            "<>",
            '"t"',
            "'",
            '"',
            "(",
            ")",
            "\n",
            "\n\n",
            "\r\n",
            " ",
            "    ",
            "\t",
            "> ",
            "- ",
            "1. ",
            "2) ",
            "# ",
            "===",
            "```",
            "~~~",
            "<!--",
            "-->",
            "<div>",
            "\u200b",
            "x",
        ]
    ),
    max_size=40,
).map("".join)


@hypothesis.settings(suppress_health_check=[hypothesis.HealthCheck.too_slow])
@hypothesis.example('[x]: <> "hidden"')
@hypothesis.example(" ```\n```\n[x]: # (hidden)")
@hypothesis.example('[x]: #\n"hidden\n2. hidden"')
@hypothesis.example('x\n\n[a]: <> "[^x]"\n\n[^x]: hidden')
# What the long search found (2026-11-20): what GitHub shows as a heading or as
# text, where markdown-it reads a definition; texts on which markdown-it-py 4.3.0
# raises an IndexError; definitions over a lazy line, after indented code and
# after HTML in a quote, which GitHub hides; footnotes whose only reference stands
# in code or HTML that the cleaning can't follow, which GitHub hides; and one with
# a reference that markdown-it leaves as text.
@hypothesis.example("[^0]:0\n>~~~[^0]")
@hypothesis.example("[^0]:0\n<A>~~~[^0]")
@hypothesis.example("[^0]:-->0\n```\n[^0]")
@hypothesis.example("><!A[^0]\n[^0]:0")
@hypothesis.example("[^0]:0\n\n[^0][0]")
@hypothesis.example("[0]:\n=")
@hypothesis.example('[x]:\n=\n"hidden"')
@hypothesis.example("[0]:\n--")
@hypothesis.example('[0]: /u "a\n=\nb"')
@hypothesis.example("[a\nb]:\n=")
@hypothesis.example("[0]:\\\t0")
@hypothesis.example(">- <!A\n>")
@hypothesis.example("> - <!--\n>")
@hypothesis.example('> [0]:\n=\n"hidden"')
@hypothesis.example('> [0]:\n--\n"hidden"')
@hypothesis.example('- [0]:\n=\n"hidden"')
@hypothesis.example('    >0\n[0]: /u "hidden words"\n\n[0]')
@hypothesis.example('><!A\n[0]: /u "hidden words"\n\n[0]')
@given(st.one_of(st.text(), STRUCTURE, DEFINITIONS))
def test_a_commonmark_parser_finds_no_definition_in_what_the_engine_reads(text):
    """What is left of a text after the definitions went holds none, as a CommonMark
    parser reads it, and only notes take their places. The parser reads a footnote
    as a definition: one that a reference shows stays. The words of a fence that
    GitHub doesn't show go as well, with a note of their own."""
    shown, removed = assistant.shown_text(text)
    environment = {}
    tokens = PARSER.parse(shown, environment)
    labels = environment.get("references", {})
    assert not [label for label in labels if not label.startswith("^")]
    # Each footnote that stays has a reference that the parser reads as a link.
    assert {
        label for label in labels if label[1:].isalnum() and label[1:].isascii()
    } <= set(references(tokens))
    notes = (assistant.DEFINITION_NOTE, assistant.FOOTNOTE_NOTE)
    assert sum(shown.count(note) for note in notes) >= removed
    for note in (*notes, assistant.INFO_NOTE):
        shown = shown.replace(note, "")
    assert subsequence(shown, assistant.strip_html(text))


FOOTNOTE_IN_TEXT = re.compile(r"\[\^[^\]\s]+\]")


def references(tokens):
    """The text of each link and image, as the parser writes the label of one, and
    each footnote reference that it leaves as text: GitHub shows `[^1][x]` as a
    reference to the footnote when nothing defines `x`. An escape is a token of its
    own, so a bracket in a text token is none."""
    for token in tokens:
        text = None
        for child in token.children or ():
            if child.type == "text" and text is None:
                for match in FOOTNOTE_IN_TEXT.finditer(child.content):
                    yield match[0][1:-1].upper()
            if child.type == "link_open":
                text = ""
            elif child.type == "link_close":
                yield " ".join(text.split()).upper()
                text = None
            elif text is not None:
                text += child.content
            elif child.type == "image":
                yield " ".join(child.content.split()).upper()


@hypothesis.example("<!-- -\u200b->\nhidden")
@hypothesis.example("<!-- x -\u200b-> hidden -->")
@given(st.one_of(st.text(), STRUCTURE, DEFINITIONS))
def test_the_engine_reads_no_text_that_github_hides(text):
    """What a fence of issue.md holds is what GitHub shows of the text, without its
    invisible characters: taken out before the HTML, one could end an HTML comment
    that GitHub never ends, and show what it hides."""
    rendered = assistant.rendered_text(text, assistant.Visible())
    marks, _, rest = rendered.partition("\n")
    inner = rest[: rest.rfind("\n" + marks)]
    assert subsequence(inner, assistant.shown_text(text)[0])
    assert not assistant.INVISIBLE.search(inner)


# Text made of the references of characters that show nothing and of others, and of
# the pieces of the code, the HTML and the blocks around them, in which GitHub reads
# them or shows them as they are written.
INVISIBLE_REFERENCES = (
    "&#xE0041;",
    "&#917569;",
    "&#x200B",
    "&ZeroWidthSpace;",
    "&zwj;",
    "&shy",
)
REFERENCES = st.lists(
    st.sampled_from(
        [
            *INVISIBLE_REFERENCES,
            "&it;",
            "&amp;",
            "&#35;",
            "&#96;",
            "`",
            "``",
            "```",
            "~~~",
            "<div>",
            "<a ",
            ">",
            "\\",
            "|",
            "- ",
            "> ",
            "   ",
            "\n",
            "\r\n",
            "x",
        ]
    ),
    max_size=40,
).map("".join)


@hypothesis.example("` x\ny `&#x200B;` z")
@hypothesis.example("  ```\n```\n&#x200B;\n```")
@hypothesis.example("<div>\n```\nx&#xE0041\n```")
@hypothesis.example("\\&#xE0041;`&#xE0041;`")
@given(REFERENCES)
def test_the_engine_reads_no_reference_that_github_reads_as_nothing(text):
    """Each reference of a character that shows nothing that the engine reads is
    one that a CommonMark parser reads as code in the text, where GitHub shows it as
    it is written: the cleaning takes out every other, and keeps none that the
    parser doesn't, also where it can't tell."""
    rendered = assistant.rendered_text(text, assistant.Visible())
    marks, _, rest = rendered.partition("\n")
    inner = rest[: rest.rfind("\n" + marks)]
    code = code_text(text)
    for reference in INVISIBLE_REFERENCES:
        assert inner.count(reference) <= code.count(reference), reference


# Text made of the pieces of HTML blocks, of the quotes and list items that may hold
# them, and of what keeps a row from starting one.
HTML_BLOCKS = st.lists(
    st.sampled_from(
        [
            "<!--",
            "-->",
            "<div>",
            "</p>",
            "<pre>",
            "</pre>",
            "<?",
            "?>",
            "<!X",
            "<![CDATA[",
            "]]>",
            "<b>",
            "<x-y>",
            "<span>",
            ">",
            "> ",
            "- ",
            "* ",
            "1. ",
            "2) ",
            "[ ] ",
            "  ",
            "    ",
            "\t",
            " ",
            "\n",
            "\n\n",
            "x",
            "```",
            '<a title=">">',
            '"',
            "'",
            "--!>",
            "[^1]: ",
            "<https://x>",
            "<div",
            "<a",
            "</div",
            "<pre",
            " t=",
            "=",
            ' t=">',
            "'>",
            "<?>",
            "<video>",
            "</video>",
            "<rp>",
            "</rp>",
            '<relative-time datetime="2020-01-01T00:00:00Z">',
            "</relative-time>",
            "Plugh",
        ]
    ),
    max_size=30,
).map("".join)


@hypothesis.example("x\n\n> <!--\n> a")
@hypothesis.example("> x\n> <!--\n> a")
@hypothesis.example("> - <!--\nx")
@hypothesis.example("- a\n  - b\n    <!--")
@hypothesis.example("<div>\n> x <!--")
@hypothesis.example("> <pre>\n<?\n</pre>\nx <!--")
@hypothesis.example("<!-- a --> <!--")
@hypothesis.example("<div\nPlugh\n")
@hypothesis.example("> <a t=\n> Plugh>")
@hypothesis.example("<pre")
@hypothesis.example("<div>\n\n```Plugh\n<div\n```")
@given(st.one_of(STRUCTURE, HTML_BLOCKS))
def test_the_engine_reads_nothing_that_an_html_block_hides(text):
    """Nothing after an HTML block that holds a `<!--` that no `-->` follows reaches
    the engine, wherever a CommonMark parser finds the block: GitHub shows none of
    it. Nor does a word of a tag that runs over several rows, or of an instruction,
    a declaration or a section of CDATA, as a browser reads the HTML of a CommonMark
    parser, in a text without comments, which tests of their own judge, nor a word
    of the info string of a fence, which a browser doesn't show. And without a
    `<!--`, a quote, a
    tag or such markup over rows, which alone can hide it, nothing ends the text
    early."""
    text += "\n\nXyzzy"
    shown = assistant.shown_text(text)[0]
    seen = browser_text(text)
    # A `<!--` in a tag is part of the tag: GitHub showed `zqd` after `<div><div<!--`
    # and a blank row (`gh api markdown -f mode=gfm`, 2026-11-20).
    assert not hides_the_rest(text) or "Xyzzy" not in shown or "Xyzzy" in seen
    if not any(mark in text for mark in ("<!--", "-->")):
        assert shown.count("Plugh") <= seen.count("Plugh")
        assert "Xyzzy" in seen or "Xyzzy" not in shown
    assert (
        any(mark in text for mark in ("<!--", '"', "'"))
        or assistant.long_tags(text) != ([], len(text))
        or "Xyzzy" in shown
    )


# Text made of the pieces of tables: pipes with and without a backslash before
# them, delimiter rows, quotes and what ends a table.
TABLES = st.lists(
    st.sampled_from(
        [
            "| a |\n|---|\n",
            "a | b\n--|--\n",
            "| a | b |\n|:-|-:|\n",
            ":-:\n",
            "> ",
            "|",
            "\\|",
            "\\\\|",
            " ",
            "\t",
            "    ",
            "\n",
            "\n\n",
            "`",
            "# ",
            "- ",
            "2. ",
            "***\n",
            "x",
            "Plugh",
        ]
    ),
    max_size=30,
).map("".join)


@hypothesis.example("| a |\n|---|\n| b | Plugh |")
@hypothesis.example("> | a |\n> |---|\n> | b | Plugh\n| c | Plugh")
@hypothesis.example("| a |\n|---|\n|b\\\\|Plugh|Plugh|")
@hypothesis.example("```Plugh\n| a |\n|---|\n| b | Plugh |")
@hypothesis.example("| a |\n|---|\n```x Plugh\n| b |")
@given(TABLES)
def test_the_engine_reads_no_cell_that_github_drops(text):
    """No word of a cell beyond the cells of a table's header reaches the engine: a
    browser shows none of it, as the oracle splits the rows. Nor does a word of the
    info string of a fence, which a browser doesn't show either."""
    shown = assistant.shown_text(text)[0]
    assert shown.count("Plugh") <= browser_text(text).count("Plugh")


# Text made of the pieces of links and images with titles. `Plugh` never stands in
# a destination: the engine reads a destination, which the page doesn't show.
TITLES = st.lists(
    st.sampled_from(
        [
            "[",
            "]",
            "](u ",
            "](",
            "![",
            '"',
            "'",
            "(",
            ")",
            "\\",
            "`",
            "- ",
            "> ",
            "1. ",
            "<div>",
            "\n",
            "\n\n",
            "\t",
            " ",
            "u",
            "Plugh",
        ]
    ),
    max_size=30,
).map("".join)


@hypothesis.example('[a](u "Plugh")')
@hypothesis.example("![Plugh](u 'Plugh')")
@hypothesis.example('![a [b](u "Plugh")](p)')
@hypothesis.example('> a\n[b](u "Plugh\n> c")')
@hypothesis.example('[a](u "`Plugh") `b` [c](u "Plugh")')
@hypothesis.example('`[a](u "x")` [b](u "Plugh")')
@given(TITLES)
def test_the_engine_reads_no_title_of_a_link_or_an_image(text):
    """No word of the title of a link or an image reaches the engine, which a page
    shows only on hover, and the description of an image counts as shown. Where
    `Plugh` follows a `](` without a blank, it may stand in a destination, and
    where a fence holds it, in the language of the fence: such a text counts as
    none, as one with a backslash before it, which the oracle reads as a token of
    its own. Where the oracle, which is not GitHub's parser, reads otherwise than
    GitHub, the cases recorded from GitHub's renderer in tests/test_assistant.py
    decide."""
    if not re.search(r"\]\(\s*\S*Plugh|```|\\Plugh", text):
        shown = assistant.shown_text(text)[0]
        seen = browser_text(text, descriptions=True)
        assert shown.count("Plugh") <= seen.count("Plugh"), (shown, seen)


# Text made of the pieces of math and of the code that GitHub draws. Without HTML,
# whose removal can make a row a fence that GitHub shows as text, as after a
# comment at the start of a row.
DRAWN = st.lists(
    st.sampled_from(
        [
            "$",
            "$$",
            "\\$",
            "&#36;",
            "`",
            "```mermaid\n",
            "~~~geojson\n",
            "```math\n",
            "```\n",
            "\\color{transparent}",
            "%",
            "&#92;",
            "> ",
            "- ",
            "| a |\n|---|\n",
            "|",
            " ",
            "\n",
            "\n\n",
            "x",
            "Plugh",
        ]
    ),
    max_size=30,
).map("".join)


@given(DRAWN)
def test_no_text_of_math_or_a_drawing_reaches_the_engine_that_could_hide(text):
    """No word of math with a backslash, a `%` or an `&` in it, nor of the source
    of a drawing that the engine may not read, reaches it. Where the text holds no
    fence, whose end a span of math may take out, the text that the engine reads
    holds no such math either, read anew."""
    found = assistant.structures_of(text)
    hidden = [
        *found.math,
        *(span for span in found.drawn if span not in found.readable),
    ]
    spans = assistant.apart([(start, end, "") for start, end in hidden])
    inside = sum(text.count("Plugh", start, end) for start, end, _ in spans)
    shown = assistant.shown_text(text)[0]
    assert shown.count("Plugh") <= text.count("Plugh") - inside, shown
    if "```" not in text and "~~~" not in text:
        assert not assistant.structures_of(shown).math, shown


@given(st.one_of(TEXT, TABLES, DRAWN, HTML_BLOCKS))
def test_the_spans_that_strip_html_keeps_make_its_text(text):
    """The spans of shown_spans, joined, are the text of shown_html; their text is the
    text as it is written but for a space in place of each date that the page
    draws."""
    spaced, spans, cut = assistant.shown_spans(text)
    assert len(spaced) == len(text)
    assert "".join(spaced[start:end] for start, end in spans) == (
        assistant.strip_html(text)
    )
    assert spaced == text or "<relative-time" in text.lower()
    assert cut == assistant.shown_html(text)[1]
    assert all(end > start for start, end in spans)
    assert all(left[1] <= right[0] for left, right in itertools.pairwise(spans))


NOTES = (
    assistant.TABLE_NOTE,
    assistant.INFO_NOTE,
    assistant.DRAWING_NOTE,
    assistant.DIAGRAM_NOTE,
    assistant.MATH_NOTE,
    assistant.DEFINITION_NOTE,
    assistant.FOOTNOTE_NOTE,
)


@hypothesis.example("$\\color{red}x$\n[a]: https://example.com\n")
@hypothesis.example("[a]: x '<pre lang=\"mermaid\">y</pre>'\n\n| a |\n|-|\n| b | c |")
@hypothesis.example(" []:0")
@hypothesis.example("> [a]: x\n- [^b]: y")
@given(st.one_of(TEXT, DECIDED, DEFINITIONS, HTML_BLOCKS, TABLES, DRAWN))
def test_the_decisions_read_a_space_where_the_ai_reads_a_note(text):
    """What the assistant decides by itself reads the text that the AI reads with a
    space in the place of each note, and nothing else changed: the same rows, the
    same definitions found, and the same text where nothing went."""
    read = assistant.shown_text(text)[0]
    spaced = assistant.shown_text(text, notes=False)[0]
    for note in NOTES:
        read = read.replace(note, " ")
    assert spaced == read


# Forms with HTML: headings, answers, comments and tags with attributes.
FORM_HTML = st.lists(
    st.sampled_from(
        [
            "### What happened\n\n",
            "### Version\n\n",
            "_No response_",
            "<!--",
            "-->",
            '<img alt="Xyzzy">',
            '<a title="Xyzzy">',
            "</a>",
            "<",
            ">",
            "\n",
            " ",
            "x",
        ]
    ),
    max_size=30,
).map("".join)


@hypothesis.example("### What happened\n\n<!-- Xyzzy\n\n### Version\n\n1.2")
@hypothesis.example('Card is empty.\n<img alt="Xyzzy">')
@given(st.one_of(st.text(), STRUCTURE, FORM_HTML))
def test_an_excerpt_reads_nothing_that_issue_md_leaves_out(text):
    """Each character of an excerpt is one that the fence of issue.md holds for the
    same text: what GitHub hides there, the excerpts in the JSON files hide as well.
    Left out are the white space, which both shorten in their own ways, and what the
    excerpt writes itself: the colon after the label of a field and the ellipsis of
    a shortened excerpt."""
    rendered = assistant.rendered_text(text, assistant.Visible())
    marks, _, rest = rendered.partition("\n")
    inner = rest[: rest.rfind("\n" + marks)]
    written = assistant.Visible()(assistant.excerpt(text, 1500))
    kept = collections.Counter(
        char for char in written if not char.isspace() and char not in ":…"
    )
    assert kept <= collections.Counter(inner)


# Whether GitHub shows a comment collapsed, as REST and GraphQL send it.
COLLAPSES = st.sampled_from(
    [None, {"reason": "spam"}, {"reason": "off-topic"}, {"reason": None}]
)
GRAPHQL_COLLAPSES = st.sampled_from([False, True, "SPAM", "spam", "OFF_TOPIC"])
THREAD = st.lists(
    st.tuples(
        st.sampled_from(["reporter", "owner", "stranger", "bot"]),
        st.one_of(st.text(max_size=60), STRUCTURE),
        COLLAPSES,
    ),
    max_size=8,
)


def secret(index):
    """A word that only the text of a collapsed comment holds."""
    return f"Qzx{index}collapsedword"


@given(THREAD)
def test_the_engine_reads_no_text_of_a_comment_that_github_shows_collapsed(thread):
    comments = [
        make_comment(
            index,
            author,
            f"{text} {secret(index)}" if minimized is not None else text,
            1,
            "OWNER" if author == "owner" else "NONE",
            minimized=minimized,
        )
        for index, (author, text, minimized) in enumerate(thread, start=1)
    ]
    issue = make_issue(body="The card stays empty.")
    visible = assistant.Visible()
    rendered = assistant.render_issue(issue, comments, visible)
    hidden = [index for index, (_, _, how) in enumerate(thread, 1) if how is not None]
    for index in hidden:
        assert secret(index) not in rendered
    assert visible.collapsed == len(hidden)
    # Every comment keeps its heading and so its number on the page.
    for index in range(1, len(thread) + 1):
        assert f"\n## Comment {index} by " in rendered
    # Nor does anything else that the AI reads of them, or decides from them.
    assert not {
        item.comment for item in assistant.attachments_of(issue, comments, REPOSITORY)
    } & set(hidden)


@given(
    st.lists(
        st.tuples(
            st.one_of(st.text(max_size=60), STRUCTURE),
            GRAPHQL_COLLAPSES,
            st.lists(
                st.tuples(
                    st.one_of(st.text(max_size=60), STRUCTURE), GRAPHQL_COLLAPSES
                ),
                max_size=3,
            ),
        ),
        max_size=5,
    )
)
def test_the_engine_reads_no_text_of_a_discussion_comment_that_github_collapsed(
    thread,
):
    def node(text, minimized, word, replies=None):
        return {
            "author": {"__typename": "User", "login": "someone"},
            "authorAssociation": "NONE",
            "createdAt": at(1),
            "body": f"{text} {word}" if minimized else text,
            "isMinimized": bool(minimized),
            "minimizedReason": minimized if isinstance(minimized, str) else None,
            **({} if replies is None else {"replies": {"nodes": replies}}),
        }

    words = []
    comments = []
    for index, (text, minimized, replies) in enumerate(thread):
        word = secret(index)
        words += [word] if minimized else []
        nodes = []
        for number, (reply, hides) in enumerate(replies):
            reply_word = secret(f"{index}r{number}")
            words += [reply_word] if hides else []
            nodes.append(node(reply, hides, reply_word))
        comments.append(node(text, minimized, word, nodes))
    discussion = {
        "id": "D_5",
        "number": 5,
        "title": "How do I hide the live card?",
        "body": "The live card shows in every view.",
        "createdAt": at(2),
        "closed": False,
        "locked": False,
        "isAnswered": False,
        "authorAssociation": "NONE",
        "author": {"__typename": "User", "login": "asker"},
        "category": {"name": "Q&A", "isAnswerable": True},
        "comments": {"totalCount": len(comments), "nodes": comments},
    }
    visible = assistant.Visible()
    rendered = assistant.render_discussion(
        assistant.discussion_as_rest(discussion), visible
    )
    for word in words:
        assert word not in rendered
    assert visible.collapsed == len(words)


# Pieces that nest, as deep as GitHub's limit and past it: quotes, list items,
# elements of HTML and their scopes, emphasis, and blanks.
NESTING = st.lists(
    st.tuples(
        st.sampled_from(
            [
                ">",
                "> ",
                "- ",
                "1. ",
                "  ",
                "<div>",
                "<span>",
                "<b>",
                "<blockquote>",
                "<ul><li>",
                "<table><td>",
                "*",
                "_",
                "<div></b>",
                "<b><div></b>",
                "<br>",
                "<div><img src=x>",
            ]
        ),
        st.integers(1, 300),
        st.sampled_from(["\n", "\n\n", " "]),
    ),
    min_size=1,
    max_size=4,
)


@hypothesis.settings(
    max_examples=EXAMPLES // 2,
    suppress_health_check=[hypothesis.HealthCheck.too_slow],
    deadline=None,
)
@hypothesis.example([(">", 255, "\n\n")])
@hypothesis.example([("  ", 128, "\n"), ("- ", 1, "\n")])
@hypothesis.example([("<table><td>", 64, " ")])
@given(NESTING)
def test_the_cleaning_keeps_nothing_that_github_shows_nothing_of_for_its_depth(parts):
    """Each part nests a piece, and a marker word follows it: of the words that a
    browser shows as nothing, for the depth of GitHub's HTML (the oracle), the AI
    reads none either."""
    text = (
        "".join(
            f"{piece * count}zqm{index}{end}"
            for index, (piece, count, end) in enumerate(parts)
        )
        + "\n\nzqafter\n"
    )
    seen = browser_text(text)
    shown = assistant.shown_text(text)[0]
    hidden = [word for word in re.findall(r"zqm\d+|zqafter", text) if word not in seen]
    assert [word for word in hidden if word in shown] == []
    # The quick count leaves no text out that the count of each row would cut.
    if assistant.shallow(text):
        assert assistant.Depth(text).cut() is None
