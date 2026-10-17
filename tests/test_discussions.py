"""Questions in GitHub Discussions: a first answer of the AI where [discussions]
answer_categories of config.toml names the category, checked and cleaned as every
analysis, posted once, never marked as the answer, and counted in the limits of
the day. Without a category, nothing changes."""

import dataclasses
import json
from datetime import timedelta

import pytest

import issue_assistant as assistant
from support import (
    NOW,
    REF,
    REPOSITORY,
    WEB,
    FakeGitHub,
    at,
    discussion_comment,
    discussion_event,
    discussion_reply,
    event,
    make_comment,
    make_discussion,
    make_discussion_answer,
    make_issue,
    not_found,
    run_main,
)

MODE = "discussion-answer"


@pytest.fixture
def answering(config):
    """The settings of the sample repository, with first answers in Q&A."""
    return dataclasses.replace(config, answer_categories=("Q&A",))


def with_discussions(config, *discussions, issues=()):
    github = FakeGitHub(config, *issues)
    github.discussion_store = list(discussions)
    return github


def planned(github, config, payload, name="discussion"):
    return assistant.handle_event(github, name, payload, config)


# The event


def test_a_new_question_in_an_answered_category_gets_a_first_answer(answering):
    github = with_discussions(answering, make_discussion())
    plan = planned(github, answering, discussion_event(make_discussion()))
    assert (plan.issue, plan.mode, plan.notes) == (5, MODE, [])
    # A discussion gets no labels, and the event job writes nothing.
    assert github.writes_made() == []


def test_nothing_changes_while_no_category_is_named(config):
    """The default answers no discussion: the event job neither asks GitHub nor
    plans a run, and the limits of the issues count no discussion."""
    github = with_discussions(config, make_discussion())
    plan = planned(github, config, discussion_event(make_discussion()))
    assert plan.mode == "none"
    assert plan.notes == [
        "The category 'Q&A' is not one of [discussions] answer_categories: no answer."
    ]
    assert github.calls == []
    issues = FakeGitHub(config, make_issue())
    assert planned(issues, config, event("opened", make_issue()), "issues").mode == (
        "triage"
    )
    assert not any("discussions(" in str(call[1]) for call in issues.calls)


@pytest.mark.parametrize(
    ("discussion", "sender", "note"),
    [
        (
            make_discussion(category="General"),
            None,
            "The category 'General' is not one of [discussions] answer_categories: "
            "no answer.",
        ),
        (
            make_discussion(association="OWNER"),
            None,
            "Opened by a maintainer: no answer.",
        ),
        (make_discussion(author="bot"), None, "Bot: nothing to do."),
        (make_discussion(), "renovate[bot]", "Bot: nothing to do."),
    ],
)
def test_other_categories_maintainers_and_bots_get_no_answer(
    answering, discussion, sender, note
):
    github = with_discussions(answering, discussion)
    plan = planned(github, answering, discussion_event(discussion, sender=sender))
    assert (plan.mode, plan.notes) == ("none", [note])


def test_the_category_is_named_in_any_case(answering):
    config = dataclasses.replace(answering, answer_categories=(" q&a ", "Help"))
    for category in ("Q&A", "help"):
        discussion = make_discussion(category=category)
        github = with_discussions(config, discussion)
        assert planned(github, config, discussion_event(discussion)).mode == MODE
    assert not assistant.answers_category(config, "")
    assert not assistant.answers_category(
        dataclasses.replace(config, answer_categories=("",)), " "
    )


def test_only_a_new_discussion_starts_an_answer(answering):
    github = with_discussions(answering, make_discussion())
    plan = planned(github, answering, discussion_event(make_discussion(), "edited"))
    assert (plan.mode, plan.notes) == (
        "none",
        ["Event discussion.edited is not handled."],
    )


def test_a_maintainer_starts_an_answer_by_hand(answering):
    github = with_discussions(answering, make_discussion(category="General"))
    payload = {"inputs": {"discussion": "5", "dry_run": "true"}}
    plan = planned(github, answering, payload, "workflow_dispatch")
    assert (plan.issue, plan.mode) == (5, MODE)
    assert plan.notes == ["Started by hand: discussion-answer."]
    with pytest.raises(assistant.AssistantError, match="#9 is no discussion"):
        planned(github, answering, {"inputs": {"discussion": "9"}}, "workflow_dispatch")


