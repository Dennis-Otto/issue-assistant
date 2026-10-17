"""The templates keep the AI engine read-only and every write in checked code.

Issues come from anyone. These tests fail when a change gives the job that runs an
engine a write permission, a tool that runs code or reaches the network, when an
engine has no rules here, or when untrusted text reaches a shell script.
"""

import dataclasses
import json
import re
import shlex
import shutil
import subprocess
import tomllib
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
        )
    ),
    "sparse-checkout-cone-mode": False,
    "persist-credentials": False,
}

# The runner of every job without the AI: a fixed version of Ubuntu, so that a new
# image behind ubuntu-latest never reaches the repositories untested.
RUNNER = "ubuntu-24.04"
HARDEN_RUNNER = "step-security/harden-runner"
# The hosts that a job without the AI reaches: the checkout and the GitHub CLI.
# Harden-Runner allows the hosts of the runner itself, such as those of the logs.
GITHUB_HOSTS = ["api.github.com:443", "github.com:443"]


def claude_reads_only(step):
    inputs = step["with"]
    assert inputs["github_token"] == "${{ github.token }}"
    assert inputs["allowed_non_write_users"] == "*"
    assert "allowed_bots" not in inputs
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
        # The inputs that let a run of the Canary that a bot started use the engine.
        "canary": {"allowed_bots": "*"},
    },
}


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
    # This repository looks after its own issues with them as well.
    assert set(NAMES) <= {path.name for path in OWN.glob("*.yml")}
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
    assert load(reusable(name))[True]["workflow_call"]["inputs"] == {
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
    """The template passes each engine's secrets by name; the job analyze, in the
    environment issue-assistant, gets their values, and no other job names them."""
    (job,) = ASSISTANT["jobs"].values()
    declared = load(reusable("issue-assistant.yml"))[True]["workflow_call"]["secrets"]
    secrets = [secret for rules in ENGINE_RULES.values() for secret in rules["secrets"]]
    assert job["secrets"] == {
        secret: f"${{{{ secrets.{secret} }}}}" for secret in secrets
    }
    assert list(declared) == secrets
    assert all(declared[secret]["required"] is False for secret in secrets)
    for name in NAMES:
        if name != "issue-assistant.yml":
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
    # The AI runs behind GitHub's egress firewall, whose runner has a label of its own.
    expected = assistant.FIREWALL_RUNNER if job_name == "analyze" else RUNNER
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


def test_the_action_runs_in_ci_behind_the_block_of_the_templates():
    """The Action test runs the action behind the egress block of the templates.

    So a new host that the action or the GitHub CLI needs fails a check here, before
    a release blocks it in every repository.
    """
    jobs = load(OWN / "action-test.yml")["jobs"]
    (job,) = [
        job
        for job in jobs.values()
        if job["steps"][0].get("uses", "").startswith(f"{HARDEN_RUNNER}@")
        and job["steps"][0]["with"]["egress-policy"] == "block"
    ]
    assert job["runs-on"] == RUNNER
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


def test_the_graphql_documents_meet_github_s_schema_of_the_day_every_week():
    """The tests check them against a copy of the schema; GitHub changes it any day."""
    workflow = load(OWN / "action-test.yml")
    # PyYAML reads the key "on" as True.
    assert workflow[True]["schedule"]
    job = workflow["jobs"]["schema"]
    assert job["if"] == (
        "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'"
    )
    (download,) = [step for step in job["steps"] if "curl" in step.get("run", "")]
    assert "https://docs.github.com/public/fpt/schema.docs.graphql" in download["run"]
    (check,) = [
        step for step in job["steps"] if "GRAPHQL_SCHEMA" in step.get("env", {})
    ]
    assert check["run"].endswith("tests/test_graphql_contract.py")


# The Canary runs the AI's path of the template every week, for an issue of a file.
CANARY = load(OWN / "canary.yml")
CANARY_ISSUE = "tests/fixtures/canary-issue.json"


def test_the_canary_runs_the_ai_as_the_template_does():
    """The Canary's job analyze is the template's: the runner, the environment, the
    permissions, the outputs and the steps of the engines.

    So an update of an engine's action that renames its output or needs another
    host, or a secret that expired, fails there before a release brings it to every
    repository. Only the steps of the issue assistant run the action of this commit.
    """
    analyze = CANARY["jobs"]["analyze"]
    for key in ("runs-on", "timeout-minutes", "environment", "permissions", "outputs"):
        assert analyze[key] == ANALYZE[key], key
    assert analyze["if"] == "github.event_name != 'push'"
    steps = {step.get("id"): step for step in analyze["steps"]}
    # The same checkout of the tool as in every repository, so that the release,
    # which waits for the canary, runs the path of production.
    assert analyze["steps"][:2] == ANALYZE["steps"][:2]
    assert analyze["steps"][1]["with"] == TOOL_CHECKOUT
    for engine, rules in ENGINE_RULES.items():
        inputs = dict(STEPS[engine]["with"], **rules["canary"])
        assert steps[engine] == dict(STEPS[engine], **{"with": inputs}), engine
    engine_inputs = dict(STEPS["engine"]["with"], mode="triage")
    assert steps["engine"] == dict(STEPS["engine"], **{"with": engine_inputs})
    usage_inputs = {
        key: value for key, value in STEPS["usage"]["with"].items() if key != "issue"
    }
    usage_inputs["mode"] = "triage"
    assert steps["usage"] == dict(STEPS["usage"], **{"with": usage_inputs})
    context = steps["context"]
    assert context["uses"] == TOOL and context["if"] == STEPS["context"]["if"]
    assert context["with"] == {"command": "context", "mode": "triage"}
    assert context["env"] == {assistant.FIXTURE_VARIABLE: CANARY_ISSUE}
    order = [step.get("id") for step in analyze["steps"]]
    assert (
        order.index("engine")
        < order.index("context")
        < min(order.index(engine) for engine in ENGINE_RULES)
    )
    # Where a repository's run skips the AI, without its secret or without an
    # answer, the canary fails.
    (unready,) = [
        step
        for step in analyze["steps"]
        if step.get("if") == "steps.engine.outputs.ready != 'true'"
    ]
    assert unready["run"].rstrip().endswith("exit 1")
    answered = analyze["steps"][-1]
    assert answered["env"] == {"ANSWER": ANALYZE["outputs"]["answer"]}
    assert '[[ -z "$ANSWER" ]]' in answered["run"]


def test_the_canary_checks_the_answer_as_apply_does_and_writes_nothing():
    assert CANARY["permissions"] == {}
    jobs = CANARY["jobs"]
    # Only the job start may write, and only to start the canary.
    assert jobs["start"]["permissions"] == {"actions": "write"}
    for name, job in jobs.items():
        if name != "start":
            assert "write" not in job["permissions"].values(), name
        assert "id-token" not in job["permissions"], name
    apply = CANARY["jobs"]["apply"]
    template = JOBS["apply"]
    assert apply["needs"] == "analyze"
    assert apply["runs-on"] == template["runs-on"]
    # The template's job may write to the issue; the canary's can only read.
    assert apply["permissions"] == dict(template["permissions"], issues="read")
    assert apply["steps"][:3] == template["steps"][:3]
    assert apply["steps"][2]["with"] == TOOL_CHECKOUT
    (step,) = [step for step in apply["steps"] if step.get("uses") == TOOL]
    assert step["with"] == {
        "command": "apply",
        "mode": "triage",
        "engine": "${{ needs.analyze.outputs.engine }}",
        "answer": "${{ needs.analyze.outputs.answer }}",
        "dry-run": True,
    }
    assert step["env"] == {assistant.FIXTURE_VARIABLE: CANARY_ISSUE}
    assert "environment" not in apply
    for rules in ENGINE_RULES.values():
        for secret in rules["secrets"]:
            assert secret not in yaml.safe_dump(apply)


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
    # It waits for the canary's analysis, which may wait for an earlier one, and
    # for its apply.
    jobs = CANARY["jobs"]
    waits = 2 * jobs["analyze"]["timeout-minutes"] + jobs["apply"]["timeout-minutes"]
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
    (step,) = ACTION["runs"]["steps"]
    assert step["env"]["GH_TELEMETRY"] == "false"


@pytest.mark.parametrize("path", WORKFLOWS, ids=workflow_id)
def test_actions_are_pinned_and_checkouts_keep_no_credentials(path):
    # Workflows that issues, comments or other workflows start run the default branch;
    # the release workflow checks out the tag that it releases.
    events = load(path)[True]
    privileged = not {
        "issues",
        "issue_comment",
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
    (step,) = ACTION["runs"]["steps"]
    assert step["run"] == 'python3 "$GITHUB_ACTION_PATH/issue_assistant.py" "$COMMAND"'
    inputs = {f"${{{{ inputs.{name} }}}}" for name in ACTION["inputs"]}
    assert inputs <= set(step["env"].values())
    for name, output in ACTION["outputs"].items():
        assert output["value"] == f"${{{{ steps.run.outputs.{name} }}}}"


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


def test_only_the_analyze_job_runs_an_engine_and_only_with_read_permissions():
    actions = {rules["action"] for rules in ENGINE_RULES.values()}
    runs_engine = {
        name
        for name, job in JOBS.items()
        if any(step.get("uses", "").split("@")[0] in actions for step in job["steps"])
    }
    assert runs_engine == {"analyze"}
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
    (apply_step,) = [step for step in JOBS["apply"]["steps"] if step["uses"] == TOOL]
    assert apply_step["with"]["command"] == "apply"
    assert apply_step["with"]["answer"] == "${{ needs.analyze.outputs.answer }}"
    assert apply_step["with"]["engine"] == "${{ needs.analyze.outputs.engine }}"
    assert JOBS["apply"]["needs"] == ["event", "analyze"]
    assert JOBS["apply"]["permissions"] == {
        "contents": "read",
        "issues": "write",
        "discussions": "read",
    }


def test_the_manual_run_offers_every_task_and_starts_as_a_dry_run():
    inputs = ASSISTANT[True]["workflow_dispatch"]["inputs"]
    assert inputs["mode"]["options"] == list(assistant.MODES)
    assert inputs["dry_run"]["default"] is True
    assert LIFECYCLE[True]["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True


def test_the_events_the_assistant_handles_trigger_it():
    triggers = ASSISTANT[True]
    assert triggers["issues"]["types"] == ["opened", "edited"]
    assert triggers["issue_comment"]["types"] == ["created"]
    condition = " ".join(JOBS["event"]["if"].split())
    assert "!github.event.issue.pull_request" in condition
    assert "github.event.sender.type != 'Bot'" in condition


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
    assert job["concurrency"] == {"group": group, "cancel-in-progress": False}


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
    _, checkout, tool, step = job["steps"]
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
    _, checkout, tool, step = job["steps"]
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
    assert [line for line in done if line != queue] == [
        f"wrote {assistant.WORKFLOWS / name}" for name in NAMES
    ] + [f"wrote {assistant.FIREWALL}"]
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


def test_a_change_of_what_install_writes_is_labeled_setup():
    # install writes the workflows, the firewall policy and the starter files that a
    # repository lacks, all from templates/; action.yml runs the script. This
    # repository's own copies in .github/ are the blueprint's and change with it.
    templates = assistant.TEMPLATES.relative_to(ROOT).as_posix()
    written = [
        *(assistant.WORKFLOWS / name for name in NAMES),
        assistant.FIREWALL,
        *assistant.STARTERS,
        assistant.FORM_STARTER,
        assistant.NOTICE,
        assistant.NOTICE_DE,
    ]
    globs = area_globs("area: setup")
    # And the jobs that the workflows call, which every repository runs.
    jobs = [reusable(name).relative_to(ROOT).as_posix() for name in NAMES]
    for path in [*(f"{templates}/{path}" for path in written), *jobs, "action.yml"]:
        assert (ROOT / path).is_file(), path
        assert any(matches(glob, path) for glob in globs), path
