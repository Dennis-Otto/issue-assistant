# Notes of this project for coding agents

Issue assistant: A GitHub Action that looks after issues: a first analysis by an AI that only reads, labels, duplicates and reminders, all checked before posting.

This file belongs to the project: the blueprint wrote it once, and its updates leave it alone. It adds to `AGENTS.md` what a coding agent needs to know about this project alone, such as its architecture, the commands of its own checks and what to watch for in its tests.

## Where things are

- The action is defined in `action.yml` and runs `issue_assistant.py`.
- `README.md` documents installation, configuration and use, `docs/` holds the pages of the website, and `CHANGELOG.md` lists the changes of every release.
- The tests are in `tests/`; `scripts/check.sh` runs every check.
