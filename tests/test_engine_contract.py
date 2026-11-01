"""The names that keep each engine to reading, against the versions that are pinned.

The boundary of each engine rests on names that its vendor owns: the inputs and outputs
of its action, the flags of its command line and the keys of its settings. GitHub only
warns about an input that an action lacks, and the command lines ignore a key that they
don't know, so a renamed one keeps the step running, without its boundary.
scripts/engine_contract.py, which the job "engine contracts" of the Action test runs
without a secret, checks them against the pinned versions; these tests check the script,
with the help of each command line as the pinned version printed it, in
tests/fixtures/engines/, and with actions and schemas that they make up.
"""

import importlib.util
import itertools
import json
import os
import re
import runpy
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

import issue_assistant as assistant

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "engine_contract", ROOT / "scripts" / "engine_contract.py"
)
assert _spec and _spec.loader
contract = importlib.util.module_from_spec(_spec)
# The dataclasses of the module look it up by its name.
sys.modules[_spec.name] = contract
_spec.loader.exec_module(contract)

FIXTURES = Path(__file__).parent / "fixtures" / "engines"
GEMINI_HELP = (FIXTURES / "gemini-0.64.0.help").read_text(encoding="utf-8")
COPILOT_HELP = (FIXTURES / "copilot-1.0.97.help").read_text(encoding="utf-8")
CODEX_HELP = (FIXTURES / "codex-exec-0.162.1.help").read_text(encoding="utf-8")
ACTION_TEST = yaml.safe_load(
    (ROOT / ".github" / "workflows" / "action-test.yml").read_text(encoding="utf-8")
)
STEPS = contract.engine_steps()
SHA = "0123456789abcdef0123456789abcdef01234567"


def step_of(key, workflow="reusable-issue-assistant.yml"):
    (step,) = [
        step
        for step in STEPS
        if step.key == key and step.workflow == f".github/workflows/{workflow}"
    ]
    return step


def action_of(step, **changes):
    """An action.yml with the inputs that the step passes and the outputs that its job
    reads, as the action at its pin has them, and the changes of a test."""
    inputs = {key: {"description": f"The {key}."} for key in step.inputs}
    if "permission-profile" in inputs:
        inputs["permission-profile"]["description"] = (
            "Codex permission profile to select through `default_permissions`."
        )
    action = {
        "name": "An engine",
        "inputs": inputs,
        "outputs": {key: {"description": key} for key in step.outputs},
    }
    for key, value in changes.items():
        action[key] = value
    return action


SETTINGS_READ = contract.settings_read


@pytest.fixture(autouse=True)
def settings_read(monkeypatch):
    """The run with broken settings, which Gemini refuses: its own tests patch the
    run of the command line instead."""
    monkeypatch.setattr(contract, "settings_read", lambda *arguments: [])


def completed(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(["cli"], code, stdout, stderr)


def command_line(key, effort="high"):
    engine = assistant.ENGINES[key]
    run = assistant.Run(engine.model, 3, "1", effort)
    return assistant.cli_command(engine.cli, "cli", run, "The prompt.")[1:]


def strict_schema(value):
    """A JSON schema that takes the value and nothing else, as the Gemini CLI's own
    schema takes no key that it doesn't name."""
    if isinstance(value, dict):
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {key: strict_schema(item) for key, item in value.items()},
        }
    if isinstance(value, list):
        return {"type": "array", "items": {"type": "string"}}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "number"}
    return {"type": "string"}


# ---------------------------------------------------------------- the job


