---
description: How often the issue assistant's labels, duplicates and questions match the maintainers', measured on closed issues, and how to measure it yourself.
---

# How accurate is it?

How often the assistant's labels, duplicates and questions match what the maintainers decided, measured by replaying the first analysis of closed issues as each was when it was opened. Few AI triagers publish such numbers, and maintainers rightly distrust a labeler that they can't measure: `evaluate` gives every repository its own numbers before it switches the assistant on, and after a change of the model or of the description of the project.

## The numbers

They come from the repositories that use the assistant, [HomeDarts for Autodarts](https://github.com/Dennis-Otto/homedarts-for-autodarts), [Paperless Sync](https://github.com/Dennis-Otto/paperless-sync) and [Paperless Unified Search](https://github.com/Dennis-Otto/paperless-unified-search), each with the day, the engine and the model of its measurement, as `evaluate` writes them into its report. [The next section](#how-the-numbers-get-here) says how they get here.

<!-- accuracy numbers: start -->

None yet: the first run of the workflow *Evaluate* brings the first table.

<!-- accuracy numbers: end -->

A number of your own repository is welcome in the [discussions](https://github.com/Dennis-Otto/issue-assistant/discussions): the table of measures of the report, with the day, the engine and the model that it names.

## How the numbers get here

The workflow `.github/workflows/evaluate.yml`, which only this repository has, runs `evaluate` on GitHub in place of a maintainer's computer, and a person merges what it writes.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/how-the-numbers-get-onto-the-accuracy-page.dark.svg">
  <img src="images/diagrams/how-the-numbers-get-onto-the-accuracy-page.svg" alt="How the numbers get onto the accuracy page: A maintainer starts the workflow Evaluate by hand with a repository and a number of issues. The job replay, on the egress-firewall runner with a read-only token and the engine's secret, runs evaluate against a checkout of that repository and keeps its report as an artifact. The job publish runs no AI: accuracy-page takes only numbers and names from the report, and refuses a report that has anything else. The release app adds them to one pull request, which a person merges, and the website shows the table.">
</picture>

- **Who starts it.** A maintainer, under *Actions → Evaluate → Run workflow*, with the repository and the number of closed issues to replay, 30 by default. Nothing starts it on its own, since a run costs money: about $0.20 for each issue with Opus at the prices of the API, so about $6 for 30. It draws on the secret of the environment `issue-assistant`, which only `main` may use, so a subscription's token uses up its limits as any analysis does.
- **The job `replay` has the AI.** It runs on the egress-firewall runner, whose firewall lets through only the hosts of `.github/egress-firewall.yaml`, with a token that only reads and with the command line of Claude from the lock file in `.github/evaluate/`. The checkout of the measured repository is the root of the replays, so the engine's tools that read see nothing outside it. The report is an artifact of the run.
- **The job `publish` has no AI.** The report comes from a job with an AI, so it counts as untrusted: the command `accuracy-page` takes from it only numbers and the names of the measures, the engine, the model and the repository, recalculates each share, and refuses a report in which anything else stands there. The lines of the issues stay in the artifact.
- **One pull request.** The release app, with a token that may write only contents and pull requests, keeps one pull request from the branch `bot/accuracy`, and each run adds its repository to it. The table of a repository replaces its older one when it comes again. Without the release app, the run warns and leaves the report in the artifact.

## Measure the accuracy

How often would the assistant have been right in your repository? `evaluate` replays the first analysis of closed issues that your maintainers decided, with the engine's command line on your computer, and compares what the AI chooses with what they chose. In the repository's checkout, with the assistant installed:

```sh
export GH_REPO=OWNER/REPO
python3 path/to/issue_assistant.py evaluate --issues 50
```

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/how-evaluate-replays-a-closed-issue.dark.svg">
  <img src="images/diagrams/how-evaluate-replays-a-closed-issue.svg" alt="How evaluate replays a closed issue: evaluate reads the closed issues, the newest first, and takes those that people labelled after they were opened or closed as a duplicate of an older issue, and that the assistant never analyzed, until it has as many as --issues asks for. It rewinds each to a minute after it was opened: the labels of that moment, no later comment, and only the issues, discussions and releases that existed then, an issue closed since as open. It writes the context of a first analysis, and the engine's command line on your computer answers, with only its tools that read. The answer's kind, areas, topics and priority are compared with the labels that people gave the issue after the moment, its duplicates with the original, and its questions with whether a maintainer asked. The report has the precision and recall of each, and a line for each issue.">
</picture>

- **Which issues.** The closed issues, the newest first, that people labelled with a type, an area, a topic or a priority of `labels.toml` after they were opened, or closed as a duplicate of an older issue, and that the assistant never analyzed, since its own labels would grade themselves: 50 unless `--issues` asks for another number up to 200, from the 1,000 closed issues opened last.
- **As they were.** Each issue as it was a minute after it was opened: with the labels of that moment, such as those of its form, without a later comment, and with only the issues, discussions and releases of that moment; an issue closed since is open there, and a discussion answered since has no answer. The texts and labels of the other issues, and the code and documentation of the checkout, are those of today, and so is the window of the other issues: an original closed long ago may be missing from it, though the search still finds it among the issues of that moment.
- **The engine.** The engine of `config.toml` with its model, turns and budget, or the one that `--engine` names, `claude`, `gemini` or `copilot`, with the model of `--model`. Its command line runs as you would run it, logged in as you, with only its tools that read the checkout, and each issue costs what a first analysis costs. Gemini takes the settings that keep it to reading only from a file of root's, so evaluate runs it only on Linux, macOS or WSL and writes them with sudo: run `sudo -v` first. Nothing is written on GitHub.
- **The measures.** *Precision* is the share of the AI's choices that the maintainers made too, *recall* the share of the maintainers' choices that the AI made: for each group of labels, over the issues that people gave a label of the group after they were opened, apart from bots, while a label that the issue had at that moment, such as that of its form, counts for neither, since the AI sees it; for the duplicates, over the issues closed as a duplicate of an older one, and for the sure ones alone, the first one whose original was open and opened or triaged by a maintainer, which gets the notice that closes the issue; and for the questions for more information that the reporter would get, never for a type that `labels.toml` asks nothing for, where the maintainers asked when the first comment of one of them asked a question or someone added `needs-info`.
- **The report.** A Markdown file with the numbers, the engine, the model and the day, and a line for each issue with its right, wrong and missed labels, its duplicate and its question: `NAME-accuracy.md` next to the checkout, or the file of `--report`, which must be outside the checkout, where the engine would read it the next time.

## What the numbers can't tell

- **The text of an issue is that of today.** A reporter who added what the maintainers asked for makes the question for it look needless.
- **The rest of the repository is partly that of today.** The texts and labels of the other issues, and the code, the documentation and the changelog of the checkout, which may already name the fix.
- **The window of the other issues is that of today.** It holds the issues open now and those closed last, so for an older issue an original that was open then and closed long ago may be missing from it; the search for its words still finds the issues of that moment. The duplicates found may be a little fewer than at that moment.
- **The labels of its form count for nothing.** A label that an issue had a minute after it was opened, such as that of its form, is no decision of the maintainers, and the AI sees it, so only the labels that people added later count.
- **A maintainer's question is a guess.** A question mark in the first comment of a maintainer, or the label `needs-info`, counts as a question for more information, also when it asked something else.
- **Small numbers vary.** A repository with few closed issues has few duplicates to find, and one more found or missed moves the share a lot; the report names the counts behind each share.
