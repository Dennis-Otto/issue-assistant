---
description: A box in the issue form with which a reporter keeps the AI out of an issue, what goes on without AI, and how the reporter lets the AI in later.
---

# Saying no to the AI

[← All features](README.md)

A reporter decides whether the AI reads their issue: a ticked box in the issue form keeps it out, and all that runs without AI goes on.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/what-the-box-of-the-issue-form-does.dark.svg">
  <img src="../images/diagrams/what-the-box-of-the-issue-form-does.svg" alt="What the box of the issue form does: A new issue with the box ticked, or a click of its reporter on the box in the issue, adds the label assistant-muted. No run of the AI starts on the issue then, and its text stays out of what the AI reads for other issues, while the labels of the form, the owners, the reminders and the closing with a release go on. A new issue without the box ticked gets its first analysis as always. On a muted issue, the reporter's /assistant again, while the issue has needs-triage and within the limits of the day, removes the label and lets the AI read it, and a maintainer's /assistant again or /assistant ask lets it read too. Without either, and also when the box is only unticked, the AI stays out.">
</picture>

- **The box is a field of the form.** The starter forms `bug_report.yml` and `feature_request.yml` that `install` writes have it, unticked, as their last field, and it links the notice that says which AI reads an issue. GitHub shows the answer in the issue as a box, so that a click of the reporter there counts too, from then on.
- **A ticked box mutes the issue.** The event job, which runs no AI, adds `assistant-muted` when the issue opens, so that no run of the AI starts on it: no first analysis, no follow-up, no check of a maintainer's comment and no reading of a comment after a release. The summary of the run says why.
- **The text stays out of other runs as well.** The AI doesn't read the issue for another issue, for a discussion or for an analysis in a repository that names this one under `[related]`, and [the accuracy](../accuracy.md) doesn't replay it.
- **All that needs no AI goes on.** The labels of the form, the owners of its area, `needs-triage`, the reminders after `/assistant waiting` and the closing with a release work as on any issue. A question in a plain comment of a maintainer sets no waiting without the AI, and a comment after a release doesn't reopen a muted issue, as the AI decides both.
- **The reporter can change their mind.** `/assistant again` in a comment, while the issue has `needs-triage` and within [the limits of the day](limits.md), removes the label and starts an analysis, also when the issue has none yet. The reporter has that one request per issue, and it counts as a run of the day: on an issue without an analysis, a short comment of the assistant records it. Unticking the box alone starts nothing and answers no question, so that an edit never lets the AI in by accident; the same goes for a click that ticks it.
- **A maintainer has the last word.** `/assistant again`, `/assistant ask` and a run by hand still start the AI, as on [any muted issue](slash-commands.md), and the notice for reporters says so. `/assistant unmute` starts nothing by itself: it ends the mute, and later events start the AI again. A reporter can't end a mute that a maintainer set, also by adding the box to the text afterwards: `/assistant mute` in the comments, or the label added by a person, tells the two apart.

## Offer it in your own forms

A repository that had forms before `install` adds the field to each form, last, so that nothing in another answer can pass for it:

```yaml
  - type: checkboxes
    id: automated-analysis
    attributes:
      label: Automated analysis
      description: An AI reads new issues and answers first; [SUPPORT.md](../blob/HEAD/SUPPORT.md#what-happens-with-your-issue) says which, and what it receives. The box keeps it out of this issue, and the maintainers read it either way. If you change your mind, write `/assistant again` in a comment.
      options:
        - label: Don't let the AI assistant read this issue.
```

`[forms] opt_out_field` in `config.toml` names the field, `"Automated analysis"` by default; a form without a field of that name offers no such choice. `check` reports a field of that name that isn't checkboxes with one box, a required box, with which every reporter would opt out, and a name of your own that no form has.

`install` writes the notice only into files that don't name the engine yet, so a file that has it gets the line on saying no by hand, from [`templates/SUPPORT-notice.md`](https://github.com/Dennis-Otto/issue-assistant/blob/main/templates/SUPPORT-notice.md).
