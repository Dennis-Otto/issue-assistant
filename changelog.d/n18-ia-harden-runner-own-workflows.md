### Bug fixes

- The jobs of the reusable workflows no longer hang until their timeout, or fail to reach GitHub, on GitHub's runners in `westus2` when StepSecurity's service doesn't answer Harden-Runner in time: they run Harden-Runner 2.22.4, which then takes GitHub's DNS experiment out of its way as well. On the runner that `ISSUE_ASSISTANT_AI_RUNNER` names, `analyze` also makes sure itself, right after Harden-Runner, and stops at once, before the engine runs, when it can't; on `ubuntu-24.04-firewall` nothing changes. It comes with the next move of the pin, without `install`.
