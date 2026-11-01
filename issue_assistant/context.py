"""The context folder: all that the engine reads besides the checkout."""

from __future__ import annotations

import json
import re
import shutil
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .answer import schema_json
from .cleaning import comment_parts, one_line, strip_html
from .constants import BOT, DISCUSSION_MODES, MAX_TEXT, MODES
from .engines import ENGINES, IN_PROMPT, STRICT, Engine, engine_named
from .errors import AssistantError, GitHubError
from .github import GitHub
from .issues import (
    Visible,
    form_fields,
    is_digest,
    is_maintainer,
    judged_comment,
    marker,
    opted_out,
    text_of,
)
from .languages import COMMON_WORDS, LOCALES
from .learning import plural, render_learned
from .paths import CONTEXT, PROMPTS
from .patterns import HTML_COMMENT
from .settings import Config, label_names, limit, maintainers, siblings
from .values import command, fence, login

# The most of one text, the issue's or a comment's, that the engine reads in
# issue.md; a longer one is cut off with a note.
MAX_RENDERED_TEXT = 20_000

# The words of that search, joined by OR: GitHub's search takes five operators.
SEARCH_TERMS = 6
# The names of errors among them, before the words of the title.
SEARCH_ERRORS = 3
# The longest word of it, so that the query stays within GitHub's 256 characters.
SEARCH_WORD = 40
# The excerpt of each other issue in issues.jsonl; issues/<number>.md has its text.
EXCERPT = 400

# The tasks that judge one comment, the one that started them, and read nothing of
# the repository's issues besides their own.
REPLIES = ("maintainer-reply", "release-reply", "maintainer-question")

# The words of a text, which a search for an issue is made of.
WORD = re.compile(r"\w+")
# The code of a system error, such as ECONNREFUSED or ENOENT.
ERROR_CODE = re.compile(r"E[A-Z]{3,}")


def excerpt(text: str, limit: int) -> str:
    """The text of an issue in short: form answers as lines, without empty answers.

    The whole text is read, because comments and empty answers can come before the
    words that count, such as the instructions of an issue template; each step takes
    time linear in its length.
    """
    text = (text or "")[:MAX_TEXT]
    fields = form_fields(text)
    if fields:
        text = "\n".join(f"{key}: {value}" for key, value in fields.items() if value)
    comments, rest = comment_parts(text)
    text = HTML_COMMENT.sub("", comments) + rest
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def describe_author(item: dict[str, Any], reporter: str, role: str = "reporter") -> str:
    """Who wrote an issue or a comment, with their roles: `role` for `reporter`,
    who opened the issue or the discussion, and maintainer."""
    name = login(item.get("user"))
    if name == BOT and marker(item):
        return "the issue assistant (you, earlier)"
    roles = []
    if name == reporter:
        roles.append(role)
    if is_maintainer(item):
        roles.append("maintainer")
    return f"@{name}" + (f" ({', '.join(roles)})" if roles else "")


def render_issue(
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
    visible: Visible | None = None,
) -> str:
    """issue.md: the issue and its comments, each text in a code fence of its own.

    Who wrote a text, in which role and when stands only in the headings and lines
    that the script writes outside the fences, and nothing in a text can close its
    fence, so no text can pass for another comment, for the maintainer or for the
    assistant.
    """
    visible = visible or Visible()
    reporter = login(issue.get("user"))
    lines = [
        f"# Issue #{issue['number']}: {one_line(visible(issue['title']))}",
        "",
        f"- Reporter: {describe_author(issue, reporter)}",
        f"- State: {issue['state']}",
        f"- Labels: {', '.join(sorted(label_names(issue))) or 'none'}",
        f"- Opened: {issue['created_at'][:10]}",
        "",
        "## Text of the issue",
        "",
        rendered_text(text_of(issue), visible),
    ]
    for index, comment in enumerate(comments, start=1):
        lines += [
            "",
            f"## Comment {index} by {describe_author(comment, reporter)}, "
            f"{comment['created_at'][:10]}",
            "",
            rendered_text(text_of(comment), visible),
        ]
    return "\n".join(lines) + "\n"


