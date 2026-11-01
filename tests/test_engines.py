"""The engines next to Claude: how each gets the rules and the schema, the firewall
policy of each, and the command lines that `analyze` runs with tools that only read.

No test reaches a vendor: npm and the command lines are fakes in place of
subprocess.run, which record what the assistant would start and with which
variables.
"""

import base64
import dataclasses
import json
import os
import re
import shutil
import subprocess
import tomllib

import pytest

import issue_assistant as assistant
from support import FakeGitHub, make_answer, make_issue, run_main

CLI_ENGINES = [key for key, engine in assistant.ENGINES.items() if engine.cli]


def engine_config(config, engine, **changes):
    return dataclasses.replace(config, engine=engine, **changes)


# How each engine gets the rules and the schema


def test_each_engine_gets_the_schema_as_it_can_take_it():
    assert {key: engine.schema for key, engine in assistant.ENGINES.items()} == {
        "claude": assistant.ENFORCED,
        "codex": assistant.STRICT,
        "gemini": assistant.IN_PROMPT,
        "copilot": assistant.IN_PROMPT,
    }
    assert CLI_ENGINES == ["gemini", "copilot"]


@pytest.mark.parametrize("mode", assistant.MODES)
def test_the_strict_schema_requires_every_field_and_lets_the_optional_be_null(
    config, mode
):
    schema = assistant.answer_schema(mode, config)
    strict = json.loads(assistant.schema_json(mode, config, strict=True))

    def walk(full, form):
        if "properties" in full:
            assert form["required"] == list(full["properties"])
            assert form["additionalProperties"] is False
            for key, value in full["properties"].items():
                if key in full["required"]:
                    walk(value, form["properties"][key])
                else:
                    inner, null = form["properties"][key]["anyOf"]
                    assert null == {"type": "null"}
                    walk(value, inner)
        if full.get("type") == "array":
            # Every list names its items, also one that must stay empty.
            assert "items" in form
            walk(full.get("items", {"type": "string"}), form["items"])

    walk(schema, strict)
    # The schema of the other engines is left as it was.
    assert json.loads(assistant.schema_json(mode, config)) == schema


def test_a_list_that_must_stay_empty_still_names_its_items(config):
    without_areas = dataclasses.replace(
        config,
        labels=tuple(label for label in config.labels if label.group != "area"),
    )
    strict = json.loads(assistant.schema_json("triage", without_areas, strict=True))
    assert strict["properties"]["areas"] == {
        "type": "array",
        "maxItems": 0,
        "items": {"type": "string"},
    }


def test_the_nulls_of_a_strict_answer_are_left_out(config):
    answer = make_answer(
        references=[
            {"path": "README.md", "line": None, "anchor": None, "reason": "Setup"},
            {"path": "src/a.py", "line": 3, "anchor": None, "reason": "Code"},
        ]
    )
    parsed = assistant.parse_answer("triage", json.dumps(answer), config)
    assert parsed["references"] == [
        {"path": "README.md", "reason": "Setup"},
        {"path": "src/a.py", "line": 3, "reason": "Code"},
    ]
    # A field that the answer must give stays null, and the schema refuses it.
    with pytest.raises(assistant.AssistantError, match="answer.summary is not text"):
        assistant.parse_answer("triage", json.dumps(make_answer(summary=None)), config)


def test_codex_gets_the_strict_schema_and_the_rules_as_developer_instructions(
    config, root
):
    codex = engine_config(config, "codex")
    written = assistant.write_context(
        FakeGitHub(codex, make_issue()), 12, "triage", codex
    )
    folder = root / assistant.CONTEXT
    schema = json.loads((folder / "schema.json").read_text("utf-8"))
    assert schema == json.loads(written["schema"])
    assert schema == assistant.strict_schema(assistant.answer_schema("triage", codex))
    # The task alone; the rules come before it as its developer instructions.
    assert (folder / "prompt.md").read_text("utf-8") == written["task"]
    assert "Your answer is one JSON object" not in written["task"]
    rules = (folder / "rules.md").read_text("utf-8")
    arguments = json.loads(written["engine-args"])
    assert arguments[:4] == ["--json", "--config", 'web_search="disabled"', "--config"]
    key, _, value = arguments[4].partition("=")
    assert key == "developer_instructions"
    assert tomllib.loads(f"rules = {value}")["rules"] == rules


