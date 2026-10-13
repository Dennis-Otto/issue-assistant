---
hide:
  - navigation
  - toc
---

# Issue assistant

**Safe AI issue triage for GitHub.** A read-only AI writes a first analysis of every new issue, and a job without AI checks it before anything is posted. Labels, duplicates, reminders and the closing of fixed issues with the release come with it.

[Set up a repository](#quick-start){ .md-button .md-button--primary }
[Latest release](https://github.com/Dennis-Otto/issue-assistant/releases/latest){ .md-button }

<picture>
  <source media="(prefers-reduced-motion: reduce) and (max-width: 600px) and (prefers-color-scheme: dark)" srcset="images/hero-mobile-still-dark.webp">
  <source media="(prefers-reduced-motion: reduce) and (max-width: 600px)" srcset="images/hero-mobile-still-light.webp">
  <source media="(prefers-reduced-motion: reduce) and (prefers-color-scheme: dark)" srcset="images/hero-still-dark.webp">
  <source media="(prefers-reduced-motion: reduce)" srcset="images/hero-still-light.webp">
  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="images/hero-mobile-dark.webp">
  <source media="(max-width: 600px)" srcset="images/hero-mobile-light.webp">
  <source media="(prefers-color-scheme: dark)" srcset="images/hero-dark.webp">
  <img src="images/hero-light.webp" alt="Animation: the life of an issue in a demo project. A reporter opens an issue with the bug form; the event job adds the labels area: dashboard and needs-triage; the issue assistant posts a first analysis with the likely cause in the code and the information still needed, and adds connectivity and needs-info; the reporter answers and needs-info goes; a merged pull request marks the issue fixed-in-next-release; the release v1.4.0 closes it with a link to the release">
</picture>

## What it does

<div class="grid cards" markdown>

- :material-robot-outline:{ .lg .middle } **A first analysis of every new issue**

    ---

    An AI that only reads writes a summary, the likely cause with links to the code and the documentation involved, what the reporter can try and what is still missing, in English or German.

- :material-label-multiple-outline:{ .lg .middle } **Labels**

    ---

    Labels from the issue forms and from the analysis, each with its reason. The labels themselves are code, in `.github/labels.toml`.

- :material-content-duplicate:{ .lg .middle } **Duplicates**

    ---

    A sure duplicate gets a notice and closes three days later, linked to the original, unless someone objects. Less sure ones become suggestions for the maintainer.

- :material-clock-alert-outline:{ .lg .middle } **Unanswered questions**

    ---

    Open questions mark an issue `needs-info`. After 15 days the reporter gets a reminder, after 30 days the issue closes, and an answer reopens it.

- :material-tag-check-outline:{ .lg .middle } **From the fix to the release**

    ---

    A merged pull request with `Fixes #12` marks the issue `fixed-in-next-release`. The issue closes with a link to the release that ships the fix, and reopens if the problem persists.

- :material-shield-search:{ .lg .middle } **Findings of code scanning**

    ---

    The findings that `.github/findings.toml` accepts are dismissed with their reason. Any other open finding fails a daily run, without a public issue.

</div>

The maintainer reads every issue and has the last word.

## Quick start

--8<-- "README.md:quick-start"

The [guide](guide.md) describes every step, the settings and the commands in detail.

## Learn more

<div class="grid cards" markdown>

- :material-book-open-variant:{ .lg .middle } **Guide**

    ---

    How the assistant works, how it keeps the AI in check, and its settings, engines and commands.

    [:octicons-arrow-right-24: Read the guide](guide.md)

- :material-sitemap-outline:{ .lg .middle } **Architecture**

    ---

    The action, its commands and the four workflows that call it in a repository.

    [:octicons-arrow-right-24: Architecture](architecture.md)

- :material-shield-lock-outline:{ .lg .middle } **Security design**

    ---

    What the assistant protects, what it trusts, the threats with their countermeasures, and the risks that remain.

    [:octicons-arrow-right-24: Security design](security.md)

- :material-map-marker-path:{ .lg .middle } **Roadmap**

    ---

    What the assistant intends to do in the next twelve months, and what it will not do.

    [:octicons-arrow-right-24: Roadmap](roadmap.md)

- :material-scale-balance:{ .lg .middle } **Decisions**

    ---

    The decisions that shape the project, each with its reasons.

    [:octicons-arrow-right-24: Decisions](decisions/README.md)

- :material-history:{ .lg .middle } **Releases and changelog**

    ---

    Every release with its notes on GitHub, and the changes of each version.

    [:octicons-arrow-right-24: Releases](https://github.com/Dennis-Otto/issue-assistant/releases) · [Changelog](changelog.md)

</div>
