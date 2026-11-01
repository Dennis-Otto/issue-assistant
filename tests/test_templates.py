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

ROOT = Path(assistant.__file__).parent
# The workflows that install writes into a repository: the triggers, the permissions
# and the pin of a reusable workflow of this repository, which has the jobs.
TEMPLATES = assistant.TEMPLATES / assistant.WORKFLOWS
# This repository's own workflows. The blueprint brings them, the four of the issue
# assistant too, which are the templates of the release that it pins; no test
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
            "issue_assistant.py",
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
# The runner of the AI: GitHub's egress-firewall runner, a technical preview, or the
# runner that the repository's variable names in its place, behind Harden-Runner.
AI_RUNNER = (
    f"${{{{ vars.{assistant.RUNNER_VARIABLE} || '{assistant.FIREWALL_RUNNER}' }}}}"
)
ON_ANOTHER_RUNNER = (
    f"vars.{assistant.RUNNER_VARIABLE} != '' && "
    f"vars.{assistant.RUNNER_VARIABLE} != '{assistant.FIREWALL_RUNNER}'"
)

# The Canary of this repository, .github/workflows/canary.yml, calls the reusable
# workflow of the issues with the input canary, for the issue of a file.
CANARY_INPUT = "canary"
CANARY_ISSUE = "tests/fixtures/canary-issue.json"
# The Workflow test, .github/workflows/workflow-test.yml, calls it with the input
# smoke: the same issue, and the answer of a file in place of the engine's.
SMOKE_INPUT = "smoke"
SMOKE_ANSWER = "tests/fixtures/smoke-answer.json"
OWN_INPUTS = (CANARY_INPUT, SMOKE_INPUT)


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
        assert "--deny-tool" not in command


def steps(path):
    # A job that calls a reusable workflow, such as the release verification, has none.
    return [
        (job_name, step)
        for job_name, job in load(path)["jobs"].items()
        for step in job.get("steps", [])
    ]


# The templates and this repository's own workflows.
WORKFLOWS = sorted(TEMPLATES.glob("*.yml")) + sorted(OWN.glob("*.yml"))


def workflow_id(path):
    return path.relative_to(ROOT).as_posix()


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
    ((job_name, job),) = workflow["jobs"].items()
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
    # and only its Workflow test the input smoke.
    if name == "issue-assistant.yml":
        for own, caller in zip(OWN_INPUTS, ("Canary", "Workflow test"), strict=True):
            given = inputs.pop(own)
            assert given["type"] == "boolean" and given["default"] is False
            assert given["description"].startswith(f"Only for the {caller}")
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
        (job,) = load(TEMPLATES / name)["jobs"].values()
        declared = load(reusable(name))[True]["workflow_call"]["secrets"]
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
    for job_name, job in load(reusable(name))["jobs"].items():
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
    return load(reusable("labels.yml"))["jobs"]["sync"]["steps"][1]


@pytest.mark.parametrize("name", NAMES)
def test_every_job_stops_where_github_names_no_commit_of_its_workflow(name):
    """The guard comes right before the first checkout of each job, the same in
    each."""
    for job_name, job in load(reusable(name))["jobs"].items():
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
    source = (ROOT / "issue_assistant.py").read_text(encoding="utf-8")
    read = {"/action.yml"} | {
        f"/{path}" for path in re.findall(r"""TOOL / ["']([^"']+)["']""", source)
    }
    action = (ROOT / "action.yml").read_text(encoding="utf-8")
    read |= {
        f"/{path}" for path in re.findall(r"\$GITHUB_ACTION_PATH/(\S+?)\"", action)
    }
    assert {path.rstrip("/") for path in checked_out} == read
    assert all((ROOT / path.strip("/")).exists() for path in checked_out)


# Every job of the templates, as (workflow, job name, job).
TEMPLATE_JOBS = [
    (name, job_name, job)
    for name in NAMES
    for job_name, job in load(reusable(name))["jobs"].items()
]
WITHOUT_AI = [item for item in TEMPLATE_JOBS if item[1] != "analyze"]


def template_job_id(item):
    return f"{item[0]}:{item[1]}"


@pytest.mark.parametrize("item", TEMPLATE_JOBS, ids=template_job_id)
def test_every_job_runs_on_a_fixed_version_of_ubuntu(item):
    name, job_name, job = item
    # The AI runs behind GitHub's egress firewall, whose runner has a label of its
    # own, unless the repository names another runner for it.
    expected = AI_RUNNER if job_name == "analyze" else RUNNER
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
    assert first["with"] == {
        "egress-policy": "block",
        "disable-sudo-and-containers": True,
        "allowed-endpoints": " ".join(GITHUB_HOSTS),
    }
    assert not any(step.get("uses", "").startswith(HARDEN_RUNNER) for step in rest)