@pytest.mark.parametrize("engine", CLI_ENGINES)
def test_an_engine_that_can_t_enforce_the_schema_is_asked_for_it(config, root, engine):
    chosen = engine_config(config, engine)
    written = assistant.write_context(
        FakeGitHub(chosen, make_issue()), 12, "follow-up", chosen
    )
    task = written["task"]
    assert task.endswith("A script reads the object and refuses anything else.\n"), (
        task[-200:]
    )
    assert f"`{assistant.CONTEXT}/schema.json`" in task
    assert (root / assistant.CONTEXT / "prompt.md").read_text("utf-8") == task
    assert json.loads(written["schema"]) == assistant.answer_schema("follow-up", chosen)
    assert written["engine-args"] == ""


def test_claude_takes_the_task_and_the_schema_as_before(config, root):
    written = assistant.write_context(
        FakeGitHub(config, make_issue()), 12, "triage", config
    )
    assert "Your answer is one JSON object" not in written["task"]
    assert written["engine-args"] == ""


@pytest.mark.parametrize(
    "text",
    [
        "Plain rules.",
        'Quotes " and backslashes \\ and \\n as text',
        "Lines\nand\ttabs\r\nand a bell \a, a delete \x7f and \x00",
        "Ümlaute, emoji 🎉 and \u202e the controls of direction",
    ],
)
def test_the_rules_reach_codex_as_they_are(text):
    """Codex reads the value of --config as TOML; every text reads back as itself."""
    assert tomllib.loads(f"rules = {assistant.toml_string(text)}")["rules"] == text


# The effort, which Gemini doesn't take


def test_an_effort_that_the_engine_doesn_t_take_is_refused(config):
    gemini = engine_config(config, "gemini", effort="high")
    problem = (
        ".github/issue-assistant/config.toml: Gemini takes no effort 'high'; it takes "
        "none, so leave it out"
    )
    assert assistant.engine_problems(gemini) == [problem]
    with pytest.raises(assistant.SettingsError) as refused:
        assistant.task_run(gemini, "triage")
    assert refused.value.problems == [problem]
    for engine in ("claude", "codex", "copilot"):
        assert (
            assistant.engine_problems(engine_config(config, engine, effort="max")) == []
        )
    # A typo is named as before, whichever the engine.
    typo = engine_config(config, "gemini", effort="hihg")
    assert "unknown effort 'hihg'" in assistant.engine_problems(typo)[0]


@pytest.mark.parametrize("engine", list(assistant.ENGINES))
def test_every_engine_runs_its_default_model_unless_config_toml_names_one(
    config, engine
):
    chosen = engine_config(config, engine)
    assert assistant.task_run(chosen, "triage").model == assistant.ENGINES[engine].model
    assert assistant.task_run(
        dataclasses.replace(chosen, model="flash"), "triage"
    ).model == ("flash")


# The firewall policy of each engine


def test_the_firewall_template_has_a_block_for_each_engine():
    template = (assistant.TEMPLATES / assistant.FIREWALL).read_text("utf-8")
    owners = [
        match[1]
        for match in map(assistant.ENGINE_BLOCK.match, template.split("\n\n"))
        if match
    ]
    assert sorted(set(owners)) == sorted(assistant.ENGINES)
    every = assistant.allowed_hosts(template)
    shared = {"api.github.com", "registry.npmjs.org"}
    hosts = {
        key: assistant.allowed_hosts(assistant.firewall_policy(key))
        for key in assistant.ENGINES
    }
    assert hosts["claude"] - shared == {
        "api.anthropic.com",
        "claude.ai",
        "downloads.claude.ai",
        "release-assets.githubusercontent.com",
        "archive.ubuntu.com",
        "azure.archive.ubuntu.com",
        "security.ubuntu.com",
    }
    assert hosts["codex"] - shared == {"api.openai.com"}
    assert hosts["gemini"] - shared == {"generativelanguage.googleapis.com"}
    assert hosts["copilot"] - shared == {
        "api.githubcopilot.com",
        "api.individual.githubcopilot.com",
        "api.business.githubcopilot.com",
        "api.enterprise.githubcopilot.com",
    }
    # Each block once, no host of another engine, and nothing that isn't a block.
    assert set().union(*hosts.values()) == every
    for key, own in hosts.items():
        others = set().union(
            *(hosts[other] - shared for other in hosts if other != key)
        )
        assert own >= shared and not own & others, key