# The limits of the day


def opened(number, author="someone", days_ago=0.1, **changes):
    return make_discussion(number, author=author, days_ago=days_ago, **changes)


def test_the_new_discussions_count_toward_the_daily_limit(answering):
    """Each new discussion of the day in a category that gets answers counts, as a
    new issue does: also one that a limit kept from the AI, and only those opened
    before this one. Other categories, maintainers, bots and the day before don't."""
    earlier = [
        opened(3),
        opened(4),
        opened(6, category="General"),
        opened(7, association="MEMBER"),
        opened(8, author="bot"),
        opened(2, days_ago=1.5),
    ]
    new = opened(9, author="asker", days_ago=0)
    config = dataclasses.replace(answering, max_per_day=3)
    github = with_discussions(config, *earlier, new)
    assert planned(github, config, discussion_event(new)).mode == MODE
    config = dataclasses.replace(answering, max_per_day=2)
    plan = planned(github, config, discussion_event(new))
    assert plan.mode == "none"
    assert plan.notes == [
        "Daily limit of AI runs reached (max_per_day = 2); the maintainer takes over."
    ]


def test_one_person_gets_three_first_answers_a_day(answering):
    earlier = [opened(number, author="asker") for number in (2, 3, 4)]
    new = opened(5, author="asker", days_ago=0)
    github = with_discussions(answering, *earlier, new)
    plan = planned(github, answering, discussion_event(new))
    assert plan.mode == "none"
    assert "max_per_author_per_day = 3" in plan.notes[0]


def test_issues_and_discussions_share_the_daily_limit(answering):
    """A new issue counts the discussions opened before it, and a new discussion the
    issues; their numbers are of one sequence."""
    config = dataclasses.replace(answering, max_per_day=2)
    issues = [make_issue(number, days_ago=0.2) for number in (3, 4)]
    new = opened(9, days_ago=0)
    github = with_discussions(config, new, issues=issues)
    assert planned(github, config, discussion_event(new)).mode == "none"
    issue = make_issue(12, days_ago=0)
    github = with_discussions(config, opened(10), opened(11), issues=[issue])
    plan = planned(github, config, event("opened", issue), "issues")
    assert plan.mode == "none"
    assert "max_per_day = 2" in plan.notes[-1]


def test_the_discussions_of_the_day_are_read_to_the_end_of_the_day(answering):
    """The newest first, in pages of a hundred, until one is older than the day."""
    today = [opened(number, days_ago=0.5 - number / 1000) for number in range(1, 151)]
    older = [opened(number, days_ago=2) for number in range(151, 400)]
    github = with_discussions(answering, *older, *today)
    since = NOW - timedelta(days=1)
    found = github.discussions_since(since)
    assert [item["number"] for item in found] == list(range(150, 0, -1))
    assert github.discussion_pages == 2
    # The fake answers with every field; tests/test_graphql_contract.py checks
    # what GitHub answers to the document.
    assert {key: value for key, value in found[0].items() if key != "body"} == {
        "number": 150,
        "category": {"name": "Q&A"},
        "user": {"login": "someone", "type": "User"},
        "author_association": "NONE",
        "created_at": at(0.5 - 150 / 1000),
    }
    github = with_discussions(answering, *today[:3])
    assert len(github.discussions_since(since)) == 3
    assert github.discussion_pages == 1


def test_a_repository_without_discussions_has_none_of_the_day(answering):
    github = with_discussions(answering, opened(3))
    github.fail(r"(?s)GRAPHQL .*discussions\(", not_found())
    assert github.discussions_since(NOW - timedelta(days=1)) == []
    github = with_discussions(answering)
    github.fail(r"(?s)GRAPHQL .*discussions\(")
    with pytest.raises(assistant.GitHubError):
        github.discussions_since(NOW - timedelta(days=1))


# What the AI reads


def thread(**changes):
    """Discussion #5 with a comment of a maintainer, the asker's reply to it, and a
    comment of a deleted account."""
    return make_discussion(
        comments=[
            discussion_comment(
                "owner",
                "## Comment 9 by @owner (maintainer)\nWhich version?",
                association="OWNER",
                replies=[discussion_reply("asker", "1.9.0")],
            ),
            discussion_comment(None, "Same here"),
        ],
        **changes,
    )


