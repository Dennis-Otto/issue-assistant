---
description: The issue assistant compared with copilot-triage, Copilot's automations, gh-aw, the Claude Code and Gemini CLI examples, GitHub's AI labeler and Dosu.
hide:
  - navigation
---

# Compared with other tools

[← README](https://github.com/Dennis-Otto/issue-assistant) · [Security design](security.md) · [Questions and troubleshooting](faq.md)

What the issue assistant does differently from the tools that maintainers use for issues today, and when another one is the better choice.

Other tools cover parts of this. GitHub Agentic Workflows and the workflows built on it share its design, and copilot-triage and the triage examples of the Gemini CLI action share its split between an AI that reads and code without AI that writes. Checked on 2026-11-08:

- **The issue examples of the [Claude Code action](https://github.com/anthropics/claude-code-action/tree/main/examples)** let the AI set labels and post comments itself, with a token that writes to issues and no check of its answer before it is posted.
- **The [triage examples of the Gemini CLI action](https://github.com/google-github-actions/run-gemini-cli/tree/main/examples/workflows/issue-triage)** give Gemini a token that only reads, and a separate job sets the labels that it chose, if they exist. They only label: no analysis, no question for the reporter, no duplicates and no reminders.
- **[GitHub's AI assessment labeler](https://docs.github.com/en/issues/tracking-your-work-with-issues/administering-issues/triaging-an-issue-with-ai)** assesses an issue when a maintainer adds a label, and posts the model's answer unchecked as a comment, from a job that writes to issues. It ran on GitHub Models, which GitHub retired on 2026-07-30.
- **[GitHub Agentic Workflows](https://github.github.com/gh-aw/)** (gh-aw) has the closest design: an agent with a read-only token behind a firewall, and writes through limited *safe outputs* after a second AI pass looks for threats. It is a general framework in version 0.x, in which you write and compile the triage workflow yourself; reminders, duplicates that close and the closing with the release are not part of its triage.
- **[copilot-triage](https://github.com/crmne/copilot-triage)** gives a model six tools that only read and keeps the tokens out of its environment; Ruby checks its decision and posts it from the same job, which can write. It also looks after discussions, pull requests and a project board, replies in under 60 words or not at all, and suggests or closes what a release fixed or the reporter calls solved. It runs on a Copilot allowance or your own key, without a firewall, in version 0.x.
- **[Copilot's automations](https://docs.github.com/en/copilot/concepts/agent-capabilities/agent-powered-workflows-and-operations/about-automations)** run GitHub's cloud agent on a new issue, with the tools that write which you select. The agent gives each label, type, field, assignee or closing a reason and a confidence, and holds the uncertain ones for an approval that GitHub says is no security control. They work only in private and internal repositories, and by default ignore the issues of people who can't write there.
- **[actions/stale](https://github.com/actions/stale)** closes inactive issues without AI, by default 60 and then 7 days after the last activity. It writes no analysis, and limiting it to issues that wait for their reporter takes a label such as `needs-info`, which something else has to set.
- **[Dosu](https://dosu.dev/pricing)** is a hosted service for documentation now. Its labels and its stale bot became open-source workflows on gh-aw, which run with your own engine.
- **[GitHub's duplicate detection](https://github.blog/changelog/2026-06-18-duplicate-detection-and-issue-fields-mcp-support-for-github-issues/)**, in public preview, suggests up to three similar issues while a reporter writes. It goes well with the assistant, which also finds duplicates of an issue that exists.

## Feature by feature

Each cell names the sources of its tool, as of the day above; the column of the issue assistant is what this documentation describes.

| | Issue assistant | Claude Code action examples | Gemini CLI triage | GitHub's AI assessment labeler | gh-aw issue triage | copilot-triage | Copilot's triage automations | actions/stale | Dosu | GitHub's duplicate detection |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A first analysis of a new issue | The likely cause with links to the lines of code, what to try and what is missing, in the reporter's language | No: the triage example only labels, and the duplicates example comments only on a duplicate[^cca] | No: it only labels, and comments only when the triage fails[^gem] | A comment with the model's assessment for each prompt, such as whether a bug report is complete; the model reads the body of the issue alone[^aal] | A comment that explains its label and how the issue might be addressed[^aw] | One reply of under 60 words or none: an answer from the documentation, a released fix, a duplicate, one question, or a recap of a long report[^ct] | What the prompt asks for; GitHub documents the triage of labels, the issue type, fields, assignees and closing, each with its reason[^cop] | No[^stale] | Not in its open-source workflows[^dosu] | No[^gh] |
| Labels | From the issue forms and from the analysis, each with its reason, learning from the corrections of maintainers; labels as code, with optional priorities, owners of areas who are assigned, and the issue types of organizations | The AI sets them[^cca] | The AI picks from all labels of the repository, without a reason; an hourly run labels the open issues without a label or with `status/needs-triage`[^gem] | `ai:<prompt>:<assessment>`, such as `ai:bug-review:missing details`, from the heading of each answer; the labels of the issue choose the prompts[^aal] | One from an allow list[^aw] | At most two for each issue, from its policy, created where missing; a label that asks the reporter for something comes off when they answer[^ct] | Set, or suggested for approval, by the agent, with its reason and its confidence[^cop] | Only its stale label[^stale] | auto-label, which learns from the corrections of maintainers[^dosu] | No[^gh] |
| Duplicates | A notice for a sure duplicate of an issue that a maintainer opened or triaged, and closing after 3 days unless someone objects; other ones become suggestions | A comment and the label `duplicate`[^cca] | No[^gem] | No[^aal] | Not part of the triage workflow[^aw] | A link to the duplicate, or closing it against an older issue that no maintainer opened or joined[^ct] | Not part of the documented triage[^cop] | No[^stale] | Not in its open-source workflows[^dosu] | Up to 3 suggestions while the reporter writes[^gh] |
| Issues that wait for their reporter | `needs-info`: a reminder after 15 days, closing after 30, and an answer reopens; other days and exemptions are settings | No[^cca] | No[^gem] | No; a label such as `ai:bug-review:missing details` marks them, and nothing follows up[^aal] | No[^aw] | No reminder and no closing; on its board, such an issue goes back to *Decide* after 21 quiet days[^ct] | Not part of the documented triage; an automation can run a prompt of your own every hour, day or week[^cop] | Any inactive issue: stale after 60 days and closed 7 days later by default, or only those with a label[^stale] | better-stale-bot, whose AI reads the thread first; 60 and 7 days by default[^dosu] | No[^gh] |
| Fixed issues close with the release that ships the fix | Yes, with a link to the release; "still broken" reopens | No[^cca] | No[^gem] | No[^aal] | No[^aw] | A workflow of its own checks up to 10 issues that a release may have fixed, and suggests or closes those whose fix the release notes name[^ct] | Not part of the documented triage[^cop] | No[^stale] | No[^dosu] | No[^gh] |
| The AI can write to GitHub | No: its token only reads | Yes: `issues: write`, and the AI applies labels and comments[^cca] | No: the job of Gemini reads issues, with no tools, and a separate job sets the labels[^gem] | The model has no tools, but the job that calls it has `issues: write` and posts its answer[^aal] | No: a token that only reads, and separate jobs write its *safe outputs*[^aw] | No: six tools that only read, and the tokens are taken out of its environment; the job that runs it holds `issues: write`[^ct] | Yes: the agent writes with the GitHub tools that you select, in the repository or only on the issue that started it[^cop] | No AI[^stale] | No: built on gh-aw[^dosu] | Not an agent[^gh] |
| The output of the AI is checked before it reaches GitHub | By a job without AI: schema, labels and issues that exist; refused when it holds anything like a secret; mentions, images, HTML and foreign links removed | No[^cca] | Only labels that exist in the repository are set, and nothing else is posted[^gem] | No: its answer becomes the comment as it is[^aal] | Limits for each kind of output, allowed labels, and a second AI pass that looks for threats[^aw] | Ruby checks the decision, reads the report and the cited evidence again, and posts no mentions, HTML, bare addresses or links that the model wrote[^ct] | Changes below the automation level of the repository wait for approval, which GitHub says is no security control[^cop] | No AI[^stale] | As in gh-aw, with the second pass[^dosu] | Not an agent[^gh] |
| The network of the AI | GitHub's egress firewall with an allow list | Not limited in the examples[^cca] | Not limited in the examples[^gem] | Not limited in the example[^aal] | gh-aw's own firewall, a proxy with an allow list[^aw] | Not limited by a firewall; none of its tools fetches an address of its choice[^ct] | The firewall of Copilot cloud agent, with the rules of the repository[^cop] | No AI[^stale] | As in gh-aw[^dosu] | Not an agent[^gh] |
| Who receives the issues | Your runners and the vendor of the engine | Your runners and Anthropic[^cca] | Your runners and Google, through the Gemini API or Vertex AI[^gem] | Your runners and GitHub Models, or the endpoint that you set[^aal] | Your runners and the vendor of the engine[^aw] | Your runners and GitHub Copilot, or the provider of your key[^ct] | GitHub[^cop] | Your runners[^stale] | The workflows: your runners and the vendor of the engine; the hosted service: Dosu[^dosu] | GitHub[^gh] |
| Engines | Claude, Codex, Gemini and Copilot, each with only tools that read; [another one can be added](engines.md#add-an-engine) | Claude[^cca] | Gemini[^gem] | The models of GitHub Models, such as `openai/gpt-4o-mini`; GitHub retired the service on 2026-07-30[^aal][^models] | GitHub Copilot, Claude Code, Codex, Gemini CLI and others[^aw] | Copilot, with `gpt-5.6-luna` by default, or a provider of RubyLLM, such as OpenAI, Anthropic, Gemini or OpenRouter, also as a fallback once the Copilot allowance is spent[^ct] | The models of Copilot cloud agent, chosen for each automation[^cop] | None[^stale] | Claude Haiku by default, or Copilot or Codex[^dosu] | Not stated[^gh] |
| Price | Free; the engine, $0.20 for a triage with Claude Opus 5.5 at the prices of the API, measured, or nothing within the free tier of the Gemini API | Free; the engine[^cca] | Free; the engine, or nothing within the free tier of the Gemini API[^gem] | Free and without a secret while GitHub Models ran; now it has no model to call, since it sends the token that posts its comment to the model as well[^aal][^models] | Free; the engine[^aw] | Free; the engine, around a cent or less a run on a Copilot allowance, by its own measure[^ct] | The AI credits of a Copilot plan from Pro up, and the minutes of GitHub Actions; limits for each run, day, week or month[^cop] | Free[^stale] | The workflows free, the engine about $0.10 a run with Haiku; the hosted service free for public repositories with 200 credits a month, Pro $16 a month[^dosu] | Not stated[^gh] |
| Set-up | One `install` command; `check` in CI keeps the workflows equal to the templates | Copy a workflow, a command and two scripts[^cca] | `/setup-github` in the Gemini CLI, or copy the dispatch and the triage workflows; a `GEMINI_API_KEY`, or Workload Identity Federation for Google Cloud[^gem] | One workflow, a prompt file for each label, and the label `request ai review` on every issue to assess[^aal] | Write the workflow in Markdown and compile it with `gh aw compile`[^aw] | A `COPILOT_GITHUB_TOKEN`, a policy in `.github/triage.yml` and a workflow that calls its own; a classic token for the board[^ct] | A form or a chat in the *Agents* tab of the repository: the triggers, the prompt and the tools, kept outside Git[^cop] | One workflow[^stale] | `gh aw` for the workflows, a GitHub App for the hosted service[^dosu] | Built into the form of a new issue[^gh] |
| Status | Version 1, signed releases with provenance | Version 1[^cca] | Version 0.x[^gem] | Version 1.0.1 of 2025-09-22, in GitHub's documentation as *Triaging an issue with AI*[^aal] | Version 0.x, to use "with caution, and at your own risk"[^aw] | Version 0.20.1 of 2026-11-01; the tag `v0` follows the releases[^ct] | Only in private and internal repositories; the reasons, the confidence and the approvals in public preview[^cop] | Version 10 and later[^stale] | A hosted service and open-source workflows[^dosu] | Public preview[^gh] |

## The tools one by one

### The issue examples of the Claude Code action

**Choose the examples** to learn the action in a repository where only trusted people open issues; **choose the assistant** where anyone can.

<details markdown="1">
<summary>How the issue examples of the Claude Code action work</summary>

The Claude Code action is the default engine of the issue assistant too. Its issue examples are short starting points: in the triage example, Claude runs two scripts that list and edit labels; in the duplicates example, it gets GitHub's tools to comment on and edit issues. Both grant `issues: write`, and nothing checks what Claude writes. That is the pattern that PromptPwnd and Comment and Control attack: the text of an issue steers an AI that can write. The assistant runs the same action with the reading tools alone and leaves every write to a job without AI, as the [security design](security/prompt-attacks.md) shows.

</details>

### The triage examples of the Gemini CLI action

**Choose the Gemini examples** for labels alone, with a key of the Gemini API or a project on Google Cloud that you have already; **choose the assistant** for an answer that the reporter reads, with the same engine.

<details markdown="1">
<summary>How the triage examples of the Gemini CLI action work</summary>

Google's action for the Gemini CLI, google-github-actions/run-gemini-cli, has two triage examples: one labels an issue when it's opened or reopened, the other runs every hour over the open issues without a label. Their design is careful. The job of Gemini has a token that only reads issues and no tools at all, it answers with a list of labels, and a separate job keeps the labels that exist in the repository and sets them. Nothing else of the AI reaches GitHub, so a steered answer can at worst set a wrong label. Nothing else comes out either: no analysis, no question for the reporter, no duplicates and no reminders, and the AI sees the title and the body of the issue, not the code. A run that fails comments with a link to its log. The action is at version 0.x.

The assistant keeps the same split, an AI that only reads and a job without AI that writes, for a whole analysis. It runs Gemini too, within the free tier of the Gemini API, with tools that read the code.

</details>

### GitHub's AI assessment labeler

**Choose the labeler** to have reports judged for completeness one at a time, on request, once it has a model again; **choose the assistant** for every new issue, with an answer that a job without AI checks before it's posted.

<details markdown="1">
<summary>How GitHub's AI assessment labeler works</summary>

github/ai-assessment-comment-labeler is the action that GitHub's documentation names for *triaging an issue with AI*. A maintainer adds the label `request ai review`, and the labels of the issue choose prompt files. For each one, a model reads the body of the issue, its answer becomes a comment, and the heading of the answer a label such as `ai:bug-review:missing details`. It needed no secret, since the token of the workflow called GitHub Models with `models: read`. The same job holds `issues: write`, though, and posts the answer of the model unchecked, so the text of an issue can steer what the comment says, links included.

GitHub retired GitHub Models on 2026-07-30. The action sends the token that posts its comment to the model as well, so another endpoint doesn't help without a change of its code; its last release, 1.0.1, dates from 2025-09-22.

</details>

### GitHub Agentic Workflows

**Choose gh-aw** for agents of your own design across many tasks; **choose the assistant** for the whole life of issues, with nothing to write yourself.

<details markdown="1">
<summary>How GitHub Agentic Workflows works</summary>

gh-aw, by GitHub Next, is a framework for agents in GitHub Actions, and its design is the closest to the assistant's: the agent gets a token that only reads, runs behind a firewall, and asks for writes as *safe outputs*, which separate jobs carry out within limits after a second AI pass looked for threats. Its issue triage is an example to adapt, with one label and a comment. gh-aw runs many engines and any task you describe, and it calls itself a 0.x project to use with caution.

The assistant is narrower and complete for issues: a schema for each task, checks without AI of every link, mention and issue number, duplicates that close, reminders, the closing with the release, and `check`, which keeps every repository on the templates.

</details>

### copilot-triage

**Choose copilot-triage** for one bot over the issues, the discussions and the pull requests of a busy repository, with a board as the inbox, at a cent a run; **choose the assistant** for an analysis that reads the code, an AI behind a firewall with a job of its own that writes, and the reminders and the closing of issues that wait for their reporter.

<details markdown="1">
<summary>How copilot-triage works</summary>

crmne/copilot-triage grew out of gh-aw: its author replaced a compiled workflow of 2,035 lines with one agent, a system prompt and small tools that only read. The model gets six of them, to search the code, the issues and the releases and to read evidence, and the tokens are taken out of its environment. Ruby checks the decision that it submits, reads the report and the cited evidence again and posts it, in the same job, which holds `issues: write`; the design rests on the tools of the model, not on a firewall. Its reach is wider than the assistant's: discussions, pull requests, with a review by Copilot when a change deserves one, and a project board that sorts every issue and pull request by what it needs from the maintainer. Its replies are short on purpose, normally under 60 words, and silence counts as a decision. With `close` or `auto` in its policy, it closes a duplicate against an older issue, an issue whose fix the release notes name, and one that the reporter says is solved; otherwise it suggests them on its board. It doesn't remind a reporter or close an issue that waits for one. It runs on a Copilot allowance, at around a cent a run by its own measure, or on a key of your own, and is at version 0.x.

</details>

### Copilot's triage automations

**Choose Copilot's automations** in a private repository of a team with a Copilot plan, where colleagues open the issues and a click approves a label; **choose the assistant** for a public repository, where anyone can open an issue and the AI must not write.

<details markdown="1">
<summary>How Copilot's triage automations work</summary>

Since June 2026, automations run GitHub's Copilot cloud agent on a schedule or on an event, such as a new issue, with a prompt and the tools that you select. For the triage of issues, the agent gives a reason for each label, issue type, field, assignee or closing and rates its confidence; the automation level of the repository decides which changes apply at once and which wait for a maintainer's approval. GitHub's documentation says that the approvals are no security control: an agent that may change issues can change them directly. Automations run only in private and internal repositories, and by default they ignore the issues of people who can't write to the repository, against prompt injection. Each run takes AI credits of a Copilot plan and minutes of GitHub Actions, and the automation lives outside Git.

</details>

### actions/stale

**Keep actions/stale** for pull requests if you like; for issues, the assistant does its work.

<details markdown="1">
<summary>How actions/stale works</summary>

actions/stale closes what stays inactive, without AI and for issues and pull requests alike: by default it marks an issue after 60 days and closes it 7 days later, and a new comment removes the mark. It can't tell an issue that waits for its reporter from one that waits for the maintainer, unless something else sets a label such as `needs-info` for it to look at.

The assistant sets `needs-info` itself when a question waits for the reporter, reminds after 15 days, closes after 30 and reopens on an answer. Like actions/stale, it takes other days, exempts issues by label, assignee or 👍, and can lock issues closed long ago, as `[lifecycle]` in `config.toml` sets.

</details>

### Dosu

**Choose Dosu's workflows** for labels alone or a stale bot that reads; **choose the assistant** for an analysis, duplicates and the closing with the release as well.

<details markdown="1">
<summary>How Dosu works</summary>

Dosu's hosted service keeps documentation up to date. Its pricing page says that labels and the stale bot are "not as hosted features anymore": both became open-source workflows on gh-aw. auto-label labels issues and pull requests with Claude Haiku by default and learns from the corrections of maintainers; better-stale-bot reads a thread before it marks or closes an issue. Both share gh-aw's design and run with your own engine.

</details>

### GitHub's duplicate detection

**Use both:** GitHub's suggestions prevent a duplicate before it exists, and the assistant closes the sure ones that get through.

<details markdown="1">
<summary>How GitHub's duplicate detection works</summary>

Since June 2026, in public preview, the form of a new issue suggests up to three existing issues that look the same while the reporter writes. The assistant finds the duplicates that are opened anyway, says why, and closes a sure one after three days unless someone objects.

</details>

## Keeping this current

The cells were checked against the sources below on the day at the top of this page. Tools change; a correction is welcome as an [issue](https://github.com/Dennis-Otto/issue-assistant/issues) or a pull request.

[^cca]: anthropics/claude-code-action: [examples/issue-triage.yml](https://github.com/anthropics/claude-code-action/blob/main/examples/issue-triage.yml) with the command that it runs, [.claude/commands/label-issue.md](https://github.com/anthropics/claude-code-action/blob/main/.claude/commands/label-issue.md); [examples/issue-deduplication.yml](https://github.com/anthropics/claude-code-action/blob/main/examples/issue-deduplication.yml); the [releases](https://github.com/anthropics/claude-code-action/releases).
[^gem]: google-github-actions/run-gemini-cli: [examples/workflows/issue-triage/gemini-triage.yml](https://github.com/google-github-actions/run-gemini-cli/blob/main/examples/workflows/issue-triage/gemini-triage.yml) with its prompt, [gemini-triage.toml](https://github.com/google-github-actions/run-gemini-cli/blob/main/examples/workflows/issue-triage/gemini-triage.toml); [gemini-scheduled-triage.yml](https://github.com/google-github-actions/run-gemini-cli/blob/main/examples/workflows/issue-triage/gemini-scheduled-triage.yml); the [README of the examples](https://github.com/google-github-actions/run-gemini-cli/blob/main/examples/workflows/issue-triage/README.md); the [README](https://github.com/google-github-actions/run-gemini-cli#readme) for the keys, and the [releases](https://github.com/google-github-actions/run-gemini-cli/releases).
[^aal]: github/ai-assessment-comment-labeler: [README](https://github.com/github/ai-assessment-comment-labeler#readme); [src/index.ts](https://github.com/github/ai-assessment-comment-labeler/blob/main/src/index.ts), which gives the same token to GitHub and to the model; the [releases](https://github.com/github/ai-assessment-comment-labeler/releases). GitHub Docs: [Triaging an issue with AI](https://docs.github.com/en/issues/tracking-your-work-with-issues/administering-issues/triaging-an-issue-with-ai). GitHub changelog: [GitHub Actions: AI labeler and moderator with the GitHub Models inference API](https://github.blog/changelog/2025-09-05-github-actions-ai-labeler-and-moderator-with-the-github-models-inference-api/), 2025-09-05.
[^models]: GitHub changelog: [GitHub Models is now retired](https://github.blog/changelog/2026-07-30-github-models-is-now-retired/), 2026-07-30.
[^aw]: GitHub Agentic Workflows: [the home page](https://github.github.com/gh-aw/) for its engines and `gh aw compile`; [Meet the Workflows: Issue Triage](https://github.github.com/gh-aw/blog/2026-01-13-meet-the-workflows/) of 2026-01-13; [Architecture](https://github.github.com/gh-aw/introduction/architecture/); [Safe outputs](https://github.github.com/gh-aw/reference/safe-outputs/); [Threat detection](https://github.github.com/gh-aw/reference/threat-detection/), which is on whenever safe outputs are; the [README](https://github.com/github/gh-aw#readme) and [releases](https://github.com/github/gh-aw/releases) of github/gh-aw.
[^ct]: crmne/copilot-triage: the [README](https://github.com/crmne/copilot-triage#readme), with its quick start, its safety and the workflow for releases; [action.yml](https://github.com/crmne/copilot-triage/blob/main/action.yml); [.github/workflows/assess.yml](https://github.com/crmne/copilot-triage/blob/main/.github/workflows/assess.yml), the workflow that holds its permissions; the [releases](https://github.com/crmne/copilot-triage/releases).
[^cop]: GitHub Docs: [Copilot automations](https://docs.github.com/en/copilot/concepts/agent-capabilities/agent-powered-workflows-and-operations/about-automations); [About rationale, confidence, and approvals for issues](https://docs.github.com/en/copilot/concepts/agent-capabilities/agent-powered-workflows-and-operations/about-automation-rationale-and-approvals); [Creating automations with Copilot cloud agent](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/create-automations). GitHub changelog: [Schedule and automate tasks with Copilot cloud agent](https://github.blog/changelog/2026-06-02-schedule-and-automate-tasks-with-copilot-cloud-agent/), 2026-06-02. GitHub Community: [Review and approve Copilot agent actions on issues](https://github.com/orgs/community/discussions/198815), 2026-06-12.
[^stale]: actions/stale: [README](https://github.com/actions/stale#readme).
[^dosu]: Dosu: [pricing](https://dosu.dev/pricing); the READMEs of [dosu-ai/auto-label](https://github.com/dosu-ai/auto-label#readme) and [dosu-ai/better-stale-bot](https://github.com/dosu-ai/better-stale-bot#readme).
[^gh]: GitHub changelog: [Detecting Duplicate Issues – Public Preview](https://github.blog/changelog/2026-06-18-duplicate-detection-and-issue-fields-mcp-support-for-github-issues/), 2026-06-18.
