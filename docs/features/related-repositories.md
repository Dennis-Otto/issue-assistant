---
description: How a first analysis of the issue assistant searches the issues of related public repositories, and says when an issue may belong in one of them.
---

# Related repositories

[← All features](README.md)

Projects that share their users, such as an app and its server, get reports in the wrong repository. `[related] repositories` in `config.toml` names up to 5 public repositories that share users and topics with this one, and a first analysis searches their issues as well:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/how-a-first-analysis-uses-the-related-repositories.dark.svg">
  <img src="../images/diagrams/how-a-first-analysis-uses-the-related-repositories.svg" alt="How a first analysis uses the related repositories: The context step asks GitHub whether each repository of [related] is public; one that isn't, or doesn't exist, is left out with a warning. It searches each public one for the words of the new issue and writes what it finds to related-issues.jsonl, each issue with its repository, and the whole texts to related/. The AI may name those issues as related, never as duplicates, and may say in belongs_in that the issue belongs in one of the related repositories. The apply job links only issues that exist in a public related repository and leaves out the rest, and belongs_in becomes one sentence for the maintainers. Nothing moves or closes on its own: a maintainer transfers the issue by hand.">
</picture>

- **The AI reads what a search finds there.** Up to 30 issues of each repository that GitHub's search finds for the words of the new issue go into `related-issues.jsonl`, each with its `repository`, and their whole texts into `related/<owner>/<name>/<number>.md`. A repository that isn't public, or that GitHub fails to search, is a warning and adds nothing.
- **Their issues can be related, never duplicates.** The comment links an issue of a related repository under *Related*, as `owner/name#123`, once `apply` found it in a public related repository; any other reference to another repository stays code. A duplicate is always an issue of this repository, so nothing closes because of an issue elsewhere.
- **One sentence says where an issue may belong.** When the issue is about the code or the documentation of a related repository, the analysis says so in a sentence for the maintainers, and the summary of the job names the repository. Nothing moves the issue: a maintainer transfers it with *Transfer issue* in its sidebar.

The issues of a related repository become links in a public comment and their texts part of the log of the run, so a private one stays out: the assistant asks GitHub whether each is public before it reads any of its issues.
