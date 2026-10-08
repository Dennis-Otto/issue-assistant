---
hide:
  - navigation
  - toc
---

# Issue assistant

A GitHub Action that looks after the issues of a repository: a first analysis of every new issue by an AI that only reads, labels, duplicates and reminders, all checked before anything is posted.

[Set up a repository](#quick-start){ .md-button .md-button--primary }
[Latest release](https://github.com/Dennis-Otto/issue-assistant/releases/latest){ .md-button }

![A comment of the issue assistant on an issue: labels, a summary, the likely cause with a link to the line of code, and what would help](https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/.github/social-preview.png)

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
