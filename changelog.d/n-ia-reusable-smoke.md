### Changed

- A dry run of the lifecycle or the findings, such as one that you start by hand, no longer waits for the morning sweep or another run of its workflow, and no longer takes the place of one that waits: it writes nothing, so it starts at once.
- Every job of the reusable workflows now stops with an explanation when GitHub names no commit of the workflow in the context of the job. Before, the checkout would have taken your repository at its default branch and run its own `action.yml`, or failed with a puzzling error.
- The reusable workflow of the discussions skips every job when a workflow calls it for another event than a new discussion or a run by hand, instead of failing in its job `event`.
