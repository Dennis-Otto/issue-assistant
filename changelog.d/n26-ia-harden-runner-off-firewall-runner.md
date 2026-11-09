### Bug fixes

- On `ubuntu-24.04-firewall`, the default runner of the AI, Harden-Runner no longer starts its agent beside the engine, and each analysis there takes about 15 s less. Its step was skipped there, but GitHub still ran the step before it that installs the agent. The AI on the runner that `ISSUE_ASSISTANT_AI_RUNNER` names now runs in a job of its own, *Analyze with the AI engine on the runner in reserve*, with the same steps behind Harden-Runner, so a run shows that job as skipped beside *Analyze with the AI engine*. It comes with the next move of the pin, without `install`.
