"""The variants of the templates, which a key of config.toml chooses: findings.yml for
the scans of main alone, issue-lifecycle.yml without the start at the end of
Release, and issue-assistant.yml with a last job that starts a workflow of the
repository for failed runs (decisions 0035, 0036 and 0037)."""

import dataclasses
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

import issue_assistant as assistant
from support import REF, REPOSITORY

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = Path(__file__).parent / "sample"
SETTINGS = assistant.SETTINGS / "config.toml"
VARIANTS = sorted(assistant.VARIANT_KEYS)
FINDINGS = "findings.yml"
LIFECYCLE = "issue-lifecycle.yml"
ASSISTANT = "issue-assistant.yml"
# A workflow of a repository that dismisses the accepted alerts of its pull requests
# in place of findings.yml, as the pages of the configuration show it.
CALLER = f"""name: After the checks
on:
  workflow_run:
    workflows: [CodeQL]
    types: [completed]
jobs:
  findings:
    if: github.event.workflow_run.event == 'pull_request'
    permissions:
      contents: read
      security-events: write
    uses: {assistant.ACTION}/.github/workflows/reusable-findings.yml@{REF} # v1.0.0
"""


def load(path):
    workflow = yaml.safe_load(path.read_text("utf-8"))
    # PyYAML reads the key `on` as True.
    workflow["on"] = workflow.pop(True)
    return workflow


def template(name):
    return assistant.TEMPLATES / assistant.WORKFLOWS / name


def variant(name):
    return assistant.VARIANTS / assistant.WORKFLOWS / name


def project(tmp_path, settings=""):
    """A copy of the sample repository before install, with these lines added to its
    config.toml."""
    root = tmp_path / "project"
    shutil.copytree(SAMPLE, root)
    if settings:
        with (root / SETTINGS).open("a", encoding="utf-8") as file:
            file.write(f"\n{settings}\n")
    return root


def files(folder):
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(autouse=True)
def no_event(monkeypatch):
    # The CI of this repository runs the tests in GitHub Actions, whose event names
    # the default branch of this repository.
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)


# The templates


def test_each_variant_is_a_template_with_a_key_of_config_toml():
    folder = assistant.VARIANTS / assistant.WORKFLOWS
    assert sorted(path.name for path in folder.iterdir()) == VARIANTS
    assert set(VARIANTS) < set(assistant.TEMPLATE_WORKFLOWS)
    defaults = assistant.Config(Path(), REPOSITORY, (), "")
    for name, (section, key) in assistant.VARIANT_KEYS.items():
        found = assistant.CONFIG_KEYS[section][key]
        # A boolean that is true without the key, or a string that is empty: the
        # template is the default.
        assert (found.kind, getattr(defaults, found.attribute)) in {
            (bool, True),
            (str, ""),
        }, name
        assert assistant.template_of(name, defaults) == template(name)
        assert assistant.chosen_variant(name, defaults) == ""


def test_the_variants_are_out_of_reach_of_the_blueprint_s_sync():
    """The repository blueprint takes each *.yml of templates/.github/workflows/,
    without its subfolders, as a workflow of its own (sync_issue_assistant.py), so
    the variants live in a folder of their own, and each keeps the name of its
    template, which zizmor's accepted findings of .github/zizmor.yml name."""
    workflows = assistant.TEMPLATES / assistant.WORKFLOWS
    assert sorted(path.name for path in workflows.glob("*.yml")) == sorted(
        assistant.TEMPLATE_WORKFLOWS
    )
    assert not any(path.is_dir() for path in workflows.iterdir())
    assert not assistant.VARIANTS.is_relative_to(workflows)
    accepted = yaml.safe_load((ROOT / ".github/zizmor.yml").read_text("utf-8"))
    ignored = accepted["rules"]["dangerous-triggers"]["ignore"]
    assert set(VARIANTS) <= set(ignored)


def test_the_variant_of_findings_yml_runs_after_the_scans_of_main_alone():
    expected = load(template(FINDINGS))
    expected["on"]["workflow_run"]["branches"] = ["main"]
    assert load(variant(FINDINGS)) == expected


