"""The templates keep the AI engine read-only and every write in checked code.

Issues come from anyone. These tests fail when a change gives the job that runs an
engine a write permission, a tool that runs code or reaches the network, when an
engine has no rules here, or when untrusted text reaches a shell script.
"""

import dataclasses
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
import urllib.parse
from glob import iglob as files_matching
from pathlib import Path

import pytest
import yaml

import issue_assistant as assistant
from issue_assistant.code_scanning import keep_reported
from support import blueprint_files, package_source

ROOT = assistant.TOOL
# The workflows that install writes into a repository: the triggers, the permissions
# and the pin of a reusable workflow of this repository, which has the jobs.
TEMPLATES = assistant.TEMPLATES / assistant.WORKFLOWS
# This repository's workflows. The blueprint brings most of them, the four of the
# issue assistant too, which are the templates of the release that it pins; no test
# compares those with the templates of this commit. The reusable workflows are this
# repository's own.
OWN = ROOT / assistant.WORKFLOWS
NAMES = assistant.TEMPLATE_WORKFLOWS


def load(path):
    # PyYAML reads the key "on" as True.
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def reusable(name):
    """The reusable workflow that the template `name` calls."""
    return OWN / f"{assistant.REUSABLE_PREFIX}{name}"


ASSISTANT = load(TEMPLATES / "issue-assistant.yml")
JOBS = load(reusable("issue-assistant.yml"))["jobs"]
DISCUSSIONS = load(TEMPLATES / assistant.DISCUSSION_WORKFLOW)
DISCUSSION_JOBS = load(reusable(assistant.DISCUSSION_WORKFLOW))["jobs"]
# The templates whose jobs run an engine, with the secrets that the caller passes.
WITH_ENGINE = ("issue-assistant.yml", assistant.DISCUSSION_WORKFLOW)
ANALYZE = JOBS["analyze"]
STEPS = {step.get("id"): step for step in ANALYZE["steps"]}
ACTION = load(ROOT / "action.yml")
LIFECYCLE = load(TEMPLATES / "issue-lifecycle.yml")
FINDINGS = load(TEMPLATES / "findings.yml")
# The issue assistant in a job of a reusable workflow: the checkout of the commit of
# that workflow, in the context folder, which .gitignore keeps out of git's files.
TOOL = f"./{assistant.CONTEXT}/action"
TOOL_CHECKOUT = {
    "repository": "${{ fromJSON(toJSON(job)).workflow_repository }}",
    "ref": "${{ fromJSON(toJSON(job)).workflow_sha }}",
    "path": TOOL.removeprefix("./"),
    "sparse-checkout": "".join(
        f"/{path}\n"
        for path in (
            "action.yml",
            "issue_assistant/",
            "version.txt",
            "prompts/",
            "locales/",
            "templates/",
            "engines/",
        )
    ),
    "sparse-checkout-cone-mode": False,
    "persist-credentials": False,
}

# The runner of every job without the AI: a fixed version of Ubuntu, so that a new
# image behind ubuntu-latest never reaches the repositories untested.
RUNNER = "ubuntu-24.04"
# The runners that the Action test runs the action on, the one of the templates first.
ACTION_RUNNERS = [RUNNER, "ubuntu-26.04", "ubuntu-24.04-arm"]
HARDEN_RUNNER = "step-security/harden-runner"
# The hosts that a job without the AI reaches: the checkout and the GitHub CLI.
# Harden-Runner allows the hosts of the runner itself, such as those of the logs.
GITHUB_HOSTS = ["api.github.com:443", "github.com:443"]
# GitHub's results service, which holds the cache of the findings with the record of
# the alerts that they reported (decision 0026); Harden-Runner allows the storage of
# its files by itself, as it does for its own cache.
CACHE_HOST = "results-receiver.actions.githubusercontent.com:443"
# The prefix of the keys of the records in the cache of the findings.
FINDINGS_CACHE = "issue-assistant-findings-"
# The job of the findings that unpacks the record from the cache, without any scope
# of the token and without the issue assistant (decision 0026).
RECORD_JOB = "record"


def tool_jobs(name):
    """The jobs of a reusable workflow that run the issue assistant: all of them, but
    the job of the findings that reads their record from the cache."""
    return {
        job_name: job
        for job_name, job in load(reusable(name))["jobs"].items()
        if (name, job_name) != ("findings.yml", RECORD_JOB)
    }


# The job of the AI on the runner that the repository's variable names in place of
# GitHub's egress-firewall runner, behind Harden-Runner (decision 0019). It runs the
# steps of analyze, which YAML aliases take from there.
RESERVE = "analyze-in-reserve"
# The runners of the jobs of the AI: GitHub's egress-firewall runner, a technical
# preview, and the runner that the repository's variable names in its place.
AI_RUNNERS = {
    "analyze": assistant.FIREWALL_RUNNER,
    RESERVE: f"${{{{ vars.{assistant.RUNNER_VARIABLE} }}}}",
}
# The conditions on the variable under which each of them runs: analyze while it is
# unset or names the firewall runner, analyze-in-reserve otherwise.
ON_THE_FIREWALL_RUNNER = (
    f"(vars.{assistant.RUNNER_VARIABLE} == '' || "
    f"vars.{assistant.RUNNER_VARIABLE} == '{assistant.FIREWALL_RUNNER}')"
)
ON_ANOTHER_RUNNER = (
    f"vars.{assistant.RUNNER_VARIABLE} != '' && "
    f"vars.{assistant.RUNNER_VARIABLE} != '{assistant.FIREWALL_RUNNER}'"
)


def words(text):
    """A condition of a workflow in one line, as GitHub reads it."""
    return " ".join(text.split())


# The Canary of this repository, .github/workflows/canary.yml, calls the reusable
# workflow of the issues with the input canary, for the issue of a file.
CANARY_INPUT = "canary"
CANARY_ISSUE = "tests/fixtures/canary-issue.json"
# The Workflow test, .github/workflows/workflow-test.yml, calls it with the input
# smoke: the same issue, and the answer of a file in place of the engine's.
SMOKE_INPUT = "smoke"
SMOKE_ANSWER = "tests/fixtures/smoke-answer.json"
OWN_INPUTS = (CANARY_INPUT, SMOKE_INPUT)
# The template of the issues calls its reusable workflow once more for each issue
# that a run by hand with the task backlog chose, with the input backlog_issue, in
# its job backlog.
BACKLOG_INPUT = "backlog_issue"


def only_in_the_canary(value):
    """An expression that is `value` in a run of the Canary and empty in every
    repository's run, which never passes the input canary."""
    return f"${{{{ inputs.{CANARY_INPUT} && {value} || '' }}}}"


