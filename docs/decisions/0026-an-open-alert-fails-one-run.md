# An open alert fails one run

- Status: accepted
- Date: 2026-11-08

## Context

The Findings workflow of every repository runs after each scan of CodeQL, OpenSSF Scorecard, Workflow security and OSV-Scanner on the default branch, every morning, and when `.github/findings.toml` changes. It dismissed the alerts that `findings.toml` accepts and failed while any other alert was open. GitHub mails the person who started a run about every run that fails, so one alert that stayed open made up to four failed runs, and four mails, for each push to the default branch, and one more every morning. On 2026-11-08 one alert of Scorecard's *SAST* check failed more than twenty runs of the Findings workflow of the repository blueprint in a day, and the mails about it buried those of the runs that needed the maintainer.

A failed run is still the one signal that reaches a maintainer without any set-up, so a new alert should fail one. The job has `contents: read` and `security-events: write` alone, which every repository's copy of the template grants: a called workflow that asks for more than its caller grants doesn't start, and a new permission would need `install` in every repository.

GitHub sets `updated_at` of an alert when it opens, opens again after a fix, is fixed or is dismissed, and leaves it as it is while every scan finds the alert open again. The alerts of the blueprint's runs on 2026-11-08 showed that: the Scorecard alert opened again at 04:08, its `updated_at`, and kept that time through ten analyses after it.

## Options

1. Never fail, and only list the open alerts in the summary. Nobody would hear of a new alert.
2. Fail only for an alert that opened during the scan that started the run, by the times of `workflow_run` in the event. The job waits in the group `findings`, which keeps only the newest waiting run: a run that GitHub replaces loses the alerts of its scan, and a run of the morning or by hand knows no scan.
3. Fail only for an alert that opened since the run before, whose time the Actions API gives. That needs `actions: read`, which the template doesn't grant.
4. Keep a record of the alerts that a run reported, each with its `updated_at`, from run to run in the Actions cache of the default branch, which needs no permission of the token, and fail only for an alert that isn't in it with the same time.

## Decision

Option 4. A job `record` before the findings restores the newest record of its branch, `.issue-assistant/findings.json`, under the keys that start with `issue-assistant-findings-`, checks that it holds alert numbers and their times alone, in at most 64 KB, and hands it on as an output, which the findings put in place. A run fails for every open alert that the record lacks, or names with another time, because it opened again; it names the others as still open, each with a warning, which mails nobody. Only a run that finds a new alert writes the record, of the alerts that are open then, and the job keeps it in the cache under the hash of the file, also after the run failed, so the cache holds one entry for each change. A record that is missing or isn't one counts as none, so every open alert fails the run again: the cache can make a run fail once more, but never hide an alert. A dry run reads the record and writes none, so the dry runs of the Workflow test fail on an alert that the default branch hasn't reported yet, as before.

## Consequences

An alert fails one run when it opens and one when it opens again, instead of every run while it is open, and the first run after the update fails once for each alert that is open then, which has no record yet. The summary and the annotations of every run still name every open alert by its number and link, and the Security tab keeps it; a maintainer who wants to be reminded of an alert that stays open finds it there.

The job reaches a third host of GitHub, its results service `results-receiver.actions.githubusercontent.com`, besides `api.github.com` and `github.com` of [decision 0008](0008-the-jobs-that-write-reach-only-github.md); Harden-Runner allows the storage of the files of the cache by itself, as it does for its own cache in every job. The other jobs that write keep their two hosts, and the tests pin the three of the findings. A failing or hanging cache stops neither the findings nor the job: each step of the cache has a minute and may fail.

The cache of the default branch is shared by every workflow there, and one that runs untrusted code, such as one on `pull_request_target` or a compromised dependency on a push, could write an entry under the same keys. `actions/cache` unpacks an archive with every path that it holds, also outside the workspace, such as the action that the job runs next: so no job that may write unpacks a cache. The job `record` has no scope of the token, checks out nothing and runs no issue assistant, and an archive planted there can run code only in that job. What leaves it is a record that its check let through, which can only keep a run from failing on an alert: it can't dismiss anything, the run still names the alert, and the alert stays in the Security tab. The findings themselves only pack their record and send it, and a test keeps every job that may write from unpacking a cache. The [threats to the runs](../security/threats-to-the-runs.md) list it with its countermeasure.

The record changes only the reusable workflow, so it reaches a repository with a move of the pin, without `install`.
