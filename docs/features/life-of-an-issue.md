---
description: How an issue moves from its first analysis to its closing with the labels of the issue assistant, and what its comments promise a reporter.
---

# The life of an issue

[← All features](README.md)

Questions of the assistant or the maintainer mark an issue `needs-info`; after 15 days the reporter gets a reminder that names the day the issue closes, and after 30 days it closes on that day. An answer, in a comment or by an edit of the issue, ends the waiting, and a comment reopens a closed issue.

Labels say where an issue stands, and the lifecycle moves it on; each box has the colour of its label:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/the-life-of-an-issue-and-its-labels.dark.svg">
  <img src="../images/diagrams/the-life-of-an-issue-and-its-labels.svg" alt="The life of an issue and its labels: A new issue gets needs-triage, the labels of its form and a first analysis, and the maintainer takes over. When information is missing, or the maintainer asks the reporter, it gets needs-info and waits for the reporter: on day 15 it gets stale and a reminder, and on day 30 it closes as not planned; an answer, also after the closing, brings it back to the maintainer. A sure duplicate gets possible-duplicate and a notice and closes as a duplicate on day 3, unless an objection, a comment or a thumbs down of the reporter or of someone who may push, brings it back to the maintainer. A merged fix gives it fixed-in-next-release, and the release closes it with a link; a comment within 30 days that it is still broken brings it back to the maintainer.">
</picture>

An issue that waits for its reporter keeps what the comments of the assistant promise:

- **An answer is a comment or an edit of the text,** as the questions offer. Either ends the waiting, also when the event job missed it, since the sweep reads the comments and the history of the text. A new title alone answers nothing, and the AI gets no follow-up to write for it.
- **The reminder names the day, and the issue closes on it.** The waiting counts days of the calendar in UTC, and the hidden marker of the reminder records the day, so the sweep of that morning closes the issue, whatever minute it starts.
- **A comment of the reporter reopens the issue** that the sweep closed for a missing answer, as the closing comment promises, whatever its labels say: also when an answer came in while the sweep closed it and took them.
- **A follow-up waits for no maintainer.** The AI writes for a minute or two; the job that posts its follow-up checks again, and posts nothing once a maintainer joined the conversation.
