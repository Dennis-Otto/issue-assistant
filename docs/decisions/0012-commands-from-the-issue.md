# Maintainers and the reporter steer the assistant with commands that are no part of the conversation

- Status: accepted
- Date: 2026-10-14

## Context

A maintainer could steer the assistant only through *Actions → Issue assistant → Run workflow*: there was no way to ask for a new analysis from the issue, to start the closing of a duplicate that the AI missed, to keep the AI out of one issue, or to ask it a question about the code. Other bots take commands in comments, such as `/triage` or a mention of the bot. The event job already reads every new comment, without AI, with a token that writes to issues.

A command in a comment is text that anyone can write, and the assistant's rules read comments as the conversation: a comment of the reporter answers the questions, one of a maintainer asks new ones, and any comment objects to a duplicate notice. GitHub tells who wrote a comment, and in which role, in the event.

## Options

1. Commands from anyone, each with a role that the assistant checks. A stranger's command would still be refused with a reaction, which makes the comment a way to get the bot's attention, and the refused line would still need to count as a comment or not.
2. Commands only from maintainers and the reporter of the issue; from anyone else, the line is an ordinary comment. The reporter gets the commands that change nothing they couldn't do with a comment (`not-duplicate`) and one new analysis per issue, while it waits for triage and within the limits of the day. Commands answer, ask and object to nothing, in the event job and in the sweep alike, so that a `/assistant mute` neither ends the waiting for an answer nor restarts it.
3. Commands from maintainers alone, through a mention of the bot. GitHub's app `github-actions` can't be mentioned, and the reporter couldn't ask for a new analysis after a misunderstanding.

## Decision

Option 2. A comment whose first line starts with `/assistant` gives a command when a maintainer, as the assistant defines them everywhere, or the reporter wrote it: `again`, `ask`, `duplicate`, `not-duplicate`, `waiting`, `mute` and `unmute`. The event job carries out the commands that need no AI and hands `again` and `ask` to the AI as tasks, `triage` and the new `maintainer-question`, which `apply` checks as every other answer. A 👀 reaction shows that a command was carried out, a 😕 that it was refused, with the reason in the run but without the text of the comment.

The reporter's `again` is recorded, with the run, in the hidden marker of the analysis that it repeats, as the limits of the AI runs record follow-ups ([0009](0009-limit-the-ai-runs.md)): only the assistant can change that comment, so deleting the command gives no second one. `mute` sets the label `assistant-muted`, which keeps every task that an event would start from running, read anew in the event job, since the event may have waited behind the run that muted the issue; a maintainer's own `again` and `ask` still run. A maintainer who names an original with `duplicate` vouches for it, so the original needs no triage, unlike a duplicate that the AI found.

## Consequences

The template of `issue-assistant.yml` offers `maintainer-question` under *Run workflow*, so repositories run `install` again. The label `assistant-muted` is in the group `decision` of the starter `labels.toml`, not a lifecycle label that `check` requires; the first `mute` creates it where `labels.toml` lacks it. The event job reads the labels of the issue once more before it starts a task of the AI, and a command adds a reaction to its comment. The sweep leaves out commands when it looks for answers, questions and objections, so a maintainer who comments only with commands has not joined the conversation for the reminder, but has for the follow-ups of the AI, as any comment of theirs does.
