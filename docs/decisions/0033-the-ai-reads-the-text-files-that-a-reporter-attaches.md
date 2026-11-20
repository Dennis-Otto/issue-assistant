# The AI reads the text files that a reporter attaches, downloaded by a job of their own without the AI

- Status: accepted, amended by [0034](0034-an-engine-that-opens-images-sees-the-screenshots-of-the-reporter.md) and on 2026-11-20 by the rule of the tags below
- Date: 2026-11-14

## Context

A reporter who drags a log, a diagnostics download or a screenshot into an issue adds a link to one of GitHub's own hosts, and that file is often the most useful part of a bug report. The AI read only the link: the context step downloaded nothing, and the rules of the prompt told the AI that it can't open it (#365). The first analysis asks for a log, the reporter attaches a `.txt`, and the follow-up can only note that something was attached.

Three things stand in the way of a download in the job of the AI:

- The firewall of the job `analyze` lets through only the hosts of `.github/egress-firewall.yaml`, and GitHub's own defaults, which include `github.com` but not `objects.githubusercontent.com` or `private-user-images.githubusercontent.com`, to which `github.com` sends the download of every attachment on. Each host of the policy is one that a prompt attack could try to reach, and every repository would have to add them to its own policy.
- An attachment comes from a stranger, so it is untrusted input for the AI and for the runner: a log can hold instructions for the AI, a heading that looks like a maintainer's comment, characters that no maintainer sees, a token that the reporter pasted by mistake, or bytes that are no text at all.
- A private repository needs a token to download its attachments, and the job of the AI has the secret of its engine.

## Options

1. **Download in the context step of `analyze`.** One step, but only with the hosts of GitHub's storage in the firewall policy of every repository, for the whole job of the AI.
2. **Download in the job `event`.** No runner more, but that job has a token that may write to the issues, and its Harden-Runner block would let more hosts through for every event.
3. **A job `attachments` of its own, between `event` and `analyze`, that hands the files on as an artifact.** It reads with a token that may only read, reaches GitHub's hosts for attachments and nothing else, and runs only when the AI will run.

## Decision

Option 3, for the text files, the cheapest step of #365 and the one that the most reports need:

- **The job `attachments`** runs where `analyze` runs, on `ubuntu-24.04` with `contents: read` and `issues: read`. Harden-Runner blocks every host but GitHub's API, `github.com`, the three hosts of GitHub's storage for attachments and GitHub's results service, which keeps the artifact. The command `attachments` downloads the files and the step *Keep the attachments for the AI* keeps them as the artifact `issue-assistant-attachments` for a day. This amends [decision 0008](0008-the-jobs-that-write-reach-only-github.md), whose jobs without the AI reach only GitHub's API and `github.com`: the storage of attachments is GitHub's too, and the job holds no token that writes, so a compromised step in it has nothing to send that could change the repository.
- **Only the files of the issue and of the comments of its reporter count,** each once, from a link that GitHub shows: one in an HTML comment or a link reference definition, which GitHub hides, counts as none, so that no file reaches the AI that the maintainers don't see. A third person can't fill the context with files of their own. Of a tag of HTML, only the address that GitHub shows as a link or an image counts: the `href` of an `<a>` and the `src` of an `<img>` (or an `<image>`, which GitHub's parser reads as one), the first of its name, as the HTML parser reads the tag. Every other attribute counts as none, also one that GitHub's sanitizer keeps: `title`, which the page shows only while the pointer rests on the tag, `alt`, `name` and `cite`, the `srcset` of a `source`, which shows its image only to readers of one colour scheme or size, and the `src` of a `video`, which shows a player and no link. A tag that GitHub shows as text, in fenced code, behind its filter of tags such as `<script`, or in a paragraph where it is no tag by the rules of CommonMark, such as the autolink `<https://…>`, shows its address as text, and that counts. Where it is hard to tell, the address counts as none: in a code span, at the start of a row that an HTML block may hold, or in a tag that no `>` ends on its row, which takes the rest of the row. Each of these rules was checked against GitHub's own renderer (amended on 2026-11-20).
- **Only GitHub's hosts.** A download starts only at `https://github.com/user-attachments/files/…`, or at `/files/` of the repository itself, the older form; it follows at most three redirects, each to one of GitHub's hosts for attachments over TLS, and any other ends it. The token of the job goes only to `github.com`, and only for a private repository: urllib never sends it on to a redirect.
- **Caps of number, size and type.** At most 5 text files, `.txt`, `.log`, `.json`, `.yaml` and `.yml`, and of each at most 80,000 bytes, enough for the 20,000 characters that the AI reads of every text. A file with a null byte, or that isn't UTF-8, is no text and is left out. Images, archives and every other file are named in `attachments.md` and never downloaded: nothing of a file is ever run, opened by a program or unpacked.
- **The text as the reporter's text.** The context step of `analyze` takes the artifact (should GitHub fail to hand it over, the analysis goes on, and `attachments.md` says that the file wasn't downloaded) and writes `attachments.md`: each file under a heading that the script writes, its text in a code fence that nothing in it can close, as `issue.md` has each comment. Before, the escape sequences of a terminal, control characters and the invisible characters go, and each part that looks like a token or a key, by the patterns that refuse an answer with a secret, becomes `[secret]`, each e-mail address `[e-mail address]`; a note says how many, so that the AI can still tell the reporter that the file shows a secret. The rules of the prompt name the file in place of "you can't open it". Once `attachments.md` is written, the context step removes the downloaded text files and the index, so that the engine's tools, which read every file of the checkout, find no copy without the cleaning, the fence and the cap.
- **Nothing else changes.** The firewall policy, the tools of the engines, which only read, and the checks of `apply` stay as they are. On the runner in reserve, Harden-Runner lets GitHub's results service through while there are attachments, as the firewall runner does by itself, so that the job can take the artifact.

Images, step 3 of #365, need an engine that reads them and caps of their own: [decision 0034](0034-an-engine-that-opens-images-sees-the-screenshots-of-the-reporter.md) adds them to this job.

## Consequences

- A log, a configuration or the diagnostics of Home Assistant that a reporter attaches are part of the first analysis and of every later task of the issue, up to 20,000 characters each.
- A run with an issue to analyze has one job more, of some seconds, and the AI starts after it; a download that fails leaves only that file out, with its reason in `attachments.md`.
- A hostile file can say anything to the AI, as the text of an issue can: it is material in a fence, the AI can only read, and `apply` checks every answer against the schema, the labels and the issues, and refuses one with a secret, whatever the AI read. `tests/test_attachments.py` shows that the same answer posts the same comment after a file that asks for anything else.
- GitHub may refuse the token of the job for the attachments of a private repository, which it documents for no token; the file is then named with the reason, and the AI reads only that it exists.
