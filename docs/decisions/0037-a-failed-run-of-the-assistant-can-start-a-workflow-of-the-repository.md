# A failed run of the assistant can start a workflow of the repository

- Status: accepted
- Date: 2026-11-18

## Context

On 2026-11-18 a user opened an issue in a repository that follows the Repo Blueprint. The run of *Issue assistant* analyzed it, and then its job *Check and post the analysis* stopped at *Make sure that Harden-Runner blocks* ([0029](0029-a-job-stops-when-harden-runner-doesnt-block.md)): the agent of Harden-Runner had taken itself away after one failed lookup. The analysis was there, but nobody posted it, and the reporter waited 44 minutes until the maintainer started the assistant by hand, which ran the engine a second time ([#468](https://github.com/Dennis-Otto/issue-assistant/issues/468)).

The blueprint has a bot for such runs: a last job of each of its bots of events starts *Bot health* with the run after a failure, and Bot health runs the failed jobs once more when Harden-Runner stopped one of them. The caller of the issue assistant can't carry that job: `check` keeps `issue-assistant.yml` equal to its template ([0006](0006-the-templates-exactly.md)), and `install` writes it anew. A variant of a template, chosen by a key of `config.toml` ([0035](0035-a-key-of-config-toml-chooses-a-variant-of-a-template.md)), allows a difference, but so far only by a boolean that is `false`.

## Options

1. Put the job into the reusable workflow, behind a key. Its caller would have to grant `actions: write` in every repository, also in those that never use it.
2. A variant of `issue-assistant.yml` with the last job, chosen by a boolean key, that starts `bot-health.yml`. It would bind every repository to the name and the input of the blueprint's bot.
3. A variant with the last job, chosen by a string key that names the workflow to start: `install` writes the name into the job, and `check` expects it there.

## Decision

Option 3, `[failures] workflow` of `config.toml`, empty by default. With a name, such as `bot-health.yml`, `install` writes the variant of `issue-assistant.yml`, whose one difference is the job `failure`, with the name in its environment, and `check` expects it, exactly, but for the pins. The name is that of a file of `.github/workflows`, without a folder: any other value stops every command, so nothing but a name reaches the YAML or the shell. `check` fails while the workflow isn't there, or doesn't start on `workflow_dispatch` with an input `run` and no other input that is required without a default.

The job is the one that the blueprint's bots of events carry. It needs the two other jobs and runs on `failure()` at the first attempt alone, so each run gets one second try; never in a fork, and never for a run by hand, whose second try would check the role of the person who ran it again. It holds `actions: write` alone, blocks every host but `api.github.com` and makes sure that the block holds, checks out nothing, and starts the workflow with the id of the run, twice more when GitHub fails the call.

This amends [0035](0035-a-key-of-config-toml-chooses-a-variant-of-a-template.md): a key chooses the variant when it differs from its default, a boolean when it is `false` and a string when it names something, and `install` may write the value of the key into the variant.

## Consequences

A second try runs the failed jobs alone. GitHub keeps the jobs that passed, with their outputs, also those inside the reusable workflow: when the failed jobs of the run of 2026-11-18 ran again, its second attempt took the event, the attachments and the analysis from the first one and ran only the job that posts, which posted nothing, since the issue had its analysis by then. So the engine isn't paid twice, and the job that posts leaves an issue alone that has the comment of its task.

What the started workflow does is the repository's. The blueprint sets the key for its repositories and teaches its sync to take the variant (Dennis-Otto/repo-blueprint#1052); a repository without the key needs no `install`.
