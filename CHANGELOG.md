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

### Fixed

- `check` and `install` name the files of a repository with slashes on Windows too, such as `.github/labels.toml` instead of `.github\labels.toml`.
- `check` and `engine` no longer need `GH_REPO`, so the check runs on your computer as well. A command that calls GitHub and finds no repository, or one that isn't written as `owner/name`, stops with one clear error instead of a Python traceback.
- A change of the templates can be released again. The workflows and the firewall policy that `install` writes now live in `templates/`, apart from this repository's own copies, which the repository blueprint brings; before, a pull request that changed a template failed the required check of the set-up. `install` writes exactly what it wrote before, so no repository needs to install again.
- No comment is lost while the assistant works on an issue. The runs of one issue now wait in line, up to 100, first in, first out; before, a second comment during an analysis cancelled the run of the first one, so an answer of the reporter could stay without a follow-up and a "still broken" after a release could leave the issue closed.
- The lifecycle no longer sweeps after every scheduled run of the release workflow, which runs every hour as a safety net and mostly releases nothing; the morning sweep catches a release that such a run made. A run of the lifecycle or the findings whose job has nothing to do takes no runner, and no longer cancels a waiting run that has work to do.

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
