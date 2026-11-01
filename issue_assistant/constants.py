"""The names and limits that several modules share: the labels, the tasks and the markers."""

from __future__ import annotations

import re

# The runner of the job analyze, in the reusable workflow of issue-assistant.yml.
FIREWALL_RUNNER = "ubuntu-24.04-firewall"
# That runner is a technical preview of GitHub. The repository's variable of this
# name may choose another runner for the AI, should GitHub rename or end it: one of
# GitHub's runners of Ubuntu with a fixed version, such as ubuntu-24.04, where
# Harden-Runner blocks every host but those of the firewall policy for the job
# analyze alone; decision 0019 says why.
RUNNER_VARIABLE = "ISSUE_ASSISTANT_AI_RUNNER"

ACTION = "Dennis-Otto/issue-assistant"

# A repository as owner/name, in the characters GitHub allows; "." and ".." are no
# names.
REPOSITORY_NAME = re.compile(r"[\w-]+/(?!\.\.?\Z)[\w.-]+", re.ASCII)

BOT = "github-actions[bot]"

# The levels of thinking that the engine takes as effort; empty keeps its default.
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# GitHub keeps at most this many characters of the text of an issue or a comment;
# the assistant reads no more of any text.
MAX_TEXT = 65_536

# What `evaluate` replays: by default the 50 closed issues opened last that people
# labelled or closed as a duplicate, at most 200, found among the 1,000 closed issues
# opened last, a page of a hundred at a time.
EVALUATED = 50
MOST_EVALUATED = 200

# A first analysis also searches the issues of the related repositories of
# [related], at most this many, for the reports that belong in one of them; see
# siblings and write_siblings.
MAX_RELATED = 5

NEEDS_TRIAGE = "needs-triage"
NEEDS_INFO = "needs-info"
STALE = "stale"
POSSIBLE_DUPLICATE = "possible-duplicate"
DUPLICATE = "duplicate"
INVALID = "invalid"
WONTFIX = "wontfix"
FIXED = "fixed-in-next-release"
LIFECYCLE = (
    NEEDS_TRIAGE,
    NEEDS_INFO,
    STALE,
    POSSIBLE_DUPLICATE,
    DUPLICATE,
    INVALID,
    FIXED,
)

# What the AI answers for the priority of an issue whose priority it can't judge.
NO_PRIORITY = "none"
# The most people that GitHub assigns to one issue.
MAX_ASSIGNEES = 10
# A GitHub login: at most 39 letters, digits and single hyphens between them.
LOGIN = re.compile(r"(?=[\w-]{1,39}\Z)[A-Za-z0-9](?:-?[A-Za-z0-9])*", re.ASCII)

MODES = (
    "triage",
    "follow-up",
    "maintainer-reply",
    "release-reply",
    "maintainer-question",
    "discussion-answer",
)
# The tasks about a discussion rather than an issue, which DISCUSSION_WORKFLOW
# starts.
DISCUSSION_MODES = ("discussion-answer",)

# The label of an issue on which the assistant starts no AI run of its own; the
# commands mute and unmute set and remove it, and a maintainer can too.
MUTED = "assistant-muted"
# The label of an issue that stays: with the default exempt_labels, the sweep leaves
# it alone. The issue of the digest has it, and a maintainer can give it to others.
PINNED = "pinned"
# The kind of the marker of the issue that keeps the digest of the sweep; see
# keep_digest_issue.
DIGEST = "digest"
# The marker of the comment with which a reporter's /assistant again ends the mute of
# the box of the form on an issue without an analysis.
LET_IN = "let-in"

# The reactions on a command: handled, or refused with the reason in the run.
HANDLED = "eyes"
REFUSED = "confused"

# The largest number that GitHub's GraphQL API takes, an Int of 32 bits; no issue
# has a larger one.
MAX_NUMBER = 2**31 - 1