def render_discussion(
    discussion: dict[str, Any], visible: Visible | None = None
) -> str:
    """discussion.md: the discussion, its comments and their replies, each text in a
    code fence of its own, as issue.md shows an issue, so that no text can pass for
    another one, for a maintainer or for the assistant."""
    visible = visible or Visible()
    author = login(discussion.get("user"))

    def by(item: dict[str, Any]) -> str:
        return f"{describe_author(item, author, 'author')}, {item['created_at'][:10]}"

    state = discussion["state"] + (", answered" if discussion["answered"] else "")
    lines = [
        f"# Discussion #{discussion['number']}: "
        f"{one_line(visible(discussion['title']))}",
        "",
        f"- Author: {describe_author(discussion, author, 'author')}",
        f"- Category: {one_line(visible(discussion['category']['name']))}",
        f"- State: {state}",
        f"- Opened: {discussion['created_at'][:10]}",
        "",
        "## Text of the discussion",
        "",
        rendered_text(text_of(discussion), visible),
    ]
    for index, comment in enumerate(discussion["comments"], start=1):
        lines += [
            "",
            f"## Comment {index} by {by(comment)}",
            "",
            rendered_text(text_of(comment), visible),
        ]
        for number, reply in enumerate(comment["replies"], start=1):
            lines += [
                "",
                f"### Reply {number} to comment {index} by {by(reply)}",
                "",
                rendered_text(text_of(reply), visible),
            ]
    later = discussion["comment_count"] - len(discussion["comments"])
    if later > 0:
        lines += ["", f"({plural(later, 'later comment')} not shown.)"]
    return "\n".join(lines) + "\n"


def rendered_text(text: str, visible: Visible) -> str:
    """A text for issue.md: without HTML and invisible characters, cut off after
    MAX_RENDERED_TEXT, and in a code fence that nothing in it can close.

    The HTML goes first, as GitHub hides it: an invisible character in the end of
    an HTML comment keeps it from ending on GitHub, and taken out first, it would end
    the comment and show the text after it to the engine. The invisible characters
    go before the fence is chosen, so that none can split a run of backticks that
    would close it.
    """
    text = visible(strip_html(text)).strip()
    if not text:
        return "(empty)"
    note = ""
    if len(text) > MAX_RENDERED_TEXT:
        text, note = text[:MAX_RENDERED_TEXT].rstrip(), "\n\n(shortened)"
    marks = fence(text)
    return f"{marks}\n{text}\n{marks}{note}"


def render_labels(config: Config) -> str:
    lines = ["# Labels you may choose", ""]
    groups = [("kind", "type"), ("areas", "area"), ("topics", "topic")]
    # Priorities are a choice of the repository: without them, nothing asks for one.
    if config.group("priority"):
        groups.append(("priority", "priority"))
    for title, name in groups:
        lines += [f"## {title}", ""]
        lines += [
            f"- `{label.name}`: {label.description}"
            for label in config.labels
            if label.group == name
        ] or ["- none"]
        lines.append("")
    lines.append('A kind of "spam" marks advertising or nonsense as invalid.')
    return "\n".join(lines) + "\n"


def render_judged(
    issue: dict[str, Any],
    comments: list[dict[str, Any]],
    judged: dict[str, Any] | None,
) -> str:
    """comment.md: the comment that a reply task judges, as issue.md shows it."""
    if judged is None:
        return (
            "# No comment to judge\n\n"
            "The comment that this task judges was deleted, or there is none.\n"
        )
    index = next(
        number for number, item in enumerate(comments, start=1) if item is judged
    )
    author = describe_author(judged, login(issue.get("user")))
    return (
        f"# Comment {index} of issue #{issue['number']} by {author}, "
        f"{judged['created_at'][:10]}\n\n"
        f"{rendered_text(text_of(judged), Visible())}\n"
    )