def test_the_variant_of_issue_lifecycle_yml_doesn_t_start_at_the_end_of_release():
    expected = load(template(LIFECYCLE))
    assert expected["on"]["workflow_run"] == {
        "workflows": ["Release"],
        "types": ["completed"],
    }
    del expected["on"]["workflow_run"]
    assert load(variant(LIFECYCLE)) == expected


@pytest.mark.parametrize("name", VARIANTS)
def test_each_variant_has_the_pin_and_says_which_key_chooses_it(name):
    text = variant(name).read_text("utf-8")
    pins = assistant.ACTION_PIN.findall(template(name).read_text("utf-8"))
    assert assistant.ACTION_PIN.findall(text) == pins
    section, key = assistant.VARIANT_KEYS[name]
    kind = assistant.CONFIG_KEYS[section][key].kind
    said = f"[{section}] {key} = false" if kind is bool else f"[{section}] {key}"
    assert said in " ".join(text.split())
    # The template names the key too, in a comment, which check ignores.
    assert f"[{section}] {key}" in " ".join(template(name).read_text("utf-8").split())


def test_the_action_test_lints_the_variants():
    workflow = load(ROOT / assistant.WORKFLOWS / "action-test.yml")
    steps = {step.get("id"): step for step in workflow["jobs"]["action"]["steps"]}
    assert "templates/variants/.github/workflows/*.yml" in steps["template-lint"]["run"]
    # zizmor audits every workflow below the folder that it is given.
    assert steps["template-security"]["with"]["inputs"] == "templates"
    explained = (ROOT / ".github/checks.project.toml").read_text("utf-8")
    assert "templates/variants/.github/workflows/*.yml" in explained


def test_the_reusable_workflow_of_the_findings_decides_by_the_event_alone():
    """Another workflow may call it after the scans of pull requests (decision
    0035), so its jobs never depend on the name of the workflow that calls them,
    and the group of a pull request is the same for every caller."""
    reusable = load(ROOT / assistant.WORKFLOWS / "reusable-findings.yml")
    caller = re.compile(r"github\.(workflow|workflow_ref|workflow_sha)\b")
    for name, job in reusable["jobs"].items():
        decided = [job.get("if", ""), json.dumps(job.get("concurrency", ""))]
        assert not any(caller.search(text) for text in decided), name
    group = reusable["jobs"]["findings"]["concurrency"]["group"]
    assert (
        "format('findings-pull-request-{0}', github.event.workflow_run.head_branch)"
        in group
    )
    assert "github.event.workflow_run.event == 'pull_request'" in group
    assert (
        "github.event.workflow_run.event == 'pull_request'"
        in (reusable["jobs"]["findings"]["if"])
    )


# install and check without the keys


@pytest.mark.parametrize(
    "settings",
    [
        "",
        "[releases]\nsweep_after_release_workflow = true\n\n"
        "[findings]\npull_requests = true\n\n"
        '[failures]\nworkflow = ""',
    ],
    ids=["without the keys", "with the keys at their defaults"],
)
def test_without_the_keys_nothing_changes(tmp_path, settings):
    """A repository without the keys, or with them true, gets every file as before
    them: each workflow its template, byte for byte with the pin of the release, and
    the same files as a repository whose config.toml has no key of them."""
    # With every workflow, the one of the discussions too.
    answers = '[discussions]\nanswer_categories = ["Q&A"]\n'
    before = project(tmp_path / "before", answers)
    done_before = assistant.install(before, REF, "v1.0.0", REPOSITORY)
    root = project(tmp_path / "now", f"{answers}\n{settings}")
    done = assistant.install(root, REF, "v1.0.0", REPOSITORY)
    assert done == done_before
    written = files(root)
    expected = files(before)
    expected[str(SETTINGS)] = (root / SETTINGS).read_bytes()
    assert written == expected
    for name in assistant.TEMPLATE_WORKFLOWS:
        text = template(name).read_text("utf-8")
        assert (root / assistant.WORKFLOWS / name).read_bytes() == (
            assistant.pinned(text, REF, "v1.0.0").encode()
        )
    assert assistant.check(assistant.load_config(root, REPOSITORY)) == []


