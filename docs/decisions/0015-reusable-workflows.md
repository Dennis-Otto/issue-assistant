# The templates call the jobs as reusable workflows of a release

- Status: accepted
- Date: 2026-10-14

## Context

[Decision 0006](0006-the-templates-exactly.md) makes every repository use the workflows exactly as the templates have them, and `check` keeps them so. The templates held every job, 50 to 200 lines each, so every change of a job was a change of a template: a new minor version, after which every repository ran `install` again. Comparable tools install with one short workflow that calls a reusable workflow of theirs, and the decision weighed only documenting the workflows against copying and checking them.

A reusable workflow runs in the repository that calls it. Three things decide whether the job of the AI can be one as well, and a spike on 2026-10-14 checked what GitHub's documentation leaves open, with a workflow that calls a reusable one at another commit of this repository ([run 38569561329](https://github.com/Dennis-Otto/issue-assistant/actions/runs/38569561329)):

- **The engine's secret.** It is a secret of the caller's environment `issue-assistant`, and the job that calls a reusable workflow can't name an environment. GitHub's documentation: a job of the reusable workflow that names the environment gets the value of a secret that the caller passes by name, also when it exists only in the environment.
- **The firewall.** The runner `ubuntu-24.04-firewall` runs the called job and enforces the caller's `.github/egress-firewall.yaml`, of the commit of the run: in the spike, the host that only the caller's policy allowed was reachable, and the one that only the policy at the commit of the called workflow allowed was blocked.
- **The variables.** GitHub's documentation: a reusable workflow sees the variables of the caller's repository, so `ISSUE_ASSISTANT_AI` still switches the AI off.

A reusable workflow can't pin the commit that it is part of, so it can't name the action of its own release with `uses: Dennis-Otto/issue-assistant@<commit>`. The context `job` knows that commit: `job.workflow_repository` and `job.workflow_sha`, which the spike found set to the called workflow's repository and commit.

## Options

1. Keep the jobs in the templates. Every change of a job stays a reinstall in every repository.
2. Move the jobs without the AI into reusable workflows and keep `analyze` in the template. That was the plan in case the secret or the firewall failed in a called job; both work.
3. Move every job into reusable workflows of this repository, one for each template, and let each job check out the issue assistant of the commit of its workflow and run the action from that checkout.
4. As option 3, with `uses: $/` for the action of the workflow's own commit, which needs no checkout. actionlint 1.7.12, the newest release, refuses that syntax and doesn't know `job.workflow_sha` either, and the Lint workflow of this repository, which the blueprint owns, runs it on every workflow.

## Decision

Option 3. The four templates keep what belongs to the repository: the triggers, the permissions that the jobs of a workflow need at most, the queue of the issue events, the engine's secrets passed by name, and one pin, `Dennis-Otto/issue-assistant/.github/workflows/reusable-<name>.yml@<commit> # vX.Y.Z`. The jobs, their conditions, runners, permissions, environment and steps are in `.github/workflows/reusable-*.yml`, which only this repository has. Each job checks out `job.workflow_repository` at `job.workflow_sha` into `.issue-assistant/action`, after the repository's checkout and inside the context folder that `.gitignore` keeps out of the repository's files, and runs the action from there. The expressions read `fromJSON(toJSON(job))`, which actionlint can't check yet.

`check` compares the templates as before, in anything but the commit hashes of their pins. The security guarantee of decision 0006 holds: the rules of the jobs live at the commit that a repository pins, which it can't change, and a job can't get more permissions than the template gives the job that calls it. `tests/test_templates.py` pins the rules on the reusable workflows and the permissions of the templates, which must be exactly what the jobs of a workflow need.

## Consequences

A release that changes no template, such as one of the jobs, the script, the prompts or the texts, reaches a repository as a pull request of Dependabot or Renovate that moves the pins; `install` runs again only after a release that changes a template, and its notes say so. A release that moves the templates to reusable workflows is one of those.

The action of a job comes from a checkout of this repository, not from the cache of actions, which costs a second or two per job. The checkout and the action are of the same commit as the jobs, so the templates and the action of one release always run together. actionlint no longer sees the runner of the AI in a repository that calls the reusable workflow, so `install` and `check` stop asking for it in `.github/actionlint.yaml`.

Once actionlint knows `$/` and `job.workflow_sha`, the jobs can take the action with `uses: $/` and drop the checkout (option 4). The Canary calls the reusable workflow as every repository does, with the permissions of the template and the engine's secrets passed by name, and the input `canary`, which gives its jobs the issue of a file and fails where a repository's run would skip the AI ([#130](https://github.com/Dennis-Otto/issue-assistant/issues/130)). So every week, and before every release that changes the jobs, it covers the path of the secret through `workflow_call` into the called job with its environment and of the variable `ISSUE_ASSISTANT_AI` into the called jobs, end to end; the spike covered the firewall and the checkout of the workflow's own commit. The Canary calls the workflow of its own repository, so that a called job sees the secrets and variables of the caller, not those of the workflow's repository, where the two differ, still rests on GitHub's documentation.

The checkout of the issue assistant lies inside the context folder, which the engine reads. It is a sparse checkout of only what the action runs (`action.yml`, the script, `version.txt`, the prompts, the texts and the templates), so the docs and tests of the issue assistant never show up in the engine's searches; a test keeps that list equal to the files that the script reads. A deny rule for `Read` of `.issue-assistant/action/**`, which Claude Code applies to `Grep` and `Glob` too, keeps the engine out of the rest, the script, the prompts of other tasks and the starter templates. The Canary runs these jobs themselves, so a pattern that leaves out a file the action needs fails before a release.
