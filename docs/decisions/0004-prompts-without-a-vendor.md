# Keep the prompts and texts independent of the AI vendor

- Status: accepted
- Date: 2026-10-07

## Context

AI services change fast, and a repository may prefer another engine than Claude.

## Options

1. Write the prompts and the workflow for one vendor.
2. Keep the prompts, the checks and every write independent of the AI, and name the vendor in one place, `ENGINES`.

## Decision

Option 2. Only one step of the `analyze` job and the table `ENGINES` know the engine; a test checks that the prompts in `prompts/` and the texts in `TEXT` name no vendor.

## Consequences

Another engine, such as OpenAI Codex or the GitHub Copilot CLI, needs an entry in `ENGINES` and a step, not new prompts. The notice to reporters names the engine that a repository uses.
