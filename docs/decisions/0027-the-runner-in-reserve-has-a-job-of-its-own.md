# The AI's runner in reserve has a job of its own

- Status: accepted, amends [0019](0019-a-runner-for-the-ai-when-the-firewall-runner-is-gone.md)
- Date: 2026-11-09

## Context

[Decision 0019](0019-a-runner-for-the-ai-when-the-firewall-runner-is-gone.md) let one job, `analyze`, run on either runner: `runs-on` was `${{ vars.ISSUE_ASSISTANT_AI_RUNNER || 'ubuntu-24.04-firewall' }}`, and the steps of Harden-Runner, the DNS step of [decision 0123 of the blueprint](https://github.com/Dennis-Otto/repo-blueprint/blob/main/docs/decisions/0123-harden-runner-v2-22-4-and-a-job-with-sudo-takes-githubs-dns-experiment-away.md) and the wait for GitHub's hosts carried a condition that skipped them on the firewall runner.

A condition of a step doesn't reach the pre step of its action. GitHub runs that one under the `pre-if` of the action, `always()` unless the action says otherwise, and Harden-Runner names none. So on the firewall runner, the default of every repository, *Pre Block every host but those of the firewall policy* ran in every analysis: the Canary of 9 November 2026 shows it asking `agent.api.stepsecurity.io` three times, which the firewall refused, then downloading and starting the agent, in block mode without a host. The agent then watched every lookup and connection of the job, the engine with its secret among them, beside the firewall that is the block there, and the step and its post step cost the job about 15 s. The comments of the workflows and [the security design](../security.md) said that Harden-Runner never runs on the firewall runner.

No input of Harden-Runner keeps its pre step from installing the agent. A step can't choose its action by an expression, and an action inside a composite action has no pre step, which is where Harden-Runner starts its agent.

## Options

1. Keep one job, and give the step of Harden-Runner an environment that its pre step reads as a runner without an agent, such as an empty `USER`. That leans on code of Harden-Runner that may change in any release, without a warning.
2. Two jobs, each with all its steps written out, and a test that keeps the shared steps equal. About 250 lines twice in each reusable workflow, and each change of a step made twice.
3. Two jobs, whose shared steps the YAML anchors and aliases that GitHub reads in a workflow take from one place.

## Decision

Option 3. `analyze` runs on `ubuntu-24.04-firewall` alone, while `ISSUE_ASSISTANT_AI_RUNNER` is unset or names that runner, and has no step of Harden-Runner. `analyze-in-reserve`, *Analyze with the AI engine on the runner in reserve*, runs on the runner of the variable otherwise: Harden-Runner, *Let Harden-Runner see the DNS lookups*, the check of the block, the wait for GitHub's hosts, then the aliases of the other steps of `analyze`, with its environment, permissions and outputs. The condition on the runner moved from the steps to the jobs, so that exactly one of them runs, under the same conditions otherwise. `apply` takes the answer of whichever ran, and writes out that the jobs that ran passed, since the skipped one would otherwise skip it too.

## Consequences

On the firewall runner no agent of StepSecurity runs beside the AI any more, and each analysis there is about 15 s shorter. The job graph of a run shows the skipped job of the other runner. The steps of both jobs are one text: actionlint and zizmor read the aliases, and zizmor applies its inline ignores to each of them; zizmor warns that its support of anchors is in beta.

`test_no_job_that_may_run_on_the_firewall_runner_has_a_step_of_harden_runner` in `tests/test_templates.py` fails for any job whose runner may be the firewall runner with a step of Harden-Runner, whatever its condition, and `test_the_ai_runs_on_another_runner_only_behind_harden_runner` keeps the steps of `analyze-in-reserve` after its own first ones equal to those of `analyze`, and the conditions of the two jobs each other's opposite.

**The way back:** should Harden-Runner get a `pre-if` that follows the condition of its step, or GitHub start to ask that condition for the pre step, the two jobs can become one again, with the conditions on the steps.
