# Limit the AI runs that strangers start, counting issues rather than answers

- Status: accepted
- Date: 2026-10-13

## Context

Every new issue of someone who doesn't maintain the repository starts a paid analysis, about $0.20 at the prices of the API, every answer of the reporter a follow-up, and every comment of a reporter in the 30 days after a release closed their issue another decision. Nothing limited them. The runs of one issue wait for each other, but those of different issues run side by side, so a flood of issues at once starts as many analyses at once. With the token of a subscription, which several repositories may share, a flood in one repository can also stop the analyses of all others.

The event job decides whether the AI runs, before any analysis exists. It has a write token for issues and nothing else to keep state in: the action keeps no database, and a comment that only counts would notify everyone who watches the issue.

## Options

1. Count the analyses and follow-ups that the assistant posted in the last 24 hours. It counts only runs that happened, but none that still run, so a flood at once passes the limit before the first comment appears; nor does it count an issue that the AI took for spam, which gets no comment, or one whose answer the checks refused. Those are exactly what a flood brings.
2. Count every new issue of the last 24 hours that someone who doesn't maintain the repository opened before this one, also one that a limit kept from the AI, and the follow-ups that the assistant posted. GitHub has every issue in its list as soon as it is opened, so each issue of a flood sees all those before it, in the order of their numbers, while their analyses still run. A repository that usually gets more new issues a day than its limit gets analyses again only when fewer came in.
3. Option 2 with a record of each run that a limit stopped, such as a label or a reaction, so that only the runs that happened count. A label needs a new entry in every repository's `labels.toml`; anyone can add a reaction, and so make the count wrong.
4. A spend limit of the engine's account alone. It caps the bill, but a flood then uses up the money of the month, and the token of a subscription has no such limit.

## Decision

Option 2, with three limits under `[engine]` in `config.toml`: `max_per_day` for the analyses of new issues and the follow-ups together, `max_per_author_per_day` for the analyses of one reporter's new issues, and `release_replies` for the comments of a reporter after each release that the AI reads. The defaults are 20, 3 and 2; issues, comments and runs by hand of maintainers are never limited and never count. It is the only option that holds against a flood at once and against issues that keep the AI from answering, with the data that GitHub already has, and its weakness, a busy repository that stays over its limit, has a plain remedy: a higher limit. A spend limit of the engine's account, as in option 4, is the advice of the documentation on top.

## Consequences

The event job reads the issues and the comments of the repository that changed in the last 24 hours before it starts an analysis, a follow-up or a reply after a release: two lists more for each such event. Over a limit, the issue keeps its labels and `needs-triage`, the run notes the limit in its summary and as a notice, and the maintainer takes over; nobody gets a comment about it. A limit that is no whole number of 0 or more fails `check` and stops the run, instead of lifting the limit. The README explains how to choose the limits, and the [security design](../security.md) names what strangers can still spend within them.
