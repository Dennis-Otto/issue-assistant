# The end of Release starts the sweep unless the release workflow starts it

- Status: accepted
- Date: 2026-11-17

## Context

The sweep of `issue-lifecycle.yml` closes the issues that a release fixed ([0005](0005-close-with-the-release.md)). A release that the job's `GITHUB_TOKEN` publishes, as release-please does with its default token, starts no workflow by `release: published`, so the template also starts the sweep at the end of every run of a workflow named `Release`. The job skips a run of `Release` that failed or that a schedule started, but not one that published nothing.

From 2026-11-10 to 2026-11-16, `Release` ran 553 times in the repository blueprint and the seven repositories that follow it, and published 56 releases: about 500 runs of Issue lifecycle followed a run of `Release` that released nothing, as [#457](https://github.com/Dennis-Otto/issue-assistant/issues/457) counted them with the API of GitHub. The blueprint will start the sweep from its release workflow itself, only when a run published a release, and then the start at the end of `Release` only adds a second sweep or one that finds nothing. Its copy of `issue-lifecycle.yml` can't leave it out, since `check` compares it with the template ([0006](0006-the-templates-exactly.md)).

## Options

1. Take `workflow_run` out of the template. Every repository whose release workflow is named `Release` and publishes with the job's token would close its fixed issues only with the morning sweep, a day later at most, until it adds a start of its own, and nothing would tell it so.
2. Keep the template, and let a repository whose release workflow starts the sweep choose the variant without `workflow_run`, by a key of `config.toml` ([0035](0035-a-key-of-config-toml-chooses-a-variant-of-a-template.md)).

## Decision

Option 2. The template keeps the start at the end of `Release`, so that nobody loses the closing at the release. `[releases] sweep_after_release_workflow = false` makes `install` write, and `check` expect, `issue-lifecycle.yml` without the `workflow_run` on `Release`, and with no other difference.

A release workflow starts the sweep with a step after the publication, `gh workflow run issue-lifecycle.yml -f dry_run=false`, with `actions: write`: a run by `workflow_dispatch` is GitHub's exception to the rule that the job's token starts no workflow. The page *From the fix to the release* shows the step, and the set-up names it.

## Consequences

`check` can't see whether a release workflow starts the sweep: a repository that sets the key without the step closes its fixed issues with the morning sweep. The pages say so, next to the key.

The sweep after `release: published`, after a merged pull request, every morning and by hand stays as it is; the job still skips a run of `Release` that failed or was scheduled, and a prerelease still closes nothing. A repository without the key needs no `install`.
