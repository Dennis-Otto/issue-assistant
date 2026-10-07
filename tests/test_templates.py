"""The templates keep the AI engine read-only and every write in checked code.

Issues come from anyone. These tests fail when a change gives the job that runs an
engine a write permission, a tool that runs code or reaches the network, when an
engine has no rules here, or when untrusted text reaches a shell script.
"""

import re
import shlex
import tomllib
from pathlib import Path

import pytest
import yaml

import issue_assistant as assistant

ROOT = Path(assistant.__file__).parent
# The workflows with which this repository looks after its issues are the templates.
TEMPLATES = OWN = ROOT / assistant.WORKFLOWS
NAMES = assistant.TEMPLATE_WORKFLOWS


def load(path):
    # PyYAML reads the key "on" as True.
    return yaml.safe_load(path.read_text(encoding="utf-8"))


ASSISTANT = load(TEMPLATES / "issue-assistant.yml")
JOBS = ASSISTANT["jobs"]
ANALYZE = JOBS["analyze"]
STEPS = {step.get("id"): step for step in ANALYZE["steps"]}
ACTION = load(ROOT / "action.yml")


def claude_reads_only(step):
    inputs = step["with"]
    assert inputs["github_token"] == "${{ github.token }}"
    assert inputs["allowed_non_write_users"] == "*"
    assert "allowed_bots" not in inputs
    assert inputs["prompt"] == "${{ steps.context.outputs.prompt }}"
    arguments = shlex.split(inputs["claude_args"])
    assert "--restricted" in arguments
    assert arguments[arguments.index("--tools") + 1] == "Read,Grep,Glob"
    assert arguments[arguments.index("--permission-prompts") + 1] == "none"
    assert "--model ${{ steps.engine.outputs.model }}" in inputs["claude_args"]
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
}


def steps(path):
    return [
        (job_name, step)
        for job_name, job in load(path)["jobs"].items()
        for step in job["steps"]
    ]


WORKFLOWS = sorted(OWN.glob("*.yml"))


def test_the_templates_are_three_of_this_repository_s_workflows():
    assert set(NAMES) <= {path.name for path in WORKFLOWS}


@pytest.mark.parametrize("name", NAMES)
def test_templates_start_without_permissions_and_not_in_forks(name):
    workflow = load(TEMPLATES / name)
    assert workflow["permissions"] == {}
    for job_name, job in workflow["jobs"].items():
        assert "permissions" in job, job_name
        assert job["timeout-minutes"] <= 20, job_name
    first = next(iter(workflow["jobs"].values()))
    assert "!github.event.repository.fork" in first["if"]


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
def test_actions_are_pinned_and_checkouts_keep_no_credentials(path):
    for job_name, step in steps(path):
        if "uses" not in step:
            continue
        uses = step["uses"]
        if uses == "./" or uses.startswith("docker://"):
            continue
        assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", uses), uses
        if uses.startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False, job_name
            assert "ref" not in step["with"], "issue events must run the default branch"


def test_install_finds_every_pin_of_the_action():
    for name in NAMES:
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        uses = re.findall(r"uses: (Dennis-Otto/issue-assistant@.*)", text)
        assert uses and all(assistant.ACTION_PIN.fullmatch(pin) for pin in uses)


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda path: path.name)
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
        assert JOBS[name]["runs-on"] == "ubuntu-latest"
        assert "environment" not in JOBS[name]
        for rules in ENGINE_RULES.values():
            for secret in rules["secrets"]:
                assert secret not in yaml.safe_dump(JOBS[name])


