# Changelog

All notable changes of the issue assistant. The complete notes of every version, with each pull request, are on the [releases page](https://github.com/Dennis-Otto/issue-assistant/releases). Versions follow [Semantic Versioning](https://semver.org/); a change of a template that repositories must install again is a new minor version, and one that breaks their settings a new major version.

## Unreleased

### Fixed

- `install` names the firewall policy with forward slashes on Windows too.
- `check` says what to install when neither PyYAML nor yq can read the workflows, instead of failing with a traceback.

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
