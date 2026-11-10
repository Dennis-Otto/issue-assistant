---
description: How often the issue assistant's labels, duplicates and questions match the maintainers', measured on closed issues, and how to measure it yourself.
---

# How accurate is it?

How often the assistant's labels, duplicates and questions match what the maintainers decided, measured by replaying the first analysis of closed issues as each was when it was opened. Few AI triagers publish such numbers, and maintainers rightly distrust a labeler that they can't measure: `evaluate` gives every repository its own numbers before it switches the assistant on, and after a change of the model or of the description of the project.

## The numbers

They come from the repositories that use the assistant, [HomeDarts for Autodarts](https://github.com/Dennis-Otto/homedarts-for-autodarts), [Paperless Sync](https://github.com/Dennis-Otto/paperless-sync) and [Paperless Unified Search](https://github.com/Dennis-Otto/paperless-unified-search), each with the day, the engine and the model of its measurement, as `evaluate` writes them into its report. [The next section](#how-the-numbers-get-here) says how they get here. Each table rests on 30 closed issues, all of them opened by the repository's maintainer, so they measure the labels of a maintainer's own issues, with no duplicate and no question to grade: [the limits](#what-the-numbers-cant-tell) say why.

<!-- accuracy numbers: start -->

### [Dennis-Otto/homedarts-for-autodarts](https://github.com/Dennis-Otto/homedarts-for-autodarts)

Measured on 2026-11-09 with Claude, `claude-opus-5-5`, on the 30 newest of 46 closed issues read that people labelled or closed as a duplicate of an older issue, and that the assistant never analyzed. The analyses cost $6.75 at the prices of the API. Small numbers vary: with few issues, one more found or missed moves a share a lot, so each share names its counts.

| Measure | Precision | Recall | Issues |
| --- | ---: | ---: | ---: |
| Type | 90 % (26 of 29) | 72 % (26 of 36) | 29 |
| Areas | 62 % (10 of 16) | 100 % (10 of 10) | 9 |
| Topics | 100 % (2 of 2) | 100 % (2 of 2) | 2 |
| Duplicates | – | – | 0 |
| Sure duplicates | – | – | 0 |
| Asks for information | – | – | 0 |

### [Dennis-Otto/paperless-sync](https://github.com/Dennis-Otto/paperless-sync)

Measured on 2026-11-09 with Claude, `claude-opus-5-5`, on the 30 newest of 43 closed issues read that people labelled or closed as a duplicate of an older issue, and that the assistant never analyzed. The analyses cost $7.47 at the prices of the API. Small numbers vary: with few issues, one more found or missed moves a share a lot, so each share names its counts.

| Measure | Precision | Recall | Issues |
| --- | ---: | ---: | ---: |
| Type | 93 % (27 of 29) | 87 % (27 of 31) | 29 |
| Areas | 65 % (11 of 17) | 92 % (11 of 12) | 12 |
| Topics | 100 % (6 of 6) | 86 % (6 of 7) | 7 |
| Duplicates | – | – | 0 |
| Sure duplicates | – | – | 0 |
| Asks for information | – | – | 0 |

### [Dennis-Otto/paperless-unified-search](https://github.com/Dennis-Otto/paperless-unified-search)

Measured on 2026-11-09 with Claude, `claude-opus-5-5`, on the 30 newest of 45 closed issues read that people labelled or closed as a duplicate of an older issue, and that the assistant never analyzed. The analyses cost $6.30 at the prices of the API. Small numbers vary: with few issues, one more found or missed moves a share a lot, so each share names its counts.

| Measure | Precision | Recall | Issues |
| --- | ---: | ---: | ---: |
| Type | 93 % (27 of 29) | 82 % (27 of 33) | 29 |
| Areas | 55 % (12 of 22) | 92 % (12 of 13) | 12 |
| Topics | 100 % (8 of 8) | 100 % (8 of 8) | 8 |
| Duplicates | – | – | 0 |
| Sure duplicates | – | – | 0 |
| Asks for information | – | – | 0 |

<!-- accuracy numbers: end -->

A number of your own repository is welcome in the [discussions](https://github.com/Dennis-Otto/issue-assistant/discussions): the table of measures of the report, with the day, the engine and the model that it names.

## How the numbers get here

The workflow `.github/workflows/evaluate.yml`, which only this repository has, runs `evaluate` on GitHub in place of a maintainer's computer, and a person merges what it writes.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/how-the-numbers-get-onto-the-accuracy-page.dark.svg">
  <img src="images/diagrams/how-the-numbers-get-onto-the-accuracy-page.svg" alt="How the numbers get onto the accuracy page: A maintainer starts the workflow Evaluate by hand with a repository and a number of issues. The job replay, on the egress-firewall runner with a read-only token and the engine's secret, runs evaluate against a checkout of that repository. Without a closed issue to replay, the run passes with a notice in its summary, which says why each issue read was left out and what an issue needs to be replayed, and the page stays. Otherwise the job keeps the report as an artifact. The job publish runs no AI: accuracy-page takes only numbers and names from the report, and refuses a report that has anything else. The release app adds them to one pull request, which a person merges, and the website shows the table.">
</picture>

- **Who starts it.** A maintainer, under *Actions → Evaluate → Run workflow*, with the repository and the number of closed issues to replay, 30 by default. Nothing starts it on its own, since a run costs money: about $0.20 to $0.25 for each issue with Opus at the prices of the API, so $6 to $7.50 for 30, as the runs of the numbers above cost. It draws on the secret of the environment `issue-assistant`, which only `main` may use, so a subscription's token uses up its limits as any analysis does.
- **The job `replay` has the AI.** It runs on the egress-firewall runner, whose firewall lets through only the hosts of `.github/egress-firewall.yaml`, with a token that only reads and with the command line of Claude from the lock file in `.github/evaluate/`. The checkout of the measured repository is the root of the replays, so the engine's tools that read see nothing outside it. The report is an artifact of the run. A repository without a closed issue to replay has nothing to measure, which is no failure: the run passes without a report, the job `publish` doesn't run, and the summary of the job counts why each closed issue read was left out, as [Which issues](#measure-the-accuracy) below says.
- **The job `publish` has no AI.** The report comes from a job with an AI, so it counts as untrusted: the command `accuracy-page` takes from it only numbers and the names of the measures, the engine, the model and the repository, recalculates each share, and refuses a report in which anything else stands there, one that names another repository than the run measured, and one in which every analysis failed, so a run that couldn't reach the engine leaves the older numbers as they are. The lines of the issues stay in the artifact.
- **One pull request.** The release app, with a token that may write only contents and pull requests, keeps one pull request from the branch `bot/accuracy`, and each run adds its repository to it. The table of a repository replaces its older one when it comes again. Without the release app, the run warns and leaves the report in the artifact.

## Measure the accuracy

How often would the assistant have been right in your repository? `evaluate` replays the first analysis of closed issues that your maintainers decided, with the engine's command line on your computer, and compares what the AI chooses with what they chose. In the repository's checkout, with the assistant installed, and with PyYAML (`pip install pyyaml`) or `yq` to read its issue forms:

```sh
export GH_REPO=OWNER/REPO
PYTHONPATH=path/to/issue-assistant python3 -P -m issue_assistant evaluate --issues 50
```

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/how-evaluate-replays-a-closed-issue.dark.svg">
  <img src="images/diagrams/how-evaluate-replays-a-closed-issue.svg" alt="How evaluate replays a closed issue: evaluate reads the closed issues, the newest first, and takes those that people labelled, after they were opened or as a maintainer opened them, or closed as a duplicate of an older issue, and that the assistant never analyzed, until it has as many as --issues asks for. It rewinds each to a minute after it was opened: the labels that its form and bots gave it by then, no later comment, and only the issues, discussions and releases that existed then, an issue closed since as open. It writes the context of a first analysis, and the engine's command line on your computer answers, with only its tools that read. The answer's kind, areas, topics and priority are compared with the labels that people chose for the issue, its duplicates with the original, and its questions with whether a maintainer asked. The report has the precision and recall of each, and a line for each issue.">
</picture>

- **Which issues.** The closed issues, the newest first, that people labelled with a type, an area, a topic or a priority of `labels.toml`, after they were opened or as a maintainer opened their own issue, or closed as a duplicate of an older issue, and that the assistant never analyzed, since its own labels would grade themselves: 50 unless `--issues` asks for another number up to 200, from the 1,000 closed issues opened last. Without one, `evaluate` writes no report and ends with a notice, not a failure: how many closed issues it read, how many of them it left out for each reason, such as those that a bot opened, those without a label of a type, an area, a topic or a priority and those whose only such label came with their form or from a bot, and what an issue needs to be replayed.
- **A maintainer's own issue.** A maintainer, or their coding agent, who opens an issue with its labels, such as with `gh issue create --label bug`, decided them: they count as the maintainers' choice, and the replay doesn't see them. GitHub records the labels of an issue form as added by the reporter as well, so `evaluate` tells them apart by the form that the text was written with, the one of `.github/ISSUE_TEMPLATE/` in the checkout whose first field the text begins with, such as `### What happened?`: the labels of that form count for nothing, as on any issue. A label that anyone but the reporter and a bot added in the first minute is a choice as well. A reporter who may not triage the issue can't choose its labels, so theirs are those of the form.
- **As they were.** Each issue as it was a minute after it was opened: with the labels that its form and bots gave it by then, without those that people chose, without a later comment, and with only the issues, discussions and releases of that moment; an issue closed since is open there, and a discussion answered since has no answer. The texts and labels of the other issues, and the code and documentation of the checkout, are those of today, and so is the window of the other issues: an original closed long ago may be missing from it, though the search still finds it among the issues of that moment.
- **The engine.** The engine of `config.toml` with its model, turns and budget, or the one that `--engine` names, `claude`, `gemini` or `copilot`, with the model of `--model`. Its command line runs as you would run it, logged in as you, with only its tools that read the checkout, and each issue costs what a first analysis costs. Gemini takes the settings that keep it to reading only from a file of root's, so evaluate runs it only on Linux, macOS or WSL and writes them with sudo: run `sudo -v` first. Nothing is written on GitHub.
- **The measures.** *Precision* is the share of the AI's choices that the maintainers made too, *recall* the share of the maintainers' choices that the AI made: for each group of labels, over the issues that people gave a label of the group, after they were opened or as a maintainer opened their own issue, apart from bots, while a label that the issue had from its form or from a bot counts for neither, since the AI sees it; for the duplicates, over the issues closed as a duplicate of an older one, and for the sure ones alone, the first one whose original was open and opened or triaged by a maintainer, which gets the notice that closes the issue; and for the questions for more information that the reporter would get, never for a type that `labels.toml` asks nothing for and never on a maintainer's own issue, which gets no analysis and so no question, where the maintainers asked when the first comment of one of them asked a question or someone added `needs-info`.
- **The report.** A Markdown file with the numbers, the engine, the model and the day, and a line for each issue with its right, wrong and missed labels, its duplicate and its question: `NAME-accuracy.md` next to the checkout, or the file of `--report`, which must be outside the checkout, where the engine would read it the next time.

## What the numbers can't tell

- **The text of an issue is that of today.** A reporter who added what the maintainers asked for makes the question for it look needless.
- **The rest of the repository is partly that of today.** The texts and labels of the other issues, and the code, the documentation and the changelog of the checkout, which may already name the fix.
- **The window of the other issues is that of today.** It holds the issues open now and those closed last, so for an older issue an original that was open then and closed long ago may be missing from it; the search for its words still finds the issues of that moment. The duplicates found may be a little fewer than at that moment.
- **The labels of its form count for nothing.** A label that the form of an issue or a bot gave it is no decision of the maintainers, and the AI sees it, so only the labels that people chose count, later or as a maintainer opened their own issue. The forms are those of the checkout today: when a maintainer opened their own issue with a form whose first field has another label now, the labels of that form count as their choice. `evaluate` reads only the issue forms (`*.yml`): the labels of a Markdown template (`.md`) that a maintainer opened their own issue with count as their choice too.
- **A maintainer's own issue is replayed as a reporter's.** The assistant gives a maintainer's own issue the labels of its form but no analysis, yet `evaluate` replays it as if a reporter had opened it, since its labels are a decision of the maintainers all the same. In a repository whose maintainer opens most issues, the numbers of the labels therefore mostly describe issues that a maintainer or their coding agent wrote.
- **A maintainer's question is a guess.** A question mark in the first comment of a maintainer, or the label `needs-info`, counts as a question for more information, also when it asked something else.
- **An issue has one type for the AI.** The assistant chooses one type for an issue, while a maintainer may give it two, such as `maintenance` and `compatibility`: the second one counts as missed, which keeps the recall of the types below their precision.
- **The issues above are their maintainer's own.** All 30 issues that each repository replayed on 2026-11-09 were opened by its maintainer, many with a coding agent and with their labels set as they were opened, which `evaluate` counts as his choice since version 1.10.4. Before, it counted them for nothing: on 2026-11-08 it could replay only 2 of the 125 closed issues of HomeDarts for Autodarts, 1 of the 101 of Paperless Sync and none of the 76 of Paperless Unified Search. So the labels are measured on issues that a maintainer or their coding agent wrote, which may say more plainly what they are about than the report of a user. None of them was closed as a duplicate, and a maintainer's own issue gets no question, so the rows of the duplicates and of the questions count none. Bots opened most of the other closed issues, which `evaluate` leaves out, and users opened only a few, most of which the assistant had already analyzed, which it can't grade itself on: the numbers of your own repository, with the issues of its users, tell you more.
- **Small numbers vary.** A repository with few closed issues has few duplicates to find, and one more found or missed moves the share a lot; the report names the counts behind each share.
