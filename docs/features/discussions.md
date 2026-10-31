---
description: How a new question in GitHub Discussions gets a first answer of the issue assistant, with the same checks as an analysis of an issue.
---

# Questions in Discussions

[← All features](README.md)

The issue chooser and `SUPPORT.md` send questions to GitHub Discussions, where nobody may answer for days. Where `[discussions] answer_categories` of `config.toml` names categories, such as `Q&A`, each new discussion there gets a first answer of the AI, from the workflow `discussion-assistant.yml`, which `install` writes then:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/how-a-new-discussion-gets-its-first-answer.dark.svg">
  <img src="../images/diagrams/how-a-new-discussion-gets-its-first-answer.svg" alt="How a new discussion gets its first answer: A new discussion starts discussion-assistant.yml. Its event job runs no AI and lets a discussion through only when its category is one of answer_categories, someone who doesn't maintain the repository opened it, and the day has room in max_per_day and max_per_author_per_day, which count new issues and discussions together. Then analyze lets the AI read the discussion, the issues and the other discussions with tools that only read, and answer with JSON. The apply job reads the discussion again and posts nothing when it is closed or locked, has an answer, or a maintainer commented first, or when the answer fails a check. Otherwise it posts one comment, which it never marks as the answer; a bug or a feature request also gets a link to the forms of the issues.">
</picture>

- **The same AI, the same checks.** The AI reads the discussion with its comments and their replies in `discussion.md`, each text in a fence of its own, as in `issue.md`, then the issues, also older ones that a search for the words of the question finds, and the other discussions. Its answer passes every check of an analysis: the schema, nothing like a secret, no mention, image, HTML or foreign link, and numbers of issues and discussions that stay links only where they exist. The footer names the AI and links the notice to reporters, which `install` writes with a line on the discussions.
- **One answer, never marked as the answer.** The assistant posts one comment and leaves marking it to the asker; in a category with answers, such as `Q&A`, its footer says that they can. A run again posts nothing more, and an answer that came too late isn't posted: when the discussion was closed or locked meanwhile, has an answer, a maintainer commented first, or it moved to another category. A maintainer answers again by hand under *Actions* > *Discussion assistant* > *Run workflow*, which starts as a dry run.
- **Bugs and feature requests belong in issues.** When the question reports a bug or asks for a feature, the answer says what the AI found, such as a workaround or what exists today, and links the bug report form or the list of the issue forms, so that the maintainers can track it. Spam gets no answer, and a possible security report only the pointer to private reporting.
- **Within the limits of the day.** Each new discussion of such a category counts toward `max_per_day` and `max_per_author_per_day` as a new issue does, in one count with the issues, also one that a limit kept from the AI. Discussions of maintainers and bots get no answer and never count.
- **Nothing changes without a category.** The default is `[]`, and the starter `config.toml` of `install` names `Q&A`. Only `discussion-assistant.yml` may write to discussions, so a repository that only moves the pins of its workflows keeps working as before; [decision 0018](../decisions/0018-answer-questions-in-discussions.md) says why.

The assistant doesn't follow up in discussions: the maintainers and the community take its questions from there.
