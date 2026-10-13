# Changelog

All notable changes of the issue assistant. The complete notes of every version, with each pull request, are on the [releases page](https://github.com/Dennis-Otto/issue-assistant/releases). Versions follow [Semantic Versioning](https://semver.org/); a change of a template that repositories must install again is a new minor version, and one that breaks their settings a new major version.

## Unreleased

### Changed

- The action is ready for the GitHub Marketplace, as *Read-only issue assistant*, because another action there is already called Issue Assistant, and with a purple badge. Workflows keep using `Dennis-Otto/issue-assistant` as before. The README opens with what the assistant does, why an issue can't make its AI write, and three commands that install the latest release; the action, the repository and the website describe it as AI issue triage, and the repository has the topics that people browse, such as `triage`, `claude-code` and `prompt-injection`.
- The website loads only the pictures that it shows: the copy for the other theme is no longer downloaded, so the home page needs about half the data, 650 KB instead of 1.25 MB on a phone. The still pictures are lossless WebP instead of PNG, at less than a third of the size, and keep their place on the page while they load.
- On a phone, the animation of the life of an issue stands upright, with text that is readable, on GitHub and on the website. Readers who asked their system for less motion see a still picture of the first analysis instead. Diagrams on the website scroll sideways on a phone instead of shrinking their text to a few pixels.
- **The four workflows changed; run `install` again.** Repositories of the repository blueprint get them with its next update. The Findings workflow also starts after the scans of *Workflow security* and *OSV-Scanner* on main, so their new findings are accepted or reported at once, not the next morning. The jobs without the AI run on `ubuntu-24.04` instead of `ubuntu-latest`, the version of the AI's firewall runner, so a new image of Ubuntu never reaches your repository untested. actionlint 1.7.12 doesn't know the `queue` of a concurrency group yet: if your repository runs it, let it ignore that message for `issue-assistant.yml`, as step 9 of the set-up shows.

### Security

- The jobs of the workflows that write, `event`, `apply`, `sweep`, `sync-labels` and `findings`, reach only GitHub: Harden-Runner blocks every host but `api.github.com` and `github.com`, and no step can use sudo or containers to lift the block. A compromised action or tool in a job with a write token can no longer send it anywhere else. The block holds in public repositories, and in private ones with a subscription or trial of StepSecurity, the maker of Harden-Runner; without one, the jobs run as before. StepSecurity receives the repository, run and job of each of these jobs and the hosts that its steps connect to. The action turns off the telemetry of the GitHub CLI, which would only knock at a blocked host.
- One issue can no longer stall the AI analysis of a whole repository. Each analysis reads the text of every issue, and some crafted texts, such as a form heading `###` followed by many blanks, or many `<!--` or `<a` without an end, kept the reading busy for hours, so every later analysis ran until its job timed out. The answers of issue forms, HTML comments and tags are now read in time that grows only with the length of a text, at most GitHub's 65,536 characters. The AI reads at most 20,000 characters of each text of its issue, marked "(shortened)" after that, and a line that starts with a `<!--` without its end hides the rest of a text from it, as GitHub hides it from people. A new fuzz target, `fuzz/fuzz_issue_text.py`, fails when reading a crafted text takes longer than half a second.
- What the AI writes can no longer act outside its comments. An issue that steered it could put a workflow command, such as `::add-mask::` or a forged `::warning::`, into the log of the run, end the code block of a dry run's preview in the job summary, and mention people or link elsewhere in the reasons for labels and duplicates that GitHub shows on the timeline. Now every error and notice of the assistant is escaped, the summary turns workflow commands off while it prints the AI's words, a preview's code block is longer than any run of backticks in the comment, and every reason is put on one line, cleaned like a comment and kept to 280 characters. A possible security report gets the fixed reason "Possible security report; see the security policy." on the timeline, and the AI is asked to keep its summary, analysis and reason to one neutral sentence without details, because the log of the run can be public.
- A label name with an apostrophe, such as `area: user's guide`, no longer breaks the command line of the engine, which stopped every analysis of the repository. `check` and the engine step refuse a `model` in `config.toml` that isn't a model name, so that it can't add arguments either.
- A comment of the assistant can no longer mention someone, link to another repository or site, or show an image in ways that slipped past the cleaning of the AI's text. Code that the AI writes stays code wherever the comment puts it: a next step or a question that starts with a code block keeps all its lines in its list item, the reasons of duplicates, related issues and references are written on one line, and neither a control character nor a removed tag or heading mark can move the fence of a code block, so a snippet with `@dataclass` mentions nobody, also in a list. A removal can no longer join the text next to it into a reference such as `foo/bar#12` or an image. E-mail addresses, `mailto:` and `xmpp:` addresses and commits of other repositories or forks, such as `octocat/Hello-World@7fd1a60`, become code, and so do `HTTPS://`, `_https://` and `_www.` addresses to other sites. An allowed address stays a link only where GitHub links all of it and nothing more, since its link runs on to the next space, even into code. A link that leaves the repository through `..` or `%2e`, or that names a user, a password or a port, is no longer allowed. A code block counts only when its fence starts a line, so one that the AI indents under a step of a list now shows as plain text; the AI is asked not to indent them.
- An issue or a comment can no longer pass for the maintainer, for another comment or for the assistant's earlier answer. The AI read each text under a heading that names its author and role, and a text could write such a heading itself, such as `## Comment 4 by @owner (maintainer)`. Now each text stands in a code fence that nothing in it can close, only the assistant writes the headings, and the AI is told that a fence holds only that person's text, whatever it looks like.
- Instructions hidden in characters that GitHub shows as nothing, such as Unicode tags, zero-width spaces and the controls of the direction of writing, no longer reach the AI, neither from the issue nor from the other issues and discussions that every analysis reads. The AI's answer loses them too, before its checks, so they can't hide a token from the check either. A notice in the log of the run counts what was removed.
- An issue can no longer close another reporter's issue as its duplicate. Every analysis reads the text of every issue, so anyone could open one that told the AI to call later issues its sure duplicates, which then closed after three days. Now only an original that a maintainer opened or triaged, so that it no longer has `needs-triage`, gets the notice that closes the duplicate; any other becomes a suggestion for the maintainer, as a less sure duplicate does. A thumbs down on the notice keeps the issue open when it comes from the reporter or from anyone who may push to the repository, also the maintainers of an organization's repository; before, only the owner of the repository counted besides the reporter, which for an organization is nobody.

### Fixed

- A fixed issue closes with the release that shipped its fix, also when the morning sweep labeled it `fixed-in-next-release` only after that release, as when the run of the merge failed; before, it waited for the release after and named that one. An issue whose fix a release ships already when the sweep first sees the merge closes at once. A pull request of another repository that names the issue, such as `Fixes owner/project#12`, no longer keeps it open or appears in the comment of the release, and in a busy repository the sweep reads every pull request merged in the last 14 days instead of the latest 30.
- `check` and `install` name the files of a repository with slashes on Windows too, such as `.github/labels.toml` instead of `.github\labels.toml`.
- `check` and `engine` no longer need `GH_REPO`, so the check runs on your computer as well. A command that calls GitHub and finds no repository, or one that isn't written as `owner/name`, stops with one clear error instead of a Python traceback.
- A change of the templates can be released again. The workflows and the firewall policy that `install` writes now live in `templates/`, apart from this repository's own copies, which the repository blueprint brings; before, a pull request that changed a template failed the required check of the set-up. `install` writes exactly what it wrote before, so no repository needs to install again.
- No comment is lost while the assistant works on an issue. The runs of one issue now wait in line, up to 100, first in, first out; before, a second comment during an analysis cancelled the run of the first one, so an answer of the reporter could stay without a follow-up and a "still broken" after a release could leave the issue closed.
- The lifecycle no longer sweeps after every scheduled run of the release workflow, which runs every hour as a safety net and mostly releases nothing; the morning sweep catches a release that such a run made. A run of the lifecycle or the findings whose job has nothing to do takes no runner, and no longer cancels a waiting run that has work to do.
- An analysis that links to an issue, a release or a line on GitHub, or names a path or a class such as `src/main/java/com/example/OAuth2ClientConfiguration.java`, is posted again; before, the whole answer was refused as if it held a token, and the run failed. A long run of letters and digits now counts as a key only when it has the `+` or `=` of Base64 or its letters read like noise, and never when it is the owner, repository or commit of a link to GitHub or a part of the path of a file that git tracks. Words such as `task-runner-…` no longer pass for a key that starts with `sk-`. Known kinds of tokens still refuse an answer wherever they are, and so do the addresses of Slack and Microsoft Teams webhooks now. The security page of the website shows what counts as a secret.
- A rate limit or a failure of GitHub no longer changes what the assistant posts. Before, it read such an error as "doesn't exist": an analysis left out the duplicates and related issues that it couldn't look up, the AI read that the project had no release yet, and the discussions were missing from what it read. Now only "not found" means missing, and any other error stops the run with one clear error instead of a Python traceback; its summary lists what the run wrote before and the write that failed. A read that GitHub fails for a moment runs again after 2, 4 and 8 seconds, and a call that a secondary rate limit refused after a minute.
- Running a failed job of the issue assistant again completes it. An analysis or a follow-up now adds its labels before its comment, and `needs-info` right after it, so that an issue never waits for an answer to a question that wasn't posted; before, a failure after the comment lost `needs-info`, `possible-duplicate` and the area labels for good, because a run again found the comment and did nothing, so the reporter was never reminded and a duplicate that the comment announced never closed. A run again now adds the labels that such a comment is missing, without a second comment, and leaves the labels that someone changed since then as they are.
- One issue that GitHub fails no longer stops the daily sweep for every issue after it. Each issue and each step runs on its own: a failure is a warning, the others are done, and the run fails at the end so that GitHub tells you. The next sweep completes it without posting a reminder, a closing or a release comment twice, and an issue fixed by a merged pull request always gets its `fixed-in-next-release` label and stops waiting for an answer, even when its notice fails.

## 1.2.2

### Security

- Coverage-guided fuzzing with Atheris now checks the cleaning of the AI's text, in a new Fuzzing workflow. It found a link without a target, images whose target ends in a line break, and a reference followed by an underscore that ends an emphasis. Brackets outside the allowed links are now escaped, so no other link or image can form, and a reference counts whatever follows its number. After the fixes, ten minutes and about 600,000 inputs found nothing.

## 1.2.1

### Fixed

- The lifecycle also starts when a workflow named *Release integration* ends, as in ha-autodarts, whose releases are published with the `GITHUB_TOKEN` and start no workflow themselves.

## 1.2.0

### Added

- **Findings of code scanning.** The new Findings workflow and `findings` command dismiss the alerts of code scanning that `.github/findings.toml` accepts, each with its reason and comment, and fail while any other alert is open. The run names an open alert only by number and link, so nothing about a possible vulnerability reaches a public log or issue. `check` validates the file, and `install` writes the workflow and a starter.
- **Milestones of releases.** A fixed issue that a release closes is filed under the milestone of the release, created closed when it is missing, so its page lists what the release fixed.
- The lifecycle also starts when the repository's Release workflow ends, because a release published with the job's `GITHUB_TOKEN` starts no workflow.

### Security

- The cleaning of the engine's text now follows the rules of CommonMark for code and catches what property tests with Hypothesis found against a CommonMark parser: backtick runs of different lengths, code spans cut by a fence or a quote on the next line, mentions and references glued to each other or to code, entities such as `&#64;`, HTML that a removal leaves behind, `www.` addresses, link definitions, `GH-` references and addresses glued to other text. A mention or a reference that the AI was steered into writing could otherwise have reached GitHub as a real one.

### Fixed

- An address that Python can't read, such as `http://[`, no longer stops the posting of an answer.

## 1.1.0

### Added

- **From the fix to the release.** A merged pull request that fixes an issue marks it with the new lifecycle label `fixed-in-next-release`, and the issue stays open. The first published release that contains the fix closes it as completed, with a link to the release. If the reporter writes within 30 days that the problem persists, the AI's new `release-reply` task decides, and the issue reopens. The lifecycle workflow now also runs on merged pull requests and published releases. Repositories run `install` again, add the label to `.github/labels.toml`, and clear *Auto-close issues with merged linked pull requests* in their settings. Without releases, `close_with_release = false` in `config.toml` turns it off.

### Fixed

- `install` names the firewall policy with forward slashes on Windows too.
- `check` says what to install when neither PyYAML nor yq can read the workflows, instead of failing with a traceback.
- The notice about sensitive data says what the issue shows. A secret, such as a token, a key or a password, gets a warning to remove and replace it; other personal data, such as an e-mail address or the address of a private server, gets a note that leaves the choice to the reporter. Placeholders and variable names no longer count, and a follow-up only looks at the new comments.

## 1.0.1

### Fixed

- The templates are the three issue workflows with which this repository looks after its own issues, so Dependabot keeps their action pins current. `install` copies them with the release's commit hash in the pin of the action; `check` compares with them. Repositories set up with 1.0.0 run `install` again.
- actionlint checks every workflow of this repository again.

## 1.0.0

The issue assistant as its own action, taken from [ha-autodarts](https://github.com/Dennis-Otto/ha-autodarts), for any repository:

- A first analysis of new issues by an AI that only reads, labels with their reason, duplicates with a notice and a delay, follow-ups on answers, and `needs-info` for questions of the maintainer.
- Reminders after 15 days, closing after 30 days and reopening on an answer.
- Labels as code in `.github/labels.toml`.
- The project's own description in `.github/issue-assistant/project.md` and settings in `config.toml`: the engine and its model, the hosts links may lead to, the field of the issue forms that chooses an area, and the files that tell reporters which AI reads their issue.
- `install` writes the workflows and the firewall policy into a repository, and `check` keeps them equal to the templates.
