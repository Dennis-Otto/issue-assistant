# A job stops when Harden-Runner doesn't block

- Status: accepted
- Date: 2026-11-09

## Context

[Decision 0008](0008-the-jobs-that-write-reach-only-github.md) puts every job that writes behind Harden-Runner in block mode, and names its fail-open as a residual risk. Harden-Runner goes on without its agent when the agent can't be downloaded, when StepSecurity answers at the start of a job that its service is unavailable, or when the agent doesn't start. Its pre step then ends green, and the job runs with its token and no block at all. On 8 November 2026, GitHub's outage answered the download of the agent with 504 in some jobs of the repositories of the blueprint, which ran without their block and passed.

The repository blueprint answered with its [decision 0134](https://github.com/Dennis-Otto/repo-blueprint/blob/main/docs/decisions/0134-a-job-stops-when-harden-runner-doesnt-block.md): every job of its workflows that blocks runs the step *Make sure that Harden-Runner blocks* right after Harden-Runner and before its warm-up. The step reads the status that the agent writes once its firewall stands, `/home/agent/agent.status`, and asks systemd whether the service `agent` runs; it needs no host, no token and no sudo. The workflows of the blueprint here have it since v1.15.1; the workflows of this project alone had it in none of their 20 jobs that block, among them `event`, `apply`, `sweep`, `sync-labels` and `findings` of the reusable workflows, which every repository that uses the assistant runs with a token that writes.

In a private repository whose owner has no subscription of StepSecurity, the agent starts and switches itself to recording alone ([`agent.go` of its v0.18.1](https://github.com/step-security/agent/blob/v0.18.1/agent.go)): it writes `Initialized` and runs, so the step passes there, and the job runs without the block, as before.

## Options

1. Leave it: a residual risk that the security design names. A job that runs without its block during an outage passes, and nobody sees that it did.
2. Warn and go on. The token stays unprotected, and a warning in a green run is easy to miss.
3. Stop the job at the step of the blueprint, with its very script, before every step that could reach a host or get a secret, and keep the steps that run after a failure, such as the explanation of the PR coach, from running without the block.

## Decision

Option 3, as the blueprint decided for its jobs. Every job of this project that blocks runs the step with the blueprint's script, right after Harden-Runner and, where a job keeps sudo, after *Let Harden-Runner see the DNS lookups*: before the warm-up, and in `analyze-in-reserve` and its test before *Make sure that the network block holds*, which proves the block with requests before the AI runs. That step would stop a job without an agent too, after a minute and with a message about the network; this one stops it at once, with the reason. A step that runs after a failure, on `always()`, `failure()` or `!cancelled()`, also has the condition `steps.blocks.outcome != 'failure'`, but the wait for the removal of Docker, which needs no host, and a step that only follows a later one, which didn't run.

## Consequences

A job that the step stopped fails with the annotation *Harden-Runner doesn't block*, which names the reason and says that it can run again. During an outage of GitHub or of StepSecurity, an issue, a comment or a discussion of that moment gets no answer, and a change of `labels.toml` no labels, until someone runs the failed job again; the sweep and the findings catch up on their next daily run. That is the trade-off: no job of the assistant writes to a repository without the block that it promises.

A repository that sets the custom property `skip-harden-runner` of StepSecurity to `true` skips Harden-Runner, and every job of the assistant that blocks stops there now. The jobs that write need the block, so such a repository takes the property away for itself.

`tests/test_templates.py` checks the place, the id, the time limit and the guards of the step in every job of this project that blocks, runs its script in each case, and keeps it equal to the step of the blueprint's workflows here, so that an update of the blueprint that changes it fails until the copies follow. The change reaches a repository with a move of the pin of its workflows, without `install`.