def search_terms(issue: dict[str, Any]) -> list[str]:
    """The words of the search for older issues like this one: the names of errors in
    its text, such as TypeError or ECONNREFUSED, then the words of its title.

    Words alone, in lower case, so that none is a qualifier of the search, such as
    repo:, or one of its operators, such as OR; none longer than SEARCH_WORD, so
    that the query stays short enough for GitHub.
    """
    errors = [
        word
        for word in WORD.findall(text_of(issue))
        if word.endswith(("Error", "Exception")) or ERROR_CODE.fullmatch(word)
    ]
    words = list(dict.fromkeys(errors))[:SEARCH_ERRORS] + WORD.findall(issue["title"])
    terms = [
        word.lower()
        for word in words
        if 2 < len(word) <= SEARCH_WORD
        and not word.isdigit()
        and word.lower() not in COMMON_WORDS
    ]
    return list(dict.fromkeys(terms))[:SEARCH_TERMS]


def related_issues(
    github: GitHub, issue: dict[str, Any], mode: str, config: Config
) -> list[dict[str, Any]]:
    """The other issues that the engine reads, the newest first: every open one, the
    max_issues closed ones that changed last and, for a first analysis, those that a
    search for its words finds among the older ones; none whose reporter keeps the AI
    out of it."""
    found = {
        item["number"]: item
        for item in github.recent_issues(limit(config, "max_issues"))
    }
    terms = search_terms(issue) if mode in {"triage", *DISCUSSION_MODES} else []
    if terms:
        try:
            for item in github.search_issues(terms):
                found.setdefault(item["number"], item)
        except GitHubError as error:
            # The issues above are the context; the search only adds to them.
            command("warning", f"The search for older issues failed: {error}")
    found.pop(issue["number"], None)
    return [
        found[number]
        for number in sorted(found, reverse=True)
        if not is_digest(found[number]) and not opted_out(found[number], config)
    ]


def render_other_issue(
    item: dict[str, Any], visible: Visible, repository: str = ""
) -> str:
    """issues/<number>.md: the whole text of another issue, for the engine to read
    when its excerpt in issues.jsonl looks promising. For an issue of a related
    repository the heading names that repository, so its number is never taken for
    one of this repository."""
    return (
        f"# Issue {repository}#{item['number']}: "
        f"{one_line(visible(item['title']))}\n\n"
        f"{rendered_text(text_of(item), visible)}\n"
    )


def issue_line(
    item: dict[str, Any], visible: Visible, **more: object
) -> dict[str, Any]:
    """The line of an issue in issues.jsonl and related-issues.jsonl, with `more`
    before its excerpt."""
    return {
        "number": item["number"],
        "title": visible(item["title"]),
        "state": item["state"],
        "state_reason": item.get("state_reason"),
        "labels": sorted(label_names(item)),
        "opened": item["created_at"][:10],
        "closed": (item.get("closed_at") or "")[:10] or None,
        "comments": item.get("comments", 0),
        **more,
        "excerpt": visible(excerpt(item.get("body") or "", EXCERPT)),
    }


def write_lines(path: Path, items: Iterable[dict[str, Any]]) -> None:
    """A file of JSON Lines: one JSON object on each line."""
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items),
        encoding="utf-8",
    )


# What a context writes besides rules.md, prompt.md and schema.json, each only for
# the tasks that need it. A run in the same checkout removes what an earlier one
# wrote, so that a reply task never reads the other issues of an analysis, and the
# answer to a discussion never the issue of an earlier task.
CONTEXT_FILES = (
    "issue.md",
    "discussion.md",
    "comment.md",
    "labels.md",
    "issues.jsonl",
    "discussions.jsonl",
    "corrections.md",
    "related-issues.jsonl",
)


