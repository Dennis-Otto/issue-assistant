---
description: What a repository needs for the issue assistant, how to install its workflows and settings step by step, and how to check that it works.
---

# Get started

What a repository needs for the issue assistant, how to set it up there, and how to see it work before the first reporter does.

--8<-- "README.md:requirements"

--8<-- "README.md:set-up"

## Check that it works

1. **The check passes** in the CI of the repository, as step 7 adds it. It names every problem of the labels, the issue forms, the workflows, the firewall policy and the settings, every key that it doesn't know, and a `project.md` that still holds the starter's first line or says less than 80 characters. [Troubleshooting](faq.md#troubleshooting) has the fix for each.
2. **The labels exist** once the set-up is merged into the default branch: the Labels workflow creates those of `.github/labels.toml`, and *Issues → Labels* lists them.
3. **A dry run shows the comment.** Under *Actions → Issue assistant → Run workflow*, enter the number of an issue. A run by hand starts as a dry run that writes nothing, and the summary of its `apply` job shows the comment that it would post, as the [reference](reference.md#commands) shows.
4. **A new issue gets its analysis** within a few minutes. Open it from an account that is not a maintainer's: issues of the owner, members and collaborators get the labels of their form, but no analysis.

When no comment comes, [troubleshooting](faq.md#troubleshooting) has the usual causes, each with its fix. [What reporters see](comments.md) shows every comment that the assistant posts.
