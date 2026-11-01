---
description: The first analysis notes which issues someone new to the project could take, the digest names them, and only a maintainer adds the label.
---

# Good first issue candidates

[← All features](README.md)

An analysis often names the file and the line involved and what to try, which is what a newcomer needs to start. An issue with the label `good first issue` shows on the contribute page of the repository and in GitHub's searches for newcomers. So the assistant tells the maintainers which issues could get it, and leaves the decision to them.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/how-an-issue-becomes-a-good-first-issue.dark.svg">
  <img src="../images/diagrams/how-an-issue-becomes-a-good-first-issue.svg" alt="How an issue becomes a good first issue: Where labels.toml has labels of the group contribution, the AI says in its first analysis whether someone new to the project could take the issue, with its reason. The script keeps that only with a file that exists among the references, nothing missing, no duplicate and no security report, and records it in the hidden marker of the analysis, so the comment reads the same and no label changes. Once a maintainer has opened or triaged the issue, the digest of the morning names it as a candidate, for 30 days from the analysis at most. The maintainer decides. Adding the label good first issue settles it, GitHub then lists the issue for newcomers, and the quality counts it as kept. A thumbs down on the analysis, or removing the label again, takes it off the list, and the quality counts it as removed.">
</picture>

- **The AI suggests, and never labels.** The schema of its answer has no such label to choose, and the labels of the group `contribution` aren't among those that `labels.md` lists for it. Nothing invites a contributor on its own, since [the maintainer has the last word](../roadmap.md#not-planned).
- **Nothing shows on the issue.** The comment reads the same with the mark as without it, and the labels are the same: the mark is `newcomer=1` in the hidden marker of the analysis. The reason of the AI, at most 200 characters on one line, goes only into the summary of the run that posted the analysis.
- **What may fit:** a confirmed, small, local change, such as a bug with a located cause or a fix of the documentation. An open design question, or a change that takes knowledge of the whole project, doesn't.
- **What the script checks, whatever the AI says:** at least one file among the references that exists in the repository, nothing in `missing_information`, no possible duplicate, no security report and no other repository that the issue may belong in. A reporter's text can talk the AI into a suggestion, and these checks and the maintainer's triage come after it.

## What the digest lists

The fourth part of the [digest of the morning sweep](digest.md), *Good first issue candidates*, has the open issues whose last analysis of the last 30 days has the mark and that a maintainer has opened or triaged, which means that they no longer have `needs-triage`. Each is a link with the day of its analysis, as in every list of the digest:

```markdown
**Good first issue candidates: 1**

- [#12](…): analyzed 5 days ago

The AI thinks that someone new to the project could take these; it sets no label. Add `good first issue` to those you confirm. A 👎 on the analysis takes one off this list.
```

- **Add the label to those you confirm.** A label of the group `contribution` takes the issue off the list, also `help wanted`.
- **Say no with a 👎 on the analysis.** Removing the label again says no as well, so that an issue doesn't return after you took it back. A 👎 of anyone who may not push counts for nothing.
- **It leaves the list without a word** when it closes, when its analysis is older than 30 days, when a later analysis by hand no longer marks it, when it waits on someone else, as with `needs-info`, `possible-duplicate` or `fixed-in-next-release`, or with `duplicate`, `invalid`, `wontfix` or `assistant-muted`.

The first day of a month, [the quality numbers](digest.md) count the issues marked in the 30 days before: *kept* with a label of the group, *removed* by a maintainer's no, *closed without a label*, and *waiting*.

## Turn it on

The starter `labels.toml` has the two labels, so a new repository has it from its first analysis. In a repository that keeps its own, add them, or move those it has from another group, as `decision` in a repository of [the blueprint](https://github.com/Dennis-Otto/repo-blueprint). Do that after the update to the release that brings the group, since `check` of an older one refuses it:

```toml
[[label]]
name = "good first issue"
color = "7057ff"
description = "Good for newcomers"
group = "contribution"

[[label]]
name = "help wanted"
color = "008672"
description = "Extra attention is needed"
group = "contribution"
```

Without labels of the group, the AI isn't asked, so an analysis costs nothing more, and the digest and the quality numbers have no such part. The first label of the group is the one that the digest tells you to add.
