"""The experimental switch `reading` and the readers on both of its values (#521).

On `cleaned`, the default, nothing is asked for and every reader gives the bytes it
gave before; the golden files of tests/golden/context hold them. On `rendered`, each
reader takes the converter's Markdown of GitHub's rendering of a text, and the
cleaned source of a text without one. FakeGitHub answers the requests of renderings
from its stores through GitHubOfRenderings; tests/test_renderings.py tests the
requests themselves.
"""

import json

import pytest
import yaml

import issue_assistant as assistant
from support import (
    REPOSITORY,
    SHAPES,
    FakeGitHub,
    at,
    files_of,
    make_comment,
    make_discussion,
    make_issue,
    run_main,
)

CLEANED = assistant.READING_CLEANED
RENDERED = assistant.READING_RENDERED
UUID = "0123abcd-0123-4567-89ab-0123456789ab"
UPLOADED = f"https://github.com/user-attachments/assets/{UUID}"
SIGNED = f"https://private-user-images.githubusercontent.com/1/2-{UUID}.png?jwt=a.b.c"
LOG = "https://github.com/user-attachments/files/101/home-assistant.log"
OTHER_LOG = "https://github.com/user-attachments/files/102/other.log"
PICTURE = (
    f'<p dir="auto"><a target="_blank" rel="noopener noreferrer" href="{SIGNED}">'
    f'<img src="{SIGNED}" alt="shot" style="max-width: 100%;"></a></p>'
)


def reader_of(html, repository=REPOSITORY):
    """A reader on `rendered` that holds the renderings of `html`, by their keys."""
    renderings = assistant.Renderings(html=dict(html))
    return assistant.TextReader(repository, RENDERED, renderings=renderings)


def issue_key(number, repository=REPOSITORY):
    return (repository, assistant.ISSUE, number, "")


def comment_key(number, comment):
    return (REPOSITORY, assistant.COMMENT, number, str(comment))


# The switch


@pytest.mark.parametrize(
    ("given", "taken"),
    [(None, CLEANED), ("", CLEANED), (CLEANED, CLEANED), (RENDERED, RENDERED)],
)
def test_the_switch_takes_only_its_two_values(monkeypatch, given, taken):
    if given is None:
        monkeypatch.delenv("READING", raising=False)
    else:
        monkeypatch.setenv("READING", given)
    assert assistant.reading_of("triage") == taken


@pytest.mark.parametrize("given", ["Rendered", "html", " cleaned"])
def test_any_other_value_of_the_switch_stops_the_step(
    monkeypatch, tmp_path, root, config, capsys, given
):
    code, _, _ = run_main(
        monkeypatch,
        tmp_path,
        root,
        FakeGitHub(config, make_issue()),
        "context",
        ISSUE="12",
        MODE="triage",
        READING=given,
    )
    assert code == 1
    assert (
        f"::error::The input reading takes cleaned or rendered, not {given!r}."
        in capsys.readouterr().out
    )


def test_a_discussion_mode_reads_cleaned(monkeypatch, config, root, capsys):
    monkeypatch.setenv("READING", RENDERED)
    assert assistant.reading_of("discussion-answer") == CLEANED
    assert capsys.readouterr().out == (
        "::notice::discussion-answer reads its texts cleaned: the reading rendered "
        "isn't built for the discussions yet.\n"
    )
    github = FakeGitHub(config, make_issue(7))
    github.discussion_store.append(make_discussion(12))
    # Also when write_context is given rendered, a discussion asks for none.
    assistant.write_context(github, 12, "discussion-answer", config, reading=RENDERED)
    assert not [call for call in github.calls if call[0] == "RENDERINGS"]


def test_the_action_hands_the_switch_to_the_command_line():
    action = yaml.safe_load((assistant.TOOL / "action.yml").read_text(encoding="utf-8"))
    given = action["inputs"]["reading"]
    assert given["description"].startswith("Experimental:")
    assert given["default"] == CLEANED and not given["required"]
    (run,) = (step for step in action["runs"]["steps"] if step.get("id") == "run")
    assert run["env"][assistant.READING_VARIABLE] == "${{ inputs.reading }}"


