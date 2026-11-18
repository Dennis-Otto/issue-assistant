### Features

- A failed run of *Issue assistant* can get a second try: `[failures] workflow` of `config.toml` names a workflow of the repository, such as `bot-health.yml` of the Repo Blueprint, and `install` writes `issue-assistant.yml` with a last job that starts it with the run when a job failed at the first attempt. A stopped analysis is then posted within minutes, without running the engine again; `check` makes sure that the workflow is there and takes the run as its input `run`. Without the key nothing changes.
