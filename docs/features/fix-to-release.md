---
description: How a fixed issue stays open until a release ships its fix, which release closes it, and how the reporter's comment reopens it.
---

# From the fix to the release

[← All features](README.md)

Apps and integrations reach their users with a release, not with a merge. So an issue stays open until the fix is released.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/a-fixed-issue-from-the-merge-to-the-release.dark.svg">
  <img src="../images/diagrams/a-fixed-issue-from-the-merge-to-the-release.svg" alt="A fixed issue, from the merge to the release: The maintainer merges a pull request with Fixes #12. issue-lifecycle.yml labels the issue fixed-in-next-release and comments that it closes with the next release; it stays open, since users don't have the fix yet. When the maintainer publishes the release v1.2.0, the lifecycle comments with the link to the release, closes the issue and files it under the milestone v1.2.0, and the reporter is asked to update. When the reporter comments within 30 days that it is still broken, issue-assistant.yml lets the AI read the comment and reopens the issue with needs-triage; after thanks or a question, it stays closed.">
</picture>

1. A pull request names the issue it fixes with `Fixes #12`, as usual. When it is merged into the default branch, the issue gets the label `fixed-in-next-release` and a comment: fixed by the pull request, closes with the next release. A maintainer can also set the label by hand, for a fix without a pull request. The morning sweep catches a merge whose run failed, up to 14 days later and however many pull requests the repository merged since; if a release ships the fix by then, the issue closes with it at once, without the comment.
2. When a release is published, every issue with the label whose fix it contains closes as completed. A comment links the release and asks the reporter to update. It is the first final release, no draft or pre-release, that was published after the last fix was merged and contains every fix, so an issue that got its label late still closes with the release that shipped its fix. A label that a maintainer set by hand, for a fix without a pull request or after one that fell short, waits for the next release after it. Only this repository's pull requests count: one of another repository that names the issue with `Fixes owner/project#12` neither keeps it open nor appears in the comment. The issue is filed under the milestone of the release, such as `v1.2.0`, which the assistant creates, closed, when it is missing; its page lists everything that the release fixed.
3. If the reporter comments within 30 days that the problem persists after the update, the AI reads the comment and the issue reopens. Thanks or questions keep it closed. The AI reads up to two comments of the reporter after each release, as `release_replies` in `config.toml` says; a maintainer reads the rest.

The fixes of an issue are the pull requests of the repository that name it and were merged into the default branch at most 14 days before its label, the longest that the sweep takes to set it, and an hour more for the run of the sweep. A fix that a release closed the issue with before it was reopened didn't help, so neither that release nor the fix counts again; any other reopening, such as of an issue that GitHub closed at the merge, keeps the fixes:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/which-release-closes-a-fixed-issue.dark.svg">
  <img src="../images/diagrams/which-release-closes-a-fixed-issue.svg" alt="Which release closes a fixed issue: For an open issue with the label fixed-in-next-release, the assistant looks for its fixes: pull requests of this repository into the default branch that name it, merged at most 14 days before the label, leaving out those that a release closed the issue with before it was reopened. With fixes and a label that the assistant set, the first final release published after the last of them was merged that contains them all closes the issue, also one published before a late label. With fixes and a label set by hand, it is the first final release after the label that contains them all. Without fixes, the first final release published after the label closes it. Drafts and pre-releases never close an issue.">
</picture>

GitHub closes linked issues when a pull request is merged unless the repository turns this off: under *Settings → General → Issues*, clear **Auto-close issues with merged linked pull requests**. A repository without releases sets `close_with_release = false` in `config.toml`.
