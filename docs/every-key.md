---
description: Every key of the settings of the issue assistant in labels.toml, config.toml and findings.toml, its type, its default and what check makes sure of.
---

# Every key

[← Configuration](configuration.md)

Each key of the three files, its type, its default and what `check` makes sure of. A key that says *required* has no default. Besides, `check` makes sure that every value has the type of its key, and fails on every key that these tables don't have, naming the key that was probably meant, such as `form_options` for `form_option`.

## labels.toml

Each `[[label]]` of `.github/labels.toml` is a label of the repository:

<details markdown="1">
<summary>Each key of labels.toml</summary>

| Key | Type | Default | What it does | What `check` makes sure of |
| --- | --- | --- | --- | --- |
| `name` | string | required | The name of the label | 1 to 50 characters, as GitHub allows, and no other label has it, in any case: GitHub takes `bug` and `Bug` for one label |
| `color` | string | required | Its colour, six hexadecimal digits without `#` | Six digits, such as `d73a4a` |
| `description` | string | required | What the label means; GitHub shows it with the label | 1 to 100 characters |
| `group` | string | required | `type`, `area`, `topic`, `priority`, `lifecycle`, `decision`, `contribution`, `release` or `dependabot`. The AI chooses one label of the group `type`, the kind of the issue, any of `area` and `topic`, and, where the repository has labels of the group `priority`, the priority of an issue that has none. Where it has labels of the group `contribution`, which only a maintainer sets, the first analysis also says whether [someone new to the project could take the issue](features/good-first-issues.md) | One of these groups, at least one label of the group `type`, and no priority named `none`, which the AI answers when it can't judge |
| `form_options` | list of strings | `[]` | The answers of the area field of the issue forms that choose the label; of a dropdown with `multiple: true`, each chosen answer adds its label | Only kinds, areas and topics have them; each is an option of a form and belongs to one label alone |
| `ask` | boolean | `true` | For a kind: whether the AI may ask its reporters for information | It is `true` or `false` |
| `owners` | list of strings | `[]` | For an area: the GitHub logins that a new issue of the area is assigned to, when nobody is assigned to it. GitHub notifies them; the assistant mentions nobody | Only areas have them; each is a login, without `@`, and there are at most 10, as many as GitHub assigns to one issue. The Labels workflow warns of a login that can't be assigned |
| `issue_type` | string | `""` | For a kind: the [issue type](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/managing-issue-types-in-an-organization) of the organization that issues of the kind get, such as `Bug` | Only kinds have one, without spaces at its start or end. The Labels workflow warns of a type that the repository doesn't have |

</details>

The group `lifecycle` needs the seven labels that the assistant sets: `needs-triage`, `needs-info`, `stale`, `possible-duplicate`, `duplicate`, `invalid` and `fixed-in-next-release`, the last one only while `close_with_release` is `true`.

## config.toml

`.github/issue-assistant/config.toml` is optional; without it, every key has its default.

