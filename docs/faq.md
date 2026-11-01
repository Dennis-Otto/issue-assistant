---
description: What the issue assistant costs, which data leaves GitHub, which languages it answers in, and why an issue of a maintainer gets no analysis.
---

# FAQ

What the issue assistant costs, which data leaves GitHub, and what happens to spam, a secret or a vulnerability in an issue. [Troubleshooting](troubleshooting.md) has the fix when something doesn't work, [Get started](getting-started.md) describes the set-up step by step, and the [discussions](https://github.com/Dennis-Otto/issue-assistant/discussions) take every question that isn't answered here.

## What does it cost?

The action is free and open source. A repository pays for its engine and, when it is private, for the minutes of its runners, which GitHub gives public repositories for free. One triage, measured on a feature request in Paperless Sync on 2026-10-12:

| Measure | Value |
| --- | --- |
| From the new issue to the comment | 2 min 39 s |
| Time on runners | 78 s: 5 s `event`, 65 s `analyze`, 8 s `apply`; the rest was the wait for a firewall runner |
| The AI | Claude Opus 5.5, 15 turns, $0.20 at the prices of the API |

A reply to a maintainer's comment, which only judges whether the issue now waits for its reporter, took Claude Opus 5.5 3 turns, 4.4 s and $0.08. The summary of every run's job shows what it cost, [in one line](features/cost.md).

With the token of a Claude subscription instead of an API key, an analysis counts toward the usage limits of the subscription instead of being billed by the token. A smaller model costs less per token, and follow-ups and the other tasks read less than a first analysis:

| Model | Input, per million tokens | Output, per million tokens | For |
| --- | --- | --- | --- |
| Claude Opus 5.5, `claude-opus-5-5` | $4.00 | $20.00 | the default: the most careful analyses |
| Claude Sonnet 5.5, `claude-sonnet-5-5` | $2.00 | $10.00 | repositories with many issues: half the price per token |
| Claude Haiku 5.5, `claude-haiku-5-5` | $0.10, up to 100K tokens per prompt; $0.50 beyond | $0.50, up to 100K tokens per prompt; $2.50 beyond | the replies to one comment, at the least cost |

For a repository with many issues, `model = "claude-sonnet-5-5"` halves the price of every token, and `light_model` lets the replies to one comment, which only say yes or no, run on a cheaper model than the analyses. These are the prices of Anthropic's API in October 2026; [its page of prices](https://platform.claude.com/docs/en/about-claude/pricing) has today's.

The other engines bill their own way. Codex runs at the prices of OpenAI's API. Gemini costs nothing within the daily limits of the free tier of the Gemini API, which suit a repository with a few issues a day, and its paid tier bills by the token. Copilot counts the premium requests of the Copilot plan of the account that owns its token. Only Claude stops at the dollars of `max_budget_usd`; Gemini stops at its turns, and Codex and Copilot at the time of the job, 20 minutes.

What strangers can spend has [limits](features/limits.md): by default 20 analyses, follow-ups and replies after a release a day, about $4 at the prices of the API. An engine secret of the repository's own caps the rest: an API key of a workspace with a monthly spend limit, rather than the token of a subscription that other repositories or people use too, so that a flood in one repository never stops the others.

## Can I change the model?

Yes, with `model` under `[engine]` in `.github/issue-assistant/config.toml`, such as `model = "sonnet"` for a faster and cheaper one. Without it, the engine's default runs: `claude-opus-5-5` for Claude, `gpt-6.1-sol` for Codex, `auto` for Gemini and `auto` for Copilot, with which their command lines choose a model. A model is a name of its engine, such as `flash` for Gemini or `gpt-5.5` for Codex. `light_model` runs the replies to one comment, which only say yes or no, and `effort` sets how much a model thinks; `max_turns` and `max_budget_usd` bound each run, as [What one run may cost](features/cost.md) shows. `check` refuses a name that could add arguments to the command line of the engine.

## Can I change when issues are reminded, closed or locked?

