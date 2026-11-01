---
description: The settings of the issue assistant in labels.toml, config.toml and findings.toml, with every key, its type, its default and what the check makes sure of.
---

# Configuration

A repository sets up the issue assistant in three files of `.github/`, which `install` writes as starters when they are missing: the labels, the settings, and the findings of code scanning that it accepts. They change through pull requests like the code, and `check` makes sure of them in the CI.

## Settings

### `.github/labels.toml`

```toml
[[label]]
name = "area: cards"
color = "bfdadc"
description = "Dashboard cards"          # at most 100 characters
group = "area"                            # type, area, topic, priority, lifecycle, decision, contribution, release or dependabot
form_options = ["Live card", "Training card"]   # answers of the Area field that choose it
owners = ["octocat"]                      # optional: assigned to its issues while nobody is

[[label]]
name = "enhancement"
color = "a2eeef"
description = "New feature or improvement"
group = "type"
ask = false                               # never ask reporters of this kind for information
issue_type = "Feature"                    # optional: the issue type of the organization for this kind

[[label]]
name = "priority: high"
color = "b60205"
description = "Breaks the project for many users or loses data, without a workaround"
group = "priority"                        # optional: the AI gives one to an issue without a priority
```

### `.github/issue-assistant/config.toml`

```toml
[engine]
name = "claude"                 # claude, codex, gemini or copilot, as Engines compares them
# model = "claude-opus-5-5"     # the engine's default model if left out; a letter or digit, then letters, digits and ._:@/[]- only
# light_model = "claude-sonnet-5-5"  # the replies to one comment, yes or no; model if left out
# effort = "medium"             # low, medium, high, xhigh or max, for the models that take it; their default if left out
max_turns = 40                  # turns of an analysis and a question; a follow-up gets half, a reply a tenth, each at least 4; 1 to 200
max_budget_usd = 1.00           # the dollars at which one run stops; more than 0, at most 100
max_per_day = 20                # analyses, follow-ups and replies after a release in 24 hours; 0: only maintainers, by hand
max_per_author_per_day = 3      # analyses of one reporter's new issues in 24 hours
release_replies = 2             # comments of the reporter after each release that the AI reads

[context]
max_issues = 1000               # closed issues that an analysis reads besides every open one: those that changed last
max_discussions = 1000          # discussions that an analysis reads: those that changed last
max_analyzed = 30               # issues analyzed last whose corrections by the maintainers a first analysis learns from; 0: none

[related]
repositories = ["owner/server"] # public repositories whose issues a first analysis searches too, up to 5

[discussions]
answer_categories = ["Q&A"]     # categories whose new discussions get a first answer; [] if left out: none

[maintainers]
language = "en"                 # the language the maintainers read, a code of locales/; a comment in another one folds a summary for them

[links]
hosts = ["docs.example.com"]    # besides this repository

[forms]
area_field = "Area"
unmapped_options = ["Other"]
opt_out_field = "Automated analysis"   # the box with which a reporter keeps the AI out of their issue

[transparency]
files = ["SUPPORT.md"]
credit = true                   # false: the footers of the AI's comments don't link the project

[releases]
close_with_release = true       # false: fixed issues don't wait for a release

[lifecycle]
remind_after_days = 15          # the reminder after the last question without an answer
close_after_days = 30           # the closing, on the day that the reminder names; more than remind_after_days
duplicate_grace_days = 3        # a sure duplicate closes this long after the notice
reopen_after_release_days = 30  # the reporter's comment after a release reopens the issue this long
max_follow_ups = 2              # follow-ups of the AI on the reporter's answers; 0: none
exempt_labels = ["pinned", "security"]   # never reminded, closed or locked by the sweep
exempt_assignees = false        # true: assigned issues are exempt too
exempt_upvotes = 0              # issues with at least this many 👍 are exempt; 0: off
lock_closed_after_days = 0      # lock issues closed longer, at least reopen_after_release_days; 0: off

[digest]
issue = false                   # true: the morning sweep keeps its digest in one issue too
title = "Triage digest"         # the title of that issue
```

Each key has the type that it has here. A file that isn't TOML, or a value of another type, such as `max_per_day = "5"`, stops every command with an error that names the file and the key. A key that the assistant doesn't know, such as `close_with_releases` or a section `[release]`, would leave the default in place unnoticed: `check` fails on it and names the key that was probably meant, and every other command warns of it.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/what-the-assistant-does-with-a-problem-of-its-settings.dark.svg">
  <img src="images/diagrams/what-the-assistant-does-with-a-problem-of-its-settings.svg" alt="What the assistant does with a problem of its settings: Every command reads labels.toml and config.toml. A file that isn't TOML stops every command, with the line of the mistake. A value of another type than its key's stops every other command with an error that names the key; check reads the rest with the defaults and names every problem at once. A key that the assistant doesn't know changes nothing, so check fails on it and names the key that was probably meant, while the other commands warn of it and keep the default.">
</picture>

The `labels` of an issue form may be a list or one text of names separated by commas, as GitHub allows, and the area field may be a dropdown with `multiple: true`: each option that a reporter chooses adds its label.

## Findings

Code scanning reports findings of CodeQL, OpenSSF Scorecard and other tools in the repository's *Security* tab. Some can't or shouldn't be fixed, such as Scorecard's *Code-Review* for a project with one maintainer. `.github/findings.toml` accepts those, each with a reason, in a file that changes only through pull requests:

```toml
[[accept]]
tool = "Scorecard"        # the tool as code scanning names it
rule = "CodeReviewID"     # the rule, such as py/unused-import
reason = "won't fix"      # false positive, won't fix or used in tests
comment = "One maintainer: nobody else can approve a pull request."
# path = "tests/"         # optional: only alerts in this file or folder
```

The Findings workflow dismisses every open alert that an entry covers, with the entry's reason and comment, and fails while any other alert is open. GitHub tells the maintainer about the failed run; its summary links the alerts by number. `check` makes sure that every entry has a tool, a rule, one of GitHub's reasons and a comment, and no key that it doesn't know: a typo such as `paths` would accept the alerts of the rule in every file, so it stops the Findings workflow too. Code scanning needs the workflow's `security-events: write`; Dependabot's and secret scanning's alerts are out of its reach and stay with Dependabot's own pull requests and notifications.

A daily run with one accepted and one open finding, in a demo project:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/findings-dark.webp">
  <img src="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/findings-light.webp" alt="A failed daily run of the Findings workflow on a demo project: an annotation names the open alert number 7 by its link only; the summary says that alert 3 was dismissed as won't fix and asks to fix or accept the others">
</picture>