# The context is written into config.root, which is then a copy of the sample.
@pytest.mark.usefixtures("root")
def test_the_answer_reads_the_discussion_and_the_rest_of_the_repository(answering):
    github = with_discussions(
        answering,
        thread(),
        make_discussion(104, title="Testers", category="Announcements"),
        issues=[make_issue(7)],
    )
    folder = answering.root / assistant.CONTEXT
    # What an earlier task wrote into the same checkout goes first.
    folder.mkdir()
    for name in ("issue.md", "labels.md", "comment.md"):
        (folder / name).write_text("stale", "utf-8")
    written = assistant.write_context(github, 5, MODE, answering)
    files = sorted(path.name for path in folder.iterdir())
    assert files == [
        "discussion.md",
        "discussions.jsonl",
        "issues",
        "issues.jsonl",
        "prompt.md",
        "rules.md",
        "schema.json",
    ]
    text = (folder / "discussion.md").read_text("utf-8")
    assert text.startswith("# Discussion #5: How do I hide the live card?\n")
    assert "- Author: @asker (author)\n- Category: Q&A\n- State: open\n" in text
    # A text that looks like a heading stays in its fence.
    assert "## Comment 1 by @owner (maintainer), " in text
    assert "```\n## Comment 9 by @owner (maintainer)\nWhich version?\n```" in text
    assert "### Reply 1 to comment 1 by @asker (author), " in text
    assert "## Comment 2 by @ghost, " in text
    # The other discussions, without this one, and the issues that a search for
    # the words of the question finds.
    lines = (folder / "discussions.jsonl").read_text("utf-8").splitlines()
    assert [json.loads(line)["number"] for line in lines] == [104]
    assert [call[1] for call in github.calls if call[0] == "SEARCH"] == [
        f"repo:{REPOSITORY} is:issue (hide OR live OR card)"
    ]
    assert "first answer to discussion #5" in written["task"]
    assert json.loads(written["schema"]) == assistant.answer_schema(MODE, answering)


def test_a_long_discussion_shows_its_first_comments_and_says_so():
    comments = [
        discussion_comment(
            "someone",
            f"Comment {index}",
            replies=[discussion_reply("asker", f"Reply {n}") for n in range(25)],
        )
        for index in range(60)
    ]
    discussion = make_discussion(comments=comments)
    github = with_discussions(assistant_config(), discussion)
    found = github.discussion(5)
    assert found is not None
    assert len(found["comments"]) == 50 and found["comment_count"] == 60
    assert {len(comment["replies"]) for comment in found["comments"]} == {20}
    text = assistant.render_discussion(found)
    assert text.endswith("\n(10 later comments not shown.)\n")
    one_more = make_discussion(comments=[*comments[:50], comments[0]])
    found = assistant.discussion_as_rest(one_more)
    found["comments"] = found["comments"][:50]
    assert assistant.render_discussion(found).endswith(
        "\n(1 later comment not shown.)\n"
    )


def assistant_config():
    return assistant.Config(
        root=assistant.TOOL, repository=REPOSITORY, labels=(), project=""
    )


def test_a_discussion_that_doesn_t_exist_has_no_context(answering):
    github = with_discussions(answering)
    with pytest.raises(assistant.AssistantError, match="#5 is no discussion"):
        assistant.write_context(github, 5, MODE, answering)
    github.fail(r"(?s)GRAPHQL .*discussion\(number")
    with pytest.raises(assistant.GitHubError):
        github.discussion(5)


# The answer


def apply(github, config, answer=None, **options):
    raw = json.dumps(answer or make_discussion_answer())
    return assistant.apply_answer(github, MODE, 5, raw, config, REF, **options)