Yes, with `[lifecycle]` in `.github/issue-assistant/config.toml`: the days until the reminder, the closing and the closing of a duplicate, the window after a release, the follow-ups of the AI, the issues that are exempt by label, assignee or 👍, and the locking of issues closed long ago, which is off by default. The comments name the days that you set. Say the same days in the descriptions of `needs-info`, `stale` and `possible-duplicate` in `labels.toml`, or none, and in your `SUPPORT.md`; `check` fails on a label that names other days.

## Which languages does it answer in?

Every language. English, German, French, Spanish, Portuguese, Italian, Dutch, Polish, Turkish, Russian, Ukrainian, Chinese, Japanese and Korean have a catalog of the assistant's own words, and in any other language the AI writes its part in the reporter's language between headings and a footer in English. One more file in `locales/` adds a language, as [Languages](features/languages.md) describes.

## Can the maintainers read an issue in a language they don't know?

Yes. An analysis, a follow-up or a first answer in Discussions in another language than the maintainers' folds a summary for them in theirs, English unless `[maintainers] language` of `config.toml` names another one: what the issue reports and the gist of the answer, inside the one comment that the assistant posts. [A summary for the maintainers](features/languages.md) tells when it comes.

## Does the assistant learn from its mistakes?

Yes, from yours: correct the issue, and the next analysis knows. Every first analysis reads what the maintainers changed of the analyses of the 30 issues analyzed last: the labels they removed or added, the duplicate notices that someone objected to, and the analyses with a 👎 of someone who may push. It needs no store of its own, since GitHub keeps all of it, and `[context] max_analyzed = 0` turns it off; [What the AI reads](features/what-the-ai-reads.md) has the details.

## How do I know whether the assistant does well?

Open the summary of the morning sweep on the first day of a month, or start the sweep by hand: it counts the labels of the AI that the maintainers kept and those they had to add, the time from a new issue to its analysis, what became of the duplicate notices and of the questions to reporters, and the 👍 and 👎 on the analyses, for the 30 days before. Every morning, it also lists what waits on the maintainers, on reporters and on a release.

## Where do I see what waits on me?