def claude_reads_only(step):
    inputs = step["with"]
    assert inputs["github_token"] == "${{ github.token }}"
    assert inputs["allowed_non_write_users"] == "*"
    # A bot may start a run of the Canary: the job start of canary.yml, or an app
    # that last changed its schedule. Every repository's run passes "", the
    # action's default, which lets no bot start the engine.
    assert inputs["allowed_bots"] == only_in_the_canary("'*'")
    # The task alone; the rules come before it as the system prompt.
    assert inputs["prompt"] == "${{ steps.context.outputs.task }}"
    arguments = shlex.split(inputs["claude_args"])
    assert "--restricted" in arguments
    assert arguments[arguments.index("--tools") + 1] == "Read,Grep,Glob"
    assert arguments[arguments.index("--permission-prompts") + 1] == "none"
    # The issue assistant's own checkout lies in the context folder; a deny rule
    # for Read keeps Grep and Glob out of it too, so the script, the prompts of
    # other tasks and the starter templates never mislead the analysis.
    denied = arguments[arguments.index("--disallowedTools") + 1]
    assert denied == f"Read({TOOL}/**)"
    rules = arguments[arguments.index("--append-system-prompt-file") + 1]
    assert rules == f"{assistant.CONTEXT}/rules.md"
    assert "--system-prompt" not in arguments
    assert "--system-prompt-file" not in arguments
    # What a run of the task may use, as the engine command chose it for the task.
    for flag, output in (
        ("--model", "model"),
        ("--max-turns", "turns"),
        ("--max-budget-usd", "budget"),
    ):
        assert f"{flag} ${{{{ steps.engine.outputs.{output} }}}}" in (
            inputs["claude_args"].splitlines()
        )
    # The effort only when config.toml sets one: a flag without its value would
    # take the next flag as its value.
    assert (
        "${{ steps.engine.outputs.effort && "
        "format('--effort {0}', steps.engine.outputs.effort) || '' }}"
    ) in inputs["claude_args"].splitlines()
    assert arguments[arguments.index("--json-schema") + 1] == (
        "${{ steps.context.outputs.schema }}"
    )
    for forbidden in (
        "--allowedTools",
        "--allowed-tools",
        "--dangerously-skip-permissions",
        "--permission-mode",
        "--mcp-config",
        "--add-dir",
    ):
        assert forbidden not in arguments
    assert step["env"]["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert "show_full_output" not in inputs and "display_report" not in inputs


# Every engine of the script needs its rules here: the action of its step, its
# secrets, the output with its answer, its hosts and the check that keeps it read-only.
ENGINE_RULES = {
    "claude": {
        "action": "anthropics/claude-code-action",
        "secrets": ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
        "output": "structured_output",
        "host": "api.anthropic.com",
        "reads_only": claude_reads_only,
    },
    "codex": {
        "action": "openai/codex-action",
        "secrets": ("OPENAI_API_KEY",),
        "output": "final-message",
        "host": "api.openai.com",
        "reads_only": lambda step: codex_reads_only(step),
    },
    "gemini": {
        "action": TOOL,
        "secrets": ("GEMINI_API_KEY",),
        "output": "answer",
        "host": "generativelanguage.googleapis.com",
        "reads_only": lambda step: cli_reads_only("gemini", step),
    },
    "copilot": {
        "action": TOOL,
        "secrets": ("COPILOT_GITHUB_TOKEN",),
        "output": "answer",
        "host": "api.githubcopilot.com",
        "reads_only": lambda step: cli_reads_only("copilot", step),
    },
}

# The arguments of `codex exec` that would let Codex write, run commands outside its
# sandbox, reach the web or read another configuration.
CODEX_FORBIDDEN = (
    "--sandbox",
    "-s",
    "--full-auto",
    "--dangerously-bypass-approvals-and-sandbox",
    "--yolo",
    "--search",
    "--add-dir",
    "--profile",
    "-p",
    "--cd",
    "-C",
    "--skip-git-repo-check",
)


def codex_reads_only(step):
    """Codex reads through commands in the read-only sandbox of its action, which
    lets them read files but neither write them nor reach the network."""
    inputs = step["with"]
    assert inputs["openai-api-key"] == "${{ secrets.OPENAI_API_KEY }}"
    # The action prints Codex's output into the log: JSON events alone (--json in
    # its arguments) and no log of Codex, so that no line of it is a workflow command.
    assert step["env"] == {"RUST_LOG": "off"}
    # Issues come from people without write access; no other user list.
    assert inputs["allow-users"] == "*"
    assert "allow-bots" not in inputs and "allow-bot-users" not in inputs
    assert inputs["permission-profile"] == ":read-only"
    assert "sandbox" not in inputs
    # sudo goes before Codex starts, so it can't read the key of the proxy.
    assert inputs["safety-strategy"] == "drop-sudo"
    assert "codex-user" not in inputs and "codex-home" not in inputs
    assert "responses-api-endpoint" not in inputs and "working-directory" not in inputs
    assert re.fullmatch(r"\d+\.\d+\.\d+", inputs["codex-version"])
    # The task alone; the rules come as developer instructions, before it.
    assert inputs["prompt-file"] == f"{assistant.CONTEXT}/prompt.md"
    assert inputs["output-schema-file"] == f"{assistant.CONTEXT}/schema.json"
    assert inputs["codex-args"] == "${{ steps.context.outputs.engine-args }}"
    assert inputs["model"] == "${{ steps.engine.outputs.model }}"
    assert inputs["effort"] == "${{ steps.engine.outputs.effort }}"
    assert set(inputs) == {
        "openai-api-key",
        "codex-version",
        "allow-users",
        "permission-profile",
        "safety-strategy",
        "prompt-file",
        "output-schema-file",
        "codex-args",
        "model",
        "effort",
    }
    arguments = json.loads(
        assistant.engine_arguments(assistant.ENGINES["codex"], "The rules.")
    )
    assert arguments == [
        "--json",
        "--config",
        'web_search="disabled"',
        "--config",
        'developer_instructions="The rules."',
    ]
    assert not set(arguments) & set(CODEX_FORBIDDEN)


def cli_reads_only(engine, step):
    """The Gemini CLI and the Copilot CLI run in the action, with the engine's secret
    alone and only tools that read files of the checkout."""
    cli = assistant.ENGINES[engine].cli
    assert step["uses"] == TOOL
    assert step["env"] == {
        secret: f"${{{{ secrets.{secret} }}}}"
        for secret in assistant.ENGINES[engine].credentials
    }
    # No version: analyze installs the one of the lock file in engines/.
    assert step["with"] == {
        "command": "analyze",
        "mode": "${{ needs.event.outputs.mode }}",
        "engine": engine,
    }
    run = assistant.Run("a-model", 7, "1", "high")
    command = assistant.cli_command(cli, "cli", run, "The prompt.")
    for forbidden in (
        "--yolo",
        "-y",
        "--approval-mode",
        "--allowed-tools",
        "--allow-all",
        "--allow-all-tools",
        "--allow-all-paths",
        "--allow-all-urls",
        "--allow-url",
        "--add-dir",
        "--include-directories",
        "--trust-folder",
        "--plugin-dir",
        "--extensions",
        "-e",
        "--share",
        "--share-gist",
    ):
        assert forbidden not in command, (engine, forbidden)
    if engine == "gemini":
        settings = assistant.cli_settings(cli, run)
        tools = settings["tools"]
        assert tools["core"] == ["read_file", "list_directory", "glob", "grep_search"]
        assert {"run_shell_command", "write_file", "replace", "web_fetch"} <= set(
            tools["exclude"]
        )
        assert tools["discoveryCommand"] == tools["callCommand"] == ""
        assert settings["admin"]["mcp"] == {"enabled": False}
        assert settings["experimental"]["enableAgents"] is False
        assert settings["hooksConfig"]["enabled"] is False
        assert settings["privacy"]["usageStatisticsEnabled"] is False
        assert settings["model"]["maxSessionTurns"] == 7
        assert cli.settings_variable == "GEMINI_CLI_SYSTEM_SETTINGS_PATH"
    else:
        tools = command[command.index("--available-tools") + 1]
        assert tools == "view,glob,grep"
        assert command[command.index("--allow-tool") + 1] == "read"
        assert command.count("--allow-tool") == 1
        assert "--disable-builtin-mcps" in command
        assert "--disallow-temp-dir" in command
        assert "--sync-session=off" in command
        assert "--deny-tool" not in command


def steps(path):
    # A job that calls a reusable workflow, such as the release verification, has none.
    return [
        (job_name, step)
        for job_name, job in load(path)["jobs"].items()
        for step in job.get("steps", [])
    ]


# The workflows that the blueprint keeps here, as .github/blueprint/owned.txt lists
# them. They change in repo-blueprint, whose tests hold for them, in every repository,
# the rules that the tests below check for the others: actions and called workflows
# pinned to a commit, checkouts without the token and, in a workflow that others
# start, of the default branch or a commit that main has, and values that reach a
# script only through its environment. So an update of the blueprint needs no change
# of these tests.
BLUEPRINT_WORKFLOWS = sorted(
    path
    for path in OWN.glob("*.yml")
    if path.relative_to(ROOT).as_posix() in blueprint_files()
)
# The workflows of this project alone, which the blueprint renders none of: the
# reusable workflows, which every repository calls, and those of its own checks.
PROJECT_WORKFLOWS = sorted(set(OWN.glob("*.yml")) - set(BLUEPRINT_WORKFLOWS))
# The templates and the workflows of this project alone.
WORKFLOWS = sorted(TEMPLATES.glob("*.yml")) + PROJECT_WORKFLOWS
# Every workflow, for a rule of a name of this project, such as its input canary,
# which no test of the blueprint knows.
EVERY_WORKFLOW = sorted(TEMPLATES.glob("*.yml")) + sorted(OWN.glob("*.yml"))


def workflow_id(path):
    return path.relative_to(ROOT).as_posix()


def test_the_tests_leave_the_blueprint_s_workflows_to_the_blueprint():
    """The workflows that .github/blueprint/owned.txt lists are left out of the
    tests that walk every workflow, and only those: every template and every workflow
    of this project alone stays in."""
    listed = {
        path for path in blueprint_files() if path.startswith(f"{assistant.WORKFLOWS}/")
    }
    assert {workflow_id(path) for path in BLUEPRINT_WORKFLOWS} == listed
    assert set(WORKFLOWS).isdisjoint(BLUEPRINT_WORKFLOWS)
    assert set(WORKFLOWS) | set(BLUEPRINT_WORKFLOWS) == set(EVERY_WORKFLOW)
    # The bots of the blueprint, which change with its releases, are its own.
    assert OWN / "screenshot-bot.yml" in BLUEPRINT_WORKFLOWS
    assert OWN / "issue-assistant.yml" in BLUEPRINT_WORKFLOWS
    # The templates, the reusable workflows that every repository calls and the
    # checks of this project are checked here.
    assert set(TEMPLATES.glob("*.yml")) <= set(WORKFLOWS)
    assert {path.name for path in PROJECT_WORKFLOWS} == {
        "action-test.yml",
        "canary.yml",
        "evaluate.yml",
        "release-gate.yml",
        "workflow-test.yml",
        *(reusable(name).name for name in NAMES),
    }


def test_the_templates_are_the_workflows_that_install_writes():
    assert sorted(path.name for path in TEMPLATES.iterdir()) == sorted(NAMES)
    # This repository looks after its own issues with them as well; its settings
    # answer no discussions, which need the workflow of the discussions.
    own = set(NAMES) - {assistant.DISCUSSION_WORKFLOW}
    assert own <= {path.name for path in OWN.glob("*.yml")}
    # Each one calls a reusable workflow of this repository, which nothing else
    # starts.
    assert sorted(OWN.glob(f"{assistant.REUSABLE_PREFIX}*.yml")) == sorted(
        reusable(name) for name in NAMES
    )
    for name in NAMES:
        assert list(load(reusable(name))[True]) == ["workflow_call"], name


LEVELS = ("none", "read", "write")


@pytest.mark.parametrize("name", NAMES)
def test_each_template_calls_its_reusable_workflow_with_what_its_jobs_need(name):
    """A template is the triggers and one job that calls the reusable workflow at
    the commit of a release, with the permissions that its jobs need, and no more:
    a called job gets at most the permissions of the job that calls it."""
    workflow = load(TEMPLATES / name)
    assert workflow["permissions"] == {}
    jobs = dict(workflow["jobs"])
    if name == "issue-assistant.yml":
        # The second call of the task backlog, which a test of its own checks.
        assert list(jobs) == ["assistant", "backlog"]
        del jobs["backlog"]
    ((job_name, job),) = jobs.items()
    path = f"{assistant.ACTION}/{assistant.WORKFLOWS}/{reusable(name).name}"
    uses, _, ref = job["uses"].partition("@")
    assert uses == path and assistant.SHA.fullmatch(ref), job_name
    pin = assistant.ACTION_PIN.search((TEMPLATES / name).read_text(encoding="utf-8"))
    assert pin and pin["uses"] == path
    needed: dict[str, str] = {}
    for called in load(reusable(name))["jobs"].values():
        for scope, level in called["permissions"].items():
            if LEVELS.index(level) > LEVELS.index(needed.get(scope, "none")):
                needed[scope] = level
    assert job["permissions"] == needed
    # A manual run may be a dry run; every other event runs for real.
    assert job["with"] == {"dry_run": "${{ inputs.dry_run || false }}"}
    dry_run = workflow[True]["workflow_dispatch"]["inputs"]["dry_run"]
    inputs = dict(load(reusable(name))[True]["workflow_call"]["inputs"])
    # Only the Canary of this repository passes the input canary, to the issues',
    # and only its Workflow test the input smoke, to the issues' and the
    # discussions'.
    own_inputs = {
        "issue-assistant.yml": OWN_INPUTS,
        assistant.DISCUSSION_WORKFLOW: (SMOKE_INPUT,),
    }.get(name, ())
    callers = {CANARY_INPUT: "Canary", SMOKE_INPUT: "Workflow test"}
    if name == "issue-assistant.yml":
        assert inputs.pop(BACKLOG_INPUT)["description"].startswith(
            "Only for a run by hand with the task backlog"
        )
    for own in own_inputs:
        given = inputs.pop(own)
        assert given["type"] == "boolean" and given["default"] is False
        assert given["description"].startswith(f"Only for the {callers[own]}")
    assert inputs == {
        "dry_run": {
            "description": dry_run["description"],
            "type": "boolean",
            "default": False,
        }
    }
    # The jobs of the issue assistant decide whether they run, not the template.
    assert set(job) <= {"name", "permissions", "uses", "with", "secrets"}


@pytest.mark.parametrize("name", NAMES)
def test_templates_start_without_permissions_and_not_in_forks(name):
    workflow = load(reusable(name))
    assert workflow["permissions"] == {}
    for job_name, job in workflow["jobs"].items():
        assert "permissions" in job, job_name
        # No job can sign in to a cloud: there is no OIDC token to steal.
        assert "id-token" not in job["permissions"], job_name
        assert job["timeout-minutes"] <= 20, job_name
    first = next(iter(workflow["jobs"].values()))
    assert "!github.event.repository.fork" in first["if"]


def test_only_the_job_of_the_ai_gets_the_engine_s_secrets():
    """The templates of the issues and of the discussions pass each engine's secrets
    by name; the job analyze, in the environment issue-assistant, gets their values,
    and no other job names them."""
    secrets = [secret for rules in ENGINE_RULES.values() for secret in rules["secrets"]]
    for name in WITH_ENGINE:
        declared = load(reusable(name))[True]["workflow_call"]["secrets"]
        # Each call, also those of the task backlog.
        for job in load(TEMPLATES / name)["jobs"].values():
            assert job["secrets"] == {
                secret: f"${{{{ secrets.{secret} }}}}" for secret in secrets
            }
        assert list(declared) == secrets
        assert all(declared[secret]["required"] is False for secret in secrets)
    for name in NAMES:
        if name not in WITH_ENGINE:
            (other,) = load(TEMPLATES / name)["jobs"].values()
            assert "secrets" not in other, name
            assert "secrets" not in load(reusable(name))[True]["workflow_call"]


@pytest.mark.parametrize("name", NAMES)
def test_every_job_runs_the_issue_assistant_of_its_workflow_s_commit(name):
    """A reusable workflow can't pin the commit that it is part of, so each job
    checks out the issue assistant of the commit of its workflow, which the caller
    pins, and runs the action from there: the templates and the action of one
    release, always."""
    assert f"{assistant.ACTION}@" not in reusable(name).read_text(encoding="utf-8")
    for job_name, job in tool_jobs(name).items():
        uses = [step.get("uses", "") for step in job["steps"]]
        tool = [index for index, step in enumerate(uses) if step == TOOL]
        (checkout,) = [
            index
            for index, step in enumerate(job["steps"])
            if step.get("with") == TOOL_CHECKOUT
        ]
        assert uses[checkout].startswith("actions/checkout@"), job_name
        # After the repository's checkout, which would remove it, and before the
        # first step that runs it.
        assert uses[checkout - 1].startswith("actions/checkout@"), job_name
        assert tool and checkout < tool[0], job_name


# The step before the checkouts of every job of a reusable workflow: it stops the
# job when GitHub names no commit of the workflow, where actions/checkout would take
# the caller's repository at its default branch.
GUARD = {
    "name": "Make sure that GitHub names the commit of this workflow",
    "env": {
        "WORKFLOW_REPOSITORY": TOOL_CHECKOUT["repository"],
        "WORKFLOW_SHA": TOOL_CHECKOUT["ref"],
    },
}


def guard_step():
    """The guard of the labels' job; every other job has the same."""
    return step_named(load(reusable("labels.yml"))["jobs"]["sync"], GUARD["name"])


@pytest.mark.parametrize("name", NAMES)
def test_every_job_stops_where_github_names_no_commit_of_its_workflow(name):
    """The guard comes right before the first checkout of each job, the same in
    each."""
    for job_name, job in tool_jobs(name).items():
        steps = job["steps"]
        guards = [
            index
            for index, step in enumerate(steps)
            if step.get("name") == GUARD["name"]
        ]
        checkouts = [
            index
            for index, step in enumerate(steps)
            if step.get("uses", "").startswith("actions/checkout@")
        ]
        assert len(guards) == 1, job_name
        assert guards[0] == checkouts[0] - 1, job_name
        guard = steps[guards[0]]
        assert guard == dict(GUARD, run=guard_step()["run"]), job_name


SHA_OF_A_COMMIT = "3d3c42e5aac5ba805825da76410c181273ba90b1"


@pytest.mark.parametrize(
    ("repository", "sha", "code"),
    [
        ("Dennis-Otto/issue-assistant", SHA_OF_A_COMMIT, 0),
        ("octo/app.js", SHA_OF_A_COMMIT, 0),
        # GitHub named nothing, as when a property of the context goes away.
        ("", "", 1),
        ("Dennis-Otto/issue-assistant", "", 1),
        ("", SHA_OF_A_COMMIT, 1),
        # A branch or a tag is no commit: it moves.
        ("Dennis-Otto/issue-assistant", "main", 1),
        ("Dennis-Otto/issue-assistant", "v1.2.2", 1),
        ("Dennis-Otto/issue-assistant", SHA_OF_A_COMMIT[:-1], 1),
        ("Dennis-Otto/issue-assistant", SHA_OF_A_COMMIT.upper(), 1),
        ("issue-assistant", SHA_OF_A_COMMIT, 1),
    ],
)
def test_the_guard_takes_only_a_repository_and_a_full_commit(
    tmp_path, repository, sha, code
):
    result, _ = run_step(
        guard_step(),
        tmp_path,
        WORKFLOW_REPOSITORY=repository,
        WORKFLOW_SHA=sha,
        GITHUB_REPOSITORY="octo/app",
    )
    assert result.returncode == code, result.stdout + result.stderr
    if code:
        assert result.stdout.startswith(
            "::error::GitHub gave this job no repository and commit of its reusable "
            f"workflow: job.workflow_repository is '{repository}' and "
            f"job.workflow_sha '{sha}', no full commit SHA. The checkout would take "
            "octo/app at its default branch in place of the issue assistant that "
            "the caller pins, so the job stops before it."
        )
    else:
        assert result.stdout == f"The issue assistant of {repository} at {sha}.\n"


# The commands of the action that may write, which a call's dry run must reach.
WRITERS = {"event", "apply", "sweep", "sync-labels", "findings"}


@pytest.mark.parametrize("name", NAMES)
def test_every_command_that_writes_gets_the_dry_run_of_its_call(name):
    """The Workflow test calls every reusable workflow as a dry run, with a token
    that may write on a pull request of this repository: a command that missed
    dry_run would write there, and in every repository's dry run."""
    commands = set()
    for job in load(reusable(name))["jobs"].values():
        for step in job["steps"]:
            if step.get("uses") != TOOL:
                continue
            command = step["with"]["command"]
            commands.add(command)
            if command in WRITERS:
                assert step["with"]["dry-run"] == "${{ inputs.dry_run }}", command
            else:
                assert "dry-run" not in step["with"], command
    assert commands & WRITERS


@pytest.mark.mutation_unsafe
def test_the_tool_checkout_holds_what_the_action_reads_and_nothing_more():
    """The checkout lies in the context folder, which the engine reads, so it holds
    only what the action runs: never the docs and tests of the issue assistant,
    whose texts would only mislead the engine."""
    checked_out = TOOL_CHECKOUT["sparse-checkout"].splitlines()
    read = {"/action.yml"} | {
        f"/{path}"
        for path in re.findall(r"""TOOL / ["']([^"']+)["']""", package_source())
    }
    action = (ROOT / "action.yml").read_text(encoding="utf-8")
    read |= {f"/{path}" for path in re.findall(r" -m (\S+) ", action)}
    assert {path.rstrip("/") for path in checked_out} == read
    assert all((ROOT / path.strip("/")).exists() for path in checked_out)


# Every job of the templates, as (workflow, job name, job).
TEMPLATE_JOBS = [
    (name, job_name, job)
    for name in NAMES
    for job_name, job in load(reusable(name))["jobs"].items()
]
WITHOUT_AI = [item for item in TEMPLATE_JOBS if item[1] not in AI_RUNNERS]


def template_job_id(item):
    return f"{item[0]}:{item[1]}"


@pytest.mark.parametrize("item", TEMPLATE_JOBS, ids=template_job_id)
def test_every_job_runs_on_a_fixed_version_of_ubuntu(item):
    name, job_name, job = item
    # The AI runs behind GitHub's egress firewall, whose runner has a label of its
    # own, unless the repository names another runner for it.
    expected = AI_RUNNERS.get(job_name, RUNNER)
    assert job["runs-on"] == expected, (name, job_name)


@pytest.mark.parametrize("item", WITHOUT_AI, ids=template_job_id)
def test_jobs_without_the_ai_reach_only_github(item):
    """Every job that writes blocks every host but GitHub's, before any other step.

    A compromised action or tool in a job with a write token could send it anywhere;
    Harden-Runner in block mode stops it, and without sudo and containers no later
    step can lift the block.
    """
    name, job_name, job = item
    first, *rest = job["steps"]
    action, _, ref = first["uses"].partition("@")
    assert action == HARDEN_RUNNER and assistant.SHA.fullmatch(ref), (name, job_name)
    hosts = GITHUB_HOSTS + [CACHE_HOST] * (job_name == "findings")
    if (name, job_name) == ("findings.yml", RECORD_JOB):
        hosts = [CACHE_HOST]
    assert first["with"] == {
        "egress-policy": "block",
        "disable-sudo-and-containers": True,
        "allowed-endpoints": " ".join(hosts),
    }
    assert not any(step.get("uses", "").startswith(HARDEN_RUNNER) for step in rest)


# The reusable workflows whose job analyze runs an engine, with their jobs.
JOBS_WITH_ENGINE = {
    "issue-assistant.yml": JOBS,
    assistant.DISCUSSION_WORKFLOW: DISCUSSION_JOBS,
}
# The step that makes sure that the network blocks every host outside the firewall
# policy before the AI runs, on both of its runners (decision 0025).
NETWORK_BLOCK = "Make sure that the network block holds"
EVALUATE = load(OWN / "evaluate.yml")
# The jobs that run the AI: analyze and analyze-in-reserve of both reusable
# workflows and replay of the Evaluate workflow, by workflow and job.
JOBS_WITH_THE_AI = {
    f"reusable-issue-assistant.yml:{RESERVE}": JOBS[RESERVE],
    "reusable-issue-assistant.yml:analyze": JOBS["analyze"],
    f"reusable-discussion-assistant.yml:{RESERVE}": DISCUSSION_JOBS[RESERVE],
    "reusable-discussion-assistant.yml:analyze": DISCUSSION_JOBS["analyze"],
    "evaluate.yml:replay": EVALUATE["jobs"]["replay"],
}
# The steps of analyze that run an engine, by their ids.
ENGINE_STEPS = ("claude", "codex", "gemini", "copilot")


@pytest.mark.parametrize("name", list(JOBS_WITH_ENGINE))
def test_the_ai_runs_on_another_runner_only_behind_harden_runner(name):
    """GitHub's egress-firewall runner is a technical preview. Should GitHub rename
    or end it, a repository names another runner in ISSUE_ASSISTANT_AI_RUNNER; while
    the variable is unset, every job runs as before.

    Then analyze-in-reserve runs in place of analyze, on that runner, with the same
    steps: Harden-Runner blocks every host but those of the firewall policy before
    any other step, the next step lets it see the DNS lookups, the one after it makes
    sure that Harden-Runner has an agent that blocks, the next that the network
    blocks, as it does first in analyze, and the next waits until Harden-Runner lets
    GitHub's hosts through. The job event names the hosts, and fails on a runner
    where nothing would block.
    """
    jobs = JOBS_WITH_ENGINE[name]
    analyze, reserve = jobs["analyze"], jobs[RESERVE]
    assert analyze["runs-on"] == assistant.FIREWALL_RUNNER
    assert reserve["runs-on"] == AI_RUNNERS[RESERVE]
    assert analyze["needs"] == reserve["needs"] == "event"
    # One of the two runs, by the variable, under the same other conditions.
    condition = words(analyze["if"]).removesuffix(f" && {ON_THE_FIREWALL_RUNNER}")
    assert "vars.ISSUE_ASSISTANT_AI != 'off'" in condition
    assert words(analyze["if"]) == f"{condition} && {ON_THE_FIREWALL_RUNNER}"
    assert words(reserve["if"]) == f"{condition} && {ON_ANOTHER_RUNNER}"
    for key in ("timeout-minutes", "environment", "permissions", "outputs"):
        assert reserve[key] == analyze[key], key
    block, see, blocks, holds, warm, *rest = reserve["steps"]
    action, _, ref = block["uses"].partition("@")
    assert action == HARDEN_RUNNER and assistant.SHA.fullmatch(ref)
    assert see["name"] == SEE_LOOKUPS
    assert blocks["name"] == MAKE_SURE
    # The engines need sudo, to install what isolates them.
    assert block["with"] == {
        "egress-policy": "block",
        "allowed-endpoints": "${{ needs.event.outputs.endpoints }}",
    }
    assert holds["name"] == NETWORK_BLOCK
    assert warm["name"] == WARM_UP
    # The steps of analyze, in their order, which the aliases take from there.
    assert [holds, *rest] == analyze["steps"]
    # The jobs choose the runner, so that no step needs to.
    for step in reserve["steps"]:
        assert assistant.RUNNER_VARIABLE not in str(step.get("if", "")), step
    for step in rest:
        assert not step.get("uses", "").startswith(HARDEN_RUNNER), step
    event = jobs["event"]
    assert event["steps"][-2] == {
        "name": "Name the hosts of the firewall policy for the AI's runner",
        "id": "endpoints",
        "if": ON_ANOTHER_RUNNER,
        "uses": TOOL,
        "env": {
            assistant.RUNNER_VARIABLE: f"${{{{ vars.{assistant.RUNNER_VARIABLE} }}}}"
        },
        "with": {"command": "endpoints"},
    }
    assert event["outputs"]["endpoints"] == "${{ steps.endpoints.outputs.endpoints }}"


def test_no_job_that_may_run_on_the_firewall_runner_has_a_step_of_harden_runner():
    """GitHub runs the pre step of an action under the pre-if of the action,
    always() unless the action says otherwise, and never asks the if of its step.
    Harden-Runner has no pre-if, so its pre step starts its agent in every job with
    its step, also where the condition of the step skips it. On GitHub's
    egress-firewall runner the agent watched every lookup and connection of analyze,
    the engine with its secret among them, beside the firewall that is the block
    there, and the job took about 15 s longer. So no job that may run there has the
    step, whatever its condition: analyze-in-reserve runs the AI behind
    Harden-Runner on every other runner."""
    found = set()
    for path in PROJECT_WORKFLOWS:
        for name, job in load(path)["jobs"].items():
            runners = json.dumps([job.get("runs-on"), job.get("strategy")])
            if assistant.FIREWALL_RUNNER not in runners:
                continue
            found.add((path.name, name))
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                assert not uses.startswith(f"{HARDEN_RUNNER}@"), (path.name, name)
    assert found == {
        ("action-test.yml", "firewall"),
        ("evaluate.yml", "replay"),
        ("reusable-discussion-assistant.yml", "analyze"),
        ("reusable-issue-assistant.yml", "analyze"),
    }


def gets_a_secret(step):
    """Whether the step gets a secret or runs an engine, which reaches its API."""
    return "secrets." in json.dumps(step) or step.get("id") in ENGINE_STEPS


@pytest.mark.parametrize("workflow", list(JOBS_WITH_THE_AI))
def test_the_ai_runs_only_once_the_network_block_holds(workflow):
    """GitHub's egress firewall enforces the policy only for a job that may read the
    repository, and its preview may change how it finds and reads the policy without
    a warning, while Harden-Runner fails open. Nothing else at run time would notice
    that the AI and the engine's secret run with open network access.

    So the step runs on every runner, without a condition, in each job that runs the
    AI: first, or right after the block of Harden-Runner, the step that lets it see
    the DNS lookups and the one that makes sure that it has an agent that blocks, so
    that it checks the block as the engine meets it, and before every step that gets
    a secret or runs an engine. The job goes on only when the step passes, and it may
    read the repository, which the firewall needs to enforce its policy."""
    job = JOBS_WITH_THE_AI[workflow]
    assert job["permissions"]["contents"] == "read"
    names = [step.get("name") for step in job["steps"]]
    index = names.index(NETWORK_BLOCK)
    assert set(job["steps"][index]) == {"name", "run"}
    before = job["steps"][:index]
    assert all(
        step.get("uses", "").startswith(f"{HARDEN_RUNNER}@")
        or step["name"] in (SEE_LOOKUPS, MAKE_SURE)
        for step in before
    )
    secrets = [step for step in job["steps"] if gets_a_secret(step)]
    assert secrets
    assert all(job["steps"].index(step) > index for step in secrets)
    name = workflow.partition(":")[2]
    assert job["runs-on"] == AI_RUNNERS.get(name, assistant.FIREWALL_RUNNER)
    if name in AI_RUNNERS:
        assert {step.get("id") for step in secrets} >= set(ENGINE_STEPS)


def test_every_job_that_makes_sure_of_the_block_runs_one_script():
    """So that a fix reaches every job, those of the reusable workflows above all,
    and the Action test runs the very step of every repository."""
    found = {
        (path.name, name): step
        for path in PROJECT_WORKFLOWS
        for name, job in load(path)["jobs"].items()
        for step in job.get("steps", [])
        if step.get("name") == NETWORK_BLOCK
    }
    assert set(found) == {
        ("reusable-issue-assistant.yml", "analyze"),
        ("reusable-issue-assistant.yml", RESERVE),
        ("reusable-discussion-assistant.yml", "analyze"),
        ("reusable-discussion-assistant.yml", RESERVE),
        ("evaluate.yml", "replay"),
        ("action-test.yml", "fallback"),
        ("action-test.yml", "firewall"),
    }
    assert len({json.dumps(step) for step in found.values()}) == 1


def probe_errors(run):
    """The two errors of the step: a host outside the policy got through, and the
    network reached no host."""
    errors = re.findall(r'^ *echo "::error::(.*)"$', run, re.MULTILINE)
    assert len(errors) == 2
    return errors


def test_the_error_of_the_block_names_its_likely_causes():
    """A maintainer who meets it sees what to look at, on either runner, and how to
    keep the AI from running until the block holds."""
    through, unreachable = probe_errors(
        step_named(JOBS["analyze"], NETWORK_BLOCK)["run"]
    )
    for cause in (
        "example.com",
        "contents: read",
        "mode: log",
        "preview",
        assistant.FIREWALL_RUNNER,
        assistant.RUNNER_VARIABLE,
        "StepSecurity",
        "ISSUE_ASSISTANT_AI to off",
    ):
        assert cause in through, cause
    assert unreachable.split(",")[0] == "No request to api.github.com"


@pytest.mark.parametrize("workflow", list(JOBS_WITH_THE_AI))
@pytest.mark.parametrize(
    ("blocked", "github", "code", "pauses", "error"),
    [
        # A request that fails to resolve, connect or answer in time: the block holds,
        # when the next one fails too, after a pause that a hiccup doesn't outlast.
        ([6, 6], [0], 0, 1, ""),
        ([7, 7], [0], 0, 1, ""),
        ([28, 28], [0], 0, 1, ""),
        # A connection that breaks off, as a firewall may end it.
        ([35, 56], [0], 0, 1, ""),
        # The agent of Harden-Runner was still starting: the block holds from the
        # third request on.
        ([0, 0, 7, 7], [0], 0, 3, ""),
        # In the last two requests of the minute it asks for.
        ([0] * 10 + [7, 7], [0], 0, 11, ""),
        # One failure among answers is a hiccup of the host, not the block.
        ([0, 28, 0, 28, 0, 28, 0, 28, 0, 28, 0, 28], [], 1, 12, "through"),
        ([0] * 11 + [7], [], 1, 12, "through"),
        # It never blocks, as on a runner without a block, or with a policy that
        # GitHub didn't enforce.
        ([0] * 12, [], 1, 12, "through"),
        # GitHub answers once Harden-Runner lets it through, within a minute.
        ([6, 6], [6, 6, 0], 0, 3, ""),
        ([6, 6], [7] * 11 + [0], 0, 12, ""),
        # The network reaches no host, so the failed requests prove no block.
        ([6, 6], [6] * 12, 1, 12, "unreachable"),
    ],
)
def test_the_ai_runs_only_where_the_block_holds(
    tmp_path, workflow, blocked, github, code, pauses, error
):
    """The step asks for example.com, a host of no firewall policy, for up to a
    minute, since Harden-Runner waits only about ten seconds for its agent, so its
    block can come a little after its step. Two requests in a row, five seconds
    apart, that fail to resolve, connect or answer in time count as the block. Then
    api.github.com, a host of every policy, has to answer within a minute, so that a
    network that reaches no host isn't taken for the block; otherwise the job stops
    before the AI."""
    (holds,) = [
        step
        for step in JOBS_WITH_THE_AI[workflow]["steps"]
        if step.get("name") == NETWORK_BLOCK
    ]
    policy = assistant.allowed_hosts(
        (assistant.TEMPLATES / assistant.FIREWALL).read_text(encoding="utf-8")
    )
    assert "example.com" not in policy
    assert "example.com" not in assistant.FIREWALL_DEFAULT_HOSTS
    assert {"api.github.com"} <= set(policy)
    folder = tmp_path / "bin"
    folder.mkdir()
    arguments = tmp_path / "arguments"
    slept = tmp_path / "slept"
    answers = tmp_path / "answers"
    answers.mkdir()
    for host, codes in (("example.com", blocked), ("api.github.com", github)):
        (answers / host).write_text(
            "".join(f"{answer}\n" for answer in codes), encoding="utf-8"
        )
    # Each request to a host takes its next answer; one too many fails the test.
    (folder / "curl").write_text(
        f'#!/bin/sh\necho "$@" >> "{arguments.as_posix()}"\n'
        "for url; do :; done\nhost=${url#https://}\nhost=${host%/}\n"
        f'count=$(grep -c "https://$host" "{arguments.as_posix()}")\n'
        f'exit "$(sed -n "${{count}}p" "{answers.as_posix()}/$host")"\n',
        encoding="utf-8",
        newline="\n",
    )
    (folder / "sleep").write_text(
        f'#!/bin/sh\necho "$@" >> "{slept.as_posix()}"\n',
        encoding="utf-8",
        newline="\n",
    )
    (folder / "curl").chmod(0o755)
    (folder / "sleep").chmod(0o755)
    result, _ = run_step(
        holds, tmp_path, PATH=f"{folder}{os.pathsep}{os.environ['PATH']}"
    )
    assert result.returncode == code, result.stderr
    assert arguments.read_text(encoding="utf-8").splitlines() == [
        "--silent --max-time 10 --output /dev/null https://example.com"
    ] * len(blocked) + [
        "--silent --max-time 10 --output /dev/null https://api.github.com/"
    ] * len(github)
    assert (slept.read_text(encoding="utf-8").split() if slept.exists() else []) == [
        "5"
    ] * pauses
    through, unreachable = probe_errors(holds["run"])
    if error:
        message = through if error == "through" else unreachable
        assert result.stdout == f"::error::{message}\n"
    else:
        assert result.stdout == (
            "The network blocks example.com, a host outside "
            ".github/egress-firewall.yaml, and lets api.github.com through.\n"
        )


# The first version that leaves the hosts of allowed-endpoints open on the runners
# with GitHub's DNS experiment, seen in westus2, also when its feature flags don't
# come: the agent sees none of the addresses that they resolve to, so it drops them,
# a checkout fails, and the job never finishes after its last step, at "Cleaning up
# orphan processes", until its timeout. v2.22.3 took the experiment away only when
# its flags came within 3 s, and a 429 of StepSecurity's API left it there
# (homedarts-for-autodarts#586). https://github.com/step-security/harden-runner/issues/708
HARDEN_RUNNER_FIXED = (2, 22, 4)


@pytest.mark.parametrize("path", PROJECT_WORKFLOWS, ids=lambda path: path.name)
def test_harden_runner_is_a_version_that_lets_the_allowed_hosts_through(path):
    """An older pin drops the allowed hosts, or hangs the job, on the runners in
    westus2. The jobs that the reusable workflows run in every repository, and those
    of the tests here, must not wait for a timeout there. Decision 0123 of the
    blueprint takes the same version for its workflows."""
    text = path.read_text(encoding="utf-8")
    uses = re.findall(rf"{HARDEN_RUNNER}@.*", text)
    pins = [
        re.fullmatch(rf"{HARDEN_RUNNER}@[0-9a-f]{{40}} # v(\d+)\.(\d+)\.(\d+)", line)
        for line in uses
    ]
    assert pins and all(pins), uses
    for pin in pins:
        assert tuple(int(part) for part in pin.groups()) >= HARDEN_RUNNER_FIXED, uses


WAIT_FOR_DOCKER = "Wait until Harden-Runner has removed Docker"
WARM_UP = "Wait until Harden-Runner lets the hosts through"
SEE_LOOKUPS = "Let Harden-Runner see the DNS lookups"
# The step that stops a job that blocks when Harden-Runner has no agent that blocks.
MAKE_SURE = "Make sure that Harden-Runner blocks"
BLOCKS_GUARD = "steps.blocks.outcome != 'failure'"
# In a job that keeps sudo, the step that lets Harden-Runner see the lookups comes
# first; when it fails, the step is skipped and nothing made sure of the block.
BLOCKS_PASSED = "steps.blocks.outcome == 'success'"
# The jobs that block with the hosts of the firewall policy, as the AI does on the
# runner of ISSUE_ASSISTANT_AI_RUNNER.
WITH_THE_FIREWALL_POLICY = {
    ("action-test.yml", "fallback"),
    ("reusable-discussion-assistant.yml", RESERVE),
    ("reusable-issue-assistant.yml", RESERVE),
}
HARDENED_JOBS = [
    (path.name, name, job, step)
    for path in PROJECT_WORKFLOWS
    for name, job in load(path)["jobs"].items()
    for step in job.get("steps", [])
    if step.get("uses", "").startswith(f"{HARDEN_RUNNER}@")
]


def hardened_job_id(item):
    return f"{item[0]}-{item[1]}"


def step_named(job, name):
    (step,) = [step for step in job["steps"] if step.get("name") == name]
    return step


def test_the_jobs_behind_harden_runner_are_found():
    found = {(workflow, name) for workflow, name, _, _ in HARDENED_JOBS}
    # Those that every repository runs, and those of the tests here.
    assert {
        ("reusable-findings.yml", "findings"),
        ("reusable-issue-assistant.yml", "event"),
        ("reusable-issue-assistant.yml", "apply"),
        ("reusable-discussion-assistant.yml", "event"),
        ("reusable-discussion-assistant.yml", "apply"),
        ("reusable-issue-lifecycle.yml", "sweep"),
        ("reusable-labels.yml", "sync"),
        ("action-test.yml", "egress"),
        ("canary.yml", "start"),
        ("canary.yml", "queue"),
        ("canary.yml", "decide"),
        ("release-gate.yml", "canary"),
        ("workflow-test.yml", "changes"),
        ("workflow-test.yml", "gate"),
    } <= found
    assert found >= WITH_THE_FIREWALL_POLICY


@pytest.mark.parametrize("item", HARDENED_JOBS, ids=hardened_job_id)
def test_every_job_without_docker_waits_until_harden_runner_has_removed_it(item):
    """Harden-Runner removes Docker with apt-get in the background, and a job that
    ends before it finishes hangs in "Cleaning up orphan processes" until its timeout,
    which a short job of a reusable workflow would reach in every repository."""
    workflow, name, job, block = item
    names = [step.get("name") for step in job["steps"]]
    if not block["with"].get("disable-sudo-and-containers"):
        assert WAIT_FOR_DOCKER not in names, (workflow, name)
        return
    wait = step_named(job, WAIT_FOR_DOCKER)
    assert wait["if"] == "always()", (workflow, name)
    # It ends the job: the explanation of a failure runs before it, since it needs the
    # checkout and takes a moment that the removal may use.
    assert job["steps"][-1] is wait, (workflow, name)


@pytest.mark.parametrize("item", HARDENED_JOBS, ids=hardened_job_id)
def test_every_job_that_blocks_waits_until_harden_runner_lets_its_hosts_through(item):
    """An address that the runner asked for past Harden-Runner stays in the cache of
    systemd-resolved for up to a minute, and Harden-Runner blocks it until then, so
    the first step that needs a host would fail, and the job could hang at its end:
    https://github.com/step-security/harden-runner/issues/708"""
    workflow, name, job, block = item
    names = [step.get("name") for step in job["steps"]]
    if block["with"]["egress-policy"] != "block":
        assert WARM_UP not in names, (workflow, name)
        return
    warm = step_named(job, WARM_UP)
    index = job["steps"].index(warm)
    # Once Harden-Runner sees the lookups, in a job that keeps sudo, and once a step
    # made sure that it blocks.
    first = job["steps"].index(block) + 1 + (SEE_LOOKUPS in names) + 1
    if (workflow, name) in WITH_THE_FIREWALL_POLICY:
        # After the step that makes sure of the block, since a request that gets
        # through before it holds says nothing. Only GitHub's hosts: the runner asked
        # for no other, and the policy names hosts of engines that this repository may
        # not use, and one that answers only on port 80, each of which would stop the
        # AI for two minutes.
        assert index == first + 1, name
        assert warm["env"] == {"HOSTS": " ".join(GITHUB_HOSTS)}, name
    else:
        # Before every step that could need a host, and for just the hosts that the
        # job allows, by the same words, so that they can't drift apart.
        assert index == first, (workflow, name)
        assert warm["env"] == {"HOSTS": block["with"]["allowed-endpoints"]}, name
    # It runs just where the block does.
    assert warm.get("if") == block.get("if"), (workflow, name)
    # Two minutes, and a last try of 5 s for each host, of a job of 5 minutes.
    assert warm["timeout-minutes"] == 4, (workflow, name)


# What a step's condition holds when it runs only after a step that comes later: one
# that the step "Make sure that Harden-Runner blocks" stopped the job before, so that
# its outcome is skipped and its outputs are empty.
FOLLOWS = re.compile(
    r"steps\.([\w-]+)\.(?:outcome == '(?:success|failure)'"
    r"|outputs\.[\w-]+ (?:!= ''|== '[^']+'))"
)


@pytest.mark.parametrize("item", HARDENED_JOBS, ids=hardened_job_id)
def test_every_job_that_blocks_stops_when_harden_runner_doesnt(item):
    """Harden-Runner goes on without its agent when the agent can't be downloaded, as
    in GitHub's outage of 8 November 2026, when StepSecurity says that it is
    unavailable, or when the agent doesn't start: a job that blocks would then reach
    every host with its token, in every repository that calls the reusable
    workflows. So each one makes sure that the agent runs, right after Harden-Runner
    and the step that lets it see the lookups of DNS, before every step that could
    need a host or get a secret: the warm-up, or the check of the network block before
    the AI (decision 0029, as decision 0134 of the blueprint has its jobs do).

    The steps that run after a failure, such as the explanation of the PR coach, a
    checkout for it or the cache of the findings, would run without the block too:
    each has the guard, but the wait for the removal of Docker, which needs no host,
    and a step that only follows a later one, which didn't run. Where the step that
    lets Harden-Runner see the lookups comes first, the guard asks for the step's
    success: when that one fails, the step is skipped, and nothing made sure of the
    block."""
    workflow, name, job, block = item
    steps = job["steps"]
    names = [step.get("name") for step in steps]
    if block["with"]["egress-policy"] != "block":
        assert MAKE_SURE not in names, (workflow, name)
        return
    assert names.count(MAKE_SURE) == 1, (workflow, name)
    index = steps.index(block) + 1 + (SEE_LOOKUPS in names)
    check = steps[index]
    assert check["name"] == MAKE_SURE, (workflow, name)
    next_step = (
        NETWORK_BLOCK if (workflow, name) in WITH_THE_FIREWALL_POLICY else WARM_UP
    )
    assert names[index + 1] == next_step, (workflow, name)
    # A script of its own that needs no host, no secret and no token, wherever the
    # block runs, and half a minute for an agent that is still starting.
    assert set(check) == {"name", "id", "timeout-minutes", "run"}, (workflow, name)
    assert check["id"] == "blocks", (workflow, name)
    assert check["timeout-minutes"] == 1, (workflow, name)
    later = steps[index + 1 :]
    later_ids = {step.get("id") for step in later} - {None}
    for step in later:
        condition = " ".join(str(step.get("if", "")).split())
        if not re.search(r"always\(\)|failure\(\)|cancelled\(\)", condition):
            continue
        if step.get("name") == WAIT_FOR_DOCKER:
            # Else the job would hang until its timeout.
            assert condition == "always()", (workflow, name)
            continue
        if set(FOLLOWS.findall(condition)) & later_ids and "||" not in condition:
            continue
        guard = BLOCKS_PASSED if SEE_LOOKUPS in names else BLOCKS_GUARD
        assert guard in condition, (workflow, name, step.get("name"))


def test_the_jobs_that_make_sure_that_harden_runner_blocks_are_found():
    """Every job of this project that blocks, those of the reusable workflows above
    all, which hold a token that writes in every repository."""
    found = {
        (workflow, name)
        for workflow, name, job, _ in HARDENED_JOBS
        if MAKE_SURE in [step.get("name") for step in job["steps"]]
    }
    assert found == {
        (workflow, name)
        for workflow, name, _, block in HARDENED_JOBS
        if block["with"]["egress-policy"] == "block"
    }
    assert len(found) == 22


def test_the_step_that_makes_sure_that_harden_runner_blocks_is_the_blueprint_s():
    """The jobs of this project run the very script of the blueprint's workflows, whose
    tests run it in every case, so that a fix of the blueprint reaches them: an update
    of the blueprint that changes it fails here until the copies follow."""
    blueprint = {
        step["run"]
        for path in BLUEPRINT_WORKFLOWS
        for job in load(path)["jobs"].values()
        for step in job.get("steps", [])
        if step.get("name") == MAKE_SURE
    }
    own = {
        step["run"]
        for _, _, job, _ in HARDENED_JOBS
        for step in job["steps"]
        if step.get("name") == MAKE_SURE
    }
    assert len(blueprint) == 1
    assert own == blueprint, (
        f"The blueprint changed the script of its step '{MAKE_SURE}': copy its run into"
        " that step of every job of this project's workflows that blocks (decision"
        " 0029). The fix bot can't change workflows."
    )


def test_a_check_whose_job_blocks_explains_a_harden_runner_that_doesnt():
    """The PR coach shows the explanation of a check that failed, also one that the
    step stopped, as the coach of the Workflow test does: it says what that means, as
    those of .github/checks.toml do for the jobs of the blueprint."""
    catalog = tomllib.loads(
        (ROOT / ".github" / "checks.project.toml").read_text(encoding="utf-8")
    )["check"]
    checks = set()
    for _, _, job, _ in HARDENED_JOBS:
        names = [step.get("name") for step in job["steps"]]
        if MAKE_SURE not in names or "Explain the failure" not in names:
            continue
        check = step_named(job, "Explain the failure")["env"]["CHECK"]
        # The legs of a matrix share the explanation of the first.
        if check == "${{ matrix.check }}":
            check = job["strategy"]["matrix"]["include"][0]["check"]
        checks.add(check)
    assert len(checks) == 9
    for check in checks:
        assert (
            "When the step *Make sure that Harden-Runner blocks* stopped the job"
            in catalog[check]["problem"]
        ), check


def make_sure(tmp_path, files, active=True, starts_after=0, installed=True):
    """The step with the folder of the agent in a folder of its own, which
    Harden-Runner made unless it didn't install the agent, and a systemctl that finds
    the service of the agent active or not; with starts_after, the agent writes its
    status at that call of sleep. The result, and the calls."""
    runner = tmp_path / "runner"
    if installed:
        (runner / "home/agent").mkdir(parents=True)
    for name, content in files.items():
        (runner / name).write_text(content, encoding="utf-8")
    script = step_named(JOBS["event"], MAKE_SURE)["run"].replace(
        "/home/agent/", f"{runner.as_posix()}/home/agent/"
    )
    status = f"{runner.as_posix()}/home/agent/agent.status"
    starts = (
        f'[ "$n" -lt {starts_after} ] || printf Initialized > "{status}"'
        if starts_after
        else ""
    )
    path, calls = stand_ins(
        tmp_path,
        systemctl="exit 0" if active else "exit 3",
        sleep=f"{count_up(tmp_path / 'sleeps')}\n{starts}",
    )
    result, _ = run_step({"run": script}, tmp_path, PATH=path)
    return result, calls.read_text(encoding="utf-8").splitlines()


AGENT = {"home/agent/agent": ""}
INITIALIZED = {**AGENT, "home/agent/agent.status": "Initialized"}


def test_the_step_passes_at_once_when_the_agent_blocks(tmp_path):
    result, calls = make_sure(tmp_path, INITIALIZED)
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "Harden-Runner blocks every host that the job doesn't name.\n"
    )
    assert calls == ["systemctl is-active --quiet agent"]