# The reusable workflows whose job analyze runs an engine, with their jobs.
JOBS_WITH_ENGINE = {
    "issue-assistant.yml": JOBS,
    assistant.DISCUSSION_WORKFLOW: DISCUSSION_JOBS,
}


@pytest.mark.parametrize("name", list(JOBS_WITH_ENGINE))
def test_the_ai_runs_on_another_runner_only_behind_harden_runner(name):
    """GitHub's egress-firewall runner is a technical preview. Should GitHub rename
    or end it, a repository names another runner in ISSUE_ASSISTANT_AI_RUNNER; while
    the variable is unset, every job runs as before.

    On that runner Harden-Runner blocks every host but those of the firewall policy,
    before any other step of analyze, and the next step makes sure that it blocks.
    The job event names the hosts, and fails on a runner where nothing would block.
    """
    jobs = JOBS_WITH_ENGINE[name]
    analyze = jobs["analyze"]
    assert analyze["runs-on"] == AI_RUNNER
    assert analyze["needs"] == "event"
    block, holds, *rest = analyze["steps"]
    action, _, ref = block["uses"].partition("@")
    assert action == HARDEN_RUNNER and assistant.SHA.fullmatch(ref)
    assert block["if"] == holds["if"] == ON_ANOTHER_RUNNER
    # The engines need sudo, to install what isolates them.
    assert block["with"] == {
        "egress-policy": "block",
        "allowed-endpoints": "${{ needs.event.outputs.endpoints }}",
    }
    assert holds["name"] == "Make sure that the block holds"
    for step in rest:
        assert assistant.RUNNER_VARIABLE not in str(step.get("if", "")), step
        assert not step.get("uses", "").startswith(HARDEN_RUNNER), step
    event = jobs["event"]
    assert event["steps"][-1] == {
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


@pytest.mark.parametrize("name", list(JOBS_WITH_ENGINE))
@pytest.mark.parametrize(
    ("answers", "code", "pauses"),
    [
        # A request that fails to resolve, connect or answer in time: the block holds,
        # when the next one fails too, after a pause that a hiccup doesn't outlast.
        ([6, 6], 0, 1),
        ([7, 7], 0, 1),
        ([28, 28], 0, 1),
        # The agent was still starting: the block holds from the third request on.
        ([0, 0, 7, 7], 0, 3),
        # In the last two requests of the minute it asks for.
        ([0] * 10 + [7, 7], 0, 11),
        # One failure among answers is a hiccup of the host, not the block.
        ([0, 28, 0, 28, 0, 28, 0, 28, 0, 28, 0, 28], 1, 12),
        ([0] * 11 + [7], 1, 12),
        # It never blocks.
        ([0] * 12, 1, 12),
    ],
)
def test_the_ai_runs_only_where_the_block_holds(tmp_path, name, answers, code, pauses):
    """Harden-Runner fails open: in a private repository without a subscription of
    StepSecurity, or when its service is down, it blocks nothing. And it waits only
    about ten seconds for its agent, so the block can come a little after its step.
    The step asks for a host of no firewall policy, for up to a minute, and stops the
    job analyze when requests keep getting through; two requests in a row, five seconds
    apart, that fail to resolve, connect or answer in time let it run."""
    holds = JOBS_WITH_ENGINE[name]["analyze"]["steps"][1]
    policy = (assistant.TEMPLATES / assistant.FIREWALL).read_text(encoding="utf-8")
    assert "example.com" not in assistant.allowed_hosts(policy)
    assert "example.com" not in assistant.FIREWALL_DEFAULT_HOSTS
    folder = tmp_path / "bin"
    folder.mkdir()
    arguments = tmp_path / "arguments"
    slept = tmp_path / "slept"
    statuses = tmp_path / "statuses"
    statuses.write_text("".join(f"{answer}\n" for answer in answers), encoding="utf-8")
    # Each request takes the next answer; one too many fails the test.
    (folder / "curl").write_text(
        f'#!/bin/sh\necho "$@" >> "{arguments.as_posix()}"\n'
        f'count=$(wc -l < "{arguments.as_posix()}")\n'
        f'exit "$(sed -n "${{count}}p" "{statuses.as_posix()}")"\n',
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
    ] * len(answers)
    assert (slept.read_text(encoding="utf-8").split() if slept.exists() else []) == [
        "5"
    ] * pauses
    if code:
        assert result.stdout.startswith(
            "::error::Harden-Runner let a request to example.com through"
        )
    else:
        assert result.stdout == (
            "Harden-Runner blocks example.com, a host outside "
            ".github/egress-firewall.yaml.\n"
        )


# The first version that leaves the hosts of allowed-endpoints open on the runners
# with GitHub's newer DNS resolver, seen in westus2: the agent sees none of the
# addresses that they resolve to, so it drops them, a checkout fails, and the job
# never finishes after its last step, at "Cleaning up orphan processes", until its
# timeout. https://github.com/step-security/harden-runner/issues/708
HARDEN_RUNNER_FIXED = (2, 22, 3)
# The blueprint brings its own workflows with their pins, and renders none of these.
PROJECT_WORKFLOWS = [
    OWN / name
    for name in (
        "action-test.yml",
        "canary.yml",
        "evaluate.yml",
        "workflow-test.yml",
    )
] + sorted(OWN.glob("reusable-*.yml"))


@pytest.mark.parametrize("path", PROJECT_WORKFLOWS, ids=lambda path: path.name)
def test_harden_runner_is_a_version_that_lets_the_allowed_hosts_through(path):
    """An older pin drops the allowed hosts, or hangs the job, on the runners in
    westus2. The jobs that the reusable workflows run in every repository, and those
    of the tests here, must not wait for a timeout there."""
    text = path.read_text(encoding="utf-8")
    uses = re.findall(rf"{HARDEN_RUNNER}@.*", text)
    pins = [
        re.fullmatch(rf"{HARDEN_RUNNER}@[0-9a-f]{{40}} # v(\d+)\.(\d+)\.(\d+)", line)
        for line in uses
    ]
    assert pins and all(pins), uses
    for pin in pins:
        assert tuple(int(part) for part in pin.groups()) >= HARDEN_RUNNER_FIXED, uses


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
    assert context["run"].startswith("python3 issue_assistant.py context --issue ")
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
    """The Action test runs the block of analyze on the runner in reserve.

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
    first = {key: value for key, value in JOBS["analyze"]["steps"][0].items()}
    del first["if"]
    first["with"] = {
        **first["with"],
        "allowed-endpoints": "${{ needs.fallback-hosts.outputs.endpoints }}",
    }
    assert block["steps"][0] == first
    holds = JOBS["analyze"]["steps"][1]["run"]
    checks = block["steps"][1]["run"]

    def without_the_error(script):
        return [line for line in script.splitlines() if "::error::" not in line]

    # The same wait for the block, each with its own words for the error.
    waits = without_the_error(holds)
    assert without_the_error(checks)[: len(waits)] == waits
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
    (template,) = ASSISTANT["jobs"].values()
    jobs = CANARY["jobs"]
    # No job of the canary runs an engine or names a secret itself; queue measures
    # the wait of analyze for its runner.
    assert list(jobs) == ["start", "assistant", "queue"]
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
        for path in WORKFLOWS
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
    assert readers == [("analyze", "context"), ("apply", "apply")]


def canary_step(job):
    """The step of the job `job` of the issues' reusable workflow that only the
    Canary runs."""
    condition = f"inputs.{CANARY_INPUT}"
    (step,) = [step for step in JOBS[job]["steps"] if step.get("if") == condition]
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
    # It waits for the jobs of the canary, which may wait for an earlier canary's.
    waits = 2 * sum(job["timeout-minutes"] for job in JOBS.values())
    assert start["timeout-minutes"] >= waits
    paths = triggers["push"]["paths"]
    for path in (
        "action.yml",
        "issue_assistant.py",
        "prompts/**",
        f"{assistant.WORKFLOWS}/{assistant.REUSABLE_PREFIX}issue-assistant.yml",
        f"templates/{assistant.FIREWALL}",
        f"{assistant.WORKFLOWS}/canary.yml",
        CANARY_ISSUE,
    ):
        assert path in paths, path
    for pattern in paths:
        assert next(ROOT.glob(pattern), None), f"{pattern} matches no file"


def start_script():
    (script,) = [s["run"] for s in CANARY["jobs"]["start"]["steps"] if "run" in s]
    return script


# The GitHub CLI of the job start, as a shell function: it notes its arguments and
# answers with the run that GitHub started and that run's conclusion.
FAKE_GH = """
gh() {
  echo "gh $*" >> "$CALLS"
  case "$1 $2" in
    "api --method") [[ -z "$RUN" ]] || echo "$RUN" ;;
    "run view") echo "$CONCLUSION" ;;
  esac
}
"""


@pytest.mark.parametrize(
    ("run", "conclusion", "code", "says"),
    [
        ("42", "success", 0, "The canary passed."),
        # A later push's canary took the place of its waiting analysis.
        ("42", "cancelled", 0, "::notice::The canary was cancelled"),
        ("42", "failure", 1, "::error::The canary ended as failure: "),
        ("42", "timed_out", 1, "::error::The canary ended as timed_out: "),
        # GitHub may start a run without naming it.
        ("", "", 0, "::warning::GitHub started the canary without naming its run"),
    ],
)
def test_the_push_fails_when_the_canary_that_it_started_fails(
    tmp_path, run, conclusion, code, says
):
    """GitHub mails a failed run to the person who started it, and nobody started
    the canary's run after a push but the job's token: the push's own run, which
    the person who merged started, carries the canary's result."""
    calls = tmp_path / "calls"
    env = {
        "CALLS": str(calls),
        "RUN": run,
        "CONCLUSION": conclusion,
        "GH_REPO": "octo/assistant",
        "GITHUB_REF_NAME": "main",
        "GITHUB_SERVER_URL": "https://github.com",
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
        "gh api --method POST repos/octo/assistant/actions/workflows/canary.yml/"
        "dispatches -f ref=main -F return_run_details=true --jq .workflow_run_id"
    )
    if run:
        url = "https://github.com/octo/assistant/actions/runs/42"
        assert f"The canary runs at {url}" in result.stdout
        assert rest == [
            "gh run watch 42 --interval 60",
            "gh run view 42 --json conclusion --jq .conclusion",
        ]
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
    # Workflows that issues, comments or other workflows start run the default branch;
    # the release workflow checks out the tag that it releases.
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
    assert step["run"] == 'python3 "$GITHUB_ACTION_PATH/issue_assistant.py" "$COMMAND"'
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
    assert "issue_assistant.py" not in guard["run"]
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
    assert runs_engine == {"analyze"}
    assert ANALYZE["permissions"] == {
        "contents": "read",
        "issues": "read",
        "discussions": "read",
    }
    assert ANALYZE["runs-on"] == AI_RUNNER
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
    for path in [
        *list(pages.values())[:3],
        ROOT / "action.yml",
        ROOT / "issue_assistant.py",
    ]:
        assert "engine-version" not in path.read_text(encoding="utf-8"), path.name
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
    assert ANALYZE["outputs"] == {
        "engine": "${{ steps.engine.outputs.engine }}",
        "answer": f"${{{{ {answers} }}}}",
    }
    (apply_step,) = [
        step for step in JOBS["apply"]["steps"] if step.get("uses") == TOOL
    ]
    assert apply_step["with"]["command"] == "apply"
    # Or, in the smoke test alone, the answer of its file.
    assert apply_step["with"]["answer"] == (
        "${{ needs.analyze.outputs.answer || steps.smoke.outputs.answer }}"
    )
    assert apply_step["with"]["engine"] == "${{ needs.analyze.outputs.engine }}"
    assert JOBS["apply"]["needs"] == ["event", "analyze"]
    assert JOBS["apply"]["permissions"] == {
        "contents": "read",
        "issues": "write",
        "discussions": "read",
    }


