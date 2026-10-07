# Roadmap

[← README](../README.md) · [Architecture](architecture.md) · [Security design](security.md)

What the issue assistant intends to do in the next twelve months, until October 2027, and what it will not do. It is a direction, not a promise: the repositories that use it set the order. Ideas are welcome as [feature requests](https://github.com/Dennis-Otto/issue-assistant/issues/new?template=feature_request.yml).

## The next twelve months

- **The repositories that use it.** The assistant looks after the issues of [ha-autodarts](https://github.com/Dennis-Otto/ha-autodarts), [Paperless Unified Search](https://github.com/Dennis-Otto/paperless-unified-search), [Paperless Sync](https://github.com/Dennis-Otto/paperless-sync) and the [repository blueprint](https://github.com/Dennis-Otto/repo-blueprint). What their maintainers miss in practice comes first.
- **Changes of GitHub and of the engine.** New versions of GitHub's API, of the egress firewall and of the Claude Code action are taken up once the tests and the action's own run pass with them.
- **More guards against steered answers.** Every kind of text that gets past the cleaning becomes a test with its fix, as the property tests and the fuzzing found so far.
- **Fixes and security** come before anything new; [SECURITY.md](../SECURITY.md) has the times.

## Ideas

- **Further engines,** such as OpenAI Codex or the GitHub Copilot CLI, when a repository needs one. The [README](../README.md#engines) describes what an engine needs: a step that only reads, and the same checks of its answer.

## Not planned

- **An AI that writes.** The AI won't get a token that writes, run commands, change code or open pull requests; a job without the AI checks and writes every answer.
- **Decisions without the maintainer.** The assistant labels, asks, reminds and suggests duplicates; the maintainer reads every issue and has the last word.
- **Third-party packages in the action.** It keeps to the standard library of Python, `gh` and `git`.
- **Data outside GitHub.** The action keeps no database and no service of its own.
