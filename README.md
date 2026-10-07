# Issue assistant

[![Tests](https://github.com/Dennis-Otto/issue-assistant/actions/workflows/tests.yml/badge.svg)](https://github.com/Dennis-Otto/issue-assistant/actions/workflows/tests.yml)
[![CodeQL](https://github.com/Dennis-Otto/issue-assistant/actions/workflows/codeql.yml/badge.svg)](https://github.com/Dennis-Otto/issue-assistant/actions/workflows/codeql.yml)
[![Secret scan](https://github.com/Dennis-Otto/issue-assistant/actions/workflows/secret-scan.yml/badge.svg)](https://github.com/Dennis-Otto/issue-assistant/actions/workflows/secret-scan.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/Dennis-Otto/issue-assistant/badge)](https://scorecard.dev/viewer/?uri=github.com/Dennis-Otto/issue-assistant)

A GitHub Action that looks after the issues of a repository:

- **A first analysis of every new issue** by an AI that only reads: a summary, the likely cause with links to the lines of code and the documentation involved, what the reporter can try, the information still missing, and related issues and discussions, in English or German.
- **Labels** from the issue forms and from the analysis, each with its reason.
- **Duplicates:** a sure duplicate gets a notice and closes, linked to the original, three days later unless someone objects; less sure ones become suggestions for the maintainer.
- **Unanswered questions:** questions of the assistant or the maintainer mark an issue `needs-info`; after 15 days the reporter gets a reminder, after 30 days the issue closes, and an answer reopens it.
- **Labels as code** in `.github/labels.toml`.

The maintainer reads every issue and has the last word. It runs in [ha-autodarts](https://github.com/Dennis-Otto/ha-autodarts), [Paperless Unified Search](https://github.com/Dennis-Otto/paperless-unified-search) and [Paperless Sync](https://github.com/Dennis-Otto/paperless-sync).

## How it works

```mermaid
flowchart LR
  E["event<br/>no AI: labels,<br/>next task"] --> A["analyze<br/>AI reads the checkout,<br/>answers with JSON"]
  A --> P["apply<br/>no AI: checks the answer,<br/>the only job that writes"]
  S["sweep, daily<br/>no AI: reminders,<br/>closing, duplicates"]
```

Three workflows run in the repository:

| Workflow | When | What |
| --- | --- | --- |
| `issue-assistant.yml` | new issues, edits of their reporters, new comments, by hand | labels from the form, the first analysis, follow-ups when the reporter answers, and whether a maintainer's comment waits for the reporter |
| `issue-lifecycle.yml` | every morning | reminders after 15 days, closing after 30 days, closing duplicates 3 days after the notice |
| `labels.yml` | when `.github/labels.toml` changes on main | creates and updates the labels; never deletes one |

Every task of the AI is one of three: `triage` for a new issue, `follow-up` when the reporter answered the assistant's questions (at most twice, and never once the maintainer joined the conversation), and `maintainer-reply` to decide whether a maintainer's comment waits for the reporter.

## Security

Issues come from anyone, so the design assumes that an issue tries to steer the AI.

- **The AI only reads.** It runs in the `analyze` job, with a read-only token, on GitHub's [egress-firewall runner](https://github.com/github-early-access/actions-native-egress-firewall) with an allow list in `.github/egress-firewall.yaml`. Claude Code runs with `--restricted` and the tools `Read`, `Grep` and `Glob` alone: no commands, no web pages, no files outside the checkout. The issue, the other issues and the discussions reach it as files that the prompt calls data, not instructions.
- **Nothing is posted unchecked.** The AI's answer is JSON with a fixed schema. The `apply` job, which runs no AI and never sees its secret, checks it against the schema, the repository's labels and the issues that exist, refuses anything that looks like a token or key, turns mentions into code, drops images, HTML and links to other sites, and links files only when git tracks them. It writes only to the issue of the event.
- **The secret stays in one place:** the environment `issue-assistant`, which only the default branch may use.
- **Every repository uses exactly the templates.** `check` fails when a workflow differs from its template in anything but the commit hashes of its actions. The tests of this repository pin the rules of the templates.

Report vulnerabilities privately, as described in [SECURITY.md](SECURITY.md).

## Set up a repository

1. **Install the workflows** with the commit hash and tag of the [latest release](https://github.com/Dennis-Otto/issue-assistant/releases/latest), from a checkout of this repository, in the root of your repository:

   ```sh
   python3 /path/to/issue-assistant/issue_assistant.py install --ref <commit-hash> --version <tag>
   ```

   It writes the three workflows, `.github/egress-firewall.yaml` and the `.gitignore` entry for the context folder, and, when they are missing, starters of the files below.
2. **Describe the project** in `.github/issue-assistant/project.md`: what it does, where the code, documentation, troubleshooting guide and changelog are, which versions and logs matter in a bug report, and how to address German reporters. The AI reads it before every task.
3. **Define the labels** in `.github/labels.toml`. The six lifecycle labels are required; kinds, areas and topics are what the AI chooses from.
4. **Adjust `.github/issue-assistant/config.toml`:** the engine, the hosts that links may lead to, the field of the issue forms that chooses an area, and the files that tell reporters which AI reads their issue.
5. **Tell reporters** in `SUPPORT.md` (or the files you list) that the issue assistant uses Claude, an AI by Anthropic.
6. **Add the check** to the CI of the repository:

   ```yaml
   - uses: actions/checkout@<commit-hash> # vX
     with:
       persist-credentials: false
   - name: Check the issue assistant's set-up
     uses: Dennis-Otto/issue-assistant@<commit-hash> # vX.Y.Z
     with:
       command: check
   ```

7. **Create the environment** `issue-assistant`, limited to the default branch, and add the engine's secret. For Claude, `claude setup-token` creates a token for a Claude subscription; store it as `CLAUDE_CODE_OAUTH_TOKEN`, or an API key as `ANTHROPIC_API_KEY`:

   ```sh
   gh api -X PUT repos/OWNER/REPO/environments/issue-assistant -F "deployment_branch_policy[protected_branches]=false" -F "deployment_branch_policy[custom_branch_policies]=true"
   gh api -X POST repos/OWNER/REPO/environments/issue-assistant/deployment-branch-policies -f name=main -f type=branch
   gh secret set CLAUDE_CODE_OAUTH_TOKEN --env issue-assistant --repo OWNER/REPO
   ```

8. If the repository runs actionlint, list the runner `ubuntu-24.04-firewall` under `self-hosted-runner.labels` in `.github/actionlint.yaml`.

Without the secret, labels, reminders, closing and reopening keep working. The repository variable `ISSUE_ASSISTANT_AI` set to `off` switches the AI off.

Updates arrive as Dependabot pull requests that move the commit hash of the action in the workflows. When a new release changes the templates, its release notes say so; run `install` again.

## Settings

### `.github/labels.toml`

```toml
[[label]]
name = "area: cards"
color = "bfdadc"
description = "Dashboard cards"          # at most 100 characters
group = "area"                            # type, area, topic, lifecycle, decision, release or dependabot
form_options = ["Live card", "Training card"]   # answers of the Area field that choose it

[[label]]
name = "enhancement"
color = "a2eeef"
description = "New feature or improvement"
group = "type"
ask = false                               # never ask reporters of this kind for information
```

### `.github/issue-assistant/config.toml`

```toml
[engine]
name = "claude"                 # the engine of ENGINES
# model = "claude-opus-5-5"     # the engine's default model if left out

[links]
hosts = ["docs.example.com"]    # besides this repository

[forms]
area_field = "Area"
unmapped_options = ["Other"]

[transparency]
files = ["SUPPORT.md"]
```

## Engines

Everything except one step of the `analyze` job is independent of the AI: the context, the prompts in `prompts/`, the checks and every write. An engine gets the read-only checkout with the context in `.issue-assistant/`, the prompt and the JSON schema of its answer (as files and as outputs of the context step), and returns the JSON answer. `apply` validates it whether or not the engine could enforce the schema, and also accepts it in a code fence.

| Engine | Step | Secret | Restrictions |
| --- | --- | --- | --- |
| `claude` (default) | `anthropics/claude-code-action` | `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` | `--restricted --tools "Read,Grep,Glob"`, schema with `--json-schema` |

To add one, for example OpenAI Codex with `openai/codex-action` (`prompt-file`, `output-schema-file` and a read-only sandbox) or the GitHub Copilot CLI in programmatic mode (only reading tools, the JSON asked for in the prompt): add it to `ENGINES` in `issue_assistant.py`, give it a step with its key as `id` in `templates/.github/workflows/issue-assistant.yml` with the same condition as the Claude step, add its secret to `ready-engines` and its answer to the job output `answer`, add its hosts to the firewall template in a block of its own, and its rules to `ENGINE_RULES` in `tests/test_templates.py`, which keeps it read-only.

## Commands

The action runs `issue_assistant.py` with one command:

| Command | Job | What |
| --- | --- | --- |
| `event` | event | handles the event, sets labels and names the AI's next task |
| `engine` | analyze | names the engine of `config.toml`, its model, and whether its secret exists |
| `context` | analyze | writes the issue, the other issues, the discussions, the labels, the prompt and the schema into `.issue-assistant/` |
| `apply` | apply | checks the AI's answer and posts it |
| `sweep` | lifecycle | reminds, closes unanswered issues and duplicates |
| `sync-labels` | labels | creates and updates the labels |
| `check` | your CI | checks labels, issue forms, workflows, firewall, settings and the notice to reporters |

`install` runs from a checkout, as above. Every command but `install` and `check` takes `dry-run: true` to only report what it would write; runs by hand under *Actions → Issue assistant → Run workflow* start as dry runs, and their summary shows the comment.

## Try a prompt locally

```sh
export GH_REPO=OWNER/REPO DRY_RUN=true
python3 issue_assistant.py context --issue 12 --mode triage    # in the repository's checkout
claude -p "$(cat .issue-assistant/prompt.md)" --restricted --tools "Read,Grep,Glob" \
  --json-schema "$(cat .issue-assistant/schema.json)" --output-format json > answer.json
RESULT="$(jq -c .structured_output answer.json)" python3 issue_assistant.py apply --issue 12 --mode triage
```

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md). The script needs only Python 3.12's standard library, `gh` and `git`, which GitHub's runners have; `check` reads YAML with PyYAML or `yq`.

## License

[MIT](LICENSE)