def language_list() -> str:
    """The languages of the catalogs, as the rules of the prompt list them for
    `{languages}`: the code of each, its name and how it addresses a reporter."""
    return "\n".join(
        f'  - `"{code}"`: {locale.name}'
        + (
            f', addressing the reporter with "{locale.address}"'
            if locale.address
            else ""
        )
        for code, locale in LOCALES.items()
    )


def write_context(
    github: GitHub,
    number: int,
    mode: str,
    config: Config,
    comment_id: int | None = None,
) -> dict[str, str]:
    """Write the files that the task of the engine reads and return the prompts and
    the schema: the task, which the rules of rules.md precede as the engine's system
    prompt, and both in one prompt for a template that passes no system prompt.

    Every task of an issue reads the issue with its comments. A reply task judges
    one comment, `comment_id`, the one whose event started the run, and reads
    nothing else; the others read the labels, the other issues and the discussions,
    up to the numbers of [context], and a first analysis also what the maintainers
    corrected of the earlier analyses. The first answer to a discussion reads the
    discussion with its comments, the issues, also those that a search for its
    words finds, and the other discussions.
    """
    if mode not in MODES:
        raise AssistantError(f"Unknown mode {mode}.")
    folder = config.root / CONTEXT
    if mode in DISCUSSION_MODES:
        found = github.discussion(number)
        if found is None:
            raise AssistantError(f"#{number} is no discussion.")
        issue, comments = found, []
    else:
        issue, comments = github.issue(number), github.comments(number)
    folder.mkdir(parents=True, exist_ok=True)
    for texts in ("issues", "related"):
        shutil.rmtree(folder / texts, ignore_errors=True)
    for name in CONTEXT_FILES:
        (folder / name).unlink(missing_ok=True)
    # The invisible characters of the issue, and of the texts that every analysis
    # reads, in which anyone could plant instructions for later issues. They go after
    # the HTML comments, as in rendered_text, so that the engine reads no text that
    # GitHub hides.
    own, elsewhere = Visible(), Visible()
    if mode in DISCUSSION_MODES:
        # The discussion and what an analysis reads of the repository; no labels,
        # which a discussion doesn't get.
        (folder / "discussion.md").write_text(
            render_discussion(issue, own), encoding="utf-8"
        )
        write_related(github, folder, issue, mode, config, elsewhere)
    else:
        (folder / "issue.md").write_text(
            render_issue(issue, comments, own), encoding="utf-8"
        )
        if mode in REPLIES:
            judged = judged_comment(issue, comments, mode, comment_id)
            (folder / "comment.md").write_text(
                render_judged(issue, comments, judged), encoding="utf-8"
            )
        else:
            (folder / "labels.md").write_text(render_labels(config), encoding="utf-8")
            others = write_related(github, folder, issue, mode, config, elsewhere)
            if mode == "triage":
                (folder / "corrections.md").write_text(
                    render_learned(github, config, issue, comments, others),
                    encoding="utf-8",
                )
                write_siblings(github, folder, issue, config, elsewhere)
    if own.removed or elsewhere.removed:
        subject = "discussion" if mode in DISCUSSION_MODES else "issue"
        command(
            "notice",
            f"Removed {own.removed + elsewhere.removed} invisible characters, such "
            "as zero-width spaces and Unicode tags, from the texts for the engine: "
            f"{own.removed} from {subject} #{number}, {elsewhere.removed} from other "
            "issues and discussions.",
        )
    values = {
        "repository": config.repository,
        "issue": str(number),
        "context": str(CONTEXT),
        "max_issues": str(config.max_issues),
        "max_discussions": str(config.max_discussions),
        "max_analyzed": str(config.max_analyzed),
        "languages": language_list(),
        "maintainers": LOCALES[maintainers(config)].name,
        "maintainers_code": maintainers(config),
        "priority": PRIORITY_RULE if config.group("priority") else "",
        "newcomer": NEWCOMER_RULE if config.group("contribution") else "",
        **related_rules(config),
    }

    def render(name: str) -> str:
        text = (PROMPTS / name).read_text(encoding="utf-8")
        for key, value in values.items():
            text = text.replace("{" + key + "}", value)
        if "{release}" in text:
            # Only the tasks that weigh what a release changed ask GitHub for it.
            text = text.replace("{release}", github.latest_release() or "none yet")
        # Last, so that braces in the project's own text stay as they are.
        return text.replace("{project}", config.project)

    engine = ENGINES[engine_named(config.engine)]
    rules, task = render("rules.md"), render(f"{mode}.md")
    if engine.schema == IN_PROMPT:
        # An engine that can't enforce the schema reads it in the folder.
        task = f"{task.rstrip()}\n\n{render('answer.md')}"
    schema = schema_json(mode, config, strict=engine.schema == STRICT)
    # The rules come before everything else as the engine's system prompt, apart
    # from its task and from the texts of GitHub that it reads in the folder.
    (folder / "rules.md").write_text(rules, encoding="utf-8")
    (folder / "prompt.md").write_text(task, encoding="utf-8")
    (folder / "schema.json").write_text(schema, encoding="utf-8")
    return {
        "prompt": f"{rules}\n\n{task}",
        "task": task,
        "schema": schema,
        "engine-args": engine_arguments(engine, rules),
    }


