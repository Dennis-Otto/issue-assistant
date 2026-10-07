## Your task: does the problem of issue #{issue} persist after the release?

A release closed issue #{issue}: the issue assistant's last comment in `{context}/issue.md` names the release that ships the fix. Since then, the reporter commented; their comment is the newest one. Read the issue and its comments.

`problem_persists` is true only when the reporter's newest comment says that the problem of this issue still occurs, or occurs again, with that release or a newer one. It is false for thanks, confirmations that it works, questions about updating, comments that they haven't updated yet, other remarks and different problems, which belong in a new issue.

`reason` explains the decision in one English sentence. `language` is the language of the reporter's comment. Don't investigate in the repository for this task.
