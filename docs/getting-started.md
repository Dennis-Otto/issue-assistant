---
description: What a repository needs for the issue assistant, how to install its workflows and settings step by step, and how to check that it works.
---

# Get started

What a repository needs for the issue assistant, how to set it up there, and how to see it work before the first reporter does.

## Requirements

- **A repository on GitHub.com with issues turned on,** public or private. Issue forms are optional; their answers choose labels when the repository has them.
- **GitHub-hosted runners.** The jobs without AI run on `ubuntu-24.04`, the AI on `ubuntu-24.04-firewall`, the runner with GitHub's [native egress firewall](https://github.com/github-early-access/actions-native-egress-firewall). That runner is a **technical preview**: it exists for Linux only, and GitHub expects a workflow on it to take roughly 15 to 20 % longer. The analysis runs on this runner, so that the AI reaches no host but those of `.github/egress-firewall.yaml`, unless the repository variable `ISSUE_ASSISTANT_AI_RUNNER` names one of GitHub's runners of Ubuntu with a fixed version in its place, such as `ubuntu-24.04`, where Harden-Runner blocks the other hosts: [What if the firewall runner is gone?](faq.md#what-if-the-firewall-runner-is-gone)
- **An engine and its secret,** in the environment `issue-assistant`: for Claude, the default engine, a token of a Claude subscription, which `claude setup-token` creates, as `CLAUDE_CODE_OAUTH_TOKEN`, or an API key as `ANTHROPIC_API_KEY`; for Codex, Gemini or Copilot, the secret that [Engines](engines.md) names. Gemini starts for free, within the daily limits of the free tier of the Gemini API. Without the secret, everything that needs the AI pauses: the first analysis, the follow-ups, and deciding whether a comment waits for the reporter or says that a fix didn't help. Labels from the forms, reminders, closing and the closing of fixed issues with the release keep working, and each run notes that no credentials are configured.
- **Dependabot or Renovate,** whose pull requests bring each new release: [Turn on updates](#turn-on-updates).
- **For the set-up:** Python 3.12 or newer with PyYAML (`pip install pyyaml`) or `yq`, git and the GitHub CLI on your computer, and the right to manage environments and secrets of the repository.

## Set up a repository

A set-up has three parts: what `install` writes, what you edit, and what you set on GitHub:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/what-install-writes-what-you-edit-and-what-you-set-on-github.dark.svg">
  <img src="images/diagrams/what-install-writes-what-you-edit-and-what-you-set-on-github.svg" alt="What install writes, what you edit and what you set on GitHub: install of a release, run in the root of the repository, writes the four workflows, the workflow of the discussions where answer_categories of config.toml names a category, the firewall policy .github/egress-firewall.yaml and an entry in .gitignore; adds the notice to reporters to SUPPORT.md and the queue of issue-assistant.yml to actionlint's settings where they are missing; and writes starters of project.md, config.toml, labels.toml, findings.toml, two issue forms and their chooser when they are missing. With --setup, it also creates the environment issue-assistant on GitHub, limited to the default branch, and asks for the engine's secret. Then you describe the project in project.md, adapt the starters, turn off the auto-close of issues by merged pull requests, and turn on what the links of the chooser need: Discussions and private vulnerability reporting. Then check passes, and a run by hand shows the first analysis as a dry run.">
</picture>

<!-- --8<-- [start:quick-start] -->

1. **Install the latest release** in the root of your repository, with Python 3.12 or newer, PyYAML (`pip install pyyaml`) or [`yq`](https://github.com/mikefarah/yq) to read YAML, git and the [GitHub CLI](https://cli.github.com):

    ```sh
    tag="$(gh release view --repo Dennis-Otto/issue-assistant --json tagName --jq .tagName)"
    dir="$(mktemp -d)"
    git clone --quiet --depth 1 --branch "$tag" https://github.com/Dennis-Otto/issue-assistant "$dir" &&
      PYTHONPATH="$dir" python3 -P -m issue_assistant install --setup
    ```

    `install` pins the workflows to the commit and the tag of its checkout, and writes the settings and the starters that the repository lacks. `--setup` creates the environment `issue-assistant`, which only the default branch may use, and asks for the engine's secret. For Claude, `claude setup-token` creates the token of a Claude subscription, `CLAUDE_CODE_OAUTH_TOKEN`; an API key goes into `ANTHROPIC_API_KEY` instead. For another engine, set its `name` in `config.toml` first, as [Change the engine](engines.md#change-the-engine) shows, and `--setup` asks for its secret. Each step looks first, so running it again changes nothing but the workflows.

2. **Describe the project** in `.github/issue-assistant/project.md`: what it does, where the code, documentation, troubleshooting guide and changelog are, which versions and logs matter in a bug report, and how to address reporters if not as the [catalogs of the languages](features/languages.md) do, such as with *Sie* in German. The AI reads it before every task. Then adapt the starters to the project, its labels and its issue forms, until the check passes:

    ```sh
    PYTHONPATH="$dir" python3 -P -m issue_assistant check
    ```

3. **Let the release close fixed issues:** under *Settings → General → Issues*, clear **Auto-close issues with merged linked pull requests**, so that a fixed issue stays open until the release that ships the fix. GitHub has no API for this setting. A repository without releases sets `close_with_release = false` in `config.toml` instead.

    **Turn on what the chooser links to,** when `install` wrote `.github/ISSUE_TEMPLATE/config.yml`, as both links lead nowhere until then: *Settings → General → Features → Discussions*, and *Settings → Advanced Security → Private vulnerability reporting*. GitHub offers private vulnerability reporting only for public repositories, so a private repository removes that link.

    **[Turn on updates](getting-started.md#turn-on-updates)** by Dependabot or Renovate, if neither looks after the repository yet.

4. **Try it:** commit the set-up, push it to the default branch, and under *Actions → Issue assistant → Run workflow*, enter the number of an issue. A run by hand starts as a dry run, which writes nothing; the summary of the job *Check and post the analysis* (`apply`) shows the labels and the comment it would post:

    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/dry-run-dark.webp">
      <img src="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/dry-run-light.webp" alt="A run of the Issue assistant workflow by hand on a demo project: the summary of the event job, and the summary of the apply job with the labels it would add and the comment it would post, as a dry run">
    </picture>

<!-- --8<-- [end:quick-start] -->

On Windows, run the commands in Git Bash; Python's launcher `py` takes the place of `python3`, which there usually opens the Microsoft Store.

### What install writes

| File | When | What you do |
| --- | --- | --- |
| `.github/workflows/issue-assistant.yml`, `issue-lifecycle.yml`, `labels.yml`, `findings.yml` | always, with the pin of the release | nothing: the check makes sure that they equal the templates |
| `.github/workflows/discussion-assistant.yml` | with the pin of the release, when `[discussions] answer_categories` of `config.toml` names a category, as the starter does, or when it is there | nothing: the check makes sure that it equals its template, and wants it while a category is named |
| `.github/egress-firewall.yaml` | when missing | add the hosts that the engine needs besides those of the template, if any |
| `.github/issue-assistant/project.md` | when missing | describe the project, as step 2 says |
| `.github/labels.toml` | when missing | define the labels: the seven lifecycle labels are required; kinds, areas, topics and, if you add them, priorities are what the AI chooses from |
| `.github/issue-assistant/config.toml` | when missing | choose the engine, the hosts that links may lead to, the field of the issue forms that chooses an area, the categories of discussions that get a first answer, and the files that tell reporters which AI reads their issue |
| `.github/findings.toml` | when missing | accept findings of code scanning that can't or shouldn't be fixed, each with its reason |
| `.github/ISSUE_TEMPLATE/bug_report.yml`, `feature_request.yml` | together with the starter `labels.toml`, when the repository has no issue forms or templates | adapt the answers of the field *Area*, whose `form_options` in `labels.toml` choose an area label; their box *Automated analysis* lets a reporter [say no to the AI](features/opt-out.md) |
| `.github/ISSUE_TEMPLATE/config.yml` | with these forms, when it is missing | turn on what its links need in the settings of the repository: Discussions, for the first category of `[discussions] answer_categories`, and private vulnerability reporting, which GitHub offers only for public repositories; remove a link that you don't want or that can't work |
| `SUPPORT.md`, or the files of `[transparency]` | when they don't name the engine; in a notice that they have, the days of `[lifecycle]` in each line that still reads as the template's | the section *What happens with your issue*, which the footer of every analysis and follow-up links, tells reporters which AI reads their issue, in German for a file such as `SUPPORT.de.md` |
| `.github/actionlint.yaml` | when it exists | nothing: it ignores the `queue` of `issue-assistant.yml`, which actionlint 1.7.12 doesn't know yet |
| `.gitignore` | when the entry is missing | nothing: `/.issue-assistant/` keeps the context of the AI out of commits |

The chooser is what GitHub shows on *New issue*. It turns blank issues off, so that every issue starts from a form, and its links take the rest to where it belongs: the first category of `answer_categories`, a link that it leaves out when none is named, private vulnerability reporting, and the first file of `[transparency]`. `install` takes the owner and the name in these links from `GH_REPO`, from `GITHUB_REPOSITORY` or from the remote `origin`; without any of them, it writes `OWNER/NAME`, and the check asks you to replace it; running `install` again does that once the remote names the repository.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/diagrams/where-the-chooser-of-new-issues-leads.dark.svg">
  <img src="images/diagrams/where-the-chooser-of-new-issues-leads.svg" alt="Where the chooser of new issues leads: On New issue, GitHub shows the chooser that install writes for a repository without forms, with blank issues turned off. A bug report and a feature request each open a form, whose answers choose the labels before the first analysis. A question goes to the discussions, a vulnerability to a private report, and the link of the support says what happens with an issue">
</picture>

GitHub ignores the case of label names, so the Labels workflow renames a label that the repository has in another case, such as `Bug` for `bug`, instead of creating a second one. A YAML file that `install` can't edit without changing the rest, such as a list in braces, it leaves as it is and names what to add.

### Keep it checked

Add the check to the CI of the repository, so that a change of the labels, the forms or the settings can't break the assistant unnoticed:

```yaml
- uses: actions/checkout@<commit-hash> # vX
  with:
    persist-credentials: false
- name: Check the issue assistant's set-up
  uses: Dennis-Otto/issue-assistant@<commit-hash> # vX.Y.Z
  # Optional: check the runner that the variable names for the AI, too.
  env:
    ISSUE_ASSISTANT_AI_RUNNER: ${{ vars.ISSUE_ASSISTANT_AI_RUNNER }}
  with:
    command: check
```

The [listing on the GitHub Marketplace](https://github.com/marketplace/actions/read-only-issue-assistant) offers a bare step of the action under *Use latest version*, with every input and an empty `command`; on its own, give it `command: check`, as above. The four workflows that run the assistant come from `install`, in step 1 of [Set up a repository](#set-up-a-repository), and call the other commands of the action themselves.

The check runs on Linux, macOS or Windows, with Python 3.12 or newer, and reads YAML with PyYAML or `yq`. In a job, the action is tested on `ubuntu-24.04`, `ubuntu-26.04` and `ubuntu-24.04-arm`; on an older Python it stops with an error that names the version and the fix. When a workflow differs from its template, it names the commands that write the templates of its release anew.

### Without `--setup`

`install` alone writes only files. Create the environment and the secret on GitHub yourself, under *Settings → Environments*, or with the GitHub CLI:

```sh
gh api -X PUT repos/OWNER/REPO/environments/issue-assistant -F "deployment_branch_policy[protected_branches]=false" -F "deployment_branch_policy[custom_branch_policies]=true"
gh api -X POST repos/OWNER/REPO/environments/issue-assistant/deployment-branch-policies -f name=main -f type=branch
gh secret set CLAUDE_CODE_OAUTH_TOKEN --env issue-assistant --repo OWNER/REPO
```

`install` runs from a git checkout of a release, whose commit and tag it pins. A checkout of another commit needs `--ref` with that commit and a `--version` that names no other one; a folder without git, such as an unpacked package of a release, needs both.

Without the secret, labels, reminders, closing and reopening keep working. The repository variable `ISSUE_ASSISTANT_AI` set to `off` switches the AI off.

### Turn on updates

Updates arrive as pull requests of Dependabot or Renovate that move the commit hash of the issue assistant in the workflows, and that is the whole update: the jobs are those of the release that the workflows pin. Only a release that changes a template needs the commands of step 1 again, and its release notes say so.

Dependabot moves them only once `.github/dependabot.yml` names the ecosystem `github-actions`:

```yaml
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
    cooldown:
      default-days: 7
```

The cooldown waits a week before a new release, as zizmor asks; security updates come at once. Renovate moves them by its defaults, once a configuration such as `renovate.json`, or the key `renovate` of `package.json`, is there. Without either, the repository keeps the release of its set-up and gets no fix, while the [security policy](https://github.com/Dennis-Otto/issue-assistant/blob/main/SECURITY.md) supports only the latest release. So `install` names the step, and `check` warns without failing: Renovate may also run from elsewhere, such as with a preset of the organization.

## Check that it works

1. **The check passes** on your computer and in the CI of the repository, as *Keep it checked* above adds it. It names every problem of the labels, the issue forms, the workflows, the firewall policy and the settings, every key that it doesn't know, and a `project.md` that still holds the starter's first line or says less than 80 characters. [Troubleshooting](troubleshooting.md) has the fix for each.
2. **The labels exist** once the set-up is merged into the default branch: the Labels workflow creates those of `.github/labels.toml`, and *Issues → Labels* lists them.
3. **A dry run shows the comment**: the summary of the job *Check and post the analysis* (`apply`) shows the labels and the comment that it would post, and writes nothing.
4. **A new issue gets its analysis** within a few minutes. Open it from an account that is not a maintainer's: issues of the owner, members and collaborators get the labels of their form, but no analysis.

When no comment comes, [troubleshooting](troubleshooting.md) has the usual causes, each with its fix. [What reporters see](comments.md) shows every comment that the assistant posts.

## Start with a backlog

Issues that were open before get a first analysis by the task [backlog](features/runs-by-hand.md#the-backlog).

<details markdown="1">
<summary>Three steps, for a maintainer</summary>

1. **See what it would do.** Under *Actions → Issue assistant → Run workflow*, choose the task `backlog` and enter `0` as the issue, or run `gh workflow run issue-assistant.yml -f mode=backlog -f issue=0`. As a dry run, it chooses the oldest open issues without a first analysis, up to `backlog_per_run` of `config.toml`, 20 by default; it runs the AI for each, so set it low for a first look. The summary of the job *Check and post the analysis* (`apply`) of each shows its labels, a possible duplicate and the missing information as a table.
2. **Set the labels.** Run it again from the same number with *dry_run* off. It sets the labels of each analysis and posts no comment on the old issues, unless you tick *comment*.
3. **Go on.** The summary of the job `assistant` names the last issue that it chose: enter that number as the issue of the next run, until it says that the backlog is done.

Each issue costs a first analysis, which counts toward no daily limit.

</details>

## Show that you use it

The footer of every comment of the AI already links the issue assistant, unless `[transparency] credit = false` in `.github/issue-assistant/config.toml` leaves that out. A badge in your README tells people before they open an issue, and helps others find the tool:

```markdown
[![issues: triaged by issue assistant](https://img.shields.io/badge/issues-triaged%20by%20issue%20assistant-blue)](https://dennis-otto.github.io/issue-assistant/)
```

## Remove the assistant

<details markdown="1">
<summary>Five steps, for a maintainer</summary>

Setting `ISSUE_ASSISTANT_AI` to `off` only turns the AI off, as [the FAQ](faq.md#how-do-i-turn-it-off) says. To remove the assistant from a repository, undo what the set-up did, in this order:

1. **Delete the workflows and the files that `install` wrote,** in one pull request: the five workflows in `.github/workflows/`, `issue-assistant.yml`, `issue-lifecycle.yml`, `labels.yml`, `findings.yml` and `discussion-assistant.yml`, which answers discussions with the engine too; the settings in `.github/issue-assistant/`, `.github/labels.toml`, `.github/findings.toml` and the firewall policy `.github/egress-firewall.yaml`; and the step of the check that [Keep it checked](#keep-it-checked) added to the CI, which would fail without them. `discussion-assistant.yml`, `.github/findings.toml` and the firewall policy exist only where the set-up wrote them, so the command skips what is missing. Once it is merged, nothing of the assistant runs any more.

    ```sh
    git rm -r --ignore-unmatch .github/workflows/issue-assistant.yml .github/workflows/issue-lifecycle.yml \
      .github/workflows/labels.yml .github/workflows/findings.yml \
      .github/workflows/discussion-assistant.yml .github/issue-assistant \
      .github/labels.toml .github/findings.toml .github/egress-firewall.yaml
    ```

2. **Delete the environment `issue-assistant`,** with the engine's secret in it, and the repository variables `ISSUE_ASSISTANT_AI` and `ISSUE_ASSISTANT_AI_RUNNER`, where you set them, under *Settings → Environments* and *Settings → Secrets and variables → Actions*, or with the GitHub CLI:

    ```sh
    gh api -X DELETE repos/OWNER/REPO/environments/issue-assistant
    gh variable delete ISSUE_ASSISTANT_AI
    gh variable delete ISSUE_ASSISTANT_AI_RUNNER
    ```

3. **Settle the issues that the assistant looked after.** Close the issue of the [digest](features/digest.md#the-digest-as-an-issue), if the sweep kept one. Nothing reminds the reporters of the open issues labeled `needs-info` any more, or closes those labeled `fixed-in-next-release` with the release: `gh issue list --label needs-info` and `gh issue list --label fixed-in-next-release` list them, for you to answer, close or relabel. The labels themselves stay, since the Labels workflow never deletes one; delete those you no longer want under *Issues → Labels*.
4. **Let merged pull requests close their issues again:** under *Settings → General → Issues*, tick **Auto-close issues with merged linked pull requests**, which step 3 of [the set-up](#set-up-a-repository) cleared.
5. **Stop telling reporters about the AI:** remove the section *What happens with your issue* from `SUPPORT.md`, or from the files of `[transparency]`, and the box *Automated analysis* from the issue forms, which nothing reads any more. The entry `/.issue-assistant/` of `.gitignore` can go as well. These are files, so they can go into the pull request of step 1.

</details>