# The variant of findings.yml


FINDINGS_OFF = "[findings]\npull_requests = false"


def add_caller(root, text=CALLER, name="after-checks.yml"):
    (root / assistant.WORKFLOWS / name).write_text(text, "utf-8")


def test_install_and_check_follow_findings_pull_requests(tmp_path):
    root = project(tmp_path, FINDINGS_OFF)
    done = assistant.install(root, REF, "v1.0.0", REPOSITORY)
    where = assistant.WORKFLOWS / FINDINGS
    assert f"wrote {where} for [findings] pull_requests = false" in done
    assert f"wrote {assistant.WORKFLOWS / LIFECYCLE}" in done
    assert (root / where).read_bytes() == assistant.pinned(
        variant(FINDINGS).read_text("utf-8"), REF, "v1.0.0"
    ).encode()
    # The other workflows are their templates.
    for name in set(assistant.TEMPLATE_WORKFLOWS) - {FINDINGS}:
        if (root / assistant.WORKFLOWS / name).is_file():
            assert (root / assistant.WORKFLOWS / name).read_bytes() == (
                assistant.pinned(template(name).read_text("utf-8"), REF, "v1.0.0")
            ).encode()
    add_caller(root)
    config = assistant.load_config(root, REPOSITORY)
    assert config.findings_pull_requests is False
    assert assistant.check(config) == []
    # The template is no longer what the settings want, and the message says why.
    (root / where).write_text(
        assistant.pinned(template(FINDINGS).read_text("utf-8"), REF, "v1.0.0"), "utf-8"
    )
    assert assistant.check_workflows(config) == [
        f"{where} differs from the template of the issue assistant for [findings] "
        "pull_requests = false of config.toml, and only the refs of its actions may "
        f"differ; {assistant.reinstall()}"
    ]
    # Without the key, the variant is no longer the template.
    shutil.copyfile(variant(FINDINGS), root / where)
    default = assistant.Config(root, REPOSITORY, (), "")
    assert assistant.check_workflows(default)[0].startswith(
        f"{where} differs from the template of the issue assistant, and only"
    )


@pytest.mark.parametrize(
    "caller",
    [
        CALLER,
        # on as a list of events; a reusable workflow of another case.
        CALLER.replace(
            "on:\n  workflow_run:\n    workflows: [CodeQL]\n    types: [completed]",
            "on: [workflow_run]",
        ).replace("Dennis-Otto", "dennis-otto"),
        # on as one event, among jobs of other kinds.
        CALLER.replace(
            "on:\n  workflow_run:\n    workflows: [CodeQL]\n    types: [completed]",
            "on: workflow_run",
        ).replace("jobs:\n", "jobs:\n  other: none\n  lint:\n    runs-on: x\n"),
    ],
    ids=["mapping", "list", "string"],
)
def test_check_finds_the_workflow_that_dismisses_in_pull_requests(root, caller):
    config = dataclasses.replace(
        assistant.load_config(root, REPOSITORY), findings_pull_requests=False
    )
    add_caller(root, caller, "after-checks.yaml")
    assert assistant.findings_variant_problems(config) == []