def test_the_manual_run_offers_every_task_and_starts_as_a_dry_run():
    inputs = ASSISTANT[True]["workflow_dispatch"]["inputs"]
    assert inputs["mode"]["options"] == [
        mode for mode in assistant.MODES if mode not in assistant.DISCUSSION_MODES
    ]
    assert inputs["dry_run"]["default"] is True
    # The discussions have one task, for the number of a discussion.
    inputs = DISCUSSIONS[True]["workflow_dispatch"]["inputs"]
    assert list(inputs) == ["discussion", "dry_run"]
    assert inputs["discussion"]["type"] == "number"
    assert inputs["dry_run"]["default"] is True
    assert LIFECYCLE[True]["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True


def test_the_events_the_assistant_handles_trigger_it():
    triggers = ASSISTANT[True]
    assert triggers["issues"]["types"] == ["opened", "edited"]
    assert triggers["issue_comment"]["types"] == ["created"]
    condition = " ".join(JOBS["event"]["if"].split())
    assert "!github.event.issue.pull_request" in condition
    assert "github.event.sender.type != 'Bot'" in condition


def test_the_discussions_analyze_as_the_issues_do():
    """The job analyze of the discussions is the one of the issues, as YAML: every
    rule above that keeps the engine to reading holds for both."""
    assert DISCUSSION_JOBS["analyze"] == without_the_canary(ANALYZE)
    issues = load(reusable("issue-assistant.yml"))[True]["workflow_call"]
    inputs = {
        name: value
        for name, value in issues["inputs"].items()
        if name not in OWN_INPUTS
    }
    assert load(reusable(assistant.DISCUSSION_WORKFLOW))[True] == {
        "workflow_call": dict(issues, inputs=inputs)
    }
    assert list(DISCUSSION_JOBS) == ["event", "analyze", "apply"]
    assert DISCUSSION_JOBS["apply"]["needs"] == ["event", "analyze"]
    # apply runs after an answer of the engine; that of the issues writes out what
    # GitHub otherwise adds, that both jobs passed, since the smoke test skips
    # analyze.
    assert DISCUSSION_JOBS["apply"]["if"] == "needs.analyze.outputs.answer != ''"
    assert " ".join(JOBS["apply"]["if"].split()) == (
        "!cancelled() && needs.event.result == 'success' && "
        f"(inputs.{SMOKE_INPUT} || (needs.analyze.result == 'success' && "
        "needs.analyze.outputs.answer != ''))"
    )
    # Their event and apply jobs run the same commands of the action; only the
    # smoke test gives apply the answer of its file.
    for name in ("event", "apply"):
        ours = [
            step["with"]
            for step in DISCUSSION_JOBS[name]["steps"]
            if step.get("uses") == TOOL
        ]
        theirs = [
            {
                key: value.replace(" || steps.smoke.outputs.answer }}", " }}")
                for key, value in step["with"].items()
            }
            for step in JOBS[name]["steps"]
            if step.get("uses") == TOOL
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
        "apply": {"contents": "read", "issues": "read", "discussions": "write"},
    }
    # The event job of the issues counts the new discussions for the limits.
    assert JOBS["event"]["permissions"]["discussions"] == "read"
    (caller,) = ASSISTANT["jobs"].values()
    assert caller["permissions"]["discussions"] == "read"


def test_a_new_discussion_triggers_the_workflow_of_the_discussions():
    assert DISCUSSIONS[True]["discussion"] == {"types": ["created"]}
    assert set(DISCUSSIONS[True]) == {"discussion", "workflow_dispatch"}
    condition = " ".join(DISCUSSION_JOBS["event"]["if"].split())
    # A call for another event, such as a pull request of the Workflow test, skips
    # every job: the event of the action reads a discussion or a manual run alone.
    assert condition == (
        "!github.event.repository.fork && "
        "(github.event_name == 'workflow_dispatch' || "
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
    assert ASSISTANT["concurrency"] == {
        "group": "issue-assistant-${{ github.event.issue.number || inputs.issue }}",
        # GitHub refuses a queue together with cancel-in-progress.
        "cancel-in-progress": False,
        "queue": "max",
    }


@pytest.mark.parametrize(
    ("name", "group"),
    [("issue-lifecycle.yml", "issue-lifecycle"), ("findings.yml", "findings")],
)
def test_a_run_that_skips_its_job_never_takes_the_place_of_a_waiting_one(name, group):
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
    (job,) = workflow["jobs"].values()
    # A dry run writes nothing, so it waits for no other run and replaces none.
    assert job["concurrency"] == {
        "group": f"${{{{ inputs.dry_run && format('{group}-dry-run-{{0}}', "
        f"github.run_id) || '{group}' }}}}",
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
    _, guard, checkout, tool, step = job["steps"]
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
    (job,) = load(reusable("findings.yml"))["jobs"].values()
    assert job["permissions"] == {"contents": "read", "security-events": "write"}
    condition = " ".join(job["if"].split())
    # Only code scanning of the default branch of this repository, never of a fork.
    assert "head_repository.full_name == github.repository" in condition
    assert "head_branch == github.event.repository.default_branch" in condition
    _, guard, checkout, tool, step = job["steps"]
    assert guard["name"] == GUARD["name"]
    assert checkout["with"] == {"persist-credentials": False}
    assert tool["with"] == TOOL_CHECKOUT and step["uses"] == TOOL
    assert step["with"]["command"] == "findings"
    assert "environment" not in job


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
    source = (ROOT / "issue_assistant.py").read_text(encoding="utf-8")
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
