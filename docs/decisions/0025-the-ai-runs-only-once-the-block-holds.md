# The AI runs only once the network block holds

- Status: accepted
- Date: 2026-11-08

## Context

[Decision 0002](0002-the-ai-only-reads.md) puts the AI behind GitHub's egress firewall, and the security design promises that its network reaches only the hosts of `.github/egress-firewall.yaml`. Nothing checked that at run time on `ubuntu-24.04-firewall`, the default runner of the AI. Only on a runner in reserve, behind Harden-Runner ([decision 0019](0019-a-runner-for-the-ai-when-the-firewall-runner-is-gone.md)), did a step ask for `example.com` before the AI ran, since Harden-Runner fails open.

The firewall is a technical preview. Its README says that a job without the permission `contents: read` gets no policy from its file, and that the job may then run without a warning that the policy wasn't loaded. It also says that the rule format, the schema, how the policy is found and the APIs may change. `check` reads the file, and its `mode`, only as text. A change of the preview, or a policy that GitHub doesn't load, would quietly leave the AI of every repository with open network access, next to the engine's secret. The jobs of each repository run what a release of the issue assistant pins, so no single repository would notice.

## Options

1. Trust the firewall, and read the README of the preview now and then. A change shows only when someone looks, and the AI meanwhile runs without a block in every repository.
2. Check the policy file harder in `check`. That proves only what the file says, not what GitHub enforces.
3. Ask the network itself before the AI runs, on every runner: a host outside every policy has to stay out of reach, and a host of every policy has to answer. The job stops otherwise.

## Decision

Option 3. The step *Make sure that the network block holds* runs in the job `analyze` of both reusable workflows and in the job `replay` of the Evaluate workflow, without a condition, before every step that gets the engine's secret or runs an engine. It replaces the step that asked only behind Harden-Runner, so there is one step, in one script, which a test keeps equal in every job.

- **A host outside every policy stays out of reach.** Two requests to `https://example.com` in a row, five seconds apart, have to fail, within a minute: one failure could be a hiccup of example.com, and Harden-Runner lets the job go on while its agent still starts. Every failure counts, whether the name doesn't resolve, no connection comes or no answer comes in time; only an answer counts as getting through. The egress firewall refuses the name of a host outside the policy, so there curl ends with code 6, *Could not resolve host*, for HTTPS and HTTP alike.
- **A host of every policy answers.** One request to `https://api.github.com/` has to get an answer within a minute, so that a runner whose network reaches no host isn't taken for one that blocks.
- **The job stops otherwise.** The step writes an error that names the likely causes, a job without `contents: read`, `mode: log` in the policy, a change of the preview, or Harden-Runner failing open, and how to keep the AI from running meanwhile, `ISSUE_ASSISTANT_AI=off`. The AI doesn't run rather than run without a block.
- **The Action test proves both ways on every pull request and every week.** Its job *the network block of the AI's runner* runs the step on `ubuntu-24.04-firewall` with this repository's policy and records in its summary how the firewall refused `example.com`. Its job *the probe of the network block on a runner without one* runs the step of the reusable workflow on plain `ubuntu-24.04`, where nothing blocks, and fails when the step passes there or stops without its message. The job behind Harden-Runner runs the same step.
- **The Canary runs it in its `analyze`,** on the firewall runner, every Monday and after every change on `main` of what the path runs; a failure there opens the issue of the bot-health bot.

The measurement on 2026-11-08, before the step went into the workflows, showed the block: on `ubuntu-24.04-firewall`, both requests to `example.com` ended with code 6 and the name resolved to no address, and on plain `ubuntu-24.04` the step stopped with its message.

## Consequences

Each analysis waits about five seconds longer on the firewall runner: the first two requests fail at once, five seconds apart. Should GitHub's preview change so that it no longer blocks, every analysis stops with the error until a maintainer looks, rather than run the AI with open network access; the Action test of the next pull request and the Canary show the same. Should the firewall one day answer a host outside the policy in a way that the step doesn't count as a failure, such as with a page of its own, the step stops the AI too, and the measurement in the summary of the Action test shows what changed.

`tests/test_templates.py` pins the step, its missing condition, its place before every step with a secret or an engine in the three jobs, its one script and its errors, and runs it against requests that fail or answer; it pins the two jobs of the Action test too. [Decision 0002](0002-the-ai-only-reads.md) and [decision 0019](0019-a-runner-for-the-ai-when-the-firewall-runner-is-gone.md) keep their place: this one adds the proof at run time to the block of both runners.