@pytest.mark.parametrize("engine", list(assistant.ENGINES))
def test_install_writes_the_hosts_of_the_engine_of_config_toml(tmp_path, engine):
    settings = tmp_path / assistant.SETTINGS / "config.toml"
    settings.parent.mkdir(parents=True)
    settings.write_text(f'[engine]\nname = "{engine}"\n', encoding="utf-8")
    done = assistant.install(tmp_path, "0" * 40, "v1.0.0")
    assert f"wrote {assistant.FIREWALL} for the engine {engine}" in done
    written = (tmp_path / assistant.FIREWALL).read_text("utf-8")
    assert written == assistant.firewall_policy(engine)
    assert written.startswith("# Hosts that jobs on GitHub's egress-firewall runner")
    assert "\nmode: enforce\nallow:\n  # GitHub API" in written
    config = assistant.load_config(tmp_path, "owner/project", lenient=True)
    assert not [
        problem
        for problem in assistant.check_workflows(config)
        if problem.startswith(str(assistant.FIREWALL))
    ]


@pytest.mark.parametrize(
    "text",
    [None, "not toml [", "engine = 1\n", '[engine]\nname = "llama"\n', "[lifecycle]\n"],
)
def test_install_writes_the_hosts_of_the_default_engine_otherwise(tmp_path, text):
    if text is not None:
        settings = tmp_path / assistant.SETTINGS / "config.toml"
        settings.parent.mkdir(parents=True)
        settings.write_text(text, encoding="utf-8")
    assert assistant.configured_engine(tmp_path) == assistant.DEFAULT_ENGINE


def test_check_names_each_host_of_a_new_engine_that_the_policy_lacks(config, root):
    # The repository's policy is Claude's; its engine is now Gemini.
    gemini = engine_config(config, "gemini")
    problems = [
        problem
        for problem in assistant.check_workflows(gemini)
        if problem.startswith(str(assistant.FIREWALL))
    ]
    assert problems == [
        f"{assistant.FIREWALL}: the engine needs the host generativelanguage.googleapis.com"
    ]
    # The hosts of an engine that the assistant doesn't know are the default's.
    unknown = engine_config(config, "llama")
    assert not [
        problem
        for problem in assistant.check_workflows(unknown)
        if problem.startswith(str(assistant.FIREWALL))
    ]


@pytest.mark.parametrize("engine", list(assistant.ENGINES))
def test_the_notice_to_reporters_names_each_engine_and_its_vendor(config, root, engine):
    chosen = engine_config(config, engine)
    (root / "SUPPORT.md").write_text("# Support\n", encoding="utf-8")
    name, vendor = assistant.ENGINES[engine].name, assistant.ENGINES[engine].vendor
    assert f"SUPPORT.md: say that {name} by {vendor} reads the issues" in (
        assistant.check_config(chosen)
    )
    settings = root / assistant.SETTINGS / "config.toml"
    settings.write_text(
        settings.read_text("utf-8").replace('name = "claude"', f'name = "{engine}"'),
        encoding="utf-8",
    )
    assert assistant.add_notices(root) == [
        "added what happens with an issue to SUPPORT.md; adapt it"
    ]
    assert f"The assistant uses {name}, an AI by {vendor};" in (
        (root / "SUPPORT.md").read_text("utf-8")
    )
    assert not [
        problem for problem in assistant.check_config(chosen) if "SUPPORT.md" in problem
    ]


# analyze: the command lines of Gemini and Copilot


