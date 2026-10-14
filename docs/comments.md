---
description: Every comment that the issue assistant posts on an issue, in English and German, from the first analysis to the release that ships the fix.
---

# What reporters see

Every comment that the issue assistant posts on an issue, as reporters see it, in English and in German. The assistant writes in the language of the reporter: German for German, English otherwise.

The website makes the comments with every build, by the functions and with the texts of `issue_assistant.py` of its version, for the demo project of the pictures with made-up people. In the first analysis and in the follow-up, the AI writes the summary, the analysis, what to try, the questions and why a link matters; the rest is the assistant's own text, the same in every repository: also the line that folds more than three links, and the footer, which links the notice to reporters and asks for a 👍 or 👎. An issue that the AI takes for spam gets no comment, only the label `invalid`, and the issues of maintainers get the labels of their form but no analysis.

<!-- comments of issue_assistant.py -->
The comments themselves are on the page What reporters see in the Guide of the [website](https://dennis-otto.github.io/issue-assistant/), which makes them from `issue_assistant.py` with every build.
<!-- end of the comments -->
