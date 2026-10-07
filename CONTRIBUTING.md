# Contributing

Contributions are welcome through issues and pull requests.
Participation follows [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), and project decision-making is described in [GOVERNANCE.md](GOVERNANCE.md).
Use [SUPPORT.md](SUPPORT.md) to choose the correct public support channel and [SECURITY.md](SECURITY.md) for private vulnerability reports.

All changes reach the protected `main` branch through pull requests that pass every required check.

## Requirements for changes

- Changes need tests. The script keeps 100 % line and branch coverage; `tests/test_templates.py` pins the security rules of the templates, and every rule that keeps the AI read-only or checks its answer has a test.
- The action runs on the Python 3.12 of GitHub's runners with the standard library alone. Use no third-party packages in `issue_assistant.py`.
- The prompts in `prompts/` and the comment texts in `TEXT` name no AI vendor; only `ENGINES` does. A test checks this.
- A change of a template is a change for every repository that uses the action: say in the pull request what repositories must do, such as run `install` again.

## Workflow

1. Open an issue first for anything larger than a small fix, so we can agree on the approach.
2. Create a branch from `main`, for example `feat/custom-reminder` or `fix/duplicate-notice`.
3. Commit with [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`, `test:`, `ci:`, `chore:`, `refactor:` or `perf:`, with an optional scope. Mark breaking changes with `!`.
4. Open a pull request with a Conventional Commit title. A workflow checks the title and sets the label that sorts the change into the release notes. Pull requests are squashed into one commit on `main`.

## Checks

```sh
python3.12 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements-dev.txt
.venv/bin/coverage run -m pytest && .venv/bin/coverage report
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
```

Update `requirements-dev.txt` with `pip-compile --generate-hashes --allow-unsafe requirements-dev.in`.

The cleaning of the AI's text has two more guards, which judge the result with an independent CommonMark parser (`tests/oracle.py`): property tests with Hypothesis in `tests/test_properties.py`, part of the test suite, and coverage-guided fuzzing with Atheris, which the Fuzzing workflow runs for a minute on every change and for fifteen minutes every week:

```sh
.venv/bin/pip install --require-hashes -r requirements-fuzz.txt
.venv/bin/python fuzz/fuzz_sanitize.py -max_total_time=60
```

A finding of either becomes an example in `test_tricky_text_is_cleaned_safely` in `tests/test_assistant.py` with its fix.

## Releases

A release is a pull request titled `chore: release x.y.z` that sets the version in `pyproject.toml` and adds a `## x.y.z` section at the top of `CHANGELOG.md`. When it is merged, the Release workflow tags `vx.y.z`, moves the major tag `vx`, and publishes the release with that section and the generated notes. Repositories that use the action receive it as a Dependabot pull request.

By contributing, you agree that your contribution is licensed under the [MIT license](LICENSE) of this project.