class FakeRun:
    """subprocess.run for npm and an engine's command line, which records each call
    with its variables and answers in turn."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, command, **options):
        self.calls.append((command, options))
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        code, stdout, stderr = result
        return subprocess.CompletedProcess(command, code, stdout, stderr)


INSTALLED = (0, "", "")
AS_ROOT = assistant.as_root


@pytest.fixture(autouse=True)
def as_root(monkeypatch):
    """The commands that analyze runs as root, which succeed: the tests run neither
    as root nor with sudo."""
    commands = []

    def run(command):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(assistant.running, "as_root", run)
    return commands


SECRETS = {
    "GEMINI_API_KEY": "gemini-secret",
    "COPILOT_GITHUB_TOKEN": "copilot-secret",
    "GH_TOKEN": "the-job-token",
    "GITHUB_TOKEN": "the-job-token",
    "ANTHROPIC_API_KEY": "another-engine-s-secret",
}


@pytest.fixture
def context(config, root, monkeypatch):
    """The context folder that the step context wrote, and npm on the PATH."""
    folder = root / assistant.CONTEXT
    folder.mkdir(exist_ok=True)
    (folder / "rules.md").write_text("The rules.", encoding="utf-8")
    (folder / "prompt.md").write_text("The task.", encoding="utf-8")
    monkeypatch.setattr(assistant.shutil, "which", lambda name: f"/usr/bin/{name}")
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    return folder


def gemini_output(**fields):
    return json.dumps({"stats": {"models": {}}, **fields})


def copilot_event(kind, **data):
    return json.dumps({"type": kind, "id": "e", "parentId": None, "data": data})


def copilot_output(*events):
    """The JSON events that Copilot prints with --output-format json, one on each
    line, around those of the test."""
    return "\n".join(
        (
            copilot_event("session.start", sessionId="s"),
            *events,
            json.dumps({"type": "result", "exitCode": 0, "usage": {}}),
        )
    )


@pytest.mark.parametrize("engine", CLI_ENGINES)
def test_analyze_runs_the_engine_with_its_secret_alone(
    monkeypatch, tmp_path, config, context, engine, as_root
):
    answer = json.dumps(make_answer())
    printed = (
        gemini_output(response=answer)
        if engine == "gemini"
        else copilot_output(copilot_event("assistant.message", content=answer))
    )
    run = FakeRun(INSTALLED, (0, printed, ""))
    monkeypatch.setattr(assistant.subprocess, "run", run)
    chosen = engine_config(config, engine, max_turns=30)
    work = tmp_path / "work"
    result = assistant.analyze(chosen, "follow-up", engine, work)
    assert result.strip() == answer
    (install, installing), (command, options) = run.calls
    cli = assistant.ENGINES[engine].cli
    # npm ci installs the lock file of the engine as it is, without the scripts of
    # any package, in a folder of its own that holds no .npmrc of the checkout, and
    # sees no secret.
    assert install == [
        "/usr/bin/npm",
        "ci",
        "--prefix",
        str(work / "cli"),
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
        "--loglevel",
        "error",
    ]
    assert installing["cwd"] == work / "cli"
    for name in assistant.LOCK_FILES:
        assert (work / "cli" / name).read_bytes() == (
            assistant.ENGINE_LOCKS / engine / name
        ).read_bytes()
    assert installing["env"]["PATH"] == os.environ["PATH"]
    assert not set(installing["env"]) & (set(SECRETS) | assistant.NPM_HIDDEN)
    binary = str(work / "cli" / "node_modules" / ".bin" / cli.command)
    model = assistant.ENGINES[engine].model
    assert (
        command[:6]
        == [binary, "--prompt", "The rules.\n\nThe task.", "--model", model]
        + (command[5:6])
    )
    assert command[-len(cli.arguments) :] == list(cli.arguments)
    assert options["cwd"] == chosen.root
    assert options["timeout"] == assistant.ENGINE_MINUTES * 60
    variables = options["env"]
    secret = assistant.ENGINES[engine].credentials[0]
    # Its own secret, and no token of GitHub or of another engine.
    assert variables[secret] == SECRETS[secret]
    assert set(variables) == {"PATH", "HOME", "LANG", "NO_COLOR", secret} | set(
        cli.variables
    ) | ({cli.settings_variable} if cli.settings_variable else set())
    assert variables["HOME"] == str(work / "home") and (work / "home").is_dir()
    settings = json.loads((work / "settings.json").read_text("utf-8"))
    assert settings == assistant.cli_settings(
        cli, assistant.task_run(chosen, "follow-up")
    )
    if engine == "gemini":
        # Root's alone, in a folder of the run that root owns and nobody else may
        # write to; it goes when the run ends.
        folder = f"/etc/issue-assistant/{work.name}"
        assert variables["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] == f"{folder}/settings.json"
        assert as_root == [
            [
                "install",
                "-d",
                "-m",
                "755",
                "-o",
                "0",
                "-g",
                "0",
                "/etc/issue-assistant",
            ]
            + [folder],
            ["install", "-m", "644", "-o", "0", "-g", "0"]
            + [str(work / "settings.json"), f"{folder}/settings.json"],
            ["rm", "-rf", folder],
        ]
        # A follow-up takes half of max_turns.
        assert settings["model"]["maxSessionTurns"] == 15
        assert settings["tools"]["core"] == [
            "read_file",
            "list_directory",
            "glob",
            "grep_search",
        ]


def test_copilot_takes_its_settings_from_the_folder_of_the_run(
    monkeypatch, tmp_path, config, context, as_root
):
    printed = copilot_output(copilot_event("assistant.message", content="{}"))
    run = FakeRun(INSTALLED, (0, printed, ""))
    monkeypatch.setattr(assistant.subprocess, "run", run)
    chosen = engine_config(config, "copilot")
    assistant.analyze(chosen, "triage", "copilot", tmp_path)
    assert as_root == []
    cli = assistant.ENGINES["copilot"].cli
    assert assistant.settings_path(cli, tmp_path) == tmp_path / "settings.json"


def test_gemini_doesn_t_run_when_root_can_t_take_its_settings(
    monkeypatch, tmp_path, config, context
):
    """The Gemini CLI would run without them, with only a warning."""
    commands = []

    def refuse(command):
        commands.append(command)
        refusal = "sudo: a password is required\n"
        return subprocess.CompletedProcess(command, 1, "", refusal)

    monkeypatch.setattr(assistant.running, "as_root", refuse)
    run = FakeRun(INSTALLED)
    monkeypatch.setattr(assistant.subprocess, "run", run)
    with pytest.raises(assistant.AssistantError) as refused:
        assistant.analyze(engine_config(config, "gemini"), "triage", "gemini", tmp_path)
    assert str(refused.value) == (
        "The settings that keep the engine's command line to reading must belong to "
        "root, and install -d as root failed: sudo: a password is required. It "
        "needs sudo without a password prompt: GitHub's runners have it; on your "
        "computer, run sudo -v first."
    )
    # npm installed it, nothing ran it, and what the copy may have made goes.
    assert len(run.calls) == 1
    assert commands[1:] == [["rm", "-rf", f"/etc/issue-assistant/{tmp_path.name}"]]


@pytest.mark.parametrize(
    ("user", "command"),
    [
        (1001, ["sudo", "--non-interactive", "install", "-d", "/etc/x"]),
        (0, ["install", "-d", "/etc/x"]),
    ],
)
def test_root_runs_a_command_with_sudo_unless_the_job_is_root(
    monkeypatch, user, command
):
    calls = []

    def run(arguments, **options):
        calls.append(arguments)
        assert options["capture_output"] and not options["check"]
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(assistant.subprocess, "run", run)
    monkeypatch.setattr(assistant.os, "geteuid", lambda: user, raising=False)
    assert AS_ROOT(["install", "-d", "/etc/x"]).returncode == 0
    assert calls == [command]


def test_root_without_sudo_is_a_failure(monkeypatch):
    def run(arguments, **options):
        raise FileNotFoundError(arguments[0])

    monkeypatch.setattr(assistant.subprocess, "run", run)
    monkeypatch.setattr(assistant.os, "geteuid", lambda: 1001, raising=False)
    result = AS_ROOT(["install", "-d", "/etc/x"])
    assert (result.returncode, result.stderr) == (127, "no sudo")


def test_copilot_gets_the_effort_of_the_task_and_gemini_takes_none(config):
    copilot = assistant.ENGINES["copilot"].cli
    gemini = assistant.ENGINES["gemini"].cli
    run = assistant.Run("auto", 4, "1", "high")
    assert assistant.cli_command(copilot, "copilot", run, "P")[5:7] == [
        "--reasoning-effort",
        "high",
    ]
    calm = dataclasses.replace(run, effort="")
    assert "--reasoning-effort" not in assistant.cli_command(
        copilot, "copilot", calm, "P"
    )
    assert "--reasoning-effort" not in assistant.cli_command(gemini, "gemini", run, "P")
    # Settings without the turns, for a command line that takes none.
    assert assistant.cli_settings(copilot, run) == {}


@pytest.mark.parametrize(
    ("key", "problem"),
    [
        ("claude", "analyze runs the engines gemini, copilot, not claude."),
        ("", "analyze runs the engines gemini, copilot, not none."),
        (
            "copilot",
            "The step of copilot ran, but config.toml names the engine gemini.",
        ),
    ],
)
def test_analyze_runs_only_the_engine_of_config_toml_that_has_a_command_line(
    tmp_path, config, key, problem
):
    gemini = engine_config(config, "gemini")
    with pytest.raises(assistant.AssistantError) as refused:
        assistant.analyze(gemini, "triage", key, tmp_path)
    assert str(refused.value) == problem


@pytest.mark.parametrize(
    "lock",
    [
        None,
        "{",
        "[]",
        {"packages": []},
        {"packages": {}},
        {"packages": {"node_modules/@google/gemini-cli": "0.64.0"}},
        {"packages": {"node_modules/@google/gemini-cli": {}}},
        {"packages": {"node_modules/@google/gemini-cli": {"version": "latest"}}},
        {"packages": {"node_modules/@google/gemini-cli": {"version": "^0.64.0"}}},
    ],
)
def test_analyze_installs_only_the_version_of_the_lock_file(
    monkeypatch, tmp_path, lock
):
    """Without the version of the command line in its lock file, such as in a
    checkout of the issue assistant without engines/, npm never runs."""
    run = FakeRun()
    monkeypatch.setattr(assistant.subprocess, "run", run)
    locks = tmp_path / "engines"
    (locks / "gemini").mkdir(parents=True)
    if lock is not None:
        (locks / "gemini" / "package-lock.json").write_text(
            lock if isinstance(lock, str) else json.dumps(lock), encoding="utf-8"
        )
    with pytest.raises(assistant.AssistantError) as refused:
        assistant.install_cli("gemini", tmp_path / "cli", locks)
    assert str(refused.value) == (
        "The issue assistant has no lock file of @google/gemini-cli with its "
        f"version at {locks / 'gemini' / 'package-lock.json'}: its step needs the "
        "folder engines/ of its checkout."
    )
    assert run.calls == []


def test_the_version_of_each_command_line_is_that_of_its_lock_file(tmp_path):
    locks = tmp_path / "engines"
    shutil.copytree(assistant.ENGINE_LOCKS / "copilot", locks / "copilot")
    path = locks / "copilot" / "package-lock.json"
    lock = json.loads(path.read_text(encoding="utf-8"))
    lock["packages"]["node_modules/@github/copilot"]["version"] = "2.0.0"
    path.write_text(json.dumps(lock), encoding="utf-8")
    assert assistant.locked_version("copilot", locks) == "2.0.0"


def test_analyze_needs_npm(monkeypatch, tmp_path, config, context):
    monkeypatch.setattr(assistant.shutil, "which", lambda name: None)
    with pytest.raises(assistant.AssistantError, match="analyze needs npm"):
        assistant.analyze(engine_config(config, "gemini"), "triage", "gemini", tmp_path)


@pytest.mark.parametrize(
    ("stderr", "reason"),
    [
        (
            "npm error code E404\nnpm error 404 Not Found - @google/gemini-cli\n",
            " (E404): npm error code E404 npm error 404 Not Found - @google/gemini-cli",
        ),
        (
            "npm ERR! code ECONNREFUSED\nnpm ERR! request failed\n",
            " (ECONNREFUSED): npm ERR! code ECONNREFUSED npm ERR! request failed",
        ),
        ("npm error something\n", ": npm error something"),
        ("", ": no message"),
        # A package that isn't the one of the lock file, by its hash.
        (
            "npm error code EINTEGRITY\nnpm error sha512-a integrity checksum failed "
            "when using sha512: wanted sha512-a but got sha512-b. (5454 bytes)\n"
            "npm error A complete log of this run can be found in: /home/x.log\n",
            " (EINTEGRITY): the registry sent a package whose hash isn't the "
            "integrity of the lock file, so nothing of it runs",
        ),
    ],
)
def test_a_failed_install_says_what_npm_said(
    monkeypatch, tmp_path, config, context, stderr, reason
):
    monkeypatch.setattr(assistant.subprocess, "run", FakeRun((1, "", stderr)))
    with pytest.raises(assistant.AssistantError) as refused:
        assistant.analyze(engine_config(config, "gemini"), "triage", "gemini", tmp_path)
    version = assistant.locked_version("gemini")
    assert str(refused.value) == (
        f"npm installed no @google/gemini-cli@{version} from "
        f"engines/gemini/package-lock.json{reason}"
    )


@pytest.mark.parametrize(
    ("engine", "result", "problem"),
    [
        (
            "gemini",
            (53, gemini_output(error={"message": "turns"}), ""),
            "Gemini stopped at the turns of the task, before it answered; max_turns "
            "under [engine] in .github/issue-assistant/config.toml gives it more.",
        ),
        (
            "gemini",
            (
                1,
                gemini_output(
                    error={"message": "API key not valid.\n Please pass a valid key."}
                ),
                "",
            ),
            "Gemini failed: API key not valid. Please pass a valid key.",
        ),
        ("gemini", (1, "Not JSON", "Error: quota\n"), "Gemini failed: Error: quota"),
        ("gemini", (0, "[1, 2]", ""), "Gemini failed: no answer"),
        ("gemini", (0, gemini_output(response="  "), ""), "Gemini failed: no answer"),
        ("gemini", (0, gemini_output(error="text"), "warned"), "Gemini failed: warned"),
        (
            "copilot",
            (1, "", "Error: No authentication information found.\n"),
            "Copilot failed: Error: No authentication information found.",
        ),
        ("copilot", (0, "\n", ""), "Copilot failed: no answer"),
        (
            "copilot",
            (
                1,
                copilot_output(
                    copilot_event("session.error", errorType="auth", message="Denied.")
                ),
                "warned",
            ),
            "Copilot failed: Denied.",
        ),
        (
            "copilot",
            (0, copilot_output(copilot_event("assistant.message", content=" ")), ""),
            "Copilot failed: no answer",
        ),
        # Killed by a signal, no stop at the turns, of which Copilot has none.
        ("copilot", (-1, "", "Hung up.\n"), "Copilot failed: Hung up."),
    ],
)
def test_an_engine_that_fails_says_why_in_one_line(
    monkeypatch, tmp_path, config, context, engine, result, problem
):
    monkeypatch.setattr(assistant.subprocess, "run", FakeRun(INSTALLED, result))
    with pytest.raises(assistant.AssistantError) as refused:
        assistant.analyze(engine_config(config, engine), "triage", engine, tmp_path)
    assert str(refused.value) == problem


def test_an_engine_that_runs_too_long_stops(monkeypatch, tmp_path, config, context):
    timeout = subprocess.TimeoutExpired(["copilot"], assistant.ENGINE_MINUTES * 60)
    monkeypatch.setattr(assistant.subprocess, "run", FakeRun(INSTALLED, timeout))
    with pytest.raises(
        assistant.AssistantError, match="Copilot gave no answer within 15 minutes."
    ):
        assistant.analyze(
            engine_config(config, "copilot"), "triage", "copilot", tmp_path
        )


# The lock files of the command lines


def test_each_command_line_that_analyze_runs_has_a_lock_file_and_nothing_else():
    assert sorted(path.name for path in assistant.ENGINE_LOCKS.iterdir()) == sorted(
        CLI_ENGINES
    )
    for key in CLI_ENGINES:
        assert sorted(
            path.name for path in (assistant.ENGINE_LOCKS / key).iterdir()
        ) == sorted(assistant.LOCK_FILES)


# An install path of a lock file: a package, or one nested below another when two
# versions of a package are needed.
LOCK_PATH = re.compile(
    r"(?:node_modules/(?:@[^/]+/)?[^/]+/)*node_modules/(?:@[^/]+/)?[^/]+"
)


@pytest.mark.parametrize("key", CLI_ENGINES)
def test_the_lock_file_pins_every_package_with_its_hash(key):
    """npm ci installs each package of the lock file at its version, from npm's
    registry, which the firewall lets through, and refuses one whose SHA-512 hash
    isn't its integrity: so analyze runs what the check engine contracts verified."""
    cli = assistant.ENGINES[key].cli
    folder = assistant.ENGINE_LOCKS / key
    manifest = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((folder / "package-lock.json").read_text(encoding="utf-8"))
    version = assistant.locked_version(key)
    # The command line alone, at its version, in a package that nobody can publish
    # and that has no scripts of its own.
    assert manifest == {
        "name": f"issue-assistant-engine-{key}",
        "version": "0.0.0",
        "private": True,
        "description": manifest["description"],
        "license": "MIT",
        "dependencies": {cli.package: version},
    }
    assert lock["lockfileVersion"] == 3
    packages = lock["packages"]
    assert packages[""]["dependencies"] == manifest["dependencies"]
    assert len(packages) > 1
    installed = {path: entry for path, entry in packages.items() if path}
    for path, entry in installed.items():
        assert LOCK_PATH.fullmatch(path) and "link" not in entry, path
    # A package bundled in another one comes in its tarball, under its hash.
    fetched = {p: e for p, e in installed.items() if not e.get("inBundle")}
    for path, entry in fetched.items():
        name = path.rpartition("node_modules/")[2]
        assert entry["resolved"] == (
            f"https://registry.npmjs.org/{name}/-/"
            f"{name.rpartition('/')[2]}-{entry['version']}.tgz"
        ), path
        algorithm, _, digest = entry["integrity"].partition("-")
        assert algorithm == "sha512", path
        assert len(base64.b64decode(digest, validate=True)) == 64, path


