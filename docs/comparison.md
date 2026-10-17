---
description: The issue assistant compared with the issue examples of the Claude Code action, GitHub Agentic Workflows, actions/stale, Dosu and GitHub's duplicate detection.
hide:
  - navigation
---

# Compared with other tools

[← README](https://github.com/Dennis-Otto/issue-assistant) · [Security design](security.md) · [Questions and troubleshooting](faq.md)

What the issue assistant does differently from the tools that maintainers use for issues today, and when another one is the better choice.

--8<-- "README.md:compare"

## Feature by feature

Each cell names the sources of its tool, as of the day above; the column of the issue assistant is what this documentation describes.

| | Issue assistant | Claude Code action examples | gh-aw issue triage | actions/stale | Dosu | GitHub's duplicate detection |
| --- | --- | --- | --- | --- | --- | --- |
| A first analysis of a new issue | The likely cause with links to the lines of code, what to try and what is missing, in the reporter's language | No: the triage example only labels, and the duplicates example comments only on a duplicate[^cca] | A comment that explains its label and how the issue might be addressed[^aw] | No[^stale] | Not in its open-source workflows[^dosu] | No[^gh] |
| Labels | From the issue forms and from the analysis, each with its reason, learning from the corrections of maintainers; labels as code, with optional priorities, owners of areas who are assigned, and the issue types of organizations | The AI sets them[^cca] | One from an allow list[^aw] | Only its stale label[^stale] | auto-label, which learns from the corrections of maintainers[^dosu] | No[^gh] |
| Duplicates | A notice for a sure duplicate of an issue that a maintainer opened or triaged, and closing after 3 days unless someone objects; other ones become suggestions | A comment and the label `duplicate`[^cca] | Not part of the triage workflow[^aw] | No[^stale] | Not in its open-source workflows[^dosu] | Up to 3 suggestions while the reporter writes[^gh] |
| Issues that wait for their reporter | `needs-info`: a reminder after 15 days, closing after 30, and an answer reopens; other days and exemptions are settings | No[^cca] | No[^aw] | Any inactive issue: stale after 60 days and closed 7 days later by default, or only those with a label[^stale] | better-stale-bot, whose AI reads the thread first; 60 and 7 days by default[^dosu] | No[^gh] |
| Fixed issues close with the release that ships the fix | Yes, with a link to the release; "still broken" reopens | No[^cca] | No[^aw] | No[^stale] | No[^dosu] | No[^gh] |
| The AI can write to GitHub | No: its token only reads | Yes: `issues: write`, and the AI applies labels and comments[^cca] | No: a token that only reads, and separate jobs write its *safe outputs*[^aw] | No AI[^stale] | No: built on gh-aw[^dosu] | Not an agent[^gh] |
| The output of the AI is checked before it reaches GitHub | By a job without AI: schema, labels and issues that exist; refused when it holds anything like a secret; mentions, images, HTML and foreign links removed | No[^cca] | Limits for each kind of output, allowed labels, and a second AI pass that looks for threats[^aw] | No AI[^stale] | As in gh-aw, with the second pass[^dosu] | Not an agent[^gh] |
| The network of the AI | GitHub's egress firewall with an allow list | Not limited in the examples[^cca] | gh-aw's own firewall, a proxy with an allow list[^aw] | No AI[^stale] | As in gh-aw[^dosu] | Not an agent[^gh] |
| Who receives the issues | Your runners and the vendor of the engine | Your runners and Anthropic[^cca] | Your runners and the vendor of the engine[^aw] | Your runners[^stale] | The workflows: your runners and the vendor of the engine; the hosted service: Dosu[^dosu] | GitHub[^gh] |
| Engines | Claude; [another one can be added](https://github.com/Dennis-Otto/issue-assistant#engines) | Claude[^cca] | GitHub Copilot, Claude Code, Codex, Gemini CLI and others[^aw] | None[^stale] | Claude Haiku by default, or Copilot or Codex[^dosu] | Not stated[^gh] |
| Price | Free; the engine, $0.20 for a triage with Claude Opus 5.5 at the prices of the API, measured | Free; the engine[^cca] | Free; the engine[^aw] | Free[^stale] | The workflows free, the engine about $0.10 a run with Haiku; the hosted service free for public repositories with 200 credits a month, Pro $16 a month[^dosu] | Not stated[^gh] |
| Set-up | One `install` command; `check` in CI keeps the workflows equal to the templates | Copy a workflow, a command and two scripts[^cca] | Write the workflow in Markdown and compile it with `gh aw compile`[^aw] | One workflow[^stale] | `gh aw` for the workflows, a GitHub App for the hosted service[^dosu] | Built into the form of a new issue[^gh] |
| Status | Version 1, signed releases with provenance | Version 1[^cca] | Version 0.x, to use "with caution, and at your own risk"[^aw] | Version 10 and later[^stale] | A hosted service and open-source workflows[^dosu] | Public preview[^gh] |

## The tools one by one

### The issue examples of the Claude Code action

The Claude Code action is the engine of the issue assistant too. Its issue examples are short starting points: in the triage example, Claude runs two scripts that list and edit labels; in the duplicates example, it gets GitHub's tools to comment on and edit issues. Both grant `issues: write`, and nothing checks what Claude writes. That is the pattern that PromptPwnd and Comment and Control attack: the text of an issue steers an AI that can write. The assistant runs the same action with the reading tools alone and leaves every write to a job without AI, as the [security design](security.md#against-promptpwnd-and-comment-and-control) shows.

**Choose the examples** to learn the action in a repository where only trusted people open issues; **choose the assistant** where anyone can.

### GitHub Agentic Workflows

gh-aw, by GitHub Next, is a framework for agents in GitHub Actions, and its design is the closest to the assistant's: the agent gets a token that only reads, runs behind a firewall, and asks for writes as *safe outputs*, which separate jobs carry out within limits after a second AI pass looked for threats. Its issue triage is an example to adapt, with one label and a comment. gh-aw runs many engines and any task you describe, and it calls itself a 0.x project to use with caution.

The assistant is narrower and complete for issues: a schema for each task, checks without AI of every link, mention and issue number, duplicates that close, reminders, the closing with the release, and `check`, which keeps every repository on the templates.

**Choose gh-aw** for agents of your own design across many tasks; **choose the assistant** for the whole life of issues, with nothing to write yourself.

### actions/stale

actions/stale closes what stays inactive, without AI and for issues and pull requests alike: by default it marks an issue after 60 days and closes it 7 days later, and a new comment removes the mark. It can't tell an issue that waits for its reporter from one that waits for the maintainer, unless something else sets a label such as `needs-info` for it to look at.

The assistant sets `needs-info` itself when a question waits for the reporter, reminds after 15 days, closes after 30 and reopens on an answer. Like actions/stale, it takes other days, exempts issues by label, assignee or 👍, and can lock issues closed long ago, as `[lifecycle]` in `config.toml` sets. **Keep actions/stale** for pull requests if you like; for issues, the assistant does its work.

### Dosu

Dosu's hosted service keeps documentation up to date. Its pricing page says that labels and the stale bot are "not as hosted features anymore": both became open-source workflows on gh-aw. auto-label labels issues and pull requests with Claude Haiku by default and learns from the corrections of maintainers; better-stale-bot reads a thread before it marks or closes an issue. Both share gh-aw's design and run with your own engine.

**Choose Dosu's workflows** for labels alone or a stale bot that reads; **choose the assistant** for an analysis, duplicates and the closing with the release as well.

### GitHub's duplicate detection

Since June 2026, in public preview, the form of a new issue suggests up to three existing issues that look the same while the reporter writes. It prevents a duplicate before it exists. The assistant finds the duplicates that are opened anyway, says why, and closes a sure one after three days unless someone objects. **Use both.**

## Keeping this current

The cells were checked against the sources below on the day at the top of this page. Tools change; a correction is welcome as an [issue](https://github.com/Dennis-Otto/issue-assistant/issues) or a pull request.

[^cca]: anthropics/claude-code-action: [examples/issue-triage.yml](https://github.com/anthropics/claude-code-action/blob/main/examples/issue-triage.yml) with the command that it runs, [.claude/commands/label-issue.md](https://github.com/anthropics/claude-code-action/blob/main/.claude/commands/label-issue.md); [examples/issue-deduplication.yml](https://github.com/anthropics/claude-code-action/blob/main/examples/issue-deduplication.yml); the [releases](https://github.com/anthropics/claude-code-action/releases).
[^aw]: GitHub Agentic Workflows: [the home page](https://github.github.com/gh-aw/) for its engines and `gh aw compile`; [Meet the Workflows: Issue Triage](https://github.github.com/gh-aw/blog/2026-01-13-meet-the-workflows/) of 2026-01-13; [Architecture](https://github.github.com/gh-aw/introduction/architecture/); [Safe outputs](https://github.github.com/gh-aw/reference/safe-outputs/); [Threat detection](https://github.github.com/gh-aw/reference/threat-detection/), which is on whenever safe outputs are; the [README](https://github.com/github/gh-aw#readme) and [releases](https://github.com/github/gh-aw/releases) of github/gh-aw.
[^stale]: actions/stale: [README](https://github.com/actions/stale#readme).
[^dosu]: Dosu: [pricing](https://dosu.dev/pricing); the READMEs of [dosu-ai/auto-label](https://github.com/dosu-ai/auto-label#readme) and [dosu-ai/better-stale-bot](https://github.com/dosu-ai/better-stale-bot#readme).
[^gh]: GitHub changelog: [Detecting Duplicate Issues – Public Preview](https://github.blog/changelog/2026-06-18-duplicate-detection-and-issue-fields-mcp-support-for-github-issues), 2026-06-18.
