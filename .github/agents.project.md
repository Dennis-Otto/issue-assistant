# Notes of this project for coding agents

Issue assistant: A GitHub Action that looks after issues: a first analysis by an AI that only reads, labels, duplicates and reminders, all checked before posting.

This file belongs to the project: the blueprint wrote it once, and its updates leave it alone. It adds to `AGENTS.md` what a coding agent needs to know about this project alone, such as its architecture, the commands of its own checks and what to watch for in its tests.

## Where things are

- The action is defined in `action.yml` and runs `issue_assistant.py`.
- `README.md` documents installation, configuration and use, `docs/` holds the pages of the website, and `CHANGELOG.md` lists the changes of every release.
- The tests are in `tests/`; `scripts/check.sh` runs every check.
- The Action test, `.github/workflows/action-test.yml`, runs the action as its users run it. Each of its jobs ends with the step *Explain the failure*, and each step of a job belongs to a part that the PR coach explains from `.github/checks.project.toml`; a new job, step or part comes with its explanation, which `tests/test_coach.py` checks.
- GitHub runs only its own actions and those that `[actions.selected]` of `.github/repository.toml` lists. A new action that only the workflows of this project run, such as an action of a new engine, goes into `patterns_allowed` of `[actions.selected]` in `.github/repository.project.toml`, in the same pull request; `tests/test_repository_settings.py` checks it, and the settings bot fails on `main` without it.
