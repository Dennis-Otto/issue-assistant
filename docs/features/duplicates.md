---
description: When the issue assistant closes a duplicate, from the notice to the closing, and which duplicates stay a suggestion for the maintainer.
---

# Duplicates

[← All features](README.md)

A sure duplicate closes only when nobody objects. The AI must be sure, and the original must be older, open, and opened or triaged by a maintainer; any other duplicate becomes a suggestion for the maintainer, in GitHub's panel, and a related issue in the comment:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/a-duplicate-from-the-notice-to-its-closing.dark.svg">
  <img src="../images/diagrams/a-duplicate-from-the-notice-to-its-closing.svg" alt="A duplicate, from the notice to its closing: The reporter opens an issue. The analysis of issue-assistant.yml says that it looks like a duplicate of an older issue and closes in 3 days, and adds possible-duplicate. A comment of anyone in those days removes possible-duplicate at once, and the issue stays open. A thumbs down on the notice by the reporter or by someone who may push makes the morning sweep of issue-lifecycle.yml remove possible-duplicate after the 3 days, and the issue stays open. Without an objection, the morning sweep posts a comment, closes the issue as a duplicate of the original and labels it duplicate.">
</picture>

The closing comment asks the reporter to go on in the original, where a 👍 shows that more people are affected. The [security design](../security/issue-text.md) says why only an original that a maintainer vouches for closes another issue.
