# The templates live in templates/

- Status: accepted
- Date: 2026-10-13

## Context

[Decision 0006](0006-the-templates-exactly.md) makes every repository use the workflows exactly as the templates have them. `install` and `check` read the four workflows and the firewall policy from this repository's own `.github/workflows/` and `.github/egress-firewall.yaml`. Since this repository was made from the [repository blueprint](https://github.com/Dennis-Otto/repo-blueprint), those files belong to the blueprint: it renders them from its own copies with every update, and they are the templates of the release of the issue assistant that the blueprint pins.

The required check `issue-assistant` runs `check` of that pinned release, which compares the files with the templates of that release. So a pull request here that changed a template failed the required check, a pull request in the blueprint that changed its copy failed its own required check in the same way, and a change here alone would have been overwritten by the next update of the blueprint. No template could change.

## Options

1. Keep the templates in `.github/` and let the blueprint write these files only once. The blueprint would no longer bring them to any repository made from it, and a pull request here would still fail the check of the pinned release, which compares the changed files with its own templates.
2. Leave the templates out of the check of this repository. That switches off a check that keeps the AI read-only, for the repository whose templates every other repository copies.
3. Keep the templates in `templates/.github/`, which only this repository has and the blueprint doesn't write. This repository's own copies stay the blueprint's, a release behind, and a release reaches them through the blueprint like every other repository.

## Decision

Option 3: the four workflows and the firewall policy are in `templates/.github/`, next to the starter files, and `install` and `check` read them only there. No test compares this repository's own copies with `templates/`, because after a change of a template they lag a release behind. The tests pin the rules of the templates, and the Action test lints them with actionlint and zizmor and checks this repository's set-up with the templates of the commit installed.

## Consequences

This amends the consequences of decision 0006: a change of a template is a pull request that changes `templates/` alone. After its release, a pull request in the blueprint runs `install` of the release there and moves the pins of the action; the next release of the blueprint brings it to every repository made from it, this one too. Repositories that use the action without the blueprint run `install` again, as before. What `install` writes stays the same as before the move.

Renovate keeps the actions of the templates current, because its manager for GitHub Actions reads every `.github/workflows/` folder, `templates/.github/workflows/` too. Renovate never moves the pins of the action itself in this repository: those in the templates are placeholders that `install` replaces, and those in `.github/` belong to the blueprint, which moves them together with the templates of the release. A pin moved alone would fail the check of a release that changed a template.
