---
description: What the first analysis of the issue assistant gives every new issue, what it looks like, and what every comment keeps in view.
---

# The first analysis

[← All features](README.md)

The AI writes it, and a job without AI checks it before anything is posted. It looks like this, here on an issue of a demo project:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/analysis-dark.webp">
  <img src="https://raw.githubusercontent.com/Dennis-Otto/issue-assistant/main/docs/images/analysis-light.webp" alt="A first analysis of the issue assistant on an issue of a demo project: thanks to the reporter, a summary, the first analysis with the likely cause, what is worth trying, the information needed with the reminder after 15 days, a related issue, links to the line of code and the section of the troubleshooting guide, and a footer that names Claude and the label it added, links how the assistant works and asks for a thumbs up or down">
</picture>

Every new issue gets it, by an AI that only reads: a summary, the likely cause with links to the lines of code and the documentation involved, what the reporter can try, the information still missing, and related issues and discussions, in the reporter's language: English, German, French, Spanish, Portuguese, Italian, Dutch, Polish, Turkish, Russian, Ukrainian, Chinese, Japanese, Korean or, around the AI's own words, English for any other. Its footer explains the assistant and asks for a 👍 or 👎, which the daily sweep counts.

Every comment keeps the questions for the reporter in view and says what it is:

- **Many links fold.** More than three links to related issues and files fold into one line below the questions, such as *Where to look: 5 links*, that opens them.
- **Issue numbers stay links when they exist.** The AI names issues and discussions as `#123`; the apply job looks up the first ten that its checks didn't find already, and only those that exist stay links. Any other number shows as code, so that a made-up one refers to nothing.
- **Lines of Markdown files open in their source.** A link to a line of a file that GitHub shows rendered, such as Markdown, AsciiDoc, a notebook or an SVG picture, opens its source with `?plain=1`, where the line is marked.
- **The footer explains the assistant.** The footer of an analysis and of a follow-up links *How the assistant works*: the part *What happens with your issue* of the first file of `[transparency] files`, or the file. It asks for a 👍 or 👎 on the comment, and the summary of the morning sweep, or of one started by hand, counts them for the analyses and follow-ups of the last 30 days, as numbers only, such as `Feedback of the last 30 days: analyses 12, 👍 9, 👎 1; follow-ups 3, 👍 2, 👎 0.` On a duplicate notice, a 👎 also keeps the issue open.
- **A reminder links the questions** that it reminds of, in the analysis, the follow-up or the comment of a maintainer that asked them.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/what-a-comment-links-and-how-its-feedback-is-counted.dark.svg">
  <img src="../images/diagrams/what-a-comment-links-and-how-its-feedback-is-counted.svg" alt="What a comment links and how its feedback is counted: The apply job looks up the issue numbers that the AI names, up to ten: an issue or a discussion that exists stays a link, any other number becomes code. More than three links to related issues and files fold into one line below the questions, which opens them. The footer of every analysis and follow-up links the notice to reporters and asks for a thumbs up or down, and the morning sweep counts those of the last 30 days in its summary. Questions that stay unanswered for 15 days get a reminder that links the comment with them.">
</picture>
