# A reporter can keep the AI out of their issue, with a box of the form

- Status: accepted
- Date: 2026-11-01

## Context

The notice in `SUPPORT.md` and the footer of every analysis tell reporters that a vendor's AI reads their issue, but only maintainers could stop it, with `/assistant mute`, and only after the first analysis had started. A reporter who pastes logs of their environment, or a project with users who care where their text goes, had no choice.

## Options

1. A command of the reporter. It comes too late: the analysis starts when the issue opens, before anyone can write a comment.
2. A label of its own for the reporter's wish. It tells the two kinds of mute apart without a read of GitHub, but every repository gets one more label, and two labels have to agree.
3. A box of the issue form that mutes the issue when it opens. The choice is there before the first run, in the form that every reporter fills in, and it reuses the one state that every path to the AI already respects.

## Decision

Option 3, with `[forms] opt_out_field` naming the field.

- **The label `assistant-muted` is the state; the box asks for it.** The box is read when the issue opens and when the reporter's edit ticks it, as a click in the issue does. Removing the label ends it, so that a box in an old text doesn't undo a maintainer's `/assistant unmute`.
- **A muted issue with the box ticked is out of every other run too.** Otherwise its text would still reach the vendor as part of the context of the next analysis, and the evaluation would replay it, as it replays exactly the issues without an analysis. A maintainer's mute keeps its issue in the context, as before: it says that the AI should start nothing, not that the text is private.
- **Only `/assistant again` lets the AI back in.** Unticking the box starts nothing: GitHub sends an edit for every change of the text, and when in doubt the AI stays out. The request has the rules of every request of the reporter: once per issue, while it has `needs-triage`, and within the limits of the day. On an issue without an analysis, a comment of the assistant with a marker of its own holds that record, as no other comment could count the run or the request. A click on the box is no answer either: only an edit that also changes the text counts as one.
- **A reporter ends only their own mute.** A reporter can add the box to their text at any time, so the box alone proves nothing: a comment `/assistant mute` of a maintainer, or the label added by a person, keeps the mute, as [decision 0012](0012-commands-from-the-issue.md) keeps that command for maintainers.
- **A maintainer's explicit command still runs.** The maintainers read the issue either way and could paste it into any AI, so a promise that they can't would be empty. The notice for reporters says that they can.
- **The default name offers nothing by itself.** A form without the field works as before, and `check` doesn't ask for one. It reports only a field of that name that isn't one box that may stay unticked, and a name of a repository's own that no form has.

## Consequences

- A new install offers the box in its starter forms; a repository with forms of its own adds the field, and the line of the notice, by hand.
- A reporter can mute their own issue without a maintainer. That costs nothing but the analysis of that issue, and the labels and the lifecycle go on.
- The box of a sibling repository counts only under the same name of the field: a repository under `[related]` with another name reads as if its issues had no box.