def test_the_engine_is_chosen_before_the_context_and_the_engines():
    assert STEPS["engine"]["with"]["command"] == "engine"
    assert "vars.ISSUE_ASSISTANT_AI != 'off'" in ANALYZE["if"]
    assert STEPS["context"]["if"] == "steps.engine.outputs.ready == 'true'"
    order = [step.get("id") for step in ANALYZE["steps"]]
    assert (
        order.index("engine")
        < order.index("context")
        < min(order.index(engine) for engine in ENGINE_RULES)
    )


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
        step
        for step in JOBS["apply"]["steps"]
        if step["uses"].startswith("Dennis-Otto/issue-assistant@")
    ]
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
    lifecycle = load(TEMPLATES / "issue-lifecycle.yml")
    assert lifecycle[True]["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True


def test_the_events_the_assistant_handles_trigger_it():
    triggers = ASSISTANT[True]
    assert triggers["issues"]["types"] == ["opened", "edited"]
    assert triggers["issue_comment"]["types"] == ["created"]
    condition = " ".join(JOBS["event"]["if"].split())
    assert "!github.event.issue.pull_request" in condition
    assert "github.event.sender.type != 'Bot'" in condition


def test_merges_and_releases_start_the_lifecycle_without_running_pull_request_code():
    lifecycle = load(TEMPLATES / "issue-lifecycle.yml")
    triggers = lifecycle[True]
    assert triggers["pull_request_target"] == {"types": ["closed"]}
    assert triggers["release"] == {"types": ["published"]}
    # A release published with the GITHUB_TOKEN starts no workflow.
    assert triggers["workflow_run"] == {
        "workflows": ["Release"],
        "types": ["completed"],
    }
    assert "pull_request" not in triggers
    (job,) = lifecycle["jobs"].values()
    condition = " ".join(job["if"].split())
    assert "github.event.pull_request.merged" in condition
    assert "github.event.pull_request.user.login != 'dependabot[bot]'" in condition
    assert "github.event.workflow_run.conclusion == 'success'" in condition
    assert job["permissions"] == {
        "contents": "read",
        "issues": "write",
        "pull-requests": "read",
    }
    checkout, step = job["steps"]
    # The default branch: no ref of the pull request, no credentials left behind.
    assert checkout["with"] == {"persist-credentials": False}
    assert step["with"]["command"] == "sweep"


def test_the_findings_workflow_may_only_dismiss_alerts_and_runs_no_ai():
    findings = load(TEMPLATES / "findings.yml")
    triggers = findings[True]
    assert set(triggers) == {"schedule", "push", "workflow_run", "workflow_dispatch"}
    assert triggers["push"] == {
        "branches": ["main"],
        "paths": [assistant.FINDINGS_FILE.as_posix()],
    }
    assert triggers["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True
    (job,) = findings["jobs"].values()
    assert job["permissions"] == {"contents": "read", "security-events": "write"}
    condition = " ".join(job["if"].split())
    # Only code scanning of the default branch of this repository, never of a fork.
    assert "head_repository.full_name == github.repository" in condition
    assert "head_branch == github.event.repository.default_branch" in condition
    checkout, step = job["steps"]
    assert checkout["with"] == {"persist-credentials": False}
    assert step["with"]["command"] == "findings"
    assert "environment" not in job and job["runs-on"] == "ubuntu-latest"


def test_the_firewall_template_allows_only_named_hosts():
    policy = load(ROOT / assistant.FIREWALL)
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
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    newest = re.search(r"^## (\d+\.\d+\.\d+)\s*$", changelog, re.MULTILINE)
    assert newest and newest[1] == project["project"]["version"]


def test_the_action_metadata_is_complete():
    assert ACTION["runs"]["using"] == "composite"
    assert ACTION["name"] and ACTION["description"] and ACTION["branding"]
    assert len(ACTION["description"]) <= 125, "the Marketplace shows 125 characters"


def test_this_repository_s_own_settings_fit():
    config = assistant.load_config(ROOT, "Dennis-Otto/issue-assistant")
    # The repository uses the action itself, so the whole check applies.
    assert assistant.check(config) == []
    defined = {label.name for label in config.labels}
    pr_labels = (OWN / "pr-labels.yml").read_text(encoding="utf-8")
    managed = re.search(r"managed=\(([^)]*)\)", pr_labels)[1].split()
    notes = load(ROOT / ".github" / "release.yml")["changelog"]
    used = set(managed) | set(notes["exclude"]["labels"])
    for category in notes["categories"]:
        used |= set(category["labels"]) - {"*"}
    assert used <= defined, used - defined
