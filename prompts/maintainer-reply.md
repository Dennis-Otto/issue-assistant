## Your task: does issue #{issue} now wait for its reporter?

A maintainer commented on issue #{issue}. `{context}/comment.md` holds that comment, the one you judge; `{context}/issue.md` holds the issue and all its comments, for the conversation around it. Read both. Judge the comment of comment.md, even when issue.md shows newer comments after it.

`waiting_for_reporter` is true only when the maintainer's comment in comment.md asks the reporter, or the people affected, for information, a test, a confirmation or another action, and progress depends on the answer. It is false for announcements, explanations, thanks, plans, answers without a question, questions to other people and rhetorical questions, and when comment.md holds no comment.

`reason` explains the decision in one English sentence. Don't investigate in the repository for this task.
