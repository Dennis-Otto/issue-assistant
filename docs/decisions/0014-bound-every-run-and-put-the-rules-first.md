# Bound every run of the AI by its task, put the rules in the system prompt, and show what each run cost

- Status: accepted
- Date: 2026-10-14

## Context

[0009](0009-limit-the-ai-runs.md) limits how many runs strangers start, but not what one run may spend. Every task ran with the same model and 40 turns, and nothing capped its cost: a reply that only judges whether a maintainer's comment waits for the reporter took Claude Opus 5.5 3 turns and $0.08, a first analysis 15 turns and $0.20, and an issue written to make the AI read on could drive one run to 40 turns of the most expensive model.

The rules of the assistant stood at the start of one user prompt, followed by the task. The words of the issue never stood in the prompt, only in files that the rules call material, but the rules shared their place with the task, where instructions compete with each other. The engine's command line takes a system prompt from a file, `--append-system-prompt-file`, which comes before every user message and is the place of instructions that come first.

The engine's action writes the numbers of every run into an execution file, its turns, time, cost and tokens, which no step read, so a maintainer saw no cost anywhere.

## Options

1. Keep one model and one number of turns for every task, and leave the cost to the limits of the day and to a spend limit of the API key. Simple, but the cheapest tasks pay for the most expensive model, and one steered run has no ceiling.
2. Separate settings for each task. Exact, but ten keys for five tasks, which repositories would have to keep consistent.
3. One model and one number of turns for the analyses, from which the other tasks take a fixed part, a lighter model for the replies to one comment, and a budget in dollars for every run; the rules as the system prompt; and one line of numbers per run in the summary of its job.

## Decision

Option 3. The tasks differ in what they read, not in how they are set up: a follow-up reads the reporter's answers to known questions and needs about half the turns of an analysis, a reply to one comment reads two files and needs a tenth, but at least 4 turns, three of which a measured reply used. So `max_turns` alone moves all of them, and `light_model` lets the yes-or-no replies run on a cheaper model without touching the analyses. `max_budget_usd` makes the cost of one run a number that the maintainer sets, whatever an issue says. Each value reaches the engine's command line without quotes, so `check` and the engine command refuse anything that is no model name, no whole number of turns from 1 to 200, no number of dollars above 0 and up to 100, or no level of effort.

The rules move into `.issue-assistant/rules.md`, which the engine takes as its system prompt; its prompt carries only the task. The output `prompt` of `context` keeps rules and task together, so a repository whose workflow is from an earlier release still runs with its rules until it installs again.

A step `usage` after the engine's step, also after a failure, reads the result of the execution file and writes one line into the summary of the job: the model, how the run ended, its turns and time, its cost against the budget, and its tokens. The file holds the whole run, with the texts of the issue, so only numbers and fixed words leave it.

## Consequences

- A run stops once it has spent `max_budget_usd`, $1.00 by default, so it costs at most that sum and its last turn, and a run that stops at its turns or its dollars posts nothing; the summary says so and names the setting to raise.
- The defaults keep the analyses as they were. A repository with many issues halves the price per token with `model = "claude-sonnet-5-5"`, and saves on the replies with `light_model`.
- A new engine must pass the rules as its system prompt, honour the turns and the budget of the task, and give `usage` the numbers of its run.
- Repositories install the workflows again to get the system prompt, the limits of each task and the line of cost; until then, they run as before.
