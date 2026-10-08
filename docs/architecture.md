# Architecture

[← README](https://github.com/Dennis-Otto/issue-assistant) · [Security design](security.md) · [Roadmap](roadmap.md)

The issue assistant is a composite GitHub Action. `action.yml` runs `issue_assistant.py` with the Python 3.12 of GitHub's runners and the standard library alone; the script talks to GitHub through the GitHub CLI. Four workflows in the repository that it looks after call the action, and one step of one job runs the AI engine.

## Components

| Component | Files | What it does |
| --- | --- | --- |
| The action | `action.yml` | Passes every input through the environment to `issue_assistant.py` and runs one command of it |
| Commands | `main()` in `issue_assistant.py` | `event`, `engine`, `context`, `apply`, `sweep`, `sync-labels`, `findings`, `check` and `install`; [the README](https://github.com/Dennis-Otto/issue-assistant#commands) names what each does |
| GitHub client | the class `GitHub` | Every read and write through `gh api`; in a dry run it only records what it would write |
| Settings | `load_config`, `load_labels`, `load_findings` | Read `.github/issue-assistant/config.toml`, `.github/labels.toml` and `.github/findings.toml` of the repository |
| Events | `handle_event` | Labels from the issue forms, and the next task of the engine: `triage`, `follow-up`, `maintainer-reply` or `release-reply` |
| Context | `write_context` | Writes the issue, its comments, related issues and discussions, the labels, the prompt and the JSON schema of the answer into `.issue-assistant/`, for the engine to read |
| Prompts | `prompts/` | The rules of every task, which call the content of the issue data, not instructions |
| Checks of the answer | `answer_schema`, `validate`, `parse_answer`, `looks_secret`, `sanitize` | Check the engine's JSON against its schema, the labels and the issues that exist; refuse anything that looks like a secret; clean the text of mentions, images, HTML and links to other sites |
| Writing | `apply_answer` and the `render_*` functions | Post the comment, set the labels and mark duplicates, only on the issue of the event |
| Lifecycle | `sweep` | Reminders after 15 days, closing after 30 days, closing duplicates after 3 days, marking fixed issues, closing them with the release and filing them under its milestone |
| Labels | `sync_labels` | Create and update the labels of `labels.toml`, never delete one |
| Findings | `watch_findings` | Dismiss the findings of code scanning that `findings.toml` accepts and fail while others are open |
| Set-up | `install`, `check` | Write the four workflows and the starter files into a repository, and check that its set-up still fits the templates |

## The workflows of a repository

```mermaid
flowchart LR
  changed(["change of<br/>labels.toml"]) --> sync
  subgraph labelling ["labels.yml"]
    sync["<b>sync-labels</b><br/>creates and updates<br/>the labels"]
  end
  scanned(["code scanning"]) --> findings
  subgraph scanning ["findings.yml"]
    findings["<b>findings</b><br/>dismisses accepted findings,<br/>fails on the others"]
  end
  daily(["every morning"]) --> findings
  daily --> sweep
  shipped(["merged pull request,<br/>published release"]) --> sweep
  subgraph lifecycle ["issue-lifecycle.yml"]
    sweep["<b>sweep</b><br/>reminders, closing,<br/>duplicates, fixed issues"]
  end
  opened(["new issue, edit<br/>or comment"]) --> event
  subgraph assistant ["issue-assistant.yml"]
    direction LR
    event["<b>event</b><br/>labels from the form,<br/>the next task"] --> analyze["<b>analyze</b><br/>the AI reads the checkout<br/>and answers with JSON"]
    analyze --> apply["<b>apply</b><br/>checks the answer,<br/>the only job that writes"]
  end
  classDef ai fill:#8250df33,stroke:#8250df
  classDef job fill:#8c959f1f,stroke:#8c959f
  classDef trigger fill:#d4a72c33,stroke:#bf8700
  class analyze ai
  class event,apply,sweep,sync,findings job
  class opened,daily,shipped,changed,scanned trigger
```

| Workflow | Jobs |
| --- | --- |
| `issue-assistant.yml` | `event` decides the next task; `analyze` writes the context and runs the engine with a read-only token, on GitHub's egress-firewall runner; `apply` checks the answer and writes it |
| `issue-lifecycle.yml` | `sweep`, every morning, after merged pull requests, published releases and the end of the release workflow |
| `labels.yml` | `sync-labels`, when `labels.toml` changes on the default branch |
| `findings.yml` | `findings`, every morning, after code scanning and when `findings.toml` changes |

## One run for a new issue

The three jobs of `issue-assistant.yml` hand the issue on; only the middle one sees the AI and its secret, and only the last one writes:

```mermaid
sequenceDiagram
  participant G as GitHub
  participant E as event
  participant C as analyze: context
  participant AI as analyze: engine
  participant P as apply
  G->>E: a new issue, #35;12
  E->>G: labels from the form, needs-triage
  E->>C: issue 12, task triage
  C->>C: writes the issue, other issues, discussions,<br/>labels, prompt and schema into .issue-assistant/
  C->>AI: prompt and schema
  Note over C,AI: read-only token, egress firewall, the engine's secret
  AI->>AI: reads the checkout with Read, Grep, Glob
  AI->>P: the answer, JSON
  Note over P: no AI, no secret
  P->>P: checks the answer against the schema,<br/>the labels and the issues, cleans the text
  P->>G: comment and labels, only on #35;12
```

The workflows of this repository are the templates: `install` copies them with the commit hash of the release, and `check` fails when a repository's copy differs in anything but that hash.

## Design decisions

- **The AI only reads, and a job without the AI writes.** The answer of the engine is data that `apply` checks before anything reaches GitHub.
- **Everything but one step is independent of the engine.** Context, prompts, checks and writes are the same for every engine; a new engine is a step and an entry of `ENGINES`, as the [README](https://github.com/Dennis-Otto/issue-assistant#engines) describes.
- **No third-party packages.** The script needs only the standard library, `gh` and `git`, which GitHub's runners have.
- **The maintainer has the last word.** The assistant labels, asks and suggests; it never decides on behalf of the maintainer.
