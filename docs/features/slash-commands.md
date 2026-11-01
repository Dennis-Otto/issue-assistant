---
description: The /assistant commands that maintainers and reporters give in a comment, who may give each, and how a reaction tells what happened.
---

# Slash commands

[← All features](README.md)

Maintainers steer the assistant from the issue, and its reporter can ask for a new analysis: a comment whose first line starts with `/assistant` gives a command.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/what-a-command-on-an-issue-does.dark.svg">
  <img src="../images/diagrams/what-a-command-on-an-issue-does.svg" alt="What a command on an issue does: A comment whose first line starts with /assistant is a command when a maintainer or the reporter of the issue wrote it; from anyone else, it is an ordinary comment. A maintainer may give every command. The reporter may give not-duplicate, and again once, while the issue has needs-triage, after its first analysis, while it isn't muted and within the limits of the day. A command that is carried out gets the reaction eyes: again starts a new analysis and ask an answer of the AI; duplicate, not-duplicate, waiting, mute and unmute set labels and post a notice without the AI. A command that is refused gets the reaction confused, and the summary of the run says why. A command answers, asks and objects to nothing.">
</picture>

| Command | Who | What it does |
| --- | --- | --- |
| `/assistant again` | maintainers; the reporter once | A new first analysis, also when the issue has one, which decides anew whether the issue is a duplicate. The reporter may ask once per issue, while it has `needs-triage` and after its first analysis, within the [limits of the AI runs](limits.md), or to let the AI in after [the box of the form](opt-out.md), which removes `assistant-muted`, also without an analysis. |
| `/assistant ask <question>` | maintainers | The AI answers the question from the code, the documentation and the issue, in a reply that links the files it rests on. The question may go on over more lines. |
| `/assistant duplicate #12` | maintainers | The notice that the issue duplicates the open issue #12, and `possible-duplicate`: it closes as a duplicate after `duplicate_grace_days` (3 by default) unless someone objects, as after a duplicate that the AI found. |
| `/assistant not-duplicate` | maintainers, the reporter | Removes `possible-duplicate`, so that the issue stays open. |
| `/assistant waiting` | maintainers | Adds `needs-info`: the issue waits for its reporter, with the reminder and the closing. |
| `/assistant mute`, `/assistant unmute` | maintainers | Adds or removes `assistant-muted`; the only commands that work on a closed issue too. On a muted issue, the AI starts nothing on its own: no analysis, follow-up, check of a maintainer's comment or reading of a comment after a release. A maintainer's `again` and `ask` still run, and labels, reminders and closing go on. On [the issue of the digest](digest.md#the-digest-as-an-issue), no command does anything. |

- **Only maintainers and the reporter give commands.** Maintainers are the owner, the members of the organization and the collaborators, as everywhere in the assistant. The same line from anyone else is an ordinary comment, which steers nothing.
- **A reaction tells what happened.** 👀 on the comment: the command is carried out, or its task of the AI starts. 😕: it was refused, as an unknown command or one that only maintainers may give, and the summary of the run says why, without repeating the comment. A refused line is still an ordinary comment, so a mistyped command still answers a question or objects to a duplicate notice.
- **A command is no part of the conversation.** It answers no question of the assistant and asks the reporter none, so the waiting for an answer and its reminder go on, and it objects to no duplicate notice; `/assistant not-duplicate` does that.
- **The event job runs no AI.** It carries out the commands that need none itself, and hands `again` and `ask` to the AI through the analyze job, as every other task. `apply` checks an answer to a question as every text of the AI and posts one answer to each question; a run by hand with the task `maintainer-question` answers the newest question again.
- **A maintainer vouches for the original.** A duplicate that the AI finds closes only an issue whose original a maintainer opened or triaged; one that a maintainer names with `/assistant duplicate` needs no triage of the original. The original must be open, as after a notice of the AI.
- **The label of a muted issue** is `assistant-muted`, in the group `decision` of the starter `labels.toml`. Where `labels.toml` doesn't define it, the first `/assistant mute` creates it, and a maintainer can also add or remove it by hand. [The box of an issue form](opt-out.md) adds it too, at the reporter's wish.
