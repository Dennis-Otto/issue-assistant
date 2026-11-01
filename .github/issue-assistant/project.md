The issue assistant is a GitHub Action for maintainers: an AI that only reads writes a first analysis of new issues, and checked code sets labels, handles duplicates and reminds or closes issues that wait for their reporter.

Where things are:

- `issue_assistant/` is the whole program, a package of the standard library alone; `action.yml` runs it, and `docs/architecture/modules.md` has the map of its modules. `prompts/` holds the prompts of the AI's tasks, `templates/` the workflows, the firewall policy and the starter files a repository gets. This repository's own copies of those workflows and of the firewall policy in `.github/` come from the repository blueprint, as the templates of the release that it pins; a change of a template changes `templates/`. `locales/` holds the texts of the comments, one catalog per language (`en.toml`, `de.toml`, …), which `tests/test_assistant.py` holds to English.
- `README.md` gives the overview; `docs/getting-started.md` documents the set-up, `docs/configuration.md` and `docs/every-key.md` the settings, `docs/engines.md` the engines, `docs/reference.md` the commands, `docs/faq.md` and `docs/troubleshooting.md` the questions and problems, and `docs/features/` each feature; `SECURITY.md` the security policy; `docs/architecture.md` and `docs/architecture/` the components and workflows, `docs/security.md` and `docs/security/` the security design, and `docs/roadmap.md` the plans.
- `tests/` holds the tests; `tests/test_templates.py` pins the security rules of the templates.
- `CHANGELOG.md` lists the changes of every release.

What matters in a bug report: the release of the issue assistant, a link to the workflow run, the relevant lines of its log or summary, and the settings of the repository (`.github/labels.toml` and `.github/issue-assistant/config.toml`).