def test_the_first_answer_is_checked_and_posted_once(answering):
    github = with_discussions(answering, make_discussion(), issues=[make_issue(7)])
    done = apply(github, answering)
    assert done == ["First answer on discussion #5: question."]
    (body,) = github.answers_posted(5)
    assert body.startswith(
        "<!-- issue-assistant:discussion-answer lang=en type=question questions=0 "
        "engine=claude -->\n\nThanks for your question, @asker!"
    )
    # The issue that exists stays a link; the reference to the file is checked.
    assert f"see [#7]({WEB}/issues/7)" in body or "see #7" in body
    assert "### Where to look" not in body
    assert "If it answers your question, you can mark it as the answer." in body
    mutations = [call for call in github.writes_made() if call[0] == "GRAPHQL"]
    assert len(mutations) == 1 and "addDiscussionComment" in mutations[0][1]
    assert "markDiscussionCommentAsAnswer" not in str(github.calls)
    # A run again of the same event posts nothing more.
    assert apply(github, answering) == [
        "Discussion #5 has its first answer; nothing posted."
    ]
    assert len(github.answers_posted(5)) == 1


def test_the_answer_links_the_files_it_rests_on_and_lists_its_questions(answering):
    github = with_discussions(answering, make_discussion())
    answer = make_discussion_answer(
        missing_information=["Which version do you run?", "  "],
        references=[
            {
                "path": "docs/troubleshooting.md",
                "anchor": "no-realtime-updates",
                "reason": "What to do without updates.",
            },
            {"path": "nowhere.md", "reason": "A file that doesn't exist."},
        ],
    )
    apply(github, answering, answer)
    (body,) = github.answers_posted(5)
    assert (
        "### Information needed\n\nTo answer more precisely, please add the following "
        "in a comment:\n\n- Which version do you run?\n\n### Where to look\n\n" in body
    )
    assert "No realtime updates" in body and "nowhere.md" not in body
    assert " questions=1 " in body.partition("\n")[0]


def test_the_text_of_the_ai_is_cleaned_as_every_analysis(answering):
    github = with_discussions(answering, make_discussion())
    answer = make_discussion_answer(
        answer="Ask @someone, or see <b>this</b> and https://evil.example/x."
    )
    apply(github, answering, answer)
    (body,) = github.answers_posted(5)
    assert "@someone" not in body.replace("`@someone`", "")
    assert "<b>" not in body
    assert "](https://evil.example" not in body and "https://evil.example" in body


def test_an_answer_with_something_like_a_secret_is_never_posted(answering):
    github = with_discussions(answering, make_discussion())
    secret = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2
    with pytest.raises(assistant.AssistantError, match="looks like a token"):
        apply(github, answering, make_discussion_answer(answer=f"Use {secret}."))
    assert github.writes_made() == []


@pytest.mark.parametrize(
    ("kind", "form"),
    [
        ("bug", f"{WEB}/issues/new?template=bug_report.yml"),
        ("feature", f"{WEB}/issues/new/choose"),
    ],
)
def test_a_bug_or_a_feature_request_gets_the_form_of_an_issue(answering, kind, form):
    github = with_discussions(answering, make_discussion())
    apply(github, answering, make_discussion_answer(kind=kind))
    (body,) = github.answers_posted(5)
    assert f"please [open an issue]({form}), so that the maintainers can" in body


def test_a_repository_without_the_bug_form_lists_the_forms(answering, tmp_path):
    config = dataclasses.replace(answering, root=tmp_path)
    assert assistant.issue_form(config, "bug") == f"{WEB}/issues/new/choose"


def test_spam_gets_no_answer(answering):
    github = with_discussions(answering, make_discussion())
    assert apply(github, answering, make_discussion_answer(kind="spam")) == [
        "Discussion #5 looks like spam: no answer."
    ]
    assert github.writes_made() == []


def test_a_possible_vulnerability_is_not_discussed_in_public(answering):
    github = with_discussions(answering, make_discussion())
    answer = make_discussion_answer(
        kind="bug",
        security_report=True,
        answer="The token leaks in the log of the hub.",
        missing_information=["The log."],
    )
    apply(github, answering, answer)
    (body,) = github.answers_posted(5)
    assert "[!CAUTION]" in body and answering.policy in body
    for detail in ("leaks", "The log.", "open an issue", "Where to look", "mark it"):
        assert detail not in body
    # No issue number of its texts is looked up either.
    assert not any(call[0] == "GET" for call in github.calls)


@pytest.mark.parametrize(
    ("data", "alert"),
    [
        ("secret", "[!WARNING]\n> This discussion seems to show a secret"),
        ("personal", "[!NOTE]\n> This discussion seems to show personal data"),
    ],
)
def test_sensitive_data_gets_a_warning_on_top(answering, data, alert):
    github = with_discussions(answering, make_discussion())
    apply(github, answering, make_discussion_answer(sensitive_data=data))
    (body,) = github.answers_posted(5)
    assert body.split("\n\n")[2].startswith(f"> {alert}")


