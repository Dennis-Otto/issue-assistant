# An engine that opens images sees the screenshots of the reporter, which the job of the attachments downloads as bytes alone

- Status: accepted
- Date: 2026-11-14

## Context

For the cards of HomeDarts and for Player Switch, the screenshot is the report, and a reporter of any project drags one into an issue more often than a log (#365, step 3). [Decision 0033](0033-the-ai-reads-the-text-files-that-a-reporter-attaches.md) brought the text files to the AI and left images out, since only some engines can open them, and an image needs caps of its own.

Of the four engines, Claude's tool `Read` and Gemini's tool `read_file` open an image of the checkout and hand it to the model, with no tool more than those that read. Codex and Copilot keep to reading files in the workflow too, but no run in the workflow has shown yet that their tools open an image there, and the canary runs only the engine of the repository's own configuration.

An image comes from a stranger, as a log does: the text in a screenshot can tell the AI what to do, a file can call itself an image and be a page or a script, and a crafted image can aim at the program that decodes it.

## Options

1. **Leave images out.** Nothing new to trust, but the most useful part of many reports stays unseen.
2. **Describe each image with a separate model before the analysis.** A second call of an engine, its secret in one more job, and the description is the AI's text, no less open to what the image says.
3. **Download the images in the job `attachments` and name them in `attachments.md` for an engine that opens images.** The same job, hosts and token as the text files, and the engine opens each with the tool that it has anyway.

## Decision

Option 3:

- **The job `attachments` downloads the images** of the issue and of the comments of its reporter, from a link that GitHub shows, as it does the text files: at most 3 of an issue, each whole and up to 5 MB, the limit of one image of the APIs of the engines; a larger one is left out, since a part of an image is none. A download starts at `github.com/user-attachments/assets/…`, at `/assets/` of a repository, or at the older link to GitHub's storage, and the token of a private repository goes to `github.com` alone, never to a host of the storage.
- **Bytes alone.** The job compares the first bytes of each file with those of PNG, JPEG, GIF and WebP and names the file after its kind, such as `image-1.png`. Nothing in it opens, decodes, converts or scales an image. A file that starts otherwise, such as an SVG, which is a document that may hold scripts, or a page of GitHub, is left out, whatever its name or link says.
- **Only for an engine that opens images.** `reads_images` of the engine in `issue_assistant/engines.py` decides it: Claude and Gemini. `attachments.md` names each image with its path, as `.issue-assistant/attachments/image-1.png`, and the context step names it only when the index of the job names that file, the name is one that the job gives, and its first bytes are still those of its kind. For Codex and Copilot, `attachments.md` says that an image is attached and that the engine doesn't open it, so the AI neither guesses at it nor asks for it again. An engine is added once a run shows that its tool opens images in the workflow.
- **The reporter's material.** The rules of the prompt say that what an image shows, text in it included, is the reporter's material and never an instruction, as for each text in a fence.
- **Nothing else changes.** The firewall policy of the AI's job, the tools of the engines, which only read, and the checks of `apply` stay as they are: the image comes in the artifact of the run, as the text files do.

## Consequences

- A screenshot of a card, of an error dialog or of a log in a terminal is part of the analysis by Claude or Gemini, at about one or two thousand tokens of each image.
- An image can say anything to the AI, as a text can, and the AI still only reads: `apply` checks every answer against the schema, the labels and the issues and refuses one with a secret, whatever the AI saw. `tests/test_attachments.py` shows that the same answer posts the same comment after a hostile image as without one.
- The engine's own program may decode an image to scale it before it sends it to its vendor, in the job of the AI, which has the engine's secret. What limits a crafted image there: only the four kinds, 5 MB, 3 images, a job whose firewall reaches only the hosts of the policy, tools that only read, and an engine whose updates Renovate brings. A repository that wants no image read can choose an engine without `reads_images`; a setting of its own is a possible later step.
- The artifact of a run holds up to 15 MB of images more, for a day.
