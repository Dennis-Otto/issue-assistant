# The title of an engine update names the engine and its versions, and each package of an engine comes in a pull request of its own

- Status: accepted
- Date: 2026-11-13

## Context

Since [decision 0030](0030-every-update-of-an-engine-merges-on-its-own-and-the-release-waits-for-the-canary.md), every update of an engine merges on its own and makes a patch release, which every repository that uses the workflows gets as a move of its pin. A bot writes no fragment of `changelog.d/`, so the release takes the title of Renovate's pull request as its entry of the changelog and its line of the notes. The release v1.10.6 consisted of one such line, "update the other engines", for #311, which moved the Gemini CLI from 0.65.0 to 0.66.0 and Codex from 0.164.0 to 0.166.0; its notes named neither (#361).

Renovate puts the versions into a title through `commitMessageExtra`, and does so only for a branch that holds one package at one version: for a group of several packages, or of several versions, it uses the name of the group and drops `commitMessageExtra`. Decision 0030 gave Codex one group for its action, `openai/codex-action`, and its command line, `@openai/codex`, so an update of both would again be titled "update Codex", with no version.

## Options

- **A fragment for each pull request of an engine, written by a workflow.** It would read like its neighbours, but a push of another author to Renovate's branch makes Renovate leave the branch alone, and the fix bot of the blueprint writes no fragment for a bot on purpose.
- **A title of the group that lists the versions.** Renovate compiles the title of a group from the settings of a single update, so a template can't name the versions of both packages.
- **A group, and so a pull request, for each package of an engine, with a title that names the engine and both versions.** The title of every update then reads like "update the Gemini CLI to 0.66.0 (was 0.65.0)".

## Decision

- Each package of an engine has a group of its own in `.github/renovate.json5`: *the engine's action*, *the Codex action*, *the Codex CLI*, *the Gemini CLI* and *the Copilot CLI*. The action of Codex and its command line no longer move together: the workflows pass the version of the command line to the action as `codex-version`, so neither waits for the other, and the check *engine contracts* checks each of them.
- The rule of the engines sets `commitMessageTopic` to the name of the engine, such as "the Codex CLI", and `commitMessageExtra` to the new version and the one before, "to 0.166.0 (was 0.164.0)"; a new commit of a tag keeps its version, and the title names the commit instead. `commitMessageLowerCase` is `never`, so that Renovate keeps the names as they are, with the verb `update` in lower case as in every other title.
- `tests/test_engine_contract.py` keeps a group of one package for each engine and these settings, and fails on an entry `* **deps:**` of `CHANGELOG.md` that names no version. The entry of v1.10.6 names the versions of #311.

This amends decision 0030, which grouped the action of Codex with its command line; everything else of it holds.

## Consequences

- The notes of every release of an update of a command line of an engine (Codex, Gemini, Copilot), on the releases page, the Marketplace and in the pull requests that move the pins, say which version of which engine the analyses run from now on. An update of the action of Claude Code or of Codex is a `chore(deps)` of the workflows and makes no release; the title of its pull request names the versions as well.
- An update of Codex that moves its action and its command line at once comes as two pull requests, each with its own checks, so a failing one holds back neither the other nor any other engine.
- An update of an engine that Renovate brings with another package in one branch, which no rule of the repository makes, would again have a title without a version; the test of the changelog then fails on the pull request of that release, until its entry names the version.
