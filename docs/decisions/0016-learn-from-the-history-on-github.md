# Learn from the maintainers' corrections as they stand on GitHub, as data of the context

- Status: accepted
- Date: 2026-10-17

## Context

An analysis chooses labels and names duplicates, and the maintainers correct it: they remove a label and add the right one, take `possible-duplicate` off, or give the analysis a 👎. Each analysis started from nothing, so the same choice came back with the next issue like it. Other tools that label issues adapt to such corrections, some with a store of their own. [0002](0002-the-ai-only-reads.md) keeps the AI read-only, and the assistant keeps no state outside GitHub: what it knows of an issue is its comments, their hidden markers and the events of its timeline.

GitHub already records every correction: the events name who added or removed a label and when, the comments hold the assistant's analyses with the duplicate they named, and the reactions who gave a 👎. Everything in a context file reaches the AI, and the titles of other issues are written by anyone.

## Options

1. A memory of the assistant's own, such as a file in the repository or a cache of the workflows, that the apply job updates after each correction. It could hold more than GitHub shows, but it needs a job that writes after every change of a label, a place to keep it and a way to repair it, and it is state that a maintainer can't see on the issue.
2. Train or tune a model on the corrections. Out of reach for an action that runs any engine with the repository's own key, and opaque to the maintainers.
3. Read the corrections from GitHub with every first analysis, as data in a context file: the latest issues with an analysis, from the newest comments of the repository, and for each the events of its labels, the outcome of its duplicate notice and the 👎 of people who may push.

## Decision

Option 3. GitHub is the record, so nothing can drift from it, a maintainer corrects the assistant just by correcting the issue, and a correction that is undone is gone from the next analysis too. `corrections.md` holds one line for each corrected issue: its number, its title as a JSON string without invisible characters, the labels of `labels.md` and fixed words of the script, in a code fence that no title can close, so that no text of an issue passes for a correction or an instruction. Only people with triage access can change labels, and a 👎 counts only from someone who may push, which GitHub's permissions say. A duplicate notice counts as objected to as the quality numbers count it: when someone other than a maintainer commented after it, or a maintainer took it back, and the issue was not closed as a duplicate.

The reading is bounded, as the window of the context is: at most five pages of the newest comments, the events of `[context] max_analyzed` issues (30), each read once, and a call for the reactions only of an analysis with a 👎. Only the issues in the window of the context count, so the AI can read each one it learns from in `issues/<number>.md`. A failing GitHub leaves a warning and a note in the file, never a failed analysis.

## Consequences

- A first analysis costs up to about 40 more read calls to GitHub, a few seconds in a quiet repository; `max_analyzed = 0` turns it off.
- The AI weighs a correction as a hint for issues like it, not as a rule: the prompt asks it not to repeat a label that a maintainer removed on a similar issue.
- The monthly quality numbers and the learning count corrections the same way, from `corrected_labels`, so what the maintainers see measured is what the AI learns from.
- A follow-up doesn't read the corrections; its labels stay as before unless the reporter's answer changes the picture.
