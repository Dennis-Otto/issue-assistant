# The jobs that write reach only GitHub

- Status: accepted
- Date: 2026-10-13

## Context

[Decision 0002](0002-the-ai-only-reads.md) puts the AI behind GitHub's egress firewall, with a read-only token. The other jobs of the templates, `event`, `apply`, `sweep`, `sync-labels` and `findings`, run no AI, but they hold the tokens that write: issues, labels and the alerts of code scanning. They ran on `ubuntu-latest` without any control of their network. A compromised action or tool in one of them, such as a poisoned release of an action that a pin was moved to, could send the token anywhere during the job.

The repository blueprint runs Harden-Runner in audit mode in its own workflows, which records the connections of a job but prevents none. These jobs need two hosts: `github.com` for the checkout and `api.github.com` for the GitHub CLI.

## Options

1. Harden-Runner in audit mode, as in the blueprint's workflows today. It shows a connection afterwards, to someone who looks, and stops nothing.
2. GitHub's egress-firewall runner for every job. Its policy is one file for the whole repository, so the jobs that write would also reach the hosts of the engine, and the runner is a technical preview whose label actionlint doesn't know.
3. Harden-Runner in block mode in every job without the AI, as its first step, with `api.github.com:443` and `github.com:443` alone, and with sudo and containers turned off, so that no later step can lift the block. The AI stays behind the firewall of decision 0002.

## Decision

Option 3. Each kind of job gets the control that fits it: the firewall with the engine's hosts for the AI, a block with GitHub's two hosts for the jobs that write. Harden-Runner allows the hosts of the runner itself, such as those of the logs and the summary, and the action turns off the telemetry of the GitHub CLI, which would only knock at a blocked host. Every job runs on `ubuntu-24.04`, the version of the firewall runner, so that a new image behind `ubuntu-latest` never reaches the repositories untested.

## Consequences

`tests/test_templates.py` pins the block, its two hosts and the runner of every job. The job `egress` of the Action test runs the action behind the same block, in dry runs of `sweep` and `sync-labels`, so a host that the action or a new GitHub CLI starts to need fails a check here before a release blocks it in every repository. A new host is a change of the templates, with its own release.

Harden-Runner reports the connections of each job to StepSecurity, whose insights show them, as for the blueprint's own jobs. A move to a newer Ubuntu is a change of the templates too, tested here first. The blueprint can take the same block over for its own jobs that write.
