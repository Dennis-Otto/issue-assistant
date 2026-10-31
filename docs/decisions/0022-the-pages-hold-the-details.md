# The pages of docs/ hold the details, and the README links them

- Status: accepted
- Date: 2026-10-31

## Context

The README was the single source of the documentation: a page of the website included its parts, so that nothing was written twice. It grew to 139 KB, with 29 pictures and paragraphs of 1,000 characters. In the GitHub app, where the maintainer reads it, setting up the assistant meant scrolling past the security, every setting, the engines, the commands, the FAQ and the troubleshooting. The website inherited the problem: *How it works* had 14 features and the security on one page, 50,000 pixels tall on a phone, and a search landed deep inside it. A visitor could not open *Slash commands* as a page.

The tools that people compare it with keep a short README and put the depth on other pages, such as actions/stale, the Gemini CLI action and GitHub Agentic Workflows.

## Options

1. Keep the README as the source and fold its sections in `<details>`. The README stays 139 KB, a fold doesn't help a search, and the website still has one page for 14 features.
2. A README generated from the pages. It needs a script and a check of its own, and what it generates is still too long to read on a phone.
3. The pages hold the details, and the README links them. A page of `docs/` is plain Markdown that reads the same on GitHub and on the website, with a page for each feature, and the README keeps the pitch, the hero, the three promises, the quick start, a line and a link for each feature, five points of the security, the comparison and the links.

## Decision

Option 3.

- **The README stays short.** At most 25 KB, and each line of *What it does* leads to the page of its feature. `tests/test_docs.py` holds it to that.
- **A feature has a page.** `docs/features/<name>.md` opens with a short summary and its picture, then has the details.
- **A page stays readable on a phone.** About 12,000 pixels tall at most, at a width of 390 pixels. A longer page is split by topic; what the code makes and only grows, every comment and the changelog, folds.
- **Snippets go only from page to page,** and only where two pages must show the same: the quick start on the home page. GitHub doesn't resolve a snippet, so a page that lives by them would read badly there; the two diagrams that two pages show are therefore copies, which a test keeps alike.
- **Links from before keep working.** A page that gave up parts forwards its former anchors with a script of the website, and the README keeps its former anchors on the lines that link the pages that have those sections now.

## Consequences

- A new feature gets a page and a line in the README, and a test fails without them.
- The comparison is written twice, on its page and in the README, since a reader should see it without leaving the README; a test keeps the two alike.
- GitHub keeps a former anchor of the README only as a named link, which lands on the line that leads to the section, not on the section itself. That is one tap more for an old link.
- This reverses what `CONTRIBUTING.md` said since the website was split into pages.
- The website gets more pages and a section *Features*, so the search lands on the page of what a visitor looks for.
