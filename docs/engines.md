---
description: The AI engines of the issue assistant compared, Claude, Codex, Gemini and Copilot, how to change the engine of a repository and how to add one.
---

# Engines

Bring the AI you already pay for, or start for free. Four engines write the analysis, and `name` under `[engine]` in `.github/issue-assistant/config.toml` chooses one. Everything except one step of the `analyze` job is the same for each of them: the context, the prompts in `prompts/`, the checks and every write. An engine gets the read-only checkout with the context in `.issue-assistant/`, the rules before its task, the JSON schema of its answer, and the model, turns, dollars and effort of the task from the step `engine`, and returns the JSON answer. `apply` checks every answer against the schema, also one that the engine couldn't enforce, in a code fence or with a sentence around it.

| Engine | `name` | Secret in the environment `issue-assistant` | What it costs |
| --- | --- | --- | --- |
| Claude by Anthropic, the default | `claude` | `CLAUDE_CODE_OAUTH_TOKEN`, the token of a Claude subscription that `claude setup-token` creates, or `ANTHROPIC_API_KEY` | the usage limits of the subscription, or the prices of Anthropic's API |
| Codex by OpenAI | `codex` | `OPENAI_API_KEY`, an API key of OpenAI | the prices of OpenAI's API |
| Gemini by Google | `gemini` | `GEMINI_API_KEY`, an API key of [Google AI Studio](https://aistudio.google.com/apikey) | **free** within the daily limits of the Gemini API's free tier, then its prices |
| Copilot by GitHub | `copilot` | `COPILOT_GITHUB_TOKEN`, a [fine-grained token](https://github.com/settings/personal-access-tokens/new) with the permission *Copilot Requests* alone and no access to repositories | the premium requests of the Copilot plan of the token's owner |

Each runs in one step of `analyze`, with only tools that read files of the checkout, a token of GitHub that only reads or none, and the hosts of its own block in `.github/egress-firewall.yaml`:

| Engine | Step | What keeps it to reading | The rules | The schema | Stops at |
| --- | --- | --- | --- | --- | --- |
| `claude` | `anthropics/claude-code-action` | `--restricted --tools "Read,Grep,Glob"`, no read of the issue assistant's own checkout | system prompt, `--append-system-prompt-file` | enforced, `--json-schema` | `max_turns` and `max_budget_usd` |
| `codex` | `openai/codex-action` | the permission profile `:read-only`: Codex reads with commands in a sandbox that can't write files or reach the network; `drop-sudo` keeps it from the API key, which the action's proxy holds; web search off | developer instructions | enforced in a strict form, `output-schema-file` | the time of the job |
| `gemini` | `analyze` runs the Gemini CLI | system settings above those of the repository: the tools `read_file`, `list_directory`, `glob` and `grep_search` alone, no MCP server, extension, agent, skill, hook or telemetry | the top of the prompt | asked for in the prompt | `max_turns` |
| `copilot` | `analyze` runs the Copilot CLI | `--available-tools view,glob,grep`, without GitHub's built-in MCP server, the temporary folder, questions or updates | the top of the prompt | asked for in the prompt | the time of the job |

The `analyze` command installs the Gemini CLI or the Copilot CLI from its lock file in `engines/` of the issue assistant with `npm ci`, which takes each package at its locked version and refuses one whose hash isn't the one of the lock file, without the scripts of its packages and without a secret in npm's reach. It runs the command line in a home of its own, with no variable of the job but the engine's secret, so it holds no token of the job. It writes the Gemini CLI's system settings with sudo into a folder below `/etc/issue-assistant` that root owns and nobody else may write to: the Gemini CLI skips system settings that another user could change, with no more than a warning, and `analyze` stops rather than run it without them. Copilot's secret is a token of GitHub of its own; give it the permission *Copilot Requests* alone and no access to repositories, so that it reaches nothing but Copilot. [Decision 0017](decisions/0017-engines-without-an-action-of-their-own.md) says why it runs them itself, and [the security design](security/engines.md#the-boundary-of-each-engine) where each engine's boundary lies.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/which-step-runs-the-engine-of-config-toml.dark.svg">
  <img src="images/diagrams/which-step-runs-the-engine-of-config-toml.svg" alt="Which step runs the engine of config.toml: The engine command reads the name under [engine] in config.toml and whether the engine's secret is in the environment issue-assistant. Without the secret, no engine runs, and labels and the lifecycle go on. With it, the context command writes the rules, the task and the schema of the answer, and one step of the job analyze runs the engine: Claude in the Claude Code action, with Read, Grep and Glob alone; Codex in the Codex action, in a sandbox that reads but neither writes nor reaches the network; Gemini or Copilot in the analyze command, which runs the vendor's command line with only its tools that read and without a token of GitHub. Every answer goes the same way to the job apply, which checks it before anything is written.">
</picture>

## Change the engine

1. Set `name` under `[engine]` in `.github/issue-assistant/config.toml`, and a `model` of that engine if you set one, such as `model = "flash"` for Gemini.
2. Add its secret to the environment, for example `gh secret set GEMINI_API_KEY --env issue-assistant`.
3. Give the firewall its hosts: `check` names each one that `.github/egress-firewall.yaml` lacks, and `install` writes the policy of the new engine alone into a repository without one.
4. Name the engine and its vendor in the notice of `SUPPORT.md`, as `check` asks, so that reporters know which AI reads their issue.

`check` then passes, and a run by hand under *Actions → Issue assistant → Run workflow* shows the first analysis of the new engine as a dry run.

## Add an engine

Add it to `ENGINES` in `issue_assistant.py`, with its secrets, the one that `install --setup` asks for first, how it gets the schema, and, for a command line without an action that keeps it to reading, a `Cli` with its package and the arguments that leave it only tools that read, and a folder `engines/<key>/` with a `package.json` that pins the package and its `package-lock.json`, made with `npm install --package-lock-only --ignore-scripts` in that folder, which Renovate keeps current. Give it a step with its key as `id` in `.github/workflows/reusable-issue-assistant.yml`, with the same condition as the other engines, add its secret to `ready-engines`, to the secrets of `workflow_call` there and to those that `templates/.github/workflows/issue-assistant.yml` passes by name, and its answer to the job output `answer`; do the same in `reusable-discussion-assistant.yml` and `discussion-assistant.yml`, whose job `analyze` a test keeps equal to the one of the issues; the Canary calls the reusable workflow, so it runs the new step as it is. Add its hosts to `templates/.github/egress-firewall.yaml` in a block of its own, which starts with `# Engine <name>:`, and its rules to `ENGINE_RULES` in `tests/test_templates.py`, which keeps it read-only. The check *engine contracts* of the Action test then checks the names of its boundary against the version that the step or the lock file pins: the inputs and outputs of its action, or the signatures of the packages of its lock file and the flags of its command line in its `--help`, the keys of its settings in their JSON schema, whose place goes into `SETTINGS_SCHEMAS` of `scripts/engine_contract.py`, and that it stops at the key alone without one. An action that installs a command line of its own goes into `ACTION_CLIS` there.
