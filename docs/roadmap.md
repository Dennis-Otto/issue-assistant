# Roadmap

[← README](https://github.com/Dennis-Otto/issue-assistant) · [Architecture](architecture.md) · [Security design](security.md)

What the issue assistant intends to do in the next twelve months, until October 2027, and what it will not do. It is a direction, not a promise: the repositories that use it set the order. Ideas are welcome as [feature requests](https://github.com/Dennis-Otto/issue-assistant/issues/new?template=feature_request.yml).

## The next twelve months

- **The repositories that use it.** The assistant looks after the issues of [HomeDarts for Autodarts](https://github.com/Dennis-Otto/homedarts-for-autodarts), [Player Switch for Autodarts](https://github.com/Dennis-Otto/player-switch-for-autodarts), [Paperless Unified Search](https://github.com/Dennis-Otto/paperless-unified-search), [Paperless Sync](https://github.com/Dennis-Otto/paperless-sync), the [repository blueprint](https://github.com/Dennis-Otto/repo-blueprint), the website [dennis-otto.github.io](https://github.com/Dennis-Otto/dennis-otto.github.io) and its own. What their maintainers miss in practice comes first.
- **The GitHub Marketplace, next.** The action isn't listed yet. Its `action.yml` already has the name, the description and the icon of a listing as *Read-only issue assistant*, since another action has the name Issue Assistant. GitHub lists an action only from the form of a release on its website, with *Publish this Action to the GitHub Marketplace* ticked, the agreement of the Marketplace accepted and a second factor of sign-in, so Dennis Otto publishes the listing by hand. Then maintainers find the action where they look for actions.
- **Changes of GitHub and of the engine.** New versions of GitHub's API, of the egress firewall and of the Claude Code action are taken up once the tests and the action's own run pass with them.
- **More guards against steered answers.** Every kind of text that gets past the cleaning becomes a test with its fix, as the property tests and the fuzzing found so far.
- **Fixes and security** come before anything new; [SECURITY.md](https://github.com/Dennis-Otto/issue-assistant/blob/main/SECURITY.md) has the times.

## Ideas

- **A tier without a secret.** GitHub Models let a workflow call a model with its own token, until GitHub retired it on 2026-07-30. If GitHub again offers models to the token of a workflow, a lighter analysis of labels, missing information and duplicates could run with no secret at all.
- **Speakers to check the catalogs of the languages, and more of them** as the reporters of the repositories that use the assistant need them: of the fourteen catalogs, only the English and the German ones are written by hand, and the twelve others, from French to Korean, are machine drafts that a speaker of each language should check. A reporter in a language without a catalog already gets the analysis in their language, with the words of the assistant in English; the [README](https://github.com/Dennis-Otto/issue-assistant#languages) has how to add one.
- **Another engine,** when a repository needs one. The [README](https://github.com/Dennis-Otto/issue-assistant#add-an-engine) describes what an engine needs: a step that only reads, and the same checks of its answer.

## Not planned

- **An AI that writes.** The AI won't get a token that writes, run commands, change code or open pull requests; a job without the AI checks and writes every answer.
- **Decisions without the maintainer.** The assistant labels, asks, reminds and suggests duplicates; the maintainer reads every issue and has the last word.
- **Third-party packages in the action.** It keeps to the standard library of Python, `gh` and `git`.
- **Data outside GitHub.** The action keeps no database and no service of its own.
