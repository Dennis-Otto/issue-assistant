# The scan of a pull request dismisses its accepted alerts

- Status: accepted
- Date: 2026-11-09

## Context

The Findings workflow dismissed the alerts that `.github/findings.toml` accepts only after the scans of the default branch, every morning and after a push of `findings.toml`. GitHub posts each alert that the scan of a pull request finds in the lines that it changes as a review thread of `github-advanced-security`, and a ruleset with *Require conversation resolution before merging*, as the repository blueprint sets for `main`, blocks the merge until every thread is resolved.

A pull request that only moves an accepted finding, such as a false positive of CodeQL on a line that now has another number, gives it another fingerprint, and so a new alert that only the scans of the pull request know: the runs of the default branch see it after the merge, too late for its thread. On 2026-11-09 the thread of such an alert, a false positive of `actions/untrusted-checkout/medium` that `findings.toml` accepts for `reusable-discussion-assistant.yml`, blocked [#319](https://github.com/Dennis-Otto/issue-assistant/pull/319) until the maintainer resolved it by hand.

GitHub resolves the thread of an alert when the alert is dismissed, in the name of whoever dismissed it: the threads of [#128](https://github.com/Dennis-Otto/issue-assistant/pull/128) show `github-actions[bot]` as the one that resolved them, after the Findings workflow had dismissed their alerts on 2026-10-17. The code scanning API lists the alerts of a pull request with its parameter `pr`, and the event `workflow_run` of a scan names the pull requests of its branch in `workflow_run.pull_requests`, for those of the repository itself alone.

## Options

1. Leave it: a person resolves each such thread. Every move of an accepted finding blocks a merge until then, also in the pull requests of the bots, whose merge waits for nobody else.
2. Accept the rule in every file. That accepts every real finding of the rule as well.
3. Read `findings.toml` of the pull request. A pull request could then accept its own findings, before anyone reviewed the acceptance.
4. After the scan of a pull request, dismiss the alerts of the pull request that `findings.toml` of the default branch accepts:
   1. for every pull request, those of forks included;
   2. for the pull requests of the repository itself alone.

## Decision

Option 4.2. After the scan of a pull request whose head is in the repository itself, the job `findings` runs too, while the job `record` skips it. The command `findings` reads the pull requests from `workflow_run.pull_requests` of the event, lists the open alerts of each with `pr`, and dismisses those that an entry of `findings.toml` covers, with the entry's reason and comment, as on the default branch. The checkout of a run of `workflow_run` is the default branch, so the acceptances are those that a review let in.

Such a run reads and writes no record, and fails for no alert: the pull request's own check of code scanning shows the other alerts, and its summary names them by number and link only. Its job waits in a group of the branch of the pull request, `findings-pull-request-<branch>`, never in `findings`, where it would take the place of a waiting run of the default branch; a newer scan of the branch takes the place of an older waiting one, and lists every open alert of the pull request again.

## Consequences

The thread of an accepted alert that a pull request moves is resolved by `github-actions[bot]` right after the scan, and the alert stays dismissed after the merge, since the scan of the default branch finds the same fingerprint. Up to three runs follow a push to a pull request, after CodeQL, Workflow security and OSV-Scanner, each of a few seconds on `ubuntu-24.04` behind Harden-Runner.

An entry that a pull request adds to `findings.toml` counts once it is merged: the next scan of the pull request after that dismisses the alert, or a person resolves its thread before.

The threads of a fork's pull request stay with a person. An outsider's code could match an accepted rule in an accepted file with a real finding, such as an untrusted checkout in a workflow whose false positive the repository accepts, and the thread is where a reviewer sees it before the merge. For a pull request of the repository itself, whose author can push to it and dismiss alerts anyway, the dismissal follows only the acceptances of the default branch, and it dismisses only what the runs of the default branch would dismiss after the merge: an entry with a `path` keeps that narrow.

The job keeps `contents: read` and `security-events: write`, which every repository's copy of the template grants, and the change reaches a repository with a move of the pin, without `install`.