def test_the_command_line_hands_the_switch_to_each_command(
    monkeypatch, tmp_path, root, config
):
    """context and attachments ask for renderings on rendered, and for none
    without the switch."""
    for reading, asked in ((None, 0), (RENDERED, 1)):
        for name, extra in (("context", {"MODE": "triage"}), ("attachments", {})):
            github = FakeGitHub(config, make_issue(body="Text"))
            env = {"ISSUE": "12", **extra}
            if reading:
                env["READING"] = reading
            code, _, _ = run_main(monkeypatch, tmp_path, root, github, name, **env)
            assert code == 0
            calls = [call for call in github.calls if call[0] == "RENDERINGS"]
            assert len(calls) == asked, (reading, name)


# A context on both settings


def world(config):
    """An issue with a comment, an older issue and a discussion with its answer,
    each with its rendering."""
    issue = make_issue(
        body=f"Text with a picture: ![shot]({UPLOADED}) and TypeError",
        body_html=PICTURE.replace("<a ", "Text with a picture: <a ").replace(
            "</a></p>", "</a> and TypeError</p>"
        ),
    )
    older = make_issue(
        7, body="Old *one*", body_html='<p dir="auto">Old <em>one</em></p>'
    )
    github = FakeGitHub(config, issue, older)
    github.comment_store[12] = [
        {
            **make_comment(2, "reporter", f"See [log]({LOG})", 1),
            "body_html": f'<p dir="auto">See <a href="{LOG}">log</a></p>',
        }
    ]
    github.discussion_store = [
        {
            "number": 104,
            "title": "Testers",
            "category": {"name": "Q&A"},
            "isAnswered": True,
            "closed": False,
            "body": "Please *test*",
            "body_html": '<p dir="auto">Please <em>test</em></p>',
            "answer": {
                "body": "Done *now*",
                "body_html": '<p dir="auto">Done <em>now</em></p>',
                "createdAt": at(2),
                "isMinimized": False,
            },
        }
    ]
    return github


def first_step(github):
    """The calls of the sources, without the requests of renderings."""
    return [call for call in github.calls if call[0] != "RENDERINGS"]


@pytest.mark.usefixtures("root")
def test_the_cleaned_reading_asks_for_no_rendering(config):
    """Without the switch, nothing is asked for and the recorded renderings change
    nothing; on rendered, the requests of the sources are the same, in their order."""
    folder = config.root / assistant.CONTEXT
    github = world(config)
    assistant.write_context(github, 12, "triage", config)
    cleaned, files = github.calls, files_of(folder)
    assert not [call for call in cleaned if call[0] == "RENDERINGS"]
    assert "Old *one*" in files and UPLOADED in files
    github = world(config)
    assistant.write_context(github, 12, "triage", config, reading=CLEANED)
    assert github.calls == cleaned and files_of(folder) == files
    github = world(config)
    assistant.write_context(github, 12, "triage", config, reading=RENDERED)
    assert first_step(github) == cleaned
    # The issue and its comment first, then the rest: two requests.
    assert len(github.calls) - len(cleaned) == 2
    assert "Old _one_" in files_of(folder)


@pytest.mark.usefixtures("root")
def test_a_rendering_failure_never_fails_the_run(config, capsys):
    """With every request failing, each text is its cleaned source: the files are
    byte for byte those of cleaned, and the notice counts why."""
    folder = config.root / assistant.CONTEXT
    github = world(config)
    assistant.write_context(github, 12, "triage", config)
    cleaned = files_of(folder)
    capsys.readouterr()
    for shape in SHAPES.values():
        github = world(config)
        github.rendering.failing = lambda aliases, request, shape=shape: shape
        assistant.write_context(github, 12, "triage", config, reading=RENDERED)
        assert files_of(folder) == cleaned
        out = capsys.readouterr().out
        assert "::notice::Renderings of GitHub: 0 read;" in out
        assert "(its request failed: 5)." in out