def test_the_step_waits_for_an_agent_that_starts_late(tmp_path):
    # Harden-Runner waits only about ten seconds for its agent before it goes on.
    result, calls = make_sure(tmp_path, AGENT, starts_after=3)
    assert result.returncode == 0, result.stderr
    assert calls.count("sleep 1") == 3


@pytest.mark.parametrize(
    ("files", "installed", "active", "why", "sleeps"),
    [
        # StepSecurity said that it is unavailable, or the repository skips it.
        ({}, False, True, "didn't install its agent", 0),
        # The download failed, as in GitHub's outage: nothing comes, so no wait.
        ({}, True, True, "couldn't download its agent", 0),
        (
            {**AGENT, "home/agent/agent.status": "Error reading config file"},
            True,
            True,
            "has an agent that didn't start",
            0,
        ),
        # Half a minute for an agent that is there and doesn't start.
        (AGENT, True, True, "has an agent that didn't start", 30),
        (INITIALIZED, True, False, "has an agent that stopped", 0),
    ],
)
def test_the_step_stops_the_job_when_harden_runner_doesnt_block(
    tmp_path, files, installed, active, why, sleeps
):
    result, calls = make_sure(tmp_path, files, active=active, installed=installed)
    assert result.returncode == 1
    assert calls.count("sleep 1") == sleeps
    assert (
        f"::error title=Harden-Runner doesn't block::Harden-Runner {why}"
        in result.stdout
    )
    assert "would reach every host, and it stops here instead" in result.stdout
    assert "The status of the agent: " in result.stdout


