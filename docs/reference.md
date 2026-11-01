---
description: The commands of the issue assistant, every input and output of its GitHub Action, and how to try a prompt on your own computer.
---

# Reference

The commands of `issue_assistant.py`, the inputs and outputs of the action that runs them, and how to try a prompt on your own computer. [Engines](engines.md) compares the AI engines that it supports.

## Commands

The action runs `issue_assistant.py` with one command:

| Command | Job | What |
| --- | --- | --- |
| `event` | event | handles the event and the slash command of a comment, sets labels and names the AI's next task, within the limits of the AI runs |
| `endpoints` | event | on the runner that `ISSUE_ASSISTANT_AI_RUNNER` names for the AI, checks it and names the hosts of `.github/egress-firewall.yaml` with their ports for Harden-Runner in `analyze`; on the firewall runner, none |
| `engine` | analyze | names the engine of `config.toml` and whether its secret exists, and the model, turns, dollars and effort of the task |
| `context` | analyze | writes what the task reads into `.issue-assistant/`: the issue, or for the first answer to a discussion the discussion; for an analysis, a follow-up or a first answer the other issues and the discussions, and for an issue the labels; for a reply or a question the comment of its event; then the rules, the task and the schema |
| `analyze` | analyze | for Gemini or Copilot: installs the engine's command line at the version that the workflow pins and runs it on the task, with only its tools that read and the engine's secret alone, and outputs its answer |
| `usage` | analyze | writes the turns, time, cost and tokens of Claude's run into the summary of the job, numbers only, and a warning when the run stopped at its turns or its dollars |
| `apply` | apply | checks the AI's answer and posts it, on the issue or the discussion |
| `sweep` | lifecycle | reminds, closes unanswered issues and duplicates, marks fixed issues and closes them with the release; the morning's writes the digest into its summary and, where `[digest] issue` asks for it, into one issue |
| `sync-labels` | labels | creates and updates the labels, renames those that the repository has in another case, updates one that another workflow created a moment before, such as the settings bot of the blueprint, and warns of owners whom GitHub can't assign and of issue types that the repository lacks |
| `findings` | findings | dismisses the accepted findings of code scanning and fails while others are open; needs no other settings |
| `check` | your CI | checks labels, issue forms, workflows, firewall, settings, the description of the project and the notice to reporters, and names every key it doesn't know; with `ISSUE_ASSISTANT_AI_RUNNER` in its environment, the runner of the AI too |
| `evaluate` | your computer | replays the first analysis of closed issues, each as it was a minute after it was opened, with the engine's command line, and reports how often its labels, duplicates and questions match the maintainers' |
| `accuracy-page` | the workflow Evaluate of this repository | writes the numbers of a report of `evaluate` into `docs/accuracy.md` of the checkout, and refuses a report with anything else; [How the numbers get here](accuracy.md#how-the-numbers-get-here) |

`install` runs from a git checkout of a release, as [Set up a repository](getting-started.md#set-up-a-repository) shows, and pins its commit and tag; `--ref` and `--version` name them for another checkout. `install --setup` also creates the environment `issue-assistant` for the default branch and has the GitHub CLI ask for the engine's secret. `install`, `check`, `engine`, `usage` and `accuracy-page` read only the checkout, `analyze` reaches npm and the engine besides, and `install --setup` the environment too; the other commands call GitHub, for the repository that `GH_REPO` names as `owner/name`, or else the workflow's `GITHUB_REPOSITORY`; `evaluate` runs the engine's command line on your computer besides, and writes nothing on GitHub. Every command but `install`, `check`, `evaluate` and `accuracy-page` takes `dry-run: true` to only report what it would write; runs by hand under *Actions → Issue assistant → Run workflow* start as dry runs, and their summary shows the comment, as the last step of the set-up shows.

## Inputs and outputs of the action

The workflows of the templates set the inputs of the action; a workflow of your own, such as the check in the CI, sets `command`, and for a command that writes maybe `dry-run`. The tables of the website come from [`action.yml`](https://github.com/Dennis-Otto/issue-assistant/blob/main/action.yml) of this version; on GitHub, that file lists the same.

### Inputs

<!-- action.yml: inputs -->

### Outputs

The commands `event`, `engine` and `context` set the outputs for the steps and jobs after them.

<!-- action.yml: outputs -->

## Try a prompt locally

```sh
export GH_REPO=OWNER/REPO DRY_RUN=true
python3 issue_assistant.py context --issue 12 --mode triage    # in the repository's checkout
claude -p "$(cat .issue-assistant/prompt.md)" --restricted --tools "Read,Grep,Glob" \
  --append-system-prompt-file .issue-assistant/rules.md --max-turns 40 --max-budget-usd 1 \
  --json-schema "$(cat .issue-assistant/schema.json)" --output-format json > answer.json
RESULT="$(jq -c .structured_output answer.json)" python3 issue_assistant.py apply --issue 12 --mode triage
```

To try it on an issue that exists nowhere, write the issue and its comments into a JSON file, as GitHub's REST API gives them, and name the file in `ISSUE_ASSISTANT_FIXTURE`, relative to the checkout. `context` and `apply` then read that issue instead of one on GitHub, take its number, and write nothing, as in a dry run; the other issues, the discussions and the labels still come from GitHub. The issue of the project's own canary, `tests/fixtures/canary-issue.json`, is an example:

```sh
gh api repos/OWNER/REPO/issues/12 | jq '{issue: ., comments: []}' > issue.json    # then change it
export ISSUE_ASSISTANT_FIXTURE=issue.json
python3 issue_assistant.py context --mode triage
```