@pytest.mark.usefixtures("root")
def test_the_renderings_of_a_context_share_the_limits_of_the_command(config):
    """The second read of a context gets what the first left of its limits."""
    github = world(config)
    seen = []
    real = github.renderings

    def renderings(texts, limits):
        seen.append(limits)
        return real(texts, limits)

    github.renderings = renderings
    assistant.write_context(github, 12, "triage", config, reading=RENDERED)
    first, second = seen
    assert first == assistant.CONTEXT_RENDERING_LIMITS
    assert second.requests == first.requests - 1
    assert second.points == first.points - 1
    assert second.seconds <= first.seconds and second.lowest == 4999


@pytest.mark.usefixtures("root")
def test_a_reply_task_reads_the_issue_and_its_comments_alone(config):
    github = world(config)
    github.comment_store[12].append(
        {
            **make_comment(3, "maintainer", "Which *one*?", 0.5, "OWNER"),
            "body_html": '<p dir="auto">Which <em>one</em>?</p>',
        }
    )
    assistant.write_context(github, 12, "maintainer-reply", config, 3, reading=RENDERED)
    (request,) = [call for call in github.calls if call[0] == "RENDERINGS"]
    assert "i7" not in request[1] and "d104" not in request[1]
    judged = (config.root / assistant.CONTEXT / "comment.md").read_text("utf-8")
    assert "Which _one_?" in judged


# Each reader on both settings


def test_issue_md_takes_the_rendering_of_each_text():
    issue = make_issue(body="**Bold**")
    comments = [
        make_comment(2, "reporter", "*Mine*", 1),
        make_comment(3, "reporter", "*Not rendered*", 1),
    ]
    assert "**Bold**" in assistant.render_issue(issue, comments)
    reader = reader_of(
        {
            issue_key(12): "<p><strong>Bold</strong></p>",
            comment_key(12, 2): "<p><em>Mine</em></p>",
        }
    )
    text = assistant.render_issue(issue, comments, reader=reader)
    assert "```\n**Bold**\n```" in text and "```\n_Mine_\n```" in text
    # A text without a rendering is its cleaned source.
    assert "```\n*Not rendered*\n```" in text


def test_a_rendering_reaches_the_ai_without_invisible_characters():
    issue = make_issue(body="A​B")
    visible = assistant.Visible()
    reader = reader_of({issue_key(12): "<p>A​B</p>"})
    assert "```\nAB\n```" in assistant.render_issue(issue, [], visible, reader)
    assert visible.removed == 1


def test_comment_md_takes_the_rendering_of_the_judged_comment():
    issue = make_issue()
    judged = make_comment(2, "maintainer", "*Why?*", 1, "OWNER")
    assert "*Why?*" in assistant.render_judged(issue, [judged], judged)
    reader = reader_of({comment_key(12, 2): "<p><em>Why?</em></p>"})
    assert "_Why?_" in assistant.render_judged(issue, [judged], judged, reader)


def test_the_other_issues_take_their_renderings():
    item = make_issue(7, body="*Old*")
    visible = assistant.Visible()
    markdown = reader_of({issue_key(7): "<p><em>Old</em></p>"}).issue(item)
    assert "*Old*" in assistant.render_other_issue(item, visible)
    assert "_Old_" in assistant.render_other_issue(item, visible, markdown=markdown)
    assert assistant.issue_line(item, visible)["excerpt"] == "*Old*"
    assert assistant.issue_line(item, visible, markdown)["excerpt"] == "_Old_"


def test_an_excerpt_takes_the_form_rows_of_the_converter():
    source = (
        "### What happened?\n\nThe card <!-- x --> is empty.\n\n### Version\n\n1.9.0\n"
    )
    html = (
        '<h3 dir="auto">What happened?</h3>\n<p dir="auto">The card  is empty.</p>\n'
        '<h3 dir="auto">Version</h3>\n<p dir="auto">1.9.0</p>'
    )
    markdown = assistant.markdown_of(html, source).text
    cleaned = assistant.excerpt(source, 400)
    assert cleaned == "What happened?: The card is empty.\nVersion: 1.9.0"
    assert assistant.excerpt("", 400, None, markdown) == cleaned


