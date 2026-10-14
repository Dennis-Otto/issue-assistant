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
| Events | `handle_event`, `within_limits` | Labels from the issue forms, and the next task of the engine: `triage`, `follow-up`, `maintainer-reply` or `release-reply`, within the [limits of the AI runs](https://github.com/Dennis-Otto/issue-assistant#limits-of-the-ai-runs) for the tasks that people who don't maintain the repository start |
| Context | `write_context` | Writes what the task of the engine reads into `.issue-assistant/`: the issue with its comments; for an analysis and a follow-up the other issues and discussions of a bounded window and the labels; for a reply the comment of its event; then the prompt and the JSON schema of the answer ([what the AI reads](how-it-works.md#what-the-ai-reads)); [each text is read in linear time](#reading-the-text-of-issues) |
| Prompts | `prompts/` | The rules of every task, which call the content of the issue data, not instructions |
| Checks of the answer | `answer_schema`, `validate`, `parse_answer`, `looks_secret`, `sanitize` | Check the engine's JSON against its schema, the labels and the issues that exist; refuse anything that looks like a secret; clean the text of mentions, images, HTML and links to other sites |
| Writing | `apply_answer` and the `render_*` functions | Post the comment, set the labels and mark duplicates, only on the issue of the event |
| Lifecycle | `sweep` | Reminders after 15 days, closing after 30 days, closing duplicates after 3 days, marking fixed issues, closing them with the release and filing them under its milestone |
| Labels | `sync_labels` | Create and update the labels of `labels.toml`, never delete one |
| Findings | `watch_findings` | Dismiss the findings of code scanning that `findings.toml` accepts and fail while others are open |
| Fixture | `read_fixture`, `GitHub.use_fixture` | An issue and its comments from a JSON file in place of GitHub's, for [the canary](#the-canary) and for trying a prompt; `context` and `apply` then write nothing |
| Set-up | `install`, `check` | Write the four workflows, the firewall policy and the starter files of `templates/` into a repository, and check that its set-up still fits the templates; both need only the checkout and name its files with slashes on every system |

## The workflows of a repository

--8<-- "README.md:workflows-diagram"

| Workflow | Jobs |
| --- | --- |
| `issue-assistant.yml` | `event` decides the next task; `analyze` writes the context and runs the engine with a read-only token, on GitHub's egress-firewall runner; `apply` checks the answer and writes it |
| `issue-lifecycle.yml` | `sweep`, every morning, after merged pull requests, published releases and the end of a run of the release workflow that no schedule started |
| `labels.yml` | `sync-labels`, when `labels.toml` changes on the default branch |
| `findings.yml` | `findings`, every morning, after each scan of code scanning on the default branch and when `findings.toml` changes |

## One run for a new issue

The three jobs of `issue-assistant.yml` hand the issue on; only the middle one sees the AI and its secret, and only the last one writes:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/one-run-of-issue-assistant-yml-for-a-new-issue.dark.svg">
  <img src="images/diagrams/one-run-of-issue-assistant-yml-for-a-new-issue.svg" alt="One run of issue-assistant.yml for a new issue: GitHub sends the event of the new issue 12 to the job event, which adds the labels of the form and needs-triage and hands the issue and the task triage to the job analyze. Its context step writes the issue, the other issues, the discussions, the labels, the prompt and the schema into .issue-assistant/ and hands the prompt and the schema to the engine, which runs with a read-only token, behind the egress firewall, with the engine's secret. The engine reads the checkout with Read, Grep and Glob and hands its answer in JSON to the job apply, which runs no AI and has no secret. apply checks the answer against the schema, the labels and the issues, cleans the text, and writes the labels, the comment and then needs-info, only on issue 12.">
</picture>

`install` copies the workflows of `templates/` with the commit hash of the release, and `check` fails when a repository's copy differs in anything but the commit hashes of its actions.

## Waiting, runners and the network

**The events of one issue wait in line.** The runs of `issue-assistant.yml` for one issue share the concurrency group `issue-assistant-<number>` with `queue: max`: up to 100 runs wait for their turn, first in, first out. An analysis takes about two and a half minutes, and without the queue a group keeps a single waiting run, which the next event cancels. A reporter who answered twice during an analysis then got no follow-up to the first answer, and a "still broken" after a release could stay without effect.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/comments-while-an-analysis-runs.dark.svg">
  <img src="images/diagrams/comments-while-an-analysis-runs.svg" alt="Comments while an analysis runs: A new issue starts the first run, which analyzes it. Two comments arrive during the analysis; their runs wait in the group of the issue, first in, first out. When the analysis is posted, the run of the first comment follows, then the run of the second one. Without the queue, the second run would have cancelled the first waiting one.">
</picture>

**A run with nothing to do takes no runner and no place in a line.** The sweep and the findings work on the whole repository, so one waiting run is enough, and their jobs, not their runs, wait in the groups `issue-lifecycle` and `findings`. A run waits for the group of its workflow before its jobs check their conditions, so a run that would only skip its job, such as after the scan of a pull request, took the place of a waiting one that had work to do; a skipped job never enters the group of a job. The release workflow of the [repository blueprint](https://github.com/Dennis-Otto/repo-blueprint) runs every hour as a safety net and mostly releases nothing: the lifecycle skips the ends of these scheduled runs, and the morning sweep catches a release that one of them made.

**The jobs without the AI reach only GitHub.** Every job runs on `ubuntu-24.04`, so a new image behind `ubuntu-latest` never reaches the repositories untested. The jobs that write start with Harden-Runner in block mode; the AI runs behind GitHub's egress firewall instead. [Decision 0008](decisions/0008-the-jobs-that-write-reach-only-github.md) explains why:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/where-the-jobs-run-and-which-hosts-they-reach.dark.svg">
  <img src="images/diagrams/where-the-jobs-run-and-which-hosts-they-reach.svg" alt="Where the jobs run and which hosts they reach: The jobs event, apply, sweep, sync-labels and findings run on ubuntu-24.04 behind Harden-Runner in block mode, without sudo and containers, and reach api.github.com and github.com. The job analyze runs on ubuntu-24.04-firewall and reaches the hosts of .github/egress-firewall.yaml. Every other host is blocked for both.">
</picture>

The checkout reaches `github.com` and the GitHub CLI `api.github.com`; Harden-Runner allows the hosts of the runner itself, such as those of the logs and the summary. The action turns off the telemetry of the GitHub CLI, which would only knock at a blocked host. The job `egress` of the Action test runs the action behind the same block, so a host that it starts to need fails there, before a release blocks it in every repository. In a private repository the block needs a subscription or trial of StepSecurity; without one, Harden-Runner lets the job run unblocked, as the [security design](security.md) says among its residual risks.

## How a change of a template reaches the repositories

The templates are in `templates/.github/`: the four workflows, the firewall policy and the starter files that `install` writes. This repository looks after its own issues with the same workflows, but its copies in `.github/` belong to the [repository blueprint](https://github.com/Dennis-Otto/repo-blueprint): every update of the blueprint writes them anew, as the templates of the release of the issue assistant that the blueprint pins. `install` and `check` of this commit never read these copies, only the check of the pinned release in the Lint workflow does (step 1 below), and no test compares them with `templates/`, because they are a release behind after every change of a template. [Decision 0007](decisions/0007-templates-live-in-templates.md) explains why.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/how-a-change-of-a-template-reaches-the-repositories.dark.svg">
  <img src="images/diagrams/how-a-change-of-a-template-reaches-the-repositories.svg" alt="How a change of a template reaches the repositories: A pull request in this repository changes templates/. Its checks lint the templates and check the set-up with them installed, while the required check issue-assistant compares this repository's own workflows with the release that they pin, so it stays green. Merged, the change becomes a release. A pull request in the repository blueprint installs that release there and moves the pins of the action. The next release of the blueprint brings it to every repository made from it, this one too. Repositories without the blueprint get the release from Dependabot and run install again.">
</picture>

1. **A pull request here** changes `templates/`, never the copies in `.github/`. `tests/test_templates.py` pins the rules of the templates. The Action test lints them with actionlint and zizmor, installs them into the sample repository and into a copy of this repository, and checks both set-ups through the action. The required check `issue-assistant` runs `check` of the release that the Lint workflow pins, on this repository's own copies, which the pull request leaves as they are.
2. **The release** of the change is a new minor version, and its notes say that repositories run `install` again. A change of `issue-assistant.yml` or of the firewall policy starts [the canary](#the-canary) with its merge, and the release waits until it passed.
3. **The blueprint takes the release over** in a pull request of its own: it runs `install` of the release in its root, takes over the firewall policy of `templates/` and moves every pin of the action, that of the check in its Lint workflow too. It also adapts its own checks to the templates when they need it, as when actionlint had to ignore the queue of a concurrency group, which it doesn't know yet. A pull request that changed only the blueprint's copies would fail the check `issue-assistant` there in the same way.
4. **The next release of the blueprint** reaches every repository made from it, this one too, as an update pull request of the blueprint bot. Repositories that use the action without the blueprint receive the release as a Dependabot pull request and run `install` again.

## How the requests to GitHub are tested

Most tests replace GitHub with the fake in `tests/support.py`, which answers the calls of the script from the issues, comments and pull requests that a test sets up. The fake knows a GraphQL query by its words, so it can't tell a misspelt field from a right one, or a field that GitHub renamed last week. The script, for its part, falls back where one of GitHub's newer fields fails: labels go without their rationale, and an issue closes through REST, without the link to its original. Such a mistake would show only as a notice in the log of a repository that uses the action, so three checks look for it before a release:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/three-checks-of-the-requests-to-github.dark.svg">
  <img src="images/diagrams/three-checks-of-the-requests-to-github.svg" alt="Three checks of the requests to GitHub: The GraphQL documents of issue_assistant.py meet a copy of GitHub's schema in the required check of every pull request. The commands sweep, sync-labels and context run against GitHub itself with a token that can only read, on every pull request, on main and every week. Every week the documents also meet the schema that GitHub publishes that day, and a notice says when the copy is behind it.">
</picture>

1. **Every GraphQL document meets GitHub's schema.** `tests/test_graphql_contract.py` runs each method of the class `GitHub` that sends GraphQL, catches what it hands to `gh` and checks it against GitHub's public schema, whose copy is `tests/github/schema.docs.graphql`: every field and argument must exist and none may be deprecated, and every variable must fit its type, such as the reason for closing an issue. The answer is worked out from the schema as well, so it holds only the fields that the document asks for, and the method can read no other. A document that the test doesn't know fails it, so a new one comes with its case.
2. **The commands that read run against GitHub.** The job `egress` of the Action test runs the action of the pull request as dry runs of `sweep` and `sync-labels` on this repository, and `context` for a closed issue of [ha-autodarts](https://github.com/Dennis-Otto/ha-autodarts), a public repository that uses the action, with its comments, the other issues and the discussions. Its token can only read, so a write that a dry run let through would fail too.
3. **Every week, the schema of the day.** GitHub changes its API without a release of the action. The job `schema` of the Action test checks the documents every week against the schema that GitHub publishes that day. GitHub deprecates a field, an argument or a value of an enum months before it removes it, and says when, so the run turns red as soon as the script uses something deprecated, while it still works. When the copy in `tests/github/` is behind, a notice says so, and a pull request refreshes it with the command in `tests/test_graphql_contract.py`.

## The canary

The tests replace the engine, and the Action test runs everything but it, so the path that matters most, from an issue through the engine to the checked answer, ran only when someone opened an issue: here that is rare, and every repository that uses the action runs it with its own secret. A renamed output of the engine's action, a host that the firewall blocks or an expired secret would show as issues without an answer, in every repository at once. The Canary, `.github/workflows/canary.yml`, runs the whole path every Monday, after every change on `main` of what it runs, and by hand:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/how-the-canary-runs-the-path-of-an-issue-through-the-ai.dark.svg">
  <img src="images/diagrams/how-the-canary-runs-the-path-of-an-issue-through-the-ai.svg" alt="How the Canary runs the path of an issue through the AI: The Canary starts every Monday, after a change on main of the script, the prompts, the action, the workflow template, the firewall policy, the settings or the canary's issue, and by hand. Its job analyze is that of the template: on the firewall runner, with the engine's secret of the environment issue-assistant, context reads the issue of tests/fixtures/canary-issue.json instead of one on GitHub, and the engine answers with JSON. Without the secret or without an answer, the canary fails. Its job apply checks the answer as for a real issue, as a dry run with a token that can only read: an answer that breaks a check fails the canary, and one that passes shows the comment it would post in the summary.">
</picture>

- **The issue exists nowhere.** `tests/fixtures/canary-issue.json` holds an issue of the bug report form of this repository and a comment, as GitHub's REST API gives them. The environment variable `ISSUE_ASSISTANT_FIXTURE` gives them to `context` and `apply` in place of an issue on GitHub; the other issues, the discussions, the labels and the latest release come from GitHub, as for a real issue. With a fixture, `apply` is a dry run whatever `dry-run` says, and the tokens of the jobs `analyze` and `apply` can only read, so nothing reaches an issue. A test keeps the issue fitting the form and the labels of this repository.
- **The engine runs as in every repository.** `tests/test_templates.py` keeps the job `analyze` of the canary equal to that of the template: the runner, the environment, the permissions, the outputs and the steps of the engines, with their pins. Only the steps of the issue assistant run the action of the commit, as `./`, and the engines also accept a run that a bot started, as the canary's own runs after a push are. Where a repository's run skips the AI, without the secret or without an answer, the canary fails.
- **The firewall policy is this repository's.** The runner enforces `.github/egress-firewall.yaml`, the blueprint's copy of the template of the release that it pins, so a change of `templates/.github/egress-firewall.yaml` reaches the canary with the blueprint's next update.
- **Only `main` runs it.** Only the default branch may use the environment `issue-assistant`, so no pull request runs the canary, and an update of the engine's action doesn't merge on its own: Renovate gives it a pull request of its own that waits for the maintainer, as `.github/renovate.json5` says. The push of its merge starts the canary with the new version; the release waits until the canary passed, and a failed update is reverted. The engine's action runs for a schedule or a run by hand, not for a push, so after a push the job `start`, whose token may only start workflows, starts the canary as a run by hand and waits for its result.
- **A red canary is seen.** GitHub mails the failure of a scheduled run to whoever last changed its schedule, here the maintainer. A run that the job `start` started has nobody to mail, so that job fails with it, and GitHub mails the failure of the push's run to the person who merged; a canary that a later push's canary replaced fails nothing. Every red run also shows under Actions.

## When a call to GitHub fails

Every call goes through `gh api`, in `GitHub._gh`. When it fails, the `GitHubError` carries what GitHub said: the HTTP status from the message of gh, `gh: Not Found (HTTP 404)`, and the types of GraphQL's errors from GitHub's JSON, such as `NOT_FOUND`. Its methods decide what happens next:

| The error | What happens |
| --- | --- |
| `missing()`: status 404 or 410, or the type `NOT_FOUND` | the thing doesn't exist: no other issue, discussion or release, a commit that no release contains, an issue that was deleted or moved |
| `rate_limited()`: a secondary rate limit | GitHub refused the call before doing anything, so it runs again after a minute, a write too, up to 3 times; gh never shows the header Retry-After, and a minute is what GitHub asks for without it |
| `transient()`: a status of 500 or more, or a broken connection | a read runs again after 2, 4 and 8 seconds; a write doesn't, since GitHub may have done it anyway |
| any other, such as the rate limit of the hour | the error goes up to the command |

Only the places that look a thing up catch an error, and only when it is `missing()`. Every other error stops the command: `main` prints it as one escaped `::error::` and the summary lists the writes of the run before it, and the write that failed. A run again repeats nothing, because every step checks first what is done:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/how-a-run-of-apply-after-a-failed-one-avoids-a-second-comment.dark.svg">
  <img src="images/diagrams/how-a-run-of-apply-after-a-failed-one-avoids-a-second-comment.svg" alt="How a run of apply after a failed one avoids a second comment: Apply looks for the comment of an earlier run of the same event. Without one, it adds the labels and possible-duplicate, posts the comment, and adds needs-info last, so that an issue never waits for an answer to a question that GitHub didn't post. With one, it posts nothing: it adds the labels of the answer and the lifecycle labels that the comment named, except the labels that anyone changed since the comment, and needs-info once the reporter answered.">
</picture>

The sweep runs each step and each issue through `isolate`: a `GitHubError` becomes a `::warning::` and a line of `SweepIncomplete`, which `main` reports with what was done before it fails the run. The steps of the sweep check what an earlier one did: a fixed issue loses `needs-info` and `stale` and gets `fixed-in-next-release` before its notice, so a failed notice never leaves it waiting for an answer, a comment of the release, of the closing of an unanswered issue or of a duplicate isn't posted twice, and an issue closes before its labels are removed, so the next sweep still finds one that GitHub didn't close.

**Which release closes a fixed issue.** `mark_fixed` reads the pull requests merged in the last 14 days, `MERGE_LOOKBACK`, a hundred at a time: GitHub lists them by their last update, which a merge is, so the pages end with the first one that nobody updated within the lookback. A busy repository merges far more than a page in that time. The query gives the state of each issue that a pull request fixes, so a closed one, as most are by then, costs no request of the token's 1,000 an hour; the read of an open issue decides. After its labels and before its notice, `mark_issue_fixed` asks whether a final release published after the merge already contains the merge commit, as when the run of the merge failed, so that a read of the releases that GitHub fails loses only the notice; then `close_issue_released` closes the issue at once, unless another fix still waits, and a later sweep never announces a fix that a `released` comment came after. `close_issue_released` takes the fixes of an issue from `closedByPullRequestsReferences`, only those of this repository, merged at most `MERGE_LOOKBACK` and `LABEL_SLACK`, an hour, before its last `labeled` event of `fixed-in-next-release` (a sweep labels the issue later in its run than the start that its lookback counts from), and not before a `released` comment that came before its last `reopened` event: the release closed the issue with that fix, and the reporter reopened it. `first_release` skips the releases published before the last of them was merged, or before the label without a fix or when someone other than `github-actions[bot]` set it, and `compare` proves that a release contains each merge commit. `releases` reads the newest hundred, as a beta channel publishes a pre-release for each fix.

## Reading the text of issues

Anyone can write an issue, and every analysis reads the text of every open issue and of the closed ones that changed last, for the excerpts of the other issues. One text that kept a pattern busy for minutes would stall every analysis until its job timed out. So the time to read a text grows only with its length:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/how-the-assistant-reads-the-text-of-an-issue.dark.svg">
  <img src="images/diagrams/how-the-assistant-reads-the-text-of-an-issue.svg" alt="How the assistant reads the text of an issue: The text of an issue or a comment, which anyone can write, is read up to GitHub's 65,536 characters. The answers of a form are cut out between their headings, which are found once; from them come the labels of the form and the reporter's language. For the AI, the answers of a form and HTML comments make an excerpt of each other issue, and HTML comments and tags go in one pass for issue.md. Then the characters that GitHub shows as nothing go, in one pass, after the HTML, so that none can end an HTML comment that GitHub never ends. The excerpts go into issues.jsonl, and issue.md holds up to 20,000 characters of each text in a code fence that the text can't close.">
</picture>

- **No pattern backtracks.** Each one looks at a character a bounded number of times: a form heading is one line, a tag ends on its line, and when no `>` closes a tag on its line, the rest of the line is passed over in one step.
- **HTML comments go as GitHub hides them.** A line that starts with a `<!--` that never ends hides the rest of the text, as on GitHub, so instructions hidden that way don't reach the AI either. A `<!--` without its end within a line stays text, as GitHub shows it.
- **Each text keeps to its place.** `render_issue` puts each text in a code fence one backtick longer than its longest run of backticks, under a heading that only the script writes, so no text can pass for another comment, the maintainer or the assistant; `Visible` takes out the characters that GitHub shows as nothing before the fence is chosen, so that none can split a run of backticks, and after the HTML, so that none can end an HTML comment that GitHub never ends and show the AI what it hides. The [security design](security.md#how-the-text-of-an-issue-reaches-the-ai) shows why.
- **Tests and fuzzing time it.** The tests read texts of 64 KiB that took the earlier patterns up to hours, and compare the results with theirs on ordinary texts; `fuzz/fuzz_issue_text.py` fails when reading a crafted text takes longer than half a second.

## Design decisions

- **The AI only reads, and a job without the AI writes.** The answer of the engine is data that `apply` checks before anything reaches GitHub.
- **Everything but one step is independent of the engine.** Context, prompts, checks and writes are the same for every engine; a new engine is a step and an entry of `ENGINES`, as the [README](https://github.com/Dennis-Otto/issue-assistant#engines) describes.
- **An error of GitHub is never an answer.** Only "not found" means that a thing is missing; any other error stops the run instead of changing what it posts, and a run again completes it without a second comment.
- **No text can make it slow.** Issues come from anyone, so every pattern that reads them takes time linear in the length of the text.
- **No third-party packages.** The script needs only the standard library, `gh` and `git`, which GitHub's runners have.
- **The maintainer has the last word.** The assistant labels, asks and suggests; it never decides on behalf of the maintainer.
