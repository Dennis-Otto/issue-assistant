---
description: The waiting periods of the issue assistant and how to change them, which issues are exempt, and how threads closed long ago lock.
---

# Waiting periods, exemptions and old threads

[← All features](README.md)

`[lifecycle]` in `config.toml` sets each of these periods, exempts issues by label, assignee or 👍, and can lock threads closed long ago; the comments and `check` follow the settings.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/what-the-morning-sweep-does-with-an-issue-by-the-settings-of-its-lifecycle.dark.svg">
  <img src="../images/diagrams/what-the-morning-sweep-does-with-an-issue-by-the-settings-of-its-lifecycle.svg" alt="What the morning sweep does with an issue, by the settings of its lifecycle: The sweep reads the lifecycle section of config.toml and stops with an error when a period can't work, such as a closing before the reminder. An issue that waits for its reporter or for the closing as a duplicate is left to the maintainers when it is exempt: it has one of the exempt labels, pinned and security by default, it is assigned while exempt_assignees is true, or it has at least exempt_upvotes thumbs up. Otherwise it gets the reminder after remind_after_days, 15 by default, and closes after close_after_days, 30, or closes as a duplicate after duplicate_grace_days, 3. Only when lock_closed_after_days is set does the sweep lock an issue closed for longer, and at least the reopen window of a release, with one line that points to a new issue, unless it is exempt or someone unlocked it.">
</picture>

The days of [the life of an issue](life-of-an-issue.md) are the defaults of `[lifecycle]` in `config.toml`, and a repository with slow reporters or a busy queue sets its own: the reminder after `remind_after_days`, the closing after `close_after_days`, a duplicate after `duplicate_grace_days`, and the window of `reopen_after_release_days` in which the reporter's comment after a release reopens the issue. The comments name the days that the repository set, in each of their languages, and `check` fails when the description of `needs-info`, `stale` or `possible-duplicate` in `labels.toml` names other days.

- **Exempt issues** are left to the maintainers: those with a label of `exempt_labels`, `pinned` and `security` by default, those assigned to someone when `exempt_assignees` is `true`, and those with at least `exempt_upvotes` 👍. They get no reminder and never close on their own, also not as a duplicate; one that becomes exempt after its reminder loses `stale`.
- **Old threads lock** when `lock_closed_after_days` is more than 0: an issue closed for longer, and at least `reopen_after_release_days`, gets one line that points to a new issue and is locked as resolved, the oldest first and 25 in a sweep. An exempt issue stays unlocked, and so does one that someone unlocked.
