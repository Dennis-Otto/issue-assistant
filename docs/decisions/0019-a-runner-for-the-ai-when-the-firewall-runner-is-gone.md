# A runner for the AI when the firewall runner is gone

- Status: accepted
- Date: 2026-10-25

## Context

[Decision 0002](0002-the-ai-only-reads.md) puts the AI behind GitHub's egress firewall: the job `analyze` of both reusable workflows runs on `ubuntu-24.04-firewall`, whose firewall blocks every host but those of the repository's `.github/egress-firewall.yaml`, for the whole machine and out of reach of every step. That runner is a technical preview. GitHub may rename it, end the preview, bring it to general availability under another label, or an account may lose it.

A job whose runner GitHub no longer has waits in the queue for one, up to a day; its `timeout-minutes` count only from its start. The templates put every run of one issue into one concurrency group whose queue waits, first in, first out, so every later event of the issue, a reminder, a follow-up or the reply after a release, waits behind the stuck run. No repository could move to another runner without a new release of the action, since the label lived in the reusable workflow, and the Canary noticed only when its job `start` timed out after 70 minutes, after a push.

## Options

1. Keep the firewall runner alone, and release a new version when its label changes. Every repository waits for that release, and for the pull request that moves its pin, while its issues get no analysis.
2. Run the AI on a plain runner behind Harden-Runner in every repository, as the jobs without the AI of [decision 0008](0008-the-jobs-that-write-reach-only-github.md). Its block lives inside the machine, where a step with sudo could lift it, and the engines need sudo to install what isolates them; in a private repository without a subscription of StepSecurity it blocks nothing.
3. Keep the firewall runner as the default, and let a repository name another runner in its variable `ISSUE_ASSISTANT_AI_RUNNER`, behind Harden-Runner with the hosts of its own firewall policy, checked before the AI runs.

## Decision

Option 3. `runs-on` of `analyze` is `${{ vars.ISSUE_ASSISTANT_AI_RUNNER || 'ubuntu-24.04-firewall' }}`, so with the variable unset every job runs as before. A repository can move at once, without a release, and back again; [the architecture](../architecture.md#waiting-runners-and-the-network) shows the paths in a diagram.

- **Only GitHub's runners of Ubuntu with a fixed version,** such as `ubuntu-24.04`. On any other runner, such as a self-hosted one, nothing would keep the AI to its hosts, and on `ubuntu-latest` a new image would reach the AI untested. The command `endpoints` in the job `event` refuses every other value and stops the run before the AI starts; `check` names it too, when the CI passes the variable.
- **The hosts of the firewall policy.** `endpoints` reads `.github/egress-firewall.yaml` of the repository and names its hosts for Harden-Runner, with `github.com` and `codeload.github.com`, which the firewall allows without the policy. The firewall allows a host whatever the port, Harden-Runner a host with its port, so each host gets 443 for HTTPS and 80 for HTTP, over which the archives of Ubuntu give the packages that an engine's action installs. The endpoints come from the policy itself, and a test of each engine compares them with its allow list, so the two lists can't drift apart.
- **The block first, and a test that it holds.** On that runner the first step of `analyze` is Harden-Runner in block mode with those endpoints. The next step asks for `example.com`, a host of no policy, and fails the job when the request gets through. Harden-Runner fails open, in a private repository without a subscription of StepSecurity or when its service is down, and the AI then doesn't run rather than run without a block.
- **The Canary measures the wait.** Its job *the AI's wait for its runner* reads the jobs of its run until `analyze` starts and shows the wait in its summary: a warning above 10 minutes and a failure above 30, also while `analyze` still waits. On `main`, the job then opens the issue of the bot-health bot for the Canary, or comments on the open one, so that a runner that never comes shows within the hour and no longer after a day: the run itself ends only when `analyze` does, up to a day later, and only then would GitHub mail its failure and bot-health look at it. The issue carries the state that bot-health keeps in its issues, so bot-health takes it over once the run ends and closes it when a Canary passes again.

## Consequences

The variable trades the block of the whole machine for a block of the job. The engines run no command that could lift it: Claude Code reads with three tools, Codex runs its commands in a sandbox without the network, and the Gemini CLI and the Copilot CLI keep to their tools that read. A step that could run commands with sudo would still reach past it, which the firewall never allows, and StepSecurity becomes a third party that receives what its agent records in `analyze` too, as [the security design](../security.md) says. The firewall runner stays the default for that reason, and the variable is a fallback, not a choice of comfort.

`tests/test_templates.py` pins the runner, the steps of the block and their condition in both reusable workflows, and runs the step that tests the block; `tests/test_setup.py` checks the endpoints of every engine and the refused runners; `tests/test_canary_queue.py` checks the limits of the wait and the issue it opens. The jobs `fallback-hosts` and `fallback` of the Action test run the block itself on `ubuntu-24.04` in every pull request: Harden-Runner with the hosts that `endpoints` names, a request to `example.com` that must fail, and requests to `registry.npmjs.org` on port 443 and `azure.archive.ubuntu.com` on port 80 that must pass. When GitHub moves the firewall runner to another label, a release changes `FIREWALL_RUNNER` and the default, and the repositories that named a fallback unset their variable.
