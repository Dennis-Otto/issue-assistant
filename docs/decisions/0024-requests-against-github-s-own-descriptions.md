# Every request asks for one version of GitHub's API and meets a reduced copy of its description

- Status: accepted
- Date: 2026-11-01

## Context

The script sends every REST request through `gh api` without `X-GitHub-Api-Version`, so its behavior follows whatever version GitHub serves by default, not the one that was tested. The GraphQL documents meet GitHub's schema in `tests/test_graphql_contract.py`, but the REST requests met only the fake in `tests/support.py`, which takes a misspelt parameter or a deprecated endpoint. The copy of the schema was refreshed by hand, although nothing about that is a decision while the documents still validate.

GitHub publishes an OpenAPI description of each version in `github/rest-api-description`. Its file for one version is 14 MB, mostly texts, examples and the schemas of answers. A file of the repository is part of what every repository that uses the action downloads on every run: gzipped, the whole file would add a sixth to that download.

## Options

1. The whole file. Simple to compare with GitHub's, but it costs every run of every repository, and a new example or a changed text would open a pull request that no request cares about.
2. Only the operations that the script uses. Small, but a misspelt path would have nothing to fail against, and a new request would need the copy changed by hand first.
3. Every operation, reduced to what a request can break: its path and method, whether it is deprecated, and its query parameters with their types and values. 180 KB, and a pull request that refreshes it shows what changed in the API.

## Decision

Option 3, written by `scripts/github_schemas.py`.

- **One constant.** `API_VERSION` is the header of every `gh api`, in the one method that starts it, and the name of the file that the copy comes from. The copy records its version, and a test fails when the two differ, so moving to another version also brings its description.
- **The source is read, and every test checks too.** `tests/test_rest_contract.py` reads every call from the script, so a request in a branch that no test takes is still checked, and a call that it can't read fails it. The fake and the stand-in of `gh` check what each test sends, which also covers the values that only a run knows.
- **The request bodies stay with the fake.** The fake already holds each write to the keys that GitHub reads, and the bodies of the description would bring back much of what the reduction leaves out.
- **A copy that still passes needs no person.** The weekly job opens or updates one pull request from `bot/github-schemas` with the release app, whose token starts the checks, and it merges itself once they pass. A request that no longer passes turns the job red instead and needs a person, who chooses what replaces it.

## Consequences

- A new REST request has to exist in GitHub's description, in the fake and in the source in a form that the test can read.
- Every repository sends the version from its next release of the action on. When GitHub ends `2022-11-28`, the weekly download of its description fails and names it, well before the requests stop working.
- `scripts/` and the workflows have calls of `gh api` of their own, which only maintainers run; they don't ask for a version yet.
- The commit of the bot goes through GitHub's API, which signs it, and always sits on the newest commit of `main`, so the required checks of the pull request never wait for an update of its branch.
