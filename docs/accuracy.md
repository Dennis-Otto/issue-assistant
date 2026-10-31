---
description: How often the issue assistant's labels, duplicates and questions match the maintainers', measured on closed issues, and how to measure it yourself.
---

# How accurate is it?

How often the assistant's labels, duplicates and questions match what the maintainers decided, measured by replaying the first analysis of closed issues as each was when it was opened. Few AI triagers publish such numbers, and maintainers rightly distrust a labeler that they can't measure: `evaluate` gives every repository its own numbers before it switches the assistant on, and after a change of the model or of the description of the project.

## The numbers

None yet. The first ones come from the repositories that use the assistant, [HomeDarts for Autodarts](https://github.com/Dennis-Otto/homedarts-for-autodarts), [Paperless Sync](https://github.com/Dennis-Otto/paperless-sync) and [Paperless Unified Search](https://github.com/Dennis-Otto/paperless-unified-search), each with the day, the engine and the model of its measurement, as `evaluate` writes them into its report.

A number of your own repository is welcome in the [discussions](https://github.com/Dennis-Otto/issue-assistant/discussions): the table of measures of the report, with the day, the engine and the model that it names.

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
