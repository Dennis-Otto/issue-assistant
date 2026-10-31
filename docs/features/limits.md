---
description: The limits on the AI runs that strangers start, with each setting, its default, how the runs count and what happens over a limit.
---

# Limits of the AI runs

[← All features](README.md)

Anyone can open an issue, and every run of the AI costs money or counts toward the usage limits of a subscription. So the runs that people who don't maintain the repository start have limits, which `[engine]` in `config.toml` sets.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/how-the-limits-decide-whether-the-ai-runs.dark.svg">
  <img src="../images/diagrams/how-the-limits-decide-whether-the-ai-runs.svg" alt="How the limits decide whether the AI runs: A new issue, a new discussion in a category of answer_categories, an answer of the reporter or a comment of the reporter after a release asks for a run of the AI. When a maintainer asks, or someone runs the workflow by hand, the AI runs. For everyone else, a comment after a release needs the AI to have read fewer than release_replies comments of the reporter since the release. Then the day needs room: fewer than max_per_day new issues, first answers to discussions, follow-ups and replies after a release in the 24 hours before. A new issue or discussion also needs its author to have opened fewer than max_per_author_per_day issues and discussions in those 24 hours. Over a limit, the maintainer takes over, and the run notes which limit was reached.">
</picture>

| Setting | Default | What it limits |
| --- | --- | --- |
| `max_per_day` | 20 | analyses of new issues, first answers to [discussions](discussions.md), follow-ups, replies after a release and new analyses that reporters ask for with `/assistant again` together, in the 24 hours before an event |
| `max_per_author_per_day` | 3 | analyses of the new issues and first answers to the new discussions of one person, in the same 24 hours |
| `release_replies` | 2 | comments of the reporter after a release closed their issue that the AI reads, again after each release |

- **Every new issue counts,** also one that a limit kept from the AI, and each counts only the issues opened before it. So a flood of issues at once, or issues made to keep the AI from answering, start no more runs than the limit, even while the first analyses still run. Issues and comments of maintainers and bots and runs by hand never count and are never limited.
- **A follow-up, a reply after a release and a reporter's `/assistant again` count as they start.** The event job records the run in the hidden marker of the assistant's own comment that the run answers: the questions of the analysis, the note about the release, or the analysis that the new one repeats. Only the assistant can change its comments, so a reporter who deletes their comments gets no more runs, and an edit notifies nobody. Answers that come in within the same few seconds can still pass the count together.
- **Over a limit, nothing is lost.** A new issue gets the labels of its form and `needs-triage`, as a maintainer's own issue does, and an answer of the reporter still ends the waiting. The run notes the limit and its setting, such as *Daily limit of AI runs reached (max_per_day = 20)*; to get an analysis anyway, run the workflow by hand.
- **The day moves on with every event.** A repository that usually gets more new issues a day than `max_per_day` gets analyses again only when fewer came in during the 24 hours before, so set it above the usual number. `0` lets only maintainers start the AI, by hand.

With the defaults, strangers can start about 20 runs a day, about $4 at the prices of the API for first analyses.
