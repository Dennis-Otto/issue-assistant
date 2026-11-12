# The task backlog is a matrix of calls of the reusable workflow, each a run by hand of one first analysis, which sets labels only unless the maintainer asks for the comment

- Status: accepted
- Date: 2026-11-12

## Context

The assistant starts on a new issue, an edit, a comment, or by hand on one issue. A repository that starts with it has issues that were open before: they get no labels, no search for a duplicate and no question for missing information, unless a maintainer starts one run by hand for each. The Marketplace brings maintainers with such a backlog, for whom the first impression would be that nothing happened to their issues (#363).

A run of `issue-assistant.yml` is three jobs for one issue: `event` chooses the task without the AI, `analyze` runs the engine with a token that only reads, behind the firewall, and `apply`, the only job that writes, checks the answer. The answer reaches `apply` as an output of `analyze`, one per run.

## Options

1. A loop in each job over the issues of the backlog. `analyze` would run the engine once for each issue and pass every answer on, and `apply` would check them all. Every job of the three would change, the outputs of a job would carry many answers, and one answer that fails would stop the rest.
2. A run by hand for each issue, which the job `event` starts through the REST API. That needs `actions: write` for a job of every repository, and starts every analysis at once.
3. A matrix in the template: its job `assistant` chooses the issues, and a second job `backlog` calls the same reusable workflow at the same commit once for each, with the issue as an input, a few at a time.

## Decision

Option 3. Each call of the matrix is the path of one issue that the three jobs already have, with every check of a run by hand: the job `event` checks the role, and the issue again; `analyze` runs the engine as for a new issue; `apply` checks the answer and writes. No job gets a new permission, the matrix runs two calls at a time with `fail-fast` off, so that a long backlog holds up neither the runners nor the engine, and a call that fails leaves the others running.

- **The input issue is the issue after which the run starts.** The run takes the open issues by their number, up to `[engine] backlog_per_run`, 20 by default and at most 256, the most jobs of a matrix. The summary names the last one, and the next run goes on after it, so a run never needs a mark on an issue to know where it stopped.
- **Only a maintainer, and no daily limit.** A run may start many runs of the AI; only the roles `admin` and `maintain` start it, as only they may override the one-comment rule, and its runs count toward no limit of `[engine]`, which bounds the runs that others start.
- **Labels only, unless the maintainer ticks `comment`.** The reporter of an old issue may not expect a comment any more. Without one, `needs-info` and `possible-duplicate` stay off too: the sweep would remind the reporter of a question that nobody asked, or close the issue as a duplicate that nobody named. Nobody is assigned, which would notify people; the issue type and the priority are set, as labels are.
- **One table for each issue.** GitHub keeps a summary for each job, and a matrix job has no outputs of each of its calls; one table over all calls would need artifacts, and with them hosts outside GitHub's for `apply`, which reaches only GitHub's. So the job `apply` of each call writes its own row: the issue, its labels, a possible duplicate and the missing information.

## Consequences

A template with two jobs: the tests that held a template to one call hold the job `backlog` to the same pin, permissions and secrets as the job `assistant`. A repository gets the task by installing the templates of the release that brings it. An issue that got its labels without a comment has no comment of the assistant, so a run from `0` would analyze it again: the next run goes on from the number that the summary names. The runs on the backlog wait for each other in a group of their own, not for the runs of the issue that a new comment starts, and the checks of `apply` keep an issue to one analysis there too.
