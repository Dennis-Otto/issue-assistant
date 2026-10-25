# A patch version of an engine merges on its own once the check of its contracts passed

- Status: accepted
- Date: 2026-10-25

## Context

The engines are the AI: the Claude Code action, the Codex action and its command line, the Gemini CLI and the Copilot CLI. Each runs only in the job `analyze`, with its secret, which only `main` may use, so no check of a pull request runs one with a key. Until now every update of an engine waited for the maintainer: Renovate gave the engine's action a pull request of its own and the other engines one together, both without auto-merge.

The engines release often, and most of their releases are patch versions, such as the Claude Code action's 1.0.248. Each waited a week for its release age and then again for the maintainer, so the repositories that use the assistant got the fixes of the engines late, and a patch that the maintainer had no time for held back the next one too.

Since [the check *engine contracts*](../architecture.md#how-the-names-of-the-engines-are-checked) of the Action test, a pull request that changes an engine is checked without a key: the inputs and outputs of each engine's action, the flags of each command line in its help, the keys of the Gemini CLI's settings in its schema, that each one stops at the missing key alone, and for the lock files of `engines/` the signatures and attestations of npm's registry and that npm refuses another hash. That is what a patch version most often breaks: a renamed input, flag or key.

## Options

1. Keep every update of an engine waiting for the maintainer. Safe, but slow, and the maintainer's look at a patch is mostly the look at a green check.
2. Let every update of an engine merge on its own once its checks passed. A minor or major version changes behaviour on purpose, more than a check of names can see.
3. Let a patch version merge on its own once every required check passed, *engine contracts* among them, and keep a minor or major version and a new commit of a tag waiting for the maintainer.

## Decision

Option 3, as the maintainer decided on 2026-10-25. `.github/renovate.json5` has two rules for each group of engines: one for `patch` with auto-merge and a group of its own, *the engine's action to a new patch version* and *the other engines to a new patch version*, and one for every other kind of update, `!patch`, that waits. A patch comes in a pull request of its own, so a minor version of another engine never holds it back, nor does a patch take one along. Every update still waits a week after its release, as the blueprint's rules have it.

GitHub merges a pull request of Renovate once its required checks passed, not all of its checks, so *engine contracts* is now a required check, in `.github/repository.project.toml`. The job reports on every pull request: on one that changes no engine and nothing that the check reads, it passes without running.

## Consequences

A patch of an engine reaches `main` without a person looking at it. The push of a patch of the Claude Code action starts the Canary, which runs the analysis of a fixture issue with the new version and the secret, and a red Canary is seen: GitHub mails nobody when an app merged, but the bot-health bot looks at the latest run of every workflow each night and keeps an issue open for one that failed. The other engines have no such run: their patches rest on the check of their names alone. Engine updates are committed as `chore(deps)`, which makes no release of its own; they reach the repositories with the next release, and the note of each pull request asks the maintainer to look at the Canary, the release notes and the diff of `package-lock.json` before that release, and to revert an update that looks wrong. A release of dependency updates alone, `fix(deps)` and `chore(deps)`, merges itself once its checks passed, so such a release could bring a patch of an engine to the repositories before anybody looked; the release bot of the blueprint would have to wait for the Canary to close that gap.

What the check can't see stays a residual risk, as [the security design](../security.md#residual-risks) says: a name that keeps its meaning but changes its behaviour, an answer or a host that changes, and a new indirect dependency of a lock file. `tests/test_engine_contract.py` keeps the rules as this record describes them, reads them as Renovate does, and keeps *engine contracts* required and reporting on every pull request.
