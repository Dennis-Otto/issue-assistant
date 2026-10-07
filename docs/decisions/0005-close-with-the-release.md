# Close an issue with the release that ships its fix

- Status: accepted
- Date: 2026-10-07

## Context

GitHub closes an issue when the pull request that fixes it is merged. The reporter then learns that it is fixed, but can't use the fix until a release ships it.

## Options

1. GitHub's closing on merge.
2. Keep the issue open with a label until a release contains the fix, then close it with a link to the release.

## Decision

Option 2: the lifecycle workflow marks the issues that a merged pull request fixes and closes them when the release that ships the fix is published, with its link. Repositories turn off GitHub's own closing; one without releases sets `close_with_release = false`.

## Consequences

An open issue means that users don't have the fix yet. The release workflow and the lifecycle workflow depend on each other.