| Key | Type | Default | What it does | What `check` makes sure of |
| --- | --- | --- | --- | --- |
| `[engine] name` | string | `"claude"` | The AI engine that writes the analysis, as [Engines](engines.md) compares them | An engine of the assistant: `claude`, `codex`, `copilot` or `gemini` |
| `[engine] model` | string | `""` | The model; empty for the engine's default: `claude-opus-5-5` for Claude, `gpt-6.1-sol` for Codex, `auto` for Gemini and `auto` for Copilot, with which the CLI chooses | A model name: a letter or a digit, then letters, digits and `._:@/[]-` |
| `[engine] light_model` | string | `""` | The model of the [replies to one comment](features/cost.md), which only judge it, yes or no: whether a maintainer's comment waits for the reporter, and whether a problem persists after the release; empty for `model` | A model name, as for `model` |
| `[engine] effort` | string | `""` | How much the model thinks, for the models that take it; empty for the model's default | `low`, `medium`, `high`, `xhigh` or `max`, and none for Gemini, which takes no effort |
| `[engine] max_turns` | integer | `40` | The most turns of a first analysis and of a maintainer's question; a follow-up gets half of them, and a reply to one comment a tenth, each rounded up and at least 4 (or `max_turns` when it is fewer), which a reply needs. Claude and Gemini stop at them; Codex and Copilot only at the time of the job | A whole number, 1 to 200 |
| `[engine] max_budget_usd` | number | `1.00` | The most dollars that one run of the engine may cost, as the engine counts them at the prices of its API; once a run has spent this sum it stops without an answer, so a run can go past it by the cost of its last turn, and the summary of the job says so. Only Claude counts dollars; the others stop at the time of the job | A number, more than 0 and at most 100 |
| `[engine] max_per_day` | integer | `20` | The most [AI runs](features/limits.md) that people who don't maintain the repository start in 24 hours: analyses of new issues, follow-ups and replies after a release; `0` lets only maintainers start the AI, by hand | A whole number, 0 or more |
| `[engine] max_per_author_per_day` | integer | `3` | The most analyses of one reporter's new issues in 24 hours | A whole number, 0 or more |
| `[engine] release_replies` | integer | `2` | How many comments of the reporter the AI reads after each release that closed their issue | A whole number, 0 or more |
| `[context] max_issues` | integer | `1000` | How many closed issues an analysis and a follow-up read besides every open one: those that changed last; see [what the AI reads](features/what-the-ai-reads.md) | A whole number, 0 or more |
| `[context] max_discussions` | integer | `1000` | How many discussions an analysis and a follow-up read: those that changed last; `0` reads none | A whole number, 0 or more |
| `[context] max_analyzed` | integer | `30` | How many of the issues analyzed last a first analysis [learns from](features/what-the-ai-reads.md): the labels that the maintainers removed or added, the duplicates that someone objected to and the analyses that a maintainer gave a 👎; `0` learns from none | A whole number, 0 or more |
| `[related] repositories` | list of strings | `[]` | Public repositories that share users and topics with this one, as `owner/name`: a first analysis also searches their issues, links those that help, and says when an issue may belong in one of them, which a maintainer then transfers by hand; see [related repositories](features/related-repositories.md) | Each is a repository, `owner/name`, other than this one, and named once; at most 5 |
| `[discussions] answer_categories` | list of strings | `[]` | The categories of GitHub Discussions, such as `Q&A`, whose new discussions get a [first answer](features/discussions.md) of the AI, in any case, once `.github/workflows/discussion-assistant.yml` is there, which `install` writes then; the starter names `Q&A`, the [chooser](getting-started.md#what-install-writes) of a new set-up links the first, and `[]` answers no discussion | Each names a category, once in any case; with any, the workflow of the discussions is there and equals its template |
| `[maintainers] language` | string | `"en"` | The language that the maintainers read: an analysis, a follow-up or a first answer in Discussions that the AI writes in another language, such as Japanese, folds a [summary for the maintainers](features/languages.md) in this one below its text | The code of a catalog in `locales/`, such as `de` or `en`; `check` lists the known ones |
| `[links] hosts` | list of strings | `[]` | The hosts that links in the comments may lead to, besides the repository itself | Each is a host name, such as `docs.example.com` |
| `[forms] area_field` | string | `"Area"` | The dropdown of the issue forms whose answer chooses an area label | Each of its options belongs to one label, or to `unmapped_options` |
| `[forms] unmapped_options` | list of strings | `["Other"]` | Answers of that dropdown that choose no label | A list of strings |
| `[transparency] files` | list of strings | `["SUPPORT.md"]` | The files that tell reporters which AI reads their issue; the footer of every analysis and follow-up links the first, at its heading *What happens with your issue* when it has one | Each exists and names the engine and its vendor: Claude and Anthropic, Codex and OpenAI, Gemini and Google, or Copilot and GitHub |
| `[transparency] credit` | boolean | `true` | Whether the footer of every analysis, follow-up, answer and first answer in Discussions ends with a link to the [issue assistant](https://github.com/Dennis-Otto/issue-assistant), so that a reader can find the tool that wrote it; `false` leaves the link out | It is `true` or `false` |
| `[releases] close_with_release` | boolean | `true` | Whether a fixed issue stays open until a release ships the fix; `false` for a repository without releases | It is `true` or `false` |
| `[lifecycle] remind_after_days` | integer | `15` | The days after the last question without an answer until the reminder, which names the day the issue closes | 1 to 3650, and the descriptions of `needs-info` and `stale` in `labels.toml` name no other days |
| `[lifecycle] close_after_days` | integer | `30` | The days after the last question until an issue without an answer closes as not planned, on the day that the reminder named | More than `remind_after_days`, up to 3650 |
| `[lifecycle] duplicate_grace_days` | integer | `3` | The days after the notice of a sure duplicate until it closes, unless someone objects | 1 to 3650, and the description of `possible-duplicate` names no other days |
| `[lifecycle] reopen_after_release_days` | integer | `30` | The days after a release closed an issue in which a comment of its reporter that the problem persists reopens it | 1 to 3650 |
| `[lifecycle] max_follow_ups` | integer | `2` | How often the AI follows up on the reporter's answers to its questions; `0` for never | 0 to 3650 |
| `[lifecycle] exempt_labels` | list of strings | `["pinned", "security"]` | Issues with one of these labels, in any case, are never reminded, closed as unanswered or as a duplicate, or locked | A list of strings |
| `[lifecycle] exempt_assignees` | boolean | `false` | Whether issues assigned to someone are exempt as well | It is `true` or `false` |
| `[lifecycle] exempt_upvotes` | integer | `0` | Issues with at least this many 👍 are exempt as well; `0` exempts none for their 👍 | 0 to 3650 |
| `[lifecycle] lock_closed_after_days` | integer | `0` | The sweep locks issues closed for more than this many days as resolved, with one line that points to a new issue, at most 25 in a sweep; `0` locks none | 0, or at least `reopen_after_release_days` and up to 3650 |
| `[digest] issue` | boolean | `false` | Whether the morning sweep also keeps [its digest](features/digest.md#the-digest-as-an-issue) in one issue, which it opens and then edits without notifying anyone | It is `true` or `false` |
| `[digest] title` | string | `"Triage digest"` | The title of that issue; the sweep sets it anew when it differs | One line of 1 to 256 characters |

## findings.toml

Each `[[accept]]` of `.github/findings.toml` accepts the alerts of one rule of code scanning:

<details markdown="1">
<summary>Each key of findings.toml</summary>

| Key | Type | Default | What it does | What `check` makes sure of |
| --- | --- | --- | --- | --- |
| `tool` | string | required | The tool, as code scanning names it, in any case | It isn't empty |
| `rule` | string | required | The rule, such as `py/unused-import` | It isn't empty |
| `reason` | string | required | The reason that GitHub records for the dismissal | `false positive`, `won't fix` or `used in tests` |
| `comment` | string | required | Why, in words, shown with the dismissed alert | 1 to 200 characters |
| `path` | string | `""` | Only alerts in this file, or in the files of this folder, such as `vendor` for `vendor/board.js` but not `vendored/board.js`; empty for every file | Nothing; a key that the table doesn't have, such as `paths`, stops the findings command too, since the acceptance would cover every file |

</details>
