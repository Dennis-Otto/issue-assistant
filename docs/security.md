# Security design

[← README](https://github.com/Dennis-Otto/issue-assistant) · [Architecture](architecture.md) · [Roadmap](roadmap.md)

What the issue assistant protects, what it trusts and which risks remain. [SECURITY.md](https://github.com/Dennis-Otto/issue-assistant/blob/main/SECURITY.md) says how to report a vulnerability and how to verify a release, and argues why this repository and its releases are safe. Issues come from anyone, so the design assumes that an issue tries to steer the AI.

## What you can expect

- The AI only reads: the checkout of the default branch and the context that the assistant writes for it. It runs no commands, opens no web pages and reads no files outside the checkout, on a runner whose network allows only the hosts of `.github/egress-firewall.yaml`.
- Nothing the AI writes reaches GitHub unchecked. A job that runs no AI and never sees its secret checks the answer and writes it, only to the issue of the event.
- No comment of the assistant mentions anyone, links to a site that `config.toml` doesn't allow, shows an image or HTML, or contains something that looks like a token or a key.
- The same holds for the AI's words outside its comments: in the log of the run, in its summary and in the reasons for labels and duplicates that GitHub shows on the timeline of the issue. They stay on one line there, and no answer can run a workflow command, such as `::add-mask::` or `::stop-commands::`.
- No issue can stall the assistant. It reads every text in time that grows only with the length of the text, and no more of a text than GitHub keeps, 65,536 characters.
- The secret of the engine stays in the environment `issue-assistant`, which only the default branch may use; only the job that runs the engine sees it.
- No code of a pull request runs in a workflow of the assistant.
- The jobs that write reach only GitHub, in a public repository and in a private one whose owner subscribes to StepSecurity. Every job without the AI blocks every host but `api.github.com` and `github.com` with Harden-Runner, and no step in it can use sudo or containers.
- An open finding of code scanning is named in a public log only by the number and link of its alert.
- The action stores no secrets and no data outside GitHub. Only Harden-Runner, in the jobs without the AI, reports to StepSecurity, as the residual risks say.

What the checks make of an answer that an issue steered, in a demo project:

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="images/cleaning-dark.webp">
  <img src="images/cleaning-light.webp" alt="What the apply job makes of an answer that an issue steered: a mention becomes code, a link to another site keeps only its text, an image keeps only its description, HTML is removed, unknown issue numbers become code, links into the repository and to existing issues stay links, and an answer with something like a token is refused as a whole">
</picture>

## Against PromptPwnd and Comment and Control

Two published attacks show what goes wrong when an AI in GitHub Actions reads text that anyone can write:

- **PromptPwnd**, published by [Aikido Security](https://www.aikido.dev/blog/promptpwnd-github-actions-ai-agents) in December 2025: the text of an issue or a pull request lands in the prompt of an AI agent that holds a strong token, and makes it run commands or write secrets into the issue.
- **Comment and Control**, published in April 2026, as [SecurityWeek](https://www.securityweek.com/claude-code-gemini-cli-github-copilot-agents-vulnerable-to-prompt-injection-via-comments/) reports: one pull request or comment made Claude Code Security Review, the Gemini CLI action and the GitHub Copilot agent leak their secrets into public logs or comments.

The [research note of the Cloud Security Alliance](https://labs.cloudsecurityalliance.org/research/csa-research-note-ai-github-actions-security-20260503-csa-st/) of May 2026 describes both and lists what protects a workflow. The difference to the assistant in one picture:

```mermaid
flowchart TB
  accTitle: The pattern that PromptPwnd attacks, and the assistant's
  accDescr: In the attacked pattern, the text of an issue goes into the prompt of an AI that holds a write token, a shell and the network, so a steered AI writes to GitHub or sends secrets away. In the assistant, the text of the issue goes into files that the prompt calls material; the AI reads them with a read-only token and reading tools alone, behind a firewall, and answers with JSON. A job without AI and without the engine's secret checks that answer and is the only one that writes to GitHub.
  subgraph attacked ["the attacked pattern"]
    direction TB
    issue1["issue text"] --> prompt1["in the prompt"]
    prompt1 --> agent1["<b>AI</b><br/>write token, shell,<br/>network"]
    agent1 --> github1[("GitHub")]
    agent1 --> leak(["secrets sent away"])
  end
  subgraph assistant ["the issue assistant"]
    direction TB
    issue2["issue text"] --> files["files that the prompt<br/>calls material"]
    files --> agent2["<b>AI</b><br/>read-only token, Read,<br/>Grep, Glob, firewall"]
    agent2 -- "JSON" --> apply("<b>apply</b><br/>no AI, no secret:<br/>checks, then writes")
    apply --> github2[("GitHub")]
  end
  classDef untrusted fill:#cf222e26,stroke:#cf222e
  classDef ai fill:#8250df33,stroke:#8250df
  classDef job fill:#8c959f1f,stroke:#8c959f
  classDef safe fill:#1a7f3726,stroke:#1a7f37
  class issue1,issue2,leak untrusted
  class agent1,agent2 ai
  class prompt1,files,apply job
  class github1,github2 safe
```

Each mitigation that the three sources recommend, how the assistant implements it, and the test that pins it:

| Mitigation | Recommended by | In the assistant | Pinned by |
| --- | --- | --- | --- |
| Give the agent no tool that writes to issues or pull requests, and no shell | Aikido, CSA | Claude Code runs with `--restricted` and the tools `Read`, `Grep` and `Glob` alone, with a token that only reads | `test_each_engine_can_only_read_the_checkout`, `test_only_the_analyze_job_runs_an_engine_and_only_with_read_permissions` in `tests/test_templates.py` |
| Keep untrusted text out of the prompt, or clearly apart from its instructions | Aikido, CSA | The prompt names files. The issue, its comments, the other issues and the discussions reach the AI in them, and the rules call them material to analyze, never instructions; no expression of their text stands in a script of a workflow | `test_the_prompt_keeps_the_text_of_issues_apart_from_its_instructions` in `tests/test_assistant.py`; `test_untrusted_text_never_reaches_a_shell_script` in `tests/test_templates.py` |
| Treat the output of the AI as untrusted | Aikido | `apply` checks the answer against its schema, the labels and the issues that exist, refuses it when it holds anything like a secret, and cleans every text of mentions, images, HTML and foreign links | `test_an_answer_that_breaks_the_schema_is_refused`, `test_any_answer_is_checked_or_refused`, `test_tricky_text_is_cleaned_safely` in `tests/test_assistant.py`; `tests/test_properties.py`; `fuzz/fuzz_sanitize.py` |
| Keep the AI apart from the credentials that write, and write only after its output is checked | CSA | `analyze` holds the engine's secret and a token that only reads. `apply` holds the token that writes, runs no AI, never sees the engine's secret and starts only after `analyze` | `test_the_answer_reaches_github_only_through_the_checks`, `test_only_the_analyze_job_runs_an_engine_and_only_with_read_permissions` |
| Limit what a leaked token can reach: Aikido suggests GitHub's IP allow lists, CSA monitoring on the runner | Aikido, CSA | The AI's own token only reads and ends with its job, and the AI runs behind GitHub's egress firewall with an allow list. Every job with a token that writes starts with Harden-Runner, which blocks every host but GitHub's | `test_the_firewall_template_allows_only_named_hosts`, `test_jobs_without_the_ai_reach_only_github` |
| Keep secrets out of public logs and comments | Comment and Control, CSA | The AI has no shell and reads no file outside the checkout, so its environment stays out of reach. An answer with anything like a token is refused as a whole, and no answer can run a workflow command | `test_a_token_anywhere_in_the_answer_is_refused`, `test_no_text_of_the_engine_runs_a_workflow_command` |
| Explicit, least permissions in every workflow | CSA | Each template starts with `permissions: {}`, and each job names the permissions it needs; no job may ask for an OIDC token | `test_templates_start_without_permissions_and_not_in_forks` |
| Pin every action to a full commit hash | CSA | Every action of the templates is pinned, and `check` keeps each repository's workflows equal to the templates | `test_actions_are_pinned_and_checkouts_keep_no_credentials` |
| No `pull_request_target` that runs code of the pull request | CSA | The lifecycle starts on `pull_request_target` only to learn which issues a merge fixes; it checks out the default branch and runs no code of the pull request | `test_merges_and_releases_start_the_lifecycle_without_running_pull_request_code` |
| Keys for the AI that are scoped to their environment | CSA | The engine's secret lives in the environment `issue-assistant`, which only the default branch may use, and only `analyze` gets it. How long the key lives is the choice of whoever creates it | `test_only_the_analyze_job_runs_an_engine_and_only_with_read_permissions` |
| Attack the workflow with prompt injection before it ships | CSA | Steered answers are tests: crafted examples, property tests against an independent CommonMark parser, and fuzzing on every change and every week | `test_tricky_text_is_cleaned_safely`; `tests/test_properties.py`; `fuzz/fuzz_sanitize.py` |

Two of the recommendations of the Cloud Security Alliance have nothing to apply to here: the templates sign in to no cloud, so there is no OIDC trust policy to narrow, and the assistant keeps no credential file of an AI tool in a repository.

## What is protected

| Asset | Where it lives | Protection |
| --- | --- | --- |
| The token of the engine, such as `CLAUDE_CODE_OAUTH_TOKEN` | The environment `issue-assistant` of the repository | Only the default branch may use the environment, and only the job that runs the engine gets the secret |
| The issues, labels and comments of the repository | GitHub | Written only by `apply`, `sweep`, `sync-labels` and `event`, which run no AI, with the permissions their job grants, and whose network reaches GitHub alone where Harden-Runner blocks |
| The findings of code scanning | GitHub's code scanning | Visible only to maintainers; the Findings workflow names them by number and link only |
| The workflows of the repositories that use the action | Their `.github/workflows/` | `check` fails when a workflow differs from its template in anything but the commit hashes of its actions |

## Trust boundaries

Red is what anyone can write; the numbers are those of the list below.

```mermaid
flowchart TB
  text["issue, comments,<br/>other issues, discussions"]
  merged(["merged pull request"])
  subgraph run ["issue-assistant.yml"]
    event["<b>event</b><br/>no AI"]
    subgraph firewall ["firewall, read-only token"]
      analyze["<b>analyze</b><br/>the AI with Read,<br/>Grep and Glob,<br/>the engine's secret"]
    end
    subgraph checked ["apply: no AI, no secret"]
      checks("<b>checks</b><br/>schema, labels and<br/>issues that exist; no<br/>secret, mention, image,<br/>HTML or foreign link")
      refused["nothing is posted"]
    end
  end
  text -- "1 · as data, in files" --> analyze
  text --> event
  event -- "the task" --> analyze
  analyze -- "2 · an answer in JSON" --> checks
  checks -- "fails" --> refused
  checks -- "3 · passes: only the<br/>issue of the event" --> github[("GitHub")]
  merged -- "4 · only the issues it fixes" --> lifecycle["<b>issue-lifecycle.yml</b><br/>the default branch, no AI"]
  lifecycle --> github
  classDef untrusted fill:#cf222e26,stroke:#cf222e
  classDef ai fill:#8250df33,stroke:#8250df
  classDef job fill:#8c959f1f,stroke:#8c959f
  classDef safe fill:#1a7f3726,stroke:#1a7f37
  class text,merged untrusted
  class analyze ai
  class event,lifecycle,checks,refused job
  class github safe
```

1. **Issues and comments → the assistant.** Their text is untrusted. It reaches the AI as files that the prompt calls data, not instructions, and reaches a shell script only through environment variables.
2. **The AI → the `apply` job.** The answer is untrusted. It must be JSON that fits the schema of its task, with labels that exist and numbers of issues that exist; anything that looks like a secret refuses the whole answer.
3. **The `apply` job → GitHub.** Only the issue of the event, with the permissions of its job.
4. **Pull requests → the lifecycle.** The lifecycle workflow starts on `pull_request_target` only to learn which issues a merged pull request fixes. It checks out the default branch, runs no AI and skips Dependabot's pull requests.
5. **The action → the repository that uses it.** The repository pins the action by the commit hash of a release, and the workflows grant each job only the permissions it needs.

## Where the AI's words go

The comment is not the only place that shows what the AI wrote. Each place gets its own guard, so that the words stay text wherever they appear:

```mermaid
flowchart TB
  accTitle: Where the AI's words go and how each place is guarded
  accDescr: The answer of the AI must fit its schema and hold nothing like a secret. Its texts for the comment are cleaned before they are posted. Its reasons are put on one line and cleaned, at most 280 characters, before they become a rationale on the timeline or a line of the log and the job summary; a possible security report gets a fixed rationale. The log prints them while workflow commands are turned off with a random token, and errors are escaped. The schema and the model reach the command line of the engine only in a form that adds no arguments.
  answer["the AI's answer"] --> checks("schema and<br/>secret check")
  checks -- "summary, analysis,<br/>steps, questions" --> cleaned["cleaned: no mention,<br/>image, HTML or<br/>foreign link"]
  cleaned --> posted[("comment on<br/>the issue")]
  checks -- "reasons" --> oneline["one line, then cleaned,<br/>at most 280 characters"]
  oneline -- "labels, duplicates;<br/>fixed for a security report" --> rationale[("rationale on<br/>the timeline")]
  oneline --> jobsummary[("job summary")]
  oneline --> runlog[("log of the run")]
  checks -- "refused" --> refusal["an escaped<br/>::error::"]
  refusal --> runlog
  settings["labels.toml,<br/>config.toml"] -- "schema with \u0027,<br/>model as a name" --> cli[("command line<br/>of the engine")]
  classDef untrusted fill:#cf222e26,stroke:#cf222e
  classDef job fill:#8c959f1f,stroke:#8c959f
  classDef safe fill:#1a7f3726,stroke:#1a7f37
  class answer untrusted
  class checks,cleaned,oneline,refusal,settings job
  class posted,rationale,jobsummary,runlog,cli safe
```

The log prints the lines and the comments of a dry run between `::stop-commands::` and a random token, so that the runner reads none of them as a workflow command. Only a terminal outside a runner, such as one running `check` on your own computer, shows them without the token.

## What counts as a secret

One text that looks like a token or a key refuses the whole answer: nothing is posted, and the run fails. So the check tells keys apart from the links, paths and names of code that answers are full of:

```mermaid
flowchart TB
  accTitle: How the apply job decides whether a text of the answer holds a secret
  accDescr: A known kind of token refuses the answer wherever it is. Any other text is looked at in runs of 40 or more letters, digits and the characters plus, slash, equals sign, underscore and dash, after the owner, the repository and the commit or branch of every link to GitHub are left out, unless the name of the repository or the branch has 32 or more characters. A run without lower case, upper case and digits is harmless. A run with the plus or equals sign of Base64, or whose letters read like noise, is a key, unless it is part of the path of a file that git tracks. Any other run is harmless.
  text["a text of<br/>the answer"] --> known{"a known kind<br/>of token?"}
  known -- "yes" --> refused["the answer<br/>is refused"]
  known -- "no" --> runs["runs of 40 or more<br/>letters, digits and +/=_-,<br/>without the repository<br/>of links to GitHub"]
  runs --> mixed{"lower case,<br/>upper case<br/>and digits?"}
  mixed -- "no" --> harmless["harmless"]
  mixed -- "yes" --> random{"+ or = of Base64,<br/>or letters that read<br/>like noise?"}
  random -- "no" --> harmless
  random -- "yes" --> tracked{"part of a path<br/>that git tracks?"}
  tracked -- "yes" --> harmless
  tracked -- "no" --> refused
  classDef question fill:#d4a72c33,stroke:#bf8700
  classDef job fill:#8c959f1f,stroke:#8c959f
  classDef untrusted fill:#cf222e26,stroke:#cf222e
  classDef safe fill:#1a7f3726,stroke:#1a7f37
  class known,mixed,random,tracked question
  class text,runs job
  class refused untrusted
  class harmless safe
```

- **Known kinds of tokens** count wherever they are: tokens of GitHub, Anthropic, OpenAI and Slack, access keys of AWS, JSON Web Tokens, private keys, and the addresses of Slack and Microsoft Teams webhooks, which let anyone post as their owner. A placeholder without the secret at the end of such an address, such as `https://hooks.slack.com/services/YOUR/WEBHOOK/URL`, is none.
- **Letters that read like noise** change between lower and upper case at least once in five pairs of neighbors, in runs of lower case of 3.5 letters or fewer on average. A random key, such as the 40 characters of a secret key of AWS, changes at about every second pair, in runs of two letters. Words change only where a new word starts, and run on in lower case: `src/main/java/com/example/OAuth2ClientConfiguration.java` and `custom_components/autodarts/AutodartsBoardManager2Coordinator.py` are names, not keys.
- **Links to GitHub** name owners and repositories that people chose, such as `Dennis-Otto`, and commits; the rest of the link, such as `issues/12` or the path of a file, is checked like any other text. So is a repository or a branch with a name of 32 or more characters, so that no key passes for one.
- **The files of the repository** are public, whatever their names look like, such as the test devices of a project named after random identifiers. Git lists them only for an answer that would be refused without them.

Of 134,000 paths in five large projects, Spring Security, Home Assistant, VS Code, Nextcloud and Elasticsearch, the check takes one for a key, a test device named after a random identifier, which passes in its own repository; before, it took 6,700. Of 4,000 links to issues, releases, pull requests and lines on GitHub it takes none, where it took 1,100. Of 20,000 random secret keys of AWS, it lets 36 pass instead of 23: those that lack a digit, lower case or upper case, as before, and a few whose letters happen to read like words.

## Threats and countermeasures

| Threat | Countermeasure | Evidence |
| --- | --- | --- |
| An issue tells the AI to run a command, read a secret or reach a server | The engine runs with the tools `Read`, `Grep` and `Glob` alone, with a read-only token, and the egress firewall allows only named hosts | `test_each_engine_can_only_read_the_checkout`, `test_only_the_analyze_job_runs_an_engine_and_only_with_read_permissions`, `test_the_firewall_template_allows_only_named_hosts` in `tests/test_templates.py` |
| An issue makes the AI post a mention, a link, an image or HTML | `apply` removes mentions, images, HTML and headings, and keeps only links to allowed sites and to issues that exist | `test_mentions_images_html_and_headings_are_removed`, `test_only_links_to_known_places_stay_links`, `test_tricky_text_is_cleaned_safely` in `tests/test_assistant.py`; property tests against a CommonMark parser in `tests/test_properties.py`; fuzzing with Atheris in `fuzz/fuzz_sanitize.py` |
| An issue keeps the assistant busy, so that no analysis of the repository finishes, because each one reads every issue | Each text is read up to GitHub's 65,536 characters, by patterns that look at each character a bounded number of times; the AI reads at most 20,000 characters of each text of its issue, and HTML comments are removed as GitHub hides them, also one that starts a line and never ends | `test_no_text_makes_reading_an_issue_slow`, `test_no_text_makes_the_cleaning_slow`, `test_forms_read_as_they_always_did` in `tests/test_assistant.py`; property tests against the earlier patterns in `tests/test_properties.py`; fuzzing with Atheris in `fuzz/fuzz_issue_text.py`, which fails on a reading that takes longer than half a second |
| The AI posts a secret that it found or was given | An answer with anything that looks like a token or a key is never posted, as [What counts as a secret](#what-counts-as-a-secret) shows | `test_an_answer_with_something_like_a_secret_is_never_posted`, `test_ordinary_long_words_links_and_paths_are_no_secrets`, `test_a_file_that_git_tracks_is_no_secret` in `tests/test_assistant.py`; `test_a_token_anywhere_in_the_answer_is_refused`, `test_a_path_that_git_tracks_is_never_a_secret` in `tests/test_properties.py` |
| An answer runs a workflow command in the log of the run, such as `::add-mask::`, `::stop-commands::` or a forged `::error::`, or its comment ends the code block of the preview in the summary of a dry run | Every error and notice of the assistant is escaped as GitHub's Actions toolkit escapes it, and a key of the answer that the schema doesn't know shows as a short JSON string. The summary turns workflow commands off with a random token while it prints the engine's words, and the fence of a preview is longer than any run of backticks in the comment | `test_no_text_of_the_engine_runs_a_workflow_command`, `test_a_workflow_command_keeps_its_message_on_its_line`, `test_each_summary_stops_the_commands_with_a_token_of_its_own`, `test_only_a_terminal_outside_a_runner_gets_the_summary_alone`, `test_a_preview_can_t_close_its_fence` in `tests/test_assistant.py` |
| An answer mentions someone or links elsewhere in a reason outside its comment: a line of the log and the summary, or the reason for a label or a duplicate on the timeline | Such reasons are cleaned like a comment, joined into one line before the cleaning, so that no code block keeps a mention as it is, and shortened to 280 characters before the cleaning, so that no cut loses the backtick that keeps a mention in its code | `test_the_reasons_on_the_timeline_are_cleaned_like_comments`, `test_the_engine_s_reasons_in_the_log_are_one_cleaned_line`, `test_a_shortened_line_keeps_its_cleaning` in `tests/test_assistant.py`; property tests in `tests/test_properties.py`; fuzzing with Atheris in `fuzz/fuzz_sanitize.py` |
| A label name or the model of `config.toml` adds arguments to the command line of the engine, or breaks it, so that no issue gets an analysis | The schema writes an apostrophe of a label name as `\u0027`, which ends no quotes and reads back as the same JSON. `check` and the `engine` command refuse a model that is not a name of letters, digits and `._:@/[]-`, and one that starts with a dash, which the engine's action would read as another flag | `test_the_schemas_fit_into_the_engine_arguments`, `test_a_model_that_would_add_arguments_is_refused` in `tests/test_assistant.py`; `test_a_model_that_would_add_arguments_fails_the_check_and_the_engine` in `tests/test_setup.py` |
| An answer that breaks the schema changes the wrong labels or issues | The answer is checked against the schema of its task, the labels and the issues that exist | `test_an_answer_that_breaks_the_schema_is_refused`, `test_any_answer_is_checked_or_refused` |
| Text of an issue runs as a shell command in a workflow | Every input goes through the environment; no expression of untrusted text stands in a `run:` script | `test_untrusted_text_never_reaches_a_shell_script`, `test_the_action_passes_every_input_through_the_environment` |
| A pull request runs its code with the permissions of the lifecycle | The lifecycle checks out the default branch and runs no code of the pull request | `test_merges_and_releases_start_the_lifecycle_without_running_pull_request_code` |
| A compromised action or tool in a job with a write token sends the token elsewhere | Harden-Runner blocks every host but `api.github.com` and `github.com` in every job without the AI, before any other step, and turns off sudo and containers so that no step lifts the block; the Action test runs the action behind the same block | `test_jobs_without_the_ai_reach_only_github`, `test_the_action_runs_in_ci_behind_the_block_of_the_templates` in `tests/test_templates.py` |
| A public log or timeline reveals a possible vulnerability | The Findings workflow names an open alert by number and link only, and can only dismiss alerts. The comment on a possible security report only points to private reporting, the timeline shows a fixed reason for its labels, and the prompt asks for one neutral sentence without details in its summary, analysis and reason | `test_other_findings_stay_open_and_name_only_number_and_link`, `test_the_findings_workflow_may_only_dismiss_alerts_and_runs_no_ai`, `test_a_possible_vulnerability_is_not_discussed_in_public` |
| A repository's copy of a workflow drifts into something weaker | `check` compares every workflow with its template, and the tests pin the security rules of the templates | `tests/test_templates.py` |

## Residual risks

- The AI can be wrong, or be steered into a wrong but harmless answer, such as a wrong label or a misleading analysis. The maintainer reads every issue and has the last word.
- The AI reads the whole checkout of the default branch. Whatever is in the repository may appear in its analysis, so keep secrets out of the repository, as every repository should.
- The provider of the engine receives the text of the issue and the parts of the repository that the AI reads. The notice in `SUPPORT.md` tells reporters which AI reads their issue.
- The assistant is as safe as the permissions that the workflows of a repository grant it; `check` keeps them equal to the templates.
- Harden-Runner fails open. In a private repository whose owner has no subscription or trial of StepSecurity, and when StepSecurity answers at the start of a job that its service is unavailable, Harden-Runner installs no agent, and the job runs without the block. Its write token could then reach any host, as before the block; the pins of the actions to commit hashes and jobs that run no AI and no code of an issue or a pull request stay the defense.
- StepSecurity, the maker of Harden-Runner, is a third party that receives data from every job without the AI: the repository, the run and the job at its start, and what its agent records during the job, the hosts that the steps connect to, blocked or allowed, and the processes that connect. Its insights page for the run shows them.
- The runner shows the inputs of a step in its log, and the log of a public repository's run is public, for 90 days unless the repository keeps logs for less. So the log of the `apply` job holds the whole answer of the AI, also what is never posted, such as its summary of a possible security report. The prompt asks for one neutral sentence without details there, but an AI can miss that; hiding the answer would take an artifact between the jobs. A maintainer who finds too much in a run deletes its logs.
