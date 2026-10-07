The issue assistant is a GitHub Action for maintainers: an AI that only reads writes a first analysis of new issues, and checked code sets labels, handles duplicates and reminds or closes issues that wait for their reporter.

Where things are:

- `issue_assistant.py` is the whole script; `action.yml` runs it. `prompts/` holds the prompts of the AI's tasks, `templates/` the workflows, the firewall policy and the starter files a repository gets.
- `README.md` documents the set-up, the settings, the engines and the commands; `SECURITY.md` the security policy; `docs/architecture.md` the components and workflows, `docs/security.md` the security design and `docs/roadmap.md` the plans.
- `tests/` holds the tests; `tests/test_templates.py` pins the security rules of the templates.
- `CHANGELOG.md` lists the changes of every release.

What matters in a bug report: the release of the issue assistant, a link to the workflow run, the relevant lines of its log or summary, and the settings of the repository (`.github/labels.toml` and `.github/issue-assistant/config.toml`).
