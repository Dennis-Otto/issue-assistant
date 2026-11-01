---
description: Every comment that the issue assistant posts on an issue or a discussion, in English and German, from the first analysis to the release that ships the fix.
---

# What reporters see

Every comment that the issue assistant posts on an issue, and on a discussion where it answers questions, as reporters see it, in English and in German, and the first analysis and the reminder also in Chinese, Japanese and Russian. The assistant writes in the language of the reporter: English, German, French, Spanish, Portuguese, Italian, Dutch, Polish, Turkish, Russian, Ukrainian, Chinese, Japanese or Korean, with the texts of the catalogs in `locales/`, and in any other language with its own words in English around the AI's; [Languages](features/languages.md) tells how it chooses. A comment in another language than the maintainers' folds a summary for them in theirs, as the example *A summary for the maintainers* shows.

The website makes the comments with every build, by the functions and with the texts of `issue_assistant.py` of its version, for the demo project of the pictures with made-up people. In the first analysis and in the follow-up, the AI writes the summary, the analysis, what to try, the questions, why a link matters and the summary for the maintainers, and in a first answer to a discussion the answer, the questions, why a link matters and the summary for the maintainers; the rest is the assistant's own text, the same in every repository: also the line that folds more than three links, and the footer, which links the notice to reporters, asks for a 👍 or 👎 and links the project, unless `[transparency] credit = false` leaves that out. An issue that the AI takes for spam gets no comment, only the label `invalid`, and the issues of maintainers get the labels of their form but no analysis.

<!-- comments of issue_assistant.py -->
The comments themselves are on the page What reporters see in the Guide of the [website](https://dennis-otto.github.io/issue-assistant/), which makes them from `issue_assistant.py` with every build.
<!-- end of the comments -->
