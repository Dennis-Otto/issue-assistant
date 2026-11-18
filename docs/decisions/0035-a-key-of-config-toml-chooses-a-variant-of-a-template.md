# A key of config.toml chooses a variant of a template

- Status: accepted, amended by [0037](0037-a-failed-run-of-the-assistant-can-start-a-workflow-of-the-repository.md)
- Date: 2026-11-17

## Context

`check` fails when a workflow of a repository differs from its template in anything but its pins ([0006](0006-the-templates-exactly.md)), and `install` writes the templates anew. So a repository can't narrow a workflow, even when it does a part of its work in a workflow of its own.

`findings.yml` starts Findings at the end of every scan of CodeQL, OpenSSF Scorecard, Workflow security and OSV-Scanner, without a filter of the branch, so that the scan of a pull request dismisses its accepted alerts ([0028](0028-the-scan-of-a-pull-request-dismisses-its-accepted-alerts.md)). From 2026-11-10 to 2026-11-16, the scans of pull requests started about 3,600 of the 5,621 runs of Findings by `workflow_run` in the repository blueprint and the seven repositories that follow it; 475 of 759 in this one, as [#456](https://github.com/Dennis-Otto/issue-assistant/issues/456) counted them with the API of GitHub. The blueprint runs a workflow after these scans anyway, *After the checks*, and could dismiss the alerts there, but it can't keep its copy of `findings.yml` for `main`. A filter `branches: [main]` in the template would take the dismissal away from every repository without such a workflow.

`issue-lifecycle.yml` has the same shape of problem with its start at the end of `Release` ([0036](0036-the-end-of-release-starts-the-sweep-unless-the-release-starts-it.md)).

## Options

1. Let `check` accept any change of a trigger. A workflow could then lose a trigger by mistake, and nobody would see the closing or the dismissal stop.
2. Change the template for everyone. That takes the work away from every repository that has no other workflow for it.
3. A variant of the template, a file of its own that differs from it in exactly one thing, chosen by a boolean key of `config.toml` when it is `false`, as `[discussions] answer_categories` already chooses whether `discussion-assistant.yml` is there.

## Decision

Option 3. `VARIANT_KEYS` names the key of each template that has a variant: `[findings] pull_requests` for `findings.yml`, and `[releases] sweep_after_release_workflow` for `issue-lifecycle.yml`. With the key `false`, `install` writes the variant in `templates/variants/.github/workflows/`, and `check` expects it, exactly, but for the pins; without the key, or with it `true`, nothing changes. The variants stay out of `templates/.github/workflows/`, each of whose `*.yml` the repository blueprint takes over as a workflow of its own. A test keeps each variant equal to its template but for its one difference, and the Action test lints the variants with actionlint and zizmor as the templates.

The names say what the workflow does with the key `true`, in the words of the keys next to them: `pull_requests` of `[findings]` reads as *Findings after the scans of pull requests*, as `images` of `[attachments]` reads as *the attachments that are images*; `sweep_after_release_workflow` stands next to `close_with_release` and says when the sweep starts.

The variant of `findings.yml` has `branches: [main]` under `workflow_run`, which amends [0028](0028-the-scan-of-a-pull-request-dismisses-its-accepted-alerts.md): the dismissal of a pull request may run in another workflow that calls `reusable-findings.yml` on `workflow_run`. So that reusable workflow decides by the event of the scan alone, never by the name of the workflow that calls it, and its group `findings-pull-request-<branch>` is the same for every caller, so two callers never dismiss at once; a test keeps both so. With the key `false`, `check` fails while no other workflow of the repository starts on `workflow_run` and calls `reusable-findings.yml`, since nothing would dismiss the accepted alerts of the pull requests then, and when the default branch, as the event of the run or the remote `origin` names it, isn't `main`, which the variant names.

## Consequences

A key now chooses between two forms of one template, which amends [0006](0006-the-templates-exactly.md): the check is still exact, against the form that the settings choose. A repository that sets a key runs `install` again; one without the keys needs no `install`, since the release only moves its pins.

A change of a template with a variant changes the variant too, or the test of the difference fails. A third variant takes a key in `VARIANT_KEYS`, a file in `templates/variants/` and a test of its difference.

Such another caller runs after the scans of pull requests alone: after a scan of the default branch, it would run all of Findings, in the group `findings`, as a second run beside that of `findings.yml`. The pages of the configuration say so.