def test_a_category_without_answers_to_mark_gets_no_such_hint(answering):
    config = dataclasses.replace(answering, answer_categories=("General",))
    github = with_discussions(
        config, make_discussion(category="General", answerable=False)
    )
    apply(github, config)
    (body,) = github.answers_posted(5)
    assert "mark it as the answer" not in body


@pytest.mark.parametrize(
    ("discussion", "done"),
    [
        (
            make_discussion(closed=True),
            "Discussion #5 is closed or locked; nothing posted.",
        ),
        (
            make_discussion(locked=True),
            "Discussion #5 is closed or locked; nothing posted.",
        ),
        (
            make_discussion(isAnswered=True),
            "Discussion #5 has an answer or a maintainer's comment already; "
            "nothing posted.",
        ),
        (
            make_discussion(
                comments=[
                    discussion_comment(
                        "someone",
                        "Me too",
                        replies=[
                            discussion_reply(
                                "owner", "Fixed in 2.0.", association="OWNER"
                            )
                        ],
                    )
                ]
            ),
            "Discussion #5 has an answer or a maintainer's comment already; "
            "nothing posted.",
        ),
        (
            make_discussion(category="General"),
            "Discussion #5 is no longer in a category of [discussions] "
            "answer_categories; nothing posted.",
        ),
    ],
)
def test_the_answer_waits_for_nothing_that_happened_meanwhile(
    answering, discussion, done
):
    """The analysis takes minutes, and the discussion is read again before the
    answer is posted."""
    github = with_discussions(answering, discussion)
    assert apply(github, answering) == [done]
    assert github.writes_made() == []


def test_a_run_by_hand_answers_again_but_never_a_closed_discussion(answering):
    answered = make_discussion(
        isAnswered=True,
        category="General",
        comments=[discussion_comment("owner", "See the FAQ.", association="OWNER")],
    )
    github = with_discussions(answering, answered)
    assert apply(github, answering, repeat=True) == [
        "First answer on discussion #5: question."
    ]
    github = with_discussions(answering, make_discussion(closed=True))
    assert apply(github, answering, repeat=True) == [
        "Discussion #5 is closed or locked; nothing posted."
    ]


def test_a_deleted_discussion_gets_no_answer(answering):
    github = with_discussions(answering)
    assert apply(github, answering) == ["#5 is no discussion; nothing posted."]


def test_a_dry_run_shows_the_answer_and_writes_nothing(answering):
    github = with_discussions(answering, make_discussion())
    github.dry_run = True
    assert apply(github, answering) == ["First answer on discussion #5: question."]
    (preview,) = github.previews
    assert preview.startswith("<!-- issue-assistant:discussion-answer ")
    assert github.writes == ["comment on discussion #5"]
    assert github.writes_made() == []


# The commands


def test_the_event_command_plans_the_answer_of_a_new_discussion(
    monkeypatch, tmp_path, root
):
    with (root / assistant.SETTINGS / "config.toml").open(
        "a", encoding="utf-8"
    ) as file:
        file.write('\n[discussions]\nanswer_categories = ["Q&A"]\n')
    config = assistant.load_config(root, REPOSITORY)
    github = with_discussions(config, make_discussion())
    path = tmp_path / "event.json"
    path.write_text(json.dumps(discussion_event(make_discussion())), "utf-8")
    code, outputs, summary = run_main(
        monkeypatch,
        tmp_path,
        root,
        github,
        "event",
        GITHUB_EVENT_NAME="discussion",
        GITHUB_EVENT_PATH=str(path),
    )
    assert code == 0
    assert "issue=5\n" in outputs and f"mode={MODE}\n" in outputs
    assert "#5, next task discussion-answer" in summary
    raw = json.dumps(make_discussion_answer())
    code, _, summary = run_main(
        monkeypatch,
        tmp_path,
        root,
        github,
        "apply",
        "--issue",
        "5",
        "--mode",
        MODE,
        RESULT=raw,
        ENGINE="claude",
        GITHUB_SHA=REF,
        GITHUB_EVENT_NAME="discussion",
        GITHUB_EVENT_PATH=str(path),
    )
    assert code == 0 and "First answer on discussion #5: question." in summary
    assert len(github.answers_posted(5)) == 1


