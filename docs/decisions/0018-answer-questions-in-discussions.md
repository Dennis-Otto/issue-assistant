# A first answer in Discussions, from a workflow of its own that alone may write there

- Status: accepted
- Date: 2026-10-18

## Context

The issue chooser and `SUPPORT.md` of the repositories send questions to GitHub Discussions, where they wait for a maintainer, while the assistant answers only issues. Comparable tools, such as Dosu and Copilot's triage, answer in Discussions too. The analysis of an issue already reads the discussions as context, and everything it writes passes the checks of `apply`; a first answer to a question needs the same AI, the same checks and one more place to post.

Posting a comment on a discussion needs the permission `discussions: write`. The jobs of the issues are a reusable workflow that each repository calls at the commit of a release ([decision 0015](0015-reusable-workflows.md)), and a release reaches most repositories as a move of that pin alone. A called workflow whose job asks for more than its caller grants doesn't start at all, and the templates so far grant discussions only reading. A job of the issues that asked for writing would stop every analysis of a repository that moved its pin without installing the templates again.

An answer in public is new for the repositories, and every run of the AI costs money or counts toward a subscription, which a flood of discussions could use up as a flood of issues could ([decision 0009](0009-limit-the-ai-runs.md)).

## Options

1. The issues' workflow gets the trigger `discussion` and its `apply` job `discussions: write`. One workflow for all, but a move of the pin breaks the analyses of every repository whose template grants only reading, until it runs `install` again.
2. A workflow of its own for the discussions, `discussion-assistant.yml`, which calls `reusable-discussion-assistant.yml`: the same job `analyze`, between an `event` job that only reads and the only `apply` job that may write to discussions. A repository gets it with `install` once it names categories, and the workflow of the issues never asks for more than reading.
3. Answers in Discussions by a GitHub App of the project. Its token could write anywhere the App is installed, and the repositories would trust a service instead of their own workflows.

## Decision

Option 2. `[discussions] answer_categories` of `config.toml` names the categories, in any case, whose new discussions get a first answer; the default is none, and the starter of `install` names `Q&A`. `install` writes the workflow only where a category is named, or keeps it current where it is there, and `check` wants it while one is named and keeps it equal to its template whenever it is there. Its job `analyze` is the one of the issues, which a test keeps equal, so every rule that keeps the engine to reading holds for both, and an engine is added in both. Each new discussion of such a category counts toward `max_per_day` and `max_per_author_per_day` as a new issue does, in one count with them: issues and discussions share their numbers, so each counts those opened before it.

`apply` reads the discussion again, posts one comment with `addDiscussionComment` and never marks it as the answer, which is the asker's choice. It posts nothing when the discussion was closed or locked meanwhile, has an answer, a maintainer commented first, or it moved to a category without answers; a run by hand of a maintainer answers anyway. A bug or a feature request gets what the AI found and a link to the form of an issue, spam no answer, and a possible security report only the pointer to private reporting.

## Consequences

The analyze job is written twice, in the reusable workflows of the issues and of the discussions, and a test fails until both are equal. A repository that opts in runs `install` again once and gets a fifth workflow, which starts for every new discussion and ends in its event job for the categories that get no answer. The event job of the issues reads the discussions of the day for the limits, with `discussions: read`, which the templates grant already, and only while a category is named. The assistant doesn't follow up in discussions yet: the event job can't record a run in the marker of a comment there without writing to discussions, which only `apply` may, so a follow-up within the limits needs a record of its own first. The notice to reporters that `install` writes names the categories, and the page *What reporters see* shows a first answer, one to a bug and one to a feature request.
