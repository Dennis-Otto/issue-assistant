---
description: What a repository needs for the issue assistant, how to install its workflows and settings step by step, and how to check that it works.
---

# Get started

What a repository needs for the issue assistant, how to set it up there, and how to see it work before the first reporter does.

--8<-- "README.md:requirements"

--8<-- "README.md:set-up"

## Check that it works

1. **The check passes** on your computer and in the CI of the repository, as *Keep it checked* above adds it. It names every problem of the labels, the issue forms, the workflows, the firewall policy and the settings, every key that it doesn't know, and a `project.md` that still holds the starter's first line or says less than 80 characters. [Troubleshooting](faq.md#troubleshooting) has the fix for each.
2. **The labels exist** once the set-up is merged into the default branch: the Labels workflow creates those of `.github/labels.toml`, and *Issues → Labels* lists them.
3. **A dry run shows the comment**, as the last step of the set-up tries it: the summary of the `apply` job of a run by hand shows the labels and the comment that it would post, and writes nothing.
4. **A new issue gets its analysis** within a few minutes. Open it from an account that is not a maintainer's: issues of the owner, members and collaborators get the labels of their form, but no analysis.

When no comment comes, [troubleshooting](faq.md#troubleshooting) has the usual causes, each with its fix. [What reporters see](comments.md) shows every comment that the assistant posts.
