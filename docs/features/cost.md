---
description: The model, the turns and the dollars that bound each run of the AI, and how the summary of a job reports what a run cost.
---

# What one run may cost

[← All features](README.md)

The [limits](limits.md) count the runs; `[engine]` in `config.toml` also bounds each one. For the task of a run, the step `engine` chooses the model, the turns and the dollars at which the engine stops, and the step of the engine passes them to its command line.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../images/diagrams/what-bounds-one-run-of-the-ai.dark.svg">
  <img src="../images/diagrams/what-bounds-one-run-of-the-ai.svg" alt="What bounds one run of the AI: The event job names the task of the run. The engine command chooses from [engine] in config.toml what the run may use: the model, or light_model for a reply to one comment; the turns, all of max_turns for an analysis or a maintainer's question, half for a follow-up and a tenth for a reply; the dollars of max_budget_usd and the effort. The context command writes the rules into rules.md and the task into prompt.md. The engine takes the rules before the task, reads the checkout and stops at its turns or its dollars, as far as it counts them; its answer goes to the job apply. For Claude, the usage command reads the numbers of the run, also after a failure, and writes one line into the summary of the job: the turns, the time, the cost and the tokens. When the run stopped at a limit, a warning names the setting.">
</picture>

| Task | Model | Turns by default | What it reads |
| --- | --- | --- | --- |
| A first analysis, `triage` | `model` | 40, all of `max_turns` | the issue, the other issues, the discussions and the repository |
| A follow-up | `model` | 20, half of `max_turns` | the same, with the reporter's answers to the questions |
| A maintainer's question, `/assistant ask` | `model` | 40, all of `max_turns` | the issue, the question and the repository |
| A reply to a maintainer's comment or after a release | `light_model`, else `model` | 4, a tenth of `max_turns`, but never fewer than 4 | the issue and the one comment that it judges, yes or no |

- **A run of Claude stops once it has spent `max_budget_usd`,** $1.00 unless you set another sum, as Claude counts it at the prices of its API. It checks the sum between its calls to the API, so a run can go past it by the cost of its last turn. Gemini stops at its turns, and Codex and Copilot at the time of the job, as [Engines](../engines.md) shows. An issue written to make the AI read on gets no further than the turns and the dollars of its task, and a run that stops there posts nothing.
- **`effort`** sets how much a model that takes it thinks, from `low`, the cheapest, to `max`; without it, the model's default runs.
- **The rules come first.** `context` writes them into `.issue-assistant/rules.md`, and the engine takes them before its task and apart from the texts of GitHub that it reads in the folder: Claude as its system prompt, Codex as its developer instructions, and Gemini and Copilot, which take no system prompt, at the top of their prompt.
- **What each run of Claude cost is one line in the summary of its job,** numbers only, also after a failure: the model, how the run ended, its turns and time, its cost against `max_budget_usd` and its tokens, such as *claude-opus-5-5 answered after 15 of 40 turns and 52 s, $0.202 of $1.00, 412,345 tokens in (398,000 of them from the cache) and 3,210 out*. A run that stopped at its turns or its dollars also gets a warning that names the setting to raise.
