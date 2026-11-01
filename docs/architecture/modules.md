---
description: The modules of the package issue_assistant, each with what it holds, and the layers that keep them apart.
---

# The modules of the package

[← Architecture](../architecture.md)

The code is the package `issue_assistant/`, of the standard library alone ([decision 0003](../decisions/0003-the-standard-library-alone.md)). Each module imports only from those before it in `issue_assistant/__init__.py`, so no two need each other, and the list there reads from the ground up.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/the-layers-of-the-package.dark.svg">
  <img src="../images/diagrams/the-layers-of-the-package.svg" alt="The layers of the package: The package issue_assistant from the command line at the top to the ground: the command line in cli.py imports what runs the engine, evaluates and sets up a repository; those import the events, commands and sweeps; those the context, the comments, the application of an answer and the digest; those what reads an issue and an answer; those the settings, the languages, the engines and the client of GitHub; and those the ground of paths, constants, patterns, values and errors. A module imports only from below, and from those before it in its own layer.">
</picture>

| Module | What it holds |
| --- | --- |
| `issue_assistant/__init__.py` | Hands on the names of every module, in the order of the layers |
| `issue_assistant/__main__.py` | Runs the command line: `python3 -m issue_assistant COMMAND` |
| `issue_assistant/paths.py` | Where the tool keeps its files, and where a repository keeps those of the assistant |
| `issue_assistant/constants.py` | The names and limits that several modules share: the labels, the tasks and the markers |
| `issue_assistant/patterns.py` | The patterns that read the Markdown of issues and comments |
| `issue_assistant/values.py` | The values of GitHub's JSON, dates, and workflow commands |
| `issue_assistant/errors.py` | The errors of the assistant, and what a failed call of `gh` means |
| `issue_assistant/languages.py` | The catalogs of `locales/`, and the guess of a text's language |
| `issue_assistant/engines.py` | The table `ENGINES`, with the command line and the limits of each |
| `issue_assistant/settings.py` | `labels.toml`, `config.toml` and `findings.toml`, read and checked |
| `issue_assistant/fixture.py` | An issue from a file, for the canary and for trying a prompt |
| `issue_assistant/github.py` | The class `GitHub`: every request, through `gh`, and GraphQL's answers as REST's |
| `issue_assistant/issues.py` | Who wrote an issue or a comment, the marks of the assistant, the issue forms and the commands in a comment |
| `issue_assistant/cleaning.py` | The cleaning of what the engine wrote: `sanitize`, `plain` and the links to files |
| `issue_assistant/answer.py` | The schema of the answer, `validate` and `parse_answer` |
| `issue_assistant/comments.py` | The `render_*` functions of the comments, in the language of the reporter |
| `issue_assistant/applying.py` | `apply_answer`: the labels, the comment, the owners and the first answer to a discussion |
| `issue_assistant/learning.py` | What maintainers changed, for `corrections.md` of the next analyses |
| `issue_assistant/digests.py` | What waits for whom, the feedback, the quality of the month and the digest |
| `issue_assistant/context.py` | `write_context`: all that the engine reads besides the checkout |
| `issue_assistant/commands.py` | The `/assistant` commands, a reporter's answer, and the limits of the AI runs |
| `issue_assistant/events.py` | `handle_event`: what a new issue, a comment or a discussion starts |
| `issue_assistant/sweeps.py` | `sweep`: reminders, closing, locking, and what a merge or a release settles |
| `issue_assistant/labels.py` | `sync_labels` and the routing of the issue forms |
| `issue_assistant/code_scanning.py` | `watch_findings`: the findings that `findings.toml` accepts, and those that remain |
| `issue_assistant/actionlint.py` | Reading YAML, and adding to actionlint's settings without losing their comments |
| `issue_assistant/setup_check.py` | `check`: everything it reports about a repository's set-up |
| `issue_assistant/installation.py` | `install` and `set_up_github`: the files of a repository and its settings on GitHub |
| `issue_assistant/running.py` | `analyze`: choosing the engine, installing its command line, starting it and reporting its usage |
| `issue_assistant/evaluation.py` | `evaluate`: replaying closed issues through the engine and scoring its answers |
| `issue_assistant/accuracy.py` | The numbers on the page of the accuracy, from a report of `evaluate` |
| `issue_assistant/cli.py` | `main()` with the help of the commands, and a function for each command |

`tests/test_package.py` keeps this shape: the layers, the standard library, this map, and no module above 1,500 lines or function above 150. `__init__.py` hands on every name, so a test or a script reads `assistant.render_analysis`; a test that replaces a name does so in the module that looks it up, as `monkeypatch.setattr(assistant.cli, "GitHub", …)`.