# The jobs that block and keep sudo: analyze-in-reserve, whose engines need it to
# install what isolates them, its runner in reserve in the Action test, and the
# engine contracts.
KEEPS_SUDO = WITH_THE_FIREWALL_POLICY | {("action-test.yml", "engines")}


@pytest.mark.parametrize("item", HARDENED_JOBS, ids=hardened_job_id)
def test_every_job_that_blocks_and_keeps_sudo_lets_harden_runner_see_its_lookups(
    item,
):
    """GitHub's DNS experiment on some of its runners hides the lookups of a job from
    Harden-Runner, which then blocks the hosts of the job until its time limit.
    Harden-Runner takes it away unless its feature flags say no; a job that keeps sudo
    makes sure, right after it, as decision 0123 of the blueprint has the jobs of its
    workflows do. A job without sudo can't, and one that only records its traffic
    drops nothing. https://github.com/step-security/harden-runner/issues/708

    The step runs just where Harden-Runner does: never on GitHub's egress-firewall
    runner, whose own firewall is the block there, and where no job has a step of
    Harden-Runner."""
    workflow, name, job, block = item
    names = [step.get("name") for step in job["steps"]]
    settings = block["with"]
    if settings["egress-policy"] != "block" or settings.get(
        "disable-sudo-and-containers"
    ):
        assert SEE_LOOKUPS not in names, (workflow, name)
        return
    assert (workflow, name) in KEEPS_SUDO
    see = job["steps"][job["steps"].index(block) + 1]
    assert see["name"] == SEE_LOOKUPS, (workflow, name)
    assert names.count(SEE_LOOKUPS) == 1, (workflow, name)
    assert see.get("if") == block.get("if"), (workflow, name)
    # A script of its own, with no secret and no token.
    assert set(see) <= {"name", "if", "run"}, (workflow, name)


def test_the_jobs_that_keep_sudo_behind_the_block_are_found():
    found = {
        (workflow, name)
        for workflow, name, job, _ in HARDENED_JOBS
        if SEE_LOOKUPS in [step.get("name") for step in job["steps"]]
    }
    assert found == KEEPS_SUDO


def test_the_wait_for_docker_and_the_warm_up_are_one_script_each():
    """So that a fix reaches every job, those of the reusable workflows above all."""
    for step_name in (WAIT_FOR_DOCKER, WARM_UP, SEE_LOOKUPS):
        scripts = {
            step["run"]
            for _, _, job, _ in HARDENED_JOBS
            for step in job["steps"]
            if step.get("name") == step_name
        }
        assert len(scripts) == 1, step_name


def test_the_firewall_policy_allows_the_hosts_of_the_warm_up():
    """A host that the job doesn't allow would hold it up for two minutes."""
    endpoints = assistant.firewall_endpoints(assistant.TEMPLATES).split()
    assert set(GITHUB_HOSTS) <= set(endpoints)


def stand_ins(tmp_path, **scripts):
    """A PATH with a script for each command, and the file that lists their calls."""
    folder = tmp_path / "bin"
    folder.mkdir()
    calls = tmp_path / "calls"
    calls.touch()
    for command, script in scripts.items():
        path = folder / command
        path.write_text(
            f'#!/bin/sh\necho "{command} $*" >> "{calls.as_posix()}"\n{script}\n',
            encoding="utf-8",
            newline="\n",
        )
        path.chmod(0o755)
    return f"{folder}{os.pathsep}{os.environ['PATH']}", calls


def count_up(counter):
    """The lines of a stand-in that count its calls in the file `counter`, in `$n`."""
    counter.write_text("0", encoding="utf-8")
    path = counter.as_posix()
    return f'n=$(( $(cat "{path}") + 1 ))\necho "$n" > "{path}"'


@pytest.mark.parametrize(("busy", "sleeps"), [(0, 0), (3, 3), (200, 120)])
def test_the_wait_for_docker_ends_once_apt_get_is_done_or_after_two_minutes(
    tmp_path, busy, sleeps
):
    # pgrep finds apt-get the first times it is asked.
    path, calls = stand_ins(
        tmp_path,
        pgrep=f'{count_up(tmp_path / "count")}\n[ "$n" -le {busy} ]',
        sleep="",
    )
    result, _ = run_step(
        step_named(JOBS["event"], WAIT_FOR_DOCKER), tmp_path, PATH=path
    )
    assert result.returncode == 0, result.stderr
    asked = calls.read_text(encoding="utf-8").splitlines()
    assert asked.count("sleep 1") == sleeps
    assert set(asked) <= {"pgrep -x apt-get|dpkg", "sleep 1"}
    assert ("::warning::Harden-Runner still removes Docker" in result.stdout) == (
        busy > 120
    )


def warm_up(tmp_path, blocked):
    """The warm-up, where curl fails the first `blocked` times that it is asked, and
    each look at the clock takes 3 s; with what it printed, and the calls."""
    clock = tmp_path / "clock"
    clock.write_text("1000", encoding="utf-8")
    path, calls = stand_ins(
        tmp_path,
        curl=f'{count_up(tmp_path / "tries")}\n[ "$n" -gt {blocked} ]',
        date=(
            f'now=$(cat "{clock.as_posix()}")\n'
            f'echo "$((now + 3))" > "{clock.as_posix()}"\n'
            'echo "$now"'
        ),
        sleep="",
    )
    result, _ = run_step(
        step_named(JOBS["event"], WARM_UP),
        tmp_path,
        PATH=path,
        HOSTS="api.github.com:443 *.actions.githubusercontent.com:443 github.com:443",
    )
    assert result.returncode == 0, result.stderr
    return result.stdout, calls.read_text(encoding="utf-8").splitlines()


PROBE = "curl --silent --head --connect-timeout 3 --max-time 5 --output /dev/null "


def see_lookups(tmp_path, files, fails="false"):
    """The step of analyze with the files of the runner in a folder of their own, and
    a sudo that fails for the commands that the condition `fails` matches; with the
    result, the calls and the folder."""
    runner = tmp_path / "runner"
    for name, content in files.items():
        (runner / name).parent.mkdir(parents=True, exist_ok=True)
        (runner / name).write_text(content, encoding="utf-8")
    root = runner.as_posix()
    script = re.sub(
        r"(?<![\w/])/(etc|run)/",
        lambda found: root + found[0],
        step_named(JOBS[RESERVE], SEE_LOOKUPS)["run"],
    )
    path, calls = stand_ins(tmp_path, sudo=f"if {fails}; then exit 1; fi")
    result, _ = run_step({"run": script}, tmp_path, PATH=path)
    return result, calls.read_text(encoding="utf-8").splitlines(), root


def test_the_dns_step_does_nothing_on_a_runner_without_github_s_experiment(tmp_path):
    result, calls, _ = see_lookups(
        tmp_path, {"etc/resolv.conf": "nameserver 127.0.0.53\n"}
    )
    assert result.returncode == 0, result.stderr
    assert calls == []
    assert "::notice::" not in result.stdout


@pytest.mark.parametrize(
    "files",
    [
        {"etc/systemd/resolved.conf.d/90-egress-proxy.conf": "[Resolve]\n"},
        {"run/systemd/resolved.conf.d/90-egress-proxy.conf": "[Resolve]\n"},
        {"etc/resolv.conf": "nameserver 127.0.0.100\n"},
    ],
    ids=["etc", "run", "resolver"],
)
def test_the_dns_step_takes_github_s_experiment_away(tmp_path, files):
    # The services of the experiment may be gone already, which fails nothing.
    result, calls, root = see_lookups(
        tmp_path,
        files,
        '[ "$1 $2" = "systemctl stop" ] || [ "$1" = resolvectl ]',
    )
    assert result.returncode == 0, result.stderr
    dropin = "systemd/resolved.conf.d/90-egress-proxy.conf"
    stub = f"{root}/run/systemd/resolve/stub-resolv.conf"
    assert calls == [
        f"sudo rm -f {root}/etc/{dropin} {root}/run/{dropin}",
        "sudo systemctl restart systemd-resolved",
        f"sudo ln -sfn {stub} {root}/etc/resolv.conf",
        "sudo systemctl stop egress-dns-resolver.service egress-outbound-proxy.service",
        "sudo resolvectl flush-caches",
    ]
    assert "::notice::GitHub's DNS experiment is on this runner" in result.stdout


def test_the_dns_step_fails_when_it_cant_take_the_experiment_away(tmp_path):
    # Harden-Runner would block the hosts of the job until its time limit; the failure
    # says so at once, and the engine doesn't run.
    result, calls, _ = see_lookups(
        tmp_path,
        {"etc/resolv.conf": "nameserver 127.0.0.100\n"},
        '[ "$1" = systemctl ]',
    )
    assert result.returncode != 0
    assert calls[-1] == "sudo systemctl restart systemd-resolved"


def test_the_warm_up_asks_each_host_once_when_harden_runner_lets_it_through(tmp_path):
    output, calls = warm_up(tmp_path, 0)
    # The wildcard names no host to ask.
    assert [call for call in calls if call.startswith("curl")] == [
        f"{PROBE}https://api.github.com/",
        f"{PROBE}https://github.com/",
    ]
    assert not any(call.startswith("sleep") for call in calls)
    assert "::warning::" not in output


def test_the_warm_up_asks_again_until_harden_runner_lets_the_host_through(tmp_path):
    output, calls = warm_up(tmp_path, 3)
    assert calls.count(f"{PROBE}https://api.github.com/") == 4
    assert calls.count(f"{PROBE}https://github.com/") == 1
    assert calls.count("sleep 3") == 3
    assert output.count("Harden-Runner still blocks api.github.com") == 3
    assert "::warning::" not in output


def test_the_warm_up_warns_after_two_minutes_and_leaves_the_failure_to_the_step(
    tmp_path,
):
    # A host that doesn't answer may be one that this run doesn't need.
    output, calls = warm_up(tmp_path, 10_000)
    # At most 40 tries of 3 s for the first host, and then one for each other host.
    assert calls.count(f"{PROBE}https://api.github.com/") == 40
    assert calls.count(f"{PROBE}https://github.com/") == 1
    assert calls.count("sleep 3") == 39
    for host in ("api.github.com", "github.com"):
        assert (
            f"::warning::Harden-Runner still blocks {host} after two minutes" in output
        )


def test_the_action_runs_in_ci_behind_the_block_of_the_templates():
    """The Action test runs the action behind the egress block of the templates.

    So a new host that the action or the GitHub CLI needs fails a check here, before
    a release blocks it in every repository.
    """
    jobs = load(OWN / "action-test.yml")["jobs"]
    (job,) = [
        job
        for name, job in jobs.items()
        if job["steps"][0].get("uses", "").startswith(f"{HARDEN_RUNNER}@")
        and job["steps"][0]["with"]["egress-policy"] == "block"
        # The jobs of the AI's runner in reserve run no command that reads GitHub,
        # the engine contracts none of the action's (tests/test_engine_contract.py), and
        # the refresh of the copies of GitHub's schemas only its own script.
        and not name.startswith("fallback")
        and name not in {"engines", "schema-refresh"}
    ]
    assert job["runs-on"] == "${{ matrix.runner }}"
    assert job["steps"][0] == JOBS["event"]["steps"][0]
    runs = [step["with"] for step in job["steps"] if step.get("uses") == "./"]
    # Dry runs of the commands that read the most of GitHub, which only read.
    assert runs == [
        {"command": "sweep", "dry-run": True},
        {"command": "sync-labels", "dry-run": True},
    ]
    # And context, which only reads, for a real issue among issues and discussions.
    (context,) = [step for step in job["steps"] if " context " in step.get("run", "")]
    assert context["run"].startswith("python3 -m issue_assistant context --issue ")
    assert context["env"]["GH_TOKEN"] == "${{ github.token }}"
    # A token that can only read, so that a write which a dry run let through fails.
    assert job["permissions"] == {
        "contents": "read",
        "discussions": "read",
        "issues": "read",
        "pull-requests": "read",
    }


@pytest.mark.parametrize(
    ("key", "name"),
    [("action", "action"), ("egress", "action behind the egress block")],
)
def test_the_action_test_runs_the_action_on_every_supported_runner(key, name):
    """People call the action from their own jobs, also on ubuntu-latest and on the
    free ARM runners. The leg on the runner of the templates keeps the name of the
    job, which main may require, and a failure on one runner cancels no other leg."""
    job = load(OWN / "action-test.yml")["jobs"][key]
    assert job["name"] == "${{ matrix.check }}"
    assert job["runs-on"] == "${{ matrix.runner }}"
    assert job["strategy"]["fail-fast"] is False
    assert job["strategy"]["matrix"]["include"] == [
        {"runner": runner, "check": name if runner == RUNNER else f"{name} ({runner})"}
        for runner in ACTION_RUNNERS
    ]


def test_the_leg_on_the_runner_of_the_templates_keeps_the_required_check():
    assert "action" in SETTINGS["branch"]["required_checks"]
    legs = load(OWN / "action-test.yml")["jobs"]["action"]["strategy"]["matrix"]
    assert legs["include"][0] == {"runner": RUNNER, "check": "action"}


def test_every_leg_runs_check_with_yq_and_without_pyyaml():
    """check prefers PyYAML, so only a job that hides it runs each image's real yq."""
    job = load(OWN / "action-test.yml")["jobs"]["action"]
    steps = {step.get("id"): step for step in job["steps"]}
    step = steps["sample-check-yq"]
    assert step["uses"] == "./"
    assert step["with"]["command"] == "check"
    assert "matrix.runner" not in step["if"]
    assert step["env"]["PYTHONPATH"] == "${{ runner.temp }}/no-pyyaml"
    install = steps["sample-install"]["run"]
    assert "no-pyyaml/yaml.py" in install and "raise ImportError" in install
    failed = next(
        step["env"]["FAILED"]
        for step in job["steps"]
        if step.get("name") == "Explain the failure"
    )
    assert "steps.sample-check-yq.outcome == 'failure'" in failed