def toml_string(text: str) -> str:
    """The text as a basic string of TOML, which takes no control character as it is."""
    escapes = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}
    return (
        '"'
        + "".join(
            escapes.get(character)
            or (
                f"\\u{ord(character):04x}"
                if unicodedata.category(character) == "Cc"
                else character
            )
            for character in text
        )
        + '"'
    )


def engine_arguments(engine: Engine, rules: str) -> str:
    """The arguments that the engine's action adds to its command line, as JSON,
    with the rules as a string of TOML; empty for an engine without any."""
    if not engine.action_arguments:
        return ""
    rules_toml = toml_string(rules)
    return json.dumps(
        [
            argument.replace("{rules}", rules_toml)
            for argument in engine.action_arguments
        ]
    )


# How the AI chooses the priority, in the step of the labels of an analysis and of a
# follow-up; only where labels.toml has priorities, as the schema of the answer.
PRIORITY_RULE = (
    " `priority` is the one of the priorities in `labels.md` whose description fits "
    "the issue best, judged by what the issue shows: how many people it affects, how "
    "badly, and whether there is a workaround; `priority_rationale` says why in one "
    'sentence. Choose `"none"` when the issue shows too little to judge.'
)

NEWCOMER_RULE = (
    "\n   - `newcomer`: whether someone new to the project could take this on. "
    "`fit` is true only for a confirmed, small, local change: the issue shows what is "
    "wrong or what to write, your investigation located it in the files that you name "
    "in `references`, and it takes no knowledge of the whole project, such as a bug "
    "with a located cause or a fix of the documentation. It is false for a security "
    "report, a duplicate, an open design question and anything that still needs "
    "information from the reporter or a decision of a maintainer, and whenever you "
    "doubt. `reason` says why in one sentence, in the language that the maintainers "
    "read, since nobody else reads it. For a security report, `fit` is false and "
    "`reason` stays empty, because the log of the run can be public. The maintainers "
    "alone decide what to offer to newcomers, so this field changes nothing that "
    "anyone sees: don't mention "
    "it in your other texts, and don't invite anyone to work on the issue."
)