def test_the_discussions_and_their_answers_take_their_renderings(tmp_path):
    discussion = {
        "number": 104,
        "title": "Testers",
        "body": "Please *test*",
        "answer": {"body": "Done *now*", "isMinimized": False},
    }
    reader = reader_of(
        {
            (REPOSITORY, assistant.DISCUSSION, 104, ""): "<p>Please <em>test</em></p>",
            (REPOSITORY, assistant.ANSWER, 104, ""): "<p>Done <em>now</em></p>",
        }
    )
    for given, expected in ((assistant.CLEANED_READER, "*"), (reader, "_")):
        assistant.write_related(tmp_path, [], [discussion], assistant.Visible(), given)
        (line,) = (tmp_path / "discussions.jsonl").read_text("utf-8").splitlines()
        found = json.loads(line)
        assert found["excerpt"] == f"Please {expected}test{expected}"
        assert found["answer"] == f"Done {expected}now{expected}"
        (tmp_path / "issues").rmdir()


def test_the_search_takes_the_words_of_the_rendering():
    issue = make_issue(title="Card", body="ValueError <!-- TypeError -->")
    assert assistant.search_terms(issue) == ["valueerror", "card"]
    # GitHub showed another word than the cleaning reads: the rendering wins.
    reader = reader_of({issue_key(12): "<p>KeyError</p>"})
    assert assistant.search_terms(issue, reader) == ["keyerror", "card"]


def test_an_uploaded_picture_keeps_its_attachment():
    """A rendering holds a signed address of the picture, valid for minutes; the
    converter's text and attachments.md hold the source's, found by its uuid."""
    issue = make_issue(body=f"![shot]({UPLOADED})")
    reader = reader_of({issue_key(12): PICTURE})
    (found,) = assistant.attachments_of(issue, [], REPOSITORY, reader)
    assert (found.url, found.kind) == (UPLOADED, "image")
    text = assistant.render_issue(issue, [], reader=reader)
    assert f"![shot]({UPLOADED})" in text and "private-user-images" not in text
    # The converter alone would lose it.
    assert "![shot]()" in assistant.markdown_of(PICTURE, issue["body"]).text


def test_a_rendering_without_a_signed_picture_stays_as_it_is():
    html = f'<p><img src="{UPLOADED}" alt="x"></p>'
    assert assistant.canonical_pictures(html) is html
    # A signed address without the uuid of an upload stays: the converter drops it.
    odd = "https://private-user-images.githubusercontent.com/1/2-shot.png?jwt=x"
    assert assistant.canonical_pictures(f'<img src="{odd}">') == f'<img src="{odd}">'


def test_the_rendering_decides_which_links_are_attachments():
    issue = make_issue(body=f"[log]({LOG}) <!-- [other]({OTHER_LOG}) -->")
    comment = make_comment(2, "reporter", "Nothing", 1)
    assert [
        item.url for item in assistant.attachments_of(issue, [comment], REPOSITORY)
    ] == [LOG]
    reader = reader_of(
        {
            issue_key(12): f'<p><a href="{LOG}">log</a></p>',
            comment_key(12, 2): f'<p><a href="{OTHER_LOG}">x</a></p>',
        }
    )
    # The address of a rendering counts only where the source holds it.
    assert [
        item.url
        for item in assistant.attachments_of(issue, [comment], REPOSITORY, reader)
    ] == [LOG]
    # A rendering that shows nothing shows no link, not those of the source.
    empty = reader_of({issue_key(12): "<p></p>"})
    assert assistant.attachments_of(issue, [], REPOSITORY, empty) == []


# The texts of a command