@pytest.mark.parametrize(
    "caller",
    [
        None,
        # No workflow_run.
        CALLER.replace("workflow_run:", "push:"),
        # Another reusable workflow.
        CALLER.replace("reusable-findings.yml", "reusable-labels.yml"),
        # Not YAML, which isn't check's to judge in a workflow of the repository.
        "on: [workflow_run\n",
        # No mapping, and none of its jobs.
        "- workflow_run\n",
        "on: workflow_run\njobs: [findings]\n",
        "on: 7\njobs: {}\n",
        # A list of events that holds no name of one.
        "on: [{workflow_run: {}}]\njobs: {}\n",
        # findings.yml itself doesn't count.
        "findings.yml",
    ],
    ids=["none", "push", "other", "no-yaml", "list", "jobs", "on", "events", "itself"],
)
def test_check_fails_while_nothing_else_dismisses_in_pull_requests(root, caller):
    config = dataclasses.replace(
        assistant.load_config(root, REPOSITORY), findings_pull_requests=False
    )
    if caller == "findings.yml":
        add_caller(root, CALLER, "findings.yml")
    elif caller is not None:
        add_caller(root, caller)
    assert assistant.findings_variant_problems(config) == [
        f"{SETTINGS}: [findings] pull_requests = false needs another workflow of "
        ".github/workflows that starts on workflow_run and calls "
        f"{assistant.ACTION}/.github/workflows/reusable-findings.yml after the scans "
        "of pull requests, or nothing dismisses their accepted alerts; add one, or "
        "take the key out"
    ]
    # With the key true, check asks for nothing of the kind.
    default = dataclasses.replace(config, findings_pull_requests=True)
    assert assistant.findings_variant_problems(default) == []


def write_event(tmp_path, monkeypatch, payload):
    path = tmp_path / "event.json"
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(path))


