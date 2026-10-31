# Four engines, and the command lines without a fitting action run in the action

- Status: accepted, amended by [0020](0020-patch-versions-of-the-engines-merge-on-their-own.md)
- Date: 2026-10-17

## Context

Claude was the only engine, so every repository needed a Claude subscription or an API key of Anthropic before it could try the assistant. Comparable tools let their users bring their own model: GitHub Agentic Workflows runs Copilot, Claude, Codex and Gemini, and Dosu's workflows run Claude, Copilot or Codex. The job `analyze` already keeps the engine in one step with a read-only token on the runner with the egress firewall, and `apply` checks every answer, so another engine changes one step.

[Decision 0002](0002-the-ai-only-reads.md) holds for each of them: the engine reads, and nothing it writes reaches GitHub unchecked. What keeps it to reading differs by vendor:

- **Codex** has an action of OpenAI, `openai/codex-action`, with a sandbox of its own: the permission profile `:read-only` lets the commands of Codex read files but neither write them nor reach the network, `drop-sudo` keeps it from the memory of the proxy that holds the API key, and `output-schema-file` makes it answer in the schema, in a strict form that wants every field. The action prints what Codex prints into the log of the job, where the runner reads a line that starts with `::` as a workflow command, so Codex runs with `--json`, which prints JSON events alone, one per line, and with `RUST_LOG=off`.
- **Gemini** has the action `google-github-actions/run-gemini-cli`. Its release v0.1.22 of 2026-04-24 always runs the CLI with `--yolo`, which approves every tool, writes its settings into the workspace's `.gemini/settings.json`, which the CLI ignores in a workspace that it doesn't trust and stops on in a run without a terminal, prints the prompt and the whole answer into the summary of the job, where no cleaning reaches them, and ends the output of the answer at a fixed line `EOF`, which an answer could hold.
- **Copilot** has no action. GitHub's guide installs the Copilot CLI with npm and runs it in a step of the workflow, which hands it every variable of the job, the token of GitHub too.

## Options

1. Use the actions where they exist, `run-gemini-cli` for Gemini and a step of the workflow for Copilot. The summary of every Gemini run would show the AI's words uncleaned, the guarantees of the security design for the log and the summary would no longer hold, and Copilot would hold the job's token.
2. Use the Codex action, and let the `analyze` command of the issue assistant run the Gemini CLI and the Copilot CLI: it installs the command line at the version that the workflow pins, without the scripts of its packages, gives it settings or arguments that leave it only tools that read, runs it in a home of its own with the engine's secret and no other variable of the job, and outputs its answer with a random delimiter. The vendor-specific parts are data in `ENGINES`, which the tests pin.
3. Run every engine through the `analyze` command, Claude and Codex too. Their actions add what a command line alone lacks, the sandbox of Codex and Claude Code's own handling of permissions, and the Canary tests Claude's action as every repository runs it.

## Decision

Option 2. Claude stays the default; Codex runs in its action, and Gemini and Copilot run in `analyze`. Every engine has a step with its key as `id`, the same condition, an entry in `ready-engines`, a branch of the output `answer`, a block of hosts of its own in the firewall template, of which `install` writes only the block of the engine of `config.toml`, and rules in `ENGINE_RULES` of `tests/test_templates.py`. The engines that can't enforce a schema get it as a file of the context folder, which their task names, and `apply` takes the object of JSON out of a code fence or a sentence around it before its checks, which are the same for every engine.

## Consequences

A repository chooses its engine with one setting, its secret and the hosts that `check` names, and can start for free with the free tier of the Gemini API. The vendor-specific parts of the script are in `ENGINES` alone, so the rest stays without the names of vendors, as a test keeps it.

The Gemini CLI and the Copilot CLI keep to reading by their own settings, not by a sandbox of the system, and Codex reads through commands in its sandbox; [the security design](../security/engines.md#the-boundary-of-each-engine) names each boundary, and the residual risks say what a fault of one would reach. Only Claude counts dollars and reports the cost of a run; Gemini stops at its turns, Codex and Copilot at the time of the job. The Canary runs only the engine of this repository, Claude, so a new version of another engine's action or command line reaches the repositories untested by a run of the AI, and its update waits for the maintainer.

## Update of 2026-10-25: the command lines come from lock files

`analyze` installed the Gemini CLI and the Copilot CLI with `npm install` at the version that the workflow pinned, so npm resolved every package below them anew in each run, with the engine's secret in the job: a broken or malicious release of one of their dependencies would have reached every repository at once, with no change here, and the run differed from what any test saw. Now each has a folder in `engines/`, with a `package.json` that pins it and a `package-lock.json` with the SHA-512 hash of every package, which `analyze` installs with `npm ci --ignore-scripts`, in a folder of its own and without a secret in npm's environment. The input `engine-version` of the action is gone, since the lock file names the version. Renovate's manager of npm updates the lock files, and its pull requests wait for the maintainer, as before.

npm verifies the signatures of npm's registry and the attestations of the packages, with `npm audit signatures`, in the check *engine contracts* of every pull request that changes a lock file and every week, not in `analyze`: the hashes bind every package to the one that was verified, and the attestations need Sigstore's root of trust, `tuf-repo-cdn.sigstore.dev`, which would be one more host in the firewall of the job with the secret, and in the policy of every repository, for no new finding. The check also makes sure that npm refuses a copy of the lock file with another hash of the command line. [The security design](../security/engines.md#the-packages-of-the-engines) shows the whole path.