def test_the_job_checks_the_engines_on_a_change_of_one_and_every_week():
    job = ACTION_TEST["jobs"]["engines"]
    assert job["name"] == "engine contracts"
    # Every pull request that changes an engine, every week and by hand; a push to
    # main brings nothing that its pull request didn't check.
    assert job["if"] == "github.event_name != 'push'"
    assert ACTION_TEST[True]["schedule"]
    # No secret: the job reads GitHub with its own token, which can only read.
    assert job["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert "secrets." not in json.dumps(job)
    block, warm_up, checkout = job["steps"][:3]
    assert block["uses"].startswith("step-security/harden-runner@")
    # GitHub for the files of the actions and the schemas, npm for the command
    # lines, and Sigstore's root of trust, with which npm verifies the attestations
    # of the packages of the lock files.
    assert block["with"] == {
        "egress-policy": "block",
        "allowed-endpoints": "api.github.com:443 github.com:443 "
        "registry.npmjs.org:443 tuf-repo-cdn.sigstore.dev:443",
    }
    assert warm_up["env"] == {"HOSTS": block["with"]["allowed-endpoints"]}
    assert checkout["with"] == {"persist-credentials": False}
    steps = {step.get("id"): step for step in job["steps"]}
    assert steps["changes"]["if"].endswith("github.event_name == 'pull_request' }}")
    assert steps["changes"]["env"]["PR"] == "${{ github.event.pull_request.number }}"
    for command in contract.COMMANDS:
        assert steps[command]["run"] == f"python3 scripts/engine_contract.py {command}"
        assert steps[command]["env"]["GH_TOKEN"] == "${{ github.token }}"
    for command in ("actions", "clis"):
        assert "steps.changes.outputs.engines != 'false'" in steps[command]["if"]


def json5(text):
    """The JSON5 of the Renovate configs as JSON: without comments, with the bare keys
    in quotes and without the comma after the last item. A string stays as it is, also
    one with // in it, such as a link."""
    token = re.compile(
        r'(?P<string>"(?:[^"\\]|\\.)*")|(?P<comment>//[^\n]*|/\*.*?\*/)'
        r"|(?P<key>[A-Za-z_$][\w$-]*(?=\s*:))|(?P<space>\s+)|(?P<other>.)",
        re.S,
    )
    tokens = [
        (match.lastgroup, match.group())
        for match in token.finditer(text)
        if match.lastgroup not in ("comment", "space")
    ] + [("end", "")]
    parts = []
    for (kind, part), (_, after) in itertools.pairwise(tokens):
        if kind == "key":
            part = f'"{part}"'
        elif part == "," and after in ("]", "}"):
            continue
        parts.append(part)
    return json.loads("".join(parts))


def test_the_json5_of_the_configs_reads_as_renovate_reads_it():
    assert json5(
        '{\n  // a comment\n  key: "https://example.com", "b-c": [1, 2,],\n'
        "  /* another */ d: {e: true,},\n}"
    ) == {"key": "https://example.com", "b-c": [1, 2], "d": {"e": True}}


# The engines that Renovate updates: the actions of the steps, and the command lines
# of the lock files and of an engine's action.
ENGINE_DEPENDENCIES = {
    step.uses.partition("@")[0] for step in STEPS if step.uses != contract.TOOL
} | {pin.package for pin in contract.cli_pins(STEPS)}
RENOVATE = json5((ROOT / ".github" / "renovate.json5").read_text(encoding="utf-8"))
DECISION = "0020-patch-versions-of-the-engines-merge-on-their-own.md"


def test_a_patch_of_an_engine_merges_on_its_own_and_the_rest_waits():
    """Decision 0020: Renovate merges a patch version of an engine once every
    required check, engine contracts too, passed, in a pull request of its own; a
    minor or major version and a new commit of a tag wait for the maintainer."""
    assert {
        "anthropics/claude-code-action",
        "openai/codex-action",
        "@openai/codex",
        "@google/gemini-cli",
        "@github/copilot",
    } == ENGINE_DEPENDENCIES
    rules = [
        rule
        for rule in RENOVATE["packageRules"]
        if set(rule.get("matchDepNames", ())) & ENGINE_DEPENDENCIES
    ]
    patches = [rule for rule in rules if rule.get("matchUpdateTypes") == ["patch"]]
    others = [rule for rule in rules if rule.get("matchUpdateTypes") == ["!patch"]]
    # Every rule of an engine is one of the two, and every engine has both.
    assert len(patches) + len(others) == len(rules) == 4
    for kind in (patches, others):
        names = [name for rule in kind for name in rule["matchDepNames"]]
        assert sorted(names) == sorted(ENGINE_DEPENDENCIES)
    for rule in patches:
        assert rule["automerge"] is True
        assert rule["groupName"].endswith(" to a new patch version")
        (note,) = rule["prBodyNotes"]
        assert note.startswith("**This patch version merges on its own**")
        assert "required check *engine contracts*" in note
        assert f"docs/decisions/{DECISION})" in note
        assert "revert this update" in note
    for rule in others:
        assert rule["automerge"] is False
        (note,) = rule["prBodyNotes"]
        assert note.startswith("**This update waits for you.**")
        assert "*engine contracts*" in note
    # A patch comes in a pull request of its own, so a minor version of another
    # engine never holds it back, nor does it take one along.
    assert {rule["groupName"] for rule in patches}.isdisjoint(
        rule["groupName"] for rule in others
    )
    assert (ROOT / "docs" / "decisions" / DECISION).is_file()


def test_the_check_of_the_engines_is_required_and_reports_on_every_pull_request():
    """GitHub merges a pull request of Renovate once its required checks passed, so
    the check engine contracts must be one of them: otherwise a patch of an engine
    would merge before it ran. It must report on every pull request, since a
    required check that doesn't report keeps a pull request from merging."""
    project = tomllib.loads(
        (ROOT / ".github" / "repository.project.toml").read_text(encoding="utf-8")
    )
    assert "engine contracts" in project["branch"]["required_checks"]
    job = ACTION_TEST["jobs"]["engines"]
    assert job["name"] == "engine contracts"
    assert job["if"] == "github.event_name != 'push'"
    # PyYAML reads the key on as True.
    events = ACTION_TEST[True]
    assert events["pull_request"] is None
    names = [
        other.get("name", key)
        for workflow in (ROOT / ".github" / "workflows").glob("*.yml")
        for key, other in yaml.safe_load(workflow.read_text("utf-8"))["jobs"].items()
    ]
    assert names.count("engine contracts") == 1


def test_the_check_watches_files_that_exist():
    for path in contract.watched(STEPS):
        assert (ROOT / path).is_file(), path
    # The lock files of the command lines that analyze installs, too.
    assert sorted(contract.LOCKS) == [
        f"engines/{key}/{name}"
        for key in ("copilot", "gemini")
        for name in ("package-lock.json", "package.json")
    ]
    assert set(contract.LOCKS) <= contract.watched(STEPS)


# ---------------------------------------------------------------- the steps


def test_every_engine_has_its_step_and_the_outputs_that_its_job_reads():
    assert contract.missing_steps(STEPS) == []
    for workflow in (
        "reusable-issue-assistant.yml",
        "reusable-discussion-assistant.yml",
    ):
        assert step_of("claude", workflow).outputs == {
            "structured_output",
            "execution_file",
        }
        assert step_of("codex", workflow).outputs == {"final-message"}
        assert step_of("gemini", workflow).outputs == {"answer"}
        assert step_of("copilot", workflow).outputs == {"answer"}
    assert step_of("codex").where == (
        ".github/workflows/reusable-issue-assistant.yml, job analyze, step codex"
    )


def test_a_step_without_its_engine_goes_unchecked_no_longer(tmp_path):
    (tmp_path / "empty.yml").write_text("name: Empty\n", encoding="utf-8")
    (tmp_path / "other.yml").write_text(
        yaml.safe_dump(
            {
                "jobs": {
                    "called": {"uses": "./.github/workflows/other.yml"},
                    "run": {
                        "outputs": {
                            "answer": "${{ steps.codex.outputs['final-message'] }}"
                        },
                        "steps": [
                            {"run": "true"},
                            {"id": "codex", "uses": f"openai/codex-action@{SHA}"},
                        ],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    (step,) = contract.engine_steps(tmp_path)
    assert step.outputs == {"final-message"} and step.inputs == {}
    problems = contract.missing_steps([step])
    assert len(problems) == len(assistant.ENGINES) - 1
    assert problems[0] == (
        "No workflow in .github/workflows has a step with the id claude, the engine "
        "Claude, so its names go unchecked."
    )


# ---------------------------------------------------------------- the actions


def test_github_files_are_read_raw_and_a_missing_one_is_none(monkeypatch):
    calls = []
    answers = iter(
        [
            completed("name: An action\n"),
            completed(stderr="gh: Not Found (HTTP 404)", code=1),
            completed(stderr="gh: Bad credentials (HTTP 401)", code=1),
            completed(code=1),
        ]
    )

    def run(command, **options):
        calls.append(command)
        assert options["capture_output"] and not options["check"]
        return next(answers)

    monkeypatch.setattr(contract.subprocess, "run", run)
    assert contract.github_file("o/r", "action.yml", SHA) == "name: An action\n"
    assert contract.github_file("o/r", "action.yml", SHA) is None
    assert calls[0] == [
        "gh",
        "api",
        "-H",
        "Accept: application/vnd.github.raw",
        f"repos/o/r/contents/action.yml?ref={SHA}",
    ]
    with pytest.raises(assistant.AssistantError, match="Bad credentials"):
        contract.github_file("o/r", "action.yml", SHA)
    with pytest.raises(assistant.AssistantError, match="no message"):
        contract.github_file("o/r", "action.yml", SHA)


def test_the_action_file_is_action_yml_or_action_yaml(monkeypatch):
    files = {("o/r", "sub/action.yaml"): "inputs: {prompt: {}}\n"}
    asked = []

    def github_file(repository, path, ref):
        asked.append((repository, path, ref))
        return files.get((repository, path))

    monkeypatch.setattr(contract, "github_file", github_file)
    assert contract.action_file(f"o/r/sub@{SHA}") == {"inputs": {"prompt": {}}}
    assert asked == [("o/r", "sub/action.yml", SHA), ("o/r", "sub/action.yaml", SHA)]
    assert contract.action_file(f"o/other@{SHA}") is None


@pytest.mark.parametrize("key", ["claude", "codex"])
def test_the_names_of_today_pass(key):
    step = step_of(key)
    assert contract.action_problems(step, action_of(step)) == []


def test_a_removed_input_of_codex_action_fails_with_its_name():
    step = step_of("codex")
    action = action_of(step)
    del action["inputs"]["permission-profile"]
    (problem,) = contract.action_problems(step, action)
    assert problem == (
        ".github/workflows/reusable-issue-assistant.yml, job analyze, step codex: "
        "openai/codex-action at bdf19a4 has no input permission-profile; GitHub only "
        "warns about it, and the step runs without it"
    )


def test_a_deprecated_or_required_input_and_a_missing_output_fail():
    step = step_of("codex")
    action = action_of(step)
    action["inputs"]["sandbox-mode"] = {"required": True}
    action["inputs"]["allow-users"]["deprecationMessage"] = "Use\nallowed-users."
    # A required input with a default, or one that isn't required, is no problem.
    action["inputs"]["home"] = {"required": True, "default": ""}
    action["inputs"]["user"] = {"required": False}
    action["inputs"]["extra"] = None
    del action["outputs"]["final-message"]
    problems = contract.action_problems(step, action)
    assert [problem.split(": ", 1)[1] for problem in problems] == [
        "openai/codex-action at bdf19a4 deprecates the input allow-users: Use "
        "allowed-users.",
        "openai/codex-action at bdf19a4 requires the input sandbox-mode, which the "
        "step doesn't pass",
        "openai/codex-action at bdf19a4 has no output final-message, which the job "
        "reads; the answer would be empty",
    ]


def test_codex_action_must_still_choose_the_profile_through_default_permissions():
    step = step_of("codex")
    action = action_of(step)
    action["inputs"]["permission-profile"] = {"description": "A profile."}
    (problem,) = contract.action_problems(step, action)
    assert "hands the input permission-profile to its command line as" in problem
    assert problem.endswith("change ACTION_CLIS in scripts/engine_contract.py")
    action["inputs"]["permission-profile"] = None
    assert len(contract.action_problems(step, action)) == 1


def test_an_action_without_its_file_fails():
    step = step_of("claude")
    (problem,) = contract.action_problems(step, None)
    assert problem.endswith("has no action.yml that can be read")
    assert contract.action_problems(step, {"name": "Bare"}) == [
        f"{step.where}: {contract.pinned(step.uses)} has no input {key}; GitHub only "
        "warns about it, and the step runs without it"
        for key in step.inputs
    ] + [
        f"{step.where}: {contract.pinned(step.uses)} has no output {key}, which the "
        "job reads; the answer would be empty"
        for key in sorted(step.outputs)
    ]


def test_actions_reads_each_action_once_and_reports(monkeypatch, capsys, tmp_path):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    read = []

    def action_file(uses):
        """The action with what every step of it passes and its jobs read."""
        read.append(uses)
        actions = [action_of(step) for step in STEPS if step.uses == uses]
        return {
            part: {
                key: value for action in actions for key, value in action[part].items()
            }
            for part in ("inputs", "outputs")
        }

    monkeypatch.setattr(contract, "action_file", action_file)
    assert contract.actions_command() == 0
    assert sorted(read) == sorted({step.uses for step in STEPS if "@" in step.uses})
    text = summary.read_text(encoding="utf-8")
    assert text.startswith(
        "### Engine contracts: The inputs and outputs of the engines' actions"
    )
    assert "step codex: 10 input(s) and 1 output(s) of openai/codex-action" in text
    assert "::error::" not in capsys.readouterr().out

    def without_outputs(uses):
        return {**action_file(uses), "outputs": {}}

    monkeypatch.setattr(contract, "action_file", without_outputs)
    assert contract.actions_command() == 1
    out = capsys.readouterr().out
    assert "::error::.github/workflows/reusable-issue-assistant.yml, job analyze" in out
    assert "1 output(s) of openai/codex-action at bdf19a4, 1 problem(s)" in out


# ---------------------------------------------------------------- the flags


def test_the_pinned_command_lines_are_those_of_the_workflows():
    pins = contract.cli_pins(STEPS)
    assert [(pin.key, pin.package, pin.action) for pin in pins] == [
        ("codex", "@openai/codex", "openai/codex-action"),
        ("copilot", "@github/copilot", ""),
        ("gemini", "@google/gemini-cli", ""),
    ]
    for pin in pins:
        assert assistant.CLI_VERSION.fullmatch(pin.version), pin
    assert pins[0].version == step_of("codex").inputs["codex-version"]
    # The Gemini CLI and the Copilot CLI at the versions of their lock files.
    assert pins[1].version == assistant.locked_version("copilot")
    assert pins[2].version == assistant.locked_version("gemini")
    assert "engine-version" not in step_of("gemini").inputs


def test_flags_are_read_with_their_values():
    assert contract.passed_flags(
        ["--json", "--config", "a=1", "--config=b", "-x", "1", "2", "--last"]
    ) == {"--json": set(), "--config": {"a=1", "b"}, "-x": {"1"}, "--last": set()}


def test_the_help_of_each_command_line_lists_its_options_and_their_values():
    gemini = contract.help_options(GEMINI_HELP)
    assert gemini["--output-format"] == {"text", "json", "stream-json"}
    assert gemini["-o"] == gemini["--output-format"]
    assert gemini["--skip-trust"] is None and "--prompt" in gemini
    copilot = contract.help_options(COPILOT_HELP)
    assert copilot["--stream"] == {"on", "off"}
    assert copilot["--reasoning-effort"] >= set(assistant.EFFORTS)
    assert copilot["-C"] is None and copilot["--allow-tool"] is None
    # Neither a hidden alias nor the flags of the examples.
    assert "--effort" not in copilot and "--allow-all-tools" in copilot
    assert "-i" not in copilot
    codex = contract.help_options(CODEX_HELP)
    assert codex["-s"] == {"read-only", "workspace-write", "danger-full-access"}
    assert {"--json", "--config", "-c", "--strict-config"} <= set(codex)


@pytest.mark.parametrize(
    ("key", "text"), [("gemini", GEMINI_HELP), ("copilot", COPILOT_HELP)]
)
def test_the_command_lines_of_analyze_pass_the_help_of_their_pins(key, text):
    """Copilot's help lists --reasoning-effort; --effort is only a hidden alias."""
    efforts = assistant.ENGINES[key].efforts or ("",)
    for effort in efforts:
        flags = contract.passed_flags(command_line(key, effort))
        assert contract.flag_problems(key, flags, contract.help_options(text)) == []


def test_the_arguments_of_the_codex_step_pass_the_help_of_its_pin():
    arguments = json.loads(
        assistant.engine_arguments(assistant.ENGINES["codex"], "The rules.")
    )
    flags = contract.passed_flags(
        [*contract.ACTION_CLIS["openai/codex-action"].checking, *arguments]
    )
    assert (
        contract.flag_problems("codex", flags, contract.help_options(CODEX_HELP)) == []
    )


def test_a_renamed_copilot_flag_fails_with_its_name():
    renamed = COPILOT_HELP.replace("--disable-builtin-mcps", "--disable-builtin-mcp")
    flags = contract.passed_flags(command_line("copilot"))
    (problem,) = contract.flag_problems(
        "@github/copilot@1.0.98", flags, contract.help_options(renamed)
    )
    assert problem == (
        "@github/copilot@1.0.98: its --help lists no --disable-builtin-mcps; a flag "
        "that it doesn't list may go without notice in its next release"
    )


def test_a_value_that_a_flag_no_longer_takes_fails():
    changed = COPILOT_HELP.replace(
        "[possible values: on, off]", "[possible values: on]"
    )
    flags = contract.passed_flags(command_line("copilot"))
    (problem,) = contract.flag_problems(
        "copilot", flags, contract.help_options(changed)
    )
    assert problem == "copilot takes no off for --stream, only on"


# ---------------------------------------------------------------- the settings


GEMINI = assistant.ENGINES["gemini"].cli
SETTINGS = assistant.cli_settings(GEMINI, assistant.Run("auto", 3, "1", ""))


def test_the_settings_of_gemini_pass_a_schema_that_has_them():
    schema = strict_schema(SETTINGS)
    assert contract.settings_problems(SETTINGS, schema, schema) == []
    assert contract.keys_of(SETTINGS) > 20 and contract.keys_of([]) == 0


def test_an_unknown_gemini_key_fails_with_its_name():
    schema = strict_schema(SETTINGS)
    del schema["properties"]["experimental"]["properties"]["enableAgents"]
    assert contract.settings_problems(SETTINGS, schema, schema) == [
        "experimental.enableAgents is no key of the settings"
    ]


def test_the_schema_is_read_as_json_schema_reads_it():
    root = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "model": {"$ref": "#/$defs/Model", "description": "Beside the $ref."},
            "list": {"type": "array", "items": {"type": "string"}},
            "kind": {"enum": ["a", "b"]},
            "either": {"anyOf": [{"type": "string"}, {"type": "boolean"}]},
            "one": {"oneOf": [{"type": "integer"}]},
            "map": {"type": "object", "additionalProperties": {"type": "boolean"}},
            "free": {"type": "object"},
            "open": {"properties": {"a": {}}, "additionalProperties": True},
            "elsewhere": {"$ref": "https://example.com/schema.json"},
            "gone": {"$ref": "#/$defs/Gone", "type": "string"},
            "loop": {"$ref": "#/$defs/Loop"},
            "several": {"type": ["string", "null"]},
            "true": True,
        },
        "$defs": {
            "Model": {
                "type": "object",
                "properties": {"turns": {"type": "number"}, "name": {}},
            },
            "Loop": {"$ref": "#/$defs/Loop"},
        },
    }
    good = {
        "model": {"turns": 7.5, "name": None},
        "list": ["a"],
        "kind": "a",
        "either": True,
        "one": 2.0,
        "map": {"x": True},
        "free": {"anything": 1},
        "open": {"a": 1, "b": 2},
        "elsewhere": 1,
        "gone": "text",
        "loop": 1,
        "several": None,
        "true": [],
    }
    assert contract.settings_problems(good, root, root) == []
    bad = {
        "model": {"turns": True, "other": 1},
        "list": ["a", 1],
        "kind": "c",
        "either": 1,
        "one": 1.5,
        "map": {"x": "yes"},
        "gone": 1,
        "several": 1,
        "unknown": 1,
    }
    assert contract.settings_problems(bad, root, root) == [
        "model.turns is no number",
        "model.other is no key of the settings",
        "list[1] is no string",
        'kind is none of "a", "b"',
        "either is no string",
        "one is no integer",
        "map.x is no boolean",
        "gone is no string",
        "several is no string or null",
        "unknown is no key of the settings",
    ]
    assert contract.settings_problems("text", root, root) == [" is no object"]


def test_a_type_that_json_schema_doesnt_have_takes_anything():
    assert contract.fits("anything", "date")
    assert not contract.fits(True, "integer") and contract.fits(3, "integer")


def test_the_schema_of_the_settings_comes_from_the_repository_of_the_package(
    monkeypatch, tmp_path
):
    package = tmp_path / "node_modules" / "@google" / "gemini-cli"
    package.mkdir(parents=True)
    command = str(tmp_path / "node_modules" / ".bin" / "gemini")
    asked = []
    files = {"schemas/settings.schema.json": '{"type": "object"}'}

    def github_file(repository, path, ref):
        asked.append((repository, path, ref))
        return files.get(path)

    monkeypatch.setattr(contract, "github_file", github_file)
    pin = contract.Pin("gemini", "@google/gemini-cli", "0.64.0")
    for repository in (
        {"type": "git", "url": "git+https://github.com/google-gemini/gemini-cli.git"},
        "github.com/google-gemini/gemini-cli",
    ):
        manifest = {"name": pin.package, "repository": repository}
        (package / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
        assert contract.settings_schema(pin, command) == {"type": "object"}
    assert asked[0] == (
        "google-gemini/gemini-cli",
        "schemas/settings.schema.json",
        "v0.64.0",
    )
    files["schemas/settings.schema.json"] = "[]"
    assert contract.settings_schema(pin, command) is None
    del files["schemas/settings.schema.json"]
    assert contract.settings_schema(pin, command) is None
    (package / "package.json").write_text("{}", encoding="utf-8")
    assert contract.settings_schema(pin, command) is None
    other = tmp_path / "node_modules" / "other"
    other.mkdir()
    (other / "package.json").write_text("{}", encoding="utf-8")
    assert (
        contract.settings_schema(contract.Pin("x", "other", "1.0.0"), command) is None
    )


# ---------------------------------------------------------------- the runs


def test_a_command_line_runs_for_its_help_with_a_home_of_its_own(monkeypatch, tmp_path):
    variables = contract.environment(tmp_path)
    assert set(variables) == {"PATH", "HOME", "LANG", "NO_COLOR"}
    assert variables["HOME"] == str(tmp_path)
    # Python needs the variables of the system on Windows.
    monkeypatch.setattr(contract, "environment", lambda home: dict(os.environ))
    helping = [sys.executable, "-c", "import sys; print('usage', sys.argv[1:])"]
    assert contract.help_of(helping, tmp_path / "home").startswith("usage ['--help']\n")
    failing = [sys.executable, "-c", "import sys; sys.exit('no help')"]
    with pytest.raises(assistant.AssistantError, match="-c .* --help failed: no help"):
        contract.help_of(failing, tmp_path / "home")


SETTINGS_FILE = Path("/work") / assistant.CLI_SETTINGS
GEMINI_PIN = contract.Pin("gemini", "@google/gemini-cli", "0.64.0")
GEMINI_WITHOUT_KEY = json.dumps(
    {
        "error": {
            "message": "Please set an Auth method or specify one of the following "
            "environment variables before running: GEMINI_API_KEY",
            "code": 41,
        }
    }
)


def test_a_run_without_a_key_must_stop_at_the_key_and_take_its_settings():
    assert (
        contract.run_problems(
            GEMINI_PIN, completed(GEMINI_WITHOUT_KEY, code=41), SETTINGS_FILE
        )
        == []
    )
    skipped = (
        f"Security Warning: Skipping system settings file '{SETTINGS_FILE}': Parent "
        "directory '/tmp' is insecure\n"
    )
    (problem,) = contract.run_problems(
        GEMINI_PIN, completed(GEMINI_WITHOUT_KEY, skipped, 41), SETTINGS_FILE
    )
    assert problem.startswith(
        "@google/gemini-cli@0.64.0 refused or skipped its settings: Security Warning:"
    )
    (problem,) = contract.run_problems(
        GEMINI_PIN,
        completed(stderr="Unknown arguments: skip-trust", code=1),
        SETTINGS_FILE,
    )
    assert problem == (
        "@google/gemini-cli@0.64.0 ran without a key and didn't stop asking for "
        "GEMINI_API_KEY, so an argument or a setting may have stopped it first: "
        "Unknown arguments: skip-trust"
    )
    (problem,) = contract.run_problems(GEMINI_PIN, completed(), SETTINGS_FILE)
    assert problem.endswith("first: no output")


def fake_install(monkeypatch, installed, lock=()):
    """install_cli without npm, and the problems `lock` of its lock file."""

    def install(key, folder, locks=assistant.ENGINE_LOCKS):
        installed.append((key, folder.name))
        return f"/bin/{assistant.ENGINES[key].cli.command}"

    monkeypatch.setattr(assistant, "install_cli", install)
    monkeypatch.setattr(contract, "lock_problems", lambda pin, work: list(lock))


def test_check_cli_runs_the_command_line_as_analyze_runs_it(monkeypatch, tmp_path):
    installed, ran, helped = [], [], []
    fake_install(monkeypatch, installed)
    monkeypatch.setattr(
        contract,
        "help_of",
        lambda command, home: helped.append(command) or GEMINI_HELP,
    )
    schema = strict_schema(SETTINGS)
    monkeypatch.setattr(contract, "settings_schema", lambda pin, path: schema)

    def run_cli(engine, cli, command_path, run, prompt, root, work):
        ran.append((engine.name, command_path, run, root, work))
        return completed(GEMINI_WITHOUT_KEY, code=41)

    monkeypatch.setattr(assistant, "run_cli", run_cli)
    checked, problems = contract.check_cli(GEMINI_PIN, tmp_path)
    assert problems == []
    assert checked == (
        "the signatures of its lock file, a changed hash refused, 4 flag(s), "
        f"{contract.keys_of(SETTINGS)} key(s) of its settings, a run without "
        "GEMINI_API_KEY, a run with broken settings in GEMINI_CLI_SYSTEM_SETTINGS_PATH"
    )
    assert installed == [("gemini", "cli")]
    assert helped == [["/bin/gemini"]]
    assert ran == [
        (
            "Gemini",
            "/bin/gemini",
            assistant.Run("auto", contract.TURNS, "1", ""),
            tmp_path / "checkout",
            tmp_path,
        )
    ]


def test_check_cli_fails_without_the_schema_of_the_settings(monkeypatch, tmp_path):
    fake_install(monkeypatch, [])
    monkeypatch.setattr(contract, "help_of", lambda command, home: GEMINI_HELP)
    monkeypatch.setattr(contract, "settings_schema", lambda pin, path: None)
    monkeypatch.setattr(
        assistant,
        "run_cli",
        lambda *arguments: completed(GEMINI_WITHOUT_KEY, code=41),
    )
    _, (problem,) = contract.check_cli(GEMINI_PIN, tmp_path)
    assert problem.startswith(
        "@google/gemini-cli@0.64.0: no JSON schema of its settings could be found"
    )
    # An unknown key, by its path.
    schema = strict_schema({"tools": {}})
    monkeypatch.setattr(contract, "settings_schema", lambda pin, path: schema)
    _, problems = contract.check_cli(GEMINI_PIN, tmp_path / "again")
    assert problems[0] == (
        "@google/gemini-cli@0.64.0: tools.core is no key of the settings"
    )


def test_check_cli_checks_every_effort_and_no_settings_of_copilot(
    monkeypatch, tmp_path
):
    fake_install(monkeypatch, [])
    monkeypatch.setattr(contract, "help_of", lambda command, home: COPILOT_HELP)
    printed = "Error: No authentication information found. Set COPILOT_GITHUB_TOKEN."
    monkeypatch.setattr(
        assistant,
        "run_cli",
        lambda *arguments: completed(stderr=printed, code=1),
    )
    pin = contract.Pin("copilot", "@github/copilot", "1.0.97")
    checked, problems = contract.check_cli(pin, tmp_path)
    assert problems == []
    assert checked == (
        "the signatures of its lock file, a changed hash refused, 13 flag(s), a run "
        "without COPILOT_GITHUB_TOKEN"
    )
    # A level of effort that the flag doesn't take.
    monkeypatch.setattr(
        contract,
        "help_of",
        lambda command, home: COPILOT_HELP.replace(", xhigh, max]", "]"),
    )
    _, (problem,) = contract.check_cli(pin, tmp_path / "again")
    assert problem == (
        "@github/copilot@1.0.97 takes no max, xhigh for --reasoning-effort, only "
        "high, low, medium, minimal, none"
    )
    # A problem of the lock file comes first.
    fake_install(monkeypatch, [], ["a signature"])
    problems = contract.check_cli(pin, tmp_path / "lock")[1]
    assert problems[0] == "a signature" and len(problems) == 2


def write_script(tmp_path, code):
    script = tmp_path / "cli.py"
    script.write_text(code, encoding="utf-8")
    return [sys.executable, str(script)]


def test_codex_passes_once_it_starts_its_turn(tmp_path):
    command = write_script(
        tmp_path,
        "import json, time\n"
        "print('Reading additional input from stdin...', flush=True)\n"
        "print(json.dumps([1]), flush=True)\n"
        "print(json.dumps({'type': 'thread.started'}), flush=True)\n"
        "print(json.dumps({'type': 'turn.started'}), flush=True)\n"
        "time.sleep(60)\n",
    )
    started, printed = contract.turn_started(
        command, dict(os.environ), tmp_path, "turn.started"
    )
    assert started and printed.endswith('{"type": "turn.started"}\n')


def test_codex_fails_when_it_stops_or_hangs_before_its_turn(monkeypatch, tmp_path):
    command = write_script(
        tmp_path,
        "print('Error loading config.toml: unknown configuration field `web_searchx`')\n",
    )
    assert contract.turn_started(
        command, dict(os.environ), tmp_path, "turn.started"
    ) == (
        False,
        "Error loading config.toml: unknown configuration field `web_searchx`\n",
    )
    monkeypatch.setattr(contract, "SECONDS", 0.5)
    hanging = write_script(tmp_path, "import time\ntime.sleep(60)\n")
    assert contract.turn_started(hanging, dict(os.environ), tmp_path, "x") == (
        False,
        "",
    )


CODEX_PIN = contract.Pin("codex", "@openai/codex", "0.162.1", "openai/codex-action")


def test_codex_runs_with_the_arguments_of_its_step_and_the_profile_of_its_action(
    monkeypatch, tmp_path
):
    installed, runs = [], []

    def install_package(cli, version, folder):
        installed.append((cli.package, version, folder.name))
        return f"/bin/{cli.command}"

    monkeypatch.setattr(contract, "install_package", install_package)
    monkeypatch.setattr(contract, "help_of", lambda command, home: CODEX_HELP)

    def turn_started(command, env, cwd, started):
        runs.append((command, env, cwd, started))
        return True, ""

    monkeypatch.setattr(contract, "turn_started", turn_started)
    checked, problems = contract.check_action_cli(CODEX_PIN, STEPS, tmp_path)
    assert problems == [] and checked == "4 flag(s), 3 key(s) of its configuration"
    assert installed == [("@openai/codex", "0.162.1", "cli")]
    ((command, env, cwd, started),) = runs
    assert command == [
        "/bin/codex",
        "exec",
        "--strict-config",
        "--skip-git-repo-check",
        "--json",
        "--config",
        'web_search="disabled"',
        "--config",
        'developer_instructions="The rules."',
        "--config",
        'default_permissions=":read-only"',
        contract.PROMPT,
    ]
    assert env["CODEX_HOME"] == str(tmp_path / "home" / ".codex")
    assert env["RUST_LOG"] == "off" and "OPENAI_API_KEY" not in env
    assert (cwd, started) == (tmp_path / "checkout", "turn.started")
    monkeypatch.setattr(
        contract, "turn_started", lambda *arguments: (False, "Error: unknown\nfield")
    )
    _, (problem,) = contract.check_action_cli(CODEX_PIN, [], tmp_path / "again")
    assert problem == (
        "@openai/codex@0.162.1 didn't start its turn with the arguments of its step "
        "and the configuration of its action: Error: unknown field"
    )


# ---------------------------------------------------------------- the lock files


class FakeAudit:
    """subprocess.run for npm audit signatures, which records its call and ends with
    `code`."""

    def __init__(self, code=0, stdout="7 packages have verified registry signatures"):
        self.code, self.stdout, self.calls = code, stdout, []

    def __call__(self, command, **options):
        self.calls.append((command, options))
        return completed(self.stdout, "npm error audit", self.code)


def tampered_install(error):
    """install_cli for the copy of the lock file with another hash of the command
    line, which raises `error` unless it is None."""
    installed = []

    def install(key, folder, locks=assistant.ENGINE_LOCKS):
        lock = json.loads((locks / key / "package-lock.json").read_text("utf-8"))
        package = assistant.ENGINES[key].cli.package
        installed.append(
            (key, folder.name, lock["packages"][f"node_modules/{package}"]["integrity"])
        )
        if error is not None:
            raise assistant.AssistantError(error)
        return "/bin/gemini"

    return install, installed


EINTEGRITY = (
    "npm installed no @google/gemini-cli@0.64.0 from "
    "engines/gemini/package-lock.json (EINTEGRITY): the registry sent a package "
    "whose hash isn't the integrity of the lock file, so nothing of it runs"
)


def test_the_lock_file_passes_when_its_signatures_verify_and_npm_refuses_a_hash(
    monkeypatch, tmp_path
):
    audit = FakeAudit()
    monkeypatch.setattr(contract.subprocess, "run", audit)
    monkeypatch.setattr(contract.shutil, "which", lambda name: f"/usr/bin/{name}")
    install, installed = tampered_install(EINTEGRITY)
    monkeypatch.setattr(assistant, "install_cli", install)
    assert contract.lock_problems(GEMINI_PIN, tmp_path) == []
    ((command, options),) = audit.calls
    folder = tmp_path / "cli"
    assert command == ["/usr/bin/npm", "audit", "signatures", "--prefix", str(folder)]
    assert options["cwd"] == folder and options["timeout"] == contract.SECONDS
    # A copy of the lock file, with another hash of the command line alone.
    assert installed == [("gemini", "tampered-cli", contract.TAMPERED)]
    copy = json.loads(
        (tmp_path / "tampered" / "gemini" / "package-lock.json").read_text("utf-8")
    )
    lock = json.loads(
        (assistant.ENGINE_LOCKS / "gemini" / "package-lock.json").read_text("utf-8")
    )
    lock["packages"]["node_modules/@google/gemini-cli"]["integrity"] = contract.TAMPERED
    assert copy == lock
    assert contract.TAMPERED.startswith("sha512-") and contract.TAMPERED not in str(
        (assistant.ENGINE_LOCKS / "gemini" / "package-lock.json").read_text("utf-8")
    )


@pytest.mark.parametrize(
    ("code", "error", "problems"),
    [
        (
            1,
            EINTEGRITY,
            [
                "@google/gemini-cli@0.64.0: npm can't verify every signature and "
                "attestation of the packages of engines/gemini/package-lock.json: 1 "
                "package has an invalid registry signature npm error audit"
            ],
        ),
        (
            0,
            None,
            [
                "@google/gemini-cli@0.64.0: npm installed it from a copy of "
                "engines/gemini/package-lock.json with another hash of it, so analyze "
                "would run a package that nobody checked"
            ],
        ),
        (
            0,
            "npm installed no @google/gemini-cli@0.64.0 from "
            "engines/gemini/package-lock.json (E404): gone",
            [
                "@google/gemini-cli@0.64.0: npm refused a copy of "
                "engines/gemini/package-lock.json with another hash of it, but not "
                "for the hash: npm installed no @google/gemini-cli@0.64.0 from "
                "engines/gemini/package-lock.json (E404): gone"
            ],
        ),
    ],
)
def test_a_signature_that_fails_or_a_hash_that_npm_takes_fails_the_check(
    monkeypatch, tmp_path, code, error, problems
):
    audit = FakeAudit(code, "1 package has an invalid registry signature")
    monkeypatch.setattr(contract.subprocess, "run", audit)
    # Without npm on the PATH, the name alone, which fails as it would.
    monkeypatch.setattr(contract.shutil, "which", lambda name: None)
    install, _ = tampered_install(error)
    monkeypatch.setattr(assistant, "install_cli", install)
    assert contract.lock_problems(GEMINI_PIN, tmp_path) == problems
    assert audit.calls[0][0][0] == "npm"


def test_codex_installs_as_its_action_installs_it(monkeypatch, tmp_path):
    cli = assistant.Cli(package="@openai/codex", command="codex", arguments=())
    monkeypatch.setattr(contract.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []

    def run(command, **options):
        calls.append(command)
        return completed()

    monkeypatch.setattr(contract.subprocess, "run", run)
    folder = tmp_path / "cli"
    path = contract.install_package(cli, "0.162.1", folder)
    assert path == str(folder / "node_modules" / ".bin" / "codex")
    # The version of the action's input, without the scripts of any package.
    assert calls == [
        [
            "/usr/bin/npm",
            "install",
            "--prefix",
            str(folder),
            "--no-save",
            "--no-package-lock",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            "--loglevel",
            "error",
            "@openai/codex@0.162.1",
        ]
    ]


@pytest.mark.parametrize(
    ("version", "which", "result", "problem"),
    [
        (
            "latest",
            "/usr/bin/npm",
            completed(),
            "The step pins @openai/codex at 'latest', no version such as 1.2.3.",
        ),
        (
            "0.162.1",
            None,
            completed(),
            "The check needs npm, which GitHub's runners have.",
        ),
        (
            "0.162.1",
            "/usr/bin/npm",
            completed(stderr="npm error 404\n", code=1),
            "npm installed no @openai/codex@0.162.1: npm error 404",
        ),
        (
            "0.162.1",
            "/usr/bin/npm",
            completed(code=1),
            "npm installed no @openai/codex@0.162.1: no message",
        ),
    ],
)
def test_codex_that_can_t_install_says_why(
    monkeypatch, tmp_path, version, which, result, problem
):
    cli = assistant.Cli(package="@openai/codex", command="codex", arguments=())
    monkeypatch.setattr(contract.shutil, "which", lambda name: which)
    monkeypatch.setattr(contract.subprocess, "run", lambda command, **options: result)
    with pytest.raises(assistant.AssistantError) as refused:
        contract.install_package(cli, version, tmp_path / "cli")
    assert str(refused.value) == problem


def test_clis_checks_each_pin_in_a_folder_of_its_own(monkeypatch, capsys, tmp_path):
    for engine in assistant.ENGINES.values():
        for name in engine.credentials:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    folders = []

    def check_cli(pin, work):
        folders.append(work)
        assert work.parent == tmp_path and work.is_dir()
        if pin.key == "gemini":
            raise assistant.AssistantError("npm installed nothing")
        return "2 flag(s)", []

    def check_action_cli(pin, steps, work):
        folders.append(work)
        return "1 flag(s)", []

    monkeypatch.setattr(contract, "check_cli", check_cli)
    monkeypatch.setattr(contract, "check_action_cli", check_action_cli)
    assert contract.clis_command() == 1
    out = capsys.readouterr().out
    assert "::error::@google/gemini-cli@" in out and ": npm installed nothing" in out
    assert "- Codex, @openai/codex@" in out and ": 1 flag(s)\n" in out
    assert "- Gemini, @google/gemini-cli@" in out and ": stopped, 1 problem(s)" in out
    # Each folder goes when its check ends.
    assert len(folders) == 3 and not any(folder.exists() for folder in folders)


def test_clis_runs_no_engine_with_a_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "a key")
    with pytest.raises(assistant.AssistantError, match="unset GEMINI_API_KEY"):
        contract.clis_command()


# ---------------------------------------------------------------- the changes


@pytest.mark.parametrize(
    ("files", "runs"),
    [
        (".github/workflows/reusable-discussion-assistant.yml\nREADME.md\n", "true"),
        ("issue_assistant/cli.py\n", "true"),
        ("engines/copilot/package-lock.json\n", "true"),
        ("README.md\ndocs/security.md\n", "false"),
    ],
)
def test_changes_tells_whether_the_check_runs(
    monkeypatch, capsys, tmp_path, files, runs
):
    output = tmp_path / "output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("PR", "7")
    asked = []
    monkeypatch.setattr(
        contract,
        "run_gh",
        lambda arguments: asked.append(arguments) or completed(files),
    )
    assert contract.changes_command() == 0
    assert asked == [
        ["api", "--paginate", "repos/o/r/pulls/7/files", "--jq", ".[].filename"]
    ]
    assert output.read_text(encoding="utf-8") == f"engines={runs}\n"
    said = capsys.readouterr().out
    assert said.endswith("so the check runs.\n" if runs == "true" else "running.\n")


def test_changes_fails_when_github_lists_no_files(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("PR", "7")
    monkeypatch.setattr(contract, "run_gh", lambda arguments: completed(code=1))
    with pytest.raises(assistant.AssistantError, match="#7: no message"):
        contract.changes_command()


# ---------------------------------------------------------------- the command


def test_the_script_runs_one_command(monkeypatch, capsys):
    assert contract.main([]) == 2
    assert contract.main(["other"]) == 2
    assert capsys.readouterr().err == (
        "usage: engine_contract.py {changes,actions,clis}\n" * 2
    )

    def failing():
        raise assistant.AssistantError("GitHub gave no action.yml")

    monkeypatch.setitem(contract.COMMANDS, "actions", failing)
    assert contract.main(["actions"]) == 1
    assert capsys.readouterr().out == "::error::GitHub gave no action.yml\n"


def test_the_script_exits_with_the_status_of_its_command(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["engine_contract.py"])
    with pytest.raises(SystemExit) as stop:
        runpy.run_path(
            str(ROOT / "scripts" / "engine_contract.py"), run_name="__main__"
        )
    assert stop.value.code == 2


def test_check_cli_looks_for_the_settings_where_gemini_reads_them(
    monkeypatch, tmp_path
):
    fake_install(monkeypatch, [])
    monkeypatch.setattr(contract, "help_of", lambda command, home: GEMINI_HELP)
    schema = strict_schema(SETTINGS)
    monkeypatch.setattr(contract, "settings_schema", lambda pin, path: schema)
    skipped = (
        "Security Warning: Skipping system settings file "
        f"'/etc/issue-assistant/{tmp_path.name}/settings.json': Directory "
        "'/etc/issue-assistant' is not owned by root (uid 0)."
    )
    monkeypatch.setattr(
        assistant,
        "run_cli",
        lambda *arguments: completed(GEMINI_WITHOUT_KEY, skipped, 41),
    )
    _, (problem,) = contract.check_cli(GEMINI_PIN, tmp_path)
    assert problem.startswith(
        "@google/gemini-cli@0.64.0 refused or skipped its settings: Security Warning:"
    )


@pytest.fixture
def placed(monkeypatch):
    """The commands that place the settings as root, which succeed."""
    commands = []

    def run(command):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(assistant.running, "as_root", run)
    return commands


def probe_run(monkeypatch, outcome):
    """Runs of the command line with broken settings, which end with `outcome`."""
    calls = []

    def run(command, **options):
        settings = options["env"]["GEMINI_CLI_SYSTEM_SETTINGS_PATH"]
        calls.append((command, options))
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome(settings)

    monkeypatch.setattr(contract.subprocess, "run", run)
    return calls


def probe(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    run = assistant.Run("auto", contract.TURNS, "1", "")
    return SETTINGS_READ(GEMINI_PIN, "/bin/gemini", run, checkout, tmp_path)


def test_gemini_refuses_broken_settings_in_the_file_of_its_variable(
    monkeypatch, tmp_path, placed
):
    def refused(settings):
        error = f"Error in {settings}: Settings file is not a valid JSON object."
        return completed(stderr=error, code=1)

    calls = probe_run(monkeypatch, refused)
    assert probe(tmp_path) == []
    folder = f"/etc/issue-assistant/{tmp_path.name}-probe"
    ((command, options),) = calls
    assert command[0] == "/bin/gemini"
    assert (
        options["env"]["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] == f"{folder}/settings.json"
    )
    assert options["env"]["HOME"] == str(tmp_path / f"{tmp_path.name}-probe" / "home")
    assert options["timeout"] == contract.SECONDS
    written = tmp_path / f"{tmp_path.name}-probe" / "settings.json"
    assert written.read_text("utf-8") == contract.BROKEN_SETTINGS
    # Placed as root, as analyze places them, and gone after the run.
    assert placed[-1] == ["rm", "-rf", folder]
    assert placed[1][-1] == f"{folder}/settings.json"


@pytest.mark.parametrize(
    ("outcome", "printed"),
    [
        # It no longer reads the variable, and stops at the key.
        (lambda settings: completed(GEMINI_WITHOUT_KEY, code=41), "Please set"),
        # It skips the file, with a warning.
        (
            lambda settings: completed(
                stderr=f"Security Warning: Skipping system settings file "
                f"'{settings}': not owned by root",
                code=41,
            ),
            "Security Warning: Skipping",
        ),
        # It names the file, but runs on.
        (lambda settings: completed(stderr=f"Error in {settings}"), "Error in"),
        (lambda settings: completed(code=1), "no output"),
        (
            subprocess.TimeoutExpired("gemini", contract.SECONDS),
            f"no end within {contract.SECONDS} seconds",
        ),
    ],
)
def test_gemini_that_takes_broken_settings_fails_the_check(
    monkeypatch, tmp_path, placed, outcome, printed
):
    probe_run(monkeypatch, outcome)
    (problem,) = probe(tmp_path)
    assert problem.startswith(
        "@google/gemini-cli@0.64.0 took broken settings in the file that "
        "GEMINI_CLI_SYSTEM_SETTINGS_PATH names without refusing them: it no longer "
        "reads that file, or skips it, and would run without its settings, "
        "silently: "
    )
    assert printed in problem
    assert placed[-1][:2] == ["rm", "-rf"]
