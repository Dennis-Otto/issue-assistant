## Your task: does the problem of issue #{issue} persist after the release?

A release closed issue #{issue}: the last comment in `{context}/issue.md` whose heading names the issue assistant names the release that ships the fix. Since then, the reporter commented. `{context}/comment.md` holds that comment, the one you judge; `{context}/issue.md` holds the issue and all its comments. Read both. Judge the comment of comment.md, even when issue.md shows newer comments after it, such as those of other people.

`problem_persists` is true only when the reporter's comment in comment.md says that the problem of this issue still occurs, or occurs again, with that release or a newer one. It is false for thanks, confirmations that it works, questions about updating, comments that they haven't updated yet, other remarks and different problems, which belong in a new issue, and when comment.md holds no comment.

`reason` explains the decision in one English sentence. `language` is the language of the comment in comment.md. Don't investigate in the repository for this task.
