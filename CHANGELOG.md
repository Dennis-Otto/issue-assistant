# Changelog

All notable changes of the issue assistant. The complete notes of every version, with each pull request, are on the [releases page](https://github.com/Dennis-Otto/issue-assistant/releases). Versions follow [Semantic Versioning](https://semver.org/); a change of a template that repositories must install again is a new minor version, and one that breaks their settings a new major version.

## Unreleased

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
