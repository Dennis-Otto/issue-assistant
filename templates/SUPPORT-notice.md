## What happens with your issue

Within a few minutes, the issue assistant labels a new issue and posts a first analysis: a summary, the likely cause or the documentation that helps, related issues and, if needed, questions. The assistant uses {engine}, an AI by {vendor}; it reads the text of the issue and the repository and can be wrong. The maintainers read every issue and decide. The assistant is [an open-source project of its own](https://github.com/Dennis-Otto/issue-assistant).

- **Questions** mark the issue as waiting for you. Answer in a comment or by editing the issue. Without an answer, a reminder follows after {remind_days} and the issue closes after {close_days}; answering reopens it.
- **A likely duplicate** of an open issue gets a notice and closes {grace_days} later, so the conversation stays in one place. If it's something different, write a comment or react to the notice with 👎, and it stays open.
- **A fix** on the default branch marks the issue `fixed-in-next-release`. It stays open until a release ships the fix, and then closes with a link to the release. If the problem persists after the update, write a comment within {reopen_days} and the issue reopens.
- **Old issues** are locked once they have been closed for more than {lock_days}. If the problem comes back, open a new issue and link the old one.
