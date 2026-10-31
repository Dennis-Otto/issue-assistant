# Issue assistant

**Safe AI issue triage for GitHub.** A read-only AI writes a first analysis of every new issue, and a job without AI checks it before anything is posted.

[![Documentation](https://img.shields.io/badge/docs-website-blue)](https://dennis-otto.github.io/issue-assistant/)
[![CI](https://github.com/Dennis-Otto/issue-assistant/actions/workflows/ci.yml/badge.svg)](https://github.com/Dennis-Otto/issue-assistant/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Dennis-Otto/issue-assistant/actions/workflows/codeql.yml/badge.svg)](https://github.com/Dennis-Otto/issue-assistant/actions/workflows/codeql.yml)
[![Secret scan](https://github.com/Dennis-Otto/issue-assistant/actions/workflows/secret-scan.yml/badge.svg)](https://github.com/Dennis-Otto/issue-assistant/actions/workflows/secret-scan.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/Dennis-Otto/issue-assistant/badge)](https://scorecard.dev/viewer/?uri=github.com/Dennis-Otto/issue-assistant)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/15278/badge)](https://www.bestpractices.dev/projects/15278)
[![REUSE](https://api.reuse.software/badge/github.com/Dennis-Otto/issue-assistant)](https://api.reuse.software/info/github.com/Dennis-Otto/issue-assistant)
[![Sponsor](https://img.shields.io/badge/sponsor-%E2%99%A5-db61a2?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/Dennis-Otto)

- **Prompt injection can't write.** The AI runs with tools that only read, behind a firewall and with a read-only token or none. Its answer reaches GitHub only through a job without AI that checks it against a schema, your labels and your issues.
- **Answers worth reading.** The likely cause with links to the lines of code, what the reporter can try, the information still missing, duplicates and labels, in the reporter's language.
- **The whole life of an issue.** Reminders and closing for unanswered issues, duplicates that close after three days, and fixed issues that close with the release that ships the fix, all without AI.

In the root of your repository, these commands install the workflows of the latest release, from a fresh copy each time, and set up the environment of the AI's secret on GitHub:

```sh
tag="$(gh release view --repo Dennis-Otto/issue-assistant --json tagName --jq .tagName)"
dir="$(mktemp -d)"
git clone --quiet --depth 1 --branch "$tag" https://github.com/Dennis-Otto/issue-assistant "$dir" &&
  python3 "$dir/issue_assistant.py" install --setup
```

Then describe the project, clear one setting and try it on an issue, as [Set up a repository](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/getting-started.md#set-up-a-repository) shows. [Requirements](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/getting-started.md#requirements) lists what it needs, and the [FAQ](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/faq.md#what-does-it-cost) what it costs.

**See it live:** [the first analysis of a feature request in Paperless Sync](https://github.com/Dennis-Otto/paperless-sync/issues/84#issuecomment-6125470710), posted 2 minutes 39 seconds after the issue was opened.

<picture>
  <source media="(prefers-reduced-motion: reduce) and (max-width: 600px) and (prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/hero-mobile-still-dark.webp">
  <source media="(prefers-reduced-motion: reduce) and (max-width: 600px)" srcset="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/hero-mobile-still-light.webp">
  <source media="(prefers-reduced-motion: reduce) and (prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/hero-still-dark.webp">
  <source media="(prefers-reduced-motion: reduce)" srcset="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/hero-still-light.webp">
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/hero-mobile-dark.webp">
  <source media="(max-width: 600px)" srcset="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/hero-mobile-light.webp">
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/hero-dark.webp">
  <img src="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/hero-light.webp" alt="Animation: the life of an issue in a demo project. A reporter opens an issue with the bug form; the event job adds the labels area: dashboard and needs-triage; the issue assistant posts a first analysis with the likely cause in the code and the information still needed, and adds connectivity and needs-info; the reporter answers and needs-info goes; a merged pull request marks the issue fixed-in-next-release; the release v1.4.0 closes it with a link to the release">
</picture>

## What it does

A GitHub Action that looks after the issues of a repository. Each line leads to a page with a picture and the details:

- **[The first analysis](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/first-analysis.md):** the likely cause with links to the code, what to try and what is missing, in the reporter's language.
- <a name="the-life-of-an-issue"></a>**[The life of an issue](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/life-of-an-issue.md):** labels say where an issue stands, and the assistant keeps what its comments promise, without AI.
- <a name="priorities-owners-and-issue-types"></a>**[Priorities, owners and issue types](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/priorities.md):** an analysis can set a priority, assign an area's owners and follow the kind with an issue type.
- <a name="waiting-periods-exemptions-and-old-threads"></a>**[Waiting periods, exemptions and old threads](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/waiting-periods.md):** a reminder after 15 days and the closing after 30, or your own days, with exemptions and locks.
- <a name="a-duplicate"></a>**[Duplicates](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/duplicates.md):** a sure duplicate closes three days after its notice unless someone objects; others are suggestions.
- <a name="related-repositories"></a>**[Related repositories](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/related-repositories.md):** also searches the issues of projects that share your users, and says when an issue may belong there.
- <a name="questions-in-discussions"></a>**[Questions in Discussions](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/discussions.md):** a first answer to a new question, with the same AI and the same checks, never marked as the answer.
- <a name="slash-commands"></a>**[Slash commands](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/slash-commands.md):** steer the assistant from the issue, such as `/assistant again` or `/assistant ask <question>`.
- <a name="from-the-fix-to-the-release"></a>**[From the fix to the release](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/fix-to-release.md):** a fixed issue stays open until a release ships the fix, then closes with a link to it.
- <a name="what-the-ai-reads"></a>**[What the AI reads](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/what-the-ai-reads.md):** each task reads what it needs and no more, and a first analysis learns from the maintainers' corrections.
- <a name="languages"></a>**[Languages](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/languages.md):** fourteen catalogs for the reporters, and a folded summary for maintainers who don't read their language.
- <a name="limits-of-the-ai-runs"></a>**[Limits of the AI runs](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/limits.md):** no stranger can run up the bill: the runs that others than the maintainers start are limited.
- <a name="what-one-run-may-cost"></a>**[What one run may cost](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/cost.md):** each run stops at its turns and its dollars, and the summary of its job says what it cost.
- <a name="what-the-sweep-tells-the-maintainers"></a>**[What the sweep tells the maintainers](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/digest.md):** every morning what waits on whom, if you want in a pinned issue, and once a month how well it did.
- <a name="when-github-fails"></a>**[When GitHub fails](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/github-failures.md):** a rate limit, a server error or an incident never changes what the assistant posts.
- **[Labels as code](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/configuration.md#githublabelstoml):** the labels of the repository in `.github/labels.toml`, from the issue forms and from the analysis, each with its reason.
- **[Findings of code scanning](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/configuration.md#findings):** the findings that `.github/findings.toml` accepts are dismissed with their reason; any other open finding fails a daily run, without a public issue.

The maintainer reads every issue and has the last word. A first analysis looks like this, here on an issue of a demo project:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/analysis-dark.webp">
  <img src="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/analysis-light.webp" alt="A first analysis of the issue assistant on an issue of a demo project: thanks to the reporter, a summary, the first analysis with the likely cause, what is worth trying, the information needed with the reminder after 15 days, a related issue, links to the line of code and the section of the troubleshooting guide, and a footer that names Claude and the label it added, links how the assistant works and asks for a thumbs up or down">
</picture>

**Used by** [HomeDarts for Autodarts](https://github.com/Dennis-Otto/homedarts-for-autodarts), [Player Switch for Autodarts](https://github.com/Dennis-Otto/player-switch-for-autodarts), [Paperless Unified Search](https://github.com/Dennis-Otto/paperless-unified-search), [Paperless Sync](https://github.com/Dennis-Otto/paperless-sync), the [repository blueprint](https://github.com/Dennis-Otto/repo-blueprint), the website [dennis-otto.github.io](https://github.com/Dennis-Otto/dennis-otto.github.io) and the issue assistant itself.

## How it works

Four workflows run in the repository, each a short one that pins a release of the issue assistant. Only the violet job runs an AI, and it can't write. [How it works](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/how-it-works.md) has what starts each of them and the task that each event gives the AI.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/diagrams/the-four-workflows-of-a-repository-and-what-starts-them.dark.svg">
  <img src="docs/images/diagrams/the-four-workflows-of-a-repository-and-what-starts-them.svg" alt="The four workflows of a repository and what starts them: Four workflows, each with what starts it. In issue-assistant.yml, a new issue, an edit or a comment starts event, which sets the labels of the form and chooses the next task; analyze lets the AI read the checkout and answer with JSON; apply checks the answer and is the only job that writes. In issue-lifecycle.yml, every morning, a merged pull request, a release and the end of a release run start sweep, which sends reminders, closes issues, closes duplicates and marks fixed issues. In labels.yml, a change of labels.toml on main starts sync-labels, which creates and updates the labels. In findings.yml, every morning, a scan on main and a change of findings.toml start findings, which dismisses the accepted findings and fails on the others.">
</picture>

## Security

Issues come from anyone, so the design assumes that an issue tries to steer the AI. It follows every mitigation that the research on [PromptPwnd and Comment and Control](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/security/prompt-attacks.md) recommends for a workflow like it:

- **The AI only reads.** It runs with a read-only token and tools that only read, on a runner whose firewall lets it reach only the hosts of its engine.
- **Nothing is posted unchecked.** A job that runs no AI and never sees its secret checks the answer against a schema, your labels and your issues.
- **Issue text can't pass for someone else.** Each text stands in a code fence that nothing in it can close, below headings that only the assistant writes.
- **No stranger can run up the bill.** People who don't maintain the repository start at most 20 runs of the AI a day by default, each with [its limits](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/features/limits.md).
- **The secret stays in one place:** the environment `issue-assistant`, which only the default branch may use, and no code of a pull request runs.

[What keeps it safe](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/how-it-works.md#security) has all of it, and the [security design](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/security.md) the threats, their countermeasures and the tests that pin them. Report vulnerabilities privately, as described in [SECURITY.md](https://github.com/Dennis-Otto/issue-assistant/blob/main/SECURITY.md).

## Compared with other tools

Other tools cover parts of this. GitHub Agentic Workflows and the workflows built on it share its design, and the triage examples of the Gemini CLI action share its split between an AI that reads and a job that writes. Checked on 2026-10-25:

- **The issue examples of the [Claude Code action](https://github.com/anthropics/claude-code-action/tree/main/examples)** let the AI set labels and post comments itself, with a token that writes to issues and no check of its answer before it is posted.
- **The [triage examples of the Gemini CLI action](https://github.com/google-github-actions/run-gemini-cli/tree/main/examples/workflows/issue-triage)** give Gemini a token that only reads, and a separate job sets the labels that it chose, if they exist. They only label: no analysis, no question for the reporter, no duplicates and no reminders.
- **[GitHub's AI assessment labeler](https://docs.github.com/en/issues/tracking-your-work-with-issues/administering-issues/triaging-an-issue-with-ai)** assesses an issue when a maintainer adds a label, and posts the model's answer unchecked as a comment, from a job that writes to issues. It ran on GitHub Models, which GitHub retired on 2026-07-30.
- **[GitHub Agentic Workflows](https://github.github.com/gh-aw/)** (gh-aw) has the closest design: an agent with a read-only token behind a firewall, and writes through limited *safe outputs* after a second AI pass looks for threats. It is a general framework in version 0.x, in which you write and compile the triage workflow yourself; reminders, duplicates that close and the closing with the release are not part of its triage.
- **[actions/stale](https://github.com/actions/stale)** closes inactive issues without AI, by default 60 and then 7 days after the last activity. It writes no analysis, and limiting it to issues that wait for their reporter takes a label such as `needs-info`, which something else has to set.
- **[Dosu](https://dosu.dev/pricing)** is a hosted service for documentation now. Its labels and its stale bot became open-source workflows on gh-aw, which run with your own engine.
- **[GitHub's duplicate detection](https://github.blog/changelog/2026-06-18-duplicate-detection-and-issue-fields-mcp-support-for-github-issues/)**, in public preview, suggests up to three similar issues while a reporter writes. It goes well with the assistant, which also finds duplicates of an issue that exists.

The [comparison](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/comparison.md) has the whole table, with the source of every cell.

## Documentation

The [website](https://dennis-otto.github.io/issue-assistant/) has all of this on a page for each topic, with a search:

- <a name="requirements"></a><a name="set-up-a-repository"></a><a name="what-install-writes"></a><a name="keep-it-checked"></a><a name="without---setup"></a>**[Get started](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/getting-started.md):** what a repository needs, every step of the set-up, and how to check that it works.
- <a name="settings"></a><a name="githublabelstoml"></a><a name="githubissue-assistantconfigtoml"></a><a name="findings"></a>**[Configuration](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/configuration.md):** the labels, the settings and the accepted findings, and [every key](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/every-key.md) with its default.
- <a name="commands"></a><a name="try-a-prompt-locally"></a>**[Reference](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/reference.md):** the commands, every input and output of the action, and how to try a prompt on your computer.
- <a name="engines"></a><a name="change-the-engine"></a><a name="add-an-engine"></a>**[Engines](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/engines.md):** Claude, Codex, Gemini and Copilot compared, and how to change or add one.
- **[What reporters see](https://dennis-otto.github.io/issue-assistant/comments.html):** every comment that the assistant posts, in English and in German.
- <a name="faq"></a><a name="what-does-it-cost"></a><a name="can-i-change-the-model"></a><a name="can-i-change-when-issues-are-reminded-closed-or-locked"></a><a name="which-languages-does-it-answer-in"></a><a name="can-the-maintainers-read-an-issue-in-a-language-they-dont-know"></a><a name="does-the-assistant-learn-from-its-mistakes"></a><a name="how-do-i-know-whether-the-assistant-does-well"></a><a name="which-data-leaves-github"></a><a name="does-it-work-in-private-repositories"></a><a name="does-it-answer-questions-in-discussions"></a><a name="why-didnt-my-own-issue-get-an-analysis"></a><a name="what-happens-to-spam"></a><a name="what-if-an-issue-shows-a-secret-or-personal-data"></a><a name="what-if-an-issue-reports-a-vulnerability"></a><a name="how-do-i-turn-it-off"></a><a name="what-if-the-firewall-runner-is-gone"></a><a name="why-must-the-workflows-equal-the-templates"></a><a name="does-it-need-claude"></a>**[FAQ](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/faq.md):** what it costs, which data leaves GitHub, and what happens to spam, a secret or a vulnerability in an issue.
- <a name="troubleshooting"></a>**[Troubleshooting](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/troubleshooting.md):** why an issue got no comment, the usual causes, each with its fix.
- <a name="measure-the-accuracy"></a>**[How accurate is it?](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/accuracy.md)** The labels, duplicates and questions against what the maintainers decided, and how to measure it yourself.
- **[Architecture](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/architecture.md):** the parts of the assistant, and how they are tested.
- **[Roadmap](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/roadmap.md)** and **[changelog](https://github.com/Dennis-Otto/issue-assistant/blob/main/CHANGELOG.md):** what comes next, and what each release changed.

<sub>💛 If the issue assistant is useful to you, you can [support its development](https://github.com/sponsors/Dennis-Otto).</sub>

## Development

See [CONTRIBUTING.md](https://github.com/Dennis-Otto/issue-assistant/blob/main/CONTRIBUTING.md). The script needs only Python 3.12's standard library, `gh` and `git`, which GitHub's runners have; `check` reads YAML with PyYAML or `yq`. The tests check every GraphQL request against GitHub's schema, [dry runs](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/architecture/dry-runs.md) run the path of an issue through the AI and each reusable workflow as a repository calls them, and a change of [a template](https://github.com/Dennis-Otto/issue-assistant/blob/main/docs/architecture/templates.md) reaches the repositories with a release.

## License

[MIT](https://github.com/Dennis-Otto/issue-assistant/blob/main/LICENSE)