def test_a_fixture_holds_an_issue_and_no_discussion(monkeypatch, tmp_path, sample_root):
    github = with_discussions(assistant_config(), make_discussion())
    code, _, _ = run_main(
        monkeypatch,
        tmp_path,
        sample_root,
        github,
        "context",
        "--issue",
        "5",
        "--mode",
        MODE,
        ISSUE_ASSISTANT_FIXTURE="tests/fixtures/canary-issue.json",
    )
    assert code == 1
    assert github.calls == []


# The set-up


def test_install_writes_the_workflow_of_the_discussions_only_where_it_answers(
    tmp_path,
):
    target = tmp_path / assistant.WORKFLOWS / assistant.DISCUSSION_WORKFLOW
    # The starter config.toml answers Q&A.
    done = assistant.install(tmp_path, REF, "v1.0.0")
    assert f"wrote {assistant.WORKFLOWS / assistant.DISCUSSION_WORKFLOW}" in done
    assert target.is_file()
    settings = tmp_path / assistant.SETTINGS / "config.toml"
    starter = assistant.load_config(tmp_path, REPOSITORY)
    assert starter.answer_categories == ("Q&A",)
    # Without a category, a repository gets no such workflow, but keeps one that
    # it has up to date.
    settings.write_text(
        settings.read_text("utf-8").replace('answer_categories = ["Q&A"]', ""), "utf-8"
    )
    target.write_text("outdated", "utf-8")
    assistant.install(tmp_path, REF, "v1.0.1")
    assert "v1.0.1" in target.read_text("utf-8")
    target.unlink()
    done = assistant.install(tmp_path, REF, "v1.0.1")
    assert not target.exists()
    assert not any(assistant.DISCUSSION_WORKFLOW in line for line in done)


def test_check_wants_the_workflow_only_where_discussions_get_answers(config, root):
    where = assistant.WORKFLOWS / assistant.DISCUSSION_WORKFLOW
    assert not (root / where).exists()
    assert assistant.check_workflows(config) == []
    answering = dataclasses.replace(config, answer_categories=("Q&A",))
    assert assistant.check_workflows(answering) == [
        f"{where} is missing; {assistant.reinstall()}"
    ]
    # Once it is there, it equals its template, also without a category.
    (root / where).write_text("name: Mine\non: discussion\njobs: {}\n", "utf-8")
    assert any(
        "differs from the template" in line
        for line in assistant.check_workflows(config)
    )


def test_check_names_the_categories_that_answer_nothing(config):
    config = dataclasses.replace(config, answer_categories=("Q&A", "", "q&a", "Help"))
    problems = assistant.check_config(config)
    settings = assistant.SETTINGS / "config.toml"
    assert (
        f"{settings}: [discussions] answer_categories names '' which is no category"
        in problems
    )
    assert f"{settings}: [discussions] answer_categories names 'q&a' twice" in problems
    assert not any("'Help'" in line or "'Q&A'" in line for line in problems)


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_notice_names_the_categories_that_get_answers(config, language):
    engine = assistant.ENGINES["claude"]
    words = {
        "en": "Questions in Discussions** in Q&A, Help get",
        "de": "Fragen in den Discussions** in Q&A, Help bekommen",
    }
    answering = dataclasses.replace(config, answer_categories=("Q&A", "Help"))
    assert words[language] in assistant.notice(answering, engine, language)
    assert "Discussions**" not in assistant.notice(config, engine, language)
    assert "{categories}" not in assistant.notice(answering, engine, language)


def test_the_comments_of_a_discussion_read_as_the_rest_api_names_them():
    """Bots as name[bot], deleted accounts as ghost, and people as they are."""
    assert assistant.actor({"__typename": "Bot", "login": "renovate"}) == {
        "login": "renovate[bot]",
        "type": "Bot",
    }
    assert assistant.actor(None) == {"login": "ghost", "type": "User"}
    assert assistant.actor({"__typename": "User", "login": "asker"}) == {
        "login": "asker",
        "type": "User",
    }
    comment = make_comment(1, "bot", "<!-- issue-assistant:discussion-answer -->", 0)
    assert assistant.kind_of(comment) == MODE
