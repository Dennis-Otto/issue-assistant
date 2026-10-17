---
description: How often the issue assistant's labels, duplicates and questions match the maintainers', measured on closed issues, and how to measure it yourself.
---

# How accurate is it?

How often the assistant's labels, duplicates and questions match what the maintainers decided, measured by replaying the first analysis of closed issues as each was when it was opened. Few AI triagers publish such numbers, and maintainers rightly distrust a labeler that they can't measure: `evaluate` gives every repository its own numbers before it switches the assistant on, and after a change of the model or of the description of the project.

## The numbers

None yet. The first ones come from the repositories that use the assistant, [ha-autodarts](https://github.com/Dennis-Otto/ha-autodarts), [Paperless Sync](https://github.com/Dennis-Otto/paperless-sync) and [Paperless Unified Search](https://github.com/Dennis-Otto/paperless-unified-search), each with the day, the engine and the model of its measurement, as `evaluate` writes them into its report.

A number of your own repository is welcome in the [discussions](https://github.com/Dennis-Otto/issue-assistant/discussions): the table of measures of the report, with the day, the engine and the model that it names.

--8<-- "README.md:accuracy"

## What the numbers can't tell

- **The text of an issue is that of today.** A reporter who added what the maintainers asked for makes the question for it look needless.
- **The rest of the repository is partly that of today.** The texts and labels of the other issues, and the code, the documentation and the changelog of the checkout, which may already name the fix.
- **A maintainer's question is a guess.** A question mark in the first comment of a maintainer, or the label `needs-info`, counts as a question for more information, also when it asked something else.
- **Small numbers vary.** A repository with few closed issues has few duplicates to find, and one more found or missed moves the share a lot; the report names the counts behind each share.