def test_only_one_leg_lints_the_templates():
    """What actionlint and zizmor find doesn't depend on the runner, so the other
    legs run only what can differ, as quickly as they can."""
    steps = load(OWN / "action-test.yml")["jobs"]["action"]["steps"]
    lints = [step for step in steps if step.get("id", "").startswith("template-")]
    assert len(lints) == 2
    only_on_one = f"matrix.runner == '{RUNNER}'"
    assert all(only_on_one in step["if"] for step in lints)
    others = [step for step in steps if step not in lints and "if" in step]
    assert others and not any(only_on_one in step["if"] for step in others)


def test_the_readme_names_the_runners_that_the_action_test_runs_on():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    (section,) = re.findall(r"^## Supported runners\n(.+?)^## ", readme, re.M | re.S)
    table = re.findall(r"^\| `([^`]+)`", section, re.MULTILINE)
    assert table == ACTION_RUNNERS


def test_the_block_of_the_ai_s_runner_in_reserve_runs_in_ci():
    """The Action test runs the block of analyze-in-reserve on the runner in reserve.

    With the hosts that endpoints names from the firewall policy, as the job event
    does, so that a host of the policy that Harden-Runner refuses, such as one on
    port 80, or one outside it that gets through, fails before a repository moves
    the AI there.
    """
    jobs = load(OWN / "action-test.yml")["jobs"]
    hosts, block = jobs["fallback-hosts"], jobs["fallback"]
    assert hosts["runs-on"] == block["runs-on"] == RUNNER
    assert hosts["steps"][0] == JOBS["event"]["steps"][0]
    (named,) = [step for step in hosts["steps"] if step.get("uses") == "./"]
    assert named["with"] == {"command": "endpoints"}
    assert named["env"] == {"ISSUE_ASSISTANT_AI_RUNNER": RUNNER}
    assert hosts["outputs"]["endpoints"] == (
        f"${{{{ steps.{named['id']}.outputs.endpoints }}}}"
    )
    assert block["needs"] == "fallback-hosts"
    first = JOBS[RESERVE]["steps"][0]
    first = {
        **first,
        "with": {
            **first["with"],
            "allowed-endpoints": "${{ needs.fallback-hosts.outputs.endpoints }}",
        },
    }
    assert block["steps"][0] == first
    # The very steps of analyze-in-reserve let Harden-Runner see the DNS lookups, make
    # sure that it has an agent that blocks and make sure of the block, before the
    # warm-up.
    assert block["steps"][1] == JOBS[RESERVE]["steps"][1]
    assert block["steps"][2] == step_named(JOBS[RESERVE], MAKE_SURE)
    assert block["steps"][3] == step_named(JOBS[RESERVE], NETWORK_BLOCK)
    assert block["steps"][4]["name"] == WARM_UP
    checks = step_named(
        block, "Make sure that the block lets the hosts of the policy through"
    )["run"]
    # A host of the policy on 443, and one on 80, which must pass the block.
    (listed,) = re.findall(r"^ *for url in (.+); do$", checks, re.MULTILINE)
    urls = [urllib.parse.urlsplit(url) for url in listed.split()]
    assert [(url.scheme, url.hostname) for url in urls] == [
        ("https", "registry.npmjs.org"),
        ("http", "azure.archive.ubuntu.com"),
    ]
    policy = assistant.allowed_hosts(
        (assistant.TEMPLATES / assistant.FIREWALL).read_text(encoding="utf-8")
    )
    assert {url.hostname for url in urls} <= set(policy)


def test_the_network_block_of_the_ai_s_runner_runs_in_ci():
    """The Action test runs the step of analyze on GitHub's egress-firewall runner,
    with this repository's policy, which it may read, and records how the firewall
    refuses a host outside it, so that a change of the preview shows in every pull
    request and every week, before an analysis meets it. On a runner without any
    block, the step of the reusable workflow itself has to stop the AI, with its
    message; a step that passed there would let the AI run with open network access
    in every repository."""
    jobs = load(OWN / "action-test.yml")["jobs"]
    firewall, unblocked = jobs["firewall"], jobs["no-block"]
    assert firewall["runs-on"] == assistant.FIREWALL_RUNNER
    assert firewall["permissions"] == {"contents": "read"}
    assert firewall["steps"][0] == step_named(JOBS["analyze"], NETWORK_BLOCK)
    record = step_named(
        firewall, "Record how the firewall refuses a host outside the policy"
    )
    assert record["if"] == "${{ !cancelled() }}"
    assert '>> "$GITHUB_STEP_SUMMARY"' not in record["run"]
    assert 'tee -a "$GITHUB_STEP_SUMMARY"' in record["run"]
    assert "--fail" in record["run"] and "--max-time 10" in record["run"]
    # Nothing blocks on this runner: no Harden-Runner, and no firewall.
    assert unblocked["runs-on"] == RUNNER
    assert not any(
        step.get("uses", "").startswith(HARDEN_RUNNER) for step in unblocked["steps"]
    )
    script = step_named(
        unblocked, "Make sure that the probe stops the AI where nothing blocks"
    )["run"]
    path = reusable("issue-assistant.yml").relative_to(ROOT).as_posix()
    assert (
        f"yq '.jobs.analyze.steps[] | select(.name == \"{NETWORK_BLOCK}\") | .run' \\\n"
        f"  {path} >"
    ) in script
    (pattern,) = re.findall(r"grep --quiet '(.+?)' ", script)
    through, _ = probe_errors(step_named(JOBS["analyze"], NETWORK_BLOCK)["run"])
    assert re.match(pattern, f"::error::{through}")
    # Each line of its output gets a mark, so that the runner runs no command of it.
    assert "sed 's/^/> /'" in script


def test_the_graphql_documents_meet_github_s_schema_of_the_day_every_week():
    """The tests check them against a copy of the schema; GitHub changes it any day."""
    workflow = load(OWN / "action-test.yml")
    # PyYAML reads the key "on" as True.
    assert workflow[True]["schedule"]
    job = workflow["jobs"]["schema"]
    assert job["if"] == (
        "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"
    )
    (check,) = [
        step for step in job["steps"] if "GRAPHQL_SCHEMA" in step.get("env", {})
    ]
    assert "tests/test_graphql_contract.py" in check["run"]
    # tests/test_rest_contract.py checks the download and the REST requests.
    assert job["steps"][job["steps"].index(check) - 1]["id"] == "download"


# The Canary runs the AI's path of every repository every week, for an issue of a
# file: it calls the reusable workflow of the issues as the template does.
CANARY = load(OWN / "canary.yml")
FIXTURE_OF_THE_CANARY = only_in_the_canary(f"'{CANARY_ISSUE}'")
# apply takes the same issue in the smoke test of the Workflow test.
FIXTURE_OF_THE_TESTS = f"${{{{ (inputs.{CANARY_INPUT} || inputs.{SMOKE_INPUT}) && '{CANARY_ISSUE}' || '' }}}}"
SETTINGS = tomllib.loads((ROOT / ".github/repository.toml").read_text(encoding="utf-8"))


def test_the_canary_calls_the_reusable_workflow_as_every_repository_does():
    """The Canary's job is the template's call: its name, its permissions and the
    engine's secrets passed by name, whose values only the job analyze gets, from
    the environment issue-assistant.

    So a secret that expired or no longer reaches the called job, a variable of the
    caller that the called workflow doesn't see, a renamed output of the engine's
    action or a host that the firewall blocks fails there before a release brings
    it to every repository. Only the path of the workflow, at the commit of the
    run, and the input canary differ.
    """
    template = ASSISTANT["jobs"]["assistant"]
    jobs = CANARY["jobs"]
    # No job of the canary runs an engine or names a secret itself; queue measures
    # the wait of analyze for its runner, and decide lets the release decide again.
    assert list(jobs) == ["start", "assistant", "queue", "decide"]
    call = jobs["assistant"]
    path = reusable("issue-assistant.yml").relative_to(ROOT).as_posix()
    assert call["uses"] == f"./{path}"
    for key in ("name", "permissions", "secrets"):
        assert call[key] == template[key], key
    # The canary's issue, as a dry run.
    assert call["with"] == {CANARY_INPUT: True, "dry_run": True}
    assert call["if"] == "github.event_name != 'push'"
    # One canary at a time; the job, not the run, waits, so that a push never
    # waits for a canary to start the next one.
    assert call["concurrency"] == {"group": "canary", "cancel-in-progress": False}
    assert set(call) == set(template) | {"if", "concurrency"}
    # No other workflow passes the input canary.
    callers = [
        (path.name, job_name)
        for path in EVERY_WORKFLOW
        for job_name, job in load(path)["jobs"].items()
        if CANARY_INPUT in (job.get("with") or {})
    ]
    assert callers == [("canary.yml", "assistant")]


def test_the_canary_writes_nothing():
    """The job start may only start workflows. The jobs of the call get what a
    repository grants, but the job event sets no labels for the canary's issue, and
    with a fixture apply is a dry run, whatever dry-run says."""
    assert CANARY["permissions"] == {}
    assert CANARY["jobs"]["start"]["permissions"] == {"actions": "write"}
    for name, job in CANARY["jobs"].items():
        assert "id-token" not in job["permissions"], name
    event = {step.get("id"): step for step in JOBS["event"]["steps"]}["event"]
    assert event["if"] == f"${{{{ !inputs.{CANARY_INPUT} && !inputs.{SMOKE_INPUT} }}}}"
    (apply,) = [step for step in JOBS["apply"]["steps"] if step.get("uses") == TOOL]
    assert apply["env"] == {assistant.FIXTURE_VARIABLE: FIXTURE_OF_THE_TESTS}
    assert apply["with"]["dry-run"] == "${{ inputs.dry_run }}"


def test_only_context_and_apply_read_the_canary_s_issue():
    """The script refuses a fixture for every other command, and every repository's
    run gives them none."""
    assert (ROOT / CANARY_ISSUE).is_file()
    readers = []
    for job_name, job in JOBS.items():
        for step in job["steps"]:
            env = step.get("env") or {}
            if assistant.FIXTURE_VARIABLE in env:
                assert step["uses"] == TOOL, job_name
                expected = (
                    FIXTURE_OF_THE_TESTS
                    if job_name == "apply"
                    else FIXTURE_OF_THE_CANARY
                )
                assert env[assistant.FIXTURE_VARIABLE] == expected
                readers.append((job_name, step["with"]["command"]))
    assert readers == [("analyze", "context"), (RESERVE, "context"), ("apply", "apply")]


def canary_step(job):
    """The step of the job `job` of the issues' reusable workflow that only the
    Canary runs; analyze-in-reserve runs the very step of analyze."""
    condition = f"inputs.{CANARY_INPUT}"
    (step,) = [step for step in JOBS[job]["steps"] if step.get("if") == condition]
    if job == "analyze":
        (same,) = [
            step for step in JOBS[RESERVE]["steps"] if step.get("if") == condition
        ]
        assert same == step
    return step


