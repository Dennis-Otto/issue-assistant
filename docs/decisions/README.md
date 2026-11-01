# Decisions

The decisions that shape this project, each with its reasons, so that a later change can see why things are as they are. A decision that changes an earlier one gets a new record, and the earlier one says which record replaces it.

Copy [the template](0000-template.md) to the next free number and describe the decision in the pull request that makes it.

| Record | Decision | Status |
| --- | --- | --- |
| [0001](0001-record-decisions.md) | Record the decisions that shape the project | accepted |
| [0002](0002-the-ai-only-reads.md) | The AI only reads; a job without AI writes | accepted |
| [0003](0003-the-standard-library-alone.md) | Run on the standard library of Python alone | accepted |
| [0004](0004-prompts-without-a-vendor.md) | Keep the prompts and texts independent of the AI vendor | accepted |
| [0005](0005-close-with-the-release.md) | Close an issue with the release that ships its fix | accepted |
| [0006](0006-the-templates-exactly.md) | Every repository uses the workflows exactly as the templates have them | accepted, amended by [0007](0007-templates-live-in-templates.md) and [0015](0015-reusable-workflows.md) |
| [0007](0007-templates-live-in-templates.md) | The templates live in `templates/`; this repository's own workflows come from the blueprint | accepted |
| [0008](0008-the-jobs-that-write-reach-only-github.md) | The jobs that write reach only GitHub: Harden-Runner blocks every other host | accepted |
| [0009](0009-limit-the-ai-runs.md) | Limit the AI runs that strangers start, counting issues rather than answers | accepted |
| [0010](0010-the-lifecycle-keeps-the-day-it-names.md) | The lifecycle counts days of the calendar and keeps the day that a reminder names | accepted |
| [0011](0011-install-pins-its-own-checkout.md) | `install` pins the commit of its own checkout, the release whose templates it writes | accepted |
| [0012](0012-commands-from-the-issue.md) | Maintainers and the reporter steer the assistant with commands that are no part of the conversation | accepted |
| [0013](0013-a-catalog-for-each-language.md) | A catalog for each language of the comments, and English around any other | accepted |
| [0014](0014-bound-every-run-and-put-the-rules-first.md) | Bound every run of the AI by its task, put the rules in the system prompt, and show what each run cost | accepted |
| [0015](0015-reusable-workflows.md) | The templates call the jobs as reusable workflows of a release, so an update is a move of the pins | accepted |
| [0016](0016-learn-from-the-history-on-github.md) | Learn from the maintainers' corrections as they stand on GitHub, as data of the context | accepted |
| [0017](0017-engines-without-an-action-of-their-own.md) | Four engines, and the command lines without a fitting action run in the action | accepted, amended by [0020](0020-patch-versions-of-the-engines-merge-on-their-own.md) |
| [0018](0018-answer-questions-in-discussions.md) | A first answer in Discussions, from a workflow of its own that alone may write there | accepted |
| [0019](0019-a-runner-for-the-ai-when-the-firewall-runner-is-gone.md) | A repository may name another runner for the AI, behind Harden-Runner with the hosts of its firewall policy, should the firewall runner be gone | accepted |
| [0020](0020-patch-versions-of-the-engines-merge-on-their-own.md) | A patch version of an engine merges on its own once the check of its contracts passed | accepted |
| [0021](0021-letters-of-a-script-count-as-stopwords.md) | Letters of a script of their own count as stopwords in the guess of the language, and Russian and Ukrainian get a third form of a number of days | accepted |
| [0022](0022-the-pages-hold-the-details.md) | The pages of `docs/` hold the details, with a page for each feature, and a short README links them | accepted |
| [0023](0023-a-reporter-can-keep-the-ai-out.md) | A reporter can keep the AI out of their issue with a box of the form, which mutes it and takes its text out of every other run | accepted |