def test_check_wants_main_as_the_default_branch_for_the_variant(
    root, tmp_path, monkeypatch
):
    add_caller(root)
    config = dataclasses.replace(
        assistant.load_config(root, REPOSITORY), findings_pull_requests=False
    )
    write_event(tmp_path, monkeypatch, {"repository": {"default_branch": "trunk"}})
    assert assistant.findings_variant_problems(config) == [
        f"{SETTINGS}: [findings] pull_requests = false runs Findings after the scans "
        "of main alone, and the default branch is trunk; take the key out"
    ]
    write_event(tmp_path, monkeypatch, {"repository": {"default_branch": "main"}})
    assert assistant.findings_variant_problems(config) == []


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.mark.parametrize(
    "event",
    [None, "no JSON", "[]", '{"repository": []}', '{"repository": {}}'],
    ids=["none", "no-json", "list", "no-mapping", "no-branch"],
)
def test_the_default_branch_comes_from_the_event_or_from_origin(
    tmp_path, monkeypatch, event
):
    root = tmp_path / "checkout"
    root.mkdir()
    if event is not None:
        write_event(tmp_path, monkeypatch, event)
    # No checkout: nobody names it, and check says nothing of it.
    assert assistant.default_branch_name(root) == ""
    git(root, "init", "-q", "--template=", "-b", "work")
    assert assistant.default_branch_name(root) == ""
    git(root, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    assert assistant.default_branch_name(root) == "trunk"
    write_event(tmp_path, monkeypatch, {"repository": {"default_branch": "main"}})
    assert assistant.default_branch_name(root) == "main"


def test_the_default_branch_is_unknown_without_git(tmp_path, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(assistant.setup_check.subprocess, "run", missing)
    assert assistant.default_branch_name(tmp_path) == ""


# The variant of issue-lifecycle.yml


def test_install_and_check_follow_sweep_after_release_workflow(tmp_path):
    root = project(tmp_path, "[releases]\nsweep_after_release_workflow = false")
    done = assistant.install(root, REF, "v1.0.0", REPOSITORY)
    where = assistant.WORKFLOWS / LIFECYCLE
    assert f"wrote {where} for [releases] sweep_after_release_workflow = false" in done
    assert f"wrote {assistant.WORKFLOWS / FINDINGS}" in done
    written = (root / where).read_text("utf-8")
    assert written == assistant.pinned(
        variant(LIFECYCLE).read_text("utf-8"), REF, "v1.0.0"
    )
    assert "workflow_run" not in load(root / where)["on"]
    config = assistant.load_config(root, REPOSITORY)
    assert config.sweep_after_release_workflow is False
    # check can't see whether the release workflow starts the sweep, and asks for
    # nothing else.
    assert assistant.check(config) == []
    shutil.copyfile(template(LIFECYCLE), root / where)
    assert assistant.check_workflows(config) == [
        f"{where} differs from the template of the issue assistant for [releases] "
        "sweep_after_release_workflow = false of config.toml, and only the refs of "
        f"its actions may differ; {assistant.reinstall()}"
    ]
    # Running install again with the key true writes the template again.
    settings = root / SETTINGS
    settings.write_text(
        settings.read_text("utf-8").replace("= false", "= true"), "utf-8"
    )
    assistant.install(root, REF, "v1.0.1", REPOSITORY)
    assert (root / where).read_text("utf-8") == assistant.pinned(
        template(LIFECYCLE).read_text("utf-8"), REF, "v1.0.1"
    )


@pytest.mark.parametrize(
    "settings",
    [
        # Not TOML: install writes the templates, and load_config stops it after.
        "[releases\n",
        # Not a boolean, which check names; install keeps the template.
        '[releases]\nsweep_after_release_workflow = "no"',
        # No name of a workflow file, which would reach the YAML; check names it.
        '[failures]\nworkflow = "../bot-health.yml"',
        '[failures]\nworkflow = "x.yml\\n  evil: true"',
        "[failures]\nworkflow = true",
    ],
    ids=["no-toml", "no-boolean", "folder", "newline", "no-string"],
)
def test_install_writes_the_templates_where_the_settings_choose_nothing(
    tmp_path, settings
):
    root = project(tmp_path, settings)
    variants = assistant.variant_settings(root)
    assert variants == assistant.Config(root, "", (), "")
    for name in VARIANTS:
        assert assistant.template_of(name, variants) == template(name)


def test_the_settings_of_the_variants_are_read_before_the_starters(tmp_path):
    # A new repository has no config.toml yet: the starter names the defaults.
    assert assistant.variant_settings(tmp_path) == assistant.Config(
        tmp_path, "", (), ""
    )
    assistant.install(tmp_path, REF, "v1.0.0")
    starter = assistant.load_config(tmp_path, REPOSITORY)
    assert starter.findings_pull_requests is True
    assert starter.sweep_after_release_workflow is True
    assert starter.failure_workflow == ""
    for name in VARIANTS:
        assert (tmp_path / assistant.WORKFLOWS / name).read_text("utf-8") == (
            assistant.pinned(template(name).read_text("utf-8"), REF, "v1.0.0")
        )


def test_a_copy_of_the_settings_keeps_every_key(config):
    changed = dataclasses.replace(
        config,
        findings_pull_requests=False,
        sweep_after_release_workflow=False,
        failure_workflow="bot-health.yml",
    )
    assert [assistant.chosen_variant(name, changed) for name in VARIANTS] == [
        "[findings] pull_requests = false",
        '[failures] workflow = "bot-health.yml"',
        "[releases] sweep_after_release_workflow = false",
    ]
    assert [assistant.template_of(name, changed) for name in VARIANTS] == [
        variant(name) for name in VARIANTS
    ]
    # Every other workflow is its template.
    assert assistant.chosen_variant("labels.yml", changed) == ""


# The variant of issue-assistant.yml

# The bot-health bot of the Repo Blueprint, as far as the last job needs it: a run
# by hand with the id of a run as its input run.
BOT_HEALTH = """name: Bot health
on:
  workflow_dispatch:
    inputs:
      run:
        description: The id of a run whose failed jobs may run again
        type: string
permissions: {}
jobs: {}
"""
START = "Start the workflow for failed runs"


def add_workflow(root, name="bot-health.yml", text=BOT_HEALTH):
    (root / assistant.WORKFLOWS / name).write_text(text, "utf-8")


def named(text, name):
    """The text of the variant of issue-assistant.yml with the workflow `name`."""
    return re.sub(r"(?m)^(      WORKFLOW: ).*$", rf"\g<1>{name}", text)


def test_the_variant_of_issue_assistant_yml_has_a_last_job_for_failed_runs():
    expected = load(template(ASSISTANT))
    workflow = load(variant(ASSISTANT))
    assert list(workflow["jobs"])[-1] == assistant.FAILURE_JOB
    job = workflow["jobs"].pop(assistant.FAILURE_JOB)
    # Nothing else differs.
    assert workflow == expected
    # It waits for every other job, and runs when one of them failed at the first
    # attempt, not in a fork and not for a run by hand.
    assert job["needs"] == list(expected["jobs"])
    assert job["if"] == (
        "failure() && github.run_attempt == 1 && !github.event.repository.fork "
        "&& github.event_name != 'workflow_dispatch'"
    )
    assert job["permissions"] == {"actions": "write"}
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] == 5
    assert job["env"] == {"WORKFLOW": "bot-health.yml"}
    # The one line of the name, which install writes.
    text = variant(ASSISTANT).read_text("utf-8")
    assert [line[0] for line in assistant.FAILURE_WORKFLOW.finditer(text)] == [
        "      WORKFLOW: bot-health.yml"
    ]
    steps = job["steps"]
    assert steps[0]["uses"].startswith("step-security/harden-runner@")
    assert steps[0]["with"] == {
        "egress-policy": "block",
        "disable-sudo-and-containers": True,
        "allowed-endpoints": "api.github.com:443",
    }
    # It checks out nothing, and starts the workflow with the run, by its token.
    assert not any("checkout" in step.get("uses", "") for step in steps)
    start = next(step for step in steps if step.get("name") == START)
    assert start["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "GH_REPO": "${{ github.repository }}",
        "RUN": "${{ github.run_id }}",
    }
    assert 'gh workflow run "$WORKFLOW" --field run="$RUN"' in start["run"]
    assert "for wait in 10 20 0; do" in start["run"]


def test_the_variant_s_last_job_blocks_as_the_jobs_of_the_reusable_workflow():
    """The steps of Harden-Runner around the start are those of the jobs of the
    reusable workflow that reach only GitHub, so that a fix of them reaches the
    variant too."""
    job = load(variant(ASSISTANT))["jobs"][assistant.FAILURE_JOB]
    reusable = load(ROOT / assistant.WORKFLOWS / "reusable-issue-assistant.yml")
    apply = reusable["jobs"]["apply"]["steps"]
    own = {step["name"]: step for step in job["steps"]}
    for name in (
        "Make sure that Harden-Runner blocks",
        "Wait until Harden-Runner lets the hosts through",
        "Wait until Harden-Runner has removed Docker",
    ):
        (theirs,) = [step for step in apply if step.get("name") == name]
        assert own[name]["run"] == theirs["run"], name


@pytest.mark.parametrize("name", ["bot-health.yml", "second_try.yaml"])
def test_install_and_check_follow_failures_workflow(tmp_path, name):
    root = project(tmp_path, f'[failures]\nworkflow = "{name}"')
    done = assistant.install(root, REF, "v1.0.0", REPOSITORY)
    where = assistant.WORKFLOWS / ASSISTANT
    assert f'wrote {where} for [failures] workflow = "{name}"' in done
    written = (root / where).read_text("utf-8")
    assert written == assistant.pinned(
        named(variant(ASSISTANT).read_text("utf-8"), name), REF, "v1.0.0"
    )
    assert load(root / where)["jobs"][assistant.FAILURE_JOB]["env"] == {
        "WORKFLOW": name
    }
    # The other workflows are their templates.
    for other in (FINDINGS, LIFECYCLE, "labels.yml"):
        assert (root / assistant.WORKFLOWS / other).read_text("utf-8") == (
            assistant.pinned(template(other).read_text("utf-8"), REF, "v1.0.0")
        )
    add_workflow(root, name)
    config = assistant.load_config(root, REPOSITORY)
    assert config.failure_workflow == name
    assert assistant.check(config) == []
    # Another workflow in the job, or the template without it, is no longer what
    # the settings want, and the message says why.
    differs = [
        f"{where} differs from the template of the issue assistant for [failures] "
        f'workflow = "{name}" of config.toml, and only the refs of its actions may '
        f"differ; {assistant.reinstall()}"
    ]
    (root / where).write_text(named(written, "other.yml"), "utf-8")
    assert assistant.check_workflows(config) == differs
    (root / where).write_text(
        assistant.pinned(template(ASSISTANT).read_text("utf-8"), REF, "v1.0.0"),
        "utf-8",
    )
    assert assistant.check_workflows(config) == differs
    # Running install again with the key empty writes the template again.
    settings = root / SETTINGS
    settings.write_text(settings.read_text("utf-8").replace(f'"{name}"', '""'), "utf-8")
    assistant.install(root, REF, "v1.0.1", REPOSITORY)
    assert (root / where).read_text("utf-8") == assistant.pinned(
        template(ASSISTANT).read_text("utf-8"), REF, "v1.0.1"
    )


@pytest.mark.parametrize(
    "workflow",
    [
        BOT_HEALTH,
        # yq reads the key on as "on".
        BOT_HEALTH.replace("\non:", '\n"on":'),
    ],
    ids=["pyyaml", "on-as-a-string"],
)
def test_check_finds_the_workflow_for_failed_runs(root, workflow):
    config = dataclasses.replace(
        assistant.load_config(root, REPOSITORY), failure_workflow="bot-health.yml"
    )
    add_workflow(root, text=workflow)
    assert assistant.failures_variant_problems(config) == []


@pytest.mark.parametrize(
    "workflow",
    [
        # No input run.
        BOT_HEALTH.replace("      run:", "      id:"),
        # No inputs, or no run by hand at all.
        "on:\n  workflow_dispatch:\njobs: {}\n",
        "on: [workflow_dispatch]\njobs: {}\n",
        "on:\n  push:\njobs: {}\n",
        "on:\n  workflow_dispatch:\n    inputs: [run]\njobs: {}\n",
        # Not YAML, and no mapping.
        "on: [workflow_dispatch\n",
        "- workflow_dispatch\n",
    ],
    ids=["no-run", "no-inputs", "list", "push", "inputs-list", "no-yaml", "no-map"],
)
def test_check_fails_while_the_workflow_can_t_take_the_run(root, workflow):
    config = dataclasses.replace(
        assistant.load_config(root, REPOSITORY), failure_workflow="bot-health.yml"
    )
    add_workflow(root, text=workflow)
    assert assistant.failures_variant_problems(config) == [
        f'{SETTINGS}: [failures] workflow = "bot-health.yml" names a workflow that '
        "the last job of issue-assistant.yml can't start with the failed run: it "
        "needs the input run under workflow_dispatch, which gets the id of the run, "
        "as the bot-health bot of the Repo Blueprint has it"
    ]


def test_check_fails_while_the_workflow_for_failed_runs_is_missing(root):
    config = dataclasses.replace(
        assistant.load_config(root, REPOSITORY), failure_workflow="bot-health.yml"
    )
    assert assistant.failures_variant_problems(config) == [
        f'{SETTINGS}: [failures] workflow = "bot-health.yml" names no workflow of '
        f"{assistant.WORKFLOWS}; add it, or take the key out"
    ]
    # Nor may it name the workflow whose job starts it.
    itself = dataclasses.replace(config, failure_workflow=ASSISTANT)
    add_workflow(root, ASSISTANT, BOT_HEALTH)
    assert assistant.failures_variant_problems(itself)[0].endswith(
        "as the bot-health bot of the Repo Blueprint has it"
    )
    # Without the key, check asks for nothing of the kind.
    default = dataclasses.replace(config, failure_workflow="")
    assert assistant.failures_variant_problems(default) == []


@pytest.mark.parametrize(
    "value", ['"../bot-health.yml"', '"bot-health"', '"a b.yml"', "true"]
)
def test_a_value_that_names_no_workflow_file_stops_every_command(root, value):
    with (root / SETTINGS).open("a", encoding="utf-8") as file:
        file.write(f"\n[failures]\nworkflow = {value}\n")
    with pytest.raises(assistant.SettingsError) as error:
        assistant.load_config(root, REPOSITORY)
    assert error.value.problems == [
        f"{SETTINGS}: [failures] workflow must be the name of a file of "
        ".github/workflows, such as bot-health.yml, or empty"
    ]