def run_step(step, tmp_path, **env):
    """The exit code and output of the script of `step`, and what it wrote to
    GITHUB_OUTPUT."""
    output = tmp_path / "output"
    output.touch()
    result = subprocess.run(
        [shutil.which("bash"), "-e", "-c", step["run"]],
        env={**env, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        check=False,
    )
    return result, output.read_text(encoding="utf-8")


def test_the_job_event_takes_the_canary_s_issue():
    """With the input canary, the job event chooses an analysis of the issue of the
    Canary's file in place of the task of an event, also when GitHub names a bot as
    the one who started a scheduled run, and sets no labels."""
    event = JOBS["event"]
    condition = " ".join(event["if"].split())
    assert condition.startswith(
        f"!github.event.repository.fork && (inputs.{CANARY_INPUT} || "
    )
    step = canary_step("event")
    assert step["id"] == "canary"
    assert step["env"] == {
        "CALLER": "${{ github.repository }}",
        "WORKFLOW": "${{ fromJSON(toJSON(job)).workflow_repository }}",
        "AI": "${{ vars.ISSUE_ASSISTANT_AI }}",
    }
    order = [step.get("id") for step in event["steps"]]
    assert order.index("smoke") == order.index("canary") - 1
    assert order.index("canary") == order.index("event") - 1
    # context takes the number of the fixture's issue when the job names none.
    assert event["outputs"] == {
        "issue": "${{ steps.event.outputs.issue }}",
        "mode": "${{ steps.event.outputs.mode || steps.canary.outputs.mode || "
        "steps.smoke.outputs.mode }}",
        "endpoints": "${{ steps.endpoints.outputs.endpoints }}",
        "backlog": "${{ steps.event.outputs.backlog }}",
    }


@pytest.mark.parametrize(
    ("caller", "ai", "code", "says"),
    [
        ("Dennis-Otto/issue-assistant", "on", 0, "The canary analyzes "),
        # GitHub keeps the case of a name as its owner typed it.
        ("dennis-otto/Issue-Assistant", "on", 0, "The canary analyzes "),
        # Only this repository has the canary's issue.
        (
            "octo/app",
            "on",
            1,
            "::error::Only the Canary of Dennis-Otto/issue-assistant may pass the "
            "input canary, not octo/app.",
        ),
        # The variables of the caller don't reach the called workflow.
        (
            "Dennis-Otto/issue-assistant",
            "",
            1,
            "::error::The called workflow doesn't see the variable ISSUE_ASSISTANT_AI",
        ),
        # A repository's run would skip the AI, which the canary is there to run.
        (
            "Dennis-Otto/issue-assistant",
            "off",
            1,
            "::error::ISSUE_ASSISTANT_AI is off, which skips the AI",
        ),
    ],
)
def test_the_canary_runs_in_this_repository_alone_and_sees_its_variable(
    tmp_path, caller, ai, code, says
):
    """The settings bot sets ISSUE_ASSISTANT_AI to "on" here, so a called workflow
    that sees no value doesn't see the variables of its caller."""
    assert SETTINGS["variables"]["ISSUE_ASSISTANT_AI"] == "on"
    result, output = run_step(
        canary_step("event"),
        tmp_path,
        CALLER=caller,
        WORKFLOW="Dennis-Otto/issue-assistant",
        AI=ai,
    )
    assert result.returncode == code, result.stdout + result.stderr
    assert says in result.stdout
    assert output == ("mode=triage\n" if code == 0 else "")


def test_the_canary_fails_where_a_repository_s_run_skips_the_ai():
    """A repository's run skips apply without the engine's secret or without an
    answer; the canary fails, last in the job analyze, since either is what it
    watches for."""
    step = canary_step("analyze")
    assert step is ANALYZE["steps"][-1]
    assert step["env"] == {
        "READY": "${{ steps.engine.outputs.ready }}",
        "ENGINE": "${{ steps.engine.outputs.engine }}",
        "ANSWER": ANALYZE["outputs"]["answer"],
    }


@pytest.mark.parametrize(
    ("ready", "answer", "code", "says"),
    [
        ("true", '{"summary": "A cause"}', 0, "The engine answered with 22 characters"),
        # No secret reached the job: it expired, or the caller no longer passes it.
        ("false", "", 1, "::error::The job analyze got no secret of the engine claude"),
        # A renamed output of the engine's action, or an engine that failed.
        ("true", "", 1, "::error::The engine gave no structured answer"),
    ],
)
def test_the_canary_checks_that_the_engine_answered(
    tmp_path, ready, answer, code, says
):
    result, _ = run_step(
        canary_step("analyze"), tmp_path, READY=ready, ENGINE="claude", ANSWER=answer
    )
    assert result.returncode == code, result.stdout + result.stderr
    assert says in result.stdout


def without_the_canary(job):
    """A job of the issues' reusable workflow as every repository's run has it:
    without the steps that only the Canary and the smoke test run, the values that
    only the Canary sets, and the condition that only the smoke test adds."""
    steps = []
    for step in job["steps"]:
        if step.get("if") in {f"inputs.{own}" for own in OWN_INPUTS}:
            continue
        step = dict(step)
        for key in ("with", "env"):
            kept = {
                name: value
                for name, value in step.get(key, {}).items()
                if not str(value).startswith(f"${{{{ inputs.{CANARY_INPUT} && ")
            }
            if kept:
                step[key] = kept
            else:
                step.pop(key, None)
        steps.append(step)
    job = dict(job, steps=steps)
    job["if"] = job["if"].removeprefix(f"!inputs.{SMOKE_INPUT} && ")
    return job


def test_the_canary_runs_every_week_and_after_every_change_of_its_path():
    triggers = CANARY[True]
    assert triggers["schedule"] and "workflow_dispatch" in triggers
    # Only main may use the engine's secret, so no pull request runs the canary.
    assert set(triggers) == {"schedule", "push", "workflow_dispatch"}
    assert triggers["push"]["branches"] == ["main"]
    # The engine's action refuses a push, so a push starts the canary by hand.
    start = CANARY["jobs"]["start"]
    assert start["if"] == "github.event_name == 'push'"
    assert start["runs-on"] == RUNNER
    assert start["steps"][0] == JOBS["event"]["steps"][0]
    assert "actions/workflows/canary.yml/dispatches" in start_script()
    # It waits for the jobs of the canary, which may wait for an earlier canary's;
    # analyze-in-reserve runs only in place of analyze.
    waits = 2 * sum(
        job["timeout-minutes"] for name, job in JOBS.items() if name != RESERVE
    )
    assert start["timeout-minutes"] >= waits
    paths = triggers["push"]["paths"]
    for path in (
        "action.yml",
        "issue_assistant/**",
        "prompts/**",
        f"{assistant.WORKFLOWS}/{assistant.REUSABLE_PREFIX}issue-assistant.yml",
        f"templates/{assistant.FIREWALL}",
        f"{assistant.WORKFLOWS}/canary.yml",
        CANARY_ISSUE,
    ):
        assert path in paths, path
    for pattern in paths:
        assert next(ROOT.glob(pattern), None), f"{pattern} matches no file"


def start_step():
    return step_named(
        CANARY["jobs"]["start"], "Start the canary and wait for its result"
    )


def start_script():
    return start_step()["run"]


# The GitHub CLI of the job start, as a shell function: it notes its arguments and
# answers with the run that GitHub started, with 404 to the first MISSING questions
# for that run, as GitHub does in the first moments after the dispatch, and with
# that run's conclusion. sleep only notes how long the step would wait.
FAKE_GH = """
gh() {
  echo "gh $*" >> "$CALLS"
  case "$*" in
    *" --method POST "*) [[ -z "$RUN" ]] || echo "$RUN" ;;
    "api --header "*" repos/$GH_REPO/actions/runs/"*)
      if ((MISSING > 0)); then
        MISSING=$((MISSING - 1))
        echo "failed to get run: HTTP 404: Not Found" >&2
        return 1
      fi
      ;;
    "run view "*) echo "$CONCLUSION" ;;
  esac
}
sleep() {
  echo "sleep $*" >> "$CALLS"
}
"""

# How often the step asks for the run before it gives up, and how long it waits
# between two questions: about a minute in all.
CANARY_LOOKUPS = 12
CANARY_LOOKUP_PAUSE = "sleep 5"


@pytest.mark.parametrize(
    ("run", "missing", "conclusion", "code", "says"),
    [
        ("42", 0, "success", 0, "The canary passed."),
        # A later push's canary took the place of its waiting analysis.
        ("42", 0, "cancelled", 0, "::notice::The canary was cancelled"),
        ("42", 0, "failure", 1, "::error::The canary ended as failure: "),
        ("42", 0, "timed_out", 1, "::error::The canary ended as timed_out: "),
        # GitHub may not serve the run that it just named for a moment.
        ("42", 2, "success", 0, "The canary passed."),
        ("42", CANARY_LOOKUPS - 1, "failure", 1, "::error::The canary ended as "),
        # Nor for a minute, and then the run's page has its result.
        (
            "42",
            CANARY_LOOKUPS,
            "",
            0,
            "::warning::GitHub doesn't show the canary's run after a minute; "
            "its result is at https://github.com/octo/assistant/actions/runs/42",
        ),
        # GitHub may start a run without naming it.
        ("", 0, "", 0, "::warning::GitHub started the canary without naming its run"),
    ],
)
def test_the_push_fails_when_the_canary_that_it_started_fails(
    tmp_path, run, missing, conclusion, code, says
):
    """GitHub mails a failed run to the person who started it, and nobody started
    the canary's run after a push but the job's token: the push's own run, which
    the person who merged started, carries the canary's result."""
    calls = tmp_path / "calls"
    env = {
        "CALLS": str(calls),
        "RUN": run,
        "MISSING": str(missing),
        "CONCLUSION": conclusion,
        "GH_REPO": "octo/assistant",
        "GITHUB_REF_NAME": "main",
        "GITHUB_SERVER_URL": "https://github.com",
        "API_VERSION_HEADER": start_step()["env"]["API_VERSION_HEADER"],
    }
    result = subprocess.run(
        [shutil.which("bash"), "-e", "-c", FAKE_GH + start_script()],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == code, result.stderr
    assert says in result.stdout
    started, *rest = calls.read_text(encoding="utf-8").splitlines()
    assert started == (
        f"gh api --header {assistant.API_VERSION_HEADER} --method POST "
        "repos/octo/assistant/actions/workflows/canary.yml/dispatches -f ref=main "
        "-F return_run_details=true --jq .workflow_run_id"
    )
    if run:
        url = "https://github.com/octo/assistant/actions/runs/42"
        assert f"The canary runs at {url}" in result.stdout
        lookup = (
            f"gh api --header {assistant.API_VERSION_HEADER} "
            "repos/octo/assistant/actions/runs/42 --silent"
        )
        lookups = min(missing + 1, CANARY_LOOKUPS)
        waited = [lookup, CANARY_LOOKUP_PAUSE] * (lookups - 1) + [lookup]
        if missing < CANARY_LOOKUPS:
            assert rest == [
                *waited,
                "gh run watch 42 --interval 60",
                "gh run view 42 --json conclusion --jq .conclusion",
            ]
        else:
            assert rest == waited
        if code:
            assert result.stdout.rstrip().endswith(url)
    else:
        assert rest == []


def test_the_canary_s_issue_is_one_that_this_repository_could_get():
    """An open issue of the bug report form from someone who doesn't maintain the
    repository, labeled as the job event leaves it for its first analysis."""
    config = assistant.load_config(ROOT, "Dennis-Otto/issue-assistant")
    fixture = assistant.read_fixture(ROOT, CANARY_ISSUE)
    issue = fixture.issue
    assert issue["state"] == "open" and not issue["locked"]
    assert not assistant.is_maintainer(issue) and not assistant.is_bot(issue["user"])
    form = load(ROOT / ".github/ISSUE_TEMPLATE/bug_report.yml")
    questions = [item["attributes"]["label"] for item in form["body"][1:]]
    assert list(assistant.form_fields(issue["body"])) == questions
    labels = assistant.label_names(issue)
    assert labels == {*form["labels"], *assistant.form_labels(issue, config)}
    assert labels <= {label.name for label in config.labels}
    assert not any(assistant.kind_of(comment) for comment in fixture.comments)


def test_the_github_cli_sends_no_telemetry():
    # Its telemetry would only knock at a host that the block doesn't allow.
    step = ACTION["runs"]["steps"][-1]
    assert step["env"]["GH_TELEMETRY"] == "false"


@pytest.mark.parametrize("path", WORKFLOWS, ids=workflow_id)
def test_actions_are_pinned_and_checkouts_keep_no_credentials(path):
    # Workflows that issues, comments or other workflows start run the default branch.
    # The blueprint's own, such as its release workflow and its screenshot bot, are
    # left to the tests of the blueprint (BLUEPRINT_WORKFLOWS).
    events = load(path)[True]
    privileged = not {
        "issues",
        "issue_comment",
        "discussion",
        "discussion_comment",
        "pull_request_target",
        "workflow_run",
    }.isdisjoint([events] if isinstance(events, str) else events)
    for job_name, step in steps(path):
        if "uses" not in step:
            continue
        uses = step["uses"]
        if uses.startswith(("./", "docker://")):
            continue
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", uses), uses
        if uses.startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False, job_name
            # The jobs of a reusable workflow run for the events of the templates;
            # their only other checkout is the issue assistant of their workflow.
            if privileged or path.name.startswith(assistant.REUSABLE_PREFIX):
                assert "ref" not in step["with"] or step["with"] == TOOL_CHECKOUT, (
                    "issue events must run the default branch"
                )
    for job_name, job in load(path)["jobs"].items():
        # A called workflow of another repository, at a commit too.
        if "uses" in job and not job["uses"].startswith("./"):
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", job["uses"]), job_name


def test_install_finds_every_pin_of_the_action():
    for name in NAMES:
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        uses = re.findall(r"uses: (Dennis-Otto/issue-assistant[@/].*)", text)
        assert uses and all(assistant.ACTION_PIN.fullmatch(pin) for pin in uses)
    # A pin of the action itself, as the templates before the reusable workflows
    # had them.
    pin = f"{assistant.ACTION}@{'a' * 40} # v1.5.0"
    assert assistant.pinned(pin, "b" * 40, "v1.6.0") == (
        f"{assistant.ACTION}@{'b' * 40} # v1.6.0"
    )


@pytest.mark.parametrize("path", WORKFLOWS, ids=workflow_id)
def test_untrusted_text_never_reaches_a_shell_script(path):
    for job_name, step in steps(path):
        script = step.get("run", "")
        # Values reach scripts only through the environment.
        assert "${{" not in script, (job_name, step.get("name"))
        for value in (step.get("env") or {}).values():
            assert not re.search(
                r"github\.event\.(issue|comment)\.(title|body)", str(value)
            )


def test_the_action_passes_every_input_through_the_environment():
    step = ACTION["runs"]["steps"][-1]
    assert step["run"] == (
        'PYTHONPATH="$GITHUB_ACTION_PATH${PYTHONPATH:+:$PYTHONPATH}" '
        'python3 -P -B -m issue_assistant "$COMMAND"'
    )
    inputs = {f"${{{{ inputs.{name} }}}}" for name in ACTION["inputs"]}
    assert inputs <= set(step["env"].values())
    for name, output in ACTION["outputs"].items():
        assert output["value"] == f"${{{{ steps.run.outputs.{name} }}}}"


# How the action says that the Python of the runner is too old or missing.
NEEDS_PYTHON = "::error::The issue assistant needs Python 3.12 or newer, and "
HOW_TO_FIX_THE_PYTHON = (
    " Run the job on a runner with a newer one, such as runs-on: ubuntu-24.04, or add "
    "actions/setup-python with python-version: 3.12 before this step.\n"
)


def fake_python(folder, version):
    """A python3 in folder that reports this version, to the guard of the action. It
    runs what the guard asks on the real Python, whose version it replaces."""
    folder.mkdir(exist_ok=True)
    python = Path(sys.executable).as_posix()
    replaced = f"sys.version_info = ({version.replace('.', ', ')}, 'final', 0)"
    fake = folder / "python3"
    fake.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = --version ]; then echo "Python {version}"; exit 0; fi\n'
        f'exec "{python}" -c "import sys; {replaced}; exec(sys.argv[1])" "$2"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return folder


def run_the_guard(folder):
    (guard, _) = ACTION["runs"]["steps"]
    return subprocess.run(
        [shutil.which("bash"), "-e", "-c", guard["run"]],
        env={**os.environ, "PATH": str(folder)},
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_action_checks_the_python_before_it_runs_the_script():
    """A failed step of a composite action stops the steps after it, unless one of
    them asks otherwise; so the script never starts on a Python that it doesn't
    support, where a check inside the script would come too late."""
    (guard, script) = ACTION["runs"]["steps"]
    assert guard["id"] == "python" and guard["shell"] == "bash"
    assert "continue-on-error" not in guard
    assert "if" not in script
    assert "issue_assistant" not in guard["run"]
    # It takes no input, so no value of an issue or a repository reaches its text.
    assert "${{" not in guard["run"] and "env" not in guard


def test_the_python_of_the_guard_is_the_one_that_the_script_targets():
    """Decision 0003: the version that ruff and mypy check the script for."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    major, minor = project["tool"]["mypy"]["python_version"].split(".")
    (guard, _) = ACTION["runs"]["steps"]
    assert f"sys.version_info < ({major}, {minor})" in guard["run"]
    assert NEEDS_PYTHON.removeprefix("::error::").startswith(
        f"The issue assistant needs Python {major}.{minor} or newer"
    )
    assert f"python-version: {major}.{minor}" in HOW_TO_FIX_THE_PYTHON


@pytest.mark.parametrize("version", ["3.12.0", "3.14.4"])
def test_the_guard_lets_a_python_that_is_new_enough_pass(tmp_path, version):
    result = run_the_guard(fake_python(tmp_path / "bin", version))
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


@pytest.mark.parametrize("version", ["3.11.9", "3.10.12", "3.8.10"])
def test_the_guard_names_a_python_that_is_too_old_and_how_to_fix_it(tmp_path, version):
    result = run_the_guard(fake_python(tmp_path / "bin", version))
    assert result.returncode == 1
    assert result.stdout == (
        f"{NEEDS_PYTHON}python3 on this runner is Python {version}."
        f"{HOW_TO_FIX_THE_PYTHON}"
    )
    assert result.stderr == ""


def test_the_guard_says_so_when_the_runner_has_no_python(tmp_path):
    (tmp_path / "bin").mkdir()
    result = run_the_guard(tmp_path / "bin")
    assert result.returncode == 1
    assert result.stdout == (
        f"{NEEDS_PYTHON}this runner has no python3.{HOW_TO_FIX_THE_PYTHON}"
    )
    assert result.stderr == ""


def old_python_job():
    return load(OWN / "action-test.yml")["jobs"]["old-python"]


def test_the_action_test_runs_the_action_on_a_python_that_is_too_old():
    """The real action on the real Python 3.11, which must fail in its first step."""
    job = old_python_job()
    assert job["runs-on"] == RUNNER
    uses = [step.get("uses", "").split("@")[0] for step in job["steps"]]
    setup = uses.index("actions/setup-python")
    action = uses.index("./")
    assert uses.index("actions/checkout") < setup < action
    assert job["steps"][setup]["with"] == {"python-version": "3.11"}
    refused = job["steps"][action]
    # Only the next step decides, so that the action failing is what makes it pass.
    assert refused["continue-on-error"] is True
    assert refused["with"] == {"command": "check"}
    verified = job["steps"][action + 1]
    assert verified["env"]["OUTCOME"] == f"${{{{ steps.{refused['id']}.outcome }}}}"


@pytest.mark.parametrize(
    ("outcome", "version", "guard", "error"),
    [
        ("failure", "3.11.9", None, None),
        ("success", "3.11.9", None, "The action ran on Python 3.11"),
        ("failure", "3.12.0", None, "let Python 3.12.0 through"),
        ("failure", "3.10.12", None, "doesn't name Python 3.11"),
        ("failure", "3.11.9", "echo Traceback; exit 1", "doesn't name Python 3.11"),
        (
            "failure",
            "3.11.9",
            'echo "::error::Python 3.11.9"; echo Traceback >&2; exit 1',
            "traceback",
        ),
    ],
)
def test_the_job_on_the_old_python_passes_only_with_the_message(
    tmp_path, outcome, version, guard, error
):
    """It reads the check out of action.yml, so that it runs what the action runs."""
    folder = fake_python(tmp_path / "bin", version)
    (real, _) = ACTION["runs"]["steps"]
    (tmp_path / "guard").write_text(guard or real["run"], encoding="utf-8")
    fake_yq = folder / "yq"
    fake_yq.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = \'.runs.steps[] | select(.id == "python") | .run\' ] '
        '&& [ "$2" = action.yml ]; then\n'
        f'  cat "{(tmp_path / "guard").as_posix()}"\n'
        "else\n"
        "  exit 2\n"
        "fi\n",
        encoding="utf-8",
    )
    fake_yq.chmod(0o755)
    (step,) = [
        step for step in old_python_job()["steps"] if "OUTCOME" in step.get("env", {})
    ]
    result = subprocess.run(
        [shutil.which("bash"), "-e", "-c", step["run"]],
        env={
            **os.environ,
            "OUTCOME": outcome,
            "PATH": os.pathsep.join([str(folder), os.environ["PATH"]]),
            "RUNNER_TEMP": str(tmp_path),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    if error is None:
        assert result.returncode == 0, result.stdout + result.stderr
        # The message shows in the log, and only the action makes it an annotation.
        assert "> ::error::" in result.stdout
        assert "\n::error::" not in f"\n{result.stdout}"
    else:
        assert result.returncode == 1
        (message,) = re.findall(r"^::error::(.+)$", result.stdout, re.MULTILINE)
        assert error in message


def test_every_engine_has_rules_a_step_and_credentials():
    assert set(ENGINE_RULES) == set(assistant.ENGINES)
    ready = STEPS["engine"]["with"]["ready-engines"]
    for engine, rules in ENGINE_RULES.items():
        step = STEPS[engine]
        assert step["uses"].split("@")[0] == rules["action"]
        assert step["if"] == (
            "steps.engine.outputs.ready == 'true' && "
            f"steps.engine.outputs.engine == '{engine}'"
        )
        secrets = " || ".join(f"secrets.{secret} != ''" for secret in rules["secrets"])
        # install --setup asks for the first of them.
        assert assistant.ENGINES[engine].credentials == rules["secrets"]
        assert f"({secrets}) && '{engine}'" in ready


@pytest.mark.parametrize("engine", sorted(ENGINE_RULES))
def test_each_engine_can_only_read_the_checkout(engine):
    ENGINE_RULES[engine]["reads_only"](STEPS[engine])


def runs_an_engine(step):
    """Whether the step runs an engine: an engine's action, or the action's command
    analyze, which runs an engine's command line."""
    actions = {rules["action"] for rules in ENGINE_RULES.values()} - {TOOL}
    uses = step.get("uses", "")
    return uses.split("@")[0] in actions or (
        uses == TOOL and step["with"]["command"] == "analyze"
    )


def test_only_the_analyze_job_runs_an_engine_and_only_with_read_permissions():
    runs_engine = {
        name for name, job in JOBS.items() if any(map(runs_an_engine, job["steps"]))
    }
    assert [step.get("id") for step in ANALYZE["steps"] if runs_an_engine(step)] == (
        list(ENGINE_RULES)
    )
    assert runs_engine == {"analyze", RESERVE}
    assert ANALYZE["permissions"] == {
        "contents": "read",
        "issues": "read",
        "discussions": "read",
    }
    assert ANALYZE["runs-on"] == assistant.FIREWALL_RUNNER
    assert ANALYZE["environment"] == {"name": "issue-assistant", "deployment": False}
    for name in ("event", "apply"):
        assert "environment" not in JOBS[name]
        for rules in ENGINE_RULES.values():
            for secret in rules["secrets"]:
                assert secret not in yaml.safe_dump(JOBS[name])


def test_the_command_line_of_every_engine_is_pinned_for_renovate():
    """Codex runs at the version that the workflow pins, below a line that lets
    Renovate update it; the Gemini CLI and the Copilot CLI at the version of their
    lock files in engines/, which Renovate's manager of npm updates, and no workflow
    names a version of them. The Canary runs the issues' workflow."""
    for path in (
        reusable("issue-assistant.yml"),
        reusable(assistant.DISCUSSION_WORKFLOW),
    ):
        text = path.read_text(encoding="utf-8")
        pin = (
            r"# renovate: datasource=npm depName=@openai/codex\n"
            r' +codex-version: "\d+\.\d+\.\d+"\n'
        )
        assert len(re.findall(pin, text)) == 1, path.name
    renovate = (ROOT / ".github" / "renovate.json5").read_text(encoding="utf-8")
    blueprint = (ROOT / ".github" / "renovate-blueprint.json5").read_text("utf-8")
    # Renovate leaves out only the installed packages, not the folder engines/.
    assert re.search(
        r'ignorePaths: \["\*\*/node_modules/\*\*", "\*\*/vendor/\*\*"\]', blueprint
    )
    for engine in assistant.ENGINES.values():
        if not engine.cli:
            continue
        assert f'"{engine.cli.package}",' in renovate
        for path in OWN.glob("*.yml"):
            text = path.read_text(encoding="utf-8")
            assert engine.cli.package not in text, path.name
    for path in OWN.glob("*.yml"):
        assert "engine-version" not in path.read_text(encoding="utf-8"), path.name


@pytest.mark.mutation_unsafe
def test_the_docs_install_the_command_lines_from_their_lock_files():
    """The pages that say how analyze installs the Gemini CLI and the Copilot CLI
    name the lock files and npm ci, and no page or file of the action names
    engine-version, which analyze no longer takes."""
    pages = {
        "engines": ROOT / "docs" / "engines.md",
        "security": ROOT / "docs" / "security" / "engines.md",
        "architecture": ROOT / "docs" / "architecture" / "testing.md",
        "decision": ROOT
        / "docs"
        / "decisions"
        / "0017-engines-without-an-action-of-their-own.md",
    }
    for name, path in pages.items():
        text = path.read_text(encoding="utf-8")
        assert "engines/" in text and "npm ci" in text, name
    # The decision record tells the history of the input, the others its present.
    for path in [*list(pages.values())[:3], ROOT / "action.yml"]:
        assert "engine-version" not in path.read_text(encoding="utf-8"), path.name
    assert "engine-version" not in package_source()
    assert "ENGINE_VERSION" not in (ROOT / "action.yml").read_text(encoding="utf-8")


def test_the_engine_is_chosen_before_the_context_and_the_engines():
    assert STEPS["engine"]["with"] == {
        "command": "engine",
        "mode": "${{ needs.event.outputs.mode }}",
        "ready-engines": STEPS["engine"]["with"]["ready-engines"],
    }
    assert "vars.ISSUE_ASSISTANT_AI != 'off'" in ANALYZE["if"]
    assert STEPS["context"]["if"] == "steps.engine.outputs.ready == 'true'"
    order = [step.get("id") for step in ANALYZE["steps"]]
    assert (
        order.index("engine")
        < order.index("context")
        < min(order.index(engine) for engine in ENGINE_RULES)
    )


def test_the_cost_of_every_run_reaches_the_summary_of_the_job():
    """After the engine's step, also when it failed, such as at its turns or its
    dollars, the usage command reports the numbers of its execution file."""
    usage = STEPS["usage"]
    assert usage["uses"] == TOOL
    assert usage["if"] == "always() && steps.claude.outputs.execution_file != ''"
    assert usage["with"] == {
        "command": "usage",
        "issue": "${{ needs.event.outputs.issue }}",
        "mode": "${{ needs.event.outputs.mode }}",
        "execution-file": "${{ steps.claude.outputs.execution_file }}",
    }
    order = [step.get("id") for step in ANALYZE["steps"]]
    assert (
        order.index("usage") == max(order.index(engine) for engine in ENGINE_RULES) + 1
    )
    assert "usage" in ACTION["inputs"]["command"]["description"]


def test_the_answer_reaches_github_only_through_the_checks():
    answers = " || ".join(
        f"steps.{engine}.outputs.{rules['output']}"
        for engine, rules in ENGINE_RULES.items()
    )
    assert (
        ANALYZE["outputs"]
        == JOBS[RESERVE]["outputs"]
        == {
            "engine": "${{ steps.engine.outputs.engine }}",
            "answer": f"${{{{ {answers} }}}}",
        }
    )
    (apply_step,) = [
        step for step in JOBS["apply"]["steps"] if step.get("uses") == TOOL
    ]
    assert apply_step["with"]["command"] == "apply"
    # From whichever of the two jobs of the AI ran, or, in the smoke test alone, the
    # answer of its file.
    assert apply_step["with"]["answer"] == (
        f"${{{{ needs.analyze.outputs.answer || needs.{RESERVE}.outputs.answer "
        "|| steps.smoke.outputs.answer }}"
    )
    assert apply_step["with"]["engine"] == (
        f"${{{{ needs.analyze.outputs.engine || needs.{RESERVE}.outputs.engine }}}}"
    )
    assert JOBS["apply"]["needs"] == ["event", "analyze", RESERVE]
    assert JOBS["apply"]["permissions"] == {
        "contents": "read",
        "issues": "write",
        "discussions": "read",
    }


def test_the_manual_run_offers_every_task_and_starts_as_a_dry_run():
    inputs = ASSISTANT[True]["workflow_dispatch"]["inputs"]
    assert inputs["mode"]["options"] == [
        *(mode for mode in assistant.MODES if mode not in assistant.DISCUSSION_MODES),
        assistant.BACKLOG,
    ]
    assert inputs["dry_run"]["default"] is True
    # An instruction and the override of the one-comment rule, which a run by hand
    # leaves out unless someone gives them, and the comments of the task backlog,
    # which sets labels only unless someone ticks them.
    assert list(inputs) == [
        "issue",
        "mode",
        "instruction",
        "override",
        "comment",
        "dry_run",
    ]
    assert inputs["comment"]["type"] == "boolean"
    assert inputs["comment"]["default"] is False
    assert inputs["instruction"]["type"] == "string"
    assert inputs["instruction"]["default"] == ""
    assert inputs["override"]["type"] == "boolean"
    assert inputs["override"]["default"] is False
    # The discussions have one task, for the number of a discussion.
    inputs = DISCUSSIONS[True]["workflow_dispatch"]["inputs"]
    assert list(inputs) == ["discussion", "dry_run"]
    assert inputs["discussion"]["type"] == "number"
    assert inputs["dry_run"]["default"] is True
    assert LIFECYCLE[True]["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True


def test_the_task_backlog_calls_the_workflow_once_for_each_issue_that_it_chose():
    """The job assistant chooses the issues of the backlog; the job backlog calls the
    same workflow at the same commit for each, with the same permissions and
    secrets, two at a time, and only when it chose any: GitHub runs no matrix of
    none. A call that fails leaves the others running."""
    jobs = ASSISTANT["jobs"]
    first, backlog = jobs["assistant"], jobs["backlog"]
    for key in ("permissions", "uses", "secrets"):
        assert backlog[key] == first[key], key
    assert backlog["needs"] == "assistant"
    assert backlog["if"] == "needs.assistant.outputs.backlog != ''"
    assert backlog["strategy"] == {
        "fail-fast": False,
        "max-parallel": 2,
        "matrix": {"issue": "${{ fromJSON(needs.assistant.outputs.backlog) }}"},
    }
    assert backlog["with"] == {
        "dry_run": first["with"]["dry_run"],
        BACKLOG_INPUT: "${{ matrix.issue }}",
    }
    called = load(reusable("issue-assistant.yml"))[True]["workflow_call"]
    given = called["inputs"][BACKLOG_INPUT]
    assert (given["type"], given["default"]) == ("number", 0)
    assert called["outputs"]["backlog"]["value"] == "${{ jobs.event.outputs.backlog }}"
    assert list(called["outputs"]) == ["backlog"]
    # Only the step of the job event that chooses the task reads the input, from
    # the environment.
    (step,) = [step for step in JOBS["event"]["steps"] if step.get("id") == "event"]
    assert step["env"] == {
        assistant.BACKLOG_VARIABLE: f"${{{{ inputs.{BACKLOG_INPUT} }}}}"
    }
    assert json.dumps(JOBS).count(BACKLOG_INPUT) == 1


def test_the_events_the_assistant_handles_trigger_it():
    triggers = ASSISTANT[True]
    assert triggers["issues"]["types"] == ["opened", "edited"]
    assert triggers["issue_comment"]["types"] == ["created"]
    condition = " ".join(JOBS["event"]["if"].split())
    assert "!github.event.issue.pull_request" in condition
    assert "github.event.sender.type != 'Bot'" in condition


def test_the_discussions_analyze_as_the_issues_do():
    """The jobs analyze and analyze-in-reserve of the discussions are those of the
    issues, as YAML: every rule above that keeps the engine to reading holds for
    both."""
    for name in ("analyze", RESERVE):
        # Both skip the engine in their smoke test, and only the issues have a
        # Canary.
        assert DISCUSSION_JOBS[name]["if"] == JOBS[name]["if"], name
        assert without_the_canary(DISCUSSION_JOBS[name]) == without_the_canary(
            JOBS[name]
        ), name
        assert not [
            step
            for step in DISCUSSION_JOBS[name]["steps"]
            if CANARY_INPUT in json.dumps(step)
        ], name
    issues = load(reusable("issue-assistant.yml"))[True]["workflow_call"]
    discussions = load(reusable(assistant.DISCUSSION_WORKFLOW))[True]["workflow_call"]
    # The same inputs and secrets, but no canary; the smoke test of each reads its
    # own files.
    assert discussions["secrets"] == issues["secrets"]
    assert list(discussions["inputs"]) == ["dry_run", SMOKE_INPUT]
    assert discussions["inputs"]["dry_run"] == issues["inputs"]["dry_run"]
    assert discussions["inputs"][SMOKE_INPUT]["description"] == (
        issues["inputs"][SMOKE_INPUT]["description"]
        .replace("smoke-answer.json", "smoke-discussion-answer.json")
        .replace(
            "the issue of tests/fixtures/canary-issue.json",
            "the discussion of tests/fixtures/smoke-discussion.json",
        )
    )
    assert set(discussions) == {"inputs", "secrets"}
    assert list(DISCUSSION_JOBS) == ["event", "analyze", RESERVE, "apply"]
    assert DISCUSSION_JOBS["apply"]["needs"] == ["event", "analyze", RESERVE]
    # apply runs after an answer of the engine on either runner. It writes out what
    # GitHub otherwise adds, that the jobs that ran passed: one of the two jobs of the
    # AI is always skipped, and the smoke tests skip both.
    answered = (
        "(needs.analyze.result == 'success' && needs.analyze.outputs.answer != '') || "
        f"(needs.{RESERVE}.result == 'success' && "
        f"needs.{RESERVE}.outputs.answer != '')"
    )
    assert (
        words(DISCUSSION_JOBS["apply"]["if"])
        == words(JOBS["apply"]["if"])
        == (
            "!cancelled() && needs.event.result == 'success' && "
            f"(inputs.{SMOKE_INPUT} || {answered})"
        )
    )
    # Their event and apply jobs run the same commands of the action; the smoke
    # test of each gives apply the answer of its file.
    for name in ("event", "apply"):
        ours = [
            step["with"]
            for step in DISCUSSION_JOBS[name]["steps"]
            if step.get("uses") == TOOL
        ]
        theirs = [
            step["with"] for step in JOBS[name]["steps"] if step.get("uses") == TOOL
        ]
        assert ours == theirs, name


def test_only_the_apply_job_of_the_discussions_writes_to_discussions():
    """A called job gets at most what its caller grants, and a workflow whose job
    asks for more doesn't start: the jobs of the issues, which a repository may call
    with an older template that grants discussions only reading, never ask for
    more, and the discussions' jobs write nothing else."""
    writers = {
        (path.name, job_name)
        for path in OWN.glob(f"{assistant.REUSABLE_PREFIX}*.yml")
        for job_name, job in load(path)["jobs"].items()
        if job["permissions"].get("discussions") == "write"
    }
    assert writers == {(reusable(assistant.DISCUSSION_WORKFLOW).name, "apply")}
    assert {name: job["permissions"] for name, job in DISCUSSION_JOBS.items()} == {
        "event": {"contents": "read", "issues": "read", "discussions": "read"},
        "analyze": {"contents": "read", "issues": "read", "discussions": "read"},
        RESERVE: {"contents": "read", "issues": "read", "discussions": "read"},
        "apply": {"contents": "read", "issues": "read", "discussions": "write"},
    }
    # The event job of the issues counts the new discussions for the limits.
    assert JOBS["event"]["permissions"]["discussions"] == "read"
    for caller in ASSISTANT["jobs"].values():
        assert caller["permissions"]["discussions"] == "read"


def test_a_new_discussion_triggers_the_workflow_of_the_discussions():
    assert DISCUSSIONS[True]["discussion"] == {"types": ["created"]}
    assert set(DISCUSSIONS[True]) == {"discussion", "workflow_dispatch"}
    condition = " ".join(DISCUSSION_JOBS["event"]["if"].split())
    # A call for another event, such as a pull request, skips every job: the event of
    # the action reads a discussion or a manual run alone. The smoke test of the
    # Workflow test alone takes the discussion of its file.
    assert condition == (
        f"!github.event.repository.fork && (inputs.{SMOKE_INPUT} || "
        "github.event_name == 'workflow_dispatch' || "
        "(github.event_name == 'discussion' && "
        "github.event.sender.type != 'Bot'))"
    )
    # The runs of one discussion one after the other; issues and discussions share
    # their numbers, so the group names the workflow.
    assert DISCUSSIONS["concurrency"] == {
        "group": "discussion-assistant-${{ github.event.discussion.number || "
        "inputs.discussion }}",
        "cancel-in-progress": False,
    }


def test_the_events_of_an_issue_wait_their_turn_instead_of_being_dropped():
    """Runs for one issue run one after the other, first in, first out, and none is
    dropped.

    By default a group keeps one waiting run and cancels it for the next one, so a
    comment written while an analysis ran could be lost: an answer of the reporter
    without a follow-up, or a "still broken" after a release that reopens nothing.
    """
    # The runs on the backlog wait for each other, not for the runs of the issue
    # whose number they start after.
    assert ASSISTANT["concurrency"] == {
        "group": "issue-assistant-${{ github.event.issue.number || "
        "(inputs.mode == 'backlog' && 'backlog') || inputs.issue }}",
        # GitHub refuses a queue together with cancel-in-progress.
        "cancel-in-progress": False,
        "queue": "max",
    }


# The scans of a pull request wait in a group of their branch, so that they never
# take the place of a run of the default branch that waits (decision 0028).
BRANCH_GROUP = (
    "github.event.workflow_run.event == 'pull_request' && "
    "format('findings-pull-request-{0}', github.event.workflow_run.head_branch) || "
)


@pytest.mark.parametrize(
    ("name", "group", "branch_group"),
    [
        ("issue-lifecycle.yml", "issue-lifecycle", ""),
        ("findings.yml", "findings", BRANCH_GROUP),
    ],
)
def test_a_run_that_skips_its_job_never_takes_the_place_of_a_waiting_one(
    name, group, branch_group
):
    """The job, not the run, waits for the group.

    A whole run waits for a group of the workflow before its job checks its
    condition, so a run that would skip its job, such as after a scheduled Release
    run or a scan of a pull request, cancelled the waiting run that had work to do.
    A job that its condition skips never enters the group of the job: on GitHub, a
    run whose jobs were all skipped left a job waiting in their group alone
    (2026-10-13).
    """
    template = load(TEMPLATES / name)
    (caller,) = template["jobs"].values()
    assert "concurrency" not in template and "concurrency" not in caller
    workflow = load(reusable(name))
    assert "concurrency" not in workflow
    (job,) = tool_jobs(name).values()
    # A dry run writes nothing, so it waits for no other run and replaces none.
    assert job["concurrency"] == {
        "group": f"${{{{ inputs.dry_run && format('{group}-dry-run-{{0}}', "
        f"github.run_id) || {branch_group}'{group}' }}}}",
        "cancel-in-progress": False,
    }


def test_merges_and_releases_start_the_lifecycle_without_running_pull_request_code():
    triggers = LIFECYCLE[True]
    assert triggers["pull_request_target"] == {"types": ["closed"]}
    assert triggers["release"] == {"types": ["published"]}
    # A release published with the GITHUB_TOKEN starts no workflow.
    assert triggers["workflow_run"] == {
        "workflows": ["Release", "Release integration"],
        "types": ["completed"],
    }
    assert "pull_request" not in triggers
    (job,) = load(reusable("issue-lifecycle.yml"))["jobs"].values()
    condition = " ".join(job["if"].split())
    assert "github.event.pull_request.merged" in condition
    assert "github.event.pull_request.user.login != 'dependabot[bot]'" in condition
    # A release workflow that runs every hour as a safety net mostly releases
    # nothing; the morning sweep catches a release of a scheduled run.
    assert (
        "(github.event_name != 'workflow_run' || "
        "(github.event.workflow_run.conclusion == 'success' && "
        "github.event.workflow_run.event != 'schedule'))"
    ) in condition
    assert job["permissions"] == {
        "contents": "read",
        "issues": "write",
        "pull-requests": "read",
    }
    _, _, _, guard, checkout, tool, step, _ = job["steps"]
    assert guard["name"] == GUARD["name"]
    # The default branch: no ref of the pull request, no credentials left behind.
    assert checkout["with"] == {"persist-credentials": False}
    assert tool["with"] == TOOL_CHECKOUT and step["uses"] == TOOL
    assert step["with"]["command"] == "sweep"


def test_the_findings_workflow_may_only_dismiss_alerts_and_runs_no_ai():
    triggers = FINDINGS[True]
    assert set(triggers) == {"schedule", "push", "workflow_run", "workflow_dispatch"}
    assert triggers["push"] == {
        "branches": ["main"],
        "paths": [assistant.FINDINGS_FILE.as_posix()],
    }
    # Every workflow of the blueprint that reports to code scanning: a new finding
    # is accepted or reported right after the scan, not the next morning.
    assert triggers["workflow_run"] == {
        "workflows": [
            "CodeQL",
            "OpenSSF Scorecard",
            "Workflow security",
            "OSV-Scanner",
        ],
        "types": ["completed"],
    }
    assert triggers["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True
    job = load(reusable("findings.yml"))["jobs"]["findings"]
    assert job["permissions"] == {"contents": "read", "security-events": "write"}
    condition = " ".join(job["if"].split())
    # Only code scanning of the default branch and of the pull requests of this
    # repository, never of a fork (decision 0028).
    assert "head_repository.full_name == github.repository" in condition
    assert "head_branch == github.event.repository.default_branch" in condition
    assert "workflow_run.event == 'pull_request'" in condition
    _, _, _, guard, checkout, tool, newest, record, step, keep, _ = job["steps"]
    assert guard["name"] == GUARD["name"]
    assert checkout["with"] == {"persist-credentials": False}
    assert tool["with"] == TOOL_CHECKOUT and step["uses"] == TOOL
    assert step["with"]["command"] == "findings"
    assert "environment" not in job


def test_the_findings_keep_the_record_of_the_reported_alerts_in_the_cache():
    """An open alert fails one run, not every run after each scan and every morning,
    which GitHub would mail the maintainer about: the record of the alerts that a run
    reported goes from run to run in the cache of the default branch (decision 0026).
    """
    jobs = load(reusable("findings.yml"))["jobs"]
    reader, job = jobs[RECORD_JOB], jobs["findings"]
    names = [step.get("name") for step in job["steps"]]
    restore = step_named(reader, "Restore the record of the reported alerts")
    check = step_named(reader, "Check the record of the reported alerts")
    newest = step_named(job, "Look up the newest record of the reported alerts")
    put = step_named(job, "Put the record of the reported alerts in place")
    keep = step_named(job, "Keep the record of the reported alerts")
    findings = names.index("Dismiss accepted findings and report the others")
    # After the checkouts, whose clean would remove the record, around the findings.
    assert names.index(newest["name"]) == findings - 2
    assert names.index(put["name"]) == findings - 1
    assert names.index(keep["name"]) == findings + 1
    record = assistant.FINDINGS_RECORD.as_posix()
    hashed = f"hashFiles('{record}')"
    for step, part in ((restore, "restore"), (newest, "restore"), (keep, "save")):
        action, _, ref = step["uses"].partition("@")
        assert action == f"actions/cache/{part}" and assistant.SHA.fullmatch(ref)
        # A cache that fails or hangs stops neither the findings nor the job.
        assert step["continue-on-error"] is True and step["timeout-minutes"] == 1
        assert step["with"]["path"] == record
    # The newest record of the branch, by a key that no record has.
    assert restore["id"] == "restore"
    assert restore["with"]["key"] == FINDINGS_CACHE + "${{ github.run_id }}"
    assert restore["with"]["restore-keys"] == FINDINGS_CACHE
    assert "if" not in restore
    # The runs after the scans of a push overlap, and the job record of one reads the
    # record before the run ahead keeps a newer one at its end: once the findings'
    # turn has come in their group, after the run ahead, they look up the key of the
    # newest record by the same keys, without downloading it, and the issue assistant
    # fails for no alert of a record that a newer one replaced.
    assert newest["id"] == "newest"
    assert newest["with"] == {**restore["with"], "lookup-only": True}
    assert newest["if"] == "needs.record.outputs.key != ''"
    assert "concurrency" in job and "concurrency" not in reader
    assert step_named(job, "Dismiss accepted findings and report the others")[
        "env"
    ] == {
        "RECORD_KEY": "${{ needs.record.outputs.key }}",
        "NEWEST_RECORD_KEY": "${{ steps.newest.outputs.cache-matched-key }}",
    }
    assert reader["outputs"] == {
        "record": "${{ steps.check.outputs.record }}",
        "key": "${{ steps.restore.outputs.cache-matched-key }}",
    }
    assert check["id"] == "check" and check["env"] == {"RECORD": record}
    # Through the environment alone, never as an expression in the script.
    assert put["env"] == {"RECORD": "${{ needs.record.outputs.record }}"}
    assert put["if"] == "needs.record.outputs.record != ''"
    assert "${{" not in check["run"] + put["run"]
    # A new record alone, by the hash of the file, also after a failed run; a dry run
    # writes none, and keeps none, nor does a job that Harden-Runner left unblocked.
    assert keep["with"]["key"] == FINDINGS_CACHE + "${{ " + hashed + " }}"
    assert " ".join(keep["if"].split()) == (
        f"!cancelled() && !inputs.dry_run && {hashed} != '' && "
        "needs.record.outputs.key != "
        f"format('{FINDINGS_CACHE}{{0}}', {hashed}) && {BLOCKS_GUARD}"
    )
    # The findings run also when the record couldn't be read, with none then, and
    # after the scan of a pull request of the repository, which needs no record and
    # whose run the job record skips (decision 0028).
    assert job["needs"] == RECORD_JOB
    default_branch = (
        "github.event.workflow_run.head_branch == "
        "github.event.repository.default_branch"
    )
    assert " ".join(reader["if"].split()) == (
        "!github.event.repository.fork && (github.event_name != 'workflow_run' || "
        "(github.event.workflow_run.head_repository.full_name == github.repository && "
        f"{default_branch}))"
    )
    assert " ".join(job["if"].split()) == (
        "!cancelled() && !github.event.repository.fork && "
        "(github.event_name != 'workflow_run' || "
        "(github.event.workflow_run.head_repository.full_name == github.repository && "
        f"({default_branch} || github.event.workflow_run.event == 'pull_request')))"
    )


def test_no_job_that_may_write_unpacks_a_cache():
    """actions/cache unpacks an archive with every path that it holds, also outside
    the workspace, such as the action that the job runs next, and every workflow of
    the default branch can write that cache. So the record of the findings comes out
    of the cache in a job without any scope of the token, which runs no issue
    assistant and hands on only a record that it checked (decision 0026)."""
    for name in NAMES:
        for job_name, job in load(reusable(name))["jobs"].items():
            # A lookup asks for the key alone, and downloads and unpacks nothing.
            restores = [
                step
                for step in job["steps"]
                if step.get("uses", "").startswith("actions/cache")
                and not step["uses"].startswith("actions/cache/save@")
                and step.get("with", {}).get("lookup-only") is not True
            ]
            if (name, job_name) == ("findings.yml", RECORD_JOB):
                assert job["permissions"] == {}
                assert len(restores) == 1
                assert not any(
                    step.get("uses", "").startswith("actions/checkout@")
                    or step.get("uses") == TOOL
                    for step in job["steps"]
                )
            else:
                assert not restores, (name, job_name)


def record_script(step):
    """The script of `step` with the Python of this test in place of the image's."""
    python = shlex.quote(Path(sys.executable).as_posix())
    return step["run"].replace("/usr/bin/python3", python)


def run_record_step(step, cwd, **env):
    output = cwd / "output"
    output.touch()
    result = subprocess.run(
        [shutil.which("bash"), "-e", "-c", record_script(step)],
        cwd=cwd,
        env={**os.environ, **env, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return output.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (None, ""),
        ("{}", "{}"),
        (
            '{"7": "2026-11-08T04:08:48Z", "12": ""}',
            '{"7":"2026-11-08T04:08:48Z","12":""}',
        ),
        # Anything but numbers and their times is a broken record.
        ("[]", "null"),
        ("not json", "null"),
        ('{"seven": "2026-11-08T04:08:48Z"}', "null"),
        ('{"7": 1}', "null"),
        ('{"7": "a\\nb"}', "null"),
        ('{"1234567890": ""}', "null"),
        ('{"7": "' + "1" * 41 + '"}', "null"),
        # Even a record of numbers and times, once it is larger than 64 KB.
        (
            json.dumps(dict.fromkeys(map(str, range(4000)), "2026-11-08T04:08:48Z")),
            "null",
        ),
        ("\udcff", "null"),
    ],
)
def test_only_a_checked_record_leaves_the_job_record(tmp_path, content, expected):
    step = step_named(
        load(reusable("findings.yml"))["jobs"][RECORD_JOB],
        "Check the record of the reported alerts",
    )
    record = tmp_path / assistant.FINDINGS_RECORD
    if content is not None:
        record.parent.mkdir(parents=True)
        record.write_bytes(content.encode("utf-8", "surrogateescape"))
    output = run_record_step(
        step, tmp_path, RECORD=assistant.FINDINGS_RECORD.as_posix()
    )
    assert output == f"record={expected}\n"


def test_a_directory_is_no_record(tmp_path):
    step = step_named(
        load(reusable("findings.yml"))["jobs"][RECORD_JOB],
        "Check the record of the reported alerts",
    )
    (tmp_path / assistant.FINDINGS_RECORD).mkdir(parents=True)
    output = run_record_step(
        step, tmp_path, RECORD=assistant.FINDINGS_RECORD.as_posix()
    )
    assert output == "record=null\n"


def test_the_record_comes_back_as_the_issue_assistant_wrote_it(tmp_path):
    """The record that the findings get from the job record has the bytes that the
    issue assistant wrote, so its hash is the key that the cache holds it under, and
    a run that finds no new alert keeps no new entry."""
    jobs = load(reusable("findings.yml"))["jobs"]
    written = tmp_path / "written"
    alerts = [
        {"number": 12, "updated_at": "2026-11-08T04:08:48Z"},
        {"number": 3, "updated_at": None},
    ]
    keep_reported(written, alerts)
    original = (written / assistant.FINDINGS_RECORD).read_bytes()
    source = tmp_path / "source"
    (source / assistant.FINDINGS_RECORD).parent.mkdir(parents=True)
    (source / assistant.FINDINGS_RECORD).write_bytes(original)
    check = step_named(jobs[RECORD_JOB], "Check the record of the reported alerts")
    output = run_record_step(check, source, RECORD=assistant.FINDINGS_RECORD.as_posix())
    record = output.removeprefix("record=").removesuffix("\n")
    target = tmp_path / "target"
    target.mkdir()
    put = step_named(jobs["findings"], "Put the record of the reported alerts in place")
    run_record_step(put, target, RECORD=record)
    assert (target / assistant.FINDINGS_RECORD).read_bytes() == original


def test_the_firewall_template_allows_only_named_hosts():
    policy = load(assistant.TEMPLATES / assistant.FIREWALL)
    assert policy["mode"] == "enforce"
    assert "no-default-urls" not in policy
    hosts = policy["allow"]
    assert len(hosts) == len(set(hosts))
    assert all("*" not in host and "/" not in host for host in hosts)
    assert {"api.github.com"} <= set(hosts)
    assert {rules["host"] for rules in ENGINE_RULES.values()} <= set(hosts)


def test_the_starter_labels_have_every_lifecycle_label():
    data = tomllib.loads(
        (assistant.TEMPLATES / assistant.LABELS_FILE).read_text(encoding="utf-8")
    )
    lifecycle = {item["name"] for item in data["label"] if item["group"] == "lifecycle"}
    assert set(assistant.LIFECYCLE) == lifecycle


def test_the_starter_labels_have_what_a_maintainer_offers_to_newcomers():
    data = tomllib.loads(
        (assistant.TEMPLATES / assistant.LABELS_FILE).read_text(encoding="utf-8")
    )
    offered = [
        item["name"] for item in data["label"] if item["group"] == "contribution"
    ]
    assert offered == ["good first issue", "help wanted"]


def test_mute_creates_the_label_of_the_starter_and_of_this_repository(sample_config):
    """The label of muted issues is the maintainer's decision, so labels.toml need
    not define it; where it doesn't, /assistant mute creates it as the starter has it.
    """
    for path in (assistant.TEMPLATES, ROOT):
        data = tomllib.loads((path / assistant.LABELS_FILE).read_text("utf-8"))
        (label,) = [item for item in data["label"] if item["name"] == assistant.MUTED]
        made = assistant.muted_label(dataclasses.replace(sample_config, labels=()))
        assert label == {
            "name": made.name,
            "color": made.color,
            "description": made.description,
            "group": made.group,
        }


# Names of AI vendors and their products; only the engine table may name one.
VENDORS = re.compile(r"Claude|Anthropic|OpenAI|Codex|Copilot|GPT|Gemini", re.IGNORECASE)


@pytest.mark.mutation_unsafe
def test_only_the_engine_table_names_a_vendor():
    source = package_source()
    table = re.search(r"^ENGINES = \{.*?\}$", source, re.MULTILINE | re.DOTALL)
    assert table and VENDORS.search(table[0])
    rest = re.sub(
        r"^DEFAULT_ENGINE = .*$", "", source.replace(table[0], ""), flags=re.MULTILINE
    )
    assert not VENDORS.search(rest)
    for prompt in assistant.PROMPTS.glob("*.md"):
        assert not VENDORS.search(prompt.read_text(encoding="utf-8")), prompt.name


def test_the_version_is_the_same_everywhere():
    # The release bot writes the version of each release into both.
    manifest = json.loads(
        (ROOT / ".release-please-manifest.json").read_text(encoding="utf-8")
    )
    version = (ROOT / "version.txt").read_text(encoding="utf-8").strip()
    assert version == manifest["."]


def test_the_action_metadata_is_complete():
    assert ACTION["runs"]["using"] == "composite"
    assert ACTION["name"] and ACTION["description"] and ACTION["branding"]
    assert len(ACTION["description"]) <= 125, "the Marketplace shows 125 characters"


def test_this_repository_s_own_settings_fit(tmp_path):
    """The repository uses the action itself, so the whole check applies.

    Its workflows and firewall policy are the blueprint's copies of the templates of
    the release that it pins, which the check issue-assistant of the Lint workflow
    compares with them. Here the check of this commit sees the set-up as the
    blueprint brings it with the next release: with the templates of this commit.
    """
    config = assistant.load_config(ROOT, "Dennis-Otto/issue-assistant")
    own = tmp_path / "issue-assistant"
    shutil.copytree(
        ROOT / ".github",
        own / ".github",
        ignore=shutil.ignore_patterns("node_modules"),
    )
    for name in (".gitignore", *config.notices):
        (own / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, own / name)
    (own / assistant.FIREWALL).unlink()
    done = assistant.install(own, "0" * 40, "v1.0.0")
    # The starter files, the forms, the notice to reporters, the entry of .gitignore
    # and the runner in actionlint's settings are all there already; the settings
    # of the blueprint may not ignore the queue of issue-assistant.yml yet.
    queue = f"let actionlint ignore the queue of issue-assistant.yml in {assistant.ACTIONLINT}"
    # Its settings answer no discussions, so it gets no workflow of the discussions.
    assert [line for line in done if line != queue] == [
        f"wrote {assistant.WORKFLOWS / name}"
        for name in NAMES
        if name != assistant.DISCUSSION_WORKFLOW
    ] + [f"wrote {assistant.FIREWALL} for the engine {config.engine}"]
    assert assistant.check(dataclasses.replace(config, root=own)) == []
    defined = {label.name for label in config.labels}
    # The labels that the release bot, the branch bot and Dependabot set.
    used = {"autorelease: pending", "autorelease: tagged", "merge-conflict"}
    used |= {"dependencies"}
    assert used <= defined, used - defined


# The area labels of pull requests, which actions/labeler gives by the files that a
# pull request changes.
LABELER = load(ROOT / ".github/labeler.yml")


def area_globs(label):
    return [
        glob
        for rule in LABELER[label]
        for files in rule["changed-files"]
        for glob in files["any-glob-to-any-file"]
    ]


def matches(glob, path):
    """Whether a glob of the labeler matches a path: ** spans folders, * stays in one,
    and both match names that start with a dot."""
    pattern = re.escape(glob).replace(r"\*\*/", "(?:.*/)?").replace(r"\*\*", ".*")
    return re.fullmatch(pattern.replace(r"\*", "[^/]*"), path) is not None


def test_globs_match_as_the_labeler_matches_them():
    assert matches("templates/**", "templates/.github/labels.toml")
    assert matches("**/x.yml", "x.yml") and matches("**/x.yml", "a/.b/x.yml")
    assert matches("a/*.yml", "a/b.yml") and not matches("a/*.yml", "a/b/c.yml")
    assert not matches("action.yml", "action.yaml")


def test_the_area_labels_of_pull_requests_exist_and_match_files():
    config = assistant.load_config(ROOT, "Dennis-Otto/issue-assistant")
    areas = {label.name for label in config.labels if label.group == "area"}
    assert LABELER and set(LABELER) <= areas, set(LABELER) - areas
    # A glob that matches no file has lost its file, or names it wrong. It looks at
    # the files on disk, not at git: the mutation tests run a copy without .git.
    for label in LABELER:
        for pattern in area_globs(label):
            found = files_matching(
                pattern, root_dir=ROOT, recursive=True, include_hidden=True
            )
            assert any((ROOT / path).is_file() for path in found), pattern


def test_the_starter_forms_are_what_github_reads():
    # GitHub shows an invalid form as a plain text box, and says so only to those who
    # open the file in the repository.
    for form in assistant.FORM_STARTERS.values():
        starter = load(assistant.TEMPLATES / form)
        assert {"name", "description", "body"} <= set(starter)
        ids = [item["id"] for item in starter["body"] if "id" in item]
        assert len(ids) == len(set(ids)) and ids
        assert any(
            item.get("validations", {}).get("required") for item in starter["body"]
        )


def test_the_starter_chooser_is_what_github_reads():
    # GitHub reads only these keys, and only with a whole URL: no link of its own
    # repository can be relative, so install writes the owner and the name.
    chooser = load(assistant.TEMPLATES / assistant.CHOOSER)
    assert set(chooser) == {"blank_issues_enabled", "contact_links"}
    assert chooser["blank_issues_enabled"] is False
    for link in chooser["contact_links"]:
        assert set(link) == {"name", "url", "about"}
    (asks, reports, *_) = chooser["contact_links"]
    assert asks["url"].startswith("https://github.com/OWNER/NAME/discussions/")
    assert reports["url"] == "https://github.com/OWNER/NAME/security/advisories/new"


def test_a_change_of_what_install_writes_is_labeled_setup():
    # install writes the workflows, the firewall policy and the starter files that a
    # repository lacks, all from templates/; action.yml runs the script. This
    # repository's own copies in .github/ are the blueprint's and change with it.
    templates = assistant.TEMPLATES.relative_to(ROOT).as_posix()
    written = [
        *(assistant.WORKFLOWS / name for name in NAMES),
        assistant.FIREWALL,
        *assistant.STARTERS,
        *assistant.FORM_STARTERS.values(),
        assistant.CHOOSER,
        assistant.NOTICE,
        assistant.NOTICE_DE,
    ]
    globs = area_globs("area: setup")
    # And the jobs that the workflows call, which every repository runs.
    jobs = [reusable(name).relative_to(ROOT).as_posix() for name in NAMES]
    for path in [*(f"{templates}/{path}" for path in written), *jobs, "action.yml"]:
        assert (ROOT / path).is_file(), path
        assert any(matches(glob, path) for glob in globs), path


def test_every_output_of_the_action_that_a_workflow_reads_is_declared():
    # A composite action passes on only the outputs that action.yml declares:
    # an output that only its inner step writes reaches the workflow empty.
    declared = set(ACTION["outputs"])
    pattern = re.compile(r"steps\.([\w-]+)\.outputs\.([\w-]+)")
    read = set()
    for path in OWN.glob(f"{assistant.REUSABLE_PREFIX}*.yml"):
        for job in load(path)["jobs"].values():
            local = {
                step["id"]
                for step in job.get("steps", [])
                if step.get("uses") == "./.issue-assistant/action" and "id" in step
            }
            text = json.dumps(job)
            read |= {name for step, name in pattern.findall(text) if step in local}
    assert read
    assert read <= declared, sorted(read - declared)