def test_the_dependency_review_allows_the_packages_under_their_vendor_s_terms():
    """The dependency review allows a package of a lock file without a free license,
    such as the Copilot CLI under GitHub's terms, by its name alone, and only one
    of a lock file: no release ships them."""
    review = (assistant.TOOL / ".github" / "dependency-review.yml").read_text("utf-8")
    allowed = set(re.findall(r"(?m)^  - pkg:npm/%40github/(\S+)$", review))
    named = set()
    for key in CLI_ENGINES:
        path = assistant.ENGINE_LOCKS / key / "package-lock.json"
        lock = json.loads(path.read_text(encoding="utf-8"))
        for entry_path, entry in lock["packages"].items():
            name = entry_path.rpartition("node_modules/")[2]
            if entry.get("license") == "SEE LICENSE IN LICENSE.md":
                named.add(name.removeprefix("@github/"))
    assert named and allowed == named


def test_the_analyze_command_outputs_the_answer(
    monkeypatch, tmp_path, root, config, context
):
    (root / assistant.SETTINGS / "config.toml").write_text(
        '[engine]\nname = "copilot"\n', encoding="utf-8"
    )
    answer = json.dumps(make_answer())
    printed = copilot_output(copilot_event("assistant.message", content=answer))
    monkeypatch.setattr(
        assistant.subprocess, "run", FakeRun(INSTALLED, (0, printed, ""))
    )
    runner = tmp_path / "runner"
    runner.mkdir()
    code, outputs, _ = run_main(
        monkeypatch,
        tmp_path,
        root,
        None,
        "analyze",
        MODE="triage",
        ENGINE="copilot",
        RUNNER_TEMP=str(runner),
    )
    assert code == 0
    assert outputs == f"answer={answer}\n"
    # Its home and command line in a folder of its own of the runner's.
    (work,) = runner.iterdir()
    assert work.name.startswith("issue-assistant-") and (work / "home").is_dir()


