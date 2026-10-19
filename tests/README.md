# The tests

`bash scripts/check.sh` runs them with every other check, and `.venv/bin/python -m pytest` runs them alone. They need no network and no `gh`: GitHub is a fake, and so is `gh`. `CONTRIBUTING.md` says what a change needs; this page says how the tests stand in for GitHub, and how to work with the golden files.

| File | What it holds |
| --- | --- |
| `support.py` | Builders of issues, comments and answers, the fake GitHub, the fake `gh` and `assert_golden` |
| `fake_gh.py` | The program that the tests of the class `GitHub` start in place of `gh` |
| `golden/` | What the assistant writes, word for word: comments, contexts, plans and job summaries |
| `test_golden.py` | The tests that compare those texts with their golden files |
| `test_assistant.py` | The events, the sweeps, the answers of the AI and the commands of the script |
| `test_discussions.py` | The first answers in Discussions: the event, the limits, the context, the checks of the answer and the set-up |
| `test_graphql_contract.py` | Every GraphQL document against the copy of GitHub's schema in `github/` |
| `test_properties.py`, `test_lifecycle_properties.py` | Property tests of the cleaning, of whole comments and of the lifecycle |
| `oracle.py`, `test_oracle.py` | The Markdown parser that judges the cleaning, held to what GitHub's renderer does |
| `test_findings.py` | The findings of code scanning |
| `test_setup.py`, `test_templates.py` | `install`, `check`, the settings and the workflow templates |
| `test_engines.py` | The engines next to Claude: their schemas, rules and firewall policies, and the command lines that `analyze` runs |
| `test_evaluate.py` | `evaluate`: the choice of the closed issues, GitHub as it was when each was opened, the replays with a fake engine, the precision and recall and the report |
| `test_docs.py` | The website and the README |
| `test_coach.py` | The explanations of the checks of this project, which the PR coach shows when a job of the Action test fails |
| `test_repository_settings.py` | The settings as code of this repository: every action that a workflow runs is one that `[actions.selected]` allows |
| `conftest.py`, `sample/` | The sample repository with the assistant installed, which most tests read |
| `fixtures/` | The issue that the canary analyzes |

## The fake GitHub

`FakeGitHub` in `support.py` answers the REST and GraphQL calls of the script from what a test puts into its stores, such as `comment_store[12]` for the comments of issue #12. The rest of the class `GitHub` runs as it is, so a test reaches the same requests as a run on GitHub. The fake is as strict as GitHub where a mistake would show only there:

- **Lists come in pages.** A list of the REST API has pages of 30 items, or of up to 100 that the path asks for with `per_page`. A reader that pages, with `pages=True` as `gh api --paginate` does, gets every item; one that doesn't gets the page that the path names, the first unless it names another. `make_comments(150)` builds a thread longer than a page, and `test_every_list_is_read_to_its_last_page` holds each reader to the last one.
- **A write has the body that GitHub reads.** A body without a key that GitHub needs, with one that it ignores, or with a reaction, a colour or a reason for closing that it doesn't know fails the test.
- **A failure is one call away.** `github.fail("^POST issues/12/comments")` lets every matching call fail, with a rate limit or with the error that it is given.

`github.calls` lists every call, and `github.writes_made()` those that change something.

## The fake gh

The tests of the class `GitHub` itself check the commands that it builds. `fake_gh(monkeypatch, tmp_path, *answers)` lets Python's real `subprocess.run` start `fake_gh.py` with the options of the script, in place of `gh`. Each answer is a dict with its `stdout`, `stderr` and exit `status`; the last one repeats. The call returns the list of the calls so far, each as the whole command and the text that `gh` read from its input, which it reads only for `--input -`:

```python
calls = fake_gh(
    monkeypatch, tmp_path, {"stdout": '{"number": 1}', "stderr": "", "status": 0}
)
assistant.GitHub("owner/project").issue(1)
assert calls == [(["gh", "api", "repos/owner/project/issues/1"], None)]
```

The tests start the stand-in with their own Python, never through the `PATH`, so they can't reach the `gh` of the computer that runs them, and on Windows no `cmd.exe` reads the `&` of a query as the end of a command.

## Golden files

`assert_golden(name, text)` compares a text with the file `golden/<name>`, which holds the text and one line break more, so that it ends with one as every text file of the repository does. A change of a comment, of the context of a task, of the plan of an event or of a job summary fails until its golden file shows it. When the change is meant, write the files anew and read their diff, which is what readers see differently:

```sh
UPDATE_GOLDEN=1 .venv/bin/python -m pytest tests/test_golden.py
git diff tests/golden/
```

The names end in `.txt`, so that the checks of Markdown and of links leave the texts alone: a comment links the demo project of the website, which doesn't exist. `UPDATE_GOLDEN` refuses to run in CI, where it would let every text pass, and `test_every_golden_file_has_its_test` fails for a file that no test compares any more, such as one of a comment that was renamed.

The comments come from the examples of the page *What reporters see* of the website, in `scripts/mkdocs_hooks.py`, so a new kind of comment gets its golden files with its example.
