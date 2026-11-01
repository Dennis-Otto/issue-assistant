"""The explanations of the checks of this project alone, which the PR coach shows when
one of them fails: in the summary of the check, as its annotation and in its one
comment on the pull request, also on a pull request from a fork.

The explanations are in .github/checks.project.toml, and blueprint.py, which the
blueprint keeps current, shows them. Each job of the Action test ends with the step
that explains each of its parts that failed; these tests run that step's command for
every job and every part, and check that each part fits the steps of its job.
"""

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
BLUEPRINT = ROOT / ".github" / "blueprint.py"
CATALOG = tomllib.loads(
    (ROOT / ".github" / "checks.project.toml").read_text(encoding="utf-8")
)["check"]
JOBS = yaml.safe_load(
    (ROOT / ".github" / "workflows" / "action-test.yml").read_text(encoding="utf-8")
)["jobs"]
EXPLAIN = "Explain the failure"
# The steps of Harden-Runner that belong to no part: right after it, and at the end.
WARM_UP = "Wait until Harden-Runner lets the hosts through"
WAIT_FOR_DOCKER = "Wait until Harden-Runner has removed Docker"
# A line of the variable FAILED of that step: the outcomes of the steps of a part,
# then the part that the coach explains when one of them failed.
PART = re.compile(r"\$\{\{ (.+) && '([a-z-]+)' \|\| '' \}\}")
OUTCOME = re.compile(r"steps\.([a-z-]+)\.outcome == 'failure'")
# The steps of a job that come before its parts.
SET_UP = ("actions/checkout@", "step-security/harden-runner@")
# The run of the CI names its commit; a person on their machine names HEAD.
SHA = ('"$GITHUB_SHA"', '"$(git rev-parse HEAD)"')
COMMAND = "python3 issue_assistant.py"
# The part that every leg on a runner other than the one of the templates explains.
OTHER_RUNNER = "other-runner"


def explain_step(job):
    """The last step of the job, but for the wait that a job without sudo ends with:
    the explanation has to run before it, while the job still counts as failed."""
    *steps, last = job["steps"]
    return steps[-1] if last["name"] == WAIT_FOR_DOCKER else last


def names_of(job):
    """The names that GitHub shows for the checks of a job, one for each leg of its
    matrix; the first leg runs on the runner of the templates."""
    legs = job.get("strategy", {}).get("matrix", {}).get("include")
    return [leg["check"] for leg in legs] if legs else [job["name"]]


def name_of(job):
    """The name of a job, by which the explanations know it."""
    return names_of(job)[0]


def parts_of(job):
    """The parts of a job, each with the ids of its steps."""
    failed = explain_step(job)["env"].get("FAILED", "")
    parts = {}
    for line in failed.splitlines():
        match = PART.fullmatch(line)
        assert match, f"a line of FAILED that names no part: {line!r}"
        parts[match[2]] = OUTCOME.findall(match[1])
    return parts


def coach(*arguments, summary):
    """blueprint.py as the step runs it in the CI, which writes the summary."""
    environment = dict(
        os.environ,
        GITHUB_ACTIONS="true",
        GITHUB_REPOSITORY="Dennis-Otto/issue-assistant",
        GITHUB_SERVER_URL="https://github.com",
        GITHUB_STEP_SUMMARY=str(summary),
    )
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(BLUEPRINT), "coach", *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


@pytest.mark.parametrize("key", list(JOBS))
def test_every_job_of_the_action_test_ends_with_the_explanation_of_its_failure(key):
    job = JOBS[key]
    step = explain_step(job)
    assert step["name"] == EXPLAIN
    assert step["if"] == "failure()"
    # The coach finds the explanation by the name that GitHub shows for the check.
    assert step["env"]["CHECK"] == job["name"]
    command = 'python3 .github/blueprint.py coach explain "$CHECK"'
    if parts_of(job):
        command += ' --parts "${parts[@]}"'
    assert step["run"].strip().endswith(command)
    assert name_of(job) in CATALOG


@pytest.mark.parametrize("key", [key for key in JOBS if parts_of(JOBS[key])])
def test_each_step_of_a_job_with_parts_belongs_to_one_part(key):
    """So that the coach explains the step that failed, never a part beside it, and
    each part runs also when one before it failed, so that the check reports every
    failure at once."""
    job = JOBS[key]
    steps = [
        step
        for step in job["steps"]
        if not step.get("uses", "").startswith(SET_UP)
        and step["name"] not in (EXPLAIN, WARM_UP, WAIT_FOR_DOCKER)
    ]
    ids = [step.get("id") for step in steps]
    named = [step for part in parts_of(job).values() for step in part]
    assert sorted(named) == sorted(ids)
    for step in steps:
        assert step["if"].startswith("${{ !cancelled() && steps.")
    for part in parts_of(job):
        assert part in CATALOG, f"{part} of {name_of(job)} has no explanation"


@pytest.mark.parametrize(
    ("key", "part"),
    [(key, part) for key in JOBS for part in parts_of(JOBS[key])],
)
def test_the_explanation_of_a_part_shows_how_to_run_its_steps(key, part):
    """Every command of the action that a part runs is among the commands of its
    explanation, so that a contributor runs on their machine what the check ran."""
    job = JOBS[key]
    ids = parts_of(job)[part]
    commands = CATALOG[part].get("commands", [])
    for step in job["steps"]:
        if step.get("id") not in ids:
            continue
        if step.get("uses") == "./":
            lines = [f"{COMMAND} {step['with']['command']}"]
        else:
            lines = [
                line.strip().replace(*SHA)
                for line in step.get("run", "").splitlines()
                if line.strip().startswith(COMMAND)
            ]
        for line in lines:
            assert any(command.endswith(line) for command in commands), line