def write_related(
    github: GitHub,
    folder: Path,
    issue: dict[str, Any],
    mode: str,
    config: Config,
    visible: Visible,
) -> list[dict[str, Any]]:
    """What the engine reads of the rest of the repository: the other issues, each
    with an excerpt in issues.jsonl and its whole text in issues/<number>.md, and
    the other discussions in discussions.jsonl. Returns the other issues.

    `visible` counts the invisible characters of the whole texts; the titles and
    excerpts of issues.jsonl come from the same texts and don't count them again.
    """
    others = related_issues(github, issue, mode, config)
    texts = folder / "issues"
    texts.mkdir()
    for item in others:
        (texts / f"{item['number']}.md").write_text(
            render_other_issue(item, visible), encoding="utf-8"
        )
    quiet = Visible()
    write_lines(
        folder / "issues.jsonl",
        (issue_line(item, quiet, closed_by=item.get("closed_by")) for item in others),
    )
    write_lines(
        folder / "discussions.jsonl",
        (
            {
                "number": item["number"],
                "title": visible(item["title"]),
                "category": (item.get("category") or {}).get("name"),
                "answered": item.get("isAnswered"),
                "closed": item.get("closed"),
                "excerpt": visible(excerpt(item.get("body") or "", 1000)),
                "answer": visible(
                    excerpt((item.get("answer") or {}).get("body") or "", 1000)
                )
                or None,
            }
            for item in github.discussions(limit(config, "max_discussions"))
            if item["number"] != issue["number"]
        ),
    )
    return others


def write_siblings(
    github: GitHub,
    folder: Path,
    issue: dict[str, Any],
    config: Config,
    visible: Visible,
) -> None:
    """related-issues.jsonl, for a first analysis in a repository with related
    repositories: the issues of each that GitHub's search finds for the words of the
    issue, each with its repository and an excerpt, and its whole text in
    related/<owner>/<name>/<number>.md.

    Only public repositories count, as GitHub.sibling says. The search adds to the
    context, as the one for older issues does, so a repository that GitHub fails to
    search is a warning.
    """
    names = siblings(config)
    if not names:
        return
    terms = search_terms(issue)
    found: list[tuple[str, dict[str, Any]]] = []
    for repository in names if terms else []:
        try:
            other = github.sibling(repository)
            if other is None:
                command(
                    "warning",
                    f"[related] {repository} is no public repository, so the "
                    "analysis reads none of its issues.",
                )
                continue
            found += [
                (repository, item)
                for item in other.search_issues(terms)
                if not is_digest(item) and not opted_out(item, config)
            ]
        except GitHubError as error:
            command("warning", f"The search in {repository} failed: {error}")
    for repository, item in found:
        texts = folder / "related" / repository
        texts.mkdir(parents=True, exist_ok=True)
        (texts / f"{item['number']}.md").write_text(
            render_other_issue(item, visible, repository), encoding="utf-8"
        )
    quiet = Visible()
    write_lines(
        folder / "related-issues.jsonl",
        ({"repository": name, **issue_line(item, quiet)} for name, item in found),
    )


def related_rules(config: Config) -> dict[str, str]:
    """What the task of a first analysis says of the related repositories, for
    `{related_files}` and `{related_rule}`; nothing without any."""
    names = siblings(config)
    if not names:
        return {"related_files": "", "related_rule": ""}
    listed = ", ".join(f"`{name}`" for name in names)
    which = (
        f"repository {listed}, which shares"
        if len(names) == 1
        else f"repositories {listed}, which share"
    )
    return {
        "related_files": (
            f"\n- `{CONTEXT}/related-issues.jsonl`: the issues of the related {which} "
            "users and topics with this one, that a search for the words of this "
            "issue found there, in the form of "
            "`issues.jsonl` with the `repository` of each. "
            f"`{CONTEXT}/related/<owner>/<name>/<number>.md` holds the whole text of "
            "each of them."
        ),
        "related_rule": (
            "\n   - Search `related-issues.jsonl` the same way. An issue there can be "
            "`related`, with its `repository`, but never one of the `duplicates`, "
            "which are issues of this repository alone. The other items of "
            f"`related` have the `repository` `{config.repository}`. Refer to an "
            "issue of a related repository as owner/name#123 in your texts."
            "\n   - `belongs_in`: the related repository that the issue is about, "
            "when it was clearly filed in the wrong repository, such as a problem "
            "in the code or the documentation of that project, and `analysis` says "
            f"why; otherwise `{config.repository}`. Nothing moves the issue: the "
            "maintainers decide."
        ),
    }
