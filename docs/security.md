# Security design

[← README](https://github.com/Dennis-Otto/issue-assistant) · [Architecture](architecture.md) · [Roadmap](roadmap.md)

What the issue assistant protects, what it trusts and which risks remain. [SECURITY.md](https://github.com/Dennis-Otto/issue-assistant/blob/main/SECURITY.md) says how to report a vulnerability and how to verify a release, and argues why this repository and its releases are safe. Issues come from anyone, so the design assumes that an issue tries to steer the AI.

## What you can expect

- The AI only reads: the checkout of the default branch and the context that the assistant writes for it. It runs no commands but those of Codex, in a sandbox that only reads, opens no web pages and reads no files outside the checkout, on a runner whose network allows only the hosts of `.github/egress-firewall.yaml`: GitHub's egress-firewall runner, or, should that runner of a technical preview be gone, the runner that the repository variable `ISSUE_ASSISTANT_AI_RUNNER` names, behind Harden-Runner with the same hosts.
- Nothing the AI writes reaches GitHub unchecked. A job that runs no AI and never sees its secret checks the answer and writes it, only to the issue or the discussion of the event.
- No comment of the assistant mentions anyone, links to a site that `config.toml` doesn't allow, to another repository, other than an issue that exists in a public repository of `[related]`, or to an e-mail address, shows an image or HTML, or contains something that looks like a token or a key. What counts is the whole comment as GitHub shows it, not each field alone: the text of a field is as safe under a heading as in a list or behind an issue number.
- The same holds for the AI's words outside its comments: in the log of the run, in its summary and in the reasons for labels and duplicates that GitHub shows on the timeline of the issue. They stay on one line there, and no answer can run a workflow command, such as `::add-mask::` or `::stop-commands::`.
- No issue can stall the assistant. It reads every text in time that grows only with the length of the text, and no more of a text than GitHub keeps, 65,536 characters.
- No stranger can run up the bill of the engine. The runs of the AI that people who don't maintain the repository start stay within the limits of `config.toml`: by default 20 analyses of new issues, follow-ups and replies after a release in 24 hours, 3 analyses of one reporter's new issues, and 2 comments of the reporter after each release that closed the issue. Every new issue counts, also while the first analyses still run, and so does every follow-up and reply after a release, also when the reporter deletes their comments.
- No text of an issue can pass for someone else or hide from the maintainer. The AI reads each text in a code fence that nothing in the text can close, under a heading of the assistant that names its author and role, and without the characters that GitHub shows as nothing.
- No issue can close another reporter's issue. A sure duplicate closes on its own only when a maintainer opened or triaged the original, and a comment of anyone, or a thumbs down of the reporter or of anyone who may push to the repository, keeps it open.
- The secret of the engine stays in the environment `issue-assistant`, which only the default branch may use; only the job that runs the engine sees it.
- No code of a pull request runs in a workflow of the assistant.
- The jobs that write reach only GitHub, in a public repository and in a private one whose owner subscribes to StepSecurity. Every job without the AI blocks every host but `api.github.com` and `github.com` with Harden-Runner, and no step in it can use sudo or containers.
- An open finding of code scanning is named in a public log only by the number and link of its alert.
- The action stores no secrets and no data outside GitHub. Only Harden-Runner, in the jobs without the AI, reports to StepSecurity, as the residual risks say.

What the checks make of an answer that an issue steered, in a demo project:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/cleaning-dark.webp">
  <img src="images/cleaning-light.webp" alt="What the apply job makes of an answer that an issue steered: a mention becomes code, a link to another site keeps only its text, an image keeps only its description, HTML is removed, unknown issue numbers become code, links into the repository and to existing issues stay links, and an answer with something like a token is refused as a whole">
</picture>

Three issues that try to steer the AI, in the same demo project: what each one says, what the AI reads of it, what the AI could answer, and what `apply` posts of that answer:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/steered-dark.webp">
  <img src="images/steered-light.webp" alt="Three issues that try to steer the AI. Instructions in an HTML comment never reach the AI, which reads the text without HTML, so its answer only helps with the issue. A message that poses as the system reaches the AI as the reporter's text; should the AI follow it, the link to another site keeps only its text and the mention becomes code. A flood of mentions and links: the mentions become code and notify nobody, issue numbers that the checks didn't find become code, and the addresses lead nowhere else">
</picture>

## What is protected

| Asset | Where it lives | Protection |
| --- | --- | --- |
| The token of the engine, such as `CLAUDE_CODE_OAUTH_TOKEN` | The environment `issue-assistant` of the repository | Only the default branch may use the environment, and only the job that runs the engine gets the secret |
| The issues, labels and comments of the repository | GitHub | Written only by `apply`, `sweep`, `sync-labels` and `event`, which run no AI, with the permissions their job grants, and whose network reaches GitHub alone where Harden-Runner blocks |
| The discussions of the repository | GitHub | Written only by the `apply` job of `discussion-assistant.yml`, the one job of the assistant with `discussions: write`: one comment on the discussion of the event, never marked as the answer, and only where `config.toml` names its category |
| The findings of code scanning | GitHub's code scanning | Visible only to maintainers; the Findings workflow names them by number and link only |
| The workflows of the repositories that use the action | Their `.github/workflows/`, which call the jobs at the commit of a release | `check` fails when a workflow differs from its template in anything but the commit hashes of its pins; the jobs are at the pinned commit of this repository, which no repository can change |

## Trust boundaries

Red is what anyone can write; the numbers are those of the list below.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/the-trust-boundaries-of-the-issue-assistant.dark.svg">
  <img src="images/diagrams/the-trust-boundaries-of-the-issue-assistant.svg" alt="The trust boundaries of the issue assistant: Anyone can write an issue, its comments, other issues and discussions, and a pull request. The text reaches the event job, which runs no AI and chooses the task, and, as data in files, the job analyze: the first boundary. There the AI reads with tools that only read, behind a firewall, with a read-only token or none and the engine's secret. Its answer in JSON crosses the second boundary into the job apply, which runs no AI and holds no secret. Its checks want the schema, labels and issues that exist, and no secret, mention, image, HTML or foreign link. When they fail, nothing is posted; when they pass, apply writes to GitHub, only to the issue or the discussion of the event: the third boundary. A merged pull request starts issue-lifecycle.yml on the default branch, without AI, which learns only the issues that it fixes, the fourth boundary, and writes to GitHub.">
</picture>

1. **Issues, discussions and comments → the assistant.** Their text is untrusted. It reaches the AI as files that the prompt calls data, not instructions, each text in a code fence that it can't close and without invisible characters, as [the next section](security/issue-text.md) shows, and reaches a shell script only through environment variables.
2. **The AI → the `apply` job.** The answer is untrusted. It must be JSON that fits the schema of its task, with labels that exist and numbers of issues that exist; anything that looks like a secret refuses the whole answer.
3. **The `apply` job → GitHub.** Only the issue or the discussion of the event, with the permissions of its job; only the `apply` job of the discussions may write to discussions, and there only one comment.
4. **Pull requests → the lifecycle.** The lifecycle workflow starts on `pull_request_target` only to learn which issues a merged pull request fixes. It checks out the default branch, runs no AI and skips Dependabot's pull requests.
5. **The action → the repository that uses it.** The repository pins the action by the commit hash of a release, and the workflows grant each job only the permissions it needs.

## Residual risks

- The AI can be wrong, or be steered into a wrong but harmless answer, such as a wrong label or a misleading analysis. A steered choice of areas can at most assign the owners that `labels.toml` names for them, who get GitHub's notification; no text names people. The maintainer reads every issue and has the last word.
- Within the limits of the AI runs, strangers still spend the repository's money: by default about 20 runs a day, about $4 at the prices of the API, each run at most its turns and the dollars of `max_budget_usd`; answers that come in within the same few seconds can pass the count together, because each event job counts before it records its run. A flood of issues uses up the day, so that the issues of real reporters wait for the maintainer until 24 hours have passed. A secret of the engine that belongs to the repository alone caps the cost: an API key of a workspace with a monthly spend limit. The token of a subscription shares its usage limits with every other repository and person that uses it, so a flood in one repository can stop the analyses of all of them.
- The reporter of an issue that a maintainer triaged can still edit its text later, and steer the analyses of later issues into calling them its duplicates. Their reporters get the notice and keep the issue open with a comment or a thumbs down, and the maintainer sees the label `possible-duplicate` for three days.
- The AI reads the whole checkout of the default branch. Whatever is in the repository may appear in its analysis, so keep secrets out of the repository, as every repository should.
- The provider of the engine receives the text of the issue and the parts of the repository that the AI reads. The notice in `SUPPORT.md` tells reporters which AI reads their issue. Google may use what reaches the free tier of the Gemini API to improve its products.
- Codex reads through commands, which its sandbox keeps from writing and from the network, so a fault of that sandbox would let a steered Codex run code in the job. The Gemini CLI and the Copilot CLI keep to their reading tools by their own settings, not by a sandbox of the system. Either way the job holds a token that only reads, or none, the engine's secret alone, and the hosts of its block in the firewall policy, and its answer still meets every check of `apply`.
- The check *engine contracts* sees the names that keep an engine to reading, not what they do: a flag or a key that keeps its name in a new release but changes its meaning, or a new setting whose default undoes one of them, passes it. A minor or major version of an engine waits for the maintainer, whose pull request of Renovate asks to read its release notes; a patch version merges on its own after that check, and its pull request asks the maintainer to read them before the next release, which brings it to the repositories. The Canary runs a patch of the Claude Code action with its secret after the merge; nothing runs one of the other engines with a key before a release.
- A lock file pins what its author published, not what is safe: a package that its author published with a fault or with malice carries a valid signature, and once its update merged, it runs with the engine's secret in every repository after the next release. The weekly refresh of the lock files and a minor or major version wait for the maintainer, whose pull request of Renovate shows in the diff of `package-lock.json` what a new indirect dependency adds; a patch version of a command line merges on its own, with the new indirect dependencies that its lock file brings, and its pull request asks for that look before the next release. The firewall keeps the secret to the hosts of its engine.
- The check *engine contracts* verifies the signatures of the packages that npm installs on GitHub's Ubuntu x64 runner, where `analyze` runs. The builds of the command lines for other systems, such as `@github/copilot-darwin-arm64` or `@lydell/node-pty-win32-x64`, are in the lock files too, pinned by their hash, but no check verifies their signatures, since `analyze` never installs them. A runner for another system in `analyze` would need the check to install the lock files for that system as well.
- The assistant is as safe as the permissions that the workflows of a repository grant it; `check` keeps them equal to the templates.
- On the runner that `ISSUE_ASSISTANT_AI_RUNNER` names, Harden-Runner blocks the hosts for the job, inside the machine, where the firewall blocks them for the whole machine, out of reach of every step. The engines run no command that could lift the block, but a fault of their boundary that let a steered engine run commands with sudo would reach past it, and StepSecurity receives what its agent records in `analyze` too. So the firewall runner stays the default, and the variable is a fallback while it is gone.
- Harden-Runner fails open. In a private repository whose owner has no subscription or trial of StepSecurity, and when StepSecurity answers at the start of a job that its service is unavailable, Harden-Runner installs no agent, and the job runs without the block. It also waits only about ten seconds for its agent, so when the agent takes longer, the first steps of the job run before the block; in `analyze`, the step after Harden-Runner waits that out. And the agent gives up and blocks nothing when it can't resolve an allowed host at its start; `analyze` then fails rather than run the AI without the block, unless `example.com` happens to fail for two requests in a row, five seconds apart. In a job without that step, a write token could then reach any host, as before the block; the pins of the actions to commit hashes and jobs that run no AI and no code of an issue or a pull request stay the defense.
- StepSecurity, the maker of Harden-Runner, is a third party that receives data from every job without the AI: the repository, the run and the job at its start, and what its agent records during the job, the hosts that the steps connect to, blocked or allowed, and the processes that connect. Its insights page for the run shows them.
- The runner shows the inputs of a step in its log, and the log of a public repository's run is public, for 90 days unless the repository keeps logs for less. So the log of the `apply` job holds the whole answer of the AI, also what is never posted, such as its summary of a possible security report. The prompt asks for one neutral sentence without details there, but an AI can miss that; hiding the answer would take an artifact between the jobs. A maintainer who finds too much in a run deletes its logs.

## The details

Each of these has a page of its own:

- [Against PromptPwnd and Comment and Control](security/prompt-attacks.md)
- [The engines and their packages](security/engines.md)
- [How the text of an issue reaches the AI](security/issue-text.md)
- [What becomes of the AI's answer](security/answers.md)
- [Threats and countermeasures](security/threats.md)
- [Threats to the runs and the workflows](security/threats-to-the-runs.md)