@pytest.mark.parametrize(
    ("key", "parts"),
    [(key, []) for key in JOBS]
    + [(key, [part]) for key in JOBS for part in parts_of(JOBS[key])]
    + [(key, list(parts_of(JOBS[key]))) for key in JOBS if parts_of(JOBS[key])],
)
def test_the_coach_explains_each_job_and_each_part_that_failed(key, parts, tmp_path):
    """The command of the step writes each explanation into the summary of the job
    and as its annotation, by whose title the report of the PR coach finds it."""
    name = name_of(JOBS[key])
    summary = tmp_path / "summary.md"
    result = coach("explain", name, "--parts", *parts, summary=summary)
    assert result.returncode == 0, result.stderr
    written = summary.read_text(encoding="utf-8")
    assert "has no explanation" not in result.stdout + written
    for part in parts or [name]:
        entry = CATALOG[part]
        heading = f"PR coach: {entry['title']}" + (f" (in {name})" if parts else "")
        assert f"### {heading}\n" in written
        title = heading.replace("%", "%25").replace(":", "%3A").replace(",", "%2C")
        assert f"::error title={title}::" in result.stdout
        # The link to read more leads to this repository, also from a fork.
        docs = entry["docs"]
        if not docs.startswith("https://"):
            docs = f"https://github.com/Dennis-Otto/issue-assistant/blob/HEAD/{docs}"
        assert f"]({docs})" in written


@pytest.mark.parametrize(
    ("key", "name"),
    [(key, name) for key in JOBS for name in names_of(JOBS[key])[1:]],
)
def test_each_leg_on_another_runner_explains_that_runner(key, name, tmp_path):
    """Whatever failed in the leg, the coach adds what to look at on a runner other
    than the one of the templates, under the name that GitHub shows for the leg, by
    which the report of the PR coach finds its annotation."""
    job = JOBS[key]
    legs = job["strategy"]["matrix"]["include"]
    (runner,) = [leg["runner"] for leg in legs if leg["check"] == name]
    assert name == f"{name_of(job)} ({runner})"
    assert parts_of(job)[OTHER_RUNNER] == []
    assert (
        f"${{{{ matrix.runner != '{legs[0]['runner']}' && '{OTHER_RUNNER}' || '' }}}}"
    ) in explain_step(job)["env"]["FAILED"]
    summary = tmp_path / "summary.md"
    result = coach("explain", name, "--parts", OTHER_RUNNER, summary=summary)
    assert result.returncode == 0, result.stderr
    heading = f"PR coach: {CATALOG[OTHER_RUNNER]['title']} (in {name})"
    assert f"### {heading}\n" in summary.read_text(encoding="utf-8")


def test_every_check_is_explained_and_says_how_it_is_fixed(tmp_path):
    """Every required check and every step of scripts/check.sh has an explanation,
    and each explanation says how it fixes itself, or why no command can."""
    result = coach("verify", summary=tmp_path / "summary.md")
    assert result.returncode == 0, result.stderr
    for key, entry in CATALOG.items():
        assert entry.get("autofix"), f"{key} says neither how nor why not"


def test_each_explanation_of_the_project_is_for_a_check_that_exists():
    """An explanation of a check that no job runs any more would never show."""
    jobs = {name_of(job) for job in JOBS.values()}
    parts = {part for job in JOBS.values() for part in parts_of(job)}
    # The fuzzing is the blueprint's workflow, which explains its job by this name.
    fuzz = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "fuzz.yml").read_text(encoding="utf-8")
    )["jobs"]["fuzz"]
    # The Canary explains the wait of the AI for its runner by the name of its job.
    queue = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "canary.yml").read_text(encoding="utf-8")
    )["jobs"]["queue"]
    assert explain_step(queue)["env"] == {"CHECK": queue["name"]}
    # The Workflow test explains its gate, the check that main requires.
    gate = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "workflow-test.yml").read_text(
            encoding="utf-8"
        )
    )["jobs"]["gate"]
    assert explain_step(gate)["env"] == {"CHECK": gate["name"]}
    # The Evaluate workflow explains each of its jobs by its name.
    evaluate = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "evaluate.yml").read_text(encoding="utf-8")
    )["jobs"]
    explained = {job["name"] for job in evaluate.values()}
    for job in evaluate.values():
        assert {"CHECK": job["name"]} in [step.get("env") for step in job["steps"]]
    assert sorted(CATALOG) == sorted(
        jobs | parts | explained | {fuzz["name"], queue["name"], gate["name"]}
    )


def test_the_explanation_of_the_template_lint_runs_the_actionlint_of_the_check():
    """The command that the explanation gives runs the same actionlint as the step,
    so that a contributor sees what the check saw; a bump of the step by Renovate
    fails here until the explanation follows."""
    step = next(
        step
        for step in JOBS["action"]["steps"]
        if step.get("id") in parts_of(JOBS["action"])["template-lint"]
        and "ACTIONLINT" in step.get("env", {})
    )
    image = step["env"]["ACTIONLINT"].split("@")[0]
    assert any(
        f" {image} " in command for command in CATALOG["template-lint"]["commands"]
    )