def test_the_texts_of_a_command_come_in_their_order():
    issue = make_issue(body="Issue")
    comments = [
        make_comment(2, "reporter", "Mine", 1),
        make_comment(3, "other", "Theirs", 1),
        make_comment(4, "reporter", "Spam", 1, minimized={"reason": "SPAM"}),
    ]
    texts = assistant.issue_texts(REPOSITORY, issue, comments)
    assert [(text.kind, text.comment, text.hidden) for text in texts] == [
        (assistant.ISSUE, "", False),
        (assistant.COMMENT, "2", False),
        (assistant.COMMENT, "3", False),
        (assistant.COMMENT, "4", True),
    ]
    mine = assistant.issue_texts(REPOSITORY, issue, comments, reporter_only=True)
    assert [text.hidden for text in mine] == [False, False, True, True]
    discussions = [
        {"number": 1, "body": "A", "answer": {"body": "B", "isMinimized": False}},
        {"number": 2, "body": "C", "answer": {"body": "D", "isMinimized": True}},
        {"number": 3, "body": None, "answer": None},
    ]
    assert [
        (text.kind, text.number, text.body)
        for text in assistant.discussion_texts(REPOSITORY, discussions)
    ] == [
        (assistant.DISCUSSION, 1, "A"),
        (assistant.ANSWER, 1, "B"),
        (assistant.DISCUSSION, 2, "C"),
        (assistant.DISCUSSION, 3, ""),
    ]
    assert assistant.other_texts("owner/server", [make_issue(31)])[0].key == (
        "owner/server",
        assistant.ISSUE,
        31,
        "",
    )


def test_the_cleaned_reader_reads_nothing():
    class Refusing:
        def renderings(self, texts, limits):  # pragma: no cover - never called
            raise AssertionError("asked")

    reader = assistant.TextReader(REPOSITORY)
    reader.read(Refusing(), [])
    reader.notice()
    assert reader.renderings is None and reader.issue(make_issue()) is None


def test_a_reader_converts_each_rendering_once(monkeypatch):
    calls = []
    real = assistant.reading.markdown_of

    def counting(html, source, notes=True):
        calls.append(notes)
        return real(html, source, notes)

    monkeypatch.setattr(assistant.reading, "markdown_of", counting)
    reader = reader_of({issue_key(12): "<p>x</p>"})
    issue = make_issue(body="x")
    assert reader.issue(issue) == reader.issue(issue) == "x"
    assert reader.issue(issue, notes=False) == "x"
    assert calls == [True, False]


# A fixture records its renderings


def test_a_fixture_takes_its_recorded_renderings(monkeypatch, tmp_path, root, config):
    issue = make_issue(1000000, body="*Fixed*", body_html="<p><em>Fixed</em></p>")
    comment = make_comment(1, "reporter", "*No HTML*", 0.5)
    (root / "canary.json").write_text(
        json.dumps({"issue": issue, "comments": [comment]}), encoding="utf-8"
    )
    github = FakeGitHub(config, make_issue(5))
    code, _, _ = run_main(
        monkeypatch,
        tmp_path,
        root,
        github,
        "context",
        MODE="triage",
        ISSUE_ASSISTANT_FIXTURE="canary.json",
        READING=RENDERED,
    )
    assert code == 0
    text = (root / assistant.CONTEXT / "issue.md").read_text("utf-8")
    assert "```\n_Fixed_\n```" in text and "```\n*No HTML*\n```" in text
    # Nothing about the fixture's issue was asked of GitHub.
    assert not [call for call in github.calls if "1000000" in str(call)]
    assert [call for call in github.calls if call[0] == "RENDERINGS"]


def test_a_recorded_rendering_above_the_cap_is_not_taken(config):
    github = FakeGitHub(config)
    issue = make_issue(body="x", body_html="<p>" + "x" * assistant.CAP + "</p>")
    github.use_fixture(assistant.Fixture(issue, []))
    texts = [assistant.Text(REPOSITORY, assistant.ISSUE, 12, "x")]
    found = github.renderings(texts, assistant.CONTEXT_RENDERING_LIMITS)
    assert found.html == {} and found.fallbacks == {assistant.ARRIVED_TOO_LARGE: 1}
    assert github.recorded_renderings() == {issue_key(12): issue["body_html"]}
    assert FakeGitHub(config).recorded_renderings() == {}


def test_a_fixture_takes_body_html_only_as_a_string(tmp_path):
    issue = make_issue(body="x", body_html=5)
    (tmp_path / "canary.json").write_text(
        json.dumps({"issue": issue, "comments": []}), encoding="utf-8"
    )
    with pytest.raises(
        assistant.AssistantError, match="issue.body_html must be a string"
    ):
        assistant.read_fixture(tmp_path, "canary.json")