def test_the_analyze_command_fails_on_an_engine_without_a_command_line(
    monkeypatch, tmp_path, root, context, capsys
):
    code, outputs, _ = run_main(
        monkeypatch, tmp_path, root, None, "analyze", MODE="triage", ENGINE="claude"
    )
    assert code == 1 and outputs == ""
    assert "::error::analyze runs the engines gemini, copilot, not claude." in (
        capsys.readouterr().out
    )


def test_copilot_s_answer_is_its_text_as_it_is(config):
    """Copilot's events hold the model's text as it is, not rendered as Markdown:
    the backslashes of the JSON, its code spans and a reference such as #12 stay."""
    answer = json.dumps(
        make_answer(analysis='Run `type C:\\temp\\log`, quote "it", see #12.')
    )
    printed = copilot_output(
        "Not JSON",
        "[1, 2]",
        json.dumps({"type": "assistant.message", "data": "text"}),
        copilot_event("assistant.message", content="An earlier call.", apiCallId="a"),
        copilot_event("assistant.message", content=7),
        # A subagent's message is not the answer.
        json.dumps(
            {"type": "assistant.message", "agentId": "x", "data": {"content": "no"}}
        ),
        copilot_event("assistant.message", content="It reads:", apiCallId="b"),
        copilot_event("assistant.message", content=answer, apiCallId="b"),
    )
    cli = assistant.ENGINES["copilot"].cli
    text, reason = assistant.event_answer(cli, printed)
    assert (text, reason) == (f"It reads:\n{answer}", None)
    parsed = assistant.parse_answer("triage", text, engine_config(config, "copilot"))
    assert parsed["analysis"] == json.loads(answer)["analysis"]
    # Without the id of the call, the last message alone.
    printed = copilot_output(
        copilot_event("assistant.message", content="First."),
        copilot_event("assistant.message", content=answer),
    )
    assert assistant.event_answer(cli, printed) == (answer, None)
