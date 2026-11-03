---
description: A run by hand on one issue, with an instruction such as "ask for the logs", and the override with which a maintainer has the assistant answer again.
---

# Runs by hand

[← All features](README.md)

A maintainer starts the assistant on one issue by hand, from the Actions tab, the GitHub CLI or the REST API, and can tell it what to do, such as "ask for the logs". It answers each task once; to have it answer again, a maintainer ticks *override* and accepts the cost of another run.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/what-a-run-by-hand-may-do.dark.svg">
  <img src="../images/diagrams/what-a-run-by-hand-may-do.svg" alt="What a run by hand may do: A run by hand of issue-assistant.yml, from the Actions tab, gh workflow run or the REST API, names an issue, a task, an optional instruction and the override. The job event asks GitHub for the role of the person who started the run, or who ran it again. Without write access, the run fails before anything is written and before the AI. With override, only the roles admin and maintain may go on, and the summary of the run records the override; the AI then answers again, even where its task already has its comment. Without override, a run whose task would post nothing stops without the AI: a triage, follow-up or answer to a maintainer's question that already has its comment, or a follow-up that is not due, as after a maintainer joined the conversation; every other run goes on to the AI, which reads the instruction after its task. The job apply checks the role again and the answer as always, and the assistant's bot posts the comment.">
</picture>

## Start one

Under *Actions → Issue assistant → Run workflow*, with these inputs:

| Input | Default | What it does |
| --- | --- | --- |
| `issue` | | The number of the issue |
| `mode` | `triage` | The task: `triage` for a first analysis, `follow-up` for the answer to the reporter, or one of the replies to a comment |
| `instruction` | empty | What the assistant should do, in your words, such as "ask for the logs"; for `triage` and `follow-up`, at most 500 characters |
| `override` | off | Answer again, also where the task already has its comment; only for maintainers |
| `dry_run` | on | Only show in the summary what the assistant would post |

The same with the GitHub CLI, or with any tool that calls the REST API, such as a chat bot:

```sh
gh workflow run issue-assistant.yml -f issue=42 -f instruction="Ask for the logs" \
  -f override=true -f dry_run=false
gh api repos/OWNER/NAME/actions/workflows/issue-assistant.yml/dispatches \
  -f ref=main -f 'inputs[issue]=42' -f 'inputs[override]=true' -f 'inputs[dry_run]=false'
```

## Who may do what

- **Someone with write access starts a run.** GitHub lets everyone with write access start a workflow, and an app with the right to run workflows too, so the job `event` asks GitHub for the role of the person who started the run, or who ran it again, before it writes anything and before the AI runs. A role of `read` or `triage`, or an account that isn't a person, fails the run with the reason in its summary.
- **Only a maintainer overrides the one-comment rule.** The assistant posts one first analysis on an issue and one follow-up to each answer of its reporter, so that its runs stay few. A maintainer, with the role `admin` or `maintain`, ticks `override` to have it answer again, as `/assistant again` of the [slash commands](slash-commands.md) does for a first analysis. A run with `override` from someone with the role `write` fails, and nothing runs.
- **Without the override, a task answers once.** A `triage`, `follow-up` or `maintainer-question` that already has its comment ends in the job `event`, without the AI, and its summary says so. So does a `follow-up` that is not due: a maintainer joined the conversation, the last analysis asked no questions, or the assistant followed up as often as `max_follow_ups` allows. An issue without a first analysis, such as one that a maintainer opened, gets it without the override.
- **Each override is in the summary.** The summary of the job `event`, a notice of the run and the summary of the job `apply` name the person who overrode the rule, so the Actions tab lists every override. The assistant keeps no count in a file that anyone could change.
- **A tool acts for a person.** A tool that starts runs through the REST API, such as a chat bot of the maintainer, uses a token that acts for a person, such as the user access token of a GitHub App: the token of the app itself names the app as the actor, which has no role, and fails the run.

## What the instruction steers

- **It follows the task.** The engine reads the instruction after its task, as words of the person who started the run, and follows them as far as its rules and the form of its answer allow: it can ask the reporter for the logs, but it can't post anything that the checks refuse, read anything else or change its rules.
- **The issue stays material.** The issue, its comments and the other texts that the AI reads stay material to analyze, also where one claims to come from a maintainer, as in every run.
- **The summary shows it.** The instruction is on one line in the summary of the run, which everyone who can read the repository can see in the Actions tab.
- **The comment is the assistant's.** The bot of the assistant posts the comment, checked as every other one; a comment in a maintainer's own name is theirs to write.

## In every repository

The template of `issue-assistant.yml` that `install` writes has these inputs, and the action checks the role, reads the instruction and keeps the rule, in the reusable workflow as in a workflow of your own that runs the action. A repository gets them with the update to the release that brings them, which moves the pin of the reusable workflow and the inputs of the template together.