In the summary of the morning sweep: under *Actions*, the workflow *Issue lifecycle*, its latest run of the schedule. It lists what waits on the maintainers, on reporters and on a release, by number and link. To have that in the Issues tab, set `[digest] issue = true` in `.github/issue-assistant/config.toml`: the sweep then [keeps it in one issue](features/digest.md#the-digest-as-an-issue), which you pin by hand and which it edits every morning without notifying anyone.

## Which data leaves GitHub?

The `analyze` job sends the vendor of the engine, Anthropic for Claude, OpenAI for Codex, Google for Gemini and GitHub for Copilot, what the AI reads: the issue and its comments, at most 20,000 characters of each text, short excerpts of the other issues and of the discussions, the labels, `.github/issue-assistant/project.md` and the files of the default branch that the AI opens. The engine runs with its telemetry and error reports turned off. What the vendor may do with the data follows your agreement with it: for Claude, the terms of your API account or of your subscription, and the privacy settings of that account. Google may use what reaches the free tier of the Gemini API to improve its products, so a private repository is better served by its paid tier. The jobs without AI tell StepSecurity, the maker of Harden-Runner, the repository, the run and the hosts that their steps reach, as the [security design](security.md#residual-risks) says. The action keeps no database and no service of its own, and the notice in `SUPPORT.md` tells reporters which AI reads their issue.

## Does it work in private repositories?

Yes, with three differences. The runners' minutes count toward the plan of the account. Harden-Runner blocks every host but GitHub's in the jobs without AI only with a subscription or trial of StepSecurity, and lets them run unblocked otherwise. And the AI reads private code, which reaches the vendor of the engine as above.

## Does it answer questions in Discussions?

Where `[discussions] answer_categories` of `config.toml` names categories, such as `Q&A`, each new discussion there gets one first answer, checked as every analysis and never marked as the answer, once `install` wrote `discussion-assistant.yml`; see [Questions in Discussions](features/discussions.md). It doesn't follow up there: the maintainers and the community take it from there.

## Why didn't my own issue get an analysis?

Issues that a maintainer opens, that is the owner, a member of the organization or a collaborator, get the labels of their form and `needs-triage`, but no analysis: the assistant writes for reporters who don't know the code. For an analysis anyway, write `/assistant again` in a comment, or run the workflow by hand under *Actions → Issue assistant → Run workflow* with the number of the issue, the task `triage` and the dry run turned off.

## What happens to spam?

An issue that the AI takes for spam, such as advertising or nonsense, gets the label `invalid` with the AI's reason on its timeline, and no comment, so that its author gets no answer. Closing or deleting it stays with the maintainer. A flood of spam meets the [limits of the AI runs](features/limits.md) first: after 3 issues of one author, or 20 issues and answers of everyone in a day, the issues only get their labels, until the maintainer has a look.

## What if an issue shows a secret or personal data?

The analysis starts with a warning. For a secret, such as a token, a key or a password, it asks the reporter to remove it from the post and from the post's edit history, and to replace it, since others may have seen it. For personal data, such as an e-mail address, a public IP address or the address of a private server, it says how to remove it. The AI never repeats such data, and an answer with something like a token is never posted.

## What if an issue reports a vulnerability?

The comment only asks the reporter to report it privately, as the security policy of the repository describes, and to remove the details from the issue. It has no summary, no analysis, no questions and no duplicates, and the reason for its labels on the timeline is a fixed sentence instead of the AI's words. The [chooser](getting-started.md#what-install-writes) that `install` writes for a repository without issue forms offers the private report before anyone opens an issue.

## How do I turn it off?

Set the repository variable `ISSUE_ASSISTANT_AI` to `off`, for example with `gh variable set ISSUE_ASSISTANT_AI --body off`, and the AI stays off while labels, reminders and closing keep working; removing the engine's secret does the same. For one issue, a comment `/assistant mute` keeps the AI from starting on its own there. To turn off everything, disable or delete the four workflows.

## What if the firewall runner is gone?

The AI runs on `ubuntu-24.04-firewall`, GitHub's runner with the egress firewall, which is a technical preview. Should GitHub rename or end it, or your account lose it, `analyze` waits in the queue for up to a day, and every later run of the same issue waits behind it. Name another runner for the AI in the repository variable `ISSUE_ASSISTANT_AI_RUNNER`, without a new release:

```sh
gh variable set ISSUE_ASSISTANT_AI_RUNNER --body ubuntu-24.04
```

The runner must be one of GitHub's runners of Ubuntu with a fixed version; the job `event` refuses any other, such as a self-hosted runner or `ubuntu-latest`, before the AI runs. There the first step of `analyze` is Harden-Runner in block mode with the hosts of your `.github/egress-firewall.yaml`, and the next one fails the job when requests to a host outside them still get through after a minute. That is the trade-off: the firewall blocks for the whole machine, out of reach of every step, Harden-Runner for the job, inside the machine; in a private repository it blocks only with a subscription of StepSecurity, so the AI doesn't run there without one. `gh variable delete ISSUE_ASSISTANT_AI_RUNNER` brings the AI back to the firewall runner, and [decision 0019](decisions/0019-a-runner-for-the-ai-when-the-firewall-runner-is-gone.md) explains the choice. The Canary of this repository fails when its `analyze` waits for longer than 30 minutes for the firewall runner and opens an issue at once, so a release follows a new label quickly.

## Why must the workflows equal the templates?

The security of the assistant lives in its workflows: the permissions of each job, the runner with the firewall and the order of the steps. So `check` fails when a workflow differs from its template in anything but the commit hashes of its actions, as decisions [0006](decisions/0006-the-templates-exactly.md) and [0007](decisions/0007-templates-live-in-templates.md) explain. The workflows only call the jobs of a release, so an update moves their pins and changes nothing else, as decision 0015 of the [decision records](https://github.com/Dennis-Otto/issue-assistant/tree/main/docs/decisions) explains. A change that a repository needs is welcome as an issue here, and reaches every repository with a release.

## Does it need Claude?

No. Claude is the default, and Codex, Gemini and Copilot are the other engines that `name` under `[engine]` chooses; Gemini starts for free, within the daily limits of the free tier of its API. [Engines](engines.md) compares them and shows how to change the engine.
